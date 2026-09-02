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

    def test_list_sorts_by_document_id_ascending(self, client, ocr_quality_db):
        _seed_scored_document(1)
        _seed_scored_document(2)
        _seed_scored_document(3)
        resp = client.get("/api/ocr-quality/documents?sort_by=document_id&sort_dir=asc")
        assert resp.status_code == 200
        ids = [d["document_id"] for d in resp.json()["documents"]]
        assert ids == [1, 2, 3]

    def test_list_sorts_by_overlay_score_descending(self, client, ocr_quality_db):
        _seed_scored_document(1, overlay_score=50.0)
        _seed_scored_document(2, overlay_score=90.0)
        _seed_scored_document(3, overlay_score=70.0)
        resp = client.get("/api/ocr-quality/documents?sort_by=overlay_score&sort_dir=desc")
        assert resp.status_code == 200
        ids = [d["document_id"] for d in resp.json()["documents"]]
        assert ids == [2, 3, 1]

    def test_list_unknown_sort_by_falls_back_to_default_order(self, client, ocr_quality_db):
        _seed_scored_document(1)
        _seed_scored_document(2)
        resp = client.get("/api/ocr-quality/documents?sort_by=not_a_real_column")
        assert resp.status_code == 200
        ids = {d["document_id"] for d in resp.json()["documents"]}
        assert ids == {1, 2}

    def test_list_invalid_sort_dir_rejected(self, client, ocr_quality_db):
        resp = client.get("/api/ocr-quality/documents?sort_by=document_id&sort_dir=sideways")
        assert resp.status_code == 422


class TestListDownstreamOutcomes:
    def test_empty_corpus_returns_empty_list(self, client, ocr_quality_db):
        resp = client.get("/api/ocr-quality/downstream-outcomes")
        assert resp.status_code == 200
        assert resp.json() == {"downstream_outcomes": []}

    def test_returns_distinct_sorted_values(self, client, ocr_quality_db):
        _seed_scored_document(1, review_status="GOOD")
        _seed_scored_document(2, review_status="GOOD")
        _seed_scored_document(3, review_status="GOOD")
        db = get_session()
        try:
            for row in db.query(DocumentAssessment).all():
                if row.document_id == 1:
                    row.downstream_outcome = "no_action_needed"
                elif row.document_id == 2:
                    row.downstream_outcome = "action_created"
                else:
                    row.downstream_outcome = "no_action_needed"
            db.commit()
        finally:
            db.close()
        resp = client.get("/api/ocr-quality/downstream-outcomes")
        assert resp.status_code == 200
        assert resp.json() == {"downstream_outcomes": ["action_created", "no_action_needed"]}


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


# ---------------------------------------------------------------------------
# Region-level inspection endpoints (issue #134, Part 1)
# ---------------------------------------------------------------------------


def _make_real_pdf_bytes(text: str = "Hello world region test", pages: int = 1) -> bytes:
    pytest.importorskip("reportlab")
    from io import BytesIO

    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    c = canvas.Canvas(buffer)
    for _ in range(pages):
        c.drawString(100, 750, text)
        c.showPage()
    c.save()
    return buffer.getvalue()


@pytest.fixture()
def clear_region_cache():
    from doc_intelligence_hub.modules.ocr_quality import region_inspection

    region_inspection.clear_cache()
    yield
    region_inspection.clear_cache()


