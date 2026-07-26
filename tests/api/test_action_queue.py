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
