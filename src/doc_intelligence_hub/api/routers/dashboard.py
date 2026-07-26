"""Dashboard & Correction History API router.

Endpoints:
    GET    /api/triage/dashboard                — Dashboard stats, queue breakdown, match rate trend, activity feed
    GET    /api/triage/corrections               — Correction history with diffs and sync status
    POST   /api/triage/corrections/{id}/undo     — Undo a correction event
    GET    /api/triage/notifications/config       — Get notification channel configs
    PUT    /api/triage/notifications/config       — Update notification channel config
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from doc_intelligence_hub.modules.triage.database import (
    get_activity_feed,
    get_dashboard_stats,
    get_match_rate_trend,
    get_notification_configs,
    get_queue_stats,
    list_correction_events,
    undo_correction_event,
    upsert_notification_config,
)

router = APIRouter(prefix="/api/triage", tags=["triage"])


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------


class NotificationConfigUpdate(BaseModel):
    channel: str = Field(
        ..., description="Channel name: 'email_digest', 'mc_alerts', or 'mc_badge'"
    )
    enabled: bool = Field(default=True, description="Whether the channel is enabled")
    config: dict[str, Any] | None = Field(
        default=None, description="Channel-specific configuration"
    )


# ------------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------------


@router.get("/dashboard")
async def dashboard() -> dict[str, Any]:
    """Dashboard stats: key metrics, queue breakdown, match rate trend, and activity feed."""
    stats = get_dashboard_stats()
    queue_stats = get_queue_stats()
    trend = get_match_rate_trend(months=6)
    activity = get_activity_feed(limit=15)

    # Compute match rate from latest month
    match_rate = trend[-1]["rate"] if trend and trend[-1]["total"] > 0 else 0

    # Compute extraction accuracy from corrections
    total_corrections = stats.get("total_corrections", 0)
    confirmed = stats.get("confirmed_corrections", 0)
    extraction_accuracy = round(
        (confirmed / total_corrections * 100) if total_corrections > 0 else 0
    )

    return {
        "stats": {
            "pending_count": stats["pending_count"],
            "match_rate": match_rate,
            "triaged_this_month": stats["triaged_this_month"],
            "extraction_accuracy": extraction_accuracy,
        },
        "queue_breakdown": stats["queue_breakdown"],
        "by_status": stats["by_status"],
        "match_rate_trend": trend,
        "activity_feed": activity,
        "queue_stats": queue_stats,
    }


# ------------------------------------------------------------------
# Correction History
# ------------------------------------------------------------------


@router.get("/corrections")
async def corrections(
    event_type: str | None = Query(default=None, description="Filter by event type"),
    target_type: str | None = Query(default=None, description="Filter by target type"),
    include_undone: bool = Query(default=False, description="Include undone corrections"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List correction events for the correction history view."""
    items = list_correction_events(
        limit=limit,
        offset=offset,
        event_type=event_type,
        target_type=target_type,
        include_undone=include_undone,
    )
    return {"items": items, "count": len(items), "offset": offset, "limit": limit}


@router.post("/corrections/{event_id}/undo")
async def undo_correction(event_id: str) -> dict[str, Any]:
    """Undo a correction event — marks it as undone in the audit trail."""
    result = undo_correction_event(event_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Correction event {event_id} not found")
    return result


# ------------------------------------------------------------------
# Notification Config
# ------------------------------------------------------------------


@router.get("/notifications/config")
async def get_notif_config() -> dict[str, Any]:
    """Get all notification channel configurations."""
    configs = get_notification_configs()
    return {"channels": configs}


@router.put("/notifications/config")
async def update_notif_config(body: NotificationConfigUpdate) -> dict[str, Any]:
    """Create or update a notification channel configuration."""
    valid_channels = {"email_digest", "mc_alerts", "mc_badge"}
    if body.channel not in valid_channels:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid channel '{body.channel}'. Must be one of: {', '.join(sorted(valid_channels))}",
        )
    result = upsert_notification_config(body.channel, enabled=body.enabled, config=body.config)
    return result
