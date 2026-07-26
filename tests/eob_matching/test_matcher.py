from __future__ import annotations

from datetime import date

from doc_intelligence_hub.modules.eob_matching.matcher import match_documents
from doc_intelligence_hub.modules.eob_matching.models import (
    ExtractedBill,
    ExtractedEOB,
    MatchConfidence,
    ServiceLine,
)


def _make_eob(
    document_id: str,
    *,
    provider: str,
    patient: str,
    service_date: date,
    responsibility: float,
    cpt_code: str = "99213",
) -> ExtractedEOB:
    return ExtractedEOB(
        insurance_company="UnitedHealthcare",
        patient_name=patient,
        provider_name=provider,
        date_of_service=service_date,
        total_patient_responsibility=responsibility,
        services=[
            ServiceLine(
                description="Visit", cpt_code=cpt_code, patient_responsibility=responsibility
            )
        ],
        document_id=document_id,
    )


def _make_bill(
    document_id: str,
    *,
    provider: str,
    patient: str,
    service_date: date,
    balance_due: float,
    cpt_code: str = "99213",
) -> ExtractedBill:
    return ExtractedBill(
        provider_name=provider,
        patient_name=patient,
        date_of_service=service_date,
        balance_due=balance_due,
        services=[ServiceLine(description="Visit", cpt_code=cpt_code, amount=balance_due)],
        document_id=document_id,
    )


def test_perfect_match_is_high_confidence() -> None:
    eob = _make_eob(
        "eob-1",
        provider="City Medical Center",
        patient="John Doe",
        service_date=date(2024, 1, 15),
        responsibility=36.0,
    )
    bill = _make_bill(
        "bill-1",
        provider="City Medical Center",
        patient="John Doe",
        service_date=date(2024, 1, 15),
        balance_due=36.0,
    )

    matches = match_documents([eob], [bill])

    assert len(matches) == 1
    assert matches[0].confidence is MatchConfidence.HIGH
    assert matches[0].score >= 85


def test_partial_match_is_medium_confidence() -> None:
    eob = _make_eob(
        "eob-1",
        provider="City Medical Center",
        patient="John Doe",
        service_date=date(2024, 1, 15),
        responsibility=36.0,
    )
    bill = _make_bill(
        "bill-1",
        provider="City Med Ctr",
        patient="Jon Doe",
        service_date=date(2024, 1, 18),
        balance_due=42.0,
        cpt_code="99214",
    )

    matches = match_documents([eob], [bill])

    assert len(matches) == 1
    assert matches[0].confidence is MatchConfidence.MEDIUM
    assert 70 <= matches[0].score < 85


def test_non_match_is_not_returned() -> None:
    eob = _make_eob(
        "eob-1",
        provider="City Medical Center",
        patient="John Doe",
        service_date=date(2024, 1, 15),
        responsibility=36.0,
    )
    bill = _make_bill(
        "bill-1",
        provider="Other Provider",
        patient="Jane Smith",
        service_date=date(2024, 3, 20),
        balance_due=500.0,
        cpt_code="80050",
    )

    matches = match_documents([eob], [bill])

    assert matches == []


def test_one_eob_can_match_many_bills() -> None:
    eob = _make_eob(
        "eob-1",
        provider="City Medical Center",
        patient="John Doe",
        service_date=date(2024, 1, 15),
        responsibility=36.0,
    )
    primary_bill = _make_bill(
        "bill-1",
        provider="City Medical Center",
        patient="John Doe",
        service_date=date(2024, 1, 15),
        balance_due=36.0,
    )
    secondary_bill = _make_bill(
        "bill-2",
        provider="City Med Ctr Outpatient",
        patient="John Doe",
        service_date=date(2024, 1, 16),
        balance_due=34.5,
    )

    matches = match_documents([eob], [primary_bill, secondary_bill])

    assert [match.bill_id for match in matches] == ["bill-1", "bill-2"]
    assert all(match.eob_id == "eob-1" for match in matches)
