"""Tests for Mission Control connector endpoints."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

from doc_intelligence_hub.modules.action_queue.database import (
    Action,
    ActionFeedback,
    get_session,
)


def _paperless_enricher():
    enricher = AsyncMock()
    return patch(
        "doc_intelligence_hub.modules.action_queue.enricher.PaperlessEnricher",
        return_value=enricher,
    ), enricher


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
        assert "updated_at" in action

    def test_actions_include_all_lifecycle_statuses_by_default(self, client, seed_actions):
        data = client.get("/api/action-queue/actions").json()
        assert {action["status"] for action in data} == {"pending", "completed"}

    def test_all_status_alias_and_deterministic_pagination(self, client, seed_actions):
        all_actions = client.get("/api/action-queue/actions?status=all").json()
        first_page = client.get("/api/action-queue/actions?status=all&limit=2&offset=0").json()
        second_page = client.get("/api/action-queue/actions?status=all&limit=2&offset=2").json()

        assert first_page + second_page == all_actions
        assert len({action["id"] for action in all_actions}) == 3

    def test_updated_since_filter(self, client, seed_actions):
        assert (
            client.get("/api/action-queue/actions?updated_since=2099-01-01T00:00:00").json() == []
        )
        assert (
            len(client.get("/api/action-queue/actions?updated_since=2000-01-01T00:00:00").json())
            == 3
        )

    def test_legacy_stored_status_is_normalized(self, client, seed_actions):
        db = get_session()
        try:
            action = db.query(Action).filter_by(status="completed").one()
            action.status = "done"
            db.commit()
        finally:
            db.close()

        data = client.get("/api/action-queue/actions").json()
        normalized = next(action for action in data if action["document_id"] == 3)
        assert normalized["status"] == "completed"
        completed = client.get("/api/action-queue/actions?status=completed").json()
        assert any(action["document_id"] == 3 for action in completed)

    def test_expired_snooze_resurfaces_before_filtering(self, client, seed_actions):
        db = get_session()
        try:
            action = db.query(Action).filter_by(status="pending").first()
            action.status = "snoozed"
            action.snoozed_until = datetime(2020, 1, 1)
            action_id = str(action.id)
            db.commit()
        finally:
            db.close()

        pending = client.get("/api/action-queue/actions?status=pending").json()
        resurfaced = next(action for action in pending if action["id"] == action_id)
        assert resurfaced["status"] == "pending"
        assert resurfaced["snoozed_until"] is None


class TestMCUpdateAction:
    """Tests for PATCH /api/action-queue/actions/{id}."""

    def test_mark_done(self, client, seed_actions):
        actions = client.get("/api/action-queue/actions?status=pending").json()
        action_id = next(
            action["id"]
            for action in actions
            if action["action_type"].upper() not in {"FILE", "ARCHIVE"}
        )

        resp = client.patch(
            f"/api/action-queue/actions/{action_id}",
            json={"status": "done"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_status"] == "completed"

    def test_bodyless_patch_keeps_legacy_done_default(self, client, seed_actions):
        actions = client.get("/api/action-queue/actions?status=pending").json()
        action_id = next(
            action["id"]
            for action in actions
            if action["action_type"].upper() not in {"FILE", "ARCHIVE"}
        )
        resp = client.patch(f"/api/action-queue/actions/{action_id}")
        assert resp.status_code == 200
        assert resp.json()["new_status"] == "completed"

    def test_completion_syncs_to_paperless_and_tracks_status(self, client, seed_actions):
        action_data = next(
            action
            for action in client.get("/api/action-queue/actions?status=pending").json()
            if action["action_type"].upper() not in {"FILE", "ARCHIVE"}
        )
        action_id = action_data["id"]
        enricher_patch, enricher = _paperless_enricher()

        with enricher_patch:
            resp = client.patch(
                f"/api/action-queue/actions/{action_id}",
                json={"status": "completed"},
            )

        assert resp.status_code == 200
        enricher.sync_status.assert_awaited_once_with(action_data["document_id"], "completed")
        db = get_session()
        try:
            action = db.query(Action).filter_by(id=int(action_id)).one()
            assert action.last_synced_status == "completed"
            assert action.completed_at is not None
        finally:
            db.close()

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

    def test_dismiss_clears_completed_timestamp(self, client, seed_actions):
        completed = client.get("/api/action-queue/actions?status=completed").json()[0]

        resp = client.patch(
            f"/api/action-queue/actions/{completed['id']}",
            json={"status": "dismissed"},
        )

        assert resp.status_code == 200
        dismissed = client.get("/api/action-queue/actions?status=dismissed").json()[0]
        assert dismissed["completed_at"] is None

    def test_reopen_clears_lifecycle_timestamps(self, client, seed_actions):
        completed = client.get("/api/action-queue/actions?status=completed").json()[0]
        db = get_session()
        try:
            action = db.query(Action).filter_by(id=int(completed["id"])).one()
            action.acknowledged_at = datetime(2026, 1, 1, 9, 0, 0)
            action.snoozed_until = datetime(2026, 9, 1, 9, 0, 0)
            db.commit()
        finally:
            db.close()

        resp = client.patch(
            f"/api/action-queue/actions/{completed['id']}",
            json={"status": "reopen"},
        )

        assert resp.status_code == 200
        assert resp.json()["new_status"] == "pending"
        db = get_session()
        try:
            action = db.query(Action).filter_by(id=int(completed["id"])).one()
            assert action.completed_at is None
            assert action.acknowledged_at is None
            assert action.snoozed_until is None
        finally:
            db.close()

    def test_file_action_requires_file_document_source_action(self, client, seed_actions):
        action_id = int(client.get("/api/action-queue/actions").json()[0]["id"])
        db = get_session()
        try:
            action = db.query(Action).filter_by(id=action_id).one()
            action.action_type = "FILE"
            db.commit()
        finally:
            db.close()

        resp = client.patch(
            f"/api/action-queue/actions/{action_id}",
            json={"status": "done"},
        )

        assert resp.status_code == 409
        assert "file_document" in resp.json()["error"]["message"]

    def test_rejects_unsupported_status(self, client, seed_actions):
        action_id = client.get("/api/action-queue/actions").json()[0]["id"]
        resp = client.patch(
            f"/api/action-queue/actions/{action_id}",
            json={"status": "in_progress"},
        )
        assert resp.status_code == 422

    def test_not_found(self, client):
        resp = client.patch(
            "/api/action-queue/actions/99999",
            json={"status": "done"},
        )
        assert resp.status_code == 404


class TestMCSourceActions:
    def test_snooze_updates_source_and_paperless(self, client, seed_actions):
        action = client.get("/api/action-queue/actions?status=pending").json()[0]
        enricher_patch, enricher = _paperless_enricher()

        with enricher_patch:
            resp = client.post(
                f"/api/action-queue/actions/{action['id']}/snooze",
                json={"until": "2026-09-01T09:00:00"},
            )

        assert resp.status_code == 200
        assert resp.json()["new_status"] == "snoozed"
        assert resp.json()["snoozed_until"] == "2026-09-01T09:00:00Z"
        enricher.sync_status.assert_awaited_once_with(action["document_id"], "snoozed")

    def test_snooze_normalizes_offset_to_utc(self, client, seed_actions):
        action = client.get("/api/action-queue/actions?status=pending").json()[0]
        resp = client.post(
            f"/api/action-queue/actions/{action['id']}/snooze",
            json={"until": "2026-09-01T09:00:00-04:00"},
        )
        assert resp.status_code == 200
        assert resp.json()["snoozed_until"] == "2026-09-01T13:00:00Z"

    def test_not_an_action_records_feedback_and_syncs_paperless(self, client, seed_actions):
        action = client.get("/api/action-queue/actions?status=pending").json()[0]
        enricher_patch, enricher = _paperless_enricher()

        with enricher_patch:
            resp = client.post(
                f"/api/action-queue/actions/{action['id']}/feedback",
                json={"feedback_type": "not_an_action", "reason": "Informational only"},
            )

        assert resp.status_code == 200
        assert resp.json()["action_status"] == "not_an_action"
        enricher.sync_status.assert_awaited_once_with(action["document_id"], "not_an_action")
        db = get_session()
        try:
            stored = db.query(Action).filter_by(id=int(action["id"])).one()
            feedback = db.query(ActionFeedback).filter_by(action_id=stored.id).one()
            assert stored.last_synced_status == "not_an_action"
            assert feedback.feedback_type == "not_an_action"
            assert feedback.reason == "Informational only"
        finally:
            db.close()

    def test_classifier_corrections_update_action_and_feedback(self, client, seed_actions):
        action = client.get("/api/action-queue/actions?status=pending").json()[0]
        action_id = action["id"]
        enricher_patch, enricher = _paperless_enricher()

        type_resp = client.post(
            f"/api/action-queue/actions/{action_id}/feedback",
            json={"feedback_type": "misclassified", "corrected_action_type": "file"},
        )
        urgency_resp = client.post(
            f"/api/action-queue/actions/{action_id}/feedback",
            json={"feedback_type": "wrong_urgency", "corrected_urgency": "low"},
        )
        with enricher_patch:
            amount_resp = client.post(
                f"/api/action-queue/actions/{action_id}/feedback",
                json={"feedback_type": "wrong_amount", "corrected_amount": 42.25},
            )

        assert type_resp.json()["action_type"] == "FILE"
        assert urgency_resp.json()["urgency"] == "LOW"
        assert amount_resp.json()["amount"] == 42.25
        enricher.sync_document_amount.assert_awaited_once_with(
            action["document_id"], 42.25, source="action_queue"
        )
        db = get_session()
        try:
            feedback = (
                db.query(ActionFeedback)
                .filter_by(action_id=int(action_id), feedback_type="wrong_amount")
                .one()
            )
            assert feedback.corrected_amount == 42.25
        finally:
            db.close()


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