class TestGetDocumentRegions:
    def test_returns_word_geometry_for_page(
        self, client, ocr_quality_db, mock_paperless, clear_region_cache
    ):
        mock_paperless.get_document_preview.return_value = (
            _make_real_pdf_bytes("Hello world"),
            "application/pdf",
        )
        resp = client.get("/api/ocr-quality/documents/1/regions?page=1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["page"] == 1
        assert body["page_count"] == 1
        texts = [w["text"] for w in body["words"]]
        assert "Hello" in texts
        assert "world" in texts
        for word in body["words"]:
            assert word["flagged"] is False

    def test_unknown_page_returns_404(
        self, client, ocr_quality_db, mock_paperless, clear_region_cache
    ):
        mock_paperless.get_document_preview.return_value = (
            _make_real_pdf_bytes("Only one page"),
            "application/pdf",
        )
        resp = client.get("/api/ocr-quality/documents/1/regions?page=5")
        assert resp.status_code == 404

    def test_fetches_pdf_only_once_within_cache_window(
        self, client, ocr_quality_db, mock_paperless, clear_region_cache
    ):
        mock_paperless.get_document_preview.return_value = (
            _make_real_pdf_bytes("Cached document"),
            "application/pdf",
        )
        resp1 = client.get("/api/ocr-quality/documents/1/regions?page=1")
        resp2 = client.get("/api/ocr-quality/documents/1/regions?page=1")
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        mock_paperless.get_document_preview.assert_called_once_with(1)

    def test_cross_references_matching_document_reasons(
        self, client, ocr_quality_db, mock_paperless, clear_region_cache
    ):
        # Seed a document assessment whose stored reasons include an
        # alignment reason, then build a page whose text sits outside its
        # own embedded image (so every word gets flagged "alignment").
        db = get_session()
        try:
            db.add(
                DocumentAssessment(
                    run_id="run-1",
                    first_seen_run_id="run-1",
                    document_id=1,
                    document_version_key="checksum:run-1",
                    scorer_version="ocr-quality-inventory-signals-v1",
                    reasons=[
                        {
                            "code": "overlay.alignment",
                            "message": "Overlay text does not appear well aligned.",
                            "severity": "warning",
                            "component": "overlay",
                        }
                    ],
                )
            )
            db.commit()
        finally:
            db.close()

        from doc_intelligence_hub.modules.ocr_quality import region_inspection

        with patch.object(region_inspection, "build_page_regions") as mock_build:
            mock_build.return_value = {
                "page": 1,
                "page_count": 1,
                "width": 600.0,
                "height": 800.0,
                "error": None,
                "words": [
                    {
                        "text": "Misaligned",
                        "x0": 0.0,
                        "top": 0.0,
                        "x1": 10.0,
                        "bottom": 10.0,
                        "confidence": None,
                        "flagged": True,
                        "flag_reasons": ["alignment"],
                        "matched_reasons": [
                            {
                                "code": "overlay.alignment",
                                "message": "Overlay text does not appear well aligned.",
                                "severity": "warning",
                                "component": "overlay",
                            }
                        ],
                    }
                ],
            }
            mock_paperless.get_document_preview.return_value = (
                _make_real_pdf_bytes("Misaligned"),
                "application/pdf",
            )
            resp = client.get("/api/ocr-quality/documents/1/regions?page=1")
            assert resp.status_code == 200
            word = resp.json()["words"][0]
            assert word["flagged"] is True
            assert word["matched_reasons"][0]["code"] == "overlay.alignment"


class TestGetDocumentPageImage:
    def test_returns_png_image(self, client, ocr_quality_db, mock_paperless, clear_region_cache):
        mock_paperless.get_document_preview.return_value = (
            _make_real_pdf_bytes("Image render test"),
            "application/pdf",
        )
        resp = client.get("/api/ocr-quality/documents/1/pages/1/image")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_unknown_page_returns_404(
        self, client, ocr_quality_db, mock_paperless, clear_region_cache
    ):
        mock_paperless.get_document_preview.return_value = (
            _make_real_pdf_bytes("Only one page"),
            "application/pdf",
        )
        resp = client.get("/api/ocr-quality/documents/1/pages/9/image")
        assert resp.status_code == 404

    def test_invalid_dpi_rejected(self, client, ocr_quality_db, mock_paperless, clear_region_cache):
        resp = client.get("/api/ocr-quality/documents/1/pages/1/image?dpi=5000")
        assert resp.status_code == 422


class TestPaperlessFetchFailure:
    def test_paperless_error_returns_502(
        self, client, ocr_quality_db, mock_paperless, clear_region_cache
    ):
        mock_paperless.get_document_preview.side_effect = RuntimeError("connection refused")
        resp = client.get("/api/ocr-quality/documents/1/regions?page=1")
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Manual annotation endpoints (issue #134, Part 2)
# ---------------------------------------------------------------------------


def _annotation_payload(**overrides):
    payload = {
        "page": 1,
        "x0": 10.0,
        "top": 20.0,
        "x1": 100.0,
        "bottom": 40.0,
        "label": "key_data",
        "note": "Account number region",
        "created_by": "reviewer-1",
    }
    payload.update(overrides)
    return payload


class TestAnnotationCrud:
    def test_create_and_list_annotation(self, client, ocr_quality_db):
        resp = client.post("/api/ocr-quality/documents/1/annotations", json=_annotation_payload())
        assert resp.status_code == 201
        created = resp.json()
        assert created["id"] is not None
        assert created["document_id"] == 1
        assert created["label"] == "key_data"
        assert created["note"] == "Account number region"

        list_resp = client.get("/api/ocr-quality/documents/1/annotations")
        assert list_resp.status_code == 200
        annotations = list_resp.json()["annotations"]
        assert len(annotations) == 1
        assert annotations[0]["id"] == created["id"]

    def test_list_filters_by_page(self, client, ocr_quality_db):
        client.post("/api/ocr-quality/documents/1/annotations", json=_annotation_payload(page=1))
        client.post("/api/ocr-quality/documents/1/annotations", json=_annotation_payload(page=2))
        resp = client.get("/api/ocr-quality/documents/1/annotations?page=2")
        assert resp.status_code == 200
        annotations = resp.json()["annotations"]
        assert len(annotations) == 1
        assert annotations[0]["page"] == 2

    def test_update_annotation_label_and_note(self, client, ocr_quality_db):
        created = client.post(
            "/api/ocr-quality/documents/1/annotations", json=_annotation_payload()
        ).json()
        resp = client.patch(
            f"/api/ocr-quality/documents/1/annotations/{created['id']}",
            json={"label": "wrong", "note": "Actually incorrect"},
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert updated["label"] == "wrong"
        assert updated["note"] == "Actually incorrect"
        # Untouched fields remain as originally created.
        assert updated["x0"] == created["x0"]

    def test_update_can_clear_note(self, client, ocr_quality_db):
        created = client.post(
            "/api/ocr-quality/documents/1/annotations", json=_annotation_payload()
        ).json()
        resp = client.patch(
            f"/api/ocr-quality/documents/1/annotations/{created['id']}",
            json={"note": None},
        )
        assert resp.status_code == 200
        assert resp.json()["note"] is None

    def test_update_unknown_annotation_404(self, client, ocr_quality_db):
        resp = client.patch(
            "/api/ocr-quality/documents/1/annotations/999",
            json={"label": "wrong"},
        )
        assert resp.status_code == 404

    def test_delete_annotation(self, client, ocr_quality_db):
        created = client.post(
            "/api/ocr-quality/documents/1/annotations", json=_annotation_payload()
        ).json()
        del_resp = client.delete(f"/api/ocr-quality/documents/1/annotations/{created['id']}")
        assert del_resp.status_code == 204

        list_resp = client.get("/api/ocr-quality/documents/1/annotations")
        assert list_resp.json()["annotations"] == []

    def test_delete_unknown_annotation_404(self, client, ocr_quality_db):
        resp = client.delete("/api/ocr-quality/documents/1/annotations/999")
        assert resp.status_code == 404

    def test_create_validates_required_label(self, client, ocr_quality_db):
        payload = _annotation_payload()
        del payload["label"]
        resp = client.post("/api/ocr-quality/documents/1/annotations", json=payload)
        assert resp.status_code == 422

    def test_annotations_scoped_per_document(self, client, ocr_quality_db):
        client.post("/api/ocr-quality/documents/1/annotations", json=_annotation_payload())
        client.post("/api/ocr-quality/documents/2/annotations", json=_annotation_payload())
        doc1 = client.get("/api/ocr-quality/documents/1/annotations").json()["annotations"]
        doc2 = client.get("/api/ocr-quality/documents/2/annotations").json()["annotations"]
        assert len(doc1) == 1
        assert len(doc2) == 1
        assert doc1[0]["id"] != doc2[0]["id"]
