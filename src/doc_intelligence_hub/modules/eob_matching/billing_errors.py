"""Billing error detection — identifies common billing errors from EOB/Bill comparisons.

Populates the `error_type` field on MatchResult during matching or post-analysis.
"""

from __future__ import annotations

from doc_intelligence_hub.modules.eob_matching.models import (
    BillingErrorType,
    ExtractedBill,
    ExtractedEOB,
    MatchResult,
)


def detect_billing_errors(
    eob: ExtractedEOB,
    bill: ExtractedBill,
    match: MatchResult,
) -> tuple[BillingErrorType | None, str | None]:
    """Analyze an EOB/Bill match pair for billing errors.

    Returns:
        Tuple of (error_type, error_details) or (None, None) if no error detected.
    """
    # Amount mismatch: patient responsibility on EOB doesn't match bill balance
    if eob.total_patient_responsibility is not None and bill.balance_due is not None:
        diff = abs(eob.total_patient_responsibility - bill.balance_due)
        if diff > 0.01 and bill.balance_due > 0:
            ratio = diff / bill.balance_due
            if ratio > 0.1:  # >10% discrepancy
                return (
                    BillingErrorType.AMOUNT_MISMATCH,
                    f"EOB patient responsibility (${eob.total_patient_responsibility:.2f}) "
                    f"differs from bill balance (${bill.balance_due:.2f}) by ${diff:.2f}",
                )

    # Balance billing: bill total exceeds allowed amount
    if eob.total_allowed is not None and bill.total_amount is not None:
        if bill.total_amount > eob.total_allowed and eob.total_allowed > 0:
            excess = bill.total_amount - eob.total_allowed
            if excess > 1.0:
                return (
                    BillingErrorType.BALANCE_BILLING,
                    f"Bill total (${bill.total_amount:.2f}) exceeds EOB allowed "
                    f"amount (${eob.total_allowed:.2f}) by ${excess:.2f}",
                )

    # Coverage denied: EOB shows zero plan payment but bill has charges
    if (
        eob.total_plan_pays is not None
        and eob.total_plan_pays == 0
        and eob.total_billed is not None
        and eob.total_billed > 0
    ):
        return (
            BillingErrorType.COVERAGE_DENIED,
            f"Plan paid $0.00 on ${eob.total_billed:.2f} billed — coverage may have been denied",
        )

    # Duplicate charge: same service lines appear multiple times
    if eob.services and bill.services:
        eob_cpts = [s.cpt_code for s in eob.services if s.cpt_code]
        if eob_cpts:
            seen = set()
            duplicates = []
            for cpt in eob_cpts:
                if cpt in seen:
                    duplicates.append(cpt)
                seen.add(cpt)
            if duplicates:
                return (
                    BillingErrorType.DUPLICATE_CHARGE,
                    f"Duplicate CPT codes found: {', '.join(duplicates)}",
                )

    return (None, None)


def analyze_match_for_errors(
    eob: ExtractedEOB,
    bill: ExtractedBill,
    match: MatchResult,
) -> MatchResult:
    """Analyze a match pair and set error_type/error_details if errors are found."""
    error_type, error_details = detect_billing_errors(eob, bill, match)
    if error_type is not None:
        match.error_type = error_type
        match.error_details = error_details
    return match
