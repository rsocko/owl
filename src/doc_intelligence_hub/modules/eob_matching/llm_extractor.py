"""LLM-based document extraction — replaces fragile regex parsing.

Uses structured LLM output via Bifrost to extract EOB and bill fields from
document text. Falls back to the legacy regex extractor if LLM is unavailable.
"""

from __future__ import annotations

import logging
from datetime import date

from doc_intelligence_hub.core.llm import chat_json
from doc_intelligence_hub.modules.eob_matching.extractor import (
    extract_bill as regex_extract_bill,
    extract_eob as regex_extract_eob,
    parse_amount,
    parse_date,
)
from doc_intelligence_hub.modules.eob_matching.models import ExtractedBill, ExtractedEOB, ServiceLine

logger = logging.getLogger(__name__)

_EOB_SYSTEM = """You are a medical document data extraction assistant.
Extract structured data from Explanation of Benefits (EOB) documents.
Return ONLY valid JSON — no markdown, no explanation."""

_EOB_PROMPT = """Extract the following fields from this EOB document text.
Return a JSON object with exactly these fields (use null for fields you cannot find):

{{
  "insurance_company": "Name of the insurance company",
  "policy_number": "Policy or member ID number",
  "patient_name": "Patient full name",
  "claim_number": "Claim number",
  "date_of_service": "YYYY-MM-DD format",
  "provider_name": "Healthcare provider/facility name",
  "services": [
    {{
      "description": "Service description",
      "cpt_code": "5-digit CPT code or null",
      "billed_amount": 0.00,
      "allowed_amount": 0.00,
      "plan_pays": 0.00,
      "patient_responsibility": 0.00
    }}
  ],
  "total_billed": 0.00,
  "total_allowed": 0.00,
  "total_plan_pays": 0.00,
  "total_patient_responsibility": 0.00
}}

Document text:
---
{text}
---"""

_BILL_SYSTEM = """You are a medical document data extraction assistant.
Extract structured data from medical bills and invoices.
Return ONLY valid JSON — no markdown, no explanation."""

_BILL_PROMPT = """Extract the following fields from this medical bill/invoice.
Return a JSON object with exactly these fields (use null for fields you cannot find):

{{
  "provider_name": "Healthcare provider/facility name",
  "patient_name": "Patient full name",
  "invoice_number": "Invoice or account number",
  "date_of_service": "YYYY-MM-DD format",
  "due_date": "YYYY-MM-DD format",
  "services": [
    {{
      "description": "Service description",
      "cpt_code": "5-digit CPT code or null",
      "amount": 0.00
    }}
  ],
  "total_amount": 0.00,
  "balance_due": 0.00,
  "payment_status": "PAID|PAST_DUE|DUE or null"
}}

Document text:
---
{text}
---"""


async def extract_eob_llm(text: str, document_id: str, *, model: str | None = None) -> ExtractedEOB:
    """Extract EOB fields using LLM. Falls back to regex on failure."""
    prompt = _EOB_PROMPT.format(text=_truncate(text))
    data = await chat_json(prompt, system=_EOB_SYSTEM, model=model, max_tokens=1536)

    if data is None:
        logger.info("LLM extraction failed for EOB %s, falling back to regex", document_id)
        return regex_extract_eob(text, document_id)

    services = []
    for svc in data.get("services") or []:
        services.append(ServiceLine(
            description=svc.get("description", "Service"),
            cpt_code=svc.get("cpt_code"),
            billed_amount=_safe_float(svc.get("billed_amount")),
            allowed_amount=_safe_float(svc.get("allowed_amount")),
            plan_pays=_safe_float(svc.get("plan_pays")),
            patient_responsibility=_safe_float(svc.get("patient_responsibility")),
        ))

    return ExtractedEOB(
        insurance_company=data.get("insurance_company"),
        policy_number=data.get("policy_number"),
        patient_name=data.get("patient_name"),
        claim_number=data.get("claim_number"),
        date_of_service=_safe_date(data.get("date_of_service")),
        provider_name=data.get("provider_name"),
        services=services,
        total_billed=_safe_float(data.get("total_billed")),
        total_allowed=_safe_float(data.get("total_allowed")),
        total_plan_pays=_safe_float(data.get("total_plan_pays")),
        total_patient_responsibility=_safe_float(data.get("total_patient_responsibility")),
        document_id=document_id,
    )


async def extract_bill_llm(text: str, document_id: str, *, model: str | None = None) -> ExtractedBill:
    """Extract bill fields using LLM. Falls back to regex on failure."""
    prompt = _BILL_PROMPT.format(text=_truncate(text))
    data = await chat_json(prompt, system=_BILL_SYSTEM, model=model, max_tokens=1536)

    if data is None:
        logger.info("LLM extraction failed for bill %s, falling back to regex", document_id)
        return regex_extract_bill(text, document_id)

    services = []
    for svc in data.get("services") or []:
        services.append(ServiceLine(
            description=svc.get("description", "Service"),
            cpt_code=svc.get("cpt_code"),
            amount=_safe_float(svc.get("amount")),
        ))

    return ExtractedBill(
        provider_name=data.get("provider_name"),
        patient_name=data.get("patient_name"),
        invoice_number=data.get("invoice_number"),
        date_of_service=_safe_date(data.get("date_of_service")),
        due_date=_safe_date(data.get("due_date")),
        services=services,
        total_amount=_safe_float(data.get("total_amount")),
        balance_due=_safe_float(data.get("balance_due")),
        payment_status=data.get("payment_status"),
        document_id=document_id,
    )


def _safe_float(value: Any) -> float | None:
    """Safely convert LLM output to float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    if isinstance(value, str):
        return parse_amount(value)
    return None


def _safe_date(value: Any) -> date | None:
    """Safely convert LLM output to date."""
    if value is None:
        return None
    if isinstance(value, str):
        return parse_date(value)
    return None


def _truncate(text: str, max_chars: int = 6000) -> str:
    """Truncate document text for LLM context."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n\n[...content truncated...]\n\n" + text[-half:]


# Re-export for type annotation
from typing import Any  # noqa: E402
