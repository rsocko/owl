"""Tests for the Mission Control connector endpoints — /api/action-queue/actions.

Regression coverage for a bug where the MC-format action list omitted
`title`/`confidence` entirely and returned `action_type`/`urgency` in
UPPERCASE, silently breaking Mission Control's `isTaskAction()` filter
(which expects lowercase values per INTEGRATION-API-CONTRACT.md).
"""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from doc_intelligence_hub.api.app import HubSettings, create_app
from doc_intelligence_hub.modules.action_queue.config import settings as aq_settings
from doc_intelligence_hub.modules.action_queue.database import Action, get_session, init_db


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "test_mc_actions.db"
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
                urgency="CRITICAL",
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
                    "account_identifier": "ending 1234",
                    "reference_number": "INV-42",
                    "email": "billing@example.com",
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
        db.commit()
    finally:
        db.close()
    return client


class TestMcListActions:
    def test_response_includes_title_and_confidence(self, seeded_client):
        resp = seeded_client.get("/api/action-queue/actions")
        assert resp.status_code == 200
        action = resp.json()[0]
        assert action["title"] == "Pay electric bill"
        assert action["confidence"] == 85

    def test_action_type_and_urgency_are_lowercase(self, seeded_client):
        """Mission Control's connector filters on lowercase enum values —
        uppercase values are silently dropped by isTaskAction()."""
        resp = seeded_client.get("/api/action-queue/actions")
        action = resp.json()[0]
        assert action["action_type"] == "pay"
        assert action["urgency"] == "critical"
        assert action["action_type"] == action["action_type"].lower()
        assert action["urgency"] == action["urgency"].lower()

    def test_category_field_present(self, seeded_client):
        resp = seeded_client.get("/api/action-queue/actions")
        action = resp.json()[0]
        assert action["category"] == "pay"

    def test_no_fields_are_blank_for_populated_action(self, seeded_client):
        resp = seeded_client.get("/api/action-queue/actions")
        action = resp.json()[0]
        assert action["category"] != ""
        assert action["title"] != ""
        assert action["confidence"] not in (None, "")

    def test_response_preserves_action_helpers(self, seeded_client):
        action = seeded_client.get("/api/action-queue/actions").json()[0]

        assert action["recommended_cta"] == {
            "id": "pay-online",
            "label": "Pay online",
            "url": "https://billing.example/pay",
        }
        assert action["extracted_data"]["account_identifier"] == "ending 1234"
        assert action["extracted_data"]["reference_number"] == "INV-42"
        assert action["extracted_data"]["links"][0]["purpose"] == "payment"

    def test_response_uses_null_for_missing_action_helpers(self, client):
        db = get_session()
        try:
            db.add(
                Action(
                    id=2,
                    document_id=99,
                    document_title="Legacy notice",
                    action_type="REVIEW",
                    title="Review notice",
                    urgency="LOW",
                    status="pending",
                )
            )
            db.commit()
        finally:
            db.close()

        action = client.get("/api/action-queue/actions").json()[0]
        assert action["recommended_cta"] is None
        assert action["extracted_data"] is None

    def test_default_list_excludes_not_ready_actions_and_opt_in_exposes_review(self, seeded_client):
        db = get_session()
        try:
            action = db.query(Action).filter_by(id=1).one()
            action.action_ready = False
            action.review_state = "needs_review"
            action.review_item_id = "review-123"
            db.commit()
        finally:
            db.close()

        assert seeded_client.get("/api/action-queue/actions").json() == []
        action = seeded_client.get("/api/action-queue/actions?include_not_ready=true").json()[0]
        assert action["action_ready"] is False
        assert action["review_state"] == "needs_review"
        assert action["needs_review_url"].endswith("item=review-123")
        assert action["source_actions"] == []

    def test_connector_preserves_additive_cta_shape_and_file_source_action(self, seeded_client):
        db = get_session()
        try:
            action = db.query(Action).filter_by(id=1).one()
            action.action_type = "FILE"
            action.recommended_cta = json.dumps(
                {
                    "id": "call-provider",
                    "label": "Call records",
                    "phone": "555-0100",
                    "metadata": {"department": "records"},
                }
            )
            db.commit()
        finally:
            db.close()

        action = seeded_client.get("/api/action-queue/actions").json()[0]
        assert action["recommended_cta"]["phone"] == "555-0100"
        assert action["recommended_cta"]["metadata"] == {"department": "records"}
        assert {source["id"] for source in action["source_actions"]} == {
            "send_to_review",
            "file_document",
        }

    def test_read_only_connector_omits_file_source_action(self, tmp_path):
        db_path = tmp_path / "readonly_mc_actions.db"
        app = create_app(
            HubSettings(
                paperless_url="http://paperless.test",
                paperless_token="test-token",
                write_to_paperless=False,
            )
        )
        original_db_url = aq_settings.database_url
        aq_settings.database_url = f"sqlite:///{db_path}"
        try:
            init_db()
            db = get_session()
            try:
                db.add(
                    Action(
                        document_id=42,
                        document_title="Annual statement",
                        action_type="FILE",
                        title="File statement",
                        status="pending",
                        action_ready=True,
                    )
                )
                db.commit()
            finally:
                db.close()

            action = TestClient(app).get("/api/action-queue/actions").json()[0]
            assert {source["id"] for source in action["source_actions"]} == {"send_to_review"}
        finally:
            aq_settings.database_url = original_db_url

    def test_connector_never_exposes_legacy_raw_account_number(self, seeded_client):
        db = get_session()
        try:
            action = db.query(Action).filter_by(id=1).one()
            action.extracted_data = {
                "account_number": "123456789",
                "account_identifier": "ending 6789",
            }
            db.commit()
        finally:
            db.close()

        extracted = seeded_client.get("/api/action-queue/actions").json()[0]["extracted_data"]
        assert "account_number" not in extracted
        assert extracted["account_identifier"] == "ending 6789"

    def test_incomplete_mc_type_correction_routes_to_review_and_returns_current_cta(
        self, seeded_client
    ):
        from unittest.mock import AsyncMock, patch

        db = get_session()
        try:
            action = db.query(Action).filter_by(id=1).one()
            action.action_type = "FILE"
            action.amount = None
            action.recommended_cta = json.dumps({"id": "archive", "label": "File document"})
            db.commit()
        finally:
            db.close()

        with patch(
            "doc_intelligence_hub.api.routers.mc_connector.project_action_metadata",
            new=AsyncMock(),
        ):
            response = seeded_client.post(
                "/api/action-queue/actions/1/feedback",
                json={
                    "feedback_type": "misclassified",
                    "corrected_action_type": "PAY",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["action_type"] == "PAY"
        assert body["recommended_cta"]["id"] == "pay-online"
        assert body["action_ready"] is False
        assert body["review_state"] == "needs_review"
        assert body["needs_review_url"]
