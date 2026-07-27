"""Tests for the EOB matching API router — database persistence and match management."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from doc_intelligence_hub.api.app import HubSettings, create_app
from doc_intelligence_hub.modules.eob_matching.database import (
    BillRecord,
    EOBRecord,
    MatchingRun,
    MatchRecord,
)
from doc_intelligence_hub.modules.eob_matching.database import (
    configure as eob_configure,
)
from doc_intelligence_hub.modules.eob_matching.database import (
    get_session as get_eob_session,
)
from doc_intelligence_hub.modules.eob_matching.database import (
    init_db as eob_init_db,
)


@pytest.fixture()
def client(tmp_path):
    """Create a test client with temp-file database."""
    db_path = tmp_path / "test_eob.db"
    eob_configure(f"sqlite:///{db_path}")
    eob_init_db()

    hub_settings = HubSettings(
        paperless_url="http://paperless.test",
        paperless_token="test-token",
        write_to_paperless=False,
    )
    app = create_app(hub_settings)
    yield TestClient(app)


@pytest.fixture()
def seeded_client(client, tmp_path):
    """Client with pre-seeded run, records, and matches."""
    db = get_eob_session()
    try:
        run = MatchingRun(
            id=1,
            started_at=datetime(2026, 7, 20, 10, 0, 0, tzinfo=UTC),
            finished_at=datetime(2026, 7, 20, 10, 1, 0, tzinfo=UTC),
            documents_scanned=10,
            eobs_found=4,
            bills_found=3,
            matches_found=2,
            high_confidence=1,
            medium_confidence=1,
            low_confidence=0,
            tags_filter="medical",
        )
        db.add(run)
        db.commit()

        db.add(
            EOBRecord(
                document_id=100,
                run_id=1,
                title="EOB from UHC",
                classification_score=92.0,
                insurance_company="UnitedHealthcare",
                patient_name="John Doe",
                provider_name="Dr. Smith",
                date_of_service="2026-06-15",
                total_patient_responsibility=150.00,
            )
        )
        db.add(
            EOBRecord(
                document_id=101,
                run_id=1,
                title="EOB from Aetna",
                classification_score=88.0,
                insurance_company="Aetna",
                patient_name="John Doe",
                provider_name="City Hospital",
            )
        )
        db.add(
            BillRecord(
                document_id=200,
                run_id=1,
                title="Bill from Dr. Smith",
                classification_score=85.0,
                provider_name="Dr. Smith",
                patient_name="John Doe",
                balance_due=150.00,
                date_of_service="2026-06-15",
            )
        )
        db.add(
            BillRecord(
                document_id=201,
                run_id=1,
                title="Bill from City Hospital",
                classification_score=80.0,
                provider_name="City Hospital",
                patient_name="John Doe",
                balance_due=500.00,
            )
        )
        db.commit()

        db.add(
            MatchRecord(
                id=1,
                run_id=1,
                eob_document_id=100,
                bill_document_id=200,
                score=92.5,
                confidence="HIGH",
                breakdown_date=95.0,
                breakdown_provider=90.0,
                breakdown_patient=100.0,
                breakdown_amount=100.0,
                breakdown_procedures=50.0,
                status="candidate",
            )
        )
        db.add(
            MatchRecord(
                id=2,
                run_id=1,
                eob_document_id=101,
                bill_document_id=201,
                score=72.0,
                confidence="MEDIUM",
                breakdown_date=60.0,
                breakdown_provider=80.0,
                breakdown_patient=100.0,
                breakdown_amount=0.0,
                breakdown_procedures=0.0,
                status="candidate",
            )
        )
        db.commit()
    finally:
        db.close()

    return client


class TestGetResults:
    def test_results_returns_idle_when_empty(self, client):
        resp = client.get("/api/eob/results")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "idle"

    def test_results_returns_last_run(self, seeded_client):
        resp = seeded_client.get("/api/eob/results")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["run"]["id"] == 1
        assert data["run"]["documents_scanned"] == 10
        assert data["run"]["matches_found"] == 2
        assert len(data["matches"]) == 2
        # Sorted by score desc
        assert data["matches"][0]["score"] == 92.5
        assert data["matches"][0]["confidence"] == "HIGH"

    def test_results_includes_preview_urls(self, seeded_client):
        resp = seeded_client.get("/api/eob/results")
        data = resp.json()
        match = data["matches"][0]
        assert match["eob_preview_url"] == "http://paperless.test/documents/100/details"
        assert match["bill_preview_url"] == "http://paperless.test/documents/200/details"

    def test_results_abbreviated_by_default(self, seeded_client):
        resp = seeded_client.get("/api/eob/results")
        data = resp.json()
        eob = data["matches"][0]["eob_details"]
        bill = data["matches"][0]["bill_details"]
        # Abbreviated: missing full-serializer-only fields
        assert "title" not in eob
        assert "classification_score" not in eob
        assert "policy_number" not in eob
        assert "title" not in bill
        assert "classification_score" not in bill
        assert "due_date" not in bill

    def test_results_detailed_includes_full_fields(self, seeded_client):
        resp = seeded_client.get("/api/eob/results?detailed=true")
        data = resp.json()
        assert data["status"] == "ok"
        eob = data["matches"][0]["eob_details"]
        bill = data["matches"][0]["bill_details"]
        # Full serializer fields present
        assert "title" in eob
        assert "classification_score" in eob
        assert "id" in eob
        assert "document_id" in eob
        assert "services_json" in eob
        assert eob["title"] == "EOB from UHC"
        assert eob["classification_score"] == 92.0
        assert "title" in bill
        assert "classification_score" in bill
        assert "id" in bill
        assert "document_id" in bill
        assert "services_json" in bill
        assert bill["title"] == "Bill from Dr. Smith"
        assert bill["classification_score"] == 85.0


class TestListRuns:
    def test_list_runs_empty(self, client):
        resp = client.get("/api/eob/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["runs"] == []
        assert data["total"] == 0

    def test_list_runs_with_data(self, seeded_client):
        resp = seeded_client.get("/api/eob/runs")
        data = resp.json()
        assert data["total"] == 1
        assert len(data["runs"]) == 1
        run = data["runs"][0]
        assert run["documents_scanned"] == 10
        assert run["eobs_found"] == 4
        assert run["tags_filter"] == "medical"

    def test_list_runs_pagination(self, seeded_client):
        resp = seeded_client.get("/api/eob/runs?limit=1&offset=0")
        data = resp.json()
        assert len(data["runs"]) == 1
        assert data["total"] == 1


class TestListMatches:
    def test_list_all_matches(self, seeded_client):
        resp = seeded_client.get("/api/eob/matches")
        data = resp.json()
        assert data["total"] == 2
        assert len(data["matches"]) == 2
        # Sorted by score desc
        assert data["matches"][0]["score"] == 92.5

    def test_filter_by_status(self, seeded_client):
        resp = seeded_client.get("/api/eob/matches?status=candidate")
        data = resp.json()
        assert data["total"] == 2

        resp = seeded_client.get("/api/eob/matches?status=confirmed")
        data = resp.json()
        assert data["total"] == 0

    def test_filter_by_run_id(self, seeded_client):
        resp = seeded_client.get("/api/eob/matches?run_id=1")
        data = resp.json()
        assert data["total"] == 2

        resp = seeded_client.get("/api/eob/matches?run_id=999")
        data = resp.json()
        assert data["total"] == 0

    def test_match_serialization(self, seeded_client):
        resp = seeded_client.get("/api/eob/matches")
        match = resp.json()["matches"][0]
        assert "id" in match
        assert "run_id" in match
        assert "eob_document_id" in match
        assert "bill_document_id" in match
        assert "score" in match
        assert "confidence" in match
        assert "breakdown" in match
        assert "status" in match
        assert "linked_in_paperless" in match
        assert "eob_preview_url" in match
        assert "bill_preview_url" in match
        assert match["breakdown"]["date"] == 95.0

    def test_matches_detailed_includes_full_fields(self, seeded_client):
        resp = seeded_client.get("/api/eob/matches?detailed=true")
        data = resp.json()
        eob = data["matches"][0]["eob_details"]
        bill = data["matches"][0]["bill_details"]
        assert eob["title"] == "EOB from UHC"
        assert eob["classification_score"] == 92.0
        assert "id" in eob
        assert "services_json" in eob
        assert bill["title"] == "Bill from Dr. Smith"
        assert bill["classification_score"] == 85.0
        assert "id" in bill
        assert "services_json" in bill


class TestUpdateMatch:
    def test_confirm_match(self, seeded_client):
        resp = seeded_client.patch("/api/eob/matches/1", json={"status": "confirmed"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "confirmed"
        assert data["confirmed_at"] is not None

    def test_reject_match(self, seeded_client):
        resp = seeded_client.patch("/api/eob/matches/2", json={"status": "rejected"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    def test_reset_to_candidate(self, seeded_client):
        # First confirm
        seeded_client.patch("/api/eob/matches/1", json={"status": "confirmed"})
        # Then reset
        resp = seeded_client.patch("/api/eob/matches/1", json={"status": "candidate"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "candidate"
        assert data["confirmed_at"] is None

    def test_update_nonexistent_match(self, seeded_client):
        resp = seeded_client.patch("/api/eob/matches/999", json={"status": "confirmed"})
        assert resp.status_code == 404

    def test_invalid_status_rejected(self, seeded_client):
        resp = seeded_client.patch("/api/eob/matches/1", json={"status": "invalid"})
        assert resp.status_code == 422


class TestCheck:
    """The check endpoint requires live Paperless connectivity which we can't test here.
    Test the write_to_paperless flag propagation instead."""

    def test_write_flag_defaults_false(self, client):
        # The hub settings set write_to_paperless=False in the fixture
        # Verify the setting propagates correctly
        from unittest.mock import MagicMock

        from doc_intelligence_hub.api.routers.eob import _is_write_enabled

        mock_request = MagicMock()
        mock_request.app.state.hub_settings.write_to_paperless = False
        assert _is_write_enabled(mock_request) is False

        mock_request.app.state.hub_settings.write_to_paperless = True
        assert _is_write_enabled(mock_request) is True
