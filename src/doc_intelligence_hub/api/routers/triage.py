"""Triage Queue API router — unified inbox for items needing human review.

Endpoints:
    GET    /api/triage/queue           — List queue items (with filters)
    GET    /api/triage/queue/{id}      — Single item detail
    POST   /api/triage/queue/{id}/resolve — Resolve item
    POST   /api/triage/queue/{id}/defer   — Defer item
    POST   /api/triage/queue/{id}/dismiss — Dismiss item
    POST   /api/triage/queue/{id}/undo    — Undo resolution
    GET    /api/triage/stats           — Counts by type and status
    POST   /api/triage/queue/populate  — Trigger queue population scan
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from doc_intelligence_hub.modules.triage.database import (
    defer_queue_item,
    dismiss_queue_item,
    get_queue_item,
    get_queue_stats,
    list_queue_items,
    resolve_queue_item,
    undo_resolution,
)

router = APIRouter(prefix="/api/triage", tags=["triage"])


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
    type: str | None = None,
    status: str | None = None,
    sort: str = "priority",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List triage queue items with optional filters."""
    items = list_queue_items(
        item_type=type,
        status=status,
        sort=sort,
        limit=min(limit, 200),
        offset=offset,
    )
    return {"items": items, "count": len(items), "offset": offset, "limit": limit}


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


@router.post("/queue/populate")
async def populate() -> dict[str, Any]:
    """Trigger a queue population scan to auto-flag items for triage."""
    from doc_intelligence_hub.modules.triage.populate import populate_queue

    result = populate_queue()
    return result
