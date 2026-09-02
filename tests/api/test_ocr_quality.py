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
