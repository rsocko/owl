from __future__ import annotations

import re
from datetime import date

from rapidfuzz import fuzz

from doc_intelligence_hub.modules.eob_matching.models import ExtractedBill, ExtractedEOB, MatchBreakdown, MatchConfidence, MatchResult

_PROVIDER_REPLACEMENTS = {
    "medical center": "med ctr",
    "medical": "med",
    "center": "ctr",
    "hospital": "hosp",
    "clinic": "clnc",
    "doctor": "dr",
    "physician": "phys",
    "associates": "assoc",
}


def match_documents(eobs: list[ExtractedEOB], bills: list[ExtractedBill]) -> list[MatchResult]:
    matches: list[MatchResult] = []
    for eob in eobs:
        for bill in bills:
            breakdown = MatchBreakdown(
                date=round(score_date_similarity(eob.date_of_service, bill.date_of_service), 2),
                provider=round(score_provider_similarity(eob.provider_name, bill.provider_name), 2),
                patient=round(score_patient_similarity(eob.patient_name, bill.patient_name), 2),
                amount=round(score_amount_similarity(eob.total_patient_responsibility, _bill_amount(bill)), 2),
                procedures=round(score_procedure_overlap(eob, bill), 2),
            )
            score = round(
                (
                    breakdown.date * 0.30
                    + breakdown.provider * 0.25
                    + breakdown.patient * 0.20
                    + breakdown.amount * 0.15
                    + breakdown.procedures * 0.10
                ),
                2,
            )
            if score < 50:
                continue
            matches.append(
                MatchResult(
                    eob_id=eob.document_id,
                    bill_id=bill.document_id,
                    score=score,
                    confidence=_confidence_for_score(score),
                    breakdown=breakdown,
                )
            )

    matches.sort(key=lambda item: (-item.score, item.eob_id, item.bill_id))
    return matches


def score_date_similarity(eob_date: date | None, bill_date: date | None) -> float:
    if not eob_date or not bill_date:
        return 0.0

    days_diff = abs((eob_date - bill_date).days)
    if days_diff == 0:
        return 100.0
    if days_diff <= 7:
        return 100.0 - (days_diff * (20.0 / 7.0))
    if days_diff <= 14:
        return 80.0 - ((days_diff - 7) * (20.0 / 7.0))
    if days_diff <= 30:
        return 60.0 - ((days_diff - 14) * (20.0 / 16.0))
    return max(0.0, 40.0 - ((days_diff - 30) * 0.5))


def score_provider_similarity(eob_provider: str | None, bill_provider: str | None) -> float:
    return _score_name_similarity(eob_provider, bill_provider, provider_mode=True)


def score_patient_similarity(eob_patient: str | None, bill_patient: str | None) -> float:
    return _score_name_similarity(eob_patient, bill_patient, provider_mode=False)


def score_amount_similarity(eob_patient_responsibility: float | None, bill_amount: float | None) -> float:
    if not eob_patient_responsibility or not bill_amount:
        return 0.0

    diff_percent = abs(eob_patient_responsibility - bill_amount) / eob_patient_responsibility
    if diff_percent == 0:
        return 100.0
    if diff_percent <= 0.05:
        return 90.0
    if diff_percent <= 0.10:
        return 75.0
    if diff_percent <= 0.20:
        return 50.0
    return 0.0


def score_procedure_overlap(eob: ExtractedEOB, bill: ExtractedBill) -> float:
    eob_codes = {service.cpt_code for service in eob.services if service.cpt_code}
    bill_codes = {service.cpt_code for service in bill.services if service.cpt_code}
    if not eob_codes or not bill_codes:
        return 0.0

    overlap = eob_codes.intersection(bill_codes)
    union = eob_codes.union(bill_codes)
    return (len(overlap) / len(union)) * 100.0


def _score_name_similarity(left: str | None, right: str | None, *, provider_mode: bool) -> float:
    if not left or not right:
        return 0.0

    left_clean = _normalize_name(left, provider_mode=provider_mode)
    right_clean = _normalize_name(right, provider_mode=provider_mode)
    if not left_clean or not right_clean:
        return 0.0

    return float(
        max(
            fuzz.ratio(left_clean, right_clean),
            fuzz.token_sort_ratio(left_clean, right_clean),
            fuzz.partial_ratio(left_clean, right_clean),
        )
    )


def _normalize_name(value: str, *, provider_mode: bool) -> str:
    normalized = value.lower()
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    if provider_mode:
        for old, new in _PROVIDER_REPLACEMENTS.items():
            normalized = normalized.replace(old, new)
    normalized = re.sub(r"\b(?:inc|llc|pllc|pc|md)\b", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _bill_amount(bill: ExtractedBill) -> float | None:
    return bill.balance_due if bill.balance_due is not None else bill.total_amount


def _confidence_for_score(score: float) -> MatchConfidence:
    if score >= 85:
        return MatchConfidence.HIGH
    if score >= 70:
        return MatchConfidence.MEDIUM
    return MatchConfidence.LOW
