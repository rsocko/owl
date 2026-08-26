"""Tests for the action queue API router — preview_url and serialization."""

import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from doc_intelligence_hub.api.app import HubSettings, create_app
from doc_intelligence_hub.modules.action_queue.config import settings as aq_settings
from doc_intelligence_hub.modules.action_queue.database import (
    Action,
    ActionFeedback,
    get_session,
    init_db,
)


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
                recommended_cta=json.dumps(
                    {
                        "id": "pay-online",
                        "label": "Pay online",
                        "url": "https://billing.example/pay",
                    }
                ),
                extracted_data={
                    "payment_url": "https://billing.example/pay",
                    "reference_number": "INV-42",
                    "links": [
                        {
                            "url": "https://billing.example/pay",
                            "label": "Pay online",
                            "purpose": "payment",
                        }
                    ],
                },
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

    def test_terminal_actions_are_paginated_by_latest_lifecycle_change(self, seeded_client):
        db = get_session()
        try:
            db.add(
                Action(
                    id=3,
                    document_id=100,
                    document_title="Recently completed older document",
                    action_type="TASK",
                    title="Recent completion",
                    status="completed",
                    created_at=datetime(2025, 1, 1, 10, 0, 0),
                    updated_at=datetime(2026, 7, 1, 10, 0, 0),
                    completed_at=datetime(2026, 7, 1, 10, 0, 0),
                )
            )
            db.commit()
        finally:
            db.close()

        data = seeded_client.get("/api/queue/actions?status=completed&limit=1").json()

        assert data["total"] == 2
        assert data["actions"][0]["id"] == 3

    def test_list_actions_empty(self, client):
        resp = client.get("/api/queue/actions")
        data = resp.json()
        assert data["total"] == 0
        assert data["actions"] == []

    def test_suggests_and_manually_links_related_pay_action(self, seeded_client):
        from unittest.mock import AsyncMock, patch

        db = get_session()
        try:
            db.add(
                Action(
                    id=3,
                    document_id=43,
                    document_title="Second water billing copy",
                    action_type="PAY",
                    title="Pay electric bill",
                    amount=125.50,
                    urgency="HIGH",
                    confidence=85,
                    status="pending",
                    correspondent="Power Co",
                    extracted_data={"account_identifier": "0513"},
                )
            )
            original = db.query(Action).filter_by(id=1).one()
            original.extracted_data = {
                **(original.extracted_data or {}),
                "account_identifier": "0513",
            }
            db.commit()
        finally:
            db.close()

        candidates = seeded_client.get("/api/queue/actions/1/link-candidates").json()
        assert candidates["candidates"][0]["action"]["id"] == 3

        synchronized: list[tuple[int, str]] = []

        async def capture_sync(_db, action, status, **_kwargs):
            synchronized.append((action.id, status))

        sync_status = AsyncMock(side_effect=capture_sync)
        with patch(
            "doc_intelligence_hub.api.routers.action_queue.sync_action_status",
            sync_status,
        ):
            linked = seeded_client.post(
                "/api/queue/actions/1/link",
                json={"related_action_id": 3},
            )
        assert linked.status_code == 200
        assert linked.json()["linked_document_count"] == 2
        assert synchronized == [(3, "pending")]
        ready = seeded_client.get("/api/queue/actions?status=pending").json()
        assert [action["id"] for action in ready["actions"]] == [1]

    def test_searches_and_links_receipt_without_an_action(self, seeded_client):
        from unittest.mock import AsyncMock, MagicMock, patch

        paperless = MagicMock()
        paperless.list_documents = AsyncMock(
            return_value=[
                {
                    "id": 77,
                    "title": "Power Co payment receipt",
                    "document_type_name": "Receipt",
                    "correspondent_name": "Power Co",
                    "created": "2026-02-10",
                }
            ]
        )
        paperless.get_document = AsyncMock(
            return_value={
                "id": 77,
                "title": "Power Co payment receipt",
                "document_type_name": "Receipt",
                "correspondent_name": "Power Co",
                "created": "2026-02-10",
                "content": "Payment received for invoice INV-42. Total $125.50.",
            }
        )

        with patch(
            "doc_intelligence_hub.api.routers.action_queue.make_paperless_client",
            return_value=paperless,
        ):
            candidates = seeded_client.get(
                "/api/queue/actions/1/link-candidates?q=payment%20receipt"
            ).json()
            linked = seeded_client.post(
                "/api/queue/actions/1/link",
                json={"related_document_id": 77},
            )

        assert candidates["candidates"][-1]["kind"] == "document"
        assert candidates["candidates"][-1]["document"]["id"] == 77
        assert linked.status_code == 200
        assert linked.json()["linked_document_count"] == 2
        assert linked.json()["completion_suggestion"]["receipt_document_id"] == 77

    def test_list_actions_resurfaces_expired_snoozes(self, seeded_client):
        seeded_client.post(
            "/api/queue/actions/1/snooze",
            json={"until": "2020-01-01T00:00:00"},
        )

        pending = seeded_client.get("/api/queue/actions?status=pending").json()
        snoozed = seeded_client.get("/api/queue/actions?status=snoozed").json()

        assert pending["total"] == 1
        assert pending["actions"][0]["id"] == 1
        assert pending["actions"][0]["status"] == "pending"
        assert pending["actions"][0]["snoozed_until"] is None
        assert snoozed["total"] == 0

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
            "document_amount",
            "document_due_date",
            "urgency",
            "severity",
            "confidence",
            "risk_score",
            "status",
            "recommended_cta",
            "action_ready",
            "review_state",
            "needs_review_url",
            "correspondent",
            "document_date",
            "document_type",
            "tags",
            "extracted_data",
            "ai_reasoning",
            "version",
            "action_index",
            "action_position",
            "sibling_count",
            "sibling_action_ids",
            "is_primary",
            "parent_action_id",
            "superseded_by_action_id",
            "obligation_id",
            "linked_documents",
            "linked_document_count",
            "completion_suggestion",
            "preview_url",
            "created_at",
            "updated_at",
            "completed_at",
            "acknowledged_at",
            "snoozed_until",
        }
        assert set(action.keys()) == expected_fields
        assert action["recommended_cta"]["url"] == "https://billing.example/pay"
        assert action["extracted_data"]["reference_number"] == "INV-42"

    def test_daily_list_excludes_not_ready_actions(self, seeded_client):
        db = get_session()
        try:
            action = db.query(Action).filter_by(id=1).one()
            action.action_ready = False
            action.review_state = "needs_review"
            db.commit()
        finally:
            db.close()

        ready = seeded_client.get("/api/queue/actions?status=pending").json()
        review = seeded_client.get(
            "/api/queue/actions?status=pending&include_not_ready=true"
        ).json()
        assert ready["total"] == 0
        assert review["total"] == 1
        assert review["actions"][0]["review_state"] == "needs_review"

    def test_all_history_includes_no_action_but_excludes_needs_review(self, seeded_client):
        feedback = seeded_client.post(
            "/api/queue/actions/1/feedback",
            json={"feedback_type": "not_an_action"},
        )
        assert feedback.status_code == 200
        db = get_session()
        try:
            db.add(
                Action(
                    id=3,
                    document_id=100,
                    document_title="Untrusted pending action",
                    action_type="TASK",
                    title="Needs review",
                    status="pending",
                    action_ready=False,
                    review_state="needs_review",
                )
            )
            db.commit()
        finally:
            db.close()

        data = seeded_client.get("/api/queue/actions?include_resolved_no_action=true").json()

        assert data["total"] == 2
        assert {action["id"] for action in data["actions"]} == {1, 2}


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

    def test_update_reopens_no_action_record_as_ready(self, seeded_client):
        feedback = seeded_client.post(
            "/api/queue/actions/1/feedback",
            json={"feedback_type": "not_an_action"},
        )
        assert feedback.status_code == 200

        reopened = seeded_client.patch(
            "/api/queue/actions/1",
            json={"status": "pending", "dry_run": False},
        )

        assert reopened.status_code == 200
        assert reopened.json()["action_ready"] is True
        assert reopened.json()["review_state"] == "ready"
        pending = seeded_client.get("/api/queue/actions?status=pending").json()
        assert [action["id"] for action in pending["actions"]] == [1]

    def test_bulk_reopens_no_action_record_as_ready(self, seeded_client):
        feedback = seeded_client.post(
            "/api/queue/actions/1/feedback",
            json={"feedback_type": "not_an_action"},
        )
        assert feedback.status_code == 200

        reopened = seeded_client.post(
            "/api/queue/actions/bulk",
            json={"action": "reopen", "action_ids": [1]},
        )

        assert reopened.status_code == 200
        pending = seeded_client.get("/api/queue/actions?status=pending").json()
        assert pending["total"] == 1
        assert pending["actions"][0]["id"] == 1
        assert pending["actions"][0]["action_ready"] is True

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

    def test_type_correction_records_feedback_and_refreshes_cta(self, seeded_client):
        resp = seeded_client.patch(
            "/api/queue/actions/1",
            json={"action_type": "FILE", "version": 1},
        )
        assert resp.status_code == 200
        assert resp.json()["action_type"] == "FILE"
        assert resp.json()["recommended_cta"]["id"] == "archive"

        db = get_session()
        try:
            feedback = db.query(ActionFeedback).one()
            assert feedback.feedback_type == "misclassified"
            assert feedback.original_action_type == "PAY"
            assert feedback.corrected_action_type == "FILE"
        finally:
            db.close()

    def test_correspondent_correction_writes_through_to_paperless(self, seeded_client):
        from unittest.mock import AsyncMock, patch

        paperless = AsyncMock()
        paperless.resolve_correspondent_id.return_value = 74
        with patch(
            "doc_intelligence_hub.api.routers.action_queue.make_paperless_client",
            return_value=paperless,
        ):
            response = seeded_client.patch(
                "/api/queue/actions/1",
                json={"correspondent": "University of Michigan", "version": 1},
            )

        assert response.status_code == 200
        assert response.json()["correspondent"] == "University of Michigan"
        paperless.update_document.assert_awaited_once_with(42, {"correspondent": 74})

    def test_correspondent_correction_keeps_local_value_when_paperless_fails(self, seeded_client):
        from unittest.mock import AsyncMock, patch

        paperless = AsyncMock()
        paperless.resolve_correspondent_id.return_value = 74
        paperless.update_document.side_effect = RuntimeError("write failed")
        with patch(
            "doc_intelligence_hub.api.routers.action_queue.make_paperless_client",
            return_value=paperless,
        ):
            response = seeded_client.patch(
                "/api/queue/actions/1",
                json={"correspondent": "University of Michigan", "version": 1},
            )

        assert response.status_code == 502
        db = get_session()
        try:
            assert db.query(Action).filter_by(id=1).one().correspondent == "Power Co"
        finally:
            db.close()

    def test_file_action_completes_only_after_paperless_filing(self, seeded_client):
        from unittest.mock import AsyncMock, patch

        db = get_session()
        try:
            action = db.query(Action).filter_by(id=1).one()
            action.action_type = "FILE"
            db.commit()
        finally:
            db.close()

        enricher = AsyncMock()
        with patch(
            "doc_intelligence_hub.modules.action_queue.enricher.PaperlessEnricher",
            return_value=enricher,
        ):
            resp = seeded_client.post("/api/queue/actions/1/file")

        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"
        enricher.sync_status.assert_awaited_once_with(42, "completed")

    def test_file_action_surfaces_partial_failure_and_keeps_action_open(self, seeded_client):
        from unittest.mock import AsyncMock, patch

        db = get_session()
        try:
            action = db.query(Action).filter_by(id=1).one()
            action.action_type = "ARCHIVE"
            db.commit()
        finally:
            db.close()

        enricher = AsyncMock()
        enricher.sync_status.side_effect = RuntimeError("tag update failed")
        with patch(
            "doc_intelligence_hub.modules.action_queue.enricher.PaperlessEnricher",
            return_value=enricher,
        ):
            resp = seeded_client.post("/api/queue/actions/1/file")

        assert resp.status_code == 502
        db = get_session()
        try:
            assert db.query(Action).filter_by(id=1).one().status == "pending"
        finally:
            db.close()

    def test_file_action_rejects_read_only_mode_and_keeps_action_open(
        self, seeded_client, monkeypatch
    ):
        db = get_session()
        try:
            action = db.query(Action).filter_by(id=1).one()
            action.action_type = "FILE"
            db.commit()
        finally:
            db.close()
        monkeypatch.setattr(seeded_client.app.state.hub_settings, "write_to_paperless", False)

        response = seeded_client.post("/api/queue/actions/1/file")

        assert response.status_code == 409
        assert "disabled" in response.json()["error"]["message"].lower()
        db = get_session()
        try:
            assert db.query(Action).filter_by(id=1).one().status == "pending"
        finally:
            db.close()

    def test_status_only_patch_does_not_publish_action_waiting_for_review(self, seeded_client):
        db = get_session()
        try:
            action = db.query(Action).filter_by(id=1).one()
            action.action_ready = False
            action.review_state = "needs_review"
            action.review_item_id = "review-1"
            db.commit()
        finally:
            db.close()

        response = seeded_client.patch("/api/queue/actions/1", json={"status": "pending"})

        assert response.status_code == 200
        assert response.json()["action_ready"] is False
        assert response.json()["review_state"] == "needs_review"
        assert response.json()["needs_review_url"].endswith("item=review-1")

    def test_file_action_cannot_use_generic_completion(self, seeded_client):
        db = get_session()
        try:
            action = db.query(Action).filter_by(id=1).one()
            action.action_type = "FILE"
            db.commit()
        finally:
            db.close()

        response = seeded_client.patch(
            "/api/queue/actions/1",
            json={"status": "completed"},
        )

        assert response.status_code == 409
        assert "file action" in response.json()["error"]["message"]

    def test_document_stays_pending_while_a_sibling_action_is_open(self, seeded_client):
        from unittest.mock import AsyncMock, patch

        db = get_session()
        try:
            db.add(
                Action(
                    document_id=42,
                    document_title="Electric Bill Jan 2026",
                    action_type="RESPOND",
                    title="Dispute late fee",
                    urgency="HIGH",
                    status="pending",
                    action_index=1,
                    is_primary=False,
                )
            )
            db.commit()
        finally:
            db.close()

        enricher = AsyncMock()
        with patch(
            "doc_intelligence_hub.modules.action_queue.enricher.PaperlessEnricher",
            return_value=enricher,
        ):
            response = seeded_client.patch(
                "/api/queue/actions/1",
                json={"status": "completed", "version": 1},
            )

        assert response.status_code == 200
        enricher.sync_status.assert_awaited_once_with(42, "pending")

    def test_incomplete_correction_clears_paperless_inference_and_routes_to_review(
        self, seeded_client
    ):
        from unittest.mock import AsyncMock, patch

        projection = AsyncMock()
        enricher = AsyncMock()
        with (
            patch(
                "doc_intelligence_hub.api.routers.action_queue.project_action_metadata",
                new=projection,
            ),
            patch(
                "doc_intelligence_hub.modules.action_queue.enricher.PaperlessEnricher",
                return_value=enricher,
            ),
        ):
            response = seeded_client.patch(
                "/api/queue/actions/1",
                json={"amount": None, "version": 1},
            )

        assert response.status_code == 200
        assert response.json()["action_ready"] is False
        assert response.json()["review_state"] == "needs_review"
        projection.assert_awaited_once()
        assert projection.await_args.kwargs["action_status"] is None
        enricher.sync_document_amount.assert_awaited_once_with(42, None)

    def test_split_creates_stable_sibling_identity(self, seeded_client):
        response = seeded_client.post(
            "/api/queue/actions/1/split",
            json={
                "action_type": "RESPOND",
                "title": "Dispute late fee",
                "summary": "Ask the provider to remove the fee",
                "urgency": "HIGH",
            },
        )

        assert response.status_code == 200
        created = response.json()
        assert created["id"] != 1
        assert created["parent_action_id"] == 1
        assert created["is_primary"] is False
        assert created["sibling_count"] == 2
        assert set(created["sibling_action_ids"]) == {1, created["id"]}

    def test_merge_requires_explicit_conflict_resolution(self, seeded_client):
        created = seeded_client.post(
            "/api/queue/actions/1/split",
            json={"action_type": "RESPOND", "title": "Dispute fee", "urgency": "HIGH"},
        ).json()

        response = seeded_client.post(
            "/api/queue/actions/1/merge",
            json={"absorbed_action_ids": [created["id"]]},
        )

        assert response.status_code == 422
        assert set(response.json()["error"]["fields"]) >= {"action_type", "title"}

    def test_merge_preserves_survivor_and_marks_absorbed_action(self, seeded_client):
        created = seeded_client.post(
            "/api/queue/actions/1/split",
            json={"action_type": "RESPOND", "title": "Dispute fee", "urgency": "HIGH"},
        ).json()

        response = seeded_client.post(
            "/api/queue/actions/1/merge",
            json={
                "absorbed_action_ids": [created["id"]],
                "action_type": "PAY",
                "title": "Pay electric bill",
                "summary": "Monthly electric bill due",
                "due_date": "2026-02-15",
                "amount": 125.5,
                "urgency": "CRITICAL",
            },
        )

        assert response.status_code == 200
        assert response.json()["id"] == 1
        siblings = seeded_client.get("/api/queue/actions/1/siblings").json()["actions"]
        absorbed = next(action for action in siblings if action["id"] == created["id"])
        assert absorbed["superseded_by_action_id"] == 1
        assert absorbed["action_ready"] is False

    def test_status_counts_exclude_uncertain_pending_actions(self, seeded_client):
        db = get_session()
        try:
            db.add(
                Action(
                    document_id=101,
                    document_title="Uncertain document",
                    action_type="REVIEW",
                    title="Review uncertain document",
                    status="pending",
                    action_ready=False,
                    review_state="needs_review",
                )
            )
            db.commit()
        finally:
            db.close()

        counts = seeded_client.get("/api/queue/status").json()["database"]
        assert counts["pending"] == 1


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

    def test_resurface_expired_snoozes_promotes_only_expired(self, seeded_client):
        seeded_client.post(
            "/api/queue/actions/1/snooze",
            json={"until": "2020-01-01T00:00:00"},
        )

        resp = seeded_client.post("/api/queue/actions/resurface-expired")

        assert resp.status_code == 200
        assert resp.json() == {"resurfaced": 1, "action_ids": [1]}
        action = seeded_client.get("/api/queue/actions?status=pending").json()["actions"][0]
        assert action["status"] == "pending"
        assert action["snoozed_until"] is None

    def test_resurface_expired_snooze_syncs_pending_to_paperless(self, seeded_client):
        from unittest.mock import AsyncMock, patch

        db = get_session()
        try:
            action = db.query(Action).filter_by(id=1).one()
            action.status = "snoozed"
            action.snoozed_until = datetime(2020, 1, 1)
            action.last_synced_status = "snoozed"
            db.commit()
        finally:
            db.close()

        enricher = AsyncMock()
        with patch(
            "doc_intelligence_hub.modules.action_queue.enricher.PaperlessEnricher",
            return_value=enricher,
        ):
            resp = seeded_client.post("/api/queue/actions/resurface-expired")

        assert resp.status_code == 200
        enricher.sync_status.assert_awaited_once_with(42, "pending")


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

    def test_update_snooze_deadline_without_resending_status(self, seeded_client):
        seeded_client.post(
            "/api/queue/actions/1/snooze",
            json={"until": "2026-09-01T00:00:00"},
        )
        resp = seeded_client.patch(
            "/api/queue/actions/1",
            json={"snoozed_until": "2026-10-01T00:00:00"},
        )
        assert resp.status_code == 200
        assert resp.json()["snoozed_until"] == "2026-10-01T00:00:00"

    def test_explicit_null_status_is_rejected(self, seeded_client):
        resp = seeded_client.patch(
            "/api/queue/actions/1",
            json={"status": None},
        )
        assert resp.status_code == 422

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


