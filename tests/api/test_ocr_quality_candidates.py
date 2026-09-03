"""API tests for OCR candidate generation/comparison/staging endpoints (issue #18, slice 1).

The single most important assertion in this file: accepting or rejecting a
candidate never calls a Paperless write method (``update_document``,
``update_custom_field(s)``, ``create_custom_field``) — see
``test_decide_candidate_makes_zero_paperless_write_calls``.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from doc_intelligence_hub.modules.ocr_quality import config as ocr_quality_config
from doc_intelligence_hub.modules.ocr_quality.candidate_models import (
    CandidateGenResult,
    CandidateState,
)
from doc_intelligence_hub.modules.ocr_quality.database import (
    OcrQualityCandidate,
    get_session,
    init_db,
)

from ..ocr_quality.conftest import make_minimal_pdf_bytes


@pytest.fixture()
def ocr_candidates_db(tmp_path):
    original = ocr_quality_config.settings.database_url
    ocr_quality_config.settings.database_url = f"sqlite:///{tmp_path / 'api_test_candidates.db'}"
    init_db()
    yield
    ocr_quality_config.settings.database_url = original


@pytest.fixture(autouse=True)
def _fast_generation(ocr_candidates_db):
    """Patch OCRmyPDF generation so background-task dispatch in tests is
    instant and deterministic, instead of depending on a real binary.
    """
    fake_result = CandidateGenResult(
        success=True,
        candidate_pdf_bytes=make_minimal_pdf_bytes("candidate text"),
        candidate_text="candidate text",
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


class TestRequestCandidates:
    def test_request_candidates_returns_202_with_ids(
        self, client, ocr_candidates_db, mock_paperless
    ):
        _configure_preview(mock_paperless)
        resp = client.post(
            "/api/ocr-quality/candidates",
            json={"document_ids": [1], "engines": ["ocrmypdf-tesseract-5"]},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["count"] == 1
        assert len(body["candidate_ids"]) == 1

    def test_request_candidates_runs_generation_in_background(
        self, client, ocr_candidates_db, mock_paperless
    ):
        _configure_preview(mock_paperless)
        resp = client.post(
            "/api/ocr-quality/candidates",
            json={"document_ids": [1], "engines": ["ocrmypdf-tesseract-5"]},
        )
        candidate_id = resp.json()["candidate_ids"][0]

        # TestClient runs BackgroundTasks synchronously after the response,
        # so by the time we check, generation (mocked to succeed instantly)
        # should already have progressed the candidate to READY.
        db = get_session()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one()
            assert row.state == CandidateState.READY.value
        finally:
            db.close()

    def test_empty_document_ids_rejected(self, client, ocr_candidates_db, mock_paperless):
        resp = client.post("/api/ocr-quality/candidates", json={"document_ids": []})
        assert resp.status_code == 422

    def test_batch_document_cap_returns_422(self, client, ocr_candidates_db, mock_paperless):
        ocr_quality_config.settings.candidate_max_documents_per_batch = 1
        try:
            _configure_preview(mock_paperless)
            resp = client.post(
                "/api/ocr-quality/candidates",
                json={"document_ids": [1, 2], "engines": ["ocrmypdf-tesseract-5"]},
            )
            assert resp.status_code == 422
            assert resp.json()["error"]["code"] == "batch_invalid"
        finally:
            ocr_quality_config.settings.candidate_max_documents_per_batch = 5

    def test_unknown_provider_returns_422(self, client, ocr_candidates_db, mock_paperless):
        _configure_preview(mock_paperless)
        resp = client.post(
            "/api/ocr-quality/candidates",
            json={"document_ids": [1], "engines": ["not-a-real-engine"]},
        )
        assert resp.status_code == 422

    def test_no_accept_all_endpoint_exists(self, client, ocr_candidates_db, mock_paperless):
        """Per the design doc, there is no bulk/"accept all" action anywhere.

        There is no dedicated "accept all" route: POSTing to a path shaped
        like one either 404s or 405s (matches an existing single-candidate
        GET route instead), and treating "all" as a literal candidate_id
        correctly fails with "unknown candidate" rather than doing anything.
        """
        resp = client.post("/api/ocr-quality/candidates/accept-all")
        assert resp.status_code in (404, 405)

        resp = client.post(
            "/api/ocr-quality/candidates/all/decision",
            json={"decision": "accepted", "actor": "reviewer1"},
        )
        assert resp.status_code == 400
        assert "Unknown candidate" in resp.json()["error"]["message"]


class TestListAndGetCandidates:
    def test_list_candidates_empty(self, client, ocr_candidates_db, mock_paperless):
        resp = client.get("/api/ocr-quality/candidates")
        assert resp.status_code == 200
        assert resp.json()["candidates"] == []

    def test_list_and_get_candidate_after_generation(
        self, client, ocr_candidates_db, mock_paperless
    ):
        _configure_preview(mock_paperless)
        create_resp = client.post(
            "/api/ocr-quality/candidates",
            json={"document_ids": [1], "engines": ["ocrmypdf-tesseract-5"]},
        )
        candidate_id = create_resp.json()["candidate_ids"][0]

        list_resp = client.get("/api/ocr-quality/candidates", params={"document_id": 1})
        assert list_resp.status_code == 200
        assert len(list_resp.json()["candidates"]) == 1

        detail_resp = client.get(f"/api/ocr-quality/candidates/{candidate_id}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["state"] == CandidateState.READY.value
        assert detail["comparison"] is not None

    def test_get_unknown_candidate_404(self, client, ocr_candidates_db, mock_paperless):
        resp = client.get("/api/ocr-quality/candidates/does-not-exist")
        assert resp.status_code == 404


class TestCandidateText:
    def test_get_candidate_text_returns_current_and_candidate_side_by_side(
        self, client, ocr_candidates_db, mock_paperless
    ):
        _configure_preview(mock_paperless)
        mock_paperless.get_document_content.return_value = "current live document text"
        create_resp = client.post(
            "/api/ocr-quality/candidates",
            json={"document_ids": [1], "engines": ["ocrmypdf-tesseract-5"]},
        )
        candidate_id = create_resp.json()["candidate_ids"][0]

        resp = client.get(f"/api/ocr-quality/candidates/{candidate_id}/text")
        assert resp.status_code == 200
        body = resp.json()
        assert body["current_text"] == "current live document text"
        assert body["candidate_text"] == "candidate text"
        # Read-only: fetching text never issues a Paperless write.
        mock_paperless.update_document.assert_not_called()
        mock_paperless.update_custom_field.assert_not_called()
        mock_paperless.update_custom_fields.assert_not_called()

    def test_get_candidate_text_unknown_candidate_404(
        self, client, ocr_candidates_db, mock_paperless
    ):
        resp = client.get("/api/ocr-quality/candidates/does-not-exist/text")
        assert resp.status_code == 404


class TestDecideCandidate:
    def _create_ready_candidate(self, client, mock_paperless) -> str:
        _configure_preview(mock_paperless)
        resp = client.post(
            "/api/ocr-quality/candidates",
            json={"document_ids": [1], "engines": ["ocrmypdf-tesseract-5"]},
        )
        return resp.json()["candidate_ids"][0]

    def test_accept_candidate(self, client, ocr_candidates_db, mock_paperless):
        candidate_id = self._create_ready_candidate(client, mock_paperless)
        resp = client.post(
            f"/api/ocr-quality/candidates/{candidate_id}/decision",
            json={"decision": "accepted", "actor": "reviewer1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["decision"] == "accepted"
        # decide_candidate only transitions to APPLYING and returns
        # immediately; the actual Paperless write is a background task
        # (application_service.py) that eventually moves it to ACCEPTED.
        assert body["state"] == CandidateState.APPLYING.value

    def test_reject_candidate(self, client, ocr_candidates_db, mock_paperless):
        candidate_id = self._create_ready_candidate(client, mock_paperless)
        resp = client.post(
            f"/api/ocr-quality/candidates/{candidate_id}/decision",
            json={"decision": "rejected", "reason": "bad text", "actor": "reviewer1"},
        )
        assert resp.status_code == 200
        assert resp.json()["decision"] == "rejected"

    def test_decide_candidate_requires_a_non_blank_actor(
        self, client, ocr_candidates_db, mock_paperless
    ):
        """Gap 1: actor is no longer optional/defaulted to "system" — a
        missing or blank actor must be rejected clearly rather than silently
        attributed to a placeholder.
        """
        candidate_id = self._create_ready_candidate(client, mock_paperless)

        missing_resp = client.post(
            f"/api/ocr-quality/candidates/{candidate_id}/decision",
            json={"decision": "accepted"},
        )
        assert missing_resp.status_code == 422

        blank_resp = client.post(
            f"/api/ocr-quality/candidates/{candidate_id}/decision",
            json={"decision": "accepted", "actor": "   "},
        )
        assert blank_resp.status_code == 422

        # The candidate must still be untouched/READY — a rejected request
        # never reaches decide_candidate.
        db = get_session()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one()
            assert row.state == CandidateState.READY.value
        finally:
            db.close()

    def test_decide_candidate_makes_zero_paperless_write_calls(
        self, client, ocr_candidates_db, mock_paperless
    ):
        """The critical safety invariant for this slice: accepting/rejecting
        a candidate must NEVER write to Paperless. This asserts none of
        PaperlessClient's known write methods are ever invoked, across the
        full request -> generate -> accept -> reject lifecycle.
        """
        candidate_id = self._create_ready_candidate(client, mock_paperless)

        accept_resp = client.post(
            f"/api/ocr-quality/candidates/{candidate_id}/decision",
            json={"decision": "accepted", "actor": "reviewer1"},
        )
        assert accept_resp.status_code == 200

        [candidate_id_2] = client.post(
            "/api/ocr-quality/candidates",
            json={"document_ids": [1], "engines": ["ocrmypdf-tesseract-5"]},
        ).json()["candidate_ids"]
        reject_resp = client.post(
            f"/api/ocr-quality/candidates/{candidate_id_2}/decision",
            json={"decision": "rejected", "actor": "reviewer1"},
        )
        assert reject_resp.status_code == 200

        mock_paperless.update_document.assert_not_called()
        mock_paperless.update_custom_field.assert_not_called()
        mock_paperless.update_custom_fields.assert_not_called()
        mock_paperless.create_custom_field.assert_not_called()

    def test_accept_fails_when_document_changed_since_comparison(
        self, client, ocr_candidates_db, mock_paperless
    ):
        candidate_id = self._create_ready_candidate(client, mock_paperless)
        # Live document changes (different checksum) after generation.
        mock_paperless.get_document_preview.return_value = (
            make_minimal_pdf_bytes("a totally different document"),
            "application/pdf",
        )
        resp = client.post(
            f"/api/ocr-quality/candidates/{candidate_id}/decision",
            json={"decision": "accepted", "actor": "reviewer1"},
        )
        assert resp.status_code == 400

    def test_decide_unknown_candidate_400(self, client, ocr_candidates_db, mock_paperless):
        resp = client.post(
            "/api/ocr-quality/candidates/does-not-exist/decision",
            json={"decision": "accepted", "actor": "reviewer1"},
        )
        assert resp.status_code == 400


class TestCancelCandidate:
    def test_cancel_requested_candidate(self, client, ocr_candidates_db, mock_paperless):
        # Patch generation to hang so the candidate stays REQUESTED/RUNNING
        # long enough to be cancellable within this synchronous test — here
        # we instead directly seed a REQUESTED row to keep the test simple
        # and avoid timing dependence on BackgroundTasks execution order.
        db = get_session()
        try:
            db.add(
                OcrQualityCandidate(
                    candidate_id="cand-cancel-1",
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

        resp = client.post("/api/ocr-quality/candidates/cand-cancel-1/cancel")
        assert resp.status_code == 200
        assert resp.json()["state"] == CandidateState.FAILED.value

    def test_cancel_unknown_candidate_400(self, client, ocr_candidates_db, mock_paperless):
        resp = client.post("/api/ocr-quality/candidates/does-not-exist/cancel")
        assert resp.status_code == 400


class TestRollbackEndpoint:
    def test_rollback_requires_a_non_blank_actor(self, client, ocr_candidates_db, mock_paperless):
        missing_resp = client.post("/api/ocr-quality/documents/1/rollback", json={})
        assert missing_resp.status_code == 422

        blank_resp = client.post("/api/ocr-quality/documents/1/rollback", json={"actor": "  "})
        assert blank_resp.status_code == 422

    def test_rollback_with_no_resolvable_target_returns_400(
        self, client, ocr_candidates_db, mock_paperless
    ):
        """With actor validated, an unresolvable target (no prior accepted
        candidate and no root version in the mock's version list) surfaces
        as a clean 400 rather than a raw 500 — the router's existing
        error-translation path for ``rollback``'s ``{"error": ...}`` result.
        """
        mock_paperless.list_document_versions.return_value = []
        resp = client.post("/api/ocr-quality/documents/1/rollback", json={"actor": "reviewer1"})
        assert resp.status_code == 400
        assert "rollback" in resp.json()["error"]["message"].lower()


class TestRetryInvalidationEndpoint:
    def test_retry_invalidation_requires_a_non_blank_actor(
        self, client, ocr_candidates_db, mock_paperless
    ):
        missing_resp = client.post(
            "/api/ocr-quality/candidates/does-not-exist/retry-invalidation", json={}
        )
        assert missing_resp.status_code == 422

        blank_resp = client.post(
            "/api/ocr-quality/candidates/does-not-exist/retry-invalidation",
            json={"actor": "   "},
        )
        assert blank_resp.status_code == 422

    def test_retry_invalidation_unknown_candidate_400(
        self, client, ocr_candidates_db, mock_paperless
    ):
        resp = client.post(
            "/api/ocr-quality/candidates/does-not-exist/retry-invalidation",
            json={"actor": "reviewer1"},
        )
        assert resp.status_code == 400
        assert "Unknown candidate" in resp.json()["error"]["message"]


class TestCalibrationSummaryEndpoint:
    """API tests for the issue #167 calibration measurement endpoint."""

    def _seed_decided_candidate(
        self,
        *,
        candidate_id: str,
        decision: str,
        content_score_delta: float | None = None,
        overlay_score_delta: float | None = None,
        blocking_findings: list[str] | None = None,
    ) -> None:
        db = get_session()
        try:
            db.add(
                OcrQualityCandidate(
                    candidate_id=candidate_id,
                    document_id=1,
                    source_checksum="chk-1",
                    state=decision,
                    engine="ocrmypdf-tesseract-5",
                    model_version="test",
                    blocking_findings=blocking_findings,
                    content_score_delta=content_score_delta,
                    overlay_score_delta=overlay_score_delta,
                    decision=decision,
                    decided_at=datetime.utcnow(),
                    expires_at=datetime.utcnow(),
                )
            )
            db.commit()
        finally:
            db.close()

    def test_no_decided_candidates_returns_null_rates(
        self, client, ocr_candidates_db, mock_paperless
    ):
        resp = client.get("/api/ocr-quality/calibration/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["decided_count"] == 0
        assert body["agreement_rate"] is None
        assert body["uncertain_count"] == 0

    def test_reports_agreement_and_disagreement_counts(
        self, client, ocr_candidates_db, mock_paperless
    ):
        self._seed_decided_candidate(
            candidate_id="cand-agree",
            decision="accepted",
            content_score_delta=10.0,
        )
        self._seed_decided_candidate(
            candidate_id="cand-fp",
            decision="rejected",
            content_score_delta=10.0,
        )
        self._seed_decided_candidate(
            candidate_id="cand-uncertain",
            decision="accepted",
        )

        resp = client.get("/api/ocr-quality/calibration/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["decided_count"] == 3
        assert body["agreement_count"] == 1
        assert body["false_positive_count"] == 1
        assert body["uncertain_count"] == 1
        assert body["uncertain_accepted_count"] == 1
