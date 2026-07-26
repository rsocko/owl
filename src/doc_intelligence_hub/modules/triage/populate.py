"""Queue population logic — scans existing data to auto-flag items for triage.

Auto-flagging rules:
- EOB matches with confidence score < 70% → eob_match_review
- EOB matches with multiple close candidates (similar scores) → eob_match_review
- Orphan EOB with no bill after 30 days → orphan_document (waiting); 60 days → overdue
- Orphan Bill with no EOB after 14 days → orphan_document (waiting); 45 days → overdue
- Deferred orphan items whose defer period expired → re-flagged as pending
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from doc_intelligence_hub.modules.triage.database import (
    create_queue_item,
    get_session as get_triage_session,
    TriageQueueItem,
)

logger = logging.getLogger(__name__)

# Orphan age thresholds (days)
_EOB_WAITING_DAYS = 30
_EOB_OVERDUE_DAYS = 60
_BILL_WAITING_DAYS = 14
_BILL_OVERDUE_DAYS = 45


def populate_queue() -> dict[str, Any]:
    """Scan EOB matching data and create triage queue items for flagged items.

    Returns a summary of items created.
    """
    stats = {"eob_low_confidence": 0, "eob_multi_candidate": 0, "orphan_documents": 0, "orphan_reflagged": 0, "skipped_existing": 0}

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
        stats["orphan_reflagged"] = _reflag_expired_deferred_orphans()
    except Exception as exc:
        logger.warning("Could not re-flag deferred orphans: %s", exc)

    total = stats["eob_low_confidence"] + stats["eob_multi_candidate"] + stats["orphan_documents"]
    logger.info("Queue population complete: %d items created, %d re-flagged (%s)", total, stats["orphan_reflagged"], stats)
    return {"items_created": total, "items_reflagged": stats["orphan_reflagged"], "details": stats}


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
    """Flag unmatched EOBs and Bills as orphans based on age thresholds.

    EOB orphans: 30 days → waiting, 60 days → overdue
    Bill orphans: 14 days → waiting, 45 days → overdue
    """
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
    now = datetime.now(UTC)

    try:
        # ── Collect IDs of documents that have any match candidate ──
        matched_eob_ids = {
            row[0]
            for row in eob_session.query(MatchRecord.eob_document_id)
            .filter(MatchRecord.status.in_(["candidate", "confirmed"]))
            .distinct()
            .all()
        }
        matched_bill_ids = {
            row[0]
            for row in eob_session.query(MatchRecord.bill_document_id)
            .filter(MatchRecord.status.in_(["candidate", "confirmed"]))
            .distinct()
            .all()
        }

        # ── Existing triage items (avoid duplicates) ──
        existing_target_ids = set()
        existing = (
            triage_session.query(TriageQueueItem.target_id)
            .filter(TriageQueueItem.item_type == "orphan_document")
            .filter(TriageQueueItem.status.in_(["pending", "deferred"]))
            .all()
        )
        existing_target_ids = {row[0] for row in existing}

        # ── Flag orphan EOBs ──
        all_eobs = eob_session.query(EOBRecord).all()
        for eob in all_eobs:
            if eob.document_id in matched_eob_ids:
                continue
            target_id = f"eob-{eob.document_id}"
            if target_id in existing_target_ids:
                continue

            age_days = _document_age_days(eob.created_at, now)
            if age_days < _EOB_WAITING_DAYS:
                continue  # Too new to flag

            orphan_status = "overdue" if age_days >= _EOB_OVERDUE_DAYS else "waiting"
            priority = 80 if orphan_status == "overdue" else 50

            amount = eob.total_patient_responsibility or eob.total_billed
            create_queue_item(
                item_type="orphan_document",
                source="auto_flag",
                target_type="document",
                target_id=target_id,
                reason=(
                    f"EOB with no matching bill — {age_days} days since received"
                    if orphan_status == "waiting"
                    else f"OVERDUE: EOB has been unmatched for {age_days} days (threshold: {_EOB_OVERDUE_DAYS}d)"
                ),
                priority=priority,
                metadata={
                    "document_id": eob.document_id,
                    "document_type": "eob",
                    "orphan_status": orphan_status,
                    "provider_name": eob.provider_name,
                    "patient_name": eob.patient_name,
                    "date_of_service": eob.date_of_service,
                    "amount": amount,
                    "insurance_company": eob.insurance_company,
                    "document_age_days": age_days,
                    "expected_match_window": _EOB_OVERDUE_DAYS,
                    "waiting_threshold_days": _EOB_WAITING_DAYS,
                    "overdue_threshold_days": _EOB_OVERDUE_DAYS,
                },
            )
            created += 1

        # ── Flag orphan Bills ──
        all_bills = eob_session.query(BillRecord).all()
        for bill in all_bills:
            if bill.document_id in matched_bill_ids:
                continue
            target_id = f"bill-{bill.document_id}"
            if target_id in existing_target_ids:
                continue

            age_days = _document_age_days(bill.created_at, now)
            if age_days < _BILL_WAITING_DAYS:
                continue

            orphan_status = "overdue" if age_days >= _BILL_OVERDUE_DAYS else "waiting"
            priority = 80 if orphan_status == "overdue" else 50

            create_queue_item(
                item_type="orphan_document",
                source="auto_flag",
                target_type="document",
                target_id=target_id,
                reason=(
                    f"Bill with no matching EOB — {age_days} days since received"
                    if orphan_status == "waiting"
                    else f"OVERDUE: Bill has been unmatched for {age_days} days (threshold: {_BILL_OVERDUE_DAYS}d)"
                ),
                priority=priority,
                metadata={
                    "document_id": bill.document_id,
                    "document_type": "bill",
                    "orphan_status": orphan_status,
                    "provider_name": bill.provider_name,
                    "patient_name": bill.patient_name,
                    "date_of_service": bill.date_of_service,
                    "amount": bill.total_amount or bill.balance_due,
                    "invoice_number": bill.invoice_number,
                    "document_age_days": age_days,
                    "expected_match_window": _BILL_OVERDUE_DAYS,
                    "waiting_threshold_days": _BILL_WAITING_DAYS,
                    "overdue_threshold_days": _BILL_OVERDUE_DAYS,
                },
            )
            created += 1

    finally:
        eob_session.close()
        triage_session.close()

    return created


def _reflag_expired_deferred_orphans() -> int:
    """Re-flag deferred orphan items whose defer period has expired."""
    triage_session = get_triage_session()
    now = datetime.now(UTC)
    reflagged = 0

    try:
        expired = (
            triage_session.query(TriageQueueItem)
            .filter(TriageQueueItem.item_type == "orphan_document")
            .filter(TriageQueueItem.status == "deferred")
            .filter(TriageQueueItem.deferred_until <= now)
            .all()
        )

        for item in expired:
            item.status = "pending"
            item.deferred_until = None
            reflagged += 1

        if reflagged:
            triage_session.commit()

    finally:
        triage_session.close()

    return reflagged


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _document_age_days(created_at: Any, now: datetime) -> int:
    """Calculate document age in days from its created_at timestamp."""
    if created_at is None:
        return 0
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at)
        except (ValueError, TypeError):
            return 0
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return max(0, (now - created_at).days)


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
