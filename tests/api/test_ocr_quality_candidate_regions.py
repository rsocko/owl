"""API tests for candidate-scoped region-inspection endpoints (issue #134 x #18).

Mirrors ``TestGetDocumentRegions``/``TestGetDocumentPageImage`` in
``test_ocr_quality.py``, but sourced from a candidate's stored PDF artifact
instead of a Paperless fetch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from doc_intelligence_hub.modules.ocr_quality import config as ocr_quality_config
from doc_intelligence_hub.modules.ocr_quality.candidate_models import CandidateGenResult

from ..ocr_quality.conftest import make_minimal_pdf_bytes


@pytest.fixture()
def ocr_candidates_db(tmp_path):
    from doc_intelligence_hub.modules.ocr_quality.database import init_db

    original = ocr_quality_config.settings.database_url
    ocr_quality_config.settings.database_url = (
        f"sqlite:///{tmp_path / 'api_test_candidate_regions.db'}"
    )
    init_db()
    yield
    ocr_quality_config.settings.database_url = original


@pytest.fixture()
def candidate_storage(tmp_path):
    original = ocr_quality_config.settings.candidate_storage_dir
    ocr_quality_config.settings.candidate_storage_dir = str(tmp_path / "candidate_artifacts")
    yield
    ocr_quality_config.settings.candidate_storage_dir = original


@pytest.fixture(autouse=True)
def _fast_generation(ocr_candidates_db, candidate_storage):
    """Patch OCRmyPDF generation to return a real, parseable PDF instantly."""
    fake_result = CandidateGenResult(
        success=True,
        candidate_pdf_bytes=make_minimal_pdf_bytes("Candidate word placement"),
        candidate_text="Candidate word placement",
        page_count=1,
        runtime_seconds=0.1,
        cost_estimate=0.0,
    )
    with patch(
        "doc_intelligence_hub.modules.ocr_quality.candidate_service.OcrMyPdfProvider.generate_candidate",
        new=AsyncMock(return_value=fake_result),
    ):
        yield


def _configure_preview(mock_paperless, document_id: int = 1, text: str = "current doc") -> None:
    mock_paperless.get_document.return_value = {
        "id": document_id,
        "checksum": f"chk-{document_id}",
        "modified": "2024-01-01T00:00:00Z",
    }
    mock_paperless.get_document_preview.return_value = (
        make_minimal_pdf_bytes(text),
        "application/pdf",
    )


def _create_ready_candidate(client, mock_paperless) -> str:
    _configure_preview(mock_paperless)
    resp = client.post(
        "/api/ocr-quality/candidates",
        json={"document_ids": [1], "engines": ["ocrmypdf-tesseract-5"]},
    )
    assert resp.status_code == 202
    return resp.json()["candidate_ids"][0]


class TestGetCandidateRegions:
    def test_returns_word_geometry_for_page(self, client, mock_paperless):
        candidate_id = _create_ready_candidate(client, mock_paperless)
        resp = client.get(f"/api/ocr-quality/candidates/{candidate_id}/regions?page=1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["page"] == 1
        assert body["page_count"] == 1
        texts = [w["text"] for w in body["words"]]
        assert "Candidate" in texts
        # No candidate-level scorer `reasons` exist in this slice.
        for word in body["words"]:
            assert word["matched_reasons"] == []

    def test_unknown_candidate_returns_404(self, client, mock_paperless):
        resp = client.get("/api/ocr-quality/candidates/does-not-exist/regions?page=1")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    def test_candidate_without_pdf_yet_returns_404(self, client, mock_paperless):
        from datetime import datetime

        from doc_intelligence_hub.modules.ocr_quality.candidate_models import CandidateState
        from doc_intelligence_hub.modules.ocr_quality.database import (
            OcrQualityCandidate,
            get_session,
        )

        db = get_session()
        try:
            db.add(
                OcrQualityCandidate(
                    candidate_id="cand-no-pdf",
                    document_id=1,
                    source_checksum="chk-1",
                    state=CandidateState.REQUESTED.value,
                    engine="ocrmypdf-tesseract-5",
                    model_version="test",
                    expires_at=datetime.utcnow(),
                )
            )
            db.commit()
        finally:
            db.close()

        resp = client.get("/api/ocr-quality/candidates/cand-no-pdf/regions?page=1")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "no_pdf_artifact"

    def test_unknown_page_returns_404(self, client, mock_paperless):
        candidate_id = _create_ready_candidate(client, mock_paperless)
        resp = client.get(f"/api/ocr-quality/candidates/{candidate_id}/regions?page=5")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "page_not_found"

    def test_never_calls_paperless_preview_for_the_candidate_itself(self, client, mock_paperless):
        candidate_id = _create_ready_candidate(client, mock_paperless)
        mock_paperless.get_document_preview.reset_mock()
        resp = client.get(f"/api/ocr-quality/candidates/{candidate_id}/regions?page=1")
        assert resp.status_code == 200
        mock_paperless.get_document_preview.assert_not_called()


class TestGetCandidatePageImage:
    def test_returns_png_image(self, client, mock_paperless):
        candidate_id = _create_ready_candidate(client, mock_paperless)
        resp = client.get(f"/api/ocr-quality/candidates/{candidate_id}/pages/1/image")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_unknown_candidate_returns_404(self, client, mock_paperless):
        resp = client.get("/api/ocr-quality/candidates/does-not-exist/pages/1/image")
        assert resp.status_code == 404

    def test_unknown_page_returns_404(self, client, mock_paperless):
        candidate_id = _create_ready_candidate(client, mock_paperless)
        resp = client.get(f"/api/ocr-quality/candidates/{candidate_id}/pages/9/image")
        assert resp.status_code == 404

    def test_invalid_dpi_rejected(self, client, mock_paperless):
        candidate_id = _create_ready_candidate(client, mock_paperless)
        resp = client.get(f"/api/ocr-quality/candidates/{candidate_id}/pages/1/image?dpi=5000")
        assert resp.status_code == 422
