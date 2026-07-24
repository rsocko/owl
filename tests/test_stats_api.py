"""Tests for the /api/stats endpoint — aggregate metrics across DI modules."""

from datetime import UTC, datetime, date, timedelta

import pytest
from fastapi.testclient import TestClient

from doc_intelligence_hub.api.app import HubSettings, create_app
from doc_intelligence_hub.modules.action_queue.config import settings as aq_settings
from doc_intelligence_hub.modules.action_queue.database import (
    Action,
    ProcessingHistory,
    get_session as get_aq_session,
    init_db as aq_init_db,
)
from doc_intelligence_hub.modules.eob_matching.database import (
    EOBRecord,
    MatchRecord,
    MatchingRun,
    configure as eob_configure,
    get_session as get_eob_session,
    init_db as eob_init_db,
)


@pytest.fixture()
def client(tmp_path):
    """Create a test client with temp-file databases."""
    # Action queue DB
    aq_db_path = tmp_path / "test_actions.db"
    original_aq_db_url = aq_settings.database_url
    aq_settings.database_url = f"sqlite:///{aq_db_path}"
    aq_init_db()

    # EOB DB
    eob_db_path = tmp_path / "test_eob.db"
    eob_configure(f"sqlite:///{eob_db_path}")
    eob_init_db()

    hub_settings = HubSettings(
        paperless_url="http://paperless.test",
        paperless_token="test-token",
    )
    app = create_app(hub_settings)

    yield TestClient(app)

    aq_settings.database_url = original_aq_db_url


@pytest.fixture()
def seeded_client(client, tmp_path):
    """Client with pre-seeded data across all modules."""
    # Seed action queue
    db = get_aq_session()
    try:
        db.add(Action(
            document_id=1,
            document_title="Electric Bill",
            action_type="PAY",
            title="Pay electric bill",
            summary="Monthly electric bill",
            urgency="CRITICAL",
            status="pending",
            created_at=datetime.now(UTC),
        ))
        db.add(Action(
            document_id=2,
            document_title="Insurance Card",
            action_type="FILE",
            title="File insurance card",
            summary="File for records",
            urgency="LOW",
            status="pending",
            created_at=datetime.now(UTC),
        ))
        db.add(Action(
            document_id=3,
            document_title="Old Bill",
            action_type="PAY",
            title="Pay old bill",
            summary="Already paid",
            urgency="MEDIUM",
            status="completed",
            completed_at=datetime.now(UTC) - timedelta(days=5),
            created_at=datetime.now(UTC) - timedelta(days=10),
        ))
        db.add(ProcessingHistory(
            document_id=1,
            processed_at=datetime.now(UTC) - timedelta(days=2),
            success=1,
        ))
        db.add(ProcessingHistory(
            document_id=2,
            processed_at=datetime.now(UTC) - timedelta(days=1),
            success=1,
        ))
        db.add(ProcessingHistory(
            document_id=3,
            processed_at=datetime.now(UTC) - timedelta(days=40),
            success=1,
        ))
        db.commit()
    finally:
        db.close()

    # Seed EOB matching
    eob_db = get_eob_session()
    try:
        run = MatchingRun(
            started_at=datetime(2026, 7, 20, 10, 0, 0, tzinfo=UTC),
            finished_at=datetime(2026, 7, 20, 10, 1, 0, tzinfo=UTC),
            documents_scanned=8,
            eobs_found=3,
            bills_found=2,
            matches_found=2,
            high_confidence=1,
            medium_confidence=1,
            low_confidence=0,
        )
        eob_db.add(run)
        eob_db.commit()
        eob_db.refresh(run)

        eob_db.add(EOBRecord(
            document_id=100,
            run_id=run.id,
            title="EOB from UHC",
            total_patient_responsibility=150.00,
        ))
        eob_db.add(EOBRecord(
            document_id=101,
            run_id=run.id,
            title="EOB from Aetna",
            total_patient_responsibility=75.50,
        ))

        eob_db.add(MatchRecord(
            run_id=run.id,
            eob_document_id=100,
            bill_document_id=200,
            score=0.92,
            confidence="HIGH",
            status="confirmed",
            confirmed_at=datetime(2026, 7, 20, 11, 0, 0, tzinfo=UTC),
        ))
        eob_db.add(MatchRecord(
            run_id=run.id,
            eob_document_id=101,
            bill_document_id=201,
            score=0.65,
            confidence="MEDIUM",
            status="candidate",
        ))
        eob_db.commit()
    finally:
        eob_db.close()

    return client


