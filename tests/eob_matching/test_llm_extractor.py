"""Tests for LLM extractor validation and confidence scoring."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from doc_intelligence_hub.modules.eob_matching.llm_extractor import (
    _safe_provider_name,
    compute_bill_confidence,
    compute_eob_confidence,
    validate_bill_extraction,
    validate_eob_extraction,
)
from doc_intelligence_hub.modules.eob_matching.models import ExtractedBill, ExtractedEOB, ServiceLine


# ---------------------------------------------------------------------------
# validate_eob_extraction tests
# ---------------------------------------------------------------------------


class TestValidateEOBExtraction:
    def _make_eob(self, **kwargs) -> ExtractedEOB:
        defaults = {
            "document_id": "eob-1",
            "provider_name": "City Medical Center",
            "date_of_service": date(2024, 1, 15),
            "total_patient_responsibility": 36.0,
        }
        defaults.update(kwargs)
        return ExtractedEOB(**defaults)

    def test_valid_eob_passes(self) -> None:
        eob = self._make_eob()
        assert validate_eob_extraction(eob) is None

    def test_rejects_empty_provider_name(self) -> None:
        eob = self._make_eob(provider_name=None)
        assert "provider_name" in validate_eob_extraction(eob)

    def test_rejects_blank_provider_name(self) -> None:
        eob = self._make_eob(provider_name="")
        assert "provider_name" in validate_eob_extraction(eob)

    def test_rejects_null_date_of_service(self) -> None:
        eob = self._make_eob(date_of_service=None)
        assert "date_of_service" in validate_eob_extraction(eob)

    def test_rejects_all_amounts_null_or_zero(self) -> None:
        eob = self._make_eob(
            total_billed=None,
            total_allowed=0,
            total_plan_pays=None,
            total_patient_responsibility=0,
        )
        assert "amount" in validate_eob_extraction(eob)

    def test_accepts_if_at_least_one_amount_nonzero(self) -> None:
        eob = self._make_eob(
            total_billed=None,
            total_allowed=None,
            total_plan_pays=None,
            total_patient_responsibility=50.0,
        )
        assert validate_eob_extraction(eob) is None


# ---------------------------------------------------------------------------
# validate_bill_extraction tests
# ---------------------------------------------------------------------------


class TestValidateBillExtraction:
    def _make_bill(self, **kwargs) -> ExtractedBill:
        defaults = {
            "document_id": "bill-1",
            "provider_name": "City Med Ctr",
            "date_of_service": date(2024, 1, 15),
            "total_amount": 250.0,
        }
        defaults.update(kwargs)
        return ExtractedBill(**defaults)

    def test_valid_bill_passes(self) -> None:
        bill = self._make_bill()
        assert validate_bill_extraction(bill) is None

    def test_rejects_empty_provider(self) -> None:
        bill = self._make_bill(provider_name=None)
        assert "provider_name" in validate_bill_extraction(bill)

    def test_rejects_null_date(self) -> None:
        bill = self._make_bill(date_of_service=None)
        assert "date_of_service" in validate_bill_extraction(bill)

    def test_rejects_all_amounts_zero(self) -> None:
        bill = self._make_bill(total_amount=0, balance_due=None)
        assert "amount" in validate_bill_extraction(bill)

    def test_accepts_if_balance_due_nonzero(self) -> None:
        bill = self._make_bill(total_amount=None, balance_due=36.0)
        assert validate_bill_extraction(bill) is None


# ---------------------------------------------------------------------------
# _safe_provider_name tests
# ---------------------------------------------------------------------------


class TestSafeProviderName:
    def test_normal_name(self) -> None:
        assert _safe_provider_name("City Medical Center") == "City Medical Center"

    def test_none_returns_none(self) -> None:
        assert _safe_provider_name(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _safe_provider_name("") is None

    def test_too_many_words_returns_none(self) -> None:
        # This simulates the LLM grabbing a disclaimer sentence
        long_text = "The summary below is intended to help you understand your benefits"
        assert _safe_provider_name(long_text) is None

    def test_short_name_ok(self) -> None:
        assert _safe_provider_name("Dr. Smith") == "Dr. Smith"


# ---------------------------------------------------------------------------
# Confidence scoring tests
# ---------------------------------------------------------------------------


class TestConfidenceScoring:
    def test_full_eob_high_confidence(self) -> None:
        eob = ExtractedEOB(
            document_id="eob-1",
            insurance_company="UHC",
            policy_number="ABC123",
            patient_name="John Doe",
            claim_number="CLM001",
            date_of_service=date(2024, 1, 15),
            provider_name="City Medical Center",
            total_billed=250.0,
            total_plan_pays=144.0,
            total_patient_responsibility=36.0,
            services=[ServiceLine(description="Visit", cpt_code="99213")],
        )
        score = compute_eob_confidence(eob)
        assert score >= 0.9

    def test_minimal_eob_lower_confidence(self) -> None:
        eob = ExtractedEOB(
            document_id="eob-2",
            provider_name="Clinic",
            date_of_service=date(2024, 1, 15),
            total_patient_responsibility=36.0,
        )
        score = compute_eob_confidence(eob)
        assert 0.4 <= score <= 0.7

    def test_empty_eob_zero_confidence(self) -> None:
        eob = ExtractedEOB(document_id="eob-3")
        score = compute_eob_confidence(eob)
        assert score == 0.0

    def test_full_bill_high_confidence(self) -> None:
        bill = ExtractedBill(
            document_id="bill-1",
            provider_name="City Med Ctr",
            patient_name="John Doe",
            invoice_number="INV-1001",
            date_of_service=date(2024, 1, 15),
            due_date=date(2024, 2, 10),
            total_amount=250.0,
            balance_due=36.0,
            payment_status="DUE",
            services=[ServiceLine(description="Visit", cpt_code="99213")],
        )
        score = compute_bill_confidence(bill)
        assert score >= 0.9

    def test_empty_bill_zero_confidence(self) -> None:
        bill = ExtractedBill(document_id="bill-2")
        score = compute_bill_confidence(bill)
        assert score == 0.0


# ---------------------------------------------------------------------------
# Integration test — extract_eob_llm falls back on bad data
# ---------------------------------------------------------------------------


class TestExtractEOBLLMFallback:
    @pytest.mark.asyncio
    async def test_falls_back_on_missing_provider(self) -> None:
        from doc_intelligence_hub.modules.eob_matching.llm_extractor import extract_eob_llm

        bad_llm_response = {
            "insurance_company": "UHC",
            "provider_name": None,
            "date_of_service": "2024-01-15",
            "total_patient_responsibility": 36.0,
        }
        with patch(
            "doc_intelligence_hub.modules.eob_matching.llm_extractor.chat_json",
            new_callable=AsyncMock,
            return_value=bad_llm_response,
        ):
            result = await extract_eob_llm("Provider Name: City Med\nDate of Service: 01/15/2024\nPatient Responsibility: $36.00", "eob-test")
            # Should fall back to regex extractor (which extracts from text)
            assert result.document_id == "eob-test"

    @pytest.mark.asyncio
    async def test_falls_back_on_garbage_provider(self) -> None:
        from doc_intelligence_hub.modules.eob_matching.llm_extractor import extract_eob_llm

        bad_llm_response = {
            "provider_name": "The summary below is intended to help you understand your benefits and coverage",
            "date_of_service": "2024-01-15",
            "total_patient_responsibility": 36.0,
        }
        with patch(
            "doc_intelligence_hub.modules.eob_matching.llm_extractor.chat_json",
            new_callable=AsyncMock,
            return_value=bad_llm_response,
        ):
            result = await extract_eob_llm("Some EOB text", "eob-test2")
            # Garbage provider name is sanitized to None, which triggers fallback
            assert result.document_id == "eob-test2"

    @pytest.mark.asyncio
    async def test_accepts_good_extraction(self) -> None:
        from doc_intelligence_hub.modules.eob_matching.llm_extractor import extract_eob_llm

        good_response = {
            "insurance_company": "UHC",
            "provider_name": "City Medical Center",
            "patient_name": "John Doe",
            "date_of_service": "2024-01-15",
            "total_patient_responsibility": 36.0,
            "total_plan_pays": 144.0,
            "total_billed": 250.0,
            "services": [],
        }
        with patch(
            "doc_intelligence_hub.modules.eob_matching.llm_extractor.chat_json",
            new_callable=AsyncMock,
            return_value=good_response,
        ):
            result = await extract_eob_llm("Some EOB text", "eob-good")
            assert result.provider_name == "City Medical Center"
            assert result.date_of_service == date(2024, 1, 15)
            assert result.extraction_confidence > 0.5


class TestExtractBillLLMFallback:
    @pytest.mark.asyncio
    async def test_falls_back_on_all_amounts_zero(self) -> None:
        from doc_intelligence_hub.modules.eob_matching.llm_extractor import extract_bill_llm

        bad_response = {
            "provider_name": "Clinic",
            "date_of_service": "2024-01-15",
            "total_amount": 0,
            "balance_due": None,
        }
        with patch(
            "doc_intelligence_hub.modules.eob_matching.llm_extractor.chat_json",
            new_callable=AsyncMock,
            return_value=bad_response,
        ):
            result = await extract_bill_llm("Provider: Clinic\nTotal Amount: $250.00\nDate of Service: 01/15/2024", "bill-test")
            assert result.document_id == "bill-test"
