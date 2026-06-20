from __future__ import annotations

from datetime import date

from doc_intelligence_hub.modules.eob_matching.extractor import extract_bill, extract_eob, parse_amount, parse_date


def test_extracts_eob_fields() -> None:
    text = """
    UnitedHealthcare
    Explanation of Benefits
    Patient Name: John Doe
    Policy Number: ABC123456789
    Claim Number: 2024010123456
    Date of Service: 01/15/2024
    Provider Name: City Medical Center
    Office Visit Established Patient 99213 $250.00 $180.00 $144.00 $36.00
    Total Billed: $250.00
    Total Allowed: $180.00
    Amount Your Plan Pays: $144.00
    Patient Responsibility: $36.00
    """

    result = extract_eob(text, "eob-1")

    assert result.document_id == "eob-1"
    assert result.insurance_company == "UnitedHealthcare"
    assert result.patient_name == "John Doe"
    assert result.policy_number == "ABC123456789"
    assert result.claim_number == "2024010123456"
    assert result.date_of_service == date(2024, 1, 15)
    assert result.provider_name == "City Medical Center"
    assert result.total_billed == 250.0
    assert result.total_allowed == 180.0
    assert result.total_plan_pays == 144.0
    assert result.total_patient_responsibility == 36.0
    assert len(result.services) == 1
    assert result.services[0].cpt_code == "99213"
    assert result.services[0].patient_responsibility == 36.0


def test_extracts_bill_fields() -> None:
    text = """
    Provider: City Med Ctr
    Patient Name: John Doe
    Invoice Number: INV-1001
    Date of Service: 2024-01-15
    Due Date: February 10, 2024
    Office Visit 99213 $36.00
    Total Amount: $250.00
    Balance Due: $36.00
    """

    result = extract_bill(text, "bill-1")

    assert result.document_id == "bill-1"
    assert result.provider_name == "City Med Ctr"
    assert result.patient_name == "John Doe"
    assert result.invoice_number == "INV-1001"
    assert result.date_of_service == date(2024, 1, 15)
    assert result.due_date == date(2024, 2, 10)
    assert result.total_amount == 250.0
    assert result.balance_due == 36.0
    assert result.payment_status == "DUE"
    assert len(result.services) == 1
    assert result.services[0].amount == 36.0


def test_parse_date_supports_multiple_formats() -> None:
    assert parse_date("01/15/2024") == date(2024, 1, 15)
    assert parse_date("2024-01-15") == date(2024, 1, 15)
    assert parse_date("February 10, 2024") == date(2024, 2, 10)
    assert parse_date("not-a-date") is None


def test_parse_amount_normalizes_currency() -> None:
    assert parse_amount("$1,234.56") == 1234.56
    assert parse_amount("36.00") == 36.0
    assert parse_amount("") is None
