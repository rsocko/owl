from __future__ import annotations

import pytest

from doc_intelligence_hub.core.paperless import PaperlessPage
from doc_intelligence_hub.modules.ocr_quality import config as ocr_quality_config
from doc_intelligence_hub.modules.ocr_quality.database import (
    DocumentAssessment,
    InventoryRun,
    PdfProfile,
    RunFailure,
    SampleSelection,
    get_session,
    init_db,
)
from doc_intelligence_hub.modules.ocr_quality.models import RunStatus
from doc_intelligence_hub.modules.ocr_quality.service import OcrQualityInventoryService


class FakeClient:
    base_url = "https://paperless.private.invalid"

    def __init__(self, documents: list[dict], previews: dict[int, bytes] | None = None):
        self.documents = {int(d["id"]): d for d in documents}
        self.previews = previews or {}
        self.preview_calls: list[int] = []
        self.scope_params_seen: list[dict | None] = []

    async def iter_document_pages(
        self, *, page_size: int, cursor: str | None = None, scope_params: dict | None = None
    ):
        self.scope_params_seen.append(scope_params)
        documents = list(self.documents.values())
        start = int(cursor or "1") - 1
        if start >= len(documents):
            return
        for offset in range(start, len(documents), page_size):
            page_number = offset // page_size + 1
            next_cursor = str(page_number + 1) if offset + page_size < len(documents) else None
            yield PaperlessPage(
                tuple(documents[offset : offset + page_size]),
                next_cursor,
                len(documents),
            )

    async def get_document_preview(self, document_id: int) -> tuple[bytes, str]:
        self.preview_calls.append(document_id)
        if document_id not in self.previews:
            raise RuntimeError("no preview available")
        return self.previews[document_id], "application/pdf"


def _doc(doc_id: int, *, content: str = "some normal document content here", **overrides) -> dict:
    base = {
        "id": doc_id,
        "content": content,
        "checksum": f"chk-{doc_id}",
        "document_type": 5,
        "correspondent": 9,
        "created": "2024-01-01T00:00:00",
    }
    base.update(overrides)
    return base


@pytest.fixture()
def ocr_db(tmp_path):
    original = ocr_quality_config.settings.database_url
    ocr_quality_config.settings.database_url = f"sqlite:///{tmp_path / 'test_ocr_quality.db'}"
    init_db()
    yield
    ocr_quality_config.settings.database_url = original


def _minimal_pdf_bytes() -> bytes:
    reportlab = pytest.importorskip("reportlab")
    from io import BytesIO

    buffer = BytesIO()
    c = reportlab.pdfgen.canvas.Canvas(buffer)
    c.drawString(100, 750, "Native digital text page for profiling tests.")
    c.showPage()
    c.save()
    return buffer.getvalue()


class TestRunCorpusScan:
    @pytest.mark.asyncio
    async def test_empty_corpus_completes_with_zero_counts(self, ocr_db):
        client = FakeClient([])
        service = OcrQualityInventoryService(client, get_session)
        result = await service.run_corpus_scan(batch_size=10)
        assert result["status"] == RunStatus.COMPLETED.value
        assert result["counts"] == {}
        assert result["redacted"] is True

    @pytest.mark.asyncio
    async def test_assesses_every_document(self, ocr_db):
        docs = [_doc(i) for i in range(1, 6)]
        client = FakeClient(docs)
        service = OcrQualityInventoryService(client, get_session)
        result = await service.run_corpus_scan(batch_size=2)
        assert result["counts"]["assessed"] == 5

        db = get_session()
        try:
            rows = db.query(DocumentAssessment).all()
            assert len(rows) == 5
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_partial_run_then_resume_completes_without_duplicates(self, ocr_db):
        docs = [_doc(i) for i in range(1, 11)]

        class FlakyClient(FakeClient):
            """Fails partway through the first attempt, simulating an interruption."""

            def __init__(self, documents):
                super().__init__(documents)
                self.should_fail = True

            async def iter_document_pages(self, *, page_size, cursor=None, scope_params=None):
                async for page in super().iter_document_pages(
                    page_size=page_size, cursor=cursor, scope_params=scope_params
                ):
                    yield page
                    if self.should_fail and page.next_cursor is not None:
                        raise RuntimeError("simulated interruption mid-scan")

        client = FlakyClient(docs)
        service = OcrQualityInventoryService(client, get_session)

        with pytest.raises(RuntimeError, match="simulated interruption"):
            await service.run_corpus_scan(batch_size=2, run_id="run-resume-1")

        db = get_session()
        try:
            run = db.query(InventoryRun).filter_by(run_id="run-resume-1").one()
            assert run.status == "running"
            assert run.cursor is not None
            partial_rows = db.query(DocumentAssessment).all()
            assert len(partial_rows) == 2  # first page only
        finally:
            db.close()

        # Resume: the interruption no longer happens, and the full corpus is
        # available; the scan should continue from the persisted cursor.
        client.should_fail = False
        second = await service.run_corpus_scan(batch_size=2, run_id="run-resume-1", resume=True)
        assert second["status"] == RunStatus.COMPLETED.value

        db = get_session()
        try:
            rows = db.query(DocumentAssessment).all()
            # No duplicate rows per document/version/scorer, and every
            # document ends up assessed exactly once despite the reprocessed
            # overlap around the resume cursor.
            assert len({(r.document_id, r.document_version_key) for r in rows}) == len(rows)
            assert {r.document_id for r in rows} == set(range(1, 11))
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_resume_without_run_id_raises(self, ocr_db):
        client = FakeClient([_doc(1)])
        service = OcrQualityInventoryService(client, get_session)
        with pytest.raises(ValueError, match="resume requires"):
            await service.run_corpus_scan(resume=True)

    @pytest.mark.asyncio
    async def test_resume_with_changed_scope_is_refused(self, ocr_db):
        client = FakeClient([_doc(1), _doc(2)])
        service = OcrQualityInventoryService(client, get_session)
        client.documents = {1: _doc(1)}
        await service.run_corpus_scan(batch_size=1, run_id="run-scope", scope_params=None)

        db = get_session()
        try:
            run = db.query(InventoryRun).filter_by(run_id="run-scope").one()
            run.status = "running"  # simulate an interrupted (not completed) run
            db.commit()
        finally:
            db.close()

        with pytest.raises(ValueError, match="Resume refused"):
            await service.run_corpus_scan(
                batch_size=1,
                run_id="run-scope",
                resume=True,
                scope_params={"tags__id__in": "1"},
            )

    @pytest.mark.asyncio
    async def test_changed_document_content_is_reassessed_as_new_version(self, ocr_db):
        client = FakeClient([_doc(1, content="original content", checksum="chk-v1")])
        service = OcrQualityInventoryService(client, get_session)
        await service.run_corpus_scan(batch_size=10, run_id="run-a")

        client.documents = {1: _doc(1, content="changed content now", checksum="chk-v2")}
        await service.run_corpus_scan(batch_size=10, run_id="run-b")

        db = get_session()
        try:
            rows = db.query(DocumentAssessment).filter_by(document_id=1).all()
            assert len(rows) == 2
            versions = {r.document_version_key for r in rows}
            assert versions == {"checksum:chk-v1", "checksum:chk-v2"}
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_document_failure_does_not_abort_the_run(self, ocr_db, monkeypatch):
        docs = [_doc(1), _doc(2), _doc(3)]
        client = FakeClient(docs)
        service = OcrQualityInventoryService(client, get_session)

        original = service._assess_document
        calls = {"count": 0}

        def _boom_on_second(db, run_id, document):
            calls["count"] += 1
            if calls["count"] == 2:
                from doc_intelligence_hub.modules.ocr_quality.database import RunFailure
                from doc_intelligence_hub.modules.ocr_quality.models import (
                    Disposition,
                    ReasonCode,
                    RunStage,
                )

                db.add(
                    RunFailure(
                        run_id=run_id,
                        document_id=document.get("id"),
                        stage=RunStage.STAGE_1_CORPUS_SCAN.value,
                        reason_code=ReasonCode.FETCH_FAILED.value,
                        error_type="SimulatedFailure",
                    )
                )
                return Disposition.FAILED
            return original(db, run_id, document)

        monkeypatch.setattr(service, "_assess_document", _boom_on_second)
        result = await service.run_corpus_scan(batch_size=10)

        assert result["status"] == RunStatus.COMPLETED.value
        assert result["counts"]["assessed"] == 2
        assert result["counts"]["failed"] == 1

        db = get_session()
        try:
            failures = db.query(RunFailure).all()
            assert len(failures) == 1
            assert failures[0].error_type == "SimulatedFailure"
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_missing_document_id_is_skipped_not_fatal(self, ocr_db):
        client = FakeClient([])
        client.documents = {}
        service = OcrQualityInventoryService(client, get_session)
        db = get_session()
        try:
            disposition = service._assess_document(db, "run-x", {"content": "no id here"})
            db.commit()
        finally:
            db.close()
        from doc_intelligence_hub.modules.ocr_quality.models import Disposition

        assert disposition == Disposition.SKIPPED

    @pytest.mark.asyncio
    async def test_scope_params_are_forwarded_to_client(self, ocr_db):
        client = FakeClient([_doc(1)])
        service = OcrQualityInventoryService(client, get_session)
        await service.run_corpus_scan(scope_params={"tags__id__in": "3"})
        assert client.scope_params_seen == [{"tags__id__in": "3"}]

    @pytest.mark.asyncio
    async def test_zero_or_negative_batch_size_rejected(self, ocr_db):
        client = FakeClient([])
        service = OcrQualityInventoryService(client, get_session)
        with pytest.raises(ValueError):
            await service.run_corpus_scan(batch_size=0)


class TestRunContract:
    """Shared run/state contract behaviors (issue #30): idempotency,
    conflict detection, cooperative cancellation, bounded retries, and
    failure/cancellation alerting.
    """

    @pytest.mark.asyncio
    async def test_concurrent_equivalent_request_raises_conflict(self, ocr_db):
        from doc_intelligence_hub.modules.ocr_quality.models import (
            INVENTORY_SIGNAL_VERSION,
            RunStage,
        )
        from doc_intelligence_hub.modules.ocr_quality.service import (
            RunConflictError,
            _compute_idempotency_key,
            _digest,
        )

        client = FakeClient([_doc(1)])
        service = OcrQualityInventoryService(client, get_session)

        # Mirror run_corpus_scan(batch_size=10)'s own digest computation so
        # this pre-seeded row is recognized as "the same effective request".
        scope_digest = _digest({})
        config_digest = _digest({"batch_size": 10, "signal_version": INVENTORY_SIGNAL_VERSION})
        idempotency_key = _compute_idempotency_key(
            RunStage.STAGE_1_CORPUS_SCAN.value,
            scope_digest=scope_digest,
            config_digest=config_digest,
        )
        # Simulate an already-RUNNING run for the exact same effective
        # request (same scope/config) created by another process/worker.
        db = get_session()
        try:
            db.add(
                InventoryRun(
                    run_id="already-running",
                    stage=RunStage.STAGE_1_CORPUS_SCAN.value,
                    scope_digest=scope_digest,
                    config_digest=config_digest,
                    instance_digest="dummy",
                    signal_version=INVENTORY_SIGNAL_VERSION,
                    status=RunStatus.RUNNING.value,
                    counts={},
                    idempotency_key=idempotency_key,
                )
            )
            db.commit()
        finally:
            db.close()

        with pytest.raises(RunConflictError) as excinfo:
            await service.run_corpus_scan(batch_size=10)
        assert excinfo.value.run_id == "already-running"

    @pytest.mark.asyncio
    async def test_cancellation_mid_run_stops_cleanly(self, ocr_db):
        docs = [_doc(i) for i in range(1, 7)]

        class CancellingClient(FakeClient):
            """Requests cancellation once the first page has been committed.

            Cancellation is requested from a fresh session (mirroring the
            real ``POST /runs/{id}/cancel`` endpoint, which runs in its own
            request/session) at a point where the run's own session has no
            pending writes, matching how this plays out in production.
            """

            def __init__(self, documents, service, run_id):
                super().__init__(documents)
                self._service = service
                self._run_id = run_id
                self._pages_yielded = 0

            async def iter_document_pages(self, *, page_size, cursor=None, scope_params=None):
                async for page in super().iter_document_pages(
                    page_size=page_size, cursor=cursor, scope_params=scope_params
                ):
                    yield page
                    self._pages_yielded += 1
                    if self._pages_yielded == 1:
                        self._service.request_cancellation(self._run_id)

        service = OcrQualityInventoryService(None, get_session)  # client set below
        client = CancellingClient(docs, service, "cancel-run")
        service.client = client

        result = await service.run_corpus_scan(batch_size=2, run_id="cancel-run")

        assert result["status"] == RunStatus.CANCELLED.value
        assert result["cancel_requested"] is True
        # Only the first two pages (4 documents) were processed before the
        # cooperative check observed cancellation and stopped the loop.
        assert sum(result["counts"].values()) == 4

        db = get_session()
        try:
            run = db.query(InventoryRun).filter_by(run_id="cancel-run").one()
            assert run.status == RunStatus.CANCELLED.value
            assert run.cancelled_at is not None
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_request_cancellation_unknown_run_raises(self, ocr_db):
        client = FakeClient([])
        service = OcrQualityInventoryService(client, get_session)
        with pytest.raises(ValueError, match="Unknown run"):
            service.request_cancellation("no-such-run")

    @pytest.mark.asyncio
    async def test_request_cancellation_on_finished_run_raises(self, ocr_db):
        client = FakeClient([_doc(1)])
        service = OcrQualityInventoryService(client, get_session)
        await service.run_corpus_scan(batch_size=10, run_id="finished-run")
        with pytest.raises(ValueError, match="not running"):
            service.request_cancellation("finished-run")

    @pytest.mark.asyncio
    async def test_repeat_manual_stage2_is_idempotent_by_document_version(
        self, ocr_db, monkeypatch
    ):
        docs = [_doc(1)]
        client = FakeClient(docs, previews={1: b"%PDF-fake-bytes-not-real"})
        service = OcrQualityInventoryService(client, get_session)
        await service.run_corpus_scan(batch_size=10, run_id="source-run-manual")

        # Stub out PDF profiling so the test doesn't depend on reportlab
        # being importable in this environment (unrelated, pre-existing
        # issue) -- this test only cares about the idempotency contract.
        from doc_intelligence_hub.modules.ocr_quality.models import Disposition

        async def _fake_profile(self_, db, run_id, document_id, max_pages, *, max_retries=0):
            return Disposition.ASSESSED

        monkeypatch.setattr(OcrQualityInventoryService, "_profile_sampled_document", _fake_profile)

        first = await service.run_manual_stage2(document_id=1, run_id="manual-run-1")
        assert "idempotent_replay_of_run_id" not in first

        second = await service.run_manual_stage2(document_id=1, run_id="manual-run-2")
        assert second["idempotent_replay_of_run_id"] == "manual-run-1"

        db = get_session()
        try:
            # The repeat must not create a second manual run row.
            assert db.query(InventoryRun).filter_by(run_id="manual-run-2").one_or_none() is None
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_forced_repeat_manual_stage2_bypasses_idempotent_replay(
        self, ocr_db, monkeypatch
    ):
        docs = [_doc(1)]
        client = FakeClient(docs, previews={1: b"%PDF-fake-bytes-not-real"})
        service = OcrQualityInventoryService(client, get_session)
        await service.run_corpus_scan(batch_size=10, run_id="source-run-manual-2")

        from doc_intelligence_hub.modules.ocr_quality.models import Disposition

        async def _fake_profile(self_, db, run_id, document_id, max_pages, *, max_retries=0):
            return Disposition.ASSESSED

        monkeypatch.setattr(OcrQualityInventoryService, "_profile_sampled_document", _fake_profile)

        await service.run_manual_stage2(document_id=1, run_id="manual-run-a")
        second = await service.run_manual_stage2(document_id=1, run_id="manual-run-b", force=True)
        assert "idempotent_replay_of_run_id" not in second

        db = get_session()
        try:
            assert db.query(InventoryRun).filter_by(run_id="manual-run-b").one_or_none() is not None
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_manual_stage2_bounded_retry_then_terminal_failure(self, ocr_db, monkeypatch):
        from doc_intelligence_hub.modules.ocr_quality.service import (
            ManualStage2ProfilingFailedError,
        )

        docs = [_doc(1)]
        # No preview registered for document 1 -> get_document_preview always raises.
        client = FakeClient(docs, previews={})
        service = OcrQualityInventoryService(client, get_session)
        await service.run_corpus_scan(batch_size=10, run_id="source-run-retry")

        alert_calls: list[dict] = []
        monkeypatch.setattr(
            "doc_intelligence_hub.modules.ocr_quality.service.alerts.emit_alert",
            lambda **kwargs: alert_calls.append(kwargs),
        )

        with pytest.raises(ManualStage2ProfilingFailedError):
            await service.run_manual_stage2(document_id=1, run_id="manual-retry-run")

        assert len(client.preview_calls) == 1 + ocr_quality_config.settings.run_max_retries

        db = get_session()
        try:
            run = db.query(InventoryRun).filter_by(run_id="manual-retry-run").one()
            assert run.retry_count == ocr_quality_config.settings.run_max_retries
            assert run.status == RunStatus.COMPLETED.value  # terminal failure recorded, not stuck
        finally:
            db.close()

        assert len(alert_calls) == 1
        assert alert_calls[0]["alert_type"] == "ocr_run_failed"

    @pytest.mark.asyncio
    async def test_low_score_alone_does_not_emit_alert(self, ocr_db, monkeypatch):
        docs = [_doc(1)]
        client = FakeClient(docs)
        service = OcrQualityInventoryService(client, get_session)

        alert_calls: list[dict] = []
        monkeypatch.setattr(
            "doc_intelligence_hub.modules.ocr_quality.service.alerts.emit_alert",
            lambda **kwargs: alert_calls.append(kwargs),
        )

        result = await service.run_corpus_scan(batch_size=10, run_id="low-score-run")
        assert result["status"] == RunStatus.COMPLETED.value
        assert alert_calls == []


class TestRunStratifiedSample:
    @pytest.mark.asyncio
    async def test_samples_and_profiles_documents(self, ocr_db):
        docs = [_doc(i) for i in range(1, 11)]
        client = FakeClient(docs, previews={i: _minimal_pdf_bytes() for i in range(1, 11)})
        service = OcrQualityInventoryService(client, get_session)
        stage1 = await service.run_corpus_scan(batch_size=10, run_id="source-run")

        stage2 = await service.run_stratified_sample(
            source_run_id="source-run",
            sample_size=5,
            seed="test-seed",
            run_id="sample-run",
        )
        assert stage1["status"] == RunStatus.COMPLETED.value
        assert stage2["status"] == RunStatus.COMPLETED.value
        assert stage2["sample_size_selected"] <= 5

        db = get_session()
        try:
            selections = db.query(SampleSelection).filter_by(run_id="sample-run").all()
            profiles = db.query(PdfProfile).filter_by(run_id="sample-run").all()
            assert len(selections) == stage2["sample_size_selected"]
            assert len(profiles) == len(selections)
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_unknown_source_run_raises(self, ocr_db):
        client = FakeClient([])
        service = OcrQualityInventoryService(client, get_session)
        with pytest.raises(ValueError, match="Unknown source"):
            await service.run_stratified_sample(
                source_run_id="does-not-exist", sample_size=5, seed="s"
            )

    @pytest.mark.asyncio
    async def test_pdf_download_failure_recorded_without_aborting(self, ocr_db):
        docs = [_doc(i) for i in range(1, 4)]
        client = FakeClient(docs, previews={})  # no previews -> every download fails
        service = OcrQualityInventoryService(client, get_session)
        await service.run_corpus_scan(batch_size=10, run_id="source-run-2")

        result = await service.run_stratified_sample(
            source_run_id="source-run-2",
            sample_size=3,
            seed="s",
            run_id="sample-run-2",
        )
        assert result["status"] == RunStatus.COMPLETED.value
        assert result["counts"].get("failed", 0) >= 1

        db = get_session()
        try:
            failures = db.query(RunFailure).filter_by(run_id="sample-run-2").all()
            assert len(failures) >= 1
        finally:
            db.close()


class TestBuildAggregateReport:
    @pytest.mark.asyncio
    async def test_report_contains_no_raw_identifiers_or_text(self, ocr_db):
        docs = [_doc(i, content=f"secret patient content {i}") for i in range(1, 6)]
        client = FakeClient(docs)
        service = OcrQualityInventoryService(client, get_session)
        await service.run_corpus_scan(batch_size=10, run_id="privacy-run")

        report = service.build_aggregate_report("privacy-run")
        serialized = str(report)
        assert "secret patient content" not in serialized
        assert report["redacted"] is True
        assert report["started_at"].endswith("Z")
        assert report["finished_at"].endswith("Z")
        assert "preliminary_score_decile_distribution" in report

    @pytest.mark.asyncio
    async def test_unknown_run_id_raises(self, ocr_db):
        client = FakeClient([])
        service = OcrQualityInventoryService(client, get_session)
        with pytest.raises(ValueError, match="Unknown inventory run"):
            service.build_aggregate_report("no-such-run")

    @pytest.mark.asyncio
    async def test_stage2_report_has_pdf_profile_distribution(self, ocr_db):
        docs = [_doc(i) for i in range(1, 4)]
        client = FakeClient(docs, previews={i: _minimal_pdf_bytes() for i in range(1, 4)})
        service = OcrQualityInventoryService(client, get_session)
        await service.run_corpus_scan(batch_size=10, run_id="src-run")
        await service.run_stratified_sample(
            source_run_id="src-run", sample_size=3, seed="s", run_id="sample-run-3"
        )
        report = service.build_aggregate_report("sample-run-3")
        assert "pdf_profile_distribution" in report
        assert "sample_size_by_stratum" in report


class TestQualityScorerIntegration:
    """Issue #29's scorer is wired into #25's scan so per-document overlay/
    machine scores and review status are actually persisted (previously
    nothing called ``assess_document``)."""

    @pytest.mark.asyncio
    async def test_stage1_persists_machine_score_without_overlay(self, ocr_db):
        docs = [_doc(1, content="Some perfectly ordinary extracted document text.")]
        client = FakeClient(docs)
        service = OcrQualityInventoryService(client, get_session)
        await service.run_corpus_scan(batch_size=10, run_id="run-1")

        db = get_session()
        try:
            row = db.query(DocumentAssessment).filter_by(document_id=1).one()
        finally:
            db.close()

        assert row.machine_score is not None
        assert row.overlay_score is None
        assert row.review_status is not None
        assert row.quality_scorer_version
        assert isinstance(row.reasons, list)
        assert isinstance(row.document_profile, dict)

    @pytest.mark.asyncio
    async def test_stage2_upgrades_existing_row_with_overlay_score(self, ocr_db):
        docs = [_doc(1)]
        client = FakeClient(docs, previews={1: _minimal_pdf_bytes()})
        service = OcrQualityInventoryService(client, get_session)
        await service.run_corpus_scan(batch_size=10, run_id="src-run")
        await service.run_stratified_sample(
            source_run_id="src-run", sample_size=1, seed="s", run_id="sample-run"
        )

        db = get_session()
        try:
            rows = db.query(DocumentAssessment).filter_by(document_id=1).all()
        finally:
            db.close()

        # Stage 2 updates the same row in place — it does not duplicate it.
        assert len(rows) == 1
        assert rows[0].overlay_score is not None
        assert rows[0].run_id == "sample-run"

    @pytest.mark.asyncio
    async def test_scorer_failure_does_not_abort_scan(self, ocr_db, monkeypatch):
        from doc_intelligence_hub.modules.ocr_quality import service as service_module

        def _boom(**_kwargs):
            raise RuntimeError("scorer exploded")

        monkeypatch.setattr(service_module, "run_quality_scorer", _boom)
        docs = [_doc(1)]
        client = FakeClient(docs)
        service = OcrQualityInventoryService(client, get_session)
        result = await service.run_corpus_scan(batch_size=10, run_id="run-1")

        assert result["status"] == RunStatus.COMPLETED.value
        db = get_session()
        try:
            row = db.query(DocumentAssessment).filter_by(document_id=1).one()
        finally:
            db.close()
        assert row.disposition == "assessed"
        assert row.machine_score is None


class TestDocumentQueries:
    """Read-only per-document query surface for the OWL review UI (#115)."""

    @pytest.mark.asyncio
    async def test_list_document_assessments_filters_by_review_status(self, ocr_db):
        docs = [_doc(i, content="normal readable extracted content here") for i in range(1, 4)]
        docs.append(_doc(4, content=None))
        client = FakeClient(docs)
        service = OcrQualityInventoryService(client, get_session)
        await service.run_corpus_scan(batch_size=10, run_id="run-1")

        all_docs = service.list_document_assessments()
        assert all_docs["total"] == 4
        assert len(all_docs["documents"]) == 4

        by_status = {d["review_status"] for d in all_docs["documents"]}
        one_status = next(iter(by_status))
        filtered = service.list_document_assessments(review_status=one_status)
        assert filtered["total"] >= 1
        assert all(d["review_status"] == one_status for d in filtered["documents"])

    @pytest.mark.asyncio
    async def test_list_document_assessments_pagination(self, ocr_db):
        docs = [_doc(i) for i in range(1, 6)]
        client = FakeClient(docs)
        service = OcrQualityInventoryService(client, get_session)
        await service.run_corpus_scan(batch_size=10, run_id="run-1")

        page = service.list_document_assessments(limit=2, offset=0)
        assert page["total"] == 5
        assert len(page["documents"]) == 2

    @pytest.mark.asyncio
    async def test_get_document_assessment_unknown_returns_none(self, ocr_db):
        service = OcrQualityInventoryService(None, get_session)
        assert service.get_document_assessment(999) is None

    @pytest.mark.asyncio
    async def test_get_document_assessment_returns_detail(self, ocr_db):
        docs = [_doc(1, content="normal readable content")]
        client = FakeClient(docs)
        service = OcrQualityInventoryService(client, get_session)
        await service.run_corpus_scan(batch_size=10, run_id="run-1")

        detail = service.get_document_assessment(1)
        assert detail is not None
        assert detail["document_id"] == 1
        assert "reasons" in detail
        assert "document_profile" in detail
        assert "secret" not in str(detail)

    @pytest.mark.asyncio
    async def test_build_corpus_distribution_empty(self, ocr_db):
        service = OcrQualityInventoryService(None, get_session)
        distribution = service.build_corpus_distribution()
        assert distribution["total_documents"] == 0
        assert distribution["redacted"] is True

    @pytest.mark.asyncio
    async def test_build_corpus_distribution_reflects_latest_assessments(self, ocr_db):
        docs = [_doc(i, content="normal readable content here") for i in range(1, 4)]
        client = FakeClient(docs)
        service = OcrQualityInventoryService(client, get_session)
        await service.run_corpus_scan(batch_size=10, run_id="run-1")

        distribution = service.build_corpus_distribution()
        assert distribution["total_documents"] == 3
        assert sum(distribution["review_status_distribution"].values()) == 3
        assert "overlay_score_decile_distribution" in distribution
        assert "machine_score_decile_distribution" in distribution
