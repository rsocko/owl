from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path

from doc_intelligence_hub.modules.statements.config import AppConfig, load_config, resolve_api_token
from doc_intelligence_hub.modules.statements.database import Database
from doc_intelligence_hub.modules.statements.detector import debug_discovery, discover_providers
from doc_intelligence_hub.modules.statements.hints import apply_hints
from doc_intelligence_hub.modules.statements.models import (
    DiscoveryDiagnosticResult,
    DiscoveryResult,
    RecommendationResult,
)
from doc_intelligence_hub.modules.statements.paperless import (
    fetch_paperless_documents,
    load_fixture_documents,
    test_paperless_connection,
)
from doc_intelligence_hub.modules.statements.recommendations import build_recommendations


async def load_documents(config: AppConfig, on_progress=None):
    validate_source_config(config)

    if config.source.mode == "fixture":
        return load_fixture_documents(config.source.fixture_path)

    if config.source.mode == "paperless":
        token = resolve_api_token(config)
        return await fetch_paperless_documents(
            base_url=config.source.paperless_url,
            api_token=token,
            verify_ssl=config.source.verify_ssl,
            timeout_seconds=config.source.timeout_seconds,
            on_progress=on_progress,
        )

    raise ValueError(f"Unsupported source mode: {config.source.mode}")


async def run_discovery(config_path: str) -> DiscoveryResult:
    config = load_config(config_path)
    _inject_document_type_mapping(config)
    documents = await load_documents(config)
    result = discover_providers(documents, config.analysis)
    if config.provider_hints:
        result = DiscoveryResult(
            analyzed_documents=result.analyzed_documents,
            providers=apply_hints(result.providers, documents, config.provider_hints),
        )
    _write_snapshot(config.runtime.snapshot_path, result.model_dump(mode="json"))
    _save_to_database(config.runtime.database_path, result)
    return result


async def run_discovery_debug(config_path: str, limit: int) -> DiscoveryDiagnosticResult:
    config = load_config(config_path)
    _inject_document_type_mapping(config)
    documents = await load_documents(config)
    return debug_discovery(documents, config.analysis, limit=limit)


async def run_recommendations(config_path: str, as_of: date) -> RecommendationResult:
    config = load_config(config_path)
    _inject_document_type_mapping(config)
    documents = await load_documents(config)
    discovery = discover_providers(documents, config.analysis)
    providers = discovery.providers
    if config.provider_hints:
        providers = apply_hints(providers, documents, config.provider_hints)
    result = build_recommendations(
        providers,
        as_of,
        max_inactive_cycles=config.analysis.max_inactive_cycles_for_recommendations,
        max_recommendations_per_provider=config.analysis.max_recommendations_per_provider,
    )
    _save_recommendations_to_database(config.runtime.database_path, result)
    _emit_recommendation_alerts(result)
    await _dispatch_recommendation_webhooks(result)
    return result


async def run_connection_test(config_path: str) -> dict[str, int | str]:
    config = load_config(config_path)
    validate_source_config(config)

    if config.source.mode == "fixture":
        documents = load_fixture_documents(config.source.fixture_path)
        return {
            "status": "ok",
            "mode": "fixture",
            "fixture_path": config.source.fixture_path,
            "documents": len(documents),
        }

    token = resolve_api_token(config)
    result = await test_paperless_connection(
        base_url=config.source.paperless_url,
        api_token=token,
        verify_ssl=config.source.verify_ssl,
        timeout_seconds=config.source.timeout_seconds,
    )
    result["mode"] = "paperless"
    return result


def validate_source_config(config: AppConfig) -> None:
    if config.source.mode == "fixture":
        if not config.source.fixture_path:
            raise ValueError("fixture_path is required for fixture mode")
        if not Path(config.source.fixture_path).exists():
            raise ValueError(f"fixture_path does not exist: {config.source.fixture_path}")
        return

    if config.source.mode == "paperless":
        if not config.source.paperless_url:
            raise ValueError("paperless_url is required for paperless mode")
        if not config.source.api_token_env:
            raise ValueError("api_token_env is required for paperless mode")
        token = resolve_api_token(config)
        if not token:
            raise ValueError(
                f"Environment variable {config.source.api_token_env} is required for paperless mode. "
                f'In PowerShell run: $env:{config.source.api_token_env} = "your-token-here"'
            )
        return

    raise ValueError(f"Unsupported source mode: {config.source.mode}")


def _inject_document_type_mapping(config: AppConfig) -> None:
    """Load document type mapping from DB and inject into analysis config."""
    db = Database(config.runtime.database_path)
    try:
        enabled_names = db.get_enabled_document_type_names()
        if enabled_names is not None:
            config.analysis.enabled_document_type_names = enabled_names
    finally:
        db.close()


def _write_snapshot(snapshot_path: str, payload: dict) -> None:
    path = Path(snapshot_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _save_to_database(database_path: str, result: DiscoveryResult) -> None:
    db = Database(database_path)
    try:
        db.save_discovery(result)
    finally:
        db.close()


def _save_recommendations_to_database(database_path: str, result: RecommendationResult) -> None:
    db = Database(database_path)
    try:
        db.save_recommendations(result)
    finally:
        db.close()


def _emit_recommendation_alerts(result: RecommendationResult) -> None:
    """Emit unified alerts from statement recommendations."""
    try:
        from doc_intelligence_hub.core.alerts import emit_statement_alerts

        recs = [
            {
                "provider_name": r.provider_name,
                "provider_key": r.provider_key,
                "status": r.status,
                "days_late": r.days_late,
                "expected_date": r.expected_date.isoformat(),
            }
            for r in result.recommendations
        ]
        emit_statement_alerts(recs)
    except Exception:
        pass  # Alert emission is best-effort


logger = logging.getLogger(__name__)


async def _dispatch_recommendation_webhooks(result: RecommendationResult) -> None:
    """Fire webhooks for NEW missing/overdue recommendations (best-effort)."""
    try:
        from doc_intelligence_hub.core.webhooks import (
            WebhookDB,
            dispatch_to_subscribers,
        )
    except Exception:
        return

    db_path = os.environ.get("WEBHOOK_DB_PATH", "data/webhook_log.db")
    db = WebhookDB(db_path)

    try:
        db.connect()
    except Exception:
        logger.debug("Could not open webhook DB at %s — skipping webhook dispatch", db_path)
        return

    n8n_url = os.environ.get("N8N_WEBHOOK_URL")
    extra_urls = [n8n_url] if n8n_url else None

    try:
        for rec in result.recommendations:
            event_type = f"statement.{rec.status}"
            expected_str = rec.expected_date.isoformat()

            # Only fire for recommendations we haven't already alerted about
            # Also skip if a "statement.found" tombstone exists for this period
            if db.was_already_alerted(rec.provider_key, expected_str, event_type):
                continue
            if db.was_already_alerted(rec.provider_key, expected_str, "statement.found"):
                continue

            payload = {
                "provider_key": rec.provider_key,
                "provider_name": rec.provider_name,
                "expected_date": expected_str,
                "status": rec.status,
                "priority": rec.priority,
                "days_late": rec.days_late,
                "earliest_date": rec.earliest_date.isoformat(),
                "latest_date": rec.latest_date.isoformat(),
            }

            results = await dispatch_to_subscribers(
                event_type, payload, db, extra_urls=extra_urls
            )

            # Mark as alerted regardless of delivery outcome to prevent
            # repeated fire-and-forget attempts on every cycle
            db.mark_alerted(rec.provider_key, expected_str, event_type)
    except Exception:
        logger.debug("Webhook dispatch failed (best-effort)", exc_info=True)
    finally:
        db.close()
