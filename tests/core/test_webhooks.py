"""Tests for core.webhooks — outbound dispatcher and WebhookDB."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from doc_intelligence_hub.core.webhooks import (
    VALID_EVENT_TYPES,
    WebhookDB,
    dispatch_to_subscribers,
    dispatch_webhook,
)


@pytest.fixture()
def webhook_db(tmp_path):
    db = WebhookDB(str(tmp_path / "test_webhooks.db"))
    db.connect()
    yield db
    db.close()


# ---------------------------------------------------------------------------
# WebhookDB subscription tests
# ---------------------------------------------------------------------------


class TestWebhookDBSubscriptions:
    def test_add_and_list(self, webhook_db: WebhookDB):
        sub_id = webhook_db.add_subscription(
            "statement.missing", "https://n8n.test/hook", "Test hook"
        )
        assert sub_id > 0
        subs = webhook_db.list_subscriptions()
        assert len(subs) == 1
        assert subs[0]["url"] == "https://n8n.test/hook"
        assert subs[0]["event_type"] == "statement.missing"

    def test_filter_by_event_type(self, webhook_db: WebhookDB):
        webhook_db.add_subscription("statement.missing", "https://a.test")
        webhook_db.add_subscription("statement.found", "https://b.test")
        missing_subs = webhook_db.list_subscriptions(event_type="statement.missing")
        assert len(missing_subs) == 1
        assert missing_subs[0]["url"] == "https://a.test"

    def test_remove_subscription(self, webhook_db: WebhookDB):
        sub_id = webhook_db.add_subscription("statement.missing", "https://a.test")
        assert webhook_db.remove_subscription(sub_id) is True
        assert webhook_db.list_subscriptions() == []

    def test_remove_nonexistent(self, webhook_db: WebhookDB):
        assert webhook_db.remove_subscription(999) is False

    def test_toggle_active(self, webhook_db: WebhookDB):
        sub_id = webhook_db.add_subscription("statement.missing", "https://a.test")
        webhook_db.set_subscription_active(sub_id, False)
        # Inactive subscriptions excluded by default
        assert webhook_db.list_subscriptions(active_only=True) == []
        # But visible when asked
        all_subs = webhook_db.list_subscriptions(active_only=False)
        assert len(all_subs) == 1
        assert all_subs[0]["active"] == 0

    def test_get_urls_for_event_includes_wildcard(self, webhook_db: WebhookDB):
        webhook_db.add_subscription("statement.missing", "https://specific.test")
        webhook_db.add_subscription("*", "https://wildcard.test")
        urls = webhook_db.get_urls_for_event("statement.missing")
        assert set(urls) == {"https://specific.test", "https://wildcard.test"}


# ---------------------------------------------------------------------------
# WebhookDB alert de-duplication tests
# ---------------------------------------------------------------------------


class TestWebhookDBAlertState:
    def test_mark_and_check(self, webhook_db: WebhookDB):
        assert not webhook_db.was_already_alerted("prov-1", "2026-07-01", "statement.missing")
        webhook_db.mark_alerted("prov-1", "2026-07-01", "statement.missing")
        assert webhook_db.was_already_alerted("prov-1", "2026-07-01", "statement.missing")

    def test_clear_specific_date(self, webhook_db: WebhookDB):
        webhook_db.mark_alerted("prov-1", "2026-07-01", "statement.missing")
        webhook_db.mark_alerted("prov-1", "2026-08-01", "statement.missing")
        cleared = webhook_db.clear_alert_state("prov-1", "2026-07-01")
        assert cleared == 1
        assert not webhook_db.was_already_alerted("prov-1", "2026-07-01", "statement.missing")
        assert webhook_db.was_already_alerted("prov-1", "2026-08-01", "statement.missing")

    def test_clear_all_for_provider(self, webhook_db: WebhookDB):
        webhook_db.mark_alerted("prov-1", "2026-07-01", "statement.missing")
        webhook_db.mark_alerted("prov-1", "2026-08-01", "statement.overdue")
        cleared = webhook_db.clear_alert_state("prov-1")
        assert cleared == 2

    def test_found_tombstone_independent_of_missing(self, webhook_db: WebhookDB):
        """A statement.found tombstone doesn't block statement.missing dedup check
        (they're different event_types), but both can coexist."""
        webhook_db.mark_alerted("prov-1", "2026-07-01", "statement.found")
        # statement.missing for the same key/date is NOT yet alerted
        assert not webhook_db.was_already_alerted("prov-1", "2026-07-01", "statement.missing")
        # but statement.found IS
        assert webhook_db.was_already_alerted("prov-1", "2026-07-01", "statement.found")


# ---------------------------------------------------------------------------
# WebhookDB delivery log tests
# ---------------------------------------------------------------------------


class TestWebhookDBDeliveryLog:
    def test_log_and_retrieve(self, webhook_db: WebhookDB):
        webhook_db.log_delivery(
            event_type="statement.missing",
            url="https://a.test",
            payload='{"test": true}',
            status_code=200,
            response_body="OK",
            success=True,
            attempt=1,
        )
        logs = webhook_db.get_recent_logs(limit=10)
        assert len(logs) == 1
        assert logs[0]["success"] == 1
        assert logs[0]["status_code"] == 200

    def test_log_failure(self, webhook_db: WebhookDB):
        webhook_db.log_delivery(
            event_type="statement.missing",
            url="https://a.test",
            payload='{}',
            status_code=None,
            response_body=None,
            success=False,
            attempt=1,
            error="Connection refused",
        )
        logs = webhook_db.get_recent_logs()
        assert logs[0]["success"] == 0
        assert logs[0]["error"] == "Connection refused"


# ---------------------------------------------------------------------------
# dispatch_webhook tests
# ---------------------------------------------------------------------------


class TestDispatchWebhook:
    @pytest.mark.asyncio
    async def test_successful_dispatch(self, webhook_db: WebhookDB):
        mock_response = httpx.Response(200, text="OK")
        with patch("doc_intelligence_hub.core.webhooks.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.post.return_value = mock_response
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await dispatch_webhook(
                "statement.missing",
                {"provider_key": "test"},
                "https://n8n.test/hook",
                db=webhook_db,
            )
            assert result is True
            logs = webhook_db.get_recent_logs()
            assert len(logs) == 1
            assert logs[0]["success"] == 1

    @pytest.mark.asyncio
    async def test_retry_on_failure(self, webhook_db: WebhookDB):
        fail_response = httpx.Response(500, text="Internal Server Error")
        with patch("doc_intelligence_hub.core.webhooks.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.post.return_value = fail_response
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await dispatch_webhook(
                "statement.missing",
                {"provider_key": "test"},
                "https://n8n.test/hook",
                max_retries=2,
                db=webhook_db,
            )
            assert result is False
            logs = webhook_db.get_recent_logs()
            assert len(logs) == 2  # 2 attempts logged

    @pytest.mark.asyncio
    async def test_invalid_event_type(self, webhook_db: WebhookDB):
        result = await dispatch_webhook(
            "invalid.event",
            {"test": True},
            "https://n8n.test/hook",
            db=webhook_db,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_timeout_handling(self, webhook_db: WebhookDB):
        with patch("doc_intelligence_hub.core.webhooks.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.post.side_effect = httpx.TimeoutException("timed out")
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await dispatch_webhook(
                "statement.missing",
                {},
                "https://n8n.test/hook",
                max_retries=1,
                db=webhook_db,
            )
            assert result is False
            logs = webhook_db.get_recent_logs()
            assert logs[0]["error"] == "Request timed out"


# ---------------------------------------------------------------------------
# dispatch_to_subscribers tests
# ---------------------------------------------------------------------------


class TestDispatchToSubscribers:
    @pytest.mark.asyncio
    async def test_dispatches_to_all_subscribers(self, webhook_db: WebhookDB):
        webhook_db.add_subscription("statement.missing", "https://a.test")
        webhook_db.add_subscription("statement.missing", "https://b.test")

        mock_response = httpx.Response(200, text="OK")
        with patch("doc_intelligence_hub.core.webhooks.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.post.return_value = mock_response
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await dispatch_to_subscribers(
                "statement.missing", {"test": True}, webhook_db
            )
            assert len(results) == 2
            assert all(results.values())

    @pytest.mark.asyncio
    async def test_includes_extra_urls(self, webhook_db: WebhookDB):
        mock_response = httpx.Response(200, text="OK")
        with patch("doc_intelligence_hub.core.webhooks.httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.post.return_value = mock_response
            MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await dispatch_to_subscribers(
                "statement.missing",
                {"test": True},
                webhook_db,
                extra_urls=["https://extra.test"],
            )
            assert "https://extra.test" in results

    @pytest.mark.asyncio
    async def test_no_subscribers(self, webhook_db: WebhookDB):
        results = await dispatch_to_subscribers(
            "statement.missing", {"test": True}, webhook_db
        )
        assert results == {}
