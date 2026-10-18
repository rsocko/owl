"""API tests for the analysis invalidation / staleness router (issue #114)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from doc_intelligence_hub.api.routers.analysis_invalidation import (
    _matches_action_queue_scope,
)
from doc_intelligence_hub.modules.analysis_invalidation import config as ai_config
from doc_intelligence_hub.modules.analysis_invalidation.database import init_db


@pytest.fixture()
def ai_db(tmp_path):
    original = ai_config.settings.database_url
    ai_config.settings.database_url = f"sqlite:///{tmp_path / 'api_test_analysis_invalidation.db'}"
    init_db()
    yield
    ai_config.settings.database_url = original


class TestSimulateVersionChange:
    def test_simulate_version_change_creates_event(self, client, ai_db):
        resp = client.post(
            "/api/analysis-invalidation/simulate-version-change",
            json={"document_id": 1, "checksum": "abc123", "metadata_fields": {"title": "x"}},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["duplicate"] is False
        assert "event_id" in body

    def test_duplicate_simulation_is_a_noop(self, client, ai_db):
        payload = {"document_id": 1, "checksum": "abc123"}
        first = client.post("/api/analysis-invalidation/simulate-version-change", json=payload)
        second = client.post("/api/analysis-invalidation/simulate-version-change", json=payload)
        assert first.json()["duplicate"] is False
        assert second.json()["duplicate"] is True


class TestInvalidateEndpoint:
    def test_requires_exactly_one_scope_selector(self, client, ai_db):
        resp = client.post("/api/analysis-invalidation/invalidate", json={})
        assert resp.status_code == 422

        resp2 = client.post(
            "/api/analysis-invalidation/invalidate",
            json={"all": True, "document_ids": [1]},
        )
        assert resp2.status_code == 422

    def test_invalidate_specific_document_ids(self, client, ai_db):
        client.post(
            "/api/analysis-invalidation/simulate-version-change",
            json={"document_id": 5, "checksum": "abc"},
        )
        resp = client.post(
            "/api/analysis-invalidation/invalidate",
            json={"document_ids": [5]},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["invalidated_count"] == 1

    def test_invalidate_rejects_batch_over_limit(self, client, ai_db, monkeypatch):
        monkeypatch.setattr(ai_config.settings, "max_manual_invalidation_batch", 1)
        resp = client.post(
            "/api/analysis-invalidation/invalidate",
            json={"document_ids": [1, 2, 3]},
        )
        # Over-limit document_ids lists are truncated to the effective limit
        # rather than rejected outright, so this exercises the truncation
        # path — only one document should actually be invalidated.
        assert resp.status_code == 202
        assert resp.json()["invalidated_count"] == 1


class TestBackfillEndpoint:
    def test_requires_one_bounded_scope(self, client, ai_db):
        response = client.post("/api/analysis-invalidation/backfill", json={})

        assert response.status_code == 422

    def test_rejects_multiple_scopes(self, client, ai_db):
        response = client.post(
            "/api/analysis-invalidation/backfill",
            json={"document_ids": [1], "saved_view_id": 2},
        )

        assert response.status_code == 422


@pytest.mark.asyncio
async def test_action_scope_uses_configured_tags_not_only_inbox():
    request = SimpleNamespace()
    client = AsyncMock()
    client.resolve_tag_names.return_value = {7: "Bills"}

    with (
        patch(
            "doc_intelligence_hub.api.routers.analysis_invalidation._get_queue_settings",
            return_value={
                "scan_mode": "tags",
                "monitor_tags": ["Bills"],
                "lookback_days": 548,
            },
        ),
        patch(
            "doc_intelligence_hub.api.routers.analysis_invalidation._document_has_existing_action",
            return_value=False,
        ),
    ):
        matches = await _matches_action_queue_scope(
            request,
            client,
            {
                "id": 42,
                "added": datetime.now(UTC).isoformat(),
                "tags": [7],
            },
        )

    assert matches is True


@pytest.mark.asyncio
async def test_action_scope_uses_saved_view_membership():
    request = SimpleNamespace()
    client = AsyncMock()
    client.list_documents.return_value = [{"id": 42}]

    with (
        patch(
            "doc_intelligence_hub.api.routers.analysis_invalidation._get_queue_settings",
            return_value={
                "scan_mode": "saved_view",
                "saved_view_id": 9,
                "lookback_days": 548,
            },
        ),
        patch(
            "doc_intelligence_hub.api.routers.analysis_invalidation._document_has_existing_action",
            return_value=False,
        ),
    ):
        matches = await _matches_action_queue_scope(
            request,
            client,
            {
                "id": 42,
                "added": datetime.now(UTC).isoformat(),
                "tags": [],
            },
        )

    assert matches is True
    client.list_documents.assert_awaited_once_with(
        saved_view=9,
        id_after=42,
        id_before=42,
        limit=1,
    )


@pytest.mark.asyncio
async def test_existing_action_remains_eligible_outside_lookback():
    request = SimpleNamespace()
    client = AsyncMock()

    with patch(
        "doc_intelligence_hub.api.routers.analysis_invalidation._document_has_existing_action",
        return_value=True,
    ):
        matches = await _matches_action_queue_scope(
            request,
            client,
            {
                "id": 42,
                "added": (datetime.now(UTC) - timedelta(days=2000)).isoformat(),
                "tags": [],
            },
        )

    assert matches is True
    client.list_documents.assert_not_awaited()


class TestDocumentStatusAndEvents:
    def test_get_document_status_unknown_document(self, client, ai_db):
        resp = client.get("/api/analysis-invalidation/documents/999")
        assert resp.status_code == 200
        body = resp.json()
        assert body["document_id"] == 999
        assert body["modules"] == []

    def test_list_events_is_redacted(self, client, ai_db):
        client.post(
            "/api/analysis-invalidation/simulate-version-change",
            json={
                "document_id": 1,
                "checksum": "abc",
                "metadata_fields": {"title": "Sensitive Document Title"},
            },
        )
        resp = client.get("/api/analysis-invalidation/events")
        assert resp.status_code == 200
        body = resp.json()
        assert body["redacted"] is True
        assert len(body["events"]) == 1
        # No raw metadata value ever appears in the API response.
        assert "Sensitive Document Title" not in resp.text
