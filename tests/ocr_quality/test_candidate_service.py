"""Tests for :class:`OcrCandidateService` (issue #18, slice 1).

Everything here uses a fake, duck-typed Paperless client — no real network
access, no real ocrmypdf/Azure calls (the fake provider registry below stands
in for real engines). The key invariant under test throughout this module:
**no method here ever calls anything that would write to Paperless.**
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from doc_intelligence_hub.modules.ocr_quality import config as ocr_quality_config
from doc_intelligence_hub.modules.ocr_quality.candidate_models import (
    CandidateGenResult,
    CandidateState,
    Decision,
)
from doc_intelligence_hub.modules.ocr_quality.candidate_service import (
    BatchCapExceeded,
    OcrCandidateService,
    UnsupportedProvider,
    _artifact_paths,
)
from doc_intelligence_hub.modules.ocr_quality.database import (
    OcrQualityCandidate,
    get_session,
    init_db,
)

from .conftest import make_minimal_pdf_bytes


class FakeClient:
    """Duck-typed stand-in for ``PaperlessClient`` — GET-only, never a write method."""

    base_url = "https://paperless.private.invalid"

    def __init__(self, documents: dict[int, dict], previews: dict[int, bytes]):
        self.documents = documents
        self.previews = previews
        self.get_document_calls: list[int] = []
        self.get_document_preview_calls: list[int] = []

    async def get_document(self, document_id: int) -> dict:
        self.get_document_calls.append(document_id)
        return self.documents[document_id]

    async def get_document_preview(self, document_id: int) -> tuple[bytes, str]:
        self.get_document_preview_calls.append(document_id)
        return self.previews[document_id], "application/pdf"

    async def get_document_content(self, document_id: int) -> str:
        return "current live document text"

    async def aclose(self) -> None:
        pass


def _doc(doc_id: int, checksum: str = "chk-1") -> dict:
    return {"id": doc_id, "checksum": checksum, "modified": "2024-01-01T00:00:00Z"}


class TestArtifactPaths:
    def test_accepts_safe_candidate_ids(self, candidate_db):
        pdf_path, text_path = _artifact_paths("cand-123.safe_id")
        assert pdf_path.name == "cand-123.safe_id.pdf"
        assert text_path.name == "cand-123.safe_id.txt"

    @pytest.mark.parametrize("candidate_id", ["../escape", r"..\\escape", "bad/slash", "bad space"])
    def test_rejects_unsafe_candidate_ids(self, candidate_db, candidate_id):
        with pytest.raises(ValueError, match="Invalid candidate artifact id"):
            _artifact_paths(candidate_id)


@pytest.fixture()
def candidate_db(tmp_path):
    original = ocr_quality_config.settings.database_url
    ocr_quality_config.settings.database_url = f"sqlite:///{tmp_path / 'candidate_test.db'}"
    init_db()
    yield
    ocr_quality_config.settings.database_url = original


@pytest.fixture()
def service(candidate_db):
    documents = {1: _doc(1, "chk-1"), 2: _doc(2, "chk-2")}
    previews = {1: make_minimal_pdf_bytes("doc one"), 2: make_minimal_pdf_bytes("doc two")}
    client = FakeClient(documents, previews)
    return OcrCandidateService(client, get_session)


class TestRequestCandidates:
    @pytest.mark.asyncio
    async def test_creates_requested_rows_per_document_and_engine(self, service):
        candidate_ids = await service.request_candidates(
            document_ids=[1, 2], engines=["ocrmypdf-tesseract-5"]
        )
        assert len(candidate_ids) == 2

        db = get_session()
        try:
            rows = db.query(OcrQualityCandidate).all()
            assert len(rows) == 2
            assert {r.state for r in rows} == {CandidateState.REQUESTED.value}
            assert {r.document_id for r in rows} == {1, 2}
        finally:
            db.close()

        # Only GETs — never any write/update call.
        assert service.client.get_document_calls
        assert not hasattr(service.client, "update_document")

    @pytest.mark.asyncio
    async def test_empty_batch_rejected(self, service):
        with pytest.raises(ValueError):
            await service.request_candidates(document_ids=[], engines=["ocrmypdf-tesseract-5"])

    @pytest.mark.asyncio
    async def test_batch_document_cap_enforced(self, service, monkeypatch):
        monkeypatch.setattr(ocr_quality_config.settings, "candidate_max_documents_per_batch", 1)
        with pytest.raises(BatchCapExceeded):
            await service.request_candidates(document_ids=[1, 2], engines=["ocrmypdf-tesseract-5"])

    @pytest.mark.asyncio
    async def test_batch_page_cap_enforced(self, service, monkeypatch):
        monkeypatch.setattr(ocr_quality_config.settings, "candidate_max_total_pages_per_batch", 1)
        # Both fixture docs are single-page, so a 2-document batch exceeds a
        # 1-page-total cap on the second document.
        with pytest.raises(BatchCapExceeded):
            await service.request_candidates(document_ids=[1, 2], engines=["ocrmypdf-tesseract-5"])

    @pytest.mark.asyncio
    async def test_unknown_provider_rejected(self, service):
        with pytest.raises(UnsupportedProvider):
            await service.request_candidates(document_ids=[1], engines=["not-a-real-engine"])

    @pytest.mark.asyncio
    async def test_non_allowlisted_provider_rejected(self, service, monkeypatch):
        monkeypatch.setattr(
            ocr_quality_config.settings, "candidate_provider_allowlist", ["ocrmypdf-tesseract-5"]
        )
        with pytest.raises(UnsupportedProvider):
            await service.request_candidates(document_ids=[1], engines=["azure-prebuilt-layout"])


class TestRunGenerationForCandidate:
    @pytest.mark.asyncio
    async def test_successful_generation_transitions_to_ready(self, service):
        [candidate_id] = await service.request_candidates(
            document_ids=[1], engines=["ocrmypdf-tesseract-5"]
        )

        fake_gen_result = CandidateGenResult(
            success=True,
            candidate_pdf_bytes=make_minimal_pdf_bytes("candidate text"),
            candidate_text="candidate text",
            page_count=1,
            runtime_seconds=1.5,
            cost_estimate=0.0,
        )
        with patch(
            "doc_intelligence_hub.modules.ocr_quality.candidate_service.OcrMyPdfProvider"
            ".generate_candidate",
            new=AsyncMock(return_value=fake_gen_result),
        ):
            await service.run_generation_for_candidate(candidate_id)

        db = get_session()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one()
            assert row.state == CandidateState.READY.value
            assert row.comparison_id is not None
            assert row.candidate_pdf_checksum is not None
            # The persisted comparison's diff stats are computed against the
            # live document's real current text (fetched via a read-only GET),
            # not against an empty/None placeholder.
            assert row.text_diff_summary["current_char_count"] == len("current live document text")
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_failed_generation_transitions_to_failed(self, service):
        [candidate_id] = await service.request_candidates(
            document_ids=[1], engines=["ocrmypdf-tesseract-5"]
        )

        fake_gen_result = CandidateGenResult(success=False, error_message="binary not found")
        with patch(
            "doc_intelligence_hub.modules.ocr_quality.candidate_service.OcrMyPdfProvider"
            ".generate_candidate",
            new=AsyncMock(return_value=fake_gen_result),
        ):
            await service.run_generation_for_candidate(candidate_id)

        db = get_session()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one()
            assert row.state == CandidateState.FAILED.value
            assert row.failure_reason == "binary not found"
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_cancelled_candidate_is_not_overwritten(self, service):
        [candidate_id] = await service.request_candidates(
            document_ids=[1], engines=["ocrmypdf-tesseract-5"]
        )
        # Move the candidate into RUNNING, then cancel it (simulating a user
        # cancelling mid-generation) before the provider call resolves.
        db = get_session()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one()
            row.state = CandidateState.RUNNING.value
            db.commit()
        finally:
            db.close()
        service.cancel_candidate(candidate_id)

        fake_gen_result = CandidateGenResult(success=True, candidate_pdf_bytes=b"%PDF-ignored")
        with patch(
            "doc_intelligence_hub.modules.ocr_quality.candidate_service.OcrMyPdfProvider"
            ".generate_candidate",
            new=AsyncMock(return_value=fake_gen_result),
        ):
            await service.run_generation_for_candidate(candidate_id)

        db = get_session()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one()
            assert row.state == CandidateState.FAILED.value  # cancellation wins, not overwritten
        finally:
            db.close()


class TestDecideCandidate:
    @pytest.mark.asyncio
    async def _make_ready_candidate(self, service) -> str:
        [candidate_id] = await service.request_candidates(
            document_ids=[1], engines=["ocrmypdf-tesseract-5"]
        )
        fake_gen_result = CandidateGenResult(
            success=True,
            candidate_pdf_bytes=make_minimal_pdf_bytes("candidate text"),
            candidate_text="candidate text",
            page_count=1,
        )
        with patch(
            "doc_intelligence_hub.modules.ocr_quality.candidate_service.OcrMyPdfProvider"
            ".generate_candidate",
            new=AsyncMock(return_value=fake_gen_result),
        ):
            await service.run_generation_for_candidate(candidate_id)
        return candidate_id

    @pytest.mark.asyncio
    async def test_accept_records_decision_and_makes_no_paperless_write(self, service):
        candidate_id = await self._make_ready_candidate(service)

        result = await service.decide_candidate(
            candidate_id, decision=Decision.ACCEPTED, reason="looks good", actor="reviewer1"
        )

        assert result["decision"] == "accepted"
        assert result["state"] == CandidateState.ACCEPTED.value

        # The only client calls made are read-only GETs; the fake client has
        # no write/update method at all, so any attempt to call one would
        # raise AttributeError rather than silently succeeding.
        assert not hasattr(service.client, "update_document")
        assert not hasattr(service.client, "patch_document")
        assert service.client.get_document_preview_calls == [1, 1]  # request + decide re-check

    @pytest.mark.asyncio
    async def test_reject_records_decision(self, service):
        candidate_id = await self._make_ready_candidate(service)

        result = await service.decide_candidate(
            candidate_id, decision=Decision.REJECTED, reason="bad OCR", actor="reviewer1"
        )

        assert result["decision"] == "rejected"
        assert result["state"] == CandidateState.REJECTED.value

    @pytest.mark.asyncio
    async def test_accept_fails_if_source_document_changed(self, service):
        candidate_id = await self._make_ready_candidate(service)
        # Simulate the live Paperless document changing after the candidate
        # was compared — acceptance must refuse per the design doc's
        # freshness invariant.
        service.client.previews[1] = make_minimal_pdf_bytes("a different document entirely")

        with pytest.raises(ValueError, match="changed since"):
            await service.decide_candidate(
                candidate_id, decision=Decision.ACCEPTED, reason=None, actor="reviewer1"
            )

    @pytest.mark.asyncio
    async def test_cannot_decide_non_ready_candidate(self, service):
        [candidate_id] = await service.request_candidates(
            document_ids=[1], engines=["ocrmypdf-tesseract-5"]
        )
        # Still REQUESTED, never generated.
        with pytest.raises(ValueError):
            await service.decide_candidate(
                candidate_id, decision=Decision.ACCEPTED, reason=None, actor="reviewer1"
            )


class TestCancelCandidate:
    @pytest.mark.asyncio
    async def test_cancel_requested_candidate(self, service):
        [candidate_id] = await service.request_candidates(
            document_ids=[1], engines=["ocrmypdf-tesseract-5"]
        )
        result = service.cancel_candidate(candidate_id)
        assert result["state"] == CandidateState.FAILED.value

    @pytest.mark.asyncio
    async def test_cannot_cancel_ready_candidate(self, service):
        candidate_id = await TestDecideCandidate()._make_ready_candidate(service)
        with pytest.raises(ValueError):
            service.cancel_candidate(candidate_id)


class TestExpireStaleCandidates:
    @pytest.mark.asyncio
    async def test_expires_past_retention_candidates(self, service):
        [candidate_id] = await service.request_candidates(
            document_ids=[1], engines=["ocrmypdf-tesseract-5"]
        )
        db = get_session()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one()
            row.expires_at = datetime.utcnow() - timedelta(days=1)
            db.commit()
        finally:
            db.close()

        expired_count = service.expire_stale_candidates()
        assert expired_count == 1

        db = get_session()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one()
            assert row.state == CandidateState.EXPIRED.value
        finally:
            db.close()
