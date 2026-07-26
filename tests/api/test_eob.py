"""Tests for EOB matching endpoints (/api/eob/*)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from doc_intelligence_hub.modules.eob_matching.database import (
    BillRecord,
    EOBRecord,
    MatchRecord,
    MatchingRun,
    get_session as get_eob_session,
    init_db as eob_init_db,
)


class TestEOBCheck:
    """Tests for GET /api/eob/check."""

    def test_check_returns_status(self, client, mock_paperless):
        resp = client.get("/api/eob/check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["module"] == "eob-matching"
        assert "read_only" in data
        assert "paperless" in data


class TestEOBResults:
    """Tests for GET /api/eob/results."""

    def test_results_idle_when_no_runs(self, client):
        resp = client.get("/api/eob/results")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "idle"

    def test_results_with_data(self, client, seed_eob):
        resp = client.get("/api/eob/results")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "run" in data
        assert "matches" in data
        assert len(data["matches"]) == 2

    def test_results_include_extraction_details(self, client, seed_eob):
        resp = client.get("/api/eob/results")
        data = resp.json()
        # Find the high-confidence match (eob_doc=100, bill_doc=200)
        match = next(m for m in data["matches"] if m["eob_document_id"] == 100)
        assert match["eob_details"] is not None
        assert match["eob_details"]["provider_name"] == "UnitedHealth"
        assert match["bill_details"] is not None
        assert match["bill_details"]["provider_name"] == "Dr. Smith"
        assert match["bill_details"]["invoice_number"] == "INV-001"


class TestEOBRuns:
    """Tests for GET /api/eob/runs."""

    def test_runs_empty(self, client):
        resp = client.get("/api/eob/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["runs"] == []
        assert data["total"] == 0

    def test_runs_with_data(self, client, seed_eob):
        resp = client.get("/api/eob/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["runs"][0]["documents_scanned"] == 8

    def test_runs_pagination(self, client, seed_eob):
        resp = client.get("/api/eob/runs?limit=1&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["runs"]) == 1
        assert data["limit"] == 1
        assert data["offset"] == 0


class TestEOBMatches:
    """Tests for GET /api/eob/matches."""

    def test_matches_empty(self, client):
        resp = client.get("/api/eob/matches")
        assert resp.status_code == 200
        data = resp.json()
        assert data["matches"] == []
        assert data["total"] == 0

    def test_matches_with_data(self, client, seed_eob):
        resp = client.get("/api/eob/matches")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["matches"]) == 2

    def test_matches_filter_by_status(self, client, seed_eob):
        resp = client.get("/api/eob/matches?status=confirmed")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["matches"][0]["status"] == "confirmed"

    def test_matches_filter_by_run_id(self, client, seed_eob):
        # Get the run ID from runs endpoint
        runs = client.get("/api/eob/runs").json()
        run_id = runs["runs"][0]["id"]

        resp = client.get(f"/api/eob/matches?run_id={run_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2


class TestUpdateMatch:
    """Tests for PATCH /api/eob/matches/{id}."""

    def test_update_match_confirm(self, client, seed_eob):
        # Get a candidate match
        matches = client.get("/api/eob/matches?status=candidate").json()
        match_id = matches["matches"][0]["id"]

        resp = client.patch(
            f"/api/eob/matches/{match_id}",
            json={"status": "confirmed"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "confirmed"
        assert data["confirmed_at"] is not None

    def test_update_match_reject(self, client, seed_eob):
        matches = client.get("/api/eob/matches?status=candidate").json()
        match_id = matches["matches"][0]["id"]

        resp = client.patch(
            f"/api/eob/matches/{match_id}",
            json={"status": "rejected"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected"

    def test_update_match_not_found(self, client):
        resp = client.patch("/api/eob/matches/99999", json={"status": "confirmed"})
        assert resp.status_code == 404

    def test_update_match_invalid_status(self, client, seed_eob):
        matches = client.get("/api/eob/matches?status=candidate").json()
        match_id = matches["matches"][0]["id"]

        resp = client.patch(
            f"/api/eob/matches/{match_id}",
            json={"status": "invalid"},
        )
        assert resp.status_code == 422


class TestPurgeStale:
    """Tests for POST /api/eob/purge-stale."""

    def test_purge_stale_dry_run(self, client, seed_eob):
        resp = client.post("/api/eob/purge-stale", json={"dry_run": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "dry_run"
        assert "stale_count" in data

    def test_purge_stale_no_body(self, client, seed_eob):
        resp = client.post("/api/eob/purge-stale")
        assert resp.status_code == 200
        data = resp.json()
        # Default dry_run=False, should execute purge
        assert data["status"] == "ok"


class TestRecordDetail:
    """Tests for GET /api/eob/records/{document_id}."""

    def test_record_detail_eob(self, client, seed_eob):
        resp = client.get("/api/eob/records/100")
        assert resp.status_code == 200
        data = resp.json()
        assert data["document_id"] == 100
        assert data["type"] == "eob"
        assert data["eob"]["provider_name"] == "UnitedHealth"
        assert data["eob"]["total_patient_responsibility"] == 150.00

    def test_record_detail_bill(self, client, seed_eob):
        resp = client.get("/api/eob/records/200")
        assert resp.status_code == 200
        data = resp.json()
        assert data["document_id"] == 200
        assert data["type"] == "bill"
        assert data["bill"]["provider_name"] == "Dr. Smith"
        assert data["bill"]["invoice_number"] == "INV-001"

    def test_record_detail_not_found(self, client):
        resp = client.get("/api/eob/records/99999")
        assert resp.status_code == 404


class TestMatchDetail:
    """Tests for GET /api/eob/matches/{match_id}/detail."""

    def test_match_detail(self, client, seed_eob):
        # Get a match ID first
        matches = client.get("/api/eob/matches").json()
        match_id = matches["matches"][0]["id"]

        resp = client.get(f"/api/eob/matches/{match_id}/detail")
        assert resp.status_code == 200
        data = resp.json()
        assert "match" in data
        assert "eob_record" in data or data["eob_record"] is None
        assert "bill_record" in data or data["bill_record"] is None
        # match should include eob_details and bill_details
        assert "eob_details" in data["match"]
        assert "bill_details" in data["match"]

    def test_match_detail_not_found(self, client):
        resp = client.get("/api/eob/matches/99999/detail")
        assert resp.status_code == 404


class TestGetMatchIncludesDetails:
    """Tests for GET /api/eob/matches/{match_id} returning eob_details/bill_details."""

    def test_get_match_includes_eob_and_bill_details(self, client, seed_eob):
        """The single-match endpoint should return eob_details and bill_details
        with actual dollar amounts needed for amount validation cards."""
        matches = client.get("/api/eob/matches").json()
        # Use the match that links eob_doc=100 → bill_doc=200
        match = next(m for m in matches["matches"] if m["eob_document_id"] == 100)

        resp = client.get(f"/api/eob/matches/{match['id']}")
        assert resp.status_code == 200
        data = resp.json()

        # eob_details should be populated with amounts
        assert data["eob_details"] is not None
        assert data["eob_details"]["provider_name"] == "UnitedHealth"
        assert data["eob_details"]["total_patient_responsibility"] == 150.00

        # bill_details should be populated with amounts
        assert data["bill_details"] is not None
        assert data["bill_details"]["provider_name"] == "Dr. Smith"
        assert data["bill_details"]["balance_due"] == 150.00
        assert data["bill_details"]["total_amount"] == 150.00
        assert data["bill_details"]["invoice_number"] == "INV-001"

    def test_get_match_details_second_match(self, client, seed_eob):
        """Verify amount data for the second match (eob_doc=101 → bill_doc=201)."""
        matches = client.get("/api/eob/matches").json()
        match = next(m for m in matches["matches"] if m["eob_document_id"] == 101)

        resp = client.get(f"/api/eob/matches/{match['id']}")
        assert resp.status_code == 200
        data = resp.json()

        assert data["eob_details"] is not None
        assert data["eob_details"]["total_patient_responsibility"] == 75.50
        assert data["bill_details"] is not None
        assert data["bill_details"]["balance_due"] == 75.50

    def test_get_match_not_found_returns_404(self, client):
        resp = client.get("/api/eob/matches/99999")
        assert resp.status_code == 404

    def test_get_match_with_missing_eob_record(self, client, seed_eob):
        """When the EOB record doesn't exist, eob_details should be null."""
        db = get_eob_session()
        try:
            run = db.query(MatchingRun).first()
            orphan = MatchRecord(
                run_id=run.id,
                eob_document_id=9999,  # no EOBRecord for this
                bill_document_id=200,
                score=0.50,
                confidence="LOW",
                status="candidate",
            )
            db.add(orphan)
            db.commit()
            db.refresh(orphan)
            orphan_id = orphan.id
        finally:
            db.close()

        resp = client.get(f"/api/eob/matches/{orphan_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["eob_details"] is None
        # bill_details should still be populated
        assert data["bill_details"] is not None
        assert data["bill_details"]["balance_due"] == 150.00
