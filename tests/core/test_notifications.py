"""Tests for core.notifications — Gotify integration and notify_alert."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from doc_intelligence_hub.core.notifications import (
    _GOTIFY_PRIORITY,
    _get_gotify_config,
    notify_alert,
    send_gotify_notification,
)


class TestGotifyConfig:
    def test_returns_none_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("GOTIFY_URL", raising=False)
        monkeypatch.delenv("GOTIFY_TOKEN", raising=False)
        assert _get_gotify_config() is None

    def test_returns_none_when_only_url(self, monkeypatch):
        monkeypatch.setenv("GOTIFY_URL", "https://gotify.example.com")
        monkeypatch.delenv("GOTIFY_TOKEN", raising=False)
        assert _get_gotify_config() is None

    def test_returns_none_when_only_token(self, monkeypatch):
        monkeypatch.delenv("GOTIFY_URL", raising=False)
        monkeypatch.setenv("GOTIFY_TOKEN", "abc123")
        assert _get_gotify_config() is None

    def test_returns_tuple_when_both_set(self, monkeypatch):
        monkeypatch.setenv("GOTIFY_URL", "https://gotify.example.com/")
        monkeypatch.setenv("GOTIFY_TOKEN", "abc123")
        result = _get_gotify_config()
        assert result == ("https://gotify.example.com", "abc123")


class TestSendGotifyNotification:
    def test_skips_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("GOTIFY_URL", raising=False)
        monkeypatch.delenv("GOTIFY_TOKEN", raising=False)
        assert send_gotify_notification("title", "msg") is False

    def test_sends_with_explicit_config(self):
        mock_resp = MagicMock(status_code=200)
        with patch("doc_intelligence_hub.core.notifications.httpx.post", return_value=mock_resp) as mock_post:
            result = send_gotify_notification(
                "Test", "Body", priority=8,
                gotify_url="https://gotify.test",
                gotify_token="tok",
            )
            assert result is True
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            assert call_kwargs.kwargs["params"] == {"token": "tok"}
            assert call_kwargs.kwargs["json"]["title"] == "Test"
            assert call_kwargs.kwargs["json"]["priority"] == 8

    def test_returns_false_on_http_error(self):
        mock_resp = MagicMock(status_code=401, text="Unauthorized")
        with patch("doc_intelligence_hub.core.notifications.httpx.post", return_value=mock_resp):
            result = send_gotify_notification(
                "Test", "Body",
                gotify_url="https://gotify.test",
                gotify_token="tok",
            )
            assert result is False

    def test_returns_false_on_exception(self):
        with patch("doc_intelligence_hub.core.notifications.httpx.post", side_effect=Exception("timeout")):
            result = send_gotify_notification(
                "Test", "Body",
                gotify_url="https://gotify.test",
                gotify_token="tok",
            )
            assert result is False


class TestNotifyAlert:
    def _make_alert(self, severity="high", title="Test", description=None, module="eob", action_url=None):
        alert = MagicMock()
        alert.severity = severity
        alert.title = title
        alert.description = description
        alert.module = module
        alert.action_url = action_url
        return alert

    def test_sends_for_high_severity(self):
        alert = self._make_alert(severity="high", description="Overdue bill")
        with patch("doc_intelligence_hub.core.notifications.send_gotify_notification", return_value=True) as mock_send:
            result = notify_alert(alert)
            assert result is True
            args = mock_send.call_args
            assert "[HIGH]" in args[0][0]
            assert args[1]["priority"] == 8

    def test_sends_for_critical_severity(self):
        alert = self._make_alert(severity="critical")
        with patch("doc_intelligence_hub.core.notifications.send_gotify_notification", return_value=True) as mock_send:
            result = notify_alert(alert)
            assert result is True
            assert mock_send.call_args[1]["priority"] == 10

    def test_includes_action_url(self):
        alert = self._make_alert(action_url="/eob/bills/42")
        with patch("doc_intelligence_hub.core.notifications.send_gotify_notification", return_value=True) as mock_send:
            notify_alert(alert)
            message = mock_send.call_args[0][1]
            assert "/eob/bills/42" in message


class TestGotifyPriorityMapping:
    def test_priority_values(self):
        assert _GOTIFY_PRIORITY["critical"] == 10
        assert _GOTIFY_PRIORITY["high"] == 8
        assert _GOTIFY_PRIORITY["medium"] == 5
        assert _GOTIFY_PRIORITY["low"] == 2
        assert _GOTIFY_PRIORITY["info"] == 1
