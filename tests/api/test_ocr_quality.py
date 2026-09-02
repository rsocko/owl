from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from doc_intelligence_hub.api.routers import ocr_quality as ocr_quality_router
from doc_intelligence_hub.modules.ocr_quality import config as ocr_quality_config
from doc_intelligence_hub.modules.ocr_quality.database import (
    DocumentAssessment,
    InventoryRun,
    get_session,
    init_db,
)
from doc_intelligence_hub.modules.ocr_quality.models import RunStage, RunStatus
from doc_intelligence_hub.modules.ocr_quality.service import _digest as _scope_digest


@pytest.fixture()
def ocr_quality_db(tmp_path):
    original = ocr_quality_config.settings.database_url
    ocr_quality_config.settings.database_url = f"sqlite:///{tmp_path / 'api_test_ocr_quality.db'}"
    init_db()
    ocr_quality_router._active_run_ids.clear()
    yield
    ocr_quality_router._active_run_ids.clear()
    ocr_quality_config.settings.database_url = original


def _seed_run(
    run_id: str = "run-1",
    *,
    status: str = RunStatus.COMPLETED.value,
    stage: str = RunStage.STAGE_1_CORPUS_SCAN.value,
    document_id: int = 1,
) -> None:
    db = get_session()
    try:
        db.add(
            InventoryRun(
                run_id=run_id,
                stage=stage,
                scope_digest="scope",
                config_digest="config",
                instance_digest="instance",
                signal_version="ocr-quality-inventory-signals-v1",
                status=status,
                counts={"assessed": 2},
            )
        )
        db.add(
            DocumentAssessment(
                run_id=run_id,
                first_seen_run_id=run_id,
                document_id=document_id,
                document_version_key=f"checksum:{run_id}",
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


# ---------------------------------------------------------------------------
# Manual trigger endpoints (issue #30, Phase 7 — manual entry points only)
# ---------------------------------------------------------------------------
#
# Starlette's TestClient runs FastAPI ``BackgroundTasks`` to completion as
# part of the same request/response ASGI cycle, so by the time
# ``client.post(...)`` returns here, the scheduled background task has
# already finished (or raised). Tests below rely on that to assert on the
# scheduled service call directly, instead of polling/sleeping.


@pytest.fixture()
def mock_run_corpus_scan(monkeypatch):
    from doc_intelligence_hub.modules.ocr_quality.service import OcrQualityInventoryService

    mock = AsyncMock(return_value={"status": "completed"})
    monkeypatch.setattr(OcrQualityInventoryService, "run_corpus_scan", mock)
    return mock


@pytest.fixture()
def mock_run_stratified_sample(monkeypatch):
    from doc_intelligence_hub.modules.ocr_quality.service import OcrQualityInventoryService

    mock = AsyncMock(return_value={"status": "completed"})
    monkeypatch.setattr(OcrQualityInventoryService, "run_stratified_sample", mock)
    return mock


class TestStartCorpusScan:
    def test_start_returns_run_id_and_schedules_scan(
        self, client, ocr_quality_db, mock_run_corpus_scan
    ):
        resp = client.post("/api/ocr-quality/runs", json={"batch_size": 50})
        assert resp.status_code == 202
        body = resp.json()
        assert body["stage"] == RunStage.STAGE_1_CORPUS_SCAN.value
        assert body["status"] == "running"
        assert body["run_id"]

        mock_run_corpus_scan.assert_awaited_once()
        _, kwargs = mock_run_corpus_scan.call_args
        assert kwargs["run_id"] == body["run_id"]
        assert kwargs["batch_size"] == 50
        assert kwargs["resume"] is False

    def test_start_resolves_scope_filters(self, client, ocr_quality_db, mock_run_corpus_scan):
        resp = client.post("/api/ocr-quality/runs", json={"tags": ["Medical"]})
        assert resp.status_code == 202
        _, kwargs = mock_run_corpus_scan.call_args
        assert kwargs["scope_params"] == {"tags__id__in": "2"}

    def test_duplicate_active_scope_returns_409(self, client, ocr_quality_db):
        """A second concurrent request for the same scope is rejected while

        the first is still actively scheduled (the mock never resolves, so
        it stays "in flight" for the duration of this test).
        """
        from doc_intelligence_hub.modules.ocr_quality.service import OcrQualityInventoryService

        with patch.object(
            OcrQualityInventoryService, "run_corpus_scan", AsyncMock(return_value={})
        ) as _mock:
            # Reserve the run manually as the endpoint would, so a second
            # call sees it as active without needing two real concurrent
            # background tasks (TestClient runs them synchronously).
            db = get_session()
            try:
                db.add(
                    InventoryRun(
                        run_id="already-running",
                        stage=RunStage.STAGE_1_CORPUS_SCAN.value,
                        scope_digest=_scope_digest({}),
                        config_digest="config",
                        instance_digest="instance",
                        signal_version="ocr-quality-inventory-signals-v1",
                        status=RunStatus.RUNNING.value,
                        counts={},
                    )
                )
                db.commit()
            finally:
                db.close()
            ocr_quality_router._active_run_ids.add("already-running")

            resp = client.post("/api/ocr-quality/runs", json={})
            assert resp.status_code == 409
            assert resp.json()["error"]["run_id"] == "already-running"
            _mock.assert_not_called()

    def test_stale_running_row_does_not_block_new_run(
        self, client, ocr_quality_db, mock_run_corpus_scan
    ):
        """A ``running`` row left by a crashed process (not in the in-memory

        active set) does not permanently block a new run for the same scope.
        """
        db = get_session()
        try:
            db.add(
                InventoryRun(
                    run_id="stale-run",
                    stage=RunStage.STAGE_1_CORPUS_SCAN.value,
                    scope_digest=_scope_digest({}),
                    config_digest="config",
                    instance_digest="instance",
                    signal_version="ocr-quality-inventory-signals-v1",
                    status=RunStatus.RUNNING.value,
                    counts={},
                )
            )
            db.commit()
        finally:
            db.close()

        resp = client.post("/api/ocr-quality/runs", json={})
        assert resp.status_code == 202
        assert resp.json()["run_id"] != "stale-run"

    def test_scan_failure_marks_run_failed(self, client, ocr_quality_db):
        from doc_intelligence_hub.modules.ocr_quality.service import OcrQualityInventoryService

        async def _fail(*, batch_size, run_id, resume, scope_params):
            db = get_session()
            try:
                db.add(
                    InventoryRun(
                        run_id=run_id,
                        stage=RunStage.STAGE_1_CORPUS_SCAN.value,
                        scope_digest=_scope_digest(scope_params or {}),
                        config_digest="config",
                        instance_digest="instance",
                        signal_version="ocr-quality-inventory-signals-v1",
                        status=RunStatus.RUNNING.value,
                        counts={},
                    )
                )
                db.commit()
            finally:
                db.close()
            raise RuntimeError("boom")

        with patch.object(
            OcrQualityInventoryService, "run_corpus_scan", AsyncMock(side_effect=_fail)
        ):
            resp = client.post("/api/ocr-quality/runs", json={})
            assert resp.status_code == 202
            run_id = resp.json()["run_id"]

        detail = client.get(f"/api/ocr-quality/runs/{run_id}").json()
        assert detail["status"] == "failed"
        assert run_id not in ocr_quality_router._active_run_ids


class TestResumeCorpusScan:
    def test_resume_unknown_run_404(self, client, ocr_quality_db, mock_run_corpus_scan):
        resp = client.post("/api/ocr-quality/runs/does-not-exist/resume", json={})
        assert resp.status_code == 404
        mock_run_corpus_scan.assert_not_called()

    def test_resume_completed_run_409(self, client, ocr_quality_db, mock_run_corpus_scan):
        _seed_run("completed-run")
        resp = client.post("/api/ocr-quality/runs/completed-run/resume", json={})
        assert resp.status_code == 409
        mock_run_corpus_scan.assert_not_called()

    def test_resume_actively_running_returns_409(self, client, ocr_quality_db):
        _seed_run("active-run", status=RunStatus.RUNNING.value)
        ocr_quality_router._active_run_ids.add("active-run")
        resp = client.post("/api/ocr-quality/runs/active-run/resume", json={})
        assert resp.status_code == 409

    def test_resume_schedules_with_resume_true(self, client, ocr_quality_db, mock_run_corpus_scan):
        _seed_run("interrupted-run", status=RunStatus.RUNNING.value)
        resp = client.post("/api/ocr-quality/runs/interrupted-run/resume", json={"batch_size": 25})
        assert resp.status_code == 202
        assert resp.json()["run_id"] == "interrupted-run"
        mock_run_corpus_scan.assert_awaited_once()
        _, kwargs = mock_run_corpus_scan.call_args
        assert kwargs["run_id"] == "interrupted-run"
        assert kwargs["resume"] is True
        assert kwargs["batch_size"] == 25


class TestStartStratifiedSample:
    def test_sample_unknown_source_run_404(
        self, client, ocr_quality_db, mock_run_stratified_sample
    ):
        resp = client.post("/api/ocr-quality/runs/does-not-exist/sample", json={})
        assert resp.status_code == 404
        mock_run_stratified_sample.assert_not_called()

    def test_sample_starts_and_schedules(self, client, ocr_quality_db, mock_run_stratified_sample):
        _seed_run("source-run")
        resp = client.post(
            "/api/ocr-quality/runs/source-run/sample",
            json={"sample_size": 100, "seed": "seed-1", "min_per_stratum": 3},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["source_run_id"] == "source-run"
        assert body["stage"] == RunStage.STAGE_2_STRATIFIED_SAMPLE.value
        assert body["run_id"] != "source-run"

        mock_run_stratified_sample.assert_awaited_once()
        _, kwargs = mock_run_stratified_sample.call_args
        assert kwargs["source_run_id"] == "source-run"
        assert kwargs["sample_size"] == 100
        assert kwargs["seed"] == "seed-1"
        assert kwargs["min_per_stratum"] == 3

    def test_duplicate_active_sample_returns_409(self, client, ocr_quality_db):
        _seed_run("source-run-2")
        _seed_run(
            "existing-sample",
            stage=RunStage.STAGE_2_STRATIFIED_SAMPLE.value,
            status=RunStatus.RUNNING.value,
            document_id=2,
        )
        db = get_session()
        try:
            run = db.query(InventoryRun).filter_by(run_id="existing-sample").one()
            run.source_run_id = "source-run-2"
            db.commit()
        finally:
            db.close()
        ocr_quality_router._active_run_ids.add("existing-sample")

        resp = client.post("/api/ocr-quality/runs/source-run-2/sample", json={})
        assert resp.status_code == 409
        assert resp.json()["error"]["run_id"] == "existing-sample"
