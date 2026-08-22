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
    get_triage_setting,
    resolve_duplicate_pair,
)
from doc_intelligence_hub.modules.triage.database import (
    get_session as get_triage_session,
)
from doc_intelligence_hub.modules.triage.relationships import (
    RelationshipConflictError,
    classify_related_notice,
    create_document_relationship,
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
    amt_a = meta_a.get("amount") if meta_a.get("amount") is not None else meta_a.get("total_amount")
    amt_b = meta_b.get("amount") if meta_b.get("amount") is not None else meta_b.get("total_amount")
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
                    "document_date": (
                        eob.created_at.date().isoformat()
                        if getattr(eob, "created_at", None)
                        else None
                    ),
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
                    "due_date": getattr(bill, "due_date", None),
                    "content_hash": getattr(bill, "checksum", None),
                    "document_date": (
                        bill.created_at.date().isoformat()
                        if getattr(bill, "created_at", None)
                        else None
                    ),
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
        return {
            "pairs_found": 0,
            "pairs_created": 0,
            "triage_items_created": 0,
            "cleaned_invalid": cleaned,
        }

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
    relationships_created = 0
    relationship_ids: list[str] = []
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

            proposal = classify_related_notice(doc_a_id, meta_a, doc_b_id, meta_b)
            relationship_conflict = False
            if proposal and proposal.auto_create and proposal.relationship_type != "same_sequence":
                try:
                    relationship, created = create_document_relationship(
                        source_document_id=proposal.source_document_id,
                        target_document_id=proposal.target_document_id,
                        relationship_type=proposal.relationship_type,
                        provenance="automatic",
                        confidence=proposal.confidence,
                        reason_codes=proposal.reason_codes,
                        priority_adjustment=proposal.priority_adjustment,
                        priority_explanation=proposal.priority_explanation,
                    )
                    if created:
                        relationships_created += 1
                        relationship_ids.append(relationship["id"])
                    continue
                except RelationshipConflictError:
                    relationship_conflict = True
                    logger.warning(
                        "Relationship proposal conflicts with an active link for docs %d and %d",
                        doc_a_id,
                        doc_b_id,
                    )

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
            proposal_data = proposal.to_dict() if proposal else None
            priority = _priority_from_similarity(overall)
            if proposal:
                priority = min(100, priority + proposal.priority_adjustment)
            if relationship_conflict:
                priority = min(100, priority + 15)
                proposal_data = {
                    **(proposal_data or {}),
                    "conflict": True,
                    "conflict_priority_adjustment": 15,
                }

            create_queue_item(
                item_type="duplicate_document",
                source="auto_flag",
                target_type="document_duplicate",
                target_id=pair["id"],
                reason=f"Potential duplicate: {score_pct}% similar (docs #{doc_a_id} & #{doc_b_id})",
                priority=priority,
                metadata={
                    "duplicate_pair_id": pair["id"],
                    "doc_a_id": doc_a_id,
                    "doc_b_id": doc_b_id,
                    "similarity_score": overall,
                    "score_pct": score_pct,
                    "provider_a": provider_a,
                    "provider_b": provider_b,
                    "breakdown": breakdown,
                    "relationship_proposal": proposal_data,
                },
            )
            triage_created += 1

    logger.info(
        "Duplicate scan complete: %d pairs found, %d relationships, %d pairs, %d triage items",
        pairs_found,
        relationships_created,
        pairs_created,
        triage_created,
    )
    return {
        "pairs_found": pairs_found,
        "pairs_created": pairs_created,
        "triage_items_created": triage_created,
        "relationships_created": relationships_created,
        "relationship_ids": relationship_ids,
        "cleaned_invalid": cleaned,
    }


def on_document_ingested(document_id: int) -> dict[str, Any]:
    """Hook called after a document is ingested. Runs duplicate detection if enabled.

    Checks the ``duplicate_auto_detect`` triage setting. When the setting is
    ``"true"`` (opt-in), scores the new document against all existing documents
    and creates duplicate-pair / triage-queue records for matches above the
    threshold. When the setting is absent or ``"false"``, this is a no-op.

    Returns a summary dict with detection results or a ``skipped`` flag.
    """
    enabled = get_triage_setting("duplicate_auto_detect", "false")
    if enabled != "true":
        logger.debug("Auto-detect disabled, skipping duplicate check for doc %d", document_id)
        return {"document_id": document_id, "skipped": True, "reason": "auto_detect_disabled"}

    logger.info("Auto-detecting duplicates for newly ingested doc %d", document_id)
    matches = detect_duplicates(document_id)
    if not matches:
        return {
            "document_id": document_id,
            "skipped": False,
            "pairs_created": 0,
            "triage_items_created": 0,
            "relationships_created": 0,
        }

    meta_a = get_document_metadata(document_id)
    pairs_created = 0
    triage_created = 0
    relationships_created = 0
    relationship_ids: list[str] = []

    for match in matches:
        other_id = match["doc_id"]

        # Skip if pair already exists
        existing = find_existing_duplicate_pair(document_id, other_id)
        if existing:
            continue

        overall = match["similarity_score"]
        breakdown = match["breakdown"]
        meta_b = match.get("metadata", {})
        proposal = (
            classify_related_notice(document_id, meta_a, other_id, meta_b) if meta_a else None
        )

        relationship_conflict = False
        if proposal and proposal.auto_create and proposal.relationship_type != "same_sequence":
            try:
                relationship, created = create_document_relationship(
                    source_document_id=proposal.source_document_id,
                    target_document_id=proposal.target_document_id,
                    relationship_type=proposal.relationship_type,
                    provenance="automatic",
                    confidence=proposal.confidence,
                    reason_codes=proposal.reason_codes,
                    priority_adjustment=proposal.priority_adjustment,
                    priority_explanation=proposal.priority_explanation,
                )
                relationships_created += int(created)
                if created:
                    relationship_ids.append(relationship["id"])
                continue
            except RelationshipConflictError:
                relationship_conflict = True
                logger.warning(
                    "Relationship proposal conflicts with an active link for docs %d and %d",
                    document_id,
                    other_id,
                )

        pair = create_duplicate_pair(
            doc_a_id=document_id,
            doc_b_id=other_id,
            similarity_score=overall,
            breakdown=breakdown,
        )
        pairs_created += 1

        provider_a = (
            (meta_a or {}).get("provider") or (meta_a or {}).get("provider_name") or "Unknown"
        )
        provider_b = meta_b.get("provider") or meta_b.get("provider_name") or "Unknown"
        score_pct = round(overall * 100, 1)
        proposal_data = proposal.to_dict() if proposal else None
        priority = _priority_from_similarity(overall)
        if proposal:
            priority = min(100, priority + proposal.priority_adjustment)
        if relationship_conflict:
            priority = min(100, priority + 15)
            proposal_data = {
                **(proposal_data or {}),
                "conflict": True,
                "conflict_priority_adjustment": 15,
            }

        create_queue_item(
            item_type="duplicate_document",
            source="auto_flag",
            target_type="document_duplicate",
            target_id=pair["id"],
            reason=f"Potential duplicate: {score_pct}% similar (docs #{document_id} & #{other_id})",
            priority=priority,
            metadata={
                "duplicate_pair_id": pair["id"],
                "doc_a_id": document_id,
                "doc_b_id": other_id,
                "similarity_score": overall,
                "score_pct": score_pct,
                "provider_a": provider_a,
                "provider_b": provider_b,
                "breakdown": breakdown,
                "relationship_proposal": proposal_data,
            },
        )
        triage_created += 1

    logger.info(
        "Auto-detect for doc %d: %d relationships, %d pairs, %d triage items",
        document_id,
        relationships_created,
        pairs_created,
        triage_created,
    )
    return {
        "document_id": document_id,
        "skipped": False,
        "pairs_created": pairs_created,
        "triage_items_created": triage_created,
        "relationships_created": relationships_created,
        "relationship_ids": relationship_ids,
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
    5. Verify primary document metadata is unchanged (defensive check)

    Args:
        pair_id: The duplicate pair ID
        primary_doc_id: The document to keep as primary
        relationship: 'true_duplicate' or 'superseded'

    Returns the resolved duplicate pair dict.
    """
    # Snapshot primary document metadata before any merge operations
    pre_merge_metadata = get_document_metadata(primary_doc_id)

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

    # Verify primary document metadata is unchanged after merge
    _verify_primary_metadata(primary_doc_id, pre_merge_metadata)

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


def _verify_primary_metadata(primary_doc_id: int, pre_merge_metadata: dict | None) -> None:
    """Defensive check: verify that the primary document's metadata was not altered by the merge.

    Compares a snapshot taken before the merge with a fresh read afterwards.
    Logs a warning if any key metadata fields differ. This is intentionally
    non-blocking — the merge result is not rolled back.
    """
    if pre_merge_metadata is None:
        logger.debug(
            "No pre-merge metadata snapshot for doc %d; skipping verification",
            primary_doc_id,
        )
        return

    post_merge_metadata = get_document_metadata(primary_doc_id)
    if post_merge_metadata is None:
        logger.warning(
            "Could not fetch post-merge metadata for primary doc %d; "
            "metadata preservation could not be verified",
            primary_doc_id,
        )
        return

    checked_fields = [
        "title",
        "provider",
        "provider_name",
        "amount",
        "date_of_service",
        "invoice_number",
        "claim_number",
        "content_hash",
        "patient_name",
    ]

    changed_fields: list[str] = []
    for field in checked_fields:
        before = pre_merge_metadata.get(field)
        after = post_merge_metadata.get(field)
        if before != after:
            changed_fields.append(field)
            logger.warning(
                "Primary doc %d metadata field '%s' changed during merge: %r -> %r",
                primary_doc_id,
                field,
                before,
                after,
            )

    if changed_fields:
        logger.warning(
            "Primary doc %d had %d metadata field(s) change during merge: %s. "
            "This may indicate an unintended side-effect.",
            primary_doc_id,
            len(changed_fields),
            ", ".join(changed_fields),
        )
    else:
        logger.info("Primary doc %d metadata verified unchanged after merge", primary_doc_id)
