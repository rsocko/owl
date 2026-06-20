from __future__ import annotations

from doc_intelligence_hub.modules.eob_matching.classifier import classify_document
from doc_intelligence_hub.modules.eob_matching.models import DocumentType


def test_classifies_eob_document() -> None:
    text = """
    UnitedHealthcare
    Explanation of Benefits
    This is not a bill.
    Date of Service: 01/15/2024
    Amount Your Plan Pays: $144.00
    """

    result = classify_document(text)

    assert result.type is DocumentType.EOB
    assert result.confidence_score >= 60
    assert "explanation_of_benefits" in result.indicators_matched


def test_classifies_bill_document() -> None:
    text = """
    City Medical Center
    Invoice Number: INV-1001
    Amount Due: $36.00
    Balance Due: $36.00
    Due Date: 02/10/2024
    Please remit payment to the address below.
    """

    result = classify_document(text)

    assert result.type is DocumentType.BILL
    assert result.confidence_score >= 60
    assert "amount_due" in result.indicators_matched


def test_classifies_unknown_document() -> None:
    text = """
    Grocery List
    Milk
    Eggs
    Bread
    """

    result = classify_document(text)

    assert result.type is DocumentType.UNKNOWN
    assert result.confidence_score == 0


def test_classifier_handles_edge_cases() -> None:
    assert classify_document("").type is DocumentType.UNKNOWN

    ambiguous = """
    Explanation of benefits summary
    Invoice reference for claim follow-up
    """
    result = classify_document(ambiguous)

    assert result.type is DocumentType.UNKNOWN
    assert result.confidence_score < 60
