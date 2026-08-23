from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class SourceConfig(BaseModel):
    mode: str = "fixture"
    fixture_path: str | None = None
    paperless_url: str | None = None
    api_token: str | None = Field(default=None, exclude=True, repr=False)
    api_token_env: str | None = None
    verify_ssl: bool = True
    timeout_seconds: int = 30


class ProviderHintGroup(BaseModel):
    """A manually defined sub-group within a correspondent."""

    name: str
    title_match: str  # substring match against normalized title


class ProviderHint(BaseModel):
    """Hints for how to handle a specific correspondent or provider."""

    correspondent: str | None = None
    provider_key: str | None = None
    action: Literal["split", "merge", "rename", "ignore", "define"] = "split"
    # For split action: define sub-groups by title matching
    groups: list[ProviderHintGroup] = Field(default_factory=list)
    # For rename action
    rename_to: str | None = None
    # For define action: manually specify frequency
    frequency: Literal["monthly", "quarterly", "annual"] | None = None
    anchor_day: int | None = None
    # For merge action: merge these provider_keys into one
    merge_keys: list[str] = Field(default_factory=list)


class AnalysisConfig(BaseModel):
    min_documents_for_pattern: int = 3
    monthly_min_days: int = 25
    monthly_max_days: int = 35
    quarterly_min_days: int = 80
    quarterly_max_days: int = 100
    annual_min_days: int = 350
    annual_max_days: int = 380
    allowed_tags: list[str] = Field(default_factory=lambda: ["statement", "bill", "invoice"])
    minimum_title_consistency: float = 0.5
    default_grace_period_days: int = 5
    max_inactive_cycles_for_recommendations: int = 6
    max_recommendations_per_provider: int = 1
    # When set, document_type matching uses this explicit set instead of keyword heuristics.
    # None means no mapping configured (use keyword fallback).
    enabled_document_type_names: set[str] | None = None


class RuntimeConfig(BaseModel):
    snapshot_path: str = "data/catalog.snapshot.json"
    database_path: str = "data/statement_tracker.db"


class ExternalSignalsConfig(BaseModel):
    base_url: str | None = None
    api_token: str | None = Field(default=None, exclude=True, repr=False)
    api_token_env: str | None = None
    verify_ssl: bool = True
    timeout_seconds: int = Field(default=30, ge=1, le=300)


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8001


class AppConfig(BaseModel):
    source: SourceConfig = Field(default_factory=SourceConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    external_signals: ExternalSignalsConfig = Field(default_factory=ExternalSignalsConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    provider_hints: list[ProviderHint] = Field(default_factory=list)


def load_config(config_path: str) -> AppConfig:
    resolved_config_path = Path(config_path).resolve()
    if not resolved_config_path.exists():
        raise FileNotFoundError(
            "Config file not found: "
            f"{resolved_config_path}. "
            "For Paperless runs, use config/config.paperless.yaml or copy "
            "config/config.paperless.example.yaml to that path."
        )
    with resolved_config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    config = AppConfig.model_validate(raw)

    if config.source.fixture_path:
        config.source.fixture_path = str(
            _resolve_path(resolved_config_path.parent, config.source.fixture_path)
        )
    config.runtime.snapshot_path = str(
        _resolve_path(resolved_config_path.parent, config.runtime.snapshot_path)
    )
    config.runtime.database_path = str(
        _resolve_path(resolved_config_path.parent, config.runtime.database_path)
    )
    return config


def _resolve_path(base_dir: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (base_dir / candidate).resolve()


def resolve_api_token(config: AppConfig) -> str | None:
    if config.source.api_token:
        return config.source.api_token
    if not config.source.api_token_env:
        return None
    return os.getenv(config.source.api_token_env)


def resolve_external_signal_token(config: AppConfig) -> str | None:
    if config.external_signals.api_token:
        return config.external_signals.api_token
    if not config.external_signals.api_token_env:
        return None
    return os.getenv(config.external_signals.api_token_env)
