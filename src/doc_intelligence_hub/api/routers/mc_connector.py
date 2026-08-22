"""Mission Control connector endpoints.

Exposes the flat-array endpoints that the MC Document Intelligence connector
expects: /api/action-queue/actions, /api/statements/missing, /api/eob/unmatched.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from doc_intelligence_hub.api.routers import get_loaded_statement_config
from doc_intelligence_hub.api.routers.action_queue import _sync_action_queue_settings
from doc_intelligence_hub.modules.action_queue.database import (
    Action,
)
from doc_intelligence_hub.modules.action_queue.database import (
    get_session as get_aq_session,
)
from doc_intelligence_hub.modules.action_queue.database import (
    init_db as aq_init_db,
)
from doc_intelligence_hub.modules.action_queue.lifecycle import (
    VALID_FEEDBACK_TYPES,
    normalize_action_status,
    normalize_utc_datetime,
    record_action_feedback,
    serialize_utc_datetime,
    stored_status_values,
    sync_action_status,
    transition_action_status,
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
logger = logging.getLogger(__name__)


class MCActionUpdateRequest(BaseModel):
    status: str = Field(
        default="done",
        pattern=r"^(pending|reopen|completed|done|dismissed)$",
    )


class MCSnoozeRequest(BaseModel):
    until: datetime


class MCFeedbackRequest(BaseModel):
    feedback_type: str = Field(
        ...,
        pattern=r"^(not_an_action|misclassified|wrong_urgency|wrong_amount)$",
    )
    corrected_action_type: str | None = None
    corrected_urgency: str | None = None
    corrected_amount: float | None = None
    reason: str | None = None


@router.get("/api/action-queue/actions")
async def mc_list_actions(
    request: Request,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    updated_since: datetime | None = None,
) -> list[dict[str, Any]]:
    """List actions as a deterministic flat array for Mission Control reconciliation."""
    _sync_action_queue_settings(request)
    aq_init_db()
    db = get_aq_session()
    try:
        query = db.query(Action)
        if status and status != "all":
            try:
                normalized_filter = normalize_action_status(status)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            query = query.filter(Action.status.in_(stored_status_values(normalized_filter)))
        if updated_since:
            query = query.filter(Action.updated_at >= normalize_utc_datetime(updated_since))
        actions = (
            query.order_by(Action.created_at.desc(), Action.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        paperless_url = getattr(request.app.state.hub_settings, "paperless_url", "") or ""
        base_url = paperless_url.rstrip("/") if paperless_url else ""

        results: list[dict[str, Any]] = []
        for action in actions:
            try:
                normalized_status = normalize_action_status(action.status)
            except ValueError:
                logger.warning(
                    "Action %d has unsupported stored status %r; exposing it as pending",
                    action.id,
                    action.status,
                )
                normalized_status = "pending"
            results.append(
                {
                    "id": str(action.id),
                    "document_id": action.document_id,
                    "document_title": action.document_title or "",
                    "title": action.title or "",
                    # Contract (INTEGRATION-API-CONTRACT.md) requires lowercase enum values —
                    # the DB stores these uppercase (PAY, CRITICAL, ...) for internal use, so
                    # normalize here. Mismatched casing silently breaks MC's isTaskAction() filter.
                    "action_type": (action.action_type or "review").lower(),
                    "category": (action.action_type or "review").lower(),
                    "urgency": (action.urgency or "medium").lower(),
                    "confidence": action.confidence,
                    "due_date": action.due_date.isoformat() if action.due_date else None,
                    "amount": action.amount,
                    "correspondent": action.correspondent,
                    "summary": action.summary or "",
                    "status": normalized_status,
                    "created_at": serialize_utc_datetime(action.created_at),
                    "updated_at": serialize_utc_datetime(action.updated_at),
                    "completed_at": serialize_utc_datetime(action.completed_at),
                    "snoozed_until": serialize_utc_datetime(action.snoozed_until),
                    "document_url": f"{base_url}/documents/{action.document_id}/details"
                    if base_url and action.document_id
                    else None,
                }
            )
        return results
    finally:
        db.close()


@router.patch("/api/action-queue/actions/{action_id}")
async def mc_update_action(
    request: Request,
    action_id: int,
    body: MCActionUpdateRequest | None = None,
) -> dict[str, Any]:
    """Update an action through OWL's shared lifecycle behavior."""
    _sync_action_queue_settings(request)
    internal_status = normalize_action_status((body or MCActionUpdateRequest()).status)
    aq_init_db()
    db = get_aq_session()
    try:
        action = db.query(Action).filter_by(id=action_id).first()
        if not action:
            raise HTTPException(status_code=404, detail=f"Action {action_id} not found")

        changed = transition_action_status(action, internal_status)
        if changed:
            action.version = (action.version or 1) + 1
        db.commit()

        if changed or action.last_synced_status != internal_status:
            await sync_action_status(db, action, internal_status, logger=logger)

        return {"status": "ok", "id": str(action_id), "new_status": internal_status}
    finally:
        db.close()


@router.post("/api/action-queue/actions/{action_id}/snooze")
async def mc_snooze_action(
    request: Request,
    action_id: int,
    body: MCSnoozeRequest,
) -> dict[str, Any]:
    """Snooze the OWL source action until the supplied ISO timestamp."""
    _sync_action_queue_settings(request)
    aq_init_db()
    db = get_aq_session()
    try:
        action = db.query(Action).filter_by(id=action_id).first()
        if not action:
            raise HTTPException(status_code=404, detail=f"Action {action_id} not found")

        changed = transition_action_status(action, "snoozed", snoozed_until=body.until)
        if changed:
            action.version = (action.version or 1) + 1
        db.commit()
        if changed or action.last_synced_status != "snoozed":
            await sync_action_status(db, action, "snoozed", logger=logger)

        return {
            "status": "ok",
            "id": str(action_id),
            "new_status": "snoozed",
            "snoozed_until": serialize_utc_datetime(action.snoozed_until),
        }
    finally:
        db.close()


@router.post("/api/action-queue/actions/{action_id}/feedback")
async def mc_submit_feedback(
    request: Request,
    action_id: int,
    body: MCFeedbackRequest,
) -> dict[str, Any]:
    """Record classifier feedback and apply any supplied source correction."""
    _sync_action_queue_settings(request)
    if body.feedback_type not in VALID_FEEDBACK_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported feedback type")

    aq_init_db()
    db = get_aq_session()
    try:
        action = db.query(Action).filter_by(id=action_id).first()
        if not action:
            raise HTTPException(status_code=404, detail=f"Action {action_id} not found")
        try:
            feedback, _ = record_action_feedback(
                db,
                action,
                feedback_type=body.feedback_type,
                corrected_action_type=body.corrected_action_type,
                corrected_urgency=body.corrected_urgency,
                corrected_amount=body.corrected_amount,
                reason=body.reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        db.commit()

        if body.feedback_type == "not_an_action":
            await sync_action_status(db, action, "not_an_action", logger=logger)

        return {
            "status": "ok",
            "id": str(action_id),
            "feedback_id": feedback.id,
            "feedback_type": body.feedback_type,
            "action_status": action.status,
            "action_type": action.action_type,
            "urgency": action.urgency,
            "amount": action.amount,
        }
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
