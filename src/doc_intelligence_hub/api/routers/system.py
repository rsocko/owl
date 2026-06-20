from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from doc_intelligence_hub.api.routers import get_loaded_statement_config, make_paperless_client
from doc_intelligence_hub.modules.action_queue.analyzer import OllamaAnalyzer
from doc_intelligence_hub.modules.action_queue.config import settings as action_queue_settings

router = APIRouter(prefix="/api", tags=["system"])


async def _paperless_status(request: Request) -> dict[str, Any]:
    try:
        client = make_paperless_client(request, timeout=10.0)
        health = await client.health_check()
        return {"status": "ok", **health}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


async def _statement_status(request: Request) -> dict[str, Any]:
    config = get_loaded_statement_config(request)
    if config is None:
        return {
            "status": "error",
            "config_path": str(request.app.state.statement_tracker_config),
            "message": "Statement tracker config could not be loaded.",
        }
    return {
        "status": "ok",
        "config_path": str(request.app.state.statement_tracker_config),
        "source_mode": config.source.mode,
        "paperless_url": config.source.paperless_url,
    }


async def _queue_status(request: Request) -> dict[str, Any]:
    hub_settings = request.app.state.hub_settings
    action_queue_settings.ollama_url = hub_settings.ollama_url
    action_queue_settings.ollama_model = hub_settings.ollama_model
    action_queue_settings.write_to_paperless = hub_settings.write_to_paperless
    analyzer = OllamaAnalyzer()
    return {
        "status": "ok" if await analyzer.health_check() else "degraded",
        "ollama_url": analyzer.base_url,
        "ollama_model": analyzer.model,
        "last_run": request.app.state.last_queue_status,
        "write_to_paperless": request.app.state.hub_settings.write_to_paperless,
    }


def _eob_status(request: Request) -> dict[str, Any]:
    return {
        "status": "ok",
        "read_only": True,
        "last_run_available": request.app.state.last_eob_results is not None,
    }


@router.get("/status")
async def api_status(request: Request) -> dict[str, Any]:
    paperless = await _paperless_status(request)
    statements = await _statement_status(request)
    queue = await _queue_status(request)
    eob = _eob_status(request)

    module_states = [paperless["status"], statements["status"], queue["status"], eob["status"]]
    overall = "error" if "error" in module_states else "degraded" if "degraded" in module_states else "ok"

    return {
        "status": overall,
        "service": "document-intelligence-hub",
        "modules": {
            "paperless": paperless,
            "statements": statements,
            "eob_matching": eob,
            "action_queue": queue,
        },
    }


@router.get("/paperless/health")
async def paperless_health(request: Request) -> dict[str, Any]:
    return await _paperless_status(request)


@router.get("/paperless/stats")
async def paperless_stats(request: Request) -> dict[str, Any]:
    client = make_paperless_client(request, timeout=20.0)
    health = await client.health_check()
    tags = await client.list_tags()
    correspondents = await client.list_correspondents()
    return {
        "status": "ok",
        "document_count": health.get("documents", 0),
        "tag_count": len(tags),
        "correspondent_count": len(correspondents),
        "tags": [{"id": item["id"], "name": item["name"]} for item in tags],
        "correspondents": [{"id": item["id"], "name": item["name"]} for item in correspondents],
    }
