"""Tests for n8n rule SSRF protection and webhook validation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doc_intelligence_hub.modules.analysis.models import (
    ContextData,
    RuleConfig,
    RuleRouting,
    RuleTier,
    RuleTrigger,
    TriggerType,
)
from doc_intelligence_hub.modules.analysis.rules.n8n_rules import (
    N8nWebhookRule,
    _validate_webhook_url,
)


def _make_rule(webhook_url: str = "https://n8n.example.com/webhook/test") -> RuleConfig:
    return RuleConfig(
        id="test-n8n",
        name="Test n8n Rule",
        tier=RuleTier.N8N,
        enabled=True,
        trigger=RuleTrigger(type=TriggerType.MANUAL),
        params={"webhook_url": webhook_url, "timeout": 10},
        routing=RuleRouting(),
    )


class TestValidateWebhookUrl:
    """Tests for the SSRF validation function."""

    def test_valid_https_url(self):
        # Note: this may fail in CI without DNS — skip if so
        result = _validate_webhook_url("https://hooks.n8n.cloud/webhook/abc123")
        # Either None (valid) or DNS resolution error — both acceptable
        assert result is None or "resolve" in result

    def test_rejects_file_scheme(self):
        result = _validate_webhook_url("file:///etc/passwd")
        assert result is not None
        assert "scheme" in result.lower() or "Unsupported" in result

    def test_rejects_ftp_scheme(self):
        result = _validate_webhook_url("ftp://example.com/file")
        assert result is not None

    @patch("doc_intelligence_hub.modules.analysis.rules.n8n_rules.socket.getaddrinfo")
    def test_rejects_loopback(self, mock_dns):
        mock_dns.return_value = [
            (2, 1, 6, "", ("127.0.0.1", 443)),  # AF_INET, loopback
        ]
        result = _validate_webhook_url("https://internal-service.local/webhook")
        assert result is not None
        assert "private" in result.lower() or "loopback" in result.lower()

    @patch("doc_intelligence_hub.modules.analysis.rules.n8n_rules.socket.getaddrinfo")
    def test_rejects_private_ip(self, mock_dns):
        mock_dns.return_value = [
            (2, 1, 6, "", ("192.0.2.2", 443)),
        ]
        result = _validate_webhook_url("https://my-server.local/webhook")
        assert result is not None
        assert "private" in result.lower() or "loopback" in result.lower()

    @patch("doc_intelligence_hub.modules.analysis.rules.n8n_rules.socket.getaddrinfo")
    def test_rejects_link_local(self, mock_dns):
        mock_dns.return_value = [
            (2, 1, 6, "", ("169.254.169.254", 80)),  # AWS metadata endpoint
        ]
        result = _validate_webhook_url("http://169.254.169.254/latest/meta-data/")
        assert result is not None

    def test_rejects_no_hostname(self):
        result = _validate_webhook_url("https:///path")
        assert result is not None
        assert "hostname" in result.lower()


class TestN8nWebhookExecution:
    """Tests for N8nWebhookRule execution with SSRF protection."""

    @pytest.mark.asyncio
    async def test_no_webhook_url_returns_error(self):
        rule = _make_rule(webhook_url="")
        instance = N8nWebhookRule(rule)
        ctx = ContextData(current_document={"id": 1})

        result = await instance.execute(ctx)
        assert result.success is False
        assert "No webhook_url" in result.error

    @pytest.mark.asyncio
    @patch("doc_intelligence_hub.modules.analysis.rules.n8n_rules._validate_webhook_url")
    async def test_ssrf_blocked(self, mock_validate):
        mock_validate.return_value = "URL resolves to private address"
        rule = _make_rule(webhook_url="https://internal/webhook")
        instance = N8nWebhookRule(rule)
        ctx = ContextData(current_document={"id": 1})

        result = await instance.execute(ctx)
        assert result.success is False
        assert "Unsafe webhook URL" in result.error

    @pytest.mark.asyncio
    @patch("doc_intelligence_hub.modules.analysis.rules.n8n_rules._validate_webhook_url")
    @patch("doc_intelligence_hub.modules.analysis.rules.n8n_rules.httpx.AsyncClient")
    async def test_successful_webhook_call(self, mock_client_cls, mock_validate):
        mock_validate.return_value = None  # URL is safe

        # Mock response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"findings": [{"title": "Test"}]}'
        mock_resp.json.return_value = {
            "findings": [{"title": "Found something", "severity": "warning"}],
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        rule = _make_rule()
        instance = N8nWebhookRule(rule)
        ctx = ContextData(current_document={"id": 1, "title": "Test Doc"})

        result = await instance.execute(ctx)
        assert result.success is True
