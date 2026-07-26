"""Queue population logic — scans existing data to auto-flag items for triage.

Auto-flagging rules:
- EOB matches with confidence score < 70% → eob_match_review
- EOB matches with multiple close candidates (similar scores) → eob_match_review
- Unmatched documents older than 14 days → orphan_document
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from doc_intelligence_hub.modules.triage.database import (
    create_queue_item,
    get_session as get_triage_session,
    TriageQueueItem,
)

logger = logging.getLogger(__name__)


def populate_queue() -> dict[str, Any]:
    """Scan EOB matching data and create triage queue items for flagged items.

    Returns a summary of items created.
    """
    stats = {"eob_low_confidence": 0, "eob_multi_candidate": 0, "orphan_documents": 0, "duplicate_documents": 0, "skipped_existing": 0}

    try:
        stats["eob_low_confidence"] = _flag_low_confidence_matches()
    except Exception as exc:
        logger.warning("Could not flag low-confidence matches: %s", exc)

    try:
        stats["eob_multi_candidate"] = _flag_multi_candidate_matches()
    except Exception as exc:
        logger.warning("Could not flag multi-candidate matches: %s", exc)

    try:
        stats["orphan_documents"] = _flag_orphan_documents()
    except Exception as exc:
        logger.warning("Could not flag orphan documents: %s", exc)

    try:
        stats["duplicate_documents"] = _flag_duplicate_documents()
    except Exception as exc:
        logger.warning("Could not flag duplicate documents: %s", exc)

    total = stats["eob_low_confidence"] + stats["eob_multi_candidate"] + stats["orphan_documents"] + stats.get("duplicate_documents", 0)
    logger.info("Queue population complete: %d items created (%s)", total, stats)
    return {"items_created": total, "details": stats}


def _flag_low_confidence_matches() -> int:
    """Flag EOB matches with score < 0.70 that aren't already in the queue."""
    try:
        from doc_intelligence_hub.modules.eob_matching.database import (
            MatchRecord,
            get_session as get_eob_session,
        )
    except ImportError:
        logger.warning("EOB matching module not available, skipping low-confidence flagging")
        return 0

    eob_session = get_eob_session()
    triage_session = get_triage_session()
    created = 0

    try:
        # Find candidate matches with low scores
        low_matches = (
            eob_session.query(MatchRecord)
            .filter(MatchRecord.status == "candidate")
            .filter(MatchRecord.score < 0.70)
            .all()
        )

        # Get existing triage items for these targets to avoid duplicates
        existing_target_ids = set()
        existing = (
            triage_session.query(TriageQueueItem.target_id)
            .filter(TriageQueueItem.item_type == "eob_match_review")
            .filter(TriageQueueItem.status.in_(["pending", "deferred"]))
            .all()
        )
        existing_target_ids = {row[0] for row in existing}

        for match in low_matches:
            target_id = str(match.id)
            if target_id in existing_target_ids:
                continue

            score_pct = round((match.score or 0) * 100, 1)
            priority = _priority_from_score(score_pct)

            # Build reason text
            weakest = _weakest_factor(match)
            reason = f"Low confidence match ({score_pct}%)"
            if weakest:
                reason += f" — {weakest} factor is the weakest signal"

            create_queue_item(
                item_type="eob_match_review",
                source="auto_flag",
                target_type="eob_match",
                target_id=target_id,
                reason=reason,
                priority=priority,
                metadata={
                    "score": match.score,
                    "score_pct": score_pct,
                    "eob_document_id": match.eob_document_id,
                    "bill_document_id": match.bill_document_id,
                    "confidence": match.confidence,
                    "breakdown": {
                        "date": match.breakdown_date,
                        "provider": match.breakdown_provider,
                        "patient": match.breakdown_patient,
                        "amount": match.breakdown_amount,
                        "procedures": match.breakdown_procedures,
                    },
                },
            )
            created += 1

    finally:
        eob_session.close()
        triage_session.close()

    return created


