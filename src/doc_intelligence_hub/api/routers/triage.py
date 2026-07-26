"""Triage Queue API router — unified inbox for items needing human review.

Endpoints:
    GET    /api/triage/queue           — List queue items (with filters)
    POST   /api/triage/queue/populate  — Trigger queue population scan
    GET    /api/triage/queue/{id}      — Single item detail
    POST   /api/triage/queue/{id}/resolve — Resolve item
    POST   /api/triage/queue/{id}/defer   — Defer item
    POST   /api/triage/queue/{id}/dismiss — Dismiss item
    POST   /api/triage/queue/{id}/undo    — Undo resolution
    GET    /api/triage/stats           — Counts by type and status
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from doc_intelligence_hub.modules.triage.database import (
    CorrectionEvent,
    defer_queue_item,
    dismiss_queue_item,
    get_queue_item,
    get_queue_stats,
    get_session as get_triage_session,
    list_queue_items,
    resolve_queue_item,
    undo_resolution,
)

router = APIRouter(prefix="/api/triage", tags=["triage"])

# Valid enum values for input validation
_VALID_ITEM_TYPES = {"eob_match_review", "grouping_anomaly", "orphan_document"}
_VALID_STATUSES = {"pending", "deferred", "resolved", "dismissed"}
_VALID_SORTS = {"priority", "created_at", "type"}

# Orphan deferral period (days)
_ORPHAN_DEFER_DAYS = 30


# ------------------------------------------------------------------
# Request / response models
# ------------------------------------------------------------------


class ResolveRequest(BaseModel):
    action: str = Field(..., description="Resolution action (e.g. 'confirm', 'reject', 'manual_link')")
    payload: dict[str, Any] | None = Field(default=None, description="Action-specific details")


class DeferRequest(BaseModel):
    until: str | None = Field(default=None, description="ISO timestamp to defer until (default: 7 days from now)")


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
        raise HTTPException(status_code=400, detail=f"Invalid type '{type}'. Must be one of: {', '.join(sorted(_VALID_ITEM_TYPES))}")
    if status and status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status '{status}'. Must be one of: {', '.join(sorted(_VALID_STATUSES))}")
    if sort not in _VALID_SORTS:
        raise HTTPException(status_code=400, detail=f"Invalid sort '{sort}'. Must be one of: {', '.join(sorted(_VALID_SORTS))}")

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
    item = resolve_queue_item(item_id, body.action, body.payload)
    if not item:
        raise HTTPException(status_code=404, detail=f"Triage item {item_id} not found")
    return item


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
    item = dismiss_queue_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Triage item {item_id} not found")
    return item


@router.post("/queue/{item_id}/undo")
async def undo_item(item_id: str) -> dict[str, Any]:
    """Undo a resolution — reset item back to pending."""
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
        raise HTTPException(status_code=400, detail=f"Item {item_id} is not an orphan_document (got {item['item_type']})")
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
            payload_json=json.dumps({"reason": "waiting_for_match", "deferred_days": _ORPHAN_DEFER_DAYS}),
        )
        session.add(event)
        session.commit()
    finally:
        session.close()

    return result


@router.post("/orphans/{item_id}/self-pay")
async def orphan_self_pay(item_id: str) -> dict[str, Any]:
    """Mark an orphan as self-pay / no bill expected."""
    _get_orphan_item(item_id)
    result = resolve_queue_item(item_id, "self_pay", {
        "reason": "Self-pay or no bill expected",
        "paperless_tags": ["no-bill-expected", "self-pay"],
    })
    if not result:
        raise HTTPException(status_code=404, detail=f"Triage item {item_id} not found")
    return {**result, "paperless_tags": ["no-bill-expected", "self-pay"]}


@router.post("/orphans/{item_id}/already-paid")
async def orphan_already_paid(item_id: str) -> dict[str, Any]:
    """Mark an orphan as already paid without requiring a bill document."""
    _get_orphan_item(item_id)
    result = resolve_queue_item(item_id, "already_paid", {
        "reason": "Payment confirmed without bill document",
        "paperless_tags": ["already-paid"],
    })
    if not result:
        raise HTTPException(status_code=404, detail=f"Triage item {item_id} not found")
    return {**result, "paperless_tags": ["already-paid"]}


@router.post("/orphans/{item_id}/not-medical")
async def orphan_not_medical(item_id: str) -> dict[str, Any]:
    """Mark an orphan as misclassified (not a medical document)."""
    _get_orphan_item(item_id)
    result = resolve_queue_item(item_id, "not_medical", {
        "reason": "Document misclassified — not a medical document",
        "paperless_tags": ["not-medical", "misclassified"],
    })
    if not result:
        raise HTTPException(status_code=404, detail=f"Triage item {item_id} not found")
    return {**result, "paperless_tags": ["not-medical", "misclassified"]}
