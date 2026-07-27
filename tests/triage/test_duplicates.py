"""Tests for triage.duplicates — signal scorers, scoring, threshold logic, and auto-detection."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from doc_intelligence_hub.modules.triage.database import (
    configure,
    create_duplicate_pair,
    get_all_triage_settings,
    get_triage_setting,
    init_db,
    set_triage_setting,
)
from doc_intelligence_hub.modules.triage.duplicates import (
    DUPLICATE_THRESHOLD,
    WEIGHTS,
    _score_amount,
    _score_content_hash,
    _score_date_of_service,
    _score_invoice_number,
    _score_provider,
    _score_title,
    _verify_primary_metadata,
    on_document_ingested,
    score_documents,
)


@pytest.fixture()
def db():
    """Create an in-memory SQLite database for each test."""
    configure("sqlite:///:memory:")
    init_db()
    yield


class TestWeightsConfig:
    def test_weights_sum_to_one(self):
        total = sum(WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, expected 1.0"

    def test_all_signals_have_weight(self):
        expected = {
            "invoice_number",
            "amount",
            "date_of_service",
            "provider",
            "title",
            "content_hash",
        }
        assert set(WEIGHTS.keys()) == expected


class TestScoreInvoiceNumber:
    def test_exact_match(self):
        assert (
            _score_invoice_number({"invoice_number": "INV-001"}, {"invoice_number": "INV-001"})
            == 1.0
        )

    def test_case_insensitive(self):
        assert (
            _score_invoice_number({"invoice_number": "inv-001"}, {"invoice_number": "INV-001"})
            == 1.0
        )

    def test_missing_both(self):
        assert _score_invoice_number({}, {}) == 0.0

    def test_missing_one(self):
        assert _score_invoice_number({"invoice_number": "X"}, {}) == 0.0

    def test_partial_match(self):
        score = _score_invoice_number({"invoice_number": "INV-001"}, {"invoice_number": "INV-002"})
        assert 0.0 < score < 1.0

    def test_claim_number_fallback(self):
        assert _score_invoice_number({"claim_number": "CLM-1"}, {"claim_number": "CLM-1"}) == 1.0


class TestScoreAmount:
    def test_exact_match(self):
        assert _score_amount({"amount": 150.00}, {"amount": 150.00}) == 1.0

    def test_close_match(self):
        # 1% tolerance → 0.95
        score = _score_amount({"amount": 100.00}, {"amount": 100.50})
        assert score >= 0.7

    def test_no_match(self):
        assert _score_amount({"amount": 100}, {"amount": 500}) == 0.0

    def test_missing_amount(self):
        assert _score_amount({"amount": 100}, {}) == 0.0

    def test_total_amount_fallback(self):
        assert _score_amount({"total_amount": 75}, {"total_amount": 75}) == 1.0

    def test_both_zero(self):
        assert _score_amount({"amount": 0}, {"amount": 0}) == 1.0


class TestScoreDateOfService:
    def test_exact_match(self):
        assert (
            _score_date_of_service(
                {"date_of_service": "2026-01-15"}, {"date_of_service": "2026-01-15"}
            )
            == 1.0
        )

    def test_one_day_apart(self):
        score = _score_date_of_service(
            {"date_of_service": "2026-01-15"}, {"date_of_service": "2026-01-16"}
        )
        assert score == 0.8

    def test_week_apart(self):
        score = _score_date_of_service(
            {"date_of_service": "2026-01-15"}, {"date_of_service": "2026-01-20"}
        )
        assert score == 0.4

    def test_far_apart(self):
        score = _score_date_of_service(
            {"date_of_service": "2026-01-15"}, {"date_of_service": "2026-06-15"}
        )
        assert score == 0.0

    def test_missing(self):
        assert _score_date_of_service({}, {}) == 0.0

    def test_different_format(self):
        # m/d/Y vs Y-m-d
        score = _score_date_of_service(
            {"date_of_service": "01/15/2026"}, {"date_of_service": "2026-01-15"}
        )
        assert score == 1.0


class TestScoreProvider:
    def test_exact_match(self):
        assert _score_provider({"provider": "Dr. Smith"}, {"provider": "Dr. Smith"}) == 1.0

    def test_case_insensitive(self):
        assert _score_provider({"provider": "dr. smith"}, {"provider": "DR. SMITH"}) == 1.0

    def test_missing(self):
        assert _score_provider({}, {}) == 0.0

    def test_provider_name_fallback(self):
        assert _score_provider({"provider_name": "Clinic"}, {"provider_name": "Clinic"}) == 1.0


class TestScoreTitle:
    def test_exact_match(self):
        assert _score_title({"title": "Electric Bill"}, {"title": "Electric Bill"}) == 1.0

    def test_similar_titles(self):
        score = _score_title(
            {"title": "Electric Bill January 2026"},
            {"title": "Electric Bill Jan 2026"},
        )
        assert score > 0.7

    def test_missing(self):
        assert _score_title({}, {}) == 0.0


class TestScoreContentHash:
    def test_exact_match(self):
        assert _score_content_hash({"content_hash": "abc123"}, {"content_hash": "abc123"}) == 1.0

    def test_different(self):
        assert _score_content_hash({"content_hash": "abc"}, {"content_hash": "xyz"}) == 0.0

    def test_missing(self):
        assert _score_content_hash({}, {}) == 0.0

    def test_checksum_fallback(self):
        assert _score_content_hash({"checksum": "abc123"}, {"checksum": "abc123"}) == 1.0


class TestScoreDocuments:
    def test_identical_documents(self):
        meta = {
            "invoice_number": "INV-001",
            "amount": 150.0,
            "date_of_service": "2026-01-15",
            "provider": "Dr. Smith",
            "title": "Medical Bill",
            "content_hash": "abc123",
        }
        score, breakdown = score_documents(meta, meta)
        assert score == 1.0
        assert all(v == 1.0 for v in breakdown.values())

    def test_completely_different(self):
        meta_a = {"title": "Electric Bill"}
        meta_b = {"title": "Insurance Card"}
        score, breakdown = score_documents(meta_a, meta_b)
        assert score < DUPLICATE_THRESHOLD

    def test_above_threshold(self):
        meta_a = {
            "invoice_number": "INV-001",
            "amount": 150.0,
            "date_of_service": "2026-01-15",
            "provider": "Dr. Smith",
        }
        meta_b = {
            "invoice_number": "INV-001",
            "amount": 150.0,
            "date_of_service": "2026-01-15",
            "provider": "Dr. Smith",
        }
        score, _ = score_documents(meta_a, meta_b)
        assert score >= DUPLICATE_THRESHOLD

    def test_breakdown_has_all_signals(self):
        _, breakdown = score_documents({}, {})
        assert set(breakdown.keys()) == set(WEIGHTS.keys())


class TestCreateDuplicatePairValidation:
    def test_rejects_self_referencing_pair(self):
        """create_duplicate_pair must reject pairs where both IDs are the same document."""
        with pytest.raises(ValueError, match="same"):
            create_duplicate_pair(
                doc_a_id=9679,
                doc_b_id=9679,
                similarity_score=1.0,
                breakdown={"invoice_number": 1.0},
            )


class TestTriageSettings:
    """Tests for the triage_settings table and CRUD helpers."""

    def test_get_default_when_unset(self, db):
        assert get_triage_setting("nonexistent") is None
        assert get_triage_setting("nonexistent", "fallback") == "fallback"

    def test_set_and_get(self, db):
        set_triage_setting("duplicate_auto_detect", "true")
        assert get_triage_setting("duplicate_auto_detect") == "true"

    def test_update_existing(self, db):
        set_triage_setting("duplicate_auto_detect", "true")
        set_triage_setting("duplicate_auto_detect", "false")
        assert get_triage_setting("duplicate_auto_detect") == "false"

    def test_get_all(self, db):
        set_triage_setting("duplicate_auto_detect", "true")
        set_triage_setting("other_key", "42")
        all_settings = get_all_triage_settings()
        assert all_settings == {"duplicate_auto_detect": "true", "other_key": "42"}


class TestOnDocumentIngested:
    """Tests for the auto-detection ingestion hook."""

    def test_skips_when_disabled(self, db):
        result = on_document_ingested(123)
        assert result["skipped"] is True
        assert result["reason"] == "auto_detect_disabled"

    def test_skips_when_explicitly_false(self, db):
        set_triage_setting("duplicate_auto_detect", "false")
        result = on_document_ingested(123)
        assert result["skipped"] is True

    @patch("doc_intelligence_hub.modules.triage.duplicates.detect_duplicates", return_value=[])
    @patch("doc_intelligence_hub.modules.triage.duplicates.get_document_metadata", return_value={"document_id": 123, "title": "Test"})
    def test_runs_when_enabled_no_matches(self, mock_meta, mock_detect, db):
        set_triage_setting("duplicate_auto_detect", "true")
        result = on_document_ingested(123)
        assert result["skipped"] is False
        assert result["pairs_created"] == 0
        mock_detect.assert_called_once_with(123)

    @patch("doc_intelligence_hub.modules.triage.duplicates.detect_duplicates")
    @patch("doc_intelligence_hub.modules.triage.duplicates.get_document_metadata")
    def test_creates_pairs_when_matches_found(self, mock_meta, mock_detect, db):
        mock_meta.return_value = {"document_id": 1, "title": "Test", "provider": "Dr. A"}
        mock_detect.return_value = [
            {
                "doc_id": 2,
                "similarity_score": 0.85,
                "breakdown": {"invoice_number": 1.0},
                "metadata": {"provider": "Dr. A"},
            }
        ]
        set_triage_setting("duplicate_auto_detect", "true")
        result = on_document_ingested(1)
        assert result["skipped"] is False
        assert result["pairs_created"] == 1
        assert result["triage_items_created"] == 1


class TestVerifyPrimaryMetadata:
    """Tests for the _verify_primary_metadata defensive check."""

    @patch("doc_intelligence_hub.modules.triage.duplicates.get_document_metadata")
    def test_no_warning_when_unchanged(self, mock_meta, caplog):
        pre = {"title": "Bill", "amount": 100, "provider": "Dr. X"}
        mock_meta.return_value = {"title": "Bill", "amount": 100, "provider": "Dr. X"}
        import logging
        with caplog.at_level(logging.INFO):
            _verify_primary_metadata(1, pre)
        assert "verified unchanged" in caplog.text

    @patch("doc_intelligence_hub.modules.triage.duplicates.get_document_metadata")
    def test_warns_when_changed(self, mock_meta, caplog):
        pre = {"title": "Bill", "amount": 100, "provider": "Dr. X"}
        mock_meta.return_value = {"title": "Different Title", "amount": 100, "provider": "Dr. X"}
        import logging
        with caplog.at_level(logging.WARNING):
            _verify_primary_metadata(1, pre)
        assert "title" in caplog.text
        assert "changed during merge" in caplog.text

    def test_skips_when_no_pre_metadata(self, caplog):
        import logging
        with caplog.at_level(logging.DEBUG):
            _verify_primary_metadata(1, None)
        # Should not raise

    @patch("doc_intelligence_hub.modules.triage.duplicates.get_document_metadata", return_value=None)
    def test_warns_when_post_metadata_unavailable(self, mock_meta, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            _verify_primary_metadata(1, {"title": "Test"})
        assert "could not be verified" in caplog.text
