"""Tests for statement tracker endpoints (/api/statements/*)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


class TestStatementHealth:
    """Tests for GET /api/statements/health."""

    def test_health_returns_ok(self, client):
        mock_config = MagicMock()
        mock_config.source.mode = "paperless"
        mock_config.source.paperless_url = "http://paperless.test"

        with patch(
            "doc_intelligence_hub.api.routers.statements.load_statement_config_from_request",
            return_value=mock_config,
        ):
            resp = client.get("/api/statements/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["module"] == "statement-tracker"


class TestDiscoveryRun:
    """Tests for POST /api/statements/discovery/run."""

    def test_discovery_run_success(self, client):
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "providers": [{"name": "Electric Co", "key": "electric-co"}],
            "total_documents": 10,
        }

        with patch(
            "doc_intelligence_hub.api.routers.statements.run_discovery",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            resp = client.post("/api/statements/discovery/run")
            assert resp.status_code == 200
            data = resp.json()
            assert "providers" in data


class TestRecommendationsRun:
    """Tests for POST /api/statements/recommendations/run."""

    def test_recommendations_run_success(self, client):
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "recommendations": [
                {"provider_name": "Electric Co", "status": "missing", "days_late": 5}
            ],
        }

        with patch(
            "doc_intelligence_hub.api.routers.statements.run_recommendations",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            resp = client.post("/api/statements/recommendations/run?as_of=2026-07-01")
            assert resp.status_code == 200
            data = resp.json()
            assert "recommendations" in data

    def test_recommendations_requires_as_of(self, client):
        resp = client.post("/api/statements/recommendations/run")
        assert resp.status_code == 422


class TestProviderOverrides:
    """Tests for /api/statements/providers/overrides endpoints."""

    def test_get_overrides(self, client):
        mock_config = MagicMock()
        mock_config.runtime.database_path = ":memory:"
        mock_db = MagicMock()
        mock_db.get_provider_overrides.return_value = {"electric-co": {"status": "confirmed"}}

        with (
            patch(
                "doc_intelligence_hub.api.routers.statements.load_statement_config_from_request",
                return_value=mock_config,
            ),
            patch(
                "doc_intelligence_hub.api.routers.statements.Database",
                return_value=mock_db,
            ),
        ):
            resp = client.get("/api/statements/providers/overrides")
            assert resp.status_code == 200
            data = resp.json()
            assert "electric-co" in data

    def test_set_override(self, client):
        mock_config = MagicMock()
        mock_config.runtime.database_path = ":memory:"
        mock_db = MagicMock()

        with (
            patch(
                "doc_intelligence_hub.api.routers.statements.load_statement_config_from_request",
                return_value=mock_config,
            ),
            patch(
                "doc_intelligence_hub.api.routers.statements.Database",
                return_value=mock_db,
            ),
        ):
            resp = client.post(
                "/api/statements/providers/electric-co/override",
                json={"status": "confirmed", "notes": "Verified manually"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["provider_key"] == "electric-co"
            mock_db.set_provider_override.assert_called_once()

    def test_delete_override(self, client):
        mock_config = MagicMock()
        mock_config.runtime.database_path = ":memory:"
        mock_db = MagicMock()

        with (
            patch(
                "doc_intelligence_hub.api.routers.statements.load_statement_config_from_request",
                return_value=mock_config,
            ),
            patch(
                "doc_intelligence_hub.api.routers.statements.Database",
                return_value=mock_db,
            ),
        ):
            resp = client.delete("/api/statements/providers/electric-co/override")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            mock_db.delete_provider_override.assert_called_once_with("electric-co")


class TestPaperlessUrl:
    """Tests for GET /api/statements/config/paperless-url."""

    def test_get_paperless_url(self, client):
        mock_config = MagicMock()
        mock_config.source.paperless_url = "http://paperless.test:8000"

        with patch(
            "doc_intelligence_hub.api.routers.statements.load_statement_config_from_request",
            return_value=mock_config,
        ):
            resp = client.get("/api/statements/config/paperless-url")
            assert resp.status_code == 200
            data = resp.json()
            assert data["paperless_url"] == "http://paperless.test:8000"


class TestDocumentEndpoints:
    """Tests for /api/statements/documents/* endpoints."""

    def test_document_thumbnail(self, client, mock_paperless):
        resp = client.get("/api/statements/documents/1/thumb")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    def test_document_preview(self, client, mock_paperless):
        resp = client.get("/api/statements/documents/1/preview")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
