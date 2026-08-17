from __future__ import annotations

import logging
import signal
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from doc_intelligence_hub.api.routers import (
    action_queue,
    admin,
    alerts,
    analysis,
    dashboard,
    document_types,
    duplicates,
    eob,
    extraction,
    insights,
    mc_connector,
    metadata,
    statements,
    stats,
    system,
    triage,
    webhooks,
)
from doc_intelligence_hub.core.llm import get_llm_settings, validate_model_availability
from doc_intelligence_hub.core.logging_config import configure_logging
from doc_intelligence_hub.core.scheduler import HubScheduler
from doc_intelligence_hub.modules.statements.api import _STATIC_DIR as _STATEMENTS_STATIC_DIR
from doc_intelligence_hub.modules.statements.config import load_config

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_HUB_STATIC_DIR = Path(__file__).resolve().parent / "static"


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
    write_to_paperless: bool = True
    # LLM settings (used by admin UI status display; actual LLM config lives in LLM_* env vars)
    llm_base_url: str = "http://localhost:11434/v1"
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
        return self.paperless_api_token or self.paperless_token


def _load_statement_tracker_config(path: str) -> Any | None:
    try:
        return load_config(path)
    except Exception:
        return None


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        detail = (
            exc.detail
            if isinstance(exc.detail, dict)
            else {"code": "http_error", "message": str(exc.detail)}
        )
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
    configure_logging()
    settings = settings or HubSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Manage startup/shutdown lifecycle."""
        logger = logging.getLogger("doc_intelligence_hub.lifecycle")

        # --- Startup ---
        # Validate LLM model availability
        llm_settings = get_llm_settings()
        logger.info("LLM config: model=%s base_url=%s", llm_settings.model, llm_settings.base_url)

        result = await validate_model_availability()
        app.state.llm_model_validation = result

        if result.get("available") is False:
            logger.warning(
                "LLM MODEL NOT AVAILABLE: %s — %s",
                llm_settings.model,
                result.get("message", "Model not found in gateway"),
            )
        elif result.get("available") is None:
            logger.warning(
                "Could not verify model availability: %s",
                result.get("message", "Gateway unreachable"),
            )
        else:
            logger.info("LLM model '%s' confirmed available via gateway.", llm_settings.model)

        # Initialize alerts database
        try:
            from doc_intelligence_hub.core.alerts import (
                cleanup_old_alerts,
            )
            from doc_intelligence_hub.core.alerts import (
                init_db as alerts_init_db,
            )

            alerts_init_db()
            resolved = cleanup_old_alerts(days=30)
            logger.info("Alerts DB initialized. Auto-resolved %d stale alerts.", resolved)
        except Exception as exc:
            logger.warning("Could not initialize alerts DB: %s", exc)

        # Initialize triage queue database
        try:
            from doc_intelligence_hub.modules.triage.database import init_db as triage_init_db

            triage_init_db()
            logger.info("Triage queue DB initialized.")
        except Exception as exc:
            logger.warning("Could not initialize triage DB: %s", exc)

        # Initialize analysis engine database and rule registry
        try:
            from doc_intelligence_hub.modules.analysis.database import init_db as analysis_init_db
            from doc_intelligence_hub.modules.analysis.rule_registry import load_rules

            analysis_init_db()
            rules = load_rules()
            logger.info("Analysis engine initialized: %d rules loaded.", len(rules))
        except Exception as exc:
            logger.warning("Could not initialize analysis engine: %s", exc)

        # Start the built-in job scheduler
        scheduler: HubScheduler = app.state.scheduler
        try:
            scheduler.configure()
            scheduler.start()
            logger.info("Built-in scheduler started with %d jobs.", len(scheduler.get_schedules()))
        except Exception as exc:
            logger.warning("Could not start scheduler: %s", exc)

        logger.info("Document Intelligence Hub started successfully.")

        yield

        # --- Shutdown ---
        logger.info("Shutting down Document Intelligence Hub gracefully...")
        scheduler.shutdown()

    app = FastAPI(
        title="Document Intelligence Hub",
        summary="Unified API for statements, EOB matching, and action queue workflows.",
        version="0.2.0",
        openapi_tags=[
            {
                "name": "system",
                "description": "Service health, connectivity, and shared Paperless metadata.",
            },
            {
                "name": "statement-tracker",
                "description": "Statement discovery, recommendations, and provider overrides.",
            },
            {
                "name": "eob-matching",
                "description": "EOB classification, extraction, matching, and Paperless linking.",
            },
            {
                "name": "action-queue",
                "description": "Action Queue connectivity, dry-runs, and pipeline status.",
            },
            {"name": "alerts", "description": "Unified alerts feed across all DI modules."},
            {
                "name": "admin",
                "description": "Admin configuration: scoring weights, schedules, and debugging.",
            },
            {
                "name": "triage",
                "description": "Triage queue for human review of automated decisions.",
            },
            {
                "name": "analysis",
                "description": "Analysis engine — configurable rules, execution, and management.",
            },
            {
                "name": "insights",
                "description": "Browsable insights produced by the analysis engine.",
            },
            {
                "name": "extraction",
                "description": "Account number and entity extraction pipelines.",
            },
            {
                "name": "webhooks",
                "description": "Webhook subscriptions and n8n automation integration.",
            },
            {
                "name": "stats",
                "description": "Aggregate statistics across all DI modules for MC integration.",
            },
        ],
        lifespan=lifespan,
    )

    app.state.hub_settings = settings
    app.state.statement_tracker_config = settings.statement_tracker_config
    app.state.statement_tracker_config_loaded = _load_statement_tracker_config(
        settings.statement_tracker_config
    )
    app.state.last_queue_status = {
        "status": "idle",
        "message": "Action queue has not been run yet.",
    }
    app.state.last_eob_results = None
    app.state.eob_weights = None
    app.state.admin_schedules = None
    app.state.scheduler = HubScheduler(port=settings.port)

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
    app.include_router(alerts.router)
    app.include_router(admin.router)
    app.include_router(document_types.router)
    app.include_router(stats.router)
    app.include_router(mc_connector.router)
    app.include_router(triage.router)
    app.include_router(dashboard.router)
    app.include_router(duplicates.router)
    app.include_router(metadata.router)
    app.include_router(analysis.router)
    app.include_router(insights.router)
    app.include_router(extraction.router)
    app.include_router(webhooks.router)

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(_HUB_STATIC_DIR / "index.html")

    # Built React/Vite assets (hashed JS/CSS bundles). The app itself uses
    # HashRouter, so no server-side catch-all route is needed for deep links.
    _assets_dir = _HUB_STATIC_DIR / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="hub-assets")

    @app.get("/favicon.svg", include_in_schema=False, response_model=None)
    async def favicon() -> FileResponse | JSONResponse:
        favicon_path = _HUB_STATIC_DIR / "favicon.svg"
        if not favicon_path.exists():
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "not_found", "message": "favicon.svg not found"}},
            )
        return FileResponse(favicon_path)

    @app.get("/icons.svg", include_in_schema=False, response_model=None)
    async def icons() -> FileResponse | JSONResponse:
        icons_path = _HUB_STATIC_DIR / "icons.svg"
        if not icons_path.exists():
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "not_found", "message": "icons.svg not found"}},
            )
        return FileResponse(icons_path)

    # Admin dashboard — redirect to main hub UI (admin features merged into Settings)
    @app.get("/admin", include_in_schema=False)
    @app.get("/admin/", include_in_schema=False)
    @app.get("/admin/{path:path}", include_in_schema=False)
    async def admin_redirect(path: str = "") -> RedirectResponse:
        return RedirectResponse(url="/#/settings", status_code=301)

    # Legacy statement tracker dashboard at /statements/
    app.mount(
        "/statements",
        StaticFiles(directory=str(_STATEMENTS_STATIC_DIR), html=True),
        name="statements-static",
    )

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

    return app


def main() -> None:
    configure_logging()
    logger = logging.getLogger("doc_intelligence_hub.main")
    settings = HubSettings()

    # Graceful shutdown: translate SIGTERM (Docker stop) into clean Uvicorn exit
    def _handle_signal(signum: int, _frame: object) -> None:
        sig_name = signal.Signals(signum).name
        logger.info("Received %s, initiating graceful shutdown...", sig_name)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("Starting Document Intelligence Hub on %s:%d", settings.host, settings.port)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


__all__ = ["HubSettings", "create_app", "main"]
