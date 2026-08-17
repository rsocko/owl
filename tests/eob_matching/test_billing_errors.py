"""Tests for billing error detection (ARCH-09)."""

from __future__ import annotations

from doc_intelligence_hub.modules.eob_matching.billing_errors import (
    analyze_match_for_errors,
    detect_billing_errors,
)
from doc_intelligence_hub.modules.eob_matching.models import (
    BillingErrorType,
    ExtractedBill,
    ExtractedEOB,
    MatchBreakdown,
    MatchConfidence,
    MatchResult,
    ServiceLine,
)


def _make_eob(**kwargs) -> ExtractedEOB:
    defaults = {"document_id": "100", "extraction_confidence": 0.9}
    return ExtractedEOB(**{**defaults, **kwargs})


def _make_bill(**kwargs) -> ExtractedBill:
    defaults = {"document_id": "200", "extraction_confidence": 0.9}
    return ExtractedBill(**{**defaults, **kwargs})


def _make_match() -> MatchResult:
    return MatchResult(
        eob_id="100",
        bill_id="200",
        score=85.0,
        confidence=MatchConfidence.HIGH,
        breakdown=MatchBreakdown(date=1.0, provider=0.9, patient=0.9, amount=0.8, procedures=0.7),
    )


class TestBillingErrorDetection:
    """Test detect_billing_errors for various error types."""

    def test_no_error_when_amounts_match(self):
        """No error when patient responsibility matches bill balance."""
        eob = _make_eob(total_patient_responsibility=150.00)
        bill = _make_bill(balance_due=150.00)
        error_type, details = detect_billing_errors(eob, bill, _make_match())
        assert error_type is None
        assert details is None

    def test_amount_mismatch_detected(self):
        """Detects amount mismatch when >10% discrepancy."""
        eob = _make_eob(total_patient_responsibility=100.00)
        bill = _make_bill(balance_due=150.00)
        error_type, details = detect_billing_errors(eob, bill, _make_match())
        assert error_type == BillingErrorType.AMOUNT_MISMATCH
        assert "$50.00" in details

    def test_balance_billing_detected(self):
        """Detects balance billing when bill exceeds allowed amount."""
        eob = _make_eob(total_allowed=200.00, total_patient_responsibility=50.00)
        bill = _make_bill(total_amount=350.00, balance_due=50.00)
        error_type, details = detect_billing_errors(eob, bill, _make_match())
        assert error_type == BillingErrorType.BALANCE_BILLING
        assert "exceeds" in details

    def test_coverage_denied_detected(self):
        """Detects coverage denied when plan pays $0."""
        eob = _make_eob(total_billed=500.00, total_plan_pays=0.0)
        bill = _make_bill(balance_due=500.00)
        error_type, details = detect_billing_errors(eob, bill, _make_match())
        assert error_type == BillingErrorType.COVERAGE_DENIED
        assert "denied" in details

    def test_duplicate_charge_detected(self):
        """Detects duplicate CPT codes in services."""
        services = [
            ServiceLine(cpt_code="99213", description="Office visit"),
            ServiceLine(cpt_code="99213", description="Office visit duplicate"),
        ]
        eob = _make_eob(services=services, total_patient_responsibility=100.00)
        bill = _make_bill(services=services, balance_due=100.00)
        error_type, details = detect_billing_errors(eob, bill, _make_match())
        assert error_type == BillingErrorType.DUPLICATE_CHARGE
        assert "99213" in details

    def test_no_error_small_amount_difference(self):
        """Small amount differences (<10%) don't trigger error."""
        eob = _make_eob(total_patient_responsibility=100.00)
        bill = _make_bill(balance_due=105.00)
        error_type, details = detect_billing_errors(eob, bill, _make_match())
        assert error_type is None

    def test_analyze_match_sets_fields(self):
        """analyze_match_for_errors sets error_type and error_details on match."""
        eob = _make_eob(total_patient_responsibility=100.00)
        bill = _make_bill(balance_due=200.00)
        match = _make_match()
        result = analyze_match_for_errors(eob, bill, match)
        assert result.error_type == BillingErrorType.AMOUNT_MISMATCH
        assert result.error_details is not None


class TestBillingErrorTypeEnum:
    """Test BillingErrorType enum values."""

    def test_all_values_are_strings(self):
        """All enum members are valid string values."""
        for member in BillingErrorType:
            assert isinstance(member.value, str)
            assert len(member.value) > 0

    def test_enum_has_expected_members(self):
        """Enum contains all expected billing error types."""
        expected = {
            "duplicate_charge",
            "coding_error",
            "coverage_denied",
            "amount_mismatch",
            "unbundling",
            "upcoding",
            "balance_billing",
            "out_of_network",
            "pre_auth_missing",
            "timely_filing",
            "other",
        }
        actual = {member.value for member in BillingErrorType}
        assert actual == expected
