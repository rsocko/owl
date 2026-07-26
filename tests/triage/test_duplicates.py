"""Tests for triage.duplicates — signal scorers, scoring, threshold logic."""

from __future__ import annotations


from doc_intelligence_hub.modules.triage.duplicates import (
    DUPLICATE_THRESHOLD,
    WEIGHTS,
    _score_amount,
    _score_content_hash,
    _score_date_of_service,
    _score_invoice_number,
    _score_provider,
    _score_title,
    score_documents,
)


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
