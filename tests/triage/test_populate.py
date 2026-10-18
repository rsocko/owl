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
from doc_intelligence_hub.modules.ocr_quality.config import settings as ocr_quality_settings
from doc_intelligence_hub.modules.ocr_quality.database import DocumentAssessment
from doc_intelligence_hub.modules.ocr_quality.database import get_session as get_ocr_session
from doc_intelligence_hub.modules.ocr_quality.database import init_db as ocr_quality_init_db
from doc_intelligence_hub.modules.triage.database import (
    TriageQueueItem,
    list_queue_items,
)
from doc_intelligence_hub.modules.triage.database import (
    configure as triage_configure,
)
from doc_intelligence_hub.modules.triage.database import get_session as get_triage_session
from doc_intelligence_hub.modules.triage.database import (
    init_db as triage_init_db,
)
from doc_intelligence_hub.modules.triage.populate import populate_queue


@pytest.fixture(autouse=True)
def setup_dbs(tmp_path):
    """Set up fresh in-memory databases for each test."""
    original_ocr_db_url = ocr_quality_settings.database_url
    eob_db = tmp_path / "eob.db"
    eob_configure(f"sqlite:///{eob_db}")
    eob_init_db()

    triage_db = tmp_path / "triage.db"
    triage_configure(f"sqlite:///{triage_db}")
    triage_init_db()

    ocr_quality_settings.database_url = f"sqlite:///{tmp_path / 'ocr-quality.db'}"
    ocr_quality_init_db()
    yield
    ocr_quality_settings.database_url = original_ocr_db_url


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
                    created_at=datetime(2026, 5, 1, tzinfo=UTC),
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

    def test_projects_review_worthy_ocr_assessments_without_raw_text(self):
        session = get_ocr_session()
        try:
            session.add_all(
                [
                    DocumentAssessment(
                        run_id="run-1",
                        first_seen_run_id="run-1",
                        document_id=501,
                        document_version_key="checksum:failed",
                        scorer_version="inventory-v1",
                        content_length=5000,
                        word_count=900,
                        preliminary_score=15,
                        document_type="2",
                        correspondent="9",
                        document_created="2026-09-01",
                        overlay_score=12,
                        machine_score=44,
                        review_status="FAILED",
                        reasons=[
                            {
                                "code": "overlay.alignment",
                                "message": "Overlay alignment is unreliable.",
                                "severity": "blocking",
                            }
                        ],
                        quality_scorer_version="quality-v2",
                    ),
                    DocumentAssessment(
                        run_id="run-1",
                        first_seen_run_id="run-1",
                        document_id=502,
                        document_version_key="checksum:good",
                        scorer_version="inventory-v1",
                        content_length=100,
                        word_count=20,
                        preliminary_score=95,
                        overlay_score=95,
                        machine_score=96,
                        review_status="GOOD",
                    ),
                ]
            )
            session.commit()
        finally:
            session.close()

        first = populate_queue()
        second = populate_queue()

        assert first["details"]["ocr_quality_reviews"] == 1
        assert second["details"]["ocr_quality_reviews"] == 0
        reviews = list_queue_items(item_type="ocr_quality_review")
        assert len(reviews) == 1
        review = reviews[0]
        assert review["target_id"] == "501"
        assert review["priority"] >= 88
        assert review["reason"] == "Overlay alignment is unreliable."
        assert review["metadata"]["review_status"] == "FAILED"
        assert review["metadata"]["weakest_dimension"] == "overlay"
        assert review["metadata"]["weakest_score"] == 12.0
        assert review["metadata"]["candidate_state"] == "none"
        assert "content" not in review["metadata"]
        assert "word_count" not in review["metadata"]

        triage_session = get_triage_session()
        try:
            stored = (
                triage_session.query(TriageQueueItem)
                .filter(TriageQueueItem.item_type == "ocr_quality_review")
                .one()
            )
            stored.status = "resolved"
            triage_session.commit()
        finally:
            triage_session.close()

        assert populate_queue()["details"]["ocr_quality_reviews"] == 0
        assert len(list_queue_items(item_type="ocr_quality_review", status="resolved")) == 1
