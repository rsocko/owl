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


def _sync_action_queue_settings(request: Request) -> None:
    hub_settings = request.app.state.hub_settings
    statement_config = get_loaded_statement_config(request)

    if hub_settings.paperless_url:
        action_queue_settings.paperless_url = hub_settings.paperless_url
    elif statement_config and statement_config.source.paperless_url:
        action_queue_settings.paperless_url = statement_config.source.paperless_url

    token = hub_settings.resolved_paperless_token or (resolve_api_token(statement_config) if statement_config else None)
    if token:
        action_queue_settings.paperless_token = token

    action_queue_settings.write_to_paperless = hub_settings.write_to_paperless
    action_queue_settings.ollama_url = hub_settings.ollama_url
    action_queue_settings.ollama_model = hub_settings.ollama_model


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

    result = await run_pipeline(limit=body.limit, dry_run=body.dry_run)
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
