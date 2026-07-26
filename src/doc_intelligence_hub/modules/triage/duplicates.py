"""Duplicate document detection and merge logic.

Detection scoring uses weighted signals to identify potential duplicate documents:
- Invoice/claim number match: 40%
- Amount match: 20%
- Date of service match: 15%
- Provider match: 10%
- Title similarity: 10%
- Content hash similarity: 5%

Merge behavior:
1. Primary document retains all Paperless metadata, tags, links
2. Archived document gets tagged `duplicate-of:{primary_id}` in Paperless
3. Existing EOB match links transfer to primary
4. Paperless document is NOT deleted — only tagged
"""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

from doc_intelligence_hub.modules.triage.database import (
    CorrectionEvent,
    TriageQueueItem,
    create_duplicate_pair,
    create_queue_item,
    find_existing_duplicate_pair,
    resolve_duplicate_pair,
)
from doc_intelligence_hub.modules.triage.database import (
    get_session as get_triage_session,
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Signal weights (must sum to 1.0)
# ------------------------------------------------------------------
WEIGHTS = {
    "invoice_number": 0.40,
    "amount": 0.20,
    "date_of_service": 0.15,
    "provider": 0.10,
    "title": 0.10,
    "content_hash": 0.05,
}

# Minimum overall score to flag as a potential duplicate
DUPLICATE_THRESHOLD = 0.60


# ------------------------------------------------------------------
# Individual signal scorers
# ------------------------------------------------------------------


def _score_invoice_number(meta_a: dict, meta_b: dict) -> float:
    """Score based on invoice/claim number match."""
    num_a = str(meta_a.get("invoice_number") or meta_a.get("claim_number") or "").strip().lower()
    num_b = str(meta_b.get("invoice_number") or meta_b.get("claim_number") or "").strip().lower()
    if not num_a or not num_b:
        return 0.0
    if num_a == num_b:
        return 1.0
    # Partial match via sequence similarity
    return SequenceMatcher(None, num_a, num_b).ratio()


def _score_amount(meta_a: dict, meta_b: dict) -> float:
    """Score based on monetary amount match."""
    amt_a = meta_a.get("amount") or meta_a.get("total_amount")
    amt_b = meta_b.get("amount") or meta_b.get("total_amount")
    if amt_a is None or amt_b is None:
        return 0.0
    try:
        a, b = float(amt_a), float(amt_b)
    except (ValueError, TypeError):
        return 0.0
    if a == b:
        return 1.0
    max_val = max(abs(a), abs(b))
    if max_val == 0:
        return 1.0
    diff_pct = abs(a - b) / max_val
    if diff_pct <= 0.01:
        return 0.95
    if diff_pct <= 0.05:
        return 0.7
    if diff_pct <= 0.10:
        return 0.4
    return 0.0


def _score_date_of_service(meta_a: dict, meta_b: dict) -> float:
    """Score based on date of service match."""
    date_a = str(meta_a.get("date_of_service") or "").strip()
    date_b = str(meta_b.get("date_of_service") or "").strip()
    if not date_a or not date_b:
        return 0.0
    if date_a == date_b:
        return 1.0
    # Try parsing and comparing dates
    try:
        from datetime import date

        def _parse(d: str) -> date | None:
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y"):
                try:
                    return datetime.strptime(d, fmt).date()
                except ValueError:
                    continue
            return None

        da, db = _parse(date_a), _parse(date_b)
        if da and db:
            delta = abs((da - db).days)
            if delta == 0:
                return 1.0
            if delta <= 1:
                return 0.8
            if delta <= 7:
                return 0.4
            return 0.0
    except Exception:
        pass
    return 0.0


def _score_provider(meta_a: dict, meta_b: dict) -> float:
    """Score based on provider name match."""
    prov_a = (
        str(
            meta_a.get("provider")
            or meta_a.get("provider_name")
            or meta_a.get("correspondent")
            or ""
        )
        .strip()
        .lower()
    )
    prov_b = (
        str(
            meta_b.get("provider")
            or meta_b.get("provider_name")
            or meta_b.get("correspondent")
            or ""
        )
        .strip()
        .lower()
    )
    if not prov_a or not prov_b:
        return 0.0
    if prov_a == prov_b:
        return 1.0
    return SequenceMatcher(None, prov_a, prov_b).ratio()


def _score_title(meta_a: dict, meta_b: dict) -> float:
    """Score based on document title similarity."""
    title_a = str(meta_a.get("title") or "").strip().lower()
    title_b = str(meta_b.get("title") or "").strip().lower()
    if not title_a or not title_b:
        return 0.0
    if title_a == title_b:
        return 1.0
    return SequenceMatcher(None, title_a, title_b).ratio()


def _score_content_hash(meta_a: dict, meta_b: dict) -> float:
    """Score based on content hash similarity."""
    hash_a = str(meta_a.get("content_hash") or meta_a.get("checksum") or "").strip()
    hash_b = str(meta_b.get("content_hash") or meta_b.get("checksum") or "").strip()
    if not hash_a or not hash_b:
        return 0.0
    if hash_a == hash_b:
        return 1.0
    return 0.0


# ------------------------------------------------------------------
# Core scoring
# ------------------------------------------------------------------


def score_documents(meta_a: dict, meta_b: dict) -> tuple[float, dict[str, float]]:
    """Score two documents for duplicate likelihood.

    Returns (overall_score, breakdown_dict) where breakdown_dict maps
    signal name → individual score (0.0–1.0).
    """
    scorers = {
        "invoice_number": _score_invoice_number,
        "amount": _score_amount,
        "date_of_service": _score_date_of_service,
        "provider": _score_provider,
        "title": _score_title,
        "content_hash": _score_content_hash,
    }

    breakdown: dict[str, float] = {}
    weighted_total = 0.0

    for signal, scorer in scorers.items():
        score = scorer(meta_a, meta_b)
        breakdown[signal] = round(score, 3)
        weighted_total += score * WEIGHTS[signal]

    return round(weighted_total, 3), breakdown


# ------------------------------------------------------------------
# Detection
# ------------------------------------------------------------------


def get_document_metadata(doc_id: int) -> dict | None:
    """Fetch metadata for a Paperless document. Returns None if unavailable."""
    try:
        from doc_intelligence_hub.modules.eob_matching.database import (
            BillRecord,
            EOBRecord,
        )
        from doc_intelligence_hub.modules.eob_matching.database import (
            get_session as get_eob_session,
        )

        eob_session = get_eob_session()
        try:
            eob = eob_session.query(EOBRecord).filter(EOBRecord.document_id == doc_id).first()
            if eob:
                return {
                    "document_id": doc_id,
                    "title": getattr(eob, "title", None) or f"EOB #{doc_id}",
                    "provider": getattr(eob, "provider_name", None),
                    "provider_name": getattr(eob, "provider_name", None),
                    "amount": getattr(eob, "total_amount", None) or getattr(eob, "amount", None),
                    "date_of_service": getattr(eob, "date_of_service", None),
                    "patient_name": getattr(eob, "patient_name", None),
                    "invoice_number": getattr(eob, "claim_number", None),
                    "claim_number": getattr(eob, "claim_number", None),
                    "content_hash": getattr(eob, "checksum", None),
                    "doc_type": "eob",
                }

            bill = eob_session.query(BillRecord).filter(BillRecord.document_id == doc_id).first()
            if bill:
                return {
                    "document_id": doc_id,
                    "title": getattr(bill, "title", None) or f"Bill #{doc_id}",
                    "provider": getattr(bill, "provider_name", None)
                    or getattr(bill, "correspondent", None),
                    "provider_name": getattr(bill, "provider_name", None)
                    or getattr(bill, "correspondent", None),
                    "amount": getattr(bill, "amount", None) or getattr(bill, "total_amount", None),
                    "date_of_service": getattr(bill, "date_of_service", None),
                    "invoice_number": getattr(bill, "invoice_number", None),
                    "content_hash": getattr(bill, "checksum", None),
                    "doc_type": "bill",
                }
        finally:
            eob_session.close()
    except ImportError:
        logger.debug("EOB matching module not available for metadata lookup")
    return None


def detect_duplicates(doc_id: int) -> list[dict[str, Any]]:
    """Score a document against all others and return potential duplicates above threshold.

    Returns list of dicts with keys: doc_id, similarity_score, breakdown.
    """
    meta_a = get_document_metadata(doc_id)
    if not meta_a:
        return []

    try:
        from doc_intelligence_hub.modules.eob_matching.database import (
            BillRecord,
            EOBRecord,
        )
        from doc_intelligence_hub.modules.eob_matching.database import (
            get_session as get_eob_session,
        )
    except ImportError:
        return []

    eob_session = get_eob_session()
    results: list[dict[str, Any]] = []

    try:
        # Collect all document IDs to compare against
        all_doc_ids: set[int] = set()
        for row in eob_session.query(EOBRecord.document_id).all():
            all_doc_ids.add(row[0])
        for row in eob_session.query(BillRecord.document_id).all():
            all_doc_ids.add(row[0])
        all_doc_ids.discard(doc_id)
    finally:
        eob_session.close()

    for other_id in all_doc_ids:
        meta_b = get_document_metadata(other_id)
        if not meta_b:
            continue

        overall, breakdown = score_documents(meta_a, meta_b)
        if overall >= DUPLICATE_THRESHOLD:
            results.append(
                {
                    "doc_id": other_id,
                    "similarity_score": overall,
                    "breakdown": breakdown,
                    "metadata": meta_b,
                }
            )

    results.sort(key=lambda r: r["similarity_score"], reverse=True)
    return results


def _cleanup_self_referencing_pairs() -> int:
    """Remove any duplicate pairs where doc_a_id == doc_b_id (data from prior bug).

    Also removes associated pending triage queue items.
    Returns number of invalid pairs removed.
    """
    from doc_intelligence_hub.modules.triage.database import (
        DocumentDuplicate,
        TriageQueueItem,
    )

    session = get_triage_session()
    try:
        invalid_pairs = (
            session.query(DocumentDuplicate)
            .filter(DocumentDuplicate.doc_a_id == DocumentDuplicate.doc_b_id)
            .all()
        )
        if not invalid_pairs:
            return 0

        invalid_ids = [p.id for p in invalid_pairs]
        logger.info("Cleaning up %d self-referencing duplicate pairs", len(invalid_ids))

        # Remove associated triage queue items
        session.query(TriageQueueItem).filter(
            TriageQueueItem.target_type == "document_duplicate",
            TriageQueueItem.target_id.in_(invalid_ids),
        ).delete(synchronize_session="fetch")

        # Remove the invalid pairs
        for pair in invalid_pairs:
            session.delete(pair)

        session.commit()
        return len(invalid_ids)
    except Exception:
        session.rollback()
        logger.exception("Failed to clean up self-referencing duplicate pairs")
        return 0
    finally:
        session.close()


def scan_all_duplicates() -> dict[str, Any]:
    """Full scan for duplicates across all documents. Creates DB records and triage items.

    Returns summary stats.
    """
    # Clean up any self-referencing pairs from a prior bug
    cleaned = _cleanup_self_referencing_pairs()

    try:
        from doc_intelligence_hub.modules.eob_matching.database import (
            BillRecord,
            EOBRecord,
        )
        from doc_intelligence_hub.modules.eob_matching.database import (
            get_session as get_eob_session,
        )
    except ImportError:
        logger.warning("EOB matching module not available, skipping duplicate scan")
        return {"pairs_found": 0, "pairs_created": 0, "triage_items_created": 0, "cleaned_invalid": cleaned}

    eob_session = get_eob_session()
    all_doc_ids: list[int] = []

    try:
        # Use a set to deduplicate — a document_id may have multiple rows
        # from different processing runs
        unique_doc_ids: set[int] = set()
        for row in eob_session.query(EOBRecord.document_id).all():
            unique_doc_ids.add(row[0])
        for row in eob_session.query(BillRecord.document_id).all():
            unique_doc_ids.add(row[0])
        all_doc_ids = list(unique_doc_ids)
    finally:
        eob_session.close()

    # Collect metadata for all docs
    meta_cache: dict[int, dict] = {}
    for did in all_doc_ids:
        meta = get_document_metadata(did)
        if meta:
            meta_cache[did] = meta

    pairs_found = 0
    pairs_created = 0
    triage_created = 0
    seen_pairs: set[tuple[int, int]] = set()

    for i, doc_a_id in enumerate(all_doc_ids):
        meta_a = meta_cache.get(doc_a_id)
        if not meta_a:
            continue

        for doc_b_id in all_doc_ids[i + 1 :]:
            if doc_b_id == doc_a_id:
                continue
            meta_b = meta_cache.get(doc_b_id)
            if not meta_b:
                continue

            pair_key = (min(doc_a_id, doc_b_id), max(doc_a_id, doc_b_id))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            overall, breakdown = score_documents(meta_a, meta_b)
            if overall < DUPLICATE_THRESHOLD:
                continue

            pairs_found += 1

            # Check if pair already exists
            existing = find_existing_duplicate_pair(doc_a_id, doc_b_id)
            if existing:
                continue

            # Create duplicate pair record
            pair = create_duplicate_pair(
                doc_a_id=doc_a_id,
                doc_b_id=doc_b_id,
                similarity_score=overall,
                breakdown=breakdown,
            )
            pairs_created += 1

            # Create triage queue item
            provider_a = meta_a.get("provider") or meta_a.get("provider_name") or "Unknown"
            provider_b = meta_b.get("provider") or meta_b.get("provider_name") or "Unknown"
            score_pct = round(overall * 100, 1)

            create_queue_item(
                item_type="duplicate_document",
                source="auto_flag",
                target_type="document_duplicate",
                target_id=pair["id"],
                reason=f"Potential duplicate: {score_pct}% similar (docs #{doc_a_id} & #{doc_b_id})",
                priority=_priority_from_similarity(overall),
                metadata={
                    "duplicate_pair_id": pair["id"],
                    "doc_a_id": doc_a_id,
                    "doc_b_id": doc_b_id,
                    "similarity_score": overall,
                    "score_pct": score_pct,
                    "provider_a": provider_a,
                    "provider_b": provider_b,
                    "breakdown": breakdown,
                },
            )
            triage_created += 1

    logger.info(
        "Duplicate scan complete: %d pairs found, %d created, %d triage items",
        pairs_found,
        pairs_created,
        triage_created,
    )
    return {
        "pairs_found": pairs_found,
        "pairs_created": pairs_created,
        "triage_items_created": triage_created,
        "cleaned_invalid": cleaned,
    }


# ------------------------------------------------------------------
# Merge / Resolve
# ------------------------------------------------------------------


def merge_documents(
    pair_id: str,
    primary_doc_id: int,
    relationship: str,
) -> dict[str, Any]:
    """Execute a duplicate merge.

    1. Resolve the duplicate pair record
    2. Tag the archived doc in Paperless with `duplicate-of:{primary_id}`
    3. Transfer EOB match links to primary
    4. Resolve associated triage queue item

    Args:
        pair_id: The duplicate pair ID
        primary_doc_id: The document to keep as primary
        relationship: 'true_duplicate' or 'superseded'

    Returns the resolved duplicate pair dict.
    """
    # Resolve the pair
    resolved = resolve_duplicate_pair(pair_id, relationship, primary_doc_id)
    if not resolved:
        raise ValueError(f"Duplicate pair {pair_id} not found")

    # Determine which doc is archived
    archived_doc_id = (
        resolved["doc_b_id"] if resolved["doc_a_id"] == primary_doc_id else resolved["doc_a_id"]
    )

    # Tag the archived document in Paperless
    _tag_archived_in_paperless(archived_doc_id, primary_doc_id)

    # Transfer EOB match links
    _transfer_eob_links(archived_doc_id, primary_doc_id)

    # Resolve the triage queue item
    _resolve_triage_item(pair_id, relationship)

    return resolved


def resolve_not_duplicate(pair_id: str) -> dict[str, Any]:
    """Mark a pair as not duplicate and resolve the triage item."""
    resolved = resolve_duplicate_pair(pair_id, "not_duplicate")
    if not resolved:
        raise ValueError(f"Duplicate pair {pair_id} not found")

    _resolve_triage_item(pair_id, "not_duplicate")
    return resolved


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _priority_from_similarity(score: float) -> int:
    """Map similarity score to triage priority."""
    if score >= 0.90:
        return 85
    if score >= 0.80:
        return 75
    if score >= 0.70:
        return 65
    return 55


def _tag_archived_in_paperless(archived_doc_id: int, primary_doc_id: int) -> None:
    """Tag the archived document in Paperless with duplicate-of:{primary_id}.

    Uses the Paperless REST API to:
    1. Resolve or create the tag name
    2. Add the tag to the document
    Since PaperlessClient is async, we use asyncio.run for the sync context.
    """
    try:
        import asyncio

        from doc_intelligence_hub.core.paperless import PaperlessClient
        from doc_intelligence_hub.modules.statements.config import load_config, resolve_api_token

        # Get config the same way routers/__init__.py does
        config = None
        with contextlib.suppress(Exception):
            config = load_config()

        base_url = None
        token = None
        if config:
            base_url = config.source.paperless_url
            token = resolve_api_token(config)

        if not base_url or not token:
            logger.warning(
                "Paperless not configured, skipping tag write for doc %d", archived_doc_id
            )
            return

        tag_name = f"duplicate-of:{primary_doc_id}"

        async def _apply_tag() -> None:
            client = PaperlessClient(base_url=base_url, token=token)
            try:
                # Resolve or create tag
                tags = await client.list_tags()
                tag_id = None
                for t in tags:
                    if t.get("name") == tag_name:
                        tag_id = t["id"]
                        break

                if tag_id is None:
                    # Create the tag via the Paperless API
                    http_client = client._get_client()
                    resp = await http_client.post("/api/tags/", json={"name": tag_name})
                    resp.raise_for_status()
                    tag_id = resp.json()["id"]

                # Get current document tags and add the new one
                doc = await client.get_document(archived_doc_id)
                current_tags = doc.get("tags", [])
                if tag_id not in current_tags:
                    current_tags.append(tag_id)
                    await client.update_document(archived_doc_id, {"tags": current_tags})
            finally:
                await client.aclose()

        # Run async code from sync context
        try:
            loop = asyncio.get_running_loop()
            # Already in an async context — schedule as a task
            loop.create_task(_apply_tag())
        except RuntimeError:
            # No running loop — run synchronously
            asyncio.run(_apply_tag())

        logger.info("Tagged doc %d with '%s'", archived_doc_id, tag_name)
    except ImportError:
        logger.warning("Paperless client not available, skipping tag write")
    except Exception as exc:
        logger.warning("Failed to tag doc %d in Paperless: %s", archived_doc_id, exc)


def _transfer_eob_links(from_doc_id: int, to_doc_id: int) -> None:
    """Transfer any EOB match links from archived doc to primary doc."""
    try:
        from doc_intelligence_hub.modules.eob_matching.database import (
            MatchRecord,
        )
        from doc_intelligence_hub.modules.eob_matching.database import (
            get_session as get_eob_session,
        )

        eob_session = get_eob_session()
        try:
            # Update matches where the archived doc is referenced as EOB
            eob_matches = (
                eob_session.query(MatchRecord)
                .filter(MatchRecord.eob_document_id == from_doc_id)
                .all()
            )
            for match in eob_matches:
                match.eob_document_id = to_doc_id
                logger.info(
                    "Transferred EOB match %s from doc %d to %d", match.id, from_doc_id, to_doc_id
                )

            # Update matches where the archived doc is referenced as bill
            bill_matches = (
                eob_session.query(MatchRecord)
                .filter(MatchRecord.bill_document_id == from_doc_id)
                .all()
            )
            for match in bill_matches:
                match.bill_document_id = to_doc_id
                logger.info(
                    "Transferred bill match %s from doc %d to %d", match.id, from_doc_id, to_doc_id
                )

            if eob_matches or bill_matches:
                eob_session.commit()
        finally:
            eob_session.close()
    except ImportError:
        logger.debug("EOB matching module not available, skipping link transfer")
    except Exception as exc:
        logger.warning("Failed to transfer EOB links: %s", exc)


def _resolve_triage_item(pair_id: str, resolution: str) -> None:
    """Resolve the triage queue item associated with a duplicate pair."""
    session = get_triage_session()
    try:
        item = (
            session.query(TriageQueueItem)
            .filter(TriageQueueItem.item_type == "duplicate_document")
            .filter(TriageQueueItem.target_id == pair_id)
            .filter(TriageQueueItem.status.in_(["pending", "deferred"]))
            .first()
        )
        if item:
            item.status = "resolved"
            item.resolved_at = datetime.now(UTC)
            item.resolved_action = f"duplicate_{resolution}"

            event = CorrectionEvent(
                event_type=f"duplicate_{resolution}",
                target_type="triage_queue",
                target_id=item.id,
                payload_json=json.dumps({"pair_id": pair_id, "resolution": resolution}),
            )
            session.add(event)
            session.commit()
    finally:
        session.close()
