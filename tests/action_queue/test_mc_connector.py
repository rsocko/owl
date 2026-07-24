"""Tests for the Mission Control connector endpoints — /api/action-queue/actions.

Regression coverage for a bug where the MC-format action list omitted
`title`/`confidence` entirely and returned `action_type`/`urgency` in
UPPERCASE, silently breaking Mission Control's `isTaskAction()` filter
(which expects lowercase values per INTEGRATION-API-CONTRACT.md).
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from doc_intelligence_hub.api.app import HubSettings, create_app
from doc_intelligence_hub.modules.action_queue.database import Action, init_db, get_session
from doc_intelligence_hub.modules.action_queue.config import settings as aq_settings


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
        db.add(Action(
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
        ))
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
