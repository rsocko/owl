from __future__ import annotations

from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from doc_intelligence_hub.api.routers import action_queue, admin, eob, mc_connector, statements, stats, system
from doc_intelligence_hub.core.llm import get_llm_settings, validate_model_availability
from doc_intelligence_hub.modules.statements.api import _STATIC_DIR as _STATEMENTS_STATIC_DIR
from doc_intelligence_hub.modules.statements.config import load_config

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_HUB_STATIC_DIR = Path(__file__).resolve().parent / "static"
_ADMIN_STATIC_DIR = _HUB_STATIC_DIR / "admin"


def _default_statement_tracker_config() -> str:
    candidates = [
        _PROJECT_ROOT / "config" / "config.docker.yaml",
        _PROJECT_ROOT / "config" / "config.paperless.yaml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(candidates[0])


class HubSettings(BaseSettings):
    paperless_url: str | None = None
    paperless_token: str | None = None
    paperless_api_token: str | None = None
    statement_tracker_config: str = Field(default_factory=_default_statement_tracker_config)
    write_to_paperless: bool = False
    # LLM settings (used by admin UI status display; actual LLM config lives in LLM_* env vars)
    llm_base_url: str = "https://service-001.example.invalid/openai/v1"
    llm_model: str = "phi3:mini"
    # Legacy Ollama settings — kept for backwards compat with existing .env files
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "phi3:mini"
    cors_origins: str = "*"
    host: str = "0.0.0.0"
    port: int = 8001

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def resolved_paperless_token(self) -> str | None:
        return self.paperless_token or self.paperless_api_token


def _load_statement_tracker_config(path: str) -> Any | None:
    try:
        return load_config(path)
    except Exception:
        return None


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Invalid request payload.",
                    "details": exc.errors(),
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_server_error",
                    "message": "An unexpected error occurred.",
                    "details": {"type": exc.__class__.__name__, "message": str(exc)},
                }
            },
        )


def create_app(settings: HubSettings | None = None) -> FastAPI:
    settings = settings or HubSettings()

    app = FastAPI(
        title="Document Intelligence Hub",
        summary="Unified API for statements, EOB matching, and action queue workflows.",
        version="0.1.0",
        openapi_tags=[
            {"name": "system", "description": "Service health, connectivity, and shared Paperless metadata."},
            {"name": "statement-tracker", "description": "Statement discovery, recommendations, and provider overrides."},
            {"name": "eob-matching", "description": "EOB classification, extraction, matching, and Paperless linking."},
            {"name": "action-queue", "description": "Action Queue connectivity, dry-runs, and pipeline status."},
            {"name": "admin", "description": "Admin configuration: scoring weights, schedules, and debugging."},
            {"name": "stats", "description": "Aggregate statistics across all DI modules for MC integration."},
        ],
    )

    app.state.hub_settings = settings
    app.state.statement_tracker_config = settings.statement_tracker_config
    app.state.statement_tracker_config_loaded = _load_statement_tracker_config(settings.statement_tracker_config)
    app.state.last_queue_status = {"status": "idle", "message": "Action queue has not been run yet."}
    app.state.last_eob_results = None
    app.state.eob_weights = None
    app.state.admin_schedules = None

    # CORS — allow Mission Control (and other local services) to reach the API
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_exception_handlers(app)

    app.include_router(system.router)
    app.include_router(statements.router, prefix="/api/statements")
    app.include_router(statements.router, prefix="/api", include_in_schema=False)
    app.include_router(eob.router)
    app.include_router(action_queue.router)
    app.include_router(admin.router)
    app.include_router(stats.router)
    app.include_router(mc_connector.router)

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(_HUB_STATIC_DIR / "index.html")

    # Admin dashboard — mount as StaticFiles so /admin, /admin/, and sub-paths all resolve
    app.mount("/admin", StaticFiles(directory=str(_ADMIN_STATIC_DIR), html=True), name="admin-static")

    # Legacy statement tracker dashboard at /statements/
    app.mount("/statements", StaticFiles(directory=str(_STATEMENTS_STATIC_DIR), html=True), name="statements-static")

    @app.get("/health", tags=["system"])
    async def health(request: Request) -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "document-intelligence-hub",
            "version": app.version,
            "statement_tracker_config": request.app.state.statement_tracker_config,
            "modules": {
                "statements": "loaded",
                "eob_matching": "loaded",
                "action_queue": "loaded",
            },
        }

    @app.on_event("startup")
    async def _validate_llm_on_startup() -> None:
        """Validate LLM model availability at startup and log warnings."""
        import logging

        logger = logging.getLogger("doc_intelligence_hub.startup")
        llm_settings = get_llm_settings()
        logger.info(
            "LLM config: model=%s base_url=%s",
            llm_settings.model,
            llm_settings.base_url,
        )

        result = await validate_model_availability()
        app.state.llm_model_validation = result

        if result.get("available") is False:
            logger.warning(
                "⚠️  LLM MODEL NOT AVAILABLE: %s — %s",
                llm_settings.model,
                result.get("message", "Model not found in gateway"),
            )
        elif result.get("available") is None:
            logger.warning(
                "⚠️  Could not verify model availability: %s",
                result.get("message", "Gateway unreachable"),
            )
        else:
            logger.info("✓ LLM model '%s' confirmed available via gateway.", llm_settings.model)

    return app


def main() -> None:
    settings = HubSettings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


__all__ = ["HubSettings", "create_app", "main"]
