"""Tests for the action queue API router — preview_url and serialization."""

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from doc_intelligence_hub.api.app import HubSettings, create_app
from doc_intelligence_hub.modules.action_queue.config import settings as aq_settings
from doc_intelligence_hub.modules.action_queue.database import Action, get_session, init_db


@pytest.fixture()
def client(tmp_path):
    """Create a test client with temp-file database and patched settings."""
    db_path = tmp_path / "test_actions.db"
    hub_settings = HubSettings(
        paperless_url="http://paperless.test",
        paperless_token="test-token",
    )
    app = create_app(hub_settings)

    original_db_url = aq_settings.database_url
    original_paperless_url = aq_settings.paperless_url
    aq_settings.database_url = f"sqlite:///{db_path}"
    aq_settings.paperless_url = "http://paperless.test"

    init_db()

    yield TestClient(app)

    aq_settings.database_url = original_db_url
    aq_settings.paperless_url = original_paperless_url


@pytest.fixture()
def seeded_client(client):
    """Client with pre-seeded action rows."""
    db = get_session()
    try:
        db.add(
            Action(
                id=1,
                document_id=42,
                document_title="Electric Bill Jan 2026",
                action_type="PAY",
                title="Pay electric bill",
                summary="Monthly electric bill due",
                due_date=date(2026, 2, 15),
                amount=125.50,
                urgency="HIGH",
                confidence=85,
                status="pending",
                correspondent="Power Co",
            )
        )
        db.add(
            Action(
                id=2,
                document_id=99,
                document_title="Insurance EOB",
                action_type="FILE",
                title="File insurance EOB",
                summary="EOB for dental visit",
                urgency="LOW",
                confidence=70,
                status="completed",
                correspondent="Blue Shield",
                completed_at=datetime(2026, 1, 20, 10, 0, 0),
            )
        )
        db.commit()
    finally:
        db.close()

    return client


