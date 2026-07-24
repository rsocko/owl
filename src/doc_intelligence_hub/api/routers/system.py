from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel

from doc_intelligence_hub.api.routers import get_loaded_statement_config, make_paperless_client
from doc_intelligence_hub.core.llm import get_llm_settings, health_check as llm_health_check

router = APIRouter(prefix="/api", tags=["system"])


class LLMSettingsUpdate(BaseModel):
    llm_model: str | None = None
    write_to_paperless: bool | None = None


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
    llm_settings = get_llm_settings()
    health = await llm_health_check()
    return {
        "status": "ok" if health.get("status") == "ok" else "degraded",
        "llm_base_url": llm_settings.base_url,
        "llm_model": llm_settings.model,
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


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    """Get current runtime settings (safe subset)."""
    hub = request.app.state.hub_settings
    llm = get_llm_settings()
    return {
        "llm_base_url": llm.base_url,
        "llm_model": llm.model,
        "write_to_paperless": hub.write_to_paperless,
        "paperless_url": hub.paperless_url,
        # Legacy fields for backwards compat with existing admin UI
        "ollama_url": llm.base_url,
        "ollama_model": llm.model,
    }


@router.put("/settings")
async def update_settings(request: Request, body: LLMSettingsUpdate) -> dict[str, Any]:
    """Update runtime settings (persists for this server session)."""
    hub = request.app.state.hub_settings
    changed = []
    if body.llm_model is not None:
        hub.llm_model = body.llm_model
        changed.append("llm_model")
    if body.write_to_paperless is not None:
        hub.write_to_paperless = body.write_to_paperless
        changed.append("write_to_paperless")
    llm = get_llm_settings()
    return {
        "status": "ok",
        "changed": changed,
        "settings": {
            "llm_base_url": llm.base_url,
            "llm_model": hub.llm_model or llm.model,
            "write_to_paperless": hub.write_to_paperless,
        },
    }


@router.get("/documents/{document_id}/preview")
async def document_preview(request: Request, document_id: int) -> dict[str, Any]:
    """Get document metadata and text content for inline preview."""
    client = make_paperless_client(request, timeout=15.0)
    doc = await client.get_document(document_id)
    content = await client.get_document_content(document_id)
    return {
        "id": doc["id"],
        "title": doc.get("title", ""),
        "correspondent": doc.get("correspondent"),
        "tags": doc.get("tags", []),
        "created": doc.get("created"),
        "added": doc.get("added"),
        "content": content[:3000],  # First 3000 chars for preview
        "content_length": len(content),
    }


@router.get("/documents/{document_id}/download")
async def document_download(request: Request, document_id: int) -> Response:
    """Proxy the document PDF/file from Paperless for viewing."""
    client = make_paperless_client(request, timeout=30.0)
    import httpx
    async with httpx.AsyncClient(
        base_url=client.base_url,
        headers={"Authorization": f"Token {client.token}"},
        timeout=30.0,
    ) as http:
        resp = await http.get(f"/api/documents/{document_id}/download/")
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "application/pdf")
        filename = resp.headers.get("content-disposition", f"doc-{document_id}.pdf")
        return Response(
            content=resp.content,
            media_type=content_type,
            headers={"Content-Disposition": filename},
        )
