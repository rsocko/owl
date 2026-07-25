"""Tests for Mission Control connector endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from doc_intelligence_hub.modules.action_queue.database import (
    Action,
    get_session as get_aq_session,
)
from doc_intelligence_hub.modules.eob_matching.database import (
    EOBRecord,
    MatchRecord,
    get_session as get_eob_session,
)


class TestMCListActions:
    """Tests for GET /api/action-queue/actions."""

    def test_empty_actions(self, client):
        resp = client.get("/api/action-queue/actions")
        assert resp.status_code == 200
        data = resp.json()
        assert data == []

    def test_actions_flat_array(self, client, seed_actions):
        resp = client.get("/api/action-queue/actions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 3

    def test_actions_filter_by_status(self, client, seed_actions):
        resp = client.get("/api/action-queue/actions?status=pending")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_action_structure(self, client, seed_actions):
        resp = client.get("/api/action-queue/actions?limit=1")
        data = resp.json()
        action = data[0]
        # MC connector contract: lowercase enum values
        assert action["action_type"] == action["action_type"].lower()
        assert action["urgency"] == action["urgency"].lower()
        assert "id" in action
        assert "document_title" in action
        assert "category" in action
        assert "summary" in action


class TestMCUpdateAction:
    """Tests for PATCH /api/action-queue/actions/{id}."""

    def test_mark_done(self, client, seed_actions):
        actions = client.get("/api/action-queue/actions?status=pending").json()
        action_id = actions[0]["id"]

        resp = client.patch(
            f"/api/action-queue/actions/{action_id}",
            json={"status": "done"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_status"] == "completed"

    def test_dismiss(self, client, seed_actions):
        actions = client.get("/api/action-queue/actions?status=pending").json()
        action_id = actions[0]["id"]

        resp = client.patch(
            f"/api/action-queue/actions/{action_id}",
            json={"status": "dismissed"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_status"] == "dismissed"

    def test_not_found(self, client):
        resp = client.patch(
            "/api/action-queue/actions/99999",
            json={"status": "done"},
        )
        assert resp.status_code == 404


class TestMCMissingStatements:
    """Tests for GET /api/statements/missing."""

    def test_missing_empty(self, client):
        """Returns empty list when no statement config or data."""
        resp = client.get("/api/statements/missing")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


class TestMCUnmatchedEobs:
    """Tests for GET /api/eob/unmatched."""

    def test_unmatched_empty(self, client):
        resp = client.get("/api/eob/unmatched")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_unmatched_with_data(self, client, seed_eob):
        resp = client.get("/api/eob/unmatched")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # EOB 101 is unmatched (only EOB 100 has a confirmed match)
        assert len(data) == 1
        assert data[0]["provider"] == "Aetna"
        assert data[0]["patient_responsibility"] == 75.50