class TestRefreshMetadata:
    """Tests for POST /api/queue/actions/refresh-metadata."""

    def test_refresh_metadata_no_candidates(self, seeded_client):
        """When all actions already have metadata, nothing to refresh."""
        # Seed metadata on existing actions so they're not candidates
        db = get_session()
        try:
            for a in db.query(Action).all():
                a.document_date = date(2026, 1, 1)
                a.document_type = "Bill"
                a.tags = ["Inbox"]
            db.commit()
        finally:
            db.close()

        resp = seeded_client.post(
            "/api/queue/actions/refresh-metadata",
            json={"force": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 0
        assert data["message"] == "No actions need metadata refresh."

    def test_refresh_metadata_finds_candidates(self, seeded_client):
        """Actions with null metadata fields are candidates for refresh."""
        # The seeded actions have no document_date/document_type/tags,
        # so they should be candidates. The endpoint will fail to reach
        # Paperless but we verify it finds them.
        from unittest.mock import AsyncMock, patch

        mock_fetch = AsyncMock(
            return_value=(
                {1: "Power Co"},  # correspondents
                {10: "Inbox", 11: "Todo"},  # tags
                {5: "Bill"},  # doc_types
            )
        )
        mock_get_doc = AsyncMock(
            side_effect=[
                {
                    "id": 42,
                    "created": "2026-01-10",
                    "document_type": 5,
                    "tags": [10, 11],
                    "correspondent": 1,
                },
                {
                    "id": 99,
                    "created": "2025-12-20",
                    "document_type": 5,
                    "tags": [10],
                    "correspondent": 1,
                },
            ]
        )

        with (
            patch(
                "doc_intelligence_hub.core.paperless.PaperlessClient.fetch_all_metadata",
                mock_fetch,
            ),
            patch(
                "doc_intelligence_hub.core.paperless.PaperlessClient.get_document",
                mock_get_doc,
            ),
        ):
            resp = seeded_client.post(
                "/api/queue/actions/refresh-metadata",
                json={"force": False},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 2
        assert data["failed"] == 0

        # Verify the metadata was written to the DB
        actions_resp = seeded_client.get("/api/queue/actions")
        for action in actions_resp.json()["actions"]:
            assert action["document_date"] is not None
            assert action["document_type"] == "Bill"
            assert action["tags"] is not None
            assert len(action["tags"]) > 0


class TestRefreshAction:
    """Tests for GET /api/queue/actions/{id}/refresh."""

    def test_refresh_action_replaces_snapshot(self, seeded_client):
        from unittest.mock import AsyncMock, patch

        mock_fetch = AsyncMock(
            return_value=(
                {2: "Updated Utility"},
                {12: "Reviewed"},
                {6: "Statement"},
            )
        )
        mock_get_doc = AsyncMock(
            return_value={
                "id": 42,
                "title": "Corrected Electric Statement",
                "created": "2026-02-03",
                "document_type": 6,
                "tags": [12],
                "correspondent": 2,
                "custom_fields": [
                    {"field": 20, "value": 123.45},
                    {"field": 21, "value": "2026-02-15"},
                ],
            }
        )
        mock_custom_fields = AsyncMock(
            return_value=[
                {"id": 20, "name": "Document Amount", "data_type": "float"},
                {"id": 21, "name": "Document Due Date", "data_type": "date"},
            ]
        )

        with (
            patch(
                "doc_intelligence_hub.core.paperless.PaperlessClient.fetch_all_metadata",
                mock_fetch,
            ),
            patch(
                "doc_intelligence_hub.core.paperless.PaperlessClient.get_document",
                mock_get_doc,
            ),
            patch(
                "doc_intelligence_hub.core.paperless.PaperlessClient.list_custom_fields",
                mock_custom_fields,
            ),
        ):
            resp = seeded_client.get("/api/queue/actions/1/refresh")

        assert resp.status_code == 200
        action = resp.json()
        assert action["document_title"] == "Corrected Electric Statement"
        assert action["correspondent"] == "Updated Utility"
        assert action["document_date"] == "2026-02-03"
        assert action["document_type"] == "Statement"
        assert action["tags"] == ["Reviewed"]
        assert action["document_amount"] == 123.45
        assert action["document_due_date"] == "2026-02-15"
        assert action["version"] == 2

    def test_refresh_action_clears_removed_metadata(self, seeded_client):
        from unittest.mock import AsyncMock, patch

        mock_fetch = AsyncMock(return_value=({}, {}, {}))
        mock_get_doc = AsyncMock(
            return_value={
                "id": 42,
                "title": "Electric Bill",
                "created": None,
                "document_type": None,
                "tag_names": [],
                "correspondent": None,
                "custom_fields": [],
            }
        )
        mock_custom_fields = AsyncMock(
            return_value=[
                {"id": 20, "name": "Document Amount", "data_type": "float"},
                {"id": 21, "name": "Document Due Date", "data_type": "date"},
            ]
        )

        with (
            patch(
                "doc_intelligence_hub.core.paperless.PaperlessClient.fetch_all_metadata",
                mock_fetch,
            ),
            patch(
                "doc_intelligence_hub.core.paperless.PaperlessClient.get_document",
                mock_get_doc,
            ),
            patch(
                "doc_intelligence_hub.core.paperless.PaperlessClient.list_custom_fields",
                mock_custom_fields,
            ),
        ):
            resp = seeded_client.get("/api/queue/actions/1/refresh")

        assert resp.status_code == 200
        action = resp.json()
        assert action["correspondent"] is None
        assert action["document_date"] is None
        assert action["document_type"] is None
        assert action["tags"] == []

    def test_refresh_action_not_found(self, seeded_client):
        resp = seeded_client.get("/api/queue/actions/999/refresh")
        assert resp.status_code == 404

    def test_refresh_action_surfaces_paperless_failure(self, seeded_client):
        from unittest.mock import AsyncMock, patch

        with patch(
            "doc_intelligence_hub.core.paperless.PaperlessClient.fetch_all_metadata",
            AsyncMock(side_effect=RuntimeError("Paperless unavailable")),
        ):
            resp = seeded_client.get("/api/queue/actions/1/refresh")

        assert resp.status_code == 502
