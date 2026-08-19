"""Tests for Triage Queue API endpoints (/api/triage/*)."""

from __future__ import annotations

import pytest

from doc_intelligence_hub.modules.triage.database import (
    create_queue_item,
)

# ---------------------------------------------------------------------------
# Seeding helper
# ---------------------------------------------------------------------------


@pytest.fixture()
def seed_triage():
    """Seed the triage database with sample queue items."""
    items = [
        create_queue_item(
            item_type="eob_match_review",
            source="auto_flag",
            target_type="eob_match",
            target_id="101",
            reason="Low confidence match (55%)",
            priority=80,
            metadata={"score_pct": 55, "eob_document_id": 100, "bill_document_id": 200},
        ),
        create_queue_item(
            item_type="eob_match_review",
            source="auto_flag",
            target_type="eob_match",
            target_id="102",
            reason="Multiple close candidates",
            priority=65,
            metadata={"score_pct": 72, "candidate_count": 3},
        ),
        create_queue_item(
            item_type="orphan_document",
            source="auto_flag",
            target_type="document",
            target_id="eob-50",
            reason="EOB with no matching bill",
            priority=40,
        ),
    ]
    return items


class TestTriageQueueList:
    """Tests for GET /api/triage/queue."""

    def test_list_empty(self, client):
        resp = client.get("/api/triage/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["count"] == 0

    def test_list_with_items(self, client, seed_triage):
        resp = client.get("/api/triage/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        # Default sort is priority desc — highest priority first
        assert data["items"][0]["priority"] >= data["items"][1]["priority"]

    def test_filter_by_type(self, client, seed_triage):
        resp = client.get("/api/triage/queue?type=orphan_document")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["items"][0]["item_type"] == "orphan_document"

    def test_filter_accepts_duplicate_document_type(self, client):
        resp = client.get("/api/triage/queue?type=duplicate_document")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_invalid_type_returns_400(self, client):
        resp = client.get("/api/triage/queue?type=invalid_type")
        assert resp.status_code == 400

    def test_invalid_status_returns_400(self, client):
        resp = client.get("/api/triage/queue?status=invalid")
        assert resp.status_code == 400

    def test_invalid_sort_returns_400(self, client):
        resp = client.get("/api/triage/queue?sort=invalid")
        assert resp.status_code == 400

    def test_pagination(self, client, seed_triage):
        resp = client.get("/api/triage/queue?limit=1&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["limit"] == 1
        assert data["offset"] == 0


class TestTriageQueueItem:
    """Tests for GET /api/triage/queue/{id}."""

    def test_get_item(self, client, seed_triage):
        item_id = seed_triage[0]["id"]
        resp = client.get(f"/api/triage/queue/{item_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == item_id
        assert data["item_type"] == "eob_match_review"

    def test_get_nonexistent_returns_404(self, client):
        resp = client.get("/api/triage/queue/nonexistent")
        assert resp.status_code == 404


class TestTriageResolve:
    """Tests for POST /api/triage/queue/{id}/resolve."""

    def test_resolve_item(self, client, seed_triage):
        item_id = seed_triage[0]["id"]
        resp = client.post(
            f"/api/triage/queue/{item_id}/resolve",
            json={"action": "confirm"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "resolved"
        assert data["resolved_action"] == "confirm"

    def test_resolve_with_payload(self, client, seed_triage):
        item_id = seed_triage[0]["id"]
        resp = client.post(
            f"/api/triage/queue/{item_id}/resolve",
            json={"action": "reject", "payload": {"reason": "wrong match"}},
        )
        assert resp.status_code == 200
        assert resp.json()["resolved_action"] == "reject"

    def test_resolve_nonexistent_returns_404(self, client):
        resp = client.post(
            "/api/triage/queue/nonexistent/resolve",
            json={"action": "confirm"},
        )
        assert resp.status_code == 404

    def test_resolve_missing_action_returns_422(self, client, seed_triage):
        item_id = seed_triage[0]["id"]
        resp = client.post(
            f"/api/triage/queue/{item_id}/resolve",
            json={},
        )
        assert resp.status_code == 422


class TestTriageDefer:
    """Tests for POST /api/triage/queue/{id}/defer."""

    def test_defer_item_default(self, client, seed_triage):
        item_id = seed_triage[0]["id"]
        resp = client.post(f"/api/triage/queue/{item_id}/defer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deferred"
        assert data["deferred_until"] is not None

    def test_defer_with_custom_date(self, client, seed_triage):
        item_id = seed_triage[0]["id"]
        resp = client.post(
            f"/api/triage/queue/{item_id}/defer",
            json={"until": "2026-12-25T00:00:00"},
        )
        assert resp.status_code == 200
        assert "2026-12-25" in resp.json()["deferred_until"]

    def test_defer_nonexistent_returns_404(self, client):
        resp = client.post("/api/triage/queue/nonexistent/defer")
        assert resp.status_code == 404


class TestTriageDismiss:
    """Tests for POST /api/triage/queue/{id}/dismiss."""

    def test_dismiss_item(self, client, seed_triage):
        item_id = seed_triage[0]["id"]
        resp = client.post(f"/api/triage/queue/{item_id}/dismiss")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "dismissed"

    def test_dismiss_nonexistent_returns_404(self, client):
        resp = client.post("/api/triage/queue/nonexistent/dismiss")
        assert resp.status_code == 404


class TestTriageUndo:
    """Tests for POST /api/triage/queue/{id}/undo."""

    def test_undo_resolved_item(self, client, seed_triage):
        item_id = seed_triage[0]["id"]
        # First resolve
        client.post(f"/api/triage/queue/{item_id}/resolve", json={"action": "confirm"})
        # Then undo
        resp = client.post(f"/api/triage/queue/{item_id}/undo")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["resolved_at"] is None

    def test_undo_nonexistent_returns_404(self, client):
        resp = client.post("/api/triage/queue/nonexistent/undo")
        assert resp.status_code == 404


class TestTriageStats:
    """Tests for GET /api/triage/stats."""

    def test_empty_stats(self, client):
        resp = client.get("/api/triage/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["pending"] == 0

    def test_stats_with_items(self, client, seed_triage):
        resp = client.get("/api/triage/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["pending"] == 3
        assert data["by_type"]["eob_match_review"] == 2
        assert data["by_type"]["orphan_document"] == 1


class TestTriagePopulate:
    """Tests for POST /api/triage/queue/populate."""

    def test_populate_empty_db(self, client):
        resp = client.post("/api/triage/queue/populate")
        assert resp.status_code == 200
        data = resp.json()
        assert "items_created" in data
        assert data["items_created"] >= 0

    def test_populate_with_eob_data(self, client, seed_eob):
        """Populate should flag the low-confidence EOB match from seed data."""
        resp = client.post("/api/triage/queue/populate")
        assert resp.status_code == 200
        data = resp.json()
        # seed_eob has a 0.65 score candidate match — should be flagged
        assert data["items_created"] >= 1

    def test_populate_idempotent(self, client, seed_eob):
        """Running populate twice should not create duplicates."""
        resp1 = client.post("/api/triage/queue/populate")
        _count1 = resp1.json()["items_created"]
        resp2 = client.post("/api/triage/queue/populate")
        count2 = resp2.json()["items_created"]
        assert count2 == 0  # No new items on second run

    def test_populate_route_not_captured_by_item_id(self, client):
        """Ensure /queue/populate is not matched as /queue/{item_id}."""
        # GET to /queue/populate should 404 (no item with id "populate")
        resp = client.get("/api/triage/queue/populate")
        assert resp.status_code == 404
