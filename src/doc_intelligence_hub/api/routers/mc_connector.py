"""Mission Control connector endpoints.

Exposes the flat-array endpoints that the MC Document Intelligence connector
expects: /api/action-queue/actions, /api/statements/missing, /api/eob/unmatched.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from doc_intelligence_hub.api.routers import get_loaded_statement_config
from doc_intelligence_hub.modules.action_queue.database import (
    Action,
)
from doc_intelligence_hub.modules.action_queue.database import (
    get_session as get_aq_session,
)
from doc_intelligence_hub.modules.action_queue.database import (
    init_db as aq_init_db,
)
from doc_intelligence_hub.modules.eob_matching.database import (
    EOBRecord,
    MatchRecord,
)
from doc_intelligence_hub.modules.eob_matching.database import (
    get_session as get_eob_session,
)
from doc_intelligence_hub.modules.eob_matching.database import (
    init_db as eob_init_db,
)
from doc_intelligence_hub.modules.statements.database import Database as StatementsDB

router = APIRouter(tags=["mc-connector"])


@router.get("/api/action-queue/actions")
async def mc_list_actions(
    request: Request,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List actions as a flat array (Mission Control connector format)."""
    aq_init_db()
    db = get_aq_session()
    try:
        query = db.query(Action).order_by(Action.created_at.desc())
        if status:
            query = query.filter_by(status=status)
        actions = query.limit(limit).all()

        paperless_url = getattr(request.app.state.hub_settings, "paperless_url", "") or ""
        base_url = paperless_url.rstrip("/") if paperless_url else ""

        return [
            {
                "id": str(a.id),
                "document_id": a.document_id,
                "document_title": a.document_title or "",
                "title": a.title or "",
                # Contract (INTEGRATION-API-CONTRACT.md) requires lowercase enum values —
                # the DB stores these uppercase (PAY, CRITICAL, ...) for internal use, so
                # normalize here. Mismatched casing silently breaks MC's isTaskAction() filter.
                "action_type": (a.action_type or "review").lower(),
                "category": (a.action_type or "review").lower(),
                "urgency": (a.urgency or "medium").lower(),
                "confidence": a.confidence,
                "due_date": a.due_date.isoformat() if a.due_date else None,
                "amount": a.amount,
                "correspondent": a.correspondent,
                "summary": a.summary or "",
                "status": a.status or "pending",
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "document_url": f"{base_url}/documents/{a.document_id}/details"
                if base_url and a.document_id
                else None,
            }
            for a in actions
        ]
    finally:
        db.close()


@router.patch("/api/action-queue/actions/{action_id}")
async def mc_update_action(request: Request, action_id: str) -> dict[str, Any]:
    """Update action status (Mission Control connector format).

    Accepts status values from MC: 'done' or 'dismissed'.
    """
    import json

    from fastapi import HTTPException

    body_bytes = await request.body()
    body = json.loads(body_bytes) if body_bytes else {}
    mc_status = body.get("status", "done")

    # Map MC status values to internal status
    status_map = {"done": "completed", "dismissed": "dismissed"}
    internal_status = status_map.get(mc_status, mc_status)

    aq_init_db()
    db = get_aq_session()
    try:
        action = db.query(Action).filter_by(id=int(action_id)).first()
        if not action:
            raise HTTPException(status_code=404, detail=f"Action {action_id} not found")
        action.status = internal_status
        if internal_status == "completed":
            from datetime import datetime

            action.completed_at = datetime.utcnow()
        db.commit()
        return {"status": "ok", "id": action_id, "new_status": internal_status}
    finally:
        db.close()


@router.get("/api/statements/missing")
async def mc_missing_statements(request: Request) -> list[dict[str, Any]]:
    """Return missing statements for Mission Control alerts.

    Returns a flat array of MissingStatement objects derived from the
    latest recommendation run.
    """
    try:
        config = get_loaded_statement_config(request)
        if config is None:
            return []

        db = StatementsDB(config.runtime.database_path)
        try:
            recommendations = db.load_latest_recommendations()
            if not recommendations:
                return []

            results: list[dict[str, Any]] = []
            for rec in recommendations.recommendations:
                if rec.status != "missing":
                    continue

                results.append(
                    {
                        "id": rec.provider_key,
                        "correspondent": rec.provider_name,
                        "correspondent_id": None,
                        "expected_period": rec.expected_date.strftime("%Y-%m"),
                        "frequency": "monthly",
                        "last_received_date": None,
                        "days_overdue": rec.days_late,
                    }
                )

            return results
        finally:
            db.close()
    except Exception:
        return []


@router.get("/api/eob/unmatched")
async def mc_unmatched_eobs(request: Request) -> list[dict[str, Any]]:
    """Return unmatched EOBs for Mission Control alerts.

    Returns EOB records that have no confirmed match, formatted as
    UnmatchedEob objects for the MC connector.
    """
    try:
        eob_init_db()
        db = get_eob_session()
        try:
            # Get EOB document IDs that have confirmed matches
            confirmed_eob_ids = {
                r.eob_document_id
                for r in db.query(MatchRecord.eob_document_id).filter_by(status="confirmed").all()
            }

            # Get all EOB records not in confirmed set
            eobs = db.query(EOBRecord).all()
            paperless_url = getattr(request.app.state.hub_settings, "paperless_url", "") or ""
            base_url = paperless_url.rstrip("/") if paperless_url else ""

            results: list[dict[str, Any]] = []
            for eob in eobs:
                if eob.document_id in confirmed_eob_ids:
                    continue
                eob_status = getattr(eob, "status", None) or "unmatched"
                results.append(
                    {
                        "id": str(eob.id),
                        "provider": eob.provider_name or "Unknown",
                        "amount": eob.total_billed or 0.0,
                        "date_of_service": eob.date_of_service.isoformat()
                        if eob.date_of_service
                        else "",
                        "patient_responsibility": eob.total_patient_responsibility or 0.0,
                        "document_url": f"{base_url}/documents/{eob.document_id}/details"
                        if base_url and eob.document_id
                        else None,
                        "created_at": eob.created_at.isoformat()
                        if hasattr(eob, "created_at") and eob.created_at
                        else None,
                        "doc_type": "eob",
                        "orphaned": eob_status == "orphan",
                        "status": eob_status,
                    }
                )

            return results
        finally:
            db.close()
    except Exception:
        return []


@router.get("/api/triage/badge-count")
async def mc_triage_badge_count() -> dict[str, Any]:
    """Return pending triage count for MC badge display.

    Designed to be polled every 15 minutes by the MC connector.
    """
    try:
        from doc_intelligence_hub.modules.triage.database import (
            get_queue_stats,
        )
        from doc_intelligence_hub.modules.triage.database import (
            init_db as triage_init_db,
        )

        triage_init_db()
        stats = get_queue_stats()
        return {
            "pending": stats.get("pending", 0),
            "total": stats.get("total", 0),
            "by_type": stats.get("by_type", {}),
        }
    except Exception:
        return {"pending": 0, "total": 0, "by_type": {}}
