"""Tests for :class:`OcrCalibrationService` and its API endpoint (issue #167).

Measures agreement between the deterministic accept/reject "lean"
(``comparison.classify_deterministic_lean``) and actual recorded human
decisions on ``OcrQualityCandidate`` rows. No Paperless client involved —
this only ever reads OWL's own candidate table.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from doc_intelligence_hub.modules.ocr_quality import config as ocr_quality_config
from doc_intelligence_hub.modules.ocr_quality.calibration_service import OcrCalibrationService
from doc_intelligence_hub.modules.ocr_quality.database import (
    OcrQualityCandidate,
    get_session,
    init_db,
)


@pytest.fixture()
def calibration_db(tmp_path):
    original = ocr_quality_config.settings.database_url
    ocr_quality_config.settings.database_url = f"sqlite:///{tmp_path / 'calibration_test.db'}"
    init_db()
    yield
    ocr_quality_config.settings.database_url = original


def _make_candidate(
    db,
    *,
    candidate_id: str,
    decision: str | None,
    blocking_findings: list[str] | None = None,
    overlay_score_delta: float | None = None,
    content_score_delta: float | None = None,
) -> None:
    now = datetime.utcnow()
    row = OcrQualityCandidate(
        candidate_id=candidate_id,
        document_id=1,
        source_checksum="chk-1",
        state="ready" if decision is None else decision,
        engine="ocrmypdf-tesseract-5",
        model_version="1",
        settings={},
        blocking_findings=blocking_findings,
        overlay_score_delta=overlay_score_delta,
        content_score_delta=content_score_delta,
        decision=decision,
        decided_at=now if decision else None,
        expires_at=now + timedelta(days=30),
    )
    db.add(row)
    db.commit()


@pytest.fixture()
def service(calibration_db):
    return OcrCalibrationService(get_session)


class TestGetSummaryNoData:
    def test_no_decided_candidates_returns_zero_counts_and_null_rates(self, service):
        summary = service.get_summary()

        assert summary["decided_count"] == 0
        assert summary["agreement_count"] == 0
        assert summary["agreement_rate"] is None
        assert summary["false_positive_count"] == 0
        assert summary["false_positive_rate"] is None
        assert summary["false_negative_count"] == 0
        assert summary["false_negative_rate"] is None
        assert summary["uncertain_count"] == 0
        assert summary["uncertain_rate"] is None
        assert summary["uncertain_accepted_count"] == 0
        assert summary["uncertain_rejected_count"] == 0

    def test_ignores_undecided_candidates(self, service, calibration_db):
        db = get_session()
        try:
            _make_candidate(db, candidate_id="c-undecided", decision=None, content_score_delta=10.0)
        finally:
            db.close()

        summary = service.get_summary()
        assert summary["decided_count"] == 0


class TestGetSummaryMixed:
    @pytest.fixture(autouse=True)
    def _seed(self, calibration_db):
        db = get_session()
        try:
            # True positive: lean favors_accept, human accepted -> agreement
            _make_candidate(
                db,
                candidate_id="c-tp",
                decision="accepted",
                content_score_delta=10.0,
                overlay_score_delta=0.0,
            )
            # False positive: lean favors_accept, human rejected
            _make_candidate(
                db,
                candidate_id="c-fp",
                decision="rejected",
                content_score_delta=8.0,
                overlay_score_delta=0.0,
            )
            # True negative: lean favors_reject (blocking finding), human rejected -> agreement
            _make_candidate(
                db,
                candidate_id="c-tn",
                decision="rejected",
                blocking_findings=["pages_missing"],
            )
            # False negative: lean favors_reject (overlay decline), human accepted anyway
            _make_candidate(
                db,
                candidate_id="c-fn",
                decision="accepted",
                overlay_score_delta=-9.0,
            )
            # Uncertain, human accepted
            _make_candidate(
                db,
                candidate_id="c-uncertain-accepted",
                decision="accepted",
                overlay_score_delta=0.0,
                content_score_delta=0.0,
            )
            # Uncertain, human rejected
            _make_candidate(
                db,
                candidate_id="c-uncertain-rejected",
                decision="rejected",
                overlay_score_delta=None,
                content_score_delta=None,
            )
        finally:
            db.close()

    def test_computes_counts_and_rates(self, service):
        summary = service.get_summary()

        assert summary["decided_count"] == 6
        assert summary["agreement_count"] == 2  # c-tp, c-tn
        assert summary["agreement_rate"] == pytest.approx(2 / 6, abs=1e-3)
        assert summary["false_positive_count"] == 1  # c-fp
        assert summary["false_positive_rate"] == pytest.approx(1 / 6, abs=1e-3)
        assert summary["false_negative_count"] == 1  # c-fn
        assert summary["false_negative_rate"] == pytest.approx(1 / 6, abs=1e-3)
        assert summary["uncertain_count"] == 2
        assert summary["uncertain_rate"] == pytest.approx(2 / 6, abs=1e-3)
        assert summary["uncertain_accepted_count"] == 1
        assert summary["uncertain_rejected_count"] == 1
