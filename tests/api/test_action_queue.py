"""Tests for action queue endpoints (/api/queue/*)."""

from __future__ import annotations


class TestQueueCheck:
    """Tests for GET /api/queue/check."""

    def test_check_returns_status(self, client, mock_paperless):
        from unittest.mock import AsyncMock, patch

        with patch(
            "doc_intelligence_hub.api.routers.action_queue.OllamaAnalyzer"
        ) as mock_analyzer_cls:
            mock_analyzer = mock_analyzer_cls.return_value
            mock_analyzer.health_check = AsyncMock(return_value=True)
            mock_analyzer.base_url = "http://llm.test"
            mock_analyzer.model = "test-model"

            resp = client.get("/api/queue/check")
            assert resp.status_code == 200
            data = resp.json()
            assert data["module"] == "action-queue"
            assert "paperless" in data


class TestQueueStatus:
    """Tests for GET /api/queue/status."""

    def test_status_idle(self, client):
        resp = client.get("/api/queue/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "database" in data
        assert "progress" in data

    def test_status_database_counts(self, client, seed_actions):
        resp = client.get("/api/queue/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["database"]["pending"] == 2
        assert data["database"]["completed"] == 1


class TestListActions:
    """Tests for GET /api/queue/actions."""

    def test_empty_actions(self, client):
        resp = client.get("/api/queue/actions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["actions"] == []
        assert data["total"] == 0

    def test_list_actions_with_data(self, client, seed_actions):
        resp = client.get("/api/queue/actions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["actions"]) == 3

    def test_list_actions_filter_by_status(self, client, seed_actions):
        resp = client.get("/api/queue/actions?status=pending")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        for action in data["actions"]:
            assert action["status"] == "pending"

    def test_list_actions_pagination(self, client, seed_actions):
        resp = client.get("/api/queue/actions?limit=1&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["actions"]) == 1
        assert data["total"] == 3

    def test_list_actions_structure(self, client, seed_actions):
        resp = client.get("/api/queue/actions?limit=1")
        data = resp.json()
        action = data["actions"][0]
        assert "id" in action
        assert "document_id" in action
        assert "document_title" in action
        assert "action_type" in action
        assert "title" in action
        assert "status" in action
        assert "preview_url" in action


class TestUpdateAction:
    """Tests for PATCH /api/queue/actions/{id}."""

    def test_complete_action(self, client, seed_actions):
        actions = client.get("/api/queue/actions?status=pending").json()
        action_id = actions["actions"][0]["id"]

        resp = client.patch(
            f"/api/queue/actions/{action_id}",
            json={"status": "completed", "dry_run": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["completed_at"] is not None

    def test_dismiss_action(self, client, seed_actions):
        actions = client.get("/api/queue/actions?status=pending").json()
        action_id = actions["actions"][0]["id"]

        resp = client.patch(
            f"/api/queue/actions/{action_id}",
            json={"status": "dismissed", "dry_run": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "dismissed"

    def test_reopen_action(self, client, seed_actions):
        actions = client.get("/api/queue/actions?status=completed").json()
        action_id = actions["actions"][0]["id"]

        resp = client.patch(
            f"/api/queue/actions/{action_id}",
            json={"status": "pending", "dry_run": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["completed_at"] is None

    def test_update_action_not_found(self, client):
        resp = client.patch(
            "/api/queue/actions/99999",
            json={"status": "completed", "dry_run": True},
        )
        assert resp.status_code == 404

    def test_update_action_invalid_status(self, client, seed_actions):
        actions = client.get("/api/queue/actions?status=pending").json()
        action_id = actions["actions"][0]["id"]

        resp = client.patch(
            f"/api/queue/actions/{action_id}",
            json={"status": "invalid_status", "dry_run": True},
        )
        assert resp.status_code == 422


class TestBulkAction:
    """Tests for POST /api/queue/actions/bulk."""

    def test_bulk_complete(self, client, seed_actions):
        actions = client.get("/api/queue/actions?status=pending").json()
        ids = [a["id"] for a in actions["actions"]]
        assert len(ids) == 2

        resp = client.post(
            "/api/queue/actions/bulk",
            json={"action": "complete", "action_ids": ids},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["affected"] == 2
        assert data["action"] == "complete"

        # Verify all are now completed
        check = client.get("/api/queue/actions?status=pending").json()
        assert check["total"] == 0

    def test_bulk_dismiss(self, client, seed_actions):
        actions = client.get("/api/queue/actions?status=pending").json()
        ids = [a["id"] for a in actions["actions"]]

        resp = client.post(
            "/api/queue/actions/bulk",
            json={"action": "dismiss", "action_ids": ids},
        )
        assert resp.status_code == 200
        assert resp.json()["affected"] == 2

    def test_bulk_reopen(self, client, seed_actions):
        actions = client.get("/api/queue/actions?status=completed").json()
        ids = [a["id"] for a in actions["actions"]]

        resp = client.post(
            "/api/queue/actions/bulk",
            json={"action": "reopen", "action_ids": ids},
        )
        assert resp.status_code == 200
        assert resp.json()["affected"] == 1

    def test_bulk_skips_already_in_target_status(self, client, seed_actions):
        """Items already in the target status should not be counted as affected."""
        actions = client.get("/api/queue/actions?status=completed").json()
        completed_id = actions["actions"][0]["id"]

        resp = client.post(
            "/api/queue/actions/bulk",
            json={"action": "complete", "action_ids": [completed_id]},
        )
        assert resp.status_code == 200
        assert resp.json()["affected"] == 0

    def test_bulk_invalid_action(self, client, seed_actions):
        resp = client.post(
            "/api/queue/actions/bulk",
            json={"action": "invalid", "action_ids": [1]},
        )
        assert resp.status_code == 422

    def test_bulk_empty_ids(self, client):
        resp = client.post(
            "/api/queue/actions/bulk",
            json={"action": "complete", "action_ids": []},
        )
        assert resp.status_code == 422

    def test_bulk_nonexistent_ids(self, client):
        resp = client.post(
            "/api/queue/actions/bulk",
            json={"action": "complete", "action_ids": [99999]},
        )
        assert resp.status_code == 404


class TestBackfill:
    """Tests for POST /api/queue/actions/backfill."""

    def test_backfill_dry_run(self, client, seed_actions):
        """Dry run returns list of unsynced actions without modifying Paperless."""
        resp = client.post(
            "/api/queue/actions/backfill",
            json={"dry_run": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        assert "would_sync" in data
        # All seed actions have last_synced_status=None, so all should be candidates
        assert data["would_sync"] == 3
        assert len(data["actions"]) == 3

    def test_backfill_dry_run_with_status_filter(self, client, seed_actions):
        """Status filter limits candidates to only that status."""
        resp = client.post(
            "/api/queue/actions/backfill",
            json={"dry_run": True, "status_filter": "pending"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["would_sync"] == 2
        for action in data["actions"]:
            assert action["status"] == "pending"

    def test_backfill_dry_run_with_limit(self, client, seed_actions):
        """Limit caps the number of candidates."""
        resp = client.post(
            "/api/queue/actions/backfill",
            json={"dry_run": True, "limit": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["would_sync"] == 1

    def test_backfill_live_run(self, client, seed_actions):
        """Live backfill writes to Paperless and updates last_synced_status."""
        from unittest.mock import AsyncMock, patch

        mock_enricher = AsyncMock()
        mock_enricher.ensure_custom_fields_exist = AsyncMock()
        mock_enricher.enrich_document = AsyncMock()
        mock_enricher.sync_status = AsyncMock()

        with patch(
            "doc_intelligence_hub.modules.action_queue.enricher.PaperlessEnricher",
            return_value=mock_enricher,
        ):
            resp = client.post(
                "/api/queue/actions/backfill",
                json={"dry_run": False},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["dry_run"] is False
            assert data["synced"] == 3
            assert data["failed"] == 0

        # Verify that running dry_run again shows 0 candidates (all now synced)
        resp2 = client.post(
            "/api/queue/actions/backfill",
            json={"dry_run": True},
        )
        assert resp2.json()["would_sync"] == 0

    def test_backfill_rejects_when_writes_disabled(self, client, seed_actions):
        """Should return 400 when write_to_paperless is disabled and not dry_run."""
        client.app.state.hub_settings.write_to_paperless = False
        try:
            resp = client.post(
                "/api/queue/actions/backfill",
                json={"dry_run": False},
            )
            assert resp.status_code == 400
            assert "write_to_paperless" in resp.json()["error"]["message"]
        finally:
            client.app.state.hub_settings.write_to_paperless = True
