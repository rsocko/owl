"""Tests for the purge-stale EOB records functionality."""

from datetime import UTC, datetime

import pytest

from doc_intelligence_hub.modules.eob_matching.database import (
    EOBRecord,
    MatchRecord,
    MatchingRun,
    configure as eob_configure,
    get_session as get_eob_session,
    init_db as eob_init_db,
)
from doc_intelligence_hub.modules.eob_matching.purge import (
    find_stale_eobs,
    is_stale_eob,
    purge_stale_eobs,
)


@pytest.fixture()
def db(tmp_path):
    """Create a temp database with tables."""
    db_path = tmp_path / "test_purge.db"
    eob_configure(f"sqlite:///{db_path}")
    eob_init_db()
    session = get_eob_session()
    yield session
    session.close()


@pytest.fixture()
def seeded_db(db):
    """DB with a mix of good and stale EOB records."""
    run = MatchingRun(
        started_at=datetime(2026, 7, 20, 10, 0, 0, tzinfo=UTC),
        finished_at=datetime(2026, 7, 20, 10, 1, 0, tzinfo=UTC),
        documents_scanned=10,
        eobs_found=5,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    # Good record — short provider name with real data
    db.add(
        EOBRecord(
            document_id=100,
            run_id=run.id,
            provider_name="Dr. Smith",
            total_billed=150.0,
            total_allowed=120.0,
            total_plan_pays=100.0,
            total_patient_responsibility=20.0,
        )
    )

    # Stale record — boilerplate provider (>8 words)
    db.add(
        EOBRecord(
            document_id=101,
            run_id=run.id,
            provider_name="The summary below is intended to help you understand",
            total_billed=0,
            total_allowed=0,
            total_plan_pays=0,
            total_patient_responsibility=0,
        )
    )

    # Stale record — contains boilerplate phrase
    db.add(
        EOBRecord(
            document_id=102,
            run_id=run.id,
            provider_name="This is not a bill for services",
            total_billed=0,
            total_allowed=0,
            total_plan_pays=0,
            total_patient_responsibility=0,
        )
    )

    # Stale record — explanation of benefits phrase
    db.add(
        EOBRecord(
            document_id=103,
            run_id=run.id,
            provider_name="Explanation of Benefits statement",
            total_billed=0,
            total_allowed=None,
            total_plan_pays=None,
            total_patient_responsibility=None,
        )
    )

    # Good record — short name, has amounts
    db.add(
        EOBRecord(
            document_id=104,
            run_id=run.id,
            provider_name="UnitedHealthcare",
            total_billed=500.0,
            total_allowed=400.0,
            total_plan_pays=350.0,
            total_patient_responsibility=50.0,
        )
    )

    # Stale record — all amounts zero with >4 word provider
    db.add(
        EOBRecord(
            document_id=105,
            run_id=run.id,
            provider_name="Some random text that is long enough",
            total_billed=0,
            total_allowed=0,
            total_plan_pays=0,
            total_patient_responsibility=0,
        )
    )

    # Add a match referencing a stale EOB
    db.add(
        MatchRecord(
            run_id=run.id,
            eob_document_id=101,
            bill_document_id=200,
            score=0.5,
            confidence="LOW",
        )
    )

    # Add a match referencing a good EOB
    db.add(
        MatchRecord(
            run_id=run.id,
            eob_document_id=100,
            bill_document_id=201,
            score=0.9,
            confidence="HIGH",
        )
    )

    db.commit()
    return db


class TestIsStaleEob:
    def test_good_record_not_stale(self):
        record = EOBRecord(
            provider_name="Dr. Smith",
            total_billed=150.0,
            total_allowed=120.0,
        )
        assert not is_stale_eob(record)

    def test_long_provider_is_stale(self):
        record = EOBRecord(
            provider_name="The summary below is intended to help you understand your benefits",
        )
        assert is_stale_eob(record)

    def test_boilerplate_phrase_is_stale(self):
        record = EOBRecord(provider_name="This is not a bill")
        assert is_stale_eob(record)

    def test_explanation_of_benefits_is_stale(self):
        record = EOBRecord(provider_name="Explanation of Benefits")
        assert is_stale_eob(record)

    def test_empty_amounts_with_long_provider_is_stale(self):
        record = EOBRecord(
            provider_name="Something five words long enough",
            total_billed=0,
            total_allowed=0,
            total_plan_pays=0,
            total_patient_responsibility=0,
        )
        assert is_stale_eob(record)

    def test_empty_amounts_with_short_provider_not_stale(self):
        record = EOBRecord(
            provider_name="Dr. Smith",
            total_billed=0,
            total_allowed=0,
            total_plan_pays=0,
            total_patient_responsibility=0,
        )
        assert not is_stale_eob(record)

    def test_none_provider_not_stale(self):
        record = EOBRecord(provider_name=None)
        assert not is_stale_eob(record)


class TestFindStaleEobs:
    def test_finds_stale_records(self, seeded_db):
        stale = find_stale_eobs(seeded_db)
        stale_doc_ids = {r.document_id for r in stale}
        assert stale_doc_ids == {101, 102, 103, 105}

    def test_excludes_good_records(self, seeded_db):
        stale = find_stale_eobs(seeded_db)
        stale_doc_ids = {r.document_id for r in stale}
        assert 100 not in stale_doc_ids
        assert 104 not in stale_doc_ids


class TestPurgeStaleEobs:
    def test_dry_run_does_not_delete(self, seeded_db):
        result = purge_stale_eobs(seeded_db, dry_run=True)
        assert result.purged_count == 4
        # Records should still exist
        remaining = seeded_db.query(EOBRecord).count()
        assert remaining == 6

    def test_purge_deletes_stale_records(self, seeded_db):
        result = purge_stale_eobs(seeded_db)
        assert result.purged_count == 4
        assert 101 in result.document_ids
        assert 102 in result.document_ids
        assert 103 in result.document_ids
        assert 105 in result.document_ids

        # Only good records remain
        remaining = seeded_db.query(EOBRecord).all()
        assert len(remaining) == 2
        assert {r.document_id for r in remaining} == {100, 104}

    def test_purge_removes_orphaned_matches(self, seeded_db):
        result = purge_stale_eobs(seeded_db)
        assert result.orphaned_matches_removed == 1

        # The match for doc 100 (good) should still exist
        remaining_matches = seeded_db.query(MatchRecord).all()
        assert len(remaining_matches) == 1
        assert remaining_matches[0].eob_document_id == 100

    def test_purge_empty_db(self, db):
        result = purge_stale_eobs(db)
        assert result.purged_count == 0
        assert result.orphaned_matches_removed == 0


class TestPurgeStaleApi:
    """Test the API endpoint via TestClient."""

    @pytest.fixture()
    def api_client(self, tmp_path):
        from fastapi.testclient import TestClient
        from doc_intelligence_hub.api.app import HubSettings, create_app

        db_path = tmp_path / "test_purge_api.db"
        eob_configure(f"sqlite:///{db_path}")
        eob_init_db()

        hub_settings = HubSettings(
            paperless_url="http://paperless.test",
            paperless_token="test-token",
            write_to_paperless=False,
        )
        app = create_app(hub_settings)
        return TestClient(app)

    @pytest.fixture()
    def seeded_api_client(self, api_client):
        db = get_eob_session()
        run = MatchingRun(
            started_at=datetime(2026, 7, 20, 10, 0, 0, tzinfo=UTC),
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        db.add(
            EOBRecord(
                document_id=200,
                run_id=run.id,
                provider_name="The summary below is intended to help you understand",
                total_billed=0,
                total_allowed=0,
            )
        )
        db.add(
            EOBRecord(
                document_id=201,
                run_id=run.id,
                provider_name="Dr. Good Provider",
                total_billed=100.0,
                total_allowed=80.0,
            )
        )
        db.commit()
        db.close()
        return api_client

    def test_purge_stale_dry_run(self, seeded_api_client):
        resp = seeded_api_client.post("/api/eob/purge-stale", json={"dry_run": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "dry_run"
        assert data["stale_count"] == 1
        assert data["records"][0]["document_id"] == 200

    def test_purge_stale_execute(self, seeded_api_client):
        resp = seeded_api_client.post("/api/eob/purge-stale", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["purged_count"] == 1
        assert 200 in data["document_ids"]

    def test_purge_stale_no_body(self, seeded_api_client):
        resp = seeded_api_client.post("/api/eob/purge-stale")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
