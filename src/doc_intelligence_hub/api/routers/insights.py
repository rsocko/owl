"""Insights API — browsing and managing analysis insights.

Consumed by the Insights Tab UI (issue #870) and Mission Control connector.

Endpoints:
    GET  /api/insights            — List insights with filters
    GET  /api/insights/summary    — Dashboard stats
    GET  /api/insights/alerts     — MC-consumable alerts
    GET  /api/insights/{id}       — Get insight detail
    POST /api/insights/{id}/acknowledge — Mark as acknowledged
    POST /api/insights/{id}/archive     — Archive insight
    POST /api/insights/bulk       — Bulk acknowledge/archive
    GET  /api/insights/history/{series_id} — Trend data for a series
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from doc_intelligence_hub.modules.analysis import database as db
from doc_intelligence_hub.modules.analysis.models import (
    BulkInsightActionRequest,
    InsightListResponse,
    InsightResponse,
    InsightSummaryResponse,
)

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("", response_model=InsightListResponse)
async def list_insights(
    route: str | None = Query(None, description="Filter by route: informational, actionable"),
    status: str | None = Query(None, description="Filter by status: new, viewed, acknowledged, archived"),
    rule_id: str | None = Query(None, description="Filter by rule ID"),
    series_id: str | None = Query(None, description="Filter by series ID"),
    severity: str | None = Query(None, description="Filter by severity: info, notice, warning, critical"),
    insight_type: str | None = Query(None, description="Filter by type: comparison, anomaly, trend, compliance, extraction"),
    correspondent: str | None = Query(None, description="Filter by correspondent/provider name"),
    since: str | None = Query(None, description="Filter by created_at >= ISO timestamp"),
    until: str | None = Query(None, description="Filter by created_at <= ISO timestamp"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List insights with optional filters and pagination."""
    since_dt = datetime.fromisoformat(since) if since else None
    until_dt = datetime.fromisoformat(until) if until else None

    items, total = db.list_insights(
        route=route,
        status=status,
        rule_id=rule_id,
        series_id=series_id,
        severity=severity,
        insight_type=insight_type,
        correspondent=correspondent,
        since=since_dt,
        until=until_dt,
        limit=limit,
        offset=offset,
    )

    return {
        "insights": items,
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/summary", response_model=InsightSummaryResponse)
async def get_summary() -> dict[str, Any]:
    """Get aggregate insight statistics for the dashboard."""
    return db.get_insight_summary()


@router.get("/alerts")
async def get_mc_alerts() -> dict[str, Any]:
    """Get insights flagged for Mission Control.

    Returns insights where mc_alert is set and status is 'new'.
    Consumed by the MC connector's fetchAlerts() alongside
    /api/statements/missing and /api/eob/unmatched.
    """
    alerts = db.get_mc_alerts()
    return {"alerts": alerts, "total": len(alerts)}


@router.get("/history/{series_id}")
async def get_series_history(
    series_id: str,
    metric_name: str | None = Query(None, description="Filter by metric name"),
    limit: int = Query(24, ge=1, le=100),
) -> dict[str, Any]:
    """Get historical trend data for a series."""
    entries = db.get_history_for_series(series_id, metric_name=metric_name, limit=limit)
    return {"entries": entries, "total": len(entries)}


@router.get("/{insight_id}", response_model=InsightResponse)
async def get_insight(insight_id: str) -> dict[str, Any]:
    """Get a single insight with full detail."""
    insight = db.get_insight(insight_id)
    if not insight:
        raise HTTPException(status_code=404, detail=f"Insight '{insight_id}' not found")

    # Mark as viewed if still new
    if insight["status"] == "new":
        updated = db.update_insight_status(insight_id, "viewed")
        if updated:
            insight = updated

    return insight


@router.post("/{insight_id}/acknowledge")
async def acknowledge_insight(insight_id: str) -> dict[str, Any]:
    """Mark an insight as acknowledged."""
    result = db.update_insight_status(insight_id, "acknowledged")
    if not result:
        raise HTTPException(status_code=404, detail=f"Insight '{insight_id}' not found")
    return result


@router.post("/{insight_id}/archive")
async def archive_insight(insight_id: str) -> dict[str, Any]:
    """Archive an insight (hide from default views)."""
    result = db.update_insight_status(insight_id, "archived")
    if not result:
        raise HTTPException(status_code=404, detail=f"Insight '{insight_id}' not found")
    return result


@router.post("/bulk")
async def bulk_action(request: BulkInsightActionRequest) -> dict[str, Any]:
    """Bulk acknowledge or archive insights."""
    if request.action not in ("acknowledge", "archive"):
        raise HTTPException(status_code=400, detail="Action must be 'acknowledge' or 'archive'")

    status = "acknowledged" if request.action == "acknowledge" else "archived"
    count = db.bulk_update_insight_status(request.insight_ids, status)

    return {
        "action": request.action,
        "affected": count,
        "total_requested": len(request.insight_ids),
    }
