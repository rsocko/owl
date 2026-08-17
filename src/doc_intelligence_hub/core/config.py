"""Unified hierarchical configuration system.

Consolidates configuration from multiple sources into a single source of truth:

    Priority (highest wins):
    1. Admin API runtime overrides (app.state)
    2. Environment variables
    3. YAML config file
    4. Built-in defaults

This replaces the fragmented pattern of env vars, YAML, admin API, and app.state
being accessed inconsistently across modules.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration Sections
# ---------------------------------------------------------------------------


class PaperlessConfig(BaseModel):
    """Paperless-ngx connection settings."""

    url: str = ""
    token: str = ""
    verify_ssl: bool = True
    timeout_seconds: int = 30


class LLMConfig(BaseModel):
    """LLM/AI gateway settings."""

    base_url: str = "http://localhost:11434/v1"
    model: str = "gpt-4o-mini"
    timeout_seconds: float = 60.0


class EOBMatchingConfig(BaseModel):
    """EOB matching module settings."""

    weights: dict[str, float] = Field(default_factory=lambda: {
        "date": 0.30,
        "provider": 0.25,
        "patient": 0.20,
        "amount": 0.15,
        "procedures": 0.10,
    })
    write_to_paperless: bool = True
    database_url: str = "sqlite:///data/eob_matching.db"


class ActionQueueConfig(BaseModel):
    """Action queue module settings."""

    confidence_threshold: int = 70
    tags_to_monitor: list[str] = Field(default_factory=lambda: ["Inbox"])
    write_to_paperless: bool = True
    database_url: str = "sqlite:///./data/actions.db"
    rate_limit_delay: float = 0.25
    pipeline_max_duration_seconds: float = 300.0


class ScheduleConfig(BaseModel):
    """Schedule settings for a single job."""

    cron: str = "0 */6 * * *"
    enabled: bool = True
    limit: int | None = None


class SchedulesConfig(BaseModel):
    """All scheduled job configurations."""

    statement_discovery: ScheduleConfig = Field(default_factory=ScheduleConfig)
    statement_gap_check: ScheduleConfig = Field(default_factory=ScheduleConfig)
    action_queue: ScheduleConfig = Field(default_factory=ScheduleConfig)
    eob_matching: ScheduleConfig = Field(default_factory=ScheduleConfig)


class RetentionConfig(BaseModel):
    """Data retention policy settings."""

    processing_history_days: int = 90
    alerts_days: int = 30
    actions_days: int = 365
    matches_days: int = 365
    discovery_runs_days: int = 365


class ServerConfig(BaseModel):
    """HTTP server settings."""

    host: str = "0.0.0.0"
    port: int = 8001
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])


# ---------------------------------------------------------------------------
# Unified Configuration
# ---------------------------------------------------------------------------


class HubConfig(BaseModel):
    """Unified configuration for the Document Intelligence Hub.

    All modules read from this single configuration object.
    """

    paperless: PaperlessConfig = Field(default_factory=PaperlessConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    eob_matching: EOBMatchingConfig = Field(default_factory=EOBMatchingConfig)
    action_queue: ActionQueueConfig = Field(default_factory=ActionQueueConfig)
    schedules: SchedulesConfig = Field(default_factory=SchedulesConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    statement_tracker_config_path: str = ""


# ---------------------------------------------------------------------------
# Configuration Provider
# ---------------------------------------------------------------------------


class ConfigProvider:
    """Hierarchical configuration provider.

    Merges defaults → YAML → env vars → runtime overrides into a single HubConfig.
    Provides get/set for runtime overrides and a snapshot for audit.
    """

    def __init__(self, yaml_path: str | None = None) -> None:
        self._yaml_path = yaml_path
        self._runtime_overrides: dict[str, Any] = {}
        self._config: HubConfig | None = None

    @property
    def config(self) -> HubConfig:
        """Get the resolved configuration (cached after first access)."""
        if self._config is None:
            self._config = self._build_config()
        return self._config

    def reload(self) -> HubConfig:
        """Force-reload configuration from all sources."""
        self._config = None
        return self.config

    def set_override(self, path: str, value: Any) -> None:
        """Set a runtime override (from admin API).

        Args:
            path: Dot-separated path (e.g., "eob_matching.weights.date")
            value: Override value
        """
        self._runtime_overrides[path] = value
        self._config = None  # invalidate cache
        # Avoid logging sensitive values (tokens, secrets)
        safe_paths = {"paperless.token", "llm.api_key"}
        display_value = "***" if any(s in path for s in safe_paths) else value
        logger.info("Config override set: %s = %s", path, display_value)

    def get_override(self, path: str) -> Any | None:
        """Get a specific runtime override, or None if not set."""
        return self._runtime_overrides.get(path)

    def clear_overrides(self) -> None:
        """Clear all runtime overrides (reset to YAML/env defaults)."""
        self._runtime_overrides.clear()
        self._config = None
        logger.info("All config overrides cleared")

    def get_overrides_snapshot(self) -> dict[str, Any]:
        """Return a copy of all active runtime overrides (for audit)."""
        return dict(self._runtime_overrides)

    def _build_config(self) -> HubConfig:
        """Build the merged configuration: defaults → YAML → env → overrides."""
        # Start with defaults
        data: dict[str, Any] = {}

        # Layer 2: YAML file
        if self._yaml_path:
            yaml_data = self._load_yaml(self._yaml_path)
            if yaml_data:
                data = self._deep_merge(data, yaml_data)

        # Layer 3: Environment variables
        env_data = self._load_env_vars()
        if env_data:
            data = self._deep_merge(data, env_data)

        # Layer 4: Runtime overrides
        for path, value in self._runtime_overrides.items():
            self._set_nested(data, path, value)

        # Build the Pydantic model
        try:
            return HubConfig.model_validate(data)
        except Exception as exc:
            logger.error("Failed to validate merged config: %s", exc)
            return HubConfig()

    def _load_yaml(self, path: str) -> dict[str, Any] | None:
        """Load YAML config file if it exists."""
        config_path = Path(path)
        if not config_path.exists():
            logger.debug("YAML config not found: %s", path)
            return None
        try:
            with config_path.open("r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            return self._normalize_yaml(raw)
        except Exception as exc:
            logger.warning("Failed to load YAML config %s: %s", path, exc)
            return None

    def _normalize_yaml(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize YAML keys to match HubConfig structure."""
        result: dict[str, Any] = {}

        # Map source.paperless_url → paperless.url, etc.
        if "source" in raw:
            source = raw["source"]
            paperless: dict[str, Any] = {}
            if "paperless_url" in source:
                paperless["url"] = source["paperless_url"]
            if "verify_ssl" in source:
                paperless["verify_ssl"] = source["verify_ssl"]
            if "timeout_seconds" in source:
                paperless["timeout_seconds"] = source["timeout_seconds"]
            if "api_token_env" in source:
                token = os.getenv(source["api_token_env"], "")
                if token:
                    paperless["token"] = token
            if paperless:
                result["paperless"] = paperless

        if "server" in raw:
            result["server"] = raw["server"]

        if "retention" in raw:
            result["retention"] = raw["retention"]

        if "schedules" in raw:
            result["schedules"] = raw["schedules"]

        return result

    def _load_env_vars(self) -> dict[str, Any]:
        """Load configuration from environment variables."""
        result: dict[str, Any] = {}

        # Paperless
        paperless: dict[str, Any] = {}
        if os.getenv("PAPERLESS_URL"):
            paperless["url"] = os.environ["PAPERLESS_URL"]
        token = os.getenv("PAPERLESS_API_TOKEN") or os.getenv("PAPERLESS_TOKEN")
        if token:
            paperless["token"] = token
        if paperless:
            result["paperless"] = paperless

        # LLM
        llm: dict[str, Any] = {}
        if os.getenv("LLM_BASE_URL"):
            llm["base_url"] = os.environ["LLM_BASE_URL"]
        if os.getenv("LLM_MODEL"):
            llm["model"] = os.environ["LLM_MODEL"]
        if os.getenv("LLM_TIMEOUT_SECONDS"):
            llm["timeout_seconds"] = float(os.environ["LLM_TIMEOUT_SECONDS"])
        if llm:
            result["llm"] = llm

        # Server
        server: dict[str, Any] = {}
        if os.getenv("HUB_HOST"):
            server["host"] = os.environ["HUB_HOST"]
        if os.getenv("HUB_PORT"):
            server["port"] = int(os.environ["HUB_PORT"])
        if os.getenv("CORS_ORIGINS"):
            server["cors_origins"] = [
                o.strip() for o in os.environ["CORS_ORIGINS"].split(",") if o.strip()
            ]
        if server:
            result["server"] = server

        return result

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """Deep-merge override dict into base dict."""
        result = dict(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = ConfigProvider._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    @staticmethod
    def _set_nested(data: dict[str, Any], path: str, value: Any) -> None:
        """Set a value at a dot-separated path in a nested dict."""
        parts = path.split(".")
        current = data
        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value


# ---------------------------------------------------------------------------
# Module-level singleton (initialized during app startup)
# ---------------------------------------------------------------------------

_provider: ConfigProvider | None = None


def init_config(yaml_path: str | None = None) -> ConfigProvider:
    """Initialize the global configuration provider."""
    global _provider
    _provider = ConfigProvider(yaml_path=yaml_path)
    return _provider


def get_config_provider() -> ConfigProvider:
    """Get the global configuration provider (must call init_config first)."""
    global _provider
    if _provider is None:
        _provider = ConfigProvider()
    return _provider


def get_config() -> HubConfig:
    """Convenience: get the resolved configuration."""
    return get_config_provider().config


__all__ = [
    "ActionQueueConfig",
    "ConfigProvider",
    "EOBMatchingConfig",
    "HubConfig",
    "LLMConfig",
    "PaperlessConfig",
    "RetentionConfig",
    "ScheduleConfig",
    "SchedulesConfig",
    "ServerConfig",
    "get_config",
    "get_config_provider",
    "init_config",
]
