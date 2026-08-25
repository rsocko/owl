"""Triage Queue API router — unified inbox for items needing human review.

Endpoints:
    GET    /api/triage/queue                       — List queue items (with filters)
    POST   /api/triage/queue/populate              — Trigger queue population scan
    POST   /api/triage/queue/bulk                  — Bulk action on multiple items
    POST   /api/triage/queue/bulk-confirm-threshold — Confirm all matches ≥ threshold
    GET    /api/triage/queue/{id}                   — Single item detail
    POST   /api/triage/queue/{id}/resolve           — Resolve item
    POST   /api/triage/queue/{id}/defer             — Defer item
    POST   /api/triage/queue/{id}/dismiss           — Dismiss item
    POST   /api/triage/queue/{id}/undo              — Undo resolution
    GET    /api/triage/stats                        — Counts by type and status
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from doc_intelligence_hub.modules.triage.database import (
    CorrectionEvent,
    bulk_confirm_by_threshold,
    bulk_defer_items,
    bulk_dismiss_items,
    bulk_resolve_items,
    defer_queue_item,
    dismiss_queue_item,
    get_queue_item,
    get_queue_stats,
    list_queue_items,
    resolve_queue_item,
    undo_resolution,
)
from doc_intelligence_hub.modules.triage.database import (
    get_session as get_triage_session,
)
from doc_intelligence_hub.modules.triage.paperless_sync import (
    sync_all_pending,
    sync_correction_to_paperless,
)

router = APIRouter(prefix="/api/triage", tags=["triage"])

# Valid enum values for input validation
_VALID_ITEM_TYPES = {
    "action_classification",
    "duplicate_document",
    "eob_match_review",
    "grouping_anomaly",
    "orphan_document",
}
_VALID_STATUSES = {"pending", "deferred", "resolved", "dismissed"}
_VALID_SORTS = {"priority", "created_at", "type"}

# Orphan deferral period (days)
_ORPHAN_DEFER_DAYS = 30


# ------------------------------------------------------------------
# Request / response models
# ------------------------------------------------------------------


class ResolveRequest(BaseModel):
    action: str = Field(
        ..., description="Resolution action (e.g. 'confirm', 'reject', 'manual_link')"
    )
    payload: dict[str, Any] | None = Field(default=None, description="Action-specific details")


class DeferRequest(BaseModel):
    until: str | None = Field(
        default=None, description="ISO timestamp to defer until (default: 7 days from now)"
    )


class BulkActionRequest(BaseModel):
    action: str = Field(..., description="Bulk action: 'confirm', 'reject', 'defer', or 'dismiss'")
    item_ids: list[str] = Field(
        ..., max_length=200, description="List of triage queue item IDs (max 200)"
    )
    payload: dict[str, Any] | None = Field(
        default=None, description="Action-specific details (e.g. defer until)"
    )


class BulkConfirmThresholdRequest(BaseModel):
    min_confidence: int = Field(
        default=90, ge=0, le=100, description="Minimum confidence percentage"
    )


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/queue")
async def list_queue(
    type: str | None = Query(default=None, description="Filter by item type"),
    status: str | None = Query(default=None, description="Filter by status"),
    sort: str = Query(default="priority", description="Sort field"),
    limit: int = Query(default=50, ge=1, le=200, description="Max items to return"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
) -> dict[str, Any]:
    """List triage queue items with optional filters."""
    if type and type not in _VALID_ITEM_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid type '{type}'. Must be one of: {', '.join(sorted(_VALID_ITEM_TYPES))}",
        )
    if status and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. Must be one of: {', '.join(sorted(_VALID_STATUSES))}",
        )
    if sort not in _VALID_SORTS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sort '{sort}'. Must be one of: {', '.join(sorted(_VALID_SORTS))}",
        )

    items = list_queue_items(
        item_type=type,
        status=status,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "count": len(items), "offset": offset, "limit": limit}


# NOTE: /queue/populate MUST be registered BEFORE /queue/{item_id} to avoid
# FastAPI matching "populate" as an item_id path parameter.
@router.post("/queue/populate")
async def populate() -> dict[str, Any]:
    """Trigger a queue population scan to auto-flag items for triage."""
    from doc_intelligence_hub.modules.triage.populate import populate_queue

    result = populate_queue()
    return result


# NOTE: /queue/bulk* MUST be registered BEFORE /queue/{item_id} to avoid
# FastAPI matching "bulk" as an item_id path parameter.
@router.post("/queue/bulk")
async def bulk_action(body: BulkActionRequest) -> dict[str, Any]:
    """Apply an action to multiple triage queue items at once."""
    if not body.item_ids:
        raise HTTPException(status_code=400, detail="item_ids must not be empty")
    if body.action in {"confirm", "reject", "dismiss"}:
        classification_ids = [
            item_id
            for item_id in body.item_ids
            if (get_queue_item(item_id) or {}).get("item_type") == "action_classification"
        ]
        if classification_ids:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Action classifications require an explicit confirm, correct, "
                    "no_action, or re_evaluate resolution"
                ),
            )
    if body.action in ("confirm", "reject"):
        affected = bulk_resolve_items(body.item_ids, body.action, body.payload)
    elif body.action == "defer":
        until = body.payload.get("until") if body.payload else None
        if until is not None:
            try:
                datetime.fromisoformat(until)
            except (ValueError, TypeError) as exc:
                raise HTTPException(
                    status_code=422, detail=f"Invalid 'until' timestamp: {until}"
                ) from exc
        affected = bulk_defer_items(body.item_ids, until)
    elif body.action == "dismiss":
        affected = bulk_dismiss_items(body.item_ids)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")
    return {"affected": affected}


@router.post("/queue/bulk-confirm-threshold")
async def bulk_confirm_threshold(body: BulkConfirmThresholdRequest) -> dict[str, Any]:
    """Confirm all pending EOB matches at or above a confidence threshold."""
    affected = bulk_confirm_by_threshold(body.min_confidence)
    return {"affected": affected}


@router.get("/queue/{item_id}")
async def get_item(item_id: str) -> dict[str, Any]:
    """Get a single triage queue item by ID."""
    item = get_queue_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Triage item {item_id} not found")
    return item


@router.post("/queue/{item_id}/resolve")
async def resolve_item(item_id: str, body: ResolveRequest) -> dict[str, Any]:
    """Resolve a triage queue item with the given action."""
    current = get_queue_item(item_id)
    if not current:
        raise HTTPException(status_code=404, detail=f"Triage item {item_id} not found")
    if current["item_type"] == "action_classification":
        if current["status"] not in {"pending", "deferred"}:
            raise HTTPException(
                status_code=409,
                detail="This action-classification review is already resolved",
            )
        return await _resolve_action_classification(current, body)
    item = resolve_queue_item(item_id, body.action, body.payload)
    if not item:
        raise HTTPException(status_code=404, detail=f"Triage item {item_id} not found")
    return item


async def _resolve_action_classification(
    item: dict[str, Any], body: ResolveRequest
) -> dict[str, Any]:
    """Apply action review outcomes through Action Queue lifecycle helpers."""
    from doc_intelligence_hub.modules.action_queue.database import (
        Action,
    )
    from doc_intelligence_hub.modules.action_queue.database import (
        get_session as get_action_session,
    )
    from doc_intelligence_hub.modules.action_queue.database import (
        init_db as init_action_db,
    )
    from doc_intelligence_hub.modules.action_queue.lifecycle import (
        action_has_critical_details,
        mark_action_ready,
        project_action_metadata,
        recalculate_action_risk,
        record_action_feedback,
        refresh_recommended_cta,
        route_action_to_review,
        sync_action_status,
    )
    from doc_intelligence_hub.modules.action_queue.pipeline import run_pipeline

    allowed = {"confirm", "correct", "no_action", "re_evaluate"}
    if body.action not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Action classification resolution must be one of: {', '.join(sorted(allowed))}",
        )
    init_action_db()
    db = get_action_session()
    try:
        action = db.query(Action).filter_by(id=int(item["target_id"])).first()
        if not action:
            raise HTTPException(status_code=404, detail="Source action not found")
        payload = body.payload or {}
        original_version = action.version or 1
        if body.action == "confirm":
            if not action_has_critical_details(action):
                return {
                    **item,
                    "action": "confirm",
                    "action_ready": False,
                    "review_state": "needs_review",
                    "reason": "Critical details are still missing.",
                }
            mark_action_ready(action)
        elif body.action == "correct":
            corrected_type = payload.get("action_type")
            if corrected_type and corrected_type.upper() != action.action_type:
                record_action_feedback(
                    db,
                    action,
                    feedback_type="misclassified",
                    corrected_action_type=corrected_type,
                    reason=payload.get("reason") or "Corrected in Needs Review",
                )
            for field in ("title", "summary", "amount", "due_date"):
                if field not in payload:
                    continue
                value = payload[field]
                if field == "due_date" and value:
                    from datetime import date

                    value = date.fromisoformat(value)
                setattr(action, field, value)
            recalculate_action_risk(action)
            refresh_recommended_cta(action)
            if not action_has_critical_details(action):
                reason = "Correction saved, but critical details are still missing."
                action.version = original_version + 1
                db.commit()
                route_action_to_review(db, action, reason=reason)
                db.commit()
                try:
                    await project_action_metadata(action, action_status=None)
                except Exception as exc:
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            f"Correction was saved, but Paperless metadata cleanup failed: {exc}"
                        ),
                    ) from exc
                refreshed_item = get_queue_item(action.review_item_id) or item
                return {
                    **refreshed_item,
                    "action": "correct",
                    "action_ready": False,
                    "review_state": "needs_review",
                    "reason": reason,
                }
            mark_action_ready(action)
        elif body.action == "no_action":
            record_action_feedback(
                db,
                action,
                feedback_type="not_an_action",
                reason=payload.get("reason") or "False positive confirmed in Needs Review",
            )
        else:
            await run_pipeline(document_id=action.document_id, force=True, dry_run=False)
            db.expire_all()
            action = db.query(Action).filter_by(id=int(item["target_id"])).first()
            if not action:
                raise HTTPException(status_code=404, detail="Re-evaluated action not found")
            if action.review_state == "needs_review" or not action.action_ready:
                refreshed_item = get_queue_item(item["id"]) or item
                return {
                    **refreshed_item,
                    "action": "re_evaluate",
                    "action_ready": False,
                    "review_state": "needs_review",
                    "reason": "Re-evaluation still requires review.",
                }

        if body.action in {"confirm", "correct"}:
            try:
                await project_action_metadata(
                    action,
                    action_status=action.status or "pending",
                )
            except Exception as exc:
                db.rollback()
                raise HTTPException(
                    status_code=502,
                    detail=f"Paperless metadata update failed; review remains open: {exc}",
                ) from exc

        action.version = original_version + 1
        db.commit()
        if body.action == "no_action":
            import logging

            await sync_action_status(
                db, action, "not_an_action", logger=logging.getLogger(__name__)
            )
        resolved = resolve_queue_item(item["id"], body.action, payload)
        return {
            **(resolved or item),
            "action_ready": bool(action.action_ready),
            "review_state": action.review_state,
        }
    finally:
        db.close()


@router.post("/queue/{item_id}/defer")
async def defer_item(item_id: str, body: DeferRequest | None = None) -> dict[str, Any]:
    """Defer a triage queue item."""
    until = body.until if body else None
    item = defer_queue_item(item_id, until)
    if not item:
        raise HTTPException(status_code=404, detail=f"Triage item {item_id} not found")
    return item


@router.post("/queue/{item_id}/dismiss")
async def dismiss_item(item_id: str) -> dict[str, Any]:
    """Dismiss a triage queue item."""
    current = get_queue_item(item_id)
    if current and current["item_type"] == "action_classification":
        raise HTTPException(
            status_code=422,
            detail="Use no_action to resolve an action classification as a false positive",
        )
    item = dismiss_queue_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Triage item {item_id} not found")
    return item


@router.post("/queue/{item_id}/undo")
async def undo_item(item_id: str) -> dict[str, Any]:
    """Undo a resolution — reset item back to pending."""
    current = get_queue_item(item_id)
    if current and current["item_type"] == "action_classification":
        raise HTTPException(
            status_code=422,
            detail="Action classification lifecycle changes cannot be undone from triage",
        )
    item = undo_resolution(item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Triage item {item_id} not found")
    return item


@router.get("/stats")
async def stats() -> dict[str, Any]:
    """Get queue statistics — counts by type and status."""
    return get_queue_stats()


# ------------------------------------------------------------------
# Orphan-specific action endpoints
# ------------------------------------------------------------------


def _get_orphan_item(item_id: str) -> dict[str, Any]:
    """Retrieve a triage item and validate it is an orphan_document."""
    item = get_queue_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Triage item {item_id} not found")
    if item["item_type"] != "orphan_document":
        raise HTTPException(
            status_code=400,
            detail=f"Item {item_id} is not an orphan_document (got {item['item_type']})",
        )
    return item


@router.post("/orphans/{item_id}/find-match")
async def orphan_find_match(item_id: str) -> dict[str, Any]:
    """Return match search context for an orphan document so the UI can open the manual match flow."""
    item = _get_orphan_item(item_id)
    meta = item.get("metadata") or {}

    return {
        "item_id": item_id,
        "document_id": meta.get("document_id"),
        "document_type": meta.get("document_type"),
        "provider_name": meta.get("provider_name"),
        "patient_name": meta.get("patient_name"),
        "date_of_service": meta.get("date_of_service"),
        "amount": meta.get("amount"),
        "search_hint": f"Find a matching {'bill' if meta.get('document_type') == 'eob' else 'EOB'} for this document",
    }


@router.post("/orphans/{item_id}/defer")
async def orphan_defer(item_id: str) -> dict[str, Any]:
    """Defer an orphan for 30 days with 'waiting for matching document' reason."""
    _get_orphan_item(item_id)

    until = (datetime.now(UTC) + timedelta(days=_ORPHAN_DEFER_DAYS)).isoformat()
    result = defer_queue_item(item_id, until)
    if not result:
        raise HTTPException(status_code=404, detail=f"Triage item {item_id} not found")

    # Record the correction event
    session = get_triage_session()
    try:
        event = CorrectionEvent(
            event_type="orphan_defer",
            target_type="document",
            target_id=result["target_id"],
            payload_json=json.dumps(
                {"reason": "waiting_for_match", "deferred_days": _ORPHAN_DEFER_DAYS}
            ),
        )
        session.add(event)
        session.commit()
    finally:
        session.close()

    return result


@router.post("/paperless-sync")
async def paperless_sync() -> dict[str, Any]:
    """Manually trigger sync of all pending CorrectionEvents to Paperless."""
    result = sync_all_pending()
    return result


def _sync_latest_correction(target_id: str) -> None:
    """Find and sync the most recent CorrectionEvent for a given target."""
    event_id = None
    session = get_triage_session()
    try:
        event = (
            session.query(CorrectionEvent)
            .filter(
                CorrectionEvent.target_id == target_id,
                CorrectionEvent.paperless_synced == 0,
                CorrectionEvent.undone == 0,
            )
            .order_by(CorrectionEvent.created_at.desc())
            .first()
        )
        if event:
            event_id = event.id
    finally:
        session.close()

    if event_id:
        sync_correction_to_paperless(event_id)


@router.post("/orphans/{item_id}/self-pay")
async def orphan_self_pay(item_id: str) -> dict[str, Any]:
    """Mark an orphan as self-pay / no bill expected."""
    _get_orphan_item(item_id)
    result = resolve_queue_item(
        item_id,
        "self_pay",
        {
            "reason": "Self-pay or no bill expected",
            "paperless_tags": ["no-bill-expected", "self-pay"],
        },
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Triage item {item_id} not found")
    _sync_latest_correction(result["target_id"])
    return {**result, "paperless_tags": ["no-bill-expected", "self-pay"]}


@router.post("/orphans/{item_id}/already-paid")
async def orphan_already_paid(item_id: str) -> dict[str, Any]:
    """Mark an orphan as already paid without requiring a bill document."""
    _get_orphan_item(item_id)
    result = resolve_queue_item(
        item_id,
        "already_paid",
        {
            "reason": "Payment confirmed without bill document",
            "paperless_tags": ["already-paid"],
        },
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Triage item {item_id} not found")
    _sync_latest_correction(result["target_id"])
    return {**result, "paperless_tags": ["already-paid"]}


@router.post("/orphans/{item_id}/not-medical")
async def orphan_not_medical(item_id: str) -> dict[str, Any]:
    """Mark an orphan as misclassified (not a medical document)."""
    _get_orphan_item(item_id)
    result = resolve_queue_item(
        item_id,
        "not_medical",
        {
            "reason": "Document misclassified — not a medical document",
            "paperless_tags": ["not-medical", "misclassified"],
        },
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Triage item {item_id} not found")
    _sync_latest_correction(result["target_id"])
    return {**result, "paperless_tags": ["not-medical", "misclassified"]}