def _flag_multi_candidate_matches() -> int:
    """Flag EOB documents that have multiple candidate matches with similar scores."""
    try:
        from doc_intelligence_hub.modules.eob_matching.database import (
            MatchRecord,
            get_session as get_eob_session,
        )
    except ImportError:
        return 0

    eob_session = get_eob_session()
    triage_session = get_triage_session()
    created = 0

    try:
        # Group candidate matches by EOB document
        candidates = (
            eob_session.query(MatchRecord)
            .filter(MatchRecord.status == "candidate")
            .order_by(MatchRecord.eob_document_id, MatchRecord.score.desc())
            .all()
        )

        by_eob: dict[int, list] = {}
        for match in candidates:
            by_eob.setdefault(match.eob_document_id, []).append(match)

        existing_target_ids = set()
        existing = (
            triage_session.query(TriageQueueItem.target_id)
            .filter(TriageQueueItem.item_type == "eob_match_review")
            .filter(TriageQueueItem.source == "auto_flag")
            .filter(TriageQueueItem.status.in_(["pending", "deferred"]))
            .all()
        )
        existing_target_ids = {row[0] for row in existing}

        for eob_doc_id, matches in by_eob.items():
            if len(matches) < 2:
                continue

            # Check if top two scores are within 15% of each other
            top_score = matches[0].score or 0
            second_score = matches[1].score or 0
            if top_score > 0 and (top_score - second_score) / top_score < 0.15:
                target_id = str(matches[0].id)
                if target_id in existing_target_ids:
                    continue

                score_pct = round(top_score * 100, 1)
                second_pct = round(second_score * 100, 1)

                create_queue_item(
                    item_type="eob_match_review",
                    source="auto_flag",
                    target_type="eob_match",
                    target_id=target_id,
                    reason=f"Multiple close candidates: top {score_pct}% vs {second_pct}% — needs human disambiguation",
                    priority=max(60, _priority_from_score(score_pct)),
                    metadata={
                        "score": top_score,
                        "score_pct": score_pct,
                        "eob_document_id": eob_doc_id,
                        "bill_document_id": matches[0].bill_document_id,
                        "confidence": matches[0].confidence,
                        "candidate_count": len(matches),
                        "second_score_pct": second_pct,
                    },
                )
                created += 1

    finally:
        eob_session.close()
        triage_session.close()

    return created


def _flag_orphan_documents() -> int:
    """Flag unmatched documents as orphans in the triage queue."""
    try:
        from doc_intelligence_hub.modules.eob_matching.database import (
            EOBRecord,
            BillRecord,
            MatchRecord,
            get_session as get_eob_session,
        )
    except ImportError:
        return 0

    eob_session = get_eob_session()
    triage_session = get_triage_session()
    created = 0

    try:
        # Find EOBs with no match at all
        matched_eob_ids = {
            row[0]
            for row in eob_session.query(MatchRecord.eob_document_id)
            .filter(MatchRecord.status.in_(["candidate", "confirmed"]))
            .distinct()
            .all()
        }

        all_eobs = eob_session.query(EOBRecord).all()

        existing_target_ids = set()
        existing = (
            triage_session.query(TriageQueueItem.target_id)
            .filter(TriageQueueItem.item_type == "orphan_document")
            .filter(TriageQueueItem.status.in_(["pending", "deferred"]))
            .all()
        )
        existing_target_ids = {row[0] for row in existing}

        for eob in all_eobs:
            if eob.document_id in matched_eob_ids:
                continue
            target_id = f"eob-{eob.document_id}"
            if target_id in existing_target_ids:
                continue

            create_queue_item(
                item_type="orphan_document",
                source="auto_flag",
                target_type="document",
                target_id=target_id,
                reason="EOB document with no matching bill candidate found",
                priority=40,
                metadata={
                    "document_id": eob.document_id,
                    "document_type": "eob",
                    "provider_name": eob.provider_name,
                    "patient_name": eob.patient_name,
                    "date_of_service": eob.date_of_service,
                },
            )
            created += 1

    finally:
        eob_session.close()
        triage_session.close()

    return created


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _priority_from_score(score_pct: float) -> int:
    """Map a confidence score to a priority value (higher = more urgent)."""
    if score_pct < 50:
        return 90
    if score_pct < 60:
        return 80
    if score_pct < 70:
        return 70
    if score_pct < 80:
        return 55
    return 40


def _weakest_factor(match: Any) -> str | None:
    """Return the name of the weakest breakdown factor."""
    factors = {
        "Date": getattr(match, "breakdown_date", None),
        "Provider": getattr(match, "breakdown_provider", None),
        "Patient": getattr(match, "breakdown_patient", None),
        "Amount": getattr(match, "breakdown_amount", None),
        "Procedures": getattr(match, "breakdown_procedures", None),
    }
    valid = {k: v for k, v in factors.items() if v is not None}
    if not valid:
        return None
    return min(valid, key=lambda k: valid[k])


def _flag_duplicate_documents() -> int:
    """Run duplicate detection scan and return count of triage items created."""
    try:
        from doc_intelligence_hub.modules.triage.duplicates import scan_all_duplicates

        result = scan_all_duplicates()
        return result.get("triage_items_created", 0)
    except ImportError:
        logger.warning("Duplicates module not available, skipping duplicate flagging")
        return 0
    except Exception as exc:
        logger.warning("Duplicate scan failed: %s", exc)
        return 0
