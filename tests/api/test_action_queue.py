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


class TestQueueRunSettings:
    def test_run_uses_persisted_document_limit(self, client, mock_paperless):
        from unittest.mock import AsyncMock, patch

        settings_resp = client.put(
            "/api/queue/settings",
            json={"document_limit": 12},
        )
        assert settings_resp.status_code == 200

        mock_run = AsyncMock(return_value={"processed": 0})
        with patch(
            "doc_intelligence_hub.api.routers.action_queue.run_pipeline",
            mock_run,
        ):
            resp = client.post("/api/queue/run", json={"dry_run": True})

        assert resp.status_code == 200
        assert resp.json()["limit"] == 12
        assert mock_run.await_args.kwargs["limit"] == 12


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

    def test_update_action_details(self, client, seed_actions):
        actions = client.get("/api/queue/actions?status=pending").json()
        action = actions["actions"][0]
        action_id = action["id"]

        resp = client.patch(
            f"/api/queue/actions/{action_id}",
            json={
                "version": action["version"],
                "action_type": "task",
                "title": "Call utility company",
                "summary": "Confirm the latest balance",
                "due_date": "2026-08-15",
                "amount": 123.45,
                "urgency": "HIGH",
                "correspondent": "Utility Co",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["action_type"] == "TASK"
        assert data["title"] == "Call utility company"
        assert data["summary"] == "Confirm the latest balance"
        assert data["due_date"] == "2026-08-15"
        assert data["amount"] == 123.45
        assert data["urgency"] == "HIGH"
        assert data["correspondent"] == "Utility Co"
        assert data["version"] == action["version"] + 1

    def test_update_action_allows_clearing_optional_fields(self, client, seed_actions):
        actions = client.get("/api/queue/actions?status=pending").json()
        action = actions["actions"][0]
        action_id = action["id"]

        resp = client.patch(
            f"/api/queue/actions/{action_id}",
            json={
                "version": action["version"],
                "summary": None,
                "due_date": None,
                "amount": None,
                "correspondent": None,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] is None
        assert data["due_date"] is None
        assert data["amount"] is None
        assert data["correspondent"] is None

    def test_update_action_invalid_action_type(self, client, seed_actions):
        actions = client.get("/api/queue/actions?status=pending").json()
        action_id = actions["actions"][0]["id"]

        resp = client.patch(
            f"/api/queue/actions/{action_id}",
            json={"action_type": "invalid"},
        )
        assert resp.status_code == 422


class TestActionFeedbackSync:
    def test_not_an_action_feedback_updates_paperless(self, client, seed_actions):
        from unittest.mock import AsyncMock, patch

        action = client.get("/api/queue/actions?status=pending").json()["actions"][0]
        action_id = action["id"]
        mock_enricher = AsyncMock()
        mock_enricher.sync_status = AsyncMock()

        with patch(
            "doc_intelligence_hub.modules.action_queue.enricher.PaperlessEnricher",
            return_value=mock_enricher,
        ):
            resp = client.post(
                f"/api/queue/actions/{action_id}/feedback",
                json={"feedback_type": "not_an_action", "reason": "Informational only"},
            )

        assert resp.status_code == 200
        assert resp.json()["action_status"] == "not_an_action"
        mock_enricher.sync_status.assert_awaited_once_with(
            action["document_id"], "not_an_action"
        )


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

    def test_backfill_includes_actions_with_unsynced_status(self, client, seed_actions):
        from doc_intelligence_hub.modules.action_queue.database import Action, get_session

        db = get_session()
        try:
            actions = db.query(Action).all()
            for action in actions:
                action.last_synced_status = action.status
            target = actions[0]
            target.status = "not_an_action"
            db.commit()
        finally:
            db.close()

        resp = client.post(
            "/api/queue/actions/backfill",
            json={"dry_run": True},
        )

        assert resp.status_code == 200
        assert resp.json()["would_sync"] == 1


class TestQueueSettings:
    """Tests for GET/PUT /api/queue/settings."""

    def test_get_default_settings(self, client, mock_paperless):
        resp = client.get("/api/queue/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["scan_mode"] == "tags"
        assert "monitor_tags" in data
        assert isinstance(data["monitor_tags"], list)
        assert data["confidence_threshold"] >= 1
        assert data["remove_source_tag_on_resolve"] is True

    def test_update_settings_tags(self, client, mock_paperless):
        resp = client.put(
            "/api/queue/settings",
            json={"monitor_tags": ["Bills", "Medical"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "monitor_tags" in data["changed"]
        assert data["settings"]["monitor_tags"] == ["Bills", "Medical"]

    def test_update_settings_scan_mode(self, client, mock_paperless):
        resp = client.put(
            "/api/queue/settings",
            json={"scan_mode": "saved_view", "saved_view_id": 5},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["settings"]["scan_mode"] == "saved_view"
        assert data["settings"]["saved_view_id"] == 5

    def test_update_settings_clear_saved_view(self, client, mock_paperless):
        # Set a saved view first
        client.put("/api/queue/settings", json={"saved_view_id": 5})
        # Clear it by sending 0
        resp = client.put("/api/queue/settings", json={"saved_view_id": 0})
        assert resp.status_code == 200
        assert resp.json()["settings"]["saved_view_id"] is None

    def test_update_settings_clear_document_limit_with_null(self, client, mock_paperless):
        client.put("/api/queue/settings", json={"document_limit": 25})

        resp = client.put("/api/queue/settings", json={"document_limit": None})

        assert resp.status_code == 200
        assert resp.json()["settings"]["document_limit"] is None

    def test_update_settings_invalid_scan_mode(self, client, mock_paperless):
        resp = client.put(
            "/api/queue/settings",
            json={"scan_mode": "invalid"},
        )
        assert resp.status_code == 422

    def test_tag_scan_mode_requires_at_least_one_tag(self, client, mock_paperless):
        resp = client.put(
            "/api/queue/settings",
            json={"scan_mode": "tags", "monitor_tags": []},
        )
        assert resp.status_code == 422

    def test_settings_are_reloaded_from_database(self, client, mock_paperless):
        from doc_intelligence_hub.modules.action_queue.config import settings

        resp = client.put(
            "/api/queue/settings",
            json={
                "monitor_tags": ["Bills"],
                "remove_source_tag_on_resolve": False,
            },
        )
        assert resp.status_code == 200

        settings.tags_to_monitor = "Temporary"
        settings.remove_source_tag_on_resolve = True

        persisted = client.get("/api/queue/settings").json()
        assert persisted["monitor_tags"] == ["Bills"]
        assert persisted["remove_source_tag_on_resolve"] is False
        assert settings.monitor_tags == ["Bills"]
        assert settings.remove_source_tag_on_resolve is False

    def test_update_remove_source_tag_setting(self, client, mock_paperless):
        resp = client.put(
            "/api/queue/settings",
            json={"remove_source_tag_on_resolve": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "remove_source_tag_on_resolve" in data["changed"]
        assert data["settings"]["remove_source_tag_on_resolve"] is False


class TestQueueMetadata:
    """Tests for GET /api/queue/metadata/* endpoints."""

    def test_list_tags(self, client, mock_paperless):
        resp = client.get("/api/queue/metadata/tags")
        assert resp.status_code == 200
        data = resp.json()
        assert "tags" in data
        assert len(data["tags"]) == 2
        assert data["tags"][0]["name"] == "Inbox"

    def test_list_saved_views(self, client, mock_paperless):
        resp = client.get("/api/queue/metadata/saved-views")
        assert resp.status_code == 200
        data = resp.json()
        assert "saved_views" in data
        assert len(data["saved_views"]) == 2

    def test_list_correspondents(self, client, mock_paperless):
        resp = client.get("/api/queue/metadata/correspondents")
        assert resp.status_code == 200
        data = resp.json()
        assert "correspondents" in data
        assert len(data["correspondents"]) == 2

    def test_list_document_types(self, client, mock_paperless):
        resp = client.get("/api/queue/metadata/document-types")
        assert resp.status_code == 200
        data = resp.json()
        assert "document_types" in data
        assert len(data["document_types"]) == 3
