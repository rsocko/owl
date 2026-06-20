from __future__ import annotations

import re
from datetime import date

from dateutil import parser

from doc_intelligence_hub.modules.eob_matching.classifier import INSURANCE_COMPANIES
from doc_intelligence_hub.modules.eob_matching.models import ExtractedBill, ExtractedEOB, ServiceLine

_AMOUNT_CAPTURE = r"\$?\s*([\d,]+(?:\.\d{2})?)"
_DATE_CAPTURE = r"([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})"
_LINE_AMOUNT_PATTERN = re.compile(r"\$\s*[\d,]+(?:\.\d{2})?|\b\d[\d,]*\.\d{2}\b")


def extract_eob(text: str, document_id: str) -> ExtractedEOB:
    return ExtractedEOB(
        insurance_company=_extract_insurance_company(text),
        policy_number=_extract_labeled_value(text, ["policy number", "member id", "policy #"]),
        patient_name=_extract_labeled_value(text, ["patient name", "patient"]),
        claim_number=_extract_labeled_value(text, ["claim number", "claim #"]),
        date_of_service=_extract_labeled_date(text, ["date of service", "service date", "dos"]),
        provider_name=_extract_labeled_value(text, ["provider name", "provider", "facility", "rendering provider"]),
        services=_extract_services(text, eob=True),
        total_billed=_extract_labeled_amount(text, ["total billed", "amount billed", "billed amount"]),
        total_allowed=_extract_labeled_amount(text, ["total allowed", "allowed amount"]),
        total_plan_pays=_extract_labeled_amount(text, ["amount your plan pays", "plan pays", "total plan pays"]),
        total_patient_responsibility=_extract_labeled_amount(
            text,
            ["patient responsibility", "your responsibility", "member responsibility", "you owe"],
        ),
        document_id=document_id,
    )


def extract_bill(text: str, document_id: str) -> ExtractedBill:
    return ExtractedBill(
        provider_name=_extract_labeled_value(text, ["provider", "provider name", "from"]),
        patient_name=_extract_labeled_value(text, ["patient name", "patient", "bill to"]),
        invoice_number=_extract_labeled_value(text, ["invoice number", "invoice #", "account number"]),
        date_of_service=_extract_labeled_date(text, ["date of service", "service date", "dos"]),
        due_date=_extract_labeled_date(text, ["due date", "payment due"]),
        services=_extract_services(text, eob=False),
        total_amount=_extract_labeled_amount(text, ["total amount", "total charges", "invoice total"]),
        balance_due=_extract_labeled_amount(text, ["balance due", "amount due", "current balance"]),
        payment_status=_extract_payment_status(text),
        document_id=document_id,
    )


def parse_amount(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = value.strip().replace("$", "").replace(",", "")
    if not cleaned:
        return None
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        parsed = parser.parse(value, fuzzy=True)
    except (ValueError, TypeError, OverflowError):
        return None
    return parsed.date()


def _extract_labeled_value(text: str, labels: list[str]) -> str | None:
    for label in labels:
        pattern = re.compile(rf"\b{re.escape(label)}\b\s*[:#-]?\s*([^\n\r]+)", re.IGNORECASE)
        match = pattern.search(text)
        if match:
            value = _clean_value(match.group(1))
            if value:
                return value
    return None


def _extract_labeled_amount(text: str, labels: list[str]) -> float | None:
    for label in labels:
        pattern = re.compile(rf"\b{re.escape(label)}\b\s*[:#-]?\s*{_AMOUNT_CAPTURE}", re.IGNORECASE)
        match = pattern.search(text)
        if match:
            return parse_amount(match.group(1))
    return None


def _extract_labeled_date(text: str, labels: list[str]) -> date | None:
    for label in labels:
        pattern = re.compile(rf"\b{re.escape(label)}\b\s*[:#-]?\s*{_DATE_CAPTURE}", re.IGNORECASE)
        match = pattern.search(text)
        if match:
            parsed = parse_date(match.group(1))
            if parsed:
                return parsed
    return None


def _extract_insurance_company(text: str) -> str | None:
    lowered = text.lower()
    for company in INSURANCE_COMPANIES:
        if company.lower() in lowered:
            return company
    return None


def _extract_services(text: str, *, eob: bool) -> list[ServiceLine]:
    services: list[ServiceLine] = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.strip().split())
        if not line:
            continue

        code_match = re.search(r"\b(\d{5})\b", line)
        if not code_match:
            continue

        amounts = [parse_amount(match.group(0)) for match in _LINE_AMOUNT_PATTERN.finditer(line)]
        amounts = [amount for amount in amounts if amount is not None]
        description = re.sub(_AMOUNT_CAPTURE, "", line)
        description = re.sub(r"\b\d{5}\b", "", description)
        description = _clean_value(description) or "Service"

        if eob:
            billed, allowed, plan_pays, patient_resp = (amounts + [None, None, None, None])[:4]
            services.append(
                ServiceLine(
                    description=description,
                    cpt_code=code_match.group(1),
                    billed_amount=billed,
                    allowed_amount=allowed,
                    plan_pays=plan_pays,
                    patient_responsibility=patient_resp,
                )
            )
            continue

        amount = amounts[-1] if amounts else None
        services.append(ServiceLine(description=description, cpt_code=code_match.group(1), amount=amount))

    return services


def _extract_payment_status(text: str) -> str | None:
    lowered = text.lower()
    if "paid in full" in lowered:
        return "PAID"
    if "past due" in lowered:
        return "PAST_DUE"
    if "balance due" in lowered or "amount due" in lowered:
        return "DUE"
    return None


def _clean_value(value: str) -> str | None:
    cleaned = value.strip(" \t:-")
    cleaned = re.split(r"\s{2,}", cleaned)[0].strip()
    cleaned = cleaned.rstrip(".,;")
    return cleaned or None
