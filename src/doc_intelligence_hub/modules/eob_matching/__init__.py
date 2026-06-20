"""Pure-logic EOB ↔ Bill matching helpers."""

from __future__ import annotations

from doc_intelligence_hub.modules.eob_matching.classifier import INSURANCE_COMPANIES, classify_document
from doc_intelligence_hub.modules.eob_matching.extractor import extract_bill, extract_eob, parse_amount, parse_date
from doc_intelligence_hub.modules.eob_matching.matcher import match_documents
from doc_intelligence_hub.modules.eob_matching.models import (
    ClassificationResult,
    DocumentType,
    ExtractedBill,
    ExtractedEOB,
    MatchBreakdown,
    MatchConfidence,
    MatchResult,
    ServiceLine,
)

__all__ = [
    "INSURANCE_COMPANIES",
    "ClassificationResult",
    "DocumentType",
    "ExtractedBill",
    "ExtractedEOB",
    "MatchBreakdown",
    "MatchConfidence",
    "MatchResult",
    "ServiceLine",
    "classify_document",
    "extract_bill",
    "extract_eob",
    "match_documents",
    "parse_amount",
    "parse_date",
]
