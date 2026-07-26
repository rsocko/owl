"""Alerts API router — unified alert feed for DI Hub and Mission Control.

Exposes:
    GET  /api/insights/alerts          — list alerts with filters
    PATCH /api/insights/alerts/{id}/acknowledge — acknowledge an alert
    GET  /api/insights/alerts/summary  — counts by severity/module
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from doc_intelligence_hub.core.alerts import (
    Alert,
    cleanup_old_alerts,
    get_session,
    init_db,
)

router = APIRouter(prefix="/api/insights", tags=["alerts"])


def _serialize_alert(a: Alert) -> dict[str, Any]:
    """Serialize an Alert row to a JSON-safe dict."""
    return {
        "id": a.id,
        "alert_type": a.alert_type,
        "severity": a.severity,
        "module": a.module,
        "title": a.title,
        "description": a.description,
        "action_url": a.action_url,
        "metadata": json.loads(a.metadata_json) if a.metadata_json else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
    }


@router.get("/alerts")
async def list_alerts(
    module: str | None = Query(
        default=None, description="Filter by module: statements, eob, action_queue"
    ),
    severity: str | None = Query(
        default=None, description="Filter by severity: critical, high, medium, low, info"
    ),
    acknowledged: bool | None = Query(default=None, description="Filter by acknowledged state"),
    resolved: bool | None = Query(
        default=False, description="Include resolved alerts (default: False)"
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List alerts with optional filters.

    Consumed by Mission Control's unified alert connector and the DI Hub UI.
    """
    init_db()
    db = get_session()
    try:
        query = db.query(Alert).order_by(Alert.created_at.desc())

        if module:
            query = query.filter(Alert.module == module)
        if severity:
            query = query.filter(Alert.severity == severity)
        if acknowledged is True:
            query = query.filter(Alert.acknowledged_at.isnot(None))
        elif acknowledged is False:
            query = query.filter(Alert.acknowledged_at.is_(None))
        if not resolved:
            query = query.filter(Alert.resolved_at.is_(None))

        total = query.count()
        alerts = query.offset(offset).limit(limit).all()

        return {
            "alerts": [_serialize_alert(a) for a in alerts],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        db.close()


@router.patch("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: int) -> dict[str, Any]:
    """Mark an alert as acknowledged."""
    init_db()
    db = get_session()
    try:
        alert = db.query(Alert).filter_by(id=alert_id).first()
        if not alert:
            raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
        alert.acknowledged_at = datetime.now(UTC)
        db.commit()
        return _serialize_alert(alert)
    finally:
        db.close()


@router.patch("/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: int) -> dict[str, Any]:
    """Mark an alert as resolved."""
    init_db()
    db = get_session()
    try:
        alert = db.query(Alert).filter_by(id=alert_id).first()
        if not alert:
            raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
        alert.resolved_at = datetime.now(UTC)
        db.commit()
        return _serialize_alert(alert)
    finally:
        db.close()


@router.get("/alerts/summary")
async def alert_summary() -> dict[str, Any]:
    """Aggregate alert counts by severity and module.

    Useful for dashboard widgets and MC KPI cards.
    """
    init_db()
    db = get_session()
    try:
        # Only count unresolved alerts
        base = db.query(Alert).filter(Alert.resolved_at.is_(None))

        total = base.count()
        unacknowledged = base.filter(Alert.acknowledged_at.is_(None)).count()

        by_severity: dict[str, int] = {}
        for sev in ("critical", "high", "medium", "low", "info"):
            by_severity[sev] = base.filter(Alert.severity == sev).count()

        by_module: dict[str, int] = {}
        for mod in ("statements", "eob", "action_queue"):
            by_module[mod] = base.filter(Alert.module == mod).count()

        return {
            "total": total,
            "unacknowledged": unacknowledged,
            "by_severity": by_severity,
            "by_module": by_module,
        }
    finally:
        db.close()


@router.post("/alerts/cleanup")
async def run_cleanup(days: int = Query(default=30, ge=1)) -> dict[str, Any]:
    """Manually trigger alert retention cleanup."""
    resolved = cleanup_old_alerts(days=days)
    return {"resolved": resolved, "retention_days": days}