class TestListActions:
    def test_list_actions_returns_preview_url(self, seeded_client):
        resp = seeded_client.get("/api/queue/actions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

        action = data["actions"][0]
        assert "preview_url" in action
        doc_id = action["document_id"]
        assert action["preview_url"] == f"http://paperless.test/documents/{doc_id}/details"

    def test_all_actions_have_preview_url(self, seeded_client):
        resp = seeded_client.get("/api/queue/actions")
        for action in resp.json()["actions"]:
            assert action["preview_url"] is not None
            assert "/documents/" in action["preview_url"]
            assert action["preview_url"].endswith("/details")

    def test_list_actions_with_status_filter(self, seeded_client):
        resp = seeded_client.get("/api/queue/actions?status=pending")
        data = resp.json()
        assert data["total"] == 1
        assert data["actions"][0]["status"] == "pending"
        assert data["actions"][0]["preview_url"] is not None

    def test_list_actions_empty(self, client):
        resp = client.get("/api/queue/actions")
        data = resp.json()
        assert data["total"] == 0
        assert data["actions"] == []

    def test_action_serialization_fields(self, seeded_client):
        resp = seeded_client.get("/api/queue/actions?status=pending")
        action = resp.json()["actions"][0]
        expected_fields = {
            "id",
            "document_id",
            "document_title",
            "action_type",
            "title",
            "summary",
            "due_date",
            "amount",
            "urgency",
            "severity",
            "confidence",
            "risk_score",
            "status",
            "recommended_cta",
            "correspondent",
            "ai_reasoning",
            "version",
            "preview_url",
            "created_at",
            "completed_at",
            "acknowledged_at",
            "snoozed_until",
        }
        assert set(action.keys()) == expected_fields


class TestUpdateAction:
    def test_update_action_returns_preview_url(self, seeded_client):
        resp = seeded_client.patch(
            "/api/queue/actions/1",
            json={"status": "completed", "dry_run": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["preview_url"] == "http://paperless.test/documents/42/details"
        assert data["status"] == "completed"
        assert data["completed_at"] is not None

    def test_update_action_dismiss(self, seeded_client):
        resp = seeded_client.patch(
            "/api/queue/actions/1",
            json={"status": "dismissed", "dry_run": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "dismissed"
        assert data["preview_url"] is not None

    def test_update_action_reopen(self, seeded_client):
        resp = seeded_client.patch(
            "/api/queue/actions/2",
            json={"status": "pending", "dry_run": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["completed_at"] is None
        assert data["preview_url"] is not None

    def test_update_nonexistent_action(self, seeded_client):
        resp = seeded_client.patch(
            "/api/queue/actions/999",
            json={"status": "completed", "dry_run": False},
        )
        assert resp.status_code == 404

    def test_optimistic_locking_conflict(self, seeded_client):
        """Returns 409 when version doesn't match (concurrent modification)."""
        resp = seeded_client.patch(
            "/api/queue/actions/1",
            json={"status": "completed", "dry_run": False, "version": 999},
        )
        assert resp.status_code == 409
        data = resp.json()
        detail = data["error"]
        assert detail["error"] == "version_conflict"
        assert detail["current_version"] == 1
        assert detail["expected_version"] == 999

    def test_optimistic_locking_success(self, seeded_client):
        """Succeeds when version matches and bumps the version."""
        # First get the current version
        list_resp = seeded_client.get("/api/queue/actions?status=pending")
        action = list_resp.json()["actions"][0]
        current_version = action["version"]

        # Update with correct version
        resp = seeded_client.patch(
            f"/api/queue/actions/{action['id']}",
            json={"status": "completed", "dry_run": False, "version": current_version},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == current_version + 1


class TestPreviewUrlEdgeCases:
    def test_preview_url_with_empty_paperless_url(self, tmp_path):
        """When both hub and action queue settings have empty paperless_url, preview_url is None."""
        db_path = tmp_path / "test_actions_edge.db"
        hub_settings = HubSettings(
            paperless_url="",
            paperless_token="test-token",
            statement_tracker_config=str(tmp_path / "nonexistent.yaml"),
        )
        app = create_app(hub_settings)

        original_db_url = aq_settings.database_url
        original_paperless_url = aq_settings.paperless_url
        aq_settings.database_url = f"sqlite:///{db_path}"
        aq_settings.paperless_url = ""

        init_db()
        db = get_session()
        try:
            db.add(
                Action(
                    document_id=42,
                    document_title="Test Doc",
                    action_type="PAY",
                    title="Test",
                    urgency="LOW",
                    status="pending",
                )
            )
            db.commit()
        finally:
            db.close()

        client = TestClient(app)
        resp = client.get("/api/queue/actions")
        for action in resp.json()["actions"]:
            assert action["preview_url"] is None

        aq_settings.database_url = original_db_url
        aq_settings.paperless_url = original_paperless_url

    def test_preview_url_strips_trailing_slash(self, seeded_client):
        original = aq_settings.paperless_url
        aq_settings.paperless_url = "http://paperless.test/"
        try:
            resp = seeded_client.get("/api/queue/actions?status=pending")
            action = resp.json()["actions"][0]
            assert action["preview_url"] == "http://paperless.test/documents/42/details"
        finally:
            aq_settings.paperless_url = original


class TestAcknowledgeAction:
    def test_acknowledge_sets_status_and_timestamp(self, seeded_client):
        resp = seeded_client.post("/api/queue/actions/1/acknowledge")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "acknowledged"
        assert data["acknowledged_at"] is not None

    def test_acknowledge_nonexistent_returns_404(self, seeded_client):
        resp = seeded_client.post("/api/queue/actions/999/acknowledge")
        assert resp.status_code == 404


class TestSnoozeAction:
    def test_snooze_sets_status_and_until(self, seeded_client):
        resp = seeded_client.post(
            "/api/queue/actions/1/snooze",
            json={"until": "2026-08-01T09:00:00"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "snoozed"
        assert "2026-08-01" in data["snoozed_until"]

    def test_snooze_invalid_timestamp_returns_422(self, seeded_client):
        resp = seeded_client.post(
            "/api/queue/actions/1/snooze",
            json={"until": "not-a-date"},
        )
        assert resp.status_code == 422

    def test_snooze_nonexistent_returns_404(self, seeded_client):
        resp = seeded_client.post(
            "/api/queue/actions/999/snooze",
            json={"until": "2026-08-01T09:00:00"},
        )
        assert resp.status_code == 404


class TestExpiredSnoozes:
    def test_expired_snoozes_returns_past_due(self, seeded_client):
        # First snooze to a past date
        seeded_client.post(
            "/api/queue/actions/1/snooze",
            json={"until": "2020-01-01T00:00:00"},
        )
        resp = seeded_client.get("/api/queue/actions/expired-snoozes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["actions"][0]["id"] == 1

    def test_expired_snoozes_excludes_future(self, seeded_client):
        # Snooze to a future date
        seeded_client.post(
            "/api/queue/actions/1/snooze",
            json={"until": "2099-12-31T23:59:59"},
        )
        resp = seeded_client.get("/api/queue/actions/expired-snoozes")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


class TestFeedback:
    def test_submit_not_an_action_feedback(self, seeded_client):
        resp = seeded_client.post(
            "/api/queue/actions/1/feedback",
            json={"feedback_type": "not_an_action", "reason": "Just an ad"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["feedback_type"] == "not_an_action"
        assert data["action_status"] == "not_an_action"

    def test_submit_misclassified_feedback_corrects_type(self, seeded_client):
        resp = seeded_client.post(
            "/api/queue/actions/1/feedback",
            json={"feedback_type": "misclassified", "corrected_action_type": "FILE"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action_type"] == "FILE"

    def test_get_feedback_history(self, seeded_client):
        # Submit feedback
        seeded_client.post(
            "/api/queue/actions/1/feedback",
            json={"feedback_type": "wrong_urgency", "reason": "Not that urgent"},
        )
        # Retrieve feedback
        resp = seeded_client.get("/api/queue/actions/1/feedback")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["feedback"]) == 1
        assert data["feedback"][0]["feedback_type"] == "wrong_urgency"

    def test_feedback_nonexistent_action_returns_404(self, seeded_client):
        resp = seeded_client.post(
            "/api/queue/actions/999/feedback",
            json={"feedback_type": "not_an_action"},
        )
        assert resp.status_code == 404


class TestNewStatusesViaUpdate:
    def test_update_to_acknowledged(self, seeded_client):
        resp = seeded_client.patch(
            "/api/queue/actions/1",
            json={"status": "acknowledged", "dry_run": False},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "acknowledged"

    def test_update_to_snoozed_requires_until(self, seeded_client):
        resp = seeded_client.patch(
            "/api/queue/actions/1",
            json={"status": "snoozed", "dry_run": False},
        )
        assert resp.status_code == 422

    def test_update_to_snoozed_with_until(self, seeded_client):
        resp = seeded_client.patch(
            "/api/queue/actions/1",
            json={"status": "snoozed", "dry_run": False, "snoozed_until": "2026-09-01T00:00:00"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "snoozed"

    def test_reopen_clears_timestamps(self, seeded_client):
        # Acknowledge first
        seeded_client.post("/api/queue/actions/1/acknowledge")
        # Reopen
        resp = seeded_client.patch(
            "/api/queue/actions/1",
            json={"status": "pending", "dry_run": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["acknowledged_at"] is None
        assert data["snoozed_until"] is None


class TestSeverityField:
    def test_severity_derived_from_urgency(self, seeded_client):
        resp = seeded_client.get("/api/queue/actions?status=pending")
        action = resp.json()["actions"][0]
        # Action 1 has urgency=HIGH → severity should be "focus"
        assert action["severity"] == "focus"

    def test_severity_low_urgency(self, seeded_client):
        resp = seeded_client.get("/api/queue/actions?status=completed")
        action = resp.json()["actions"][0]
        # Action 2 has urgency=LOW → severity should be "safe"
        assert action["severity"] == "safe"
