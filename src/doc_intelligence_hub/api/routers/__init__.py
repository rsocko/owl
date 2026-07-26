"""Router exports and shared API helpers."""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import HTTPException, Request

from doc_intelligence_hub.core.paperless import PaperlessClient
from doc_intelligence_hub.modules.statements.config import AppConfig, load_config, resolve_api_token


def raise_api_error(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> NoReturn:
    raise HTTPException(
        status_code=status_code, detail={"code": code, "message": message, "details": details or {}}
    )


def get_statement_config_path(request: Request) -> str:
    return str(request.app.state.statement_tracker_config)


def load_statement_config_from_request(request: Request) -> AppConfig:
    return load_config(get_statement_config_path(request))


def get_loaded_statement_config(request: Request) -> AppConfig | None:
    config = getattr(request.app.state, "statement_tracker_config_loaded", None)
    if config is None:
        config_path = get_statement_config_path(request)
        try:
            config = load_config(config_path)
        except Exception:
            return None
        request.app.state.statement_tracker_config_loaded = config
    return config


def make_paperless_client(request: Request, *, timeout: float = 15.0) -> PaperlessClient:
    settings = request.app.state.hub_settings
    statement_config = get_loaded_statement_config(request)

    base_url = settings.paperless_url or (
        statement_config.source.paperless_url if statement_config else None
    )
    token = settings.resolved_paperless_token or (
        resolve_api_token(statement_config) if statement_config else None
    )
    verify_ssl = statement_config.source.verify_ssl if statement_config else True

    if not base_url or not token:
        raise_api_error(
            503,
            "paperless_not_configured",
            "Paperless connection settings are incomplete.",
            {"paperless_url": bool(base_url), "paperless_token": bool(token)},
        )

    return PaperlessClient(
        base_url=base_url,
        token=token,
        verify_ssl=verify_ssl,
        timeout=timeout,
    )


__all__ = [
    "get_loaded_statement_config",
    "get_statement_config_path",
    "load_statement_config_from_request",
    "make_paperless_client",
    "raise_api_error",
]
