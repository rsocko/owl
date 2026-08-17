"""Tests for the unified configuration system (ARCH-07)."""

from __future__ import annotations

import os
from unittest.mock import patch

from doc_intelligence_hub.core.config import (
    ConfigProvider,
    HubConfig,
    get_config,
    get_config_provider,
    init_config,
)


class TestConfigProvider:
    """Test ConfigProvider hierarchical merging."""

    def test_defaults_without_yaml_or_env(self):
        """Config provider returns sane defaults with no sources."""
        provider = ConfigProvider()
        config = provider.config
        assert isinstance(config, HubConfig)
        assert config.llm.model == "gpt-4o-mini"
        assert config.eob_matching.weights["date"] == 0.30
        assert config.server.port == 8001

    def test_env_vars_override_defaults(self):
        """Environment variables override built-in defaults."""
        env = {
            "PAPERLESS_URL": "http://test-paperless:8000",
            "PAPERLESS_API_TOKEN": "test-token-123",
            "LLM_MODEL": "gpt-4o",
            "HUB_PORT": "9000",
        }
        with patch.dict(os.environ, env, clear=False):
            provider = ConfigProvider()
            config = provider.config
            assert config.paperless.url == "http://test-paperless:8000"
            assert config.paperless.token == "test-token-123"
            assert config.llm.model == "gpt-4o"
            assert config.server.port == 9000

    def test_runtime_overrides_take_priority(self):
        """Runtime overrides (admin API) take highest priority."""
        provider = ConfigProvider()
        provider.set_override("eob_matching.weights.date", 0.40)
        provider.set_override("llm.model", "claude-3")
        config = provider.config
        assert config.eob_matching.weights["date"] == 0.40
        assert config.llm.model == "claude-3"

    def test_clear_overrides(self):
        """Clearing overrides reverts to env/YAML/defaults."""
        provider = ConfigProvider()
        provider.set_override("llm.model", "custom-model")
        assert provider.config.llm.model == "custom-model"

        provider.clear_overrides()
        assert provider.config.llm.model == "gpt-4o-mini"

    def test_get_overrides_snapshot(self):
        """Snapshot returns current active overrides for audit."""
        provider = ConfigProvider()
        provider.set_override("server.port", 3000)
        provider.set_override("retention.alerts_days", 7)
        snapshot = provider.get_overrides_snapshot()
        assert snapshot == {"server.port": 3000, "retention.alerts_days": 7}

    def test_reload_rebuilds_config(self):
        """Reload forces re-evaluation of all sources."""
        provider = ConfigProvider()
        assert provider.config.server.port == 8001
        # Set override and reload
        provider.set_override("server.port", 4000)
        reloaded = provider.reload()
        assert reloaded.server.port == 4000

    def test_deep_merge(self):
        """Deep merge correctly merges nested dicts."""
        base = {"a": {"b": 1, "c": 2}, "d": 3}
        override = {"a": {"b": 10, "e": 5}, "f": 6}
        result = ConfigProvider._deep_merge(base, override)
        assert result == {"a": {"b": 10, "c": 2, "e": 5}, "d": 3, "f": 6}

    def test_set_nested(self):
        """Dot-path setter creates intermediate dicts."""
        data: dict = {}
        ConfigProvider._set_nested(data, "a.b.c", 42)
        assert data == {"a": {"b": {"c": 42}}}


class TestModuleSingleton:
    """Test module-level singleton functions."""

    def test_init_and_get(self):
        """init_config creates provider, get_config returns config."""
        provider = init_config()
        config = get_config()
        assert isinstance(config, HubConfig)
        assert get_config_provider() is provider