class TestStatsEndpoint:
    """Tests for GET /api/stats."""

    def test_stats_returns_200_empty_databases(self, client):
        """Stats endpoint works even with empty databases."""
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()

        assert "actions" in data
        assert "documents" in data
        assert "statements" in data
        assert "eob" in data
        assert "modules" in data

        # Empty state
        assert data["actions"]["pending"] == 0
        assert data["actions"]["critical"] == 0
        assert data["actions"]["completed_this_period"] == 0
        assert data["documents"]["total_processed"] == 0
        assert data["eob"]["matched"] == 0
        assert data["eob"]["unmatched"] == 0

    def test_stats_with_seeded_data(self, seeded_client):
        """Stats reflect seeded action queue and EOB data."""
        resp = seeded_client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()

        # Action queue stats
        assert data["actions"]["pending"] == 2
        assert data["actions"]["critical"] == 1
        assert data["actions"]["completed_this_period"] == 1  # within last 30 days

        # Documents processed
        assert data["documents"]["total_processed"] == 3
        assert data["documents"]["added_this_period"] == 2  # 2 within last 30 days

        # EOB stats
        assert data["eob"]["matched"] == 1
        assert data["eob"]["unmatched"] == 1
        assert data["eob"]["unresolved_amount"] == 75.50  # Only EOB 101 is unmatched

    def test_stats_period_filter_week(self, seeded_client):
        """Week period only counts items from last 7 days."""
        resp = seeded_client.get("/api/stats?period=week")
        assert resp.status_code == 200
        data = resp.json()

        # The completed action was 5 days ago — within a week
        assert data["actions"]["completed_this_period"] == 1

        # Only 2 docs processed in last 7 days
        assert data["documents"]["added_this_period"] == 2

    def test_stats_period_filter_quarter(self, seeded_client):
        """Quarter period captures all items within 90 days."""
        resp = seeded_client.get("/api/stats?period=quarter")
        assert resp.status_code == 200
        data = resp.json()

        # All 3 processing entries are within 90 days
        assert data["documents"]["added_this_period"] == 3
        assert data["actions"]["completed_this_period"] == 1

    def test_stats_invalid_period_returns_422(self, client):
        """Invalid period value is rejected."""
        resp = client.get("/api/stats?period=year")
        assert resp.status_code == 422

    def test_stats_modules_structure(self, client):
        """Module status entries have correct structure."""
        resp = client.get("/api/stats")
        data = resp.json()

        modules = data["modules"]
        assert len(modules) == 3

        module_names = {m["name"] for m in modules}
        assert module_names == {"action-queue", "statements", "eob-matching"}

        for module in modules:
            assert "name" in module
            assert "status" in module
            assert module["status"] in ("healthy", "degraded", "down")
            assert "last_sync" in module
            assert "item_count" in module

    def test_stats_action_queue_module_healthy(self, seeded_client):
        """Action queue module reports healthy with item count."""
        resp = seeded_client.get("/api/stats")
        data = resp.json()

        aq_module = next(m for m in data["modules"] if m["name"] == "action-queue")
        assert aq_module["status"] == "healthy"
        assert aq_module["item_count"] == 2  # 2 pending actions

    def test_stats_eob_module_healthy(self, seeded_client):
        """EOB matching module reports healthy with candidate count."""
        resp = seeded_client.get("/api/stats")
        data = resp.json()

        eob_module = next(m for m in data["modules"] if m["name"] == "eob-matching")
        assert eob_module["status"] == "healthy"
        assert eob_module["item_count"] == 1  # 1 candidate match
        assert eob_module["last_sync"] is not None
