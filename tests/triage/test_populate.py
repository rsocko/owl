"""Tests for triage queue population logic."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from doc_intelligence_hub.modules.eob_matching.database import (
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
from doc_intelligence_hub.modules.triage.database import (
    configure as triage_configure,
)
from doc_intelligence_hub.modules.triage.database import (
    init_db as triage_init_db,
)
from doc_intelligence_hub.modules.triage.database import (
    list_queue_items,
)
from doc_intelligence_hub.modules.triage.populate import populate_queue


@pytest.fixture(autouse=True)
def setup_dbs(tmp_path):
    """Set up fresh in-memory databases for each test."""
    eob_db = tmp_path / "eob.db"
    eob_configure(f"sqlite:///{eob_db}")
    eob_init_db()

    triage_db = tmp_path / "triage.db"
    triage_configure(f"sqlite:///{triage_db}")
    triage_init_db()


def _seed_eob_data(
    *,
    low_confidence_count: int = 0,
    high_confidence_count: int = 0,
    multi_candidate_eob: bool = False,
    orphan_eob_count: int = 0,
):
    """Seed EOB matching data with various scenarios."""
    session = get_eob_session()
    try:
        run = MatchingRun(
            started_at=datetime(2026, 7, 20, 10, 0, 0, tzinfo=UTC),
            finished_at=datetime(2026, 7, 20, 10, 1, 0, tzinfo=UTC),
            documents_scanned=20,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        doc_id = 100
        bill_id = 200

        # Low confidence matches
        for i in range(low_confidence_count):
            session.add(
                EOBRecord(
                    document_id=doc_id,
                    run_id=run.id,
                    title=f"Low EOB {i}",
                    provider_name="Provider A",
                )
            )
            session.add(
                MatchRecord(
                    run_id=run.id,
                    eob_document_id=doc_id,
                    bill_document_id=bill_id,
                    score=0.45 + i * 0.05,
                    confidence="LOW",
                    status="candidate",
                    breakdown_date=0.5,
                    breakdown_provider=0.3,
                    breakdown_patient=0.4,
                    breakdown_amount=0.6,
                    breakdown_procedures=0.2,
                )
            )
            doc_id += 1
            bill_id += 1

        # High confidence matches
        for i in range(high_confidence_count):
            session.add(
                EOBRecord(
                    document_id=doc_id,
                    run_id=run.id,
                    title=f"High EOB {i}",
                    provider_name="Provider B",
                )
            )
            session.add(
                MatchRecord(
                    run_id=run.id,
                    eob_document_id=doc_id,
                    bill_document_id=bill_id,
                    score=0.92,
                    confidence="HIGH",
                    status="confirmed",
                )
            )
            doc_id += 1
            bill_id += 1

        # Multi-candidate scenario: one EOB with two similar-score matches
        if multi_candidate_eob:
            session.add(
                EOBRecord(
                    document_id=doc_id, run_id=run.id, title="Multi EOB", provider_name="Provider C"
                )
            )
            session.add(
                MatchRecord(
                    run_id=run.id,
                    eob_document_id=doc_id,
                    bill_document_id=bill_id,
                    score=0.75,
                    confidence="MEDIUM",
                    status="candidate",
                )
            )
            session.add(
                MatchRecord(
                    run_id=run.id,
                    eob_document_id=doc_id,
                    bill_document_id=bill_id + 1,
                    score=0.72,
                    confidence="MEDIUM",
                    status="candidate",
                )
            )
            doc_id += 1
            bill_id += 2

        # Orphan EOBs (no match at all)
        for i in range(orphan_eob_count):
            session.add(
                EOBRecord(
                    document_id=doc_id,
                    run_id=run.id,
                    title=f"Orphan EOB {i}",
                    provider_name="Orphan Provider",
                    date_of_service="2026-01-15",
                )
            )
            doc_id += 1

        session.commit()
    finally:
        session.close()


class TestPopulateQueue:
    def test_empty_database_creates_nothing(self):
        result = populate_queue()
        assert result["items_created"] == 0

    def test_flags_low_confidence_matches(self):
        _seed_eob_data(low_confidence_count=3)
        result = populate_queue()
        assert result["details"]["eob_low_confidence"] == 3
        items = list_queue_items(item_type="eob_match_review")
        assert len(items) == 3
        assert all(i["item_type"] == "eob_match_review" for i in items)

    def test_does_not_flag_high_confidence(self):
        _seed_eob_data(high_confidence_count=5)
        result = populate_queue()
        # High confidence matches are confirmed, not candidates, so nothing flagged
        assert result["details"]["eob_low_confidence"] == 0

    def test_flags_multi_candidate_matches(self):
        _seed_eob_data(multi_candidate_eob=True)
        result = populate_queue()
        assert result["details"]["eob_multi_candidate"] >= 1

    def test_flags_orphan_documents(self):
        _seed_eob_data(orphan_eob_count=2)
        result = populate_queue()
        assert result["details"]["orphan_documents"] == 2
        items = list_queue_items(item_type="orphan_document")
        assert len(items) == 2

    def test_idempotent_no_duplicates(self):
        _seed_eob_data(low_confidence_count=2, orphan_eob_count=1)
        result1 = populate_queue()
        assert result1["items_created"] >= 3
        result2 = populate_queue()
        assert result2["items_created"] == 0

    def test_priority_scales_with_score(self):
        _seed_eob_data(low_confidence_count=2)
        populate_queue()
        items = list_queue_items(sort="priority")
        # Lower scores should get higher priority
        assert items[0]["priority"] >= items[1]["priority"]
