from __future__ import annotations

import re

from doc_intelligence_hub.modules.eob_matching.models import ClassificationResult, DocumentType

INSURANCE_COMPANIES = [
    "UnitedHealthcare",
    "Aetna",
    "Blue Cross Blue Shield",
    "BCBS",
    "Kaiser",
    "Kaiser Permanente",
    "Cigna",
    "Humana",
    "Anthem",
    "Molina",
    "Centene",
    "Oscar",
    "Medicare",
    "Medicaid",
]

_EOB_PATTERNS: list[tuple[str, int, re.Pattern[str] | None, str | None]] = [
    ("explanation_of_benefits", 50, re.compile(r"\bexplanation of benefits\b", re.IGNORECASE), None),
    ("not_a_bill_disclaimer", 40, re.compile(r"\bthis is not a bill\b", re.IGNORECASE), None),
    ("insurance_company", 30, None, None),
    ("plan_pays", 20, re.compile(r"\b(?:amount your plan pays|plan pays)\b", re.IGNORECASE), None),
    (
        "service_date_table",
        15,
        re.compile(
            r"(?:service date|date of service).{0,80}(?:billed|allowed|plan pays|patient responsibility)",
            re.IGNORECASE | re.DOTALL,
        ),
        None,
    ),
]

_BILL_PATTERNS: list[tuple[str, int, re.Pattern[str] | None]] = [
    ("invoice", 40, re.compile(r"\binvoice\b", re.IGNORECASE)),
    ("amount_due", 40, re.compile(r"\bamount due\b", re.IGNORECASE)),
    ("balance", 25, re.compile(r"\bbalance(?: due)?\b", re.IGNORECASE)),
    ("remit_payment", 30, re.compile(r"\bplease remit payment\b", re.IGNORECASE)),
    (
        "due_date",
        25,
        re.compile(
            r"\bdue date\b\s*[:#-]?\s*(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})",
            re.IGNORECASE,
        ),
    ),
    ("account_number", 20, re.compile(r"\baccount\s+number\b\s*[:#-]?\s*[\w-]+", re.IGNORECASE)),
]

_THRESHOLD = 60


def classify_document(text: str) -> ClassificationResult:
    if not text or not text.strip():
        return ClassificationResult(type=DocumentType.UNKNOWN, confidence_score=0.0, indicators_matched=[])

    eob_score, eob_indicators = _score_eob(text)
    bill_score, bill_indicators = _score_bill(text)

    if eob_score > bill_score and eob_score >= _THRESHOLD:
        return ClassificationResult(
            type=DocumentType.EOB,
            confidence_score=float(min(eob_score, 100)),
            indicators_matched=eob_indicators,
        )

    if bill_score > eob_score and bill_score >= _THRESHOLD:
        return ClassificationResult(
            type=DocumentType.BILL,
            confidence_score=float(min(bill_score, 100)),
            indicators_matched=bill_indicators,
        )

    indicators = eob_indicators + [indicator for indicator in bill_indicators if indicator not in eob_indicators]
    return ClassificationResult(
        type=DocumentType.UNKNOWN,
        confidence_score=float(min(max(eob_score, bill_score), 100)),
        indicators_matched=indicators,
    )


def _score_eob(text: str) -> tuple[int, list[str]]:
    score = 0
    indicators: list[str] = []

    for name, points, pattern, _ in _EOB_PATTERNS:
        if name == "insurance_company":
            company = _find_insurance_company(text)
            if company:
                score += points
                indicators.append(f"insurance_company:{company}")
            continue
        if pattern and pattern.search(text):
            score += points
            indicators.append(name)

    return score, indicators


def _score_bill(text: str) -> tuple[int, list[str]]:
    score = 0
    indicators: list[str] = []
    for name, points, pattern in _BILL_PATTERNS:
        if pattern and pattern.search(text):
            score += points
            indicators.append(name)
    return score, indicators


def _find_insurance_company(text: str) -> str | None:
    lowered = text.lower()
    for company in INSURANCE_COMPANIES:
        if company.lower() in lowered:
            return company
    return None
