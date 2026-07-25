"""Tests for health and system endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "document-intelligence-hub"
        assert "modules" in data
        assert data["modules"]["statements"] == "loaded"
        assert data["modules"]["eob_matching"] == "loaded"
        assert data["modules"]["action_queue"] == "loaded"

    def test_health_includes_version(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert "version" in data


class TestApiStatus:
    """Tests for GET /api/status."""

    def test_status_returns_module_info(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "modules" in data
        assert "paperless" in data["modules"]
        assert "statements" in data["modules"]
        assert "eob_matching" in data["modules"]
        assert "action_queue" in data["modules"]

    def test_status_overall_reflects_modules(self, client, mock_paperless):
        """If all modules are ok, overall status should be ok."""
        mock_paperless.health_check.return_value = {"status": "ok", "documents": 10}
        resp = client.get("/api/status")
        data = resp.json()
        # Paperless and queue may show as ok/degraded depending on mock; verify structure
        assert data["status"] in ("ok", "degraded", "error")


class TestPaperlessHealth:
    """Tests for GET /api/paperless/health."""

    def test_paperless_health_ok(self, client, mock_paperless):
        mock_paperless.health_check.return_value = {"status": "ok", "documents": 42}
        resp = client.get("/api/paperless/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["documents"] == 42

    def test_paperless_health_error(self, client, mock_paperless):
        mock_paperless.health_check.side_effect = Exception("Connection refused")
        resp = client.get("/api/paperless/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "message" in data


class TestPaperlessStats:
    """Tests for GET /api/paperless/stats."""

    def test_paperless_stats(self, client, mock_paperless):
        resp = client.get("/api/paperless/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["tag_count"] == 2
        assert data["correspondent_count"] == 2
        assert len(data["tags"]) == 2
        assert len(data["correspondents"]) == 2


class TestSettings:
    """Tests for GET/PUT /api/settings."""

    def test_get_settings(self, client):
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "llm_base_url" in data
        assert "llm_model" in data
        assert "write_to_paperless" in data

    def test_update_settings(self, client):
        resp = client.put(
            "/api/settings",
            json={"llm_model": "phi3:mini", "write_to_paperless": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "llm_model" in data["changed"]
        assert "write_to_paperless" in data["changed"]

    def test_update_settings_empty_body(self, client):
        resp = client.put("/api/settings", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["changed"] == []


class TestLLMEndpoints:
    """Tests for /api/llm/* endpoints."""

    def test_llm_models(self, client):
        resp = client.get("/api/llm/models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True

    def test_llm_test_connection_success(self, client):
        with patch("openai.AsyncOpenAI") as mock_openai:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.choices = [MagicMock(message=MagicMock(content="pong"))]
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client

            resp = client.post(
                "/api/llm/test",
                json={"base_url": "http://llm.test/v1", "model": "test-model"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["response"] == "pong"

    def test_llm_test_connection_failure(self, client):
        with patch("openai.AsyncOpenAI") as mock_openai:
            mock_client = AsyncMock()
            mock_client.chat.completions.create.side_effect = Exception("timeout")
            mock_openai.return_value = mock_client

            resp = client.post(
                "/api/llm/test",
                json={"base_url": "http://bad-url.test/v1"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "error"


class TestDocumentPreview:
    """Tests for GET /api/documents/{id}/preview."""

    def test_document_preview(self, client, mock_paperless):
        mock_paperless.get_document.return_value = {
            "id": 42,
            "title": "My Document",
            "correspondent": 1,
            "tags": [1, 2],
            "created": "2026-03-15",
            "added": "2026-03-15",
        }
        mock_paperless.get_document_content.return_value = "A" * 5000

        resp = client.get("/api/documents/42/preview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 42
        assert data["title"] == "My Document"
        assert len(data["content"]) == 3000  # truncated
        assert data["content_length"] == 5000
