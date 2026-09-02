from __future__ import annotations

import pytest

from doc_intelligence_hub.modules.ocr_quality import config as ocr_quality_config
from doc_intelligence_hub.modules.ocr_quality.database import (
    DocumentAssessment,
    InventoryRun,
    get_session,
    init_db,
)
from doc_intelligence_hub.modules.ocr_quality.models import RunStage, RunStatus


@pytest.fixture()
def ocr_quality_db(tmp_path):
    original = ocr_quality_config.settings.database_url
    ocr_quality_config.settings.database_url = f"sqlite:///{tmp_path / 'api_test_ocr_quality.db'}"
    init_db()
    yield
    ocr_quality_config.settings.database_url = original


def _seed_run(run_id: str = "run-1") -> None:
    db = get_session()
    try:
        db.add(
            InventoryRun(
                run_id=run_id,
                stage=RunStage.STAGE_1_CORPUS_SCAN.value,
                scope_digest="scope",
                config_digest="config",
                instance_digest="instance",
                signal_version="ocr-quality-inventory-signals-v1",
                status=RunStatus.COMPLETED.value,
                counts={"assessed": 2},
            )
        )
        db.add(
            DocumentAssessment(
                run_id=run_id,
                first_seen_run_id=run_id,
                document_id=1,
                document_version_key="checksum:abc",
                scorer_version="ocr-quality-inventory-signals-v1",
                content_length=100,
                word_count=20,
                preliminary_score=80,
                document_type="5",
                downstream_outcome="reviewed",
            )
        )
        db.commit()
    finally:
        db.close()


class TestListRuns:
    def test_list_runs_empty(self, client, ocr_quality_db):
        resp = client.get("/api/ocr-quality/runs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["runs"] == []
        assert body["redacted"] is True

    def test_list_runs_returns_seeded_run(self, client, ocr_quality_db):
        _seed_run()
        resp = client.get("/api/ocr-quality/runs")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["runs"]) == 1
        assert body["runs"][0]["run_id"] == "run-1"
        # No raw content/titles should ever appear in this response.
        assert "content" not in str(body)


class TestGetRun:
    def test_get_unknown_run_404(self, client, ocr_quality_db):
        resp = client.get("/api/ocr-quality/runs/does-not-exist")
        assert resp.status_code == 404

    def test_get_known_run(self, client, ocr_quality_db):
        _seed_run()
        resp = client.get("/api/ocr-quality/runs/run-1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"


class TestGetRunReport:
    def test_report_unknown_run_404(self, client, ocr_quality_db):
        resp = client.get("/api/ocr-quality/runs/does-not-exist/report")
        assert resp.status_code == 404

    def test_report_known_run_is_privacy_safe(self, client, ocr_quality_db):
        _seed_run()
        resp = client.get("/api/ocr-quality/runs/run-1/report")
        assert resp.status_code == 200
        body = resp.json()
        assert body["redacted"] is True
        assert "preliminary_score_decile_distribution" in body


def _seed_scored_document(
    document_id: int = 1,
    *,
    run_id: str = "run-1",
    review_status: str = "GOOD",
    document_type: str | None = "5",
    correspondent: str | None = "9",
    overlay_score: float | None = 90.0,
    machine_score: float | None = 88.0,
) -> None:
    db = get_session()
    try:
        db.add(
            DocumentAssessment(
                run_id=run_id,
                first_seen_run_id=run_id,
                document_id=document_id,
                document_version_key=f"checksum:abc{document_id}",
                scorer_version="ocr-quality-inventory-signals-v1",
                content_length=100,
                word_count=20,
                preliminary_score=80,
                document_type=document_type,
                correspondent=correspondent,
                document_created="2026-01-01",
                overlay_score=overlay_score,
                machine_score=machine_score,
                review_status=review_status,
                reasons=[
                    {
                        "code": "status.combined_score",
                        "message": "ok",
                        "severity": "info",
                        "component": "profile",
                    }
                ],
                document_profile={"page_count": 1, "dominant_classification": "digital_text"},
                quality_scorer_version="ocr-quality-scoring-v1",
            )
        )
        db.commit()
    finally:
        db.close()


class TestGetCorpusDistribution:
    def test_distribution_empty_corpus(self, client, ocr_quality_db):
        resp = client.get("/api/ocr-quality/distribution")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_documents"] == 0
        assert body["redacted"] is True

    def test_distribution_reflects_seeded_documents(self, client, ocr_quality_db):
        _seed_scored_document(1, review_status="GOOD")
        _seed_scored_document(2, review_status="FAILED", overlay_score=None, machine_score=10.0)
        resp = client.get("/api/ocr-quality/distribution")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_documents"] == 2
        assert body["review_status_distribution"]["GOOD"] == 1
        assert body["review_status_distribution"]["FAILED"] == 1
        # Aggregate-only — no raw content ever appears.
        assert "content" not in str(body)


class TestListDocuments:
    def test_list_empty(self, client, ocr_quality_db):
        resp = client.get("/api/ocr-quality/documents")
        assert resp.status_code == 200
        body = resp.json()
        assert body["documents"] == []
        assert body["total"] == 0
        assert body["redacted"] is True

    def test_list_returns_seeded_documents(self, client, ocr_quality_db):
        _seed_scored_document(1, review_status="GOOD")
        _seed_scored_document(2, review_status="FAILED")
        resp = client.get("/api/ocr-quality/documents")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert {d["document_id"] for d in body["documents"]} == {1, 2}

    def test_list_filters_by_review_status(self, client, ocr_quality_db):
        _seed_scored_document(1, review_status="GOOD")
        _seed_scored_document(2, review_status="FAILED")
        resp = client.get("/api/ocr-quality/documents?review_status=FAILED")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["documents"][0]["document_id"] == 2

    def test_list_filters_by_document_type_and_correspondent(self, client, ocr_quality_db):
        _seed_scored_document(1, document_type="5", correspondent="9")
        _seed_scored_document(2, document_type="6", correspondent="10")
        resp = client.get("/api/ocr-quality/documents?document_type=6")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        resp = client.get("/api/ocr-quality/documents?correspondent=9")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_list_pagination(self, client, ocr_quality_db):
        for i in range(1, 6):
            _seed_scored_document(i)
        resp = client.get("/api/ocr-quality/documents?limit=2&offset=0")
        body = resp.json()
        assert body["total"] == 5
        assert len(body["documents"]) == 2

    def test_list_response_excludes_raw_content(self, client, ocr_quality_db):
        _seed_scored_document(1)
        resp = client.get("/api/ocr-quality/documents")
        assert "content" not in str(resp.json())


class TestGetDocument:
    def test_get_unknown_document_404(self, client, ocr_quality_db):
        resp = client.get("/api/ocr-quality/documents/999")
        assert resp.status_code == 404

    def test_get_known_document_detail(self, client, ocr_quality_db):
        _seed_scored_document(1)
        resp = client.get("/api/ocr-quality/documents/1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["document_id"] == 1
        assert body["review_status"] == "GOOD"
        assert "reasons" in body
        assert "document_profile" in body
        assert body["document_profile"]["dominant_classification"] == "digital_text"
