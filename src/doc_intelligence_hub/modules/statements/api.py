from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse

from doc_intelligence_hub.modules.statements.config import load_config, resolve_api_token
from doc_intelligence_hub.modules.statements.database import Database
from doc_intelligence_hub.modules.statements.detector import discover_providers
from doc_intelligence_hub.modules.statements.recommendations import build_recommendations
from doc_intelligence_hub.modules.statements.service import load_documents, run_discovery, run_recommendations, validate_source_config

_STATIC_DIR = Path(__file__).parent / "static"


def create_app(config_path: str) -> FastAPI:
    app = FastAPI(title="Statement Tracker Phase 1")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/discovery/run")
    async def discovery() -> dict:
        result = await run_discovery(config_path)
        return result.model_dump(mode="json")

    @app.post("/api/recommendations/run")
    async def recommendations(as_of: date = Query(...)) -> dict:
        result = await run_recommendations(config_path, as_of)
        return result.model_dump(mode="json")

    @app.get("/api/discovery/stream")
    async def discovery_stream():
        """SSE endpoint that streams progress during discovery."""
        return StreamingResponse(
            _discovery_event_generator(config_path),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/recommendations/stream")
    async def recommendations_stream(as_of: date = Query(...)):
        """SSE endpoint that streams progress during recommendations."""
        return StreamingResponse(
            _recommendations_event_generator(config_path, as_of),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ----- Provider management endpoints -----

    @app.get("/api/providers/overrides")
    async def get_overrides() -> dict:
        config = load_config(config_path)
        db = Database(config.runtime.database_path)
        try:
            return db.get_provider_overrides()
        finally:
            db.close()

    @app.post("/api/providers/{provider_key}/override")
    async def set_override(provider_key: str, body: dict) -> dict:
        config = load_config(config_path)
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

    @app.delete("/api/providers/{provider_key}/override")
    async def delete_override(provider_key: str) -> dict:
        config = load_config(config_path)
        db = Database(config.runtime.database_path)
        try:
            db.delete_provider_override(provider_key)
            return {"status": "ok", "provider_key": provider_key}
        finally:
            db.close()

    @app.get("/api/config/paperless-url")
    async def get_paperless_url() -> dict:
        config = load_config(config_path)
        return {"paperless_url": config.source.paperless_url}

    @app.get("/api/documents/{doc_id}/thumb")
    async def document_thumbnail(doc_id: int):
        """Proxy thumbnail from Paperless (requires auth token)."""
        config = load_config(config_path)
        token = resolve_api_token(config)
        url = f"{config.source.paperless_url}/api/documents/{doc_id}/thumb/"
        async with httpx.AsyncClient(verify=config.source.verify_ssl) as client:
            resp = await client.get(url, headers={"Authorization": f"Token {token}"}, timeout=15)
            if resp.status_code == 200:
                return Response(
                    content=resp.content,
                    media_type=resp.headers.get("content-type", "image/webp"),
                    headers={"Cache-Control": "public, max-age=86400"},
                )
            return Response(status_code=resp.status_code)

    @app.get("/api/documents/{doc_id}/preview")
    async def document_preview(doc_id: int):
        """Proxy PDF preview from Paperless (requires auth token)."""
        config = load_config(config_path)
        token = resolve_api_token(config)
        url = f"{config.source.paperless_url}/api/documents/{doc_id}/preview/"
        async with httpx.AsyncClient(verify=config.source.verify_ssl, follow_redirects=True) as client:
            resp = await client.get(url, headers={"Authorization": f"Token {token}"}, timeout=30)
            if resp.status_code == 200:
                return Response(
                    content=resp.content,
                    media_type=resp.headers.get("content-type", "application/pdf"),
                    headers={"Cache-Control": "public, max-age=3600"},
                )
            return Response(status_code=resp.status_code)

    return app


async def _discovery_event_generator(config_path: str):
    """Generate SSE events for discovery with progress updates."""
    from doc_intelligence_hub.modules.statements.config import load_config
    from doc_intelligence_hub.modules.statements.hints import apply_hints
    from doc_intelligence_hub.modules.statements.models import DiscoveryResult
    from doc_intelligence_hub.modules.statements.service import _inject_document_type_mapping, _save_to_database, _write_snapshot

    config = load_config(config_path)
    _inject_document_type_mapping(config)

    # We need to yield from inside the generator, so use a queue
    queue: asyncio.Queue = asyncio.Queue()

    async def send_progress(stage, message, current, total):
        await queue.put({"stage": stage, "message": message, "current": current, "total": total})

    async def run_task():
        try:
            validate_source_config(config)
            documents = await load_documents(config, on_progress=send_progress)
            await send_progress("analyzing", "Analyzing patterns...", 0, 0)
            result = discover_providers(documents, config.analysis)
            if config.provider_hints:
                result = DiscoveryResult(
                    analyzed_documents=result.analyzed_documents,
                    providers=apply_hints(result.providers, documents, config.provider_hints),
                )
            _write_snapshot(config.runtime.snapshot_path, result.model_dump(mode="json"))
            _save_to_database(config.runtime.database_path, result)
            await queue.put({"stage": "complete", "result": result.model_dump(mode="json")})
        except Exception as e:
            await queue.put({"stage": "error", "message": str(e)})

    task = asyncio.create_task(run_task())

    while True:
        event = await queue.get()
        data = json.dumps(event)
        yield f"data: {data}\n\n"
        if event["stage"] in ("complete", "error"):
            break

    await task


async def _recommendations_event_generator(config_path: str, as_of: date):
    """Generate SSE events for recommendations with progress updates."""
    from doc_intelligence_hub.modules.statements.config import load_config
    from doc_intelligence_hub.modules.statements.hints import apply_hints
    from doc_intelligence_hub.modules.statements.service import _inject_document_type_mapping, _save_recommendations_to_database, _save_to_database, _write_snapshot

    config = load_config(config_path)
    _inject_document_type_mapping(config)

    queue: asyncio.Queue = asyncio.Queue()

    async def send_progress(stage, message, current, total):
        await queue.put({"stage": stage, "message": message, "current": current, "total": total})

    async def run_task():
        try:
            validate_source_config(config)
            documents = await load_documents(config, on_progress=send_progress)
            await send_progress("analyzing", "Discovering providers...", 0, 0)
            discovery = discover_providers(documents, config.analysis)
            providers = discovery.providers
            if config.provider_hints:
                providers = apply_hints(providers, documents, config.provider_hints)
            await send_progress("recommending", "Calculating recommendations...", 0, 0)
            result = build_recommendations(
                providers,
                as_of,
                max_inactive_cycles=config.analysis.max_inactive_cycles_for_recommendations,
                max_recommendations_per_provider=config.analysis.max_recommendations_per_provider,
            )
            _save_recommendations_to_database(config.runtime.database_path, result)
            await queue.put({"stage": "complete", "result": result.model_dump(mode="json")})
        except Exception as e:
            await queue.put({"stage": "error", "message": str(e)})

    task = asyncio.create_task(run_task())

    while True:
        event = await queue.get()
        data = json.dumps(event)
        yield f"data: {data}\n\n"
        if event["stage"] in ("complete", "error"):
            break

    await task
