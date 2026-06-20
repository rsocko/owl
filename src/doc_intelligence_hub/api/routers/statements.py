from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from starlette.responses import StreamingResponse

from doc_intelligence_hub.api.routers import (
    get_statement_config_path,
    load_statement_config_from_request,
    make_paperless_client,
)
from doc_intelligence_hub.modules.statements.api import (
    _discovery_event_generator,
    _recommendations_event_generator,
)
from doc_intelligence_hub.modules.statements.database import Database
from doc_intelligence_hub.modules.statements.service import run_discovery, run_recommendations

router = APIRouter(tags=["statement-tracker"])


@router.get("/health")
async def statement_health(request: Request) -> dict[str, Any]:
    config = load_statement_config_from_request(request)
    return {
        "status": "ok",
        "module": "statement-tracker",
        "config_path": get_statement_config_path(request),
        "source_mode": config.source.mode,
        "paperless_url": config.source.paperless_url,
    }


@router.post("/discovery/run")
async def discovery_run(request: Request) -> dict[str, Any]:
    result = await run_discovery(get_statement_config_path(request))
    return result.model_dump(mode="json")


@router.post("/recommendations/run")
async def recommendations_run(request: Request, as_of: date = Query(...)) -> dict[str, Any]:
    result = await run_recommendations(get_statement_config_path(request), as_of)
    return result.model_dump(mode="json")


@router.get("/discovery/stream")
async def discovery_stream(request: Request) -> StreamingResponse:
    return StreamingResponse(
        _discovery_event_generator(get_statement_config_path(request)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/recommendations/stream")
async def recommendations_stream(request: Request, as_of: date = Query(...)) -> StreamingResponse:
    return StreamingResponse(
        _recommendations_event_generator(get_statement_config_path(request), as_of),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/providers/overrides")
async def get_provider_overrides(request: Request) -> dict[str, Any]:
    config = load_statement_config_from_request(request)
    db = Database(config.runtime.database_path)
    try:
        return db.get_provider_overrides()
    finally:
        db.close()


@router.post("/providers/{provider_key}/override")
async def set_provider_override(request: Request, provider_key: str, body: dict[str, Any]) -> dict[str, Any]:
    config = load_statement_config_from_request(request)
    db = Database(config.runtime.database_path)
    try:
        db.set_provider_override(
            provider_key=provider_key,
            status=body.get("status", "confirmed"),
            display_name=body.get("display_name"),
            frequency_override=body.get("frequency_override"),
            anchor_day_override=body.get("anchor_day_override"),
            notes=body.get("notes"),
        )
        return {"status": "ok", "provider_key": provider_key}
    finally:
        db.close()


@router.delete("/providers/{provider_key}/override")
async def delete_provider_override(request: Request, provider_key: str) -> dict[str, Any]:
    config = load_statement_config_from_request(request)
    db = Database(config.runtime.database_path)
    try:
        db.delete_provider_override(provider_key)
        return {"status": "ok", "provider_key": provider_key}
    finally:
        db.close()


@router.get("/config/paperless-url")
async def get_paperless_url(request: Request) -> dict[str, Any]:
    config = load_statement_config_from_request(request)
    return {"paperless_url": config.source.paperless_url}


@router.get("/documents/{doc_id}/thumb")
async def document_thumbnail(request: Request, doc_id: int) -> Response:
    client = make_paperless_client(request, timeout=20.0)
    content, media_type = await client.get_document_thumbnail(doc_id)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/documents/{doc_id}/preview")
async def document_preview(request: Request, doc_id: int) -> Response:
    client = make_paperless_client(request, timeout=30.0)
    content, media_type = await client.get_document_preview(doc_id)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )
