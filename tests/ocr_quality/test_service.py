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
