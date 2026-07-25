"""Tests for EOB matching endpoints (/api/eob/*)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from doc_intelligence_hub.modules.eob_matching.database import (
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
