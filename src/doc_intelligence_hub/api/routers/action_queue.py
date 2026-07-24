from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from doc_intelligence_hub.api.routers import get_loaded_statement_config, make_paperless_client
from doc_intelligence_hub.modules.action_queue.analyzer import OllamaAnalyzer
from doc_intelligence_hub.modules.action_queue.config import settings as action_queue_settings
from doc_intelligence_hub.modules.action_queue.database import Action, get_session, init_db
from doc_intelligence_hub.modules.action_queue.pipeline import run_pipeline
from doc_intelligence_hub.modules.statements.config import resolve_api_token

router = APIRouter(prefix="/api/queue", tags=["action-queue"])


class QueueRunRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=500)
    dry_run: bool = True
    force: bool = False


class ActionUpdateRequest(BaseModel):
    status: str = Field(..., pattern=r"^(completed|dismissed|pending)$")
    dry_run: bool = True


def _sync_action_queue_settings(request: Request) -> None:
    hub_settings = request.app.state.hub_settings
    statement_config = get_loaded_statement_config(request)

    if hub_settings.paperless_url:
        action_queue_settings.paperless_url = hub_settings.paperless_url
    elif statement_config and statement_config.source.paperless_url:
        action_queue_settings.paperless_url = statement_config.source.paperless_url

    token = hub_settings.resolved_paperless_token or (resolve_api_token(statement_config) if statement_config else None)
    if token:
        action_queue_settings.paperless_api_token = token

    action_queue_settings.write_to_paperless = hub_settings.write_to_paperless
    action_queue_settings.ollama_url = hub_settings.ollama_url
    action_queue_settings.ollama_model = hub_settings.ollama_model


def _build_preview_url(document_id: int | None) -> str | None:
    """Build a Paperless document preview URL, or None if unavailable."""
    paperless_base = action_queue_settings.paperless_url.rstrip("/")
    if not paperless_base or not document_id:
        return None
    return f"{paperless_base}/documents/{document_id}/details"


def _serialize_action(a: Action) -> dict[str, Any]:
    """Serialize an Action row to a JSON-safe dict with preview_url."""
    return {
        "id": a.id,
        "document_id": a.document_id,
        "document_title": a.document_title,
        "action_type": a.action_type,
        "title": a.title,
        "summary": a.summary,
        "due_date": a.due_date.isoformat() if a.due_date else None,
        "amount": a.amount,
        "urgency": a.urgency,
        "confidence": a.confidence,
        "status": a.status,
        "correspondent": a.correspondent,
        "ai_reasoning": a.ai_reasoning,
        "preview_url": _build_preview_url(a.document_id),
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "completed_at": a.completed_at.isoformat() if a.completed_at else None,
    }


def _database_counts() -> dict[str, int]:
    init_db()
    db = get_session()
    try:
        pending = db.query(Action).filter_by(status="pending").count()
        completed = db.query(Action).filter_by(status="completed").count()
        dismissed = db.query(Action).filter_by(status="dismissed").count()
        return {
            "pending": pending,
            "completed": completed,
            "dismissed": dismissed,
            "total": pending + completed + dismissed,
        }
    finally:
        db.close()


@router.get("/check")
async def queue_check(request: Request) -> dict[str, Any]:
    _sync_action_queue_settings(request)
    client = make_paperless_client(request, timeout=10.0)
    paperless = await client.health_check()
    analyzer = OllamaAnalyzer()
    ollama_ok = await analyzer.health_check()
    return {
        "status": "ok" if ollama_ok else "degraded",
        "module": "action-queue",
        "read_only": not action_queue_settings.write_to_paperless,
        "paperless": paperless,
        "ollama": {
            "status": "ok" if ollama_ok else "error",
            "base_url": analyzer.base_url,
            "model": analyzer.model,
        },
    }


@router.post("/run")
async def queue_run(request: Request, body: QueueRunRequest) -> dict[str, Any]:
    _sync_action_queue_settings(request)
    started_at = datetime.utcnow().isoformat()
    request.app.state.last_queue_status = {
        "status": "running",
        "started_at": started_at,
        "dry_run": body.dry_run,
        "limit": body.limit,
        "read_only": not action_queue_settings.write_to_paperless,
    }

    result = await run_pipeline(limit=body.limit, dry_run=body.dry_run, force=body.force)
    finished_at = datetime.utcnow().isoformat()
    status = {
        "status": "ok",
        "started_at": started_at,
        "finished_at": finished_at,
        "dry_run": body.dry_run,
        "limit": body.limit,
        "read_only": not action_queue_settings.write_to_paperless,
        "result": result,
        "database": _database_counts(),
    }
    request.app.state.last_queue_status = status
    return status


@router.get("/status")
async def queue_status(request: Request) -> dict[str, Any]:
    _sync_action_queue_settings(request)
    base_status = request.app.state.last_queue_status or {"status": "idle"}
    return {
        **base_status,
        "read_only": not action_queue_settings.write_to_paperless,
        "database": _database_counts(),
    }


@router.get("/actions")
async def list_actions(
    request: Request,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List action items from the database with optional status filter."""
    _sync_action_queue_settings(request)
    init_db()
    db = get_session()
    try:
        query = db.query(Action).order_by(Action.created_at.desc())
        if status:
            query = query.filter_by(status=status)
        total = query.count()
        actions = query.offset(offset).limit(limit).all()

        return {
            "actions": [_serialize_action(a) for a in actions],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        db.close()


@router.patch("/actions/{action_id}")
async def update_action(request: Request, action_id: int, body: ActionUpdateRequest) -> dict[str, Any]:
    """Update an action's status (complete, dismiss, or re-open)."""
    _sync_action_queue_settings(request)
    init_db()
    db = get_session()
    try:
        action = db.query(Action).filter_by(id=action_id).first()
        if not action:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"Action {action_id} not found")
        action.status = body.status
        if body.status == "completed":
            action.completed_at = datetime.utcnow()
        elif body.status == "pending":
            action.completed_at = None
        db.commit()
        return _serialize_action(action)
    finally:
        db.close()
