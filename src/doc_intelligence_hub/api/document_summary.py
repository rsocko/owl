"""Normalized, privacy-scoped document summaries for review surfaces."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from doc_intelligence_hub.core.paperless import mask_account_identifier


class DocumentSummaryContext(StrEnum):
    GENERAL = "general"
    ACCOUNT_REVIEW = "account_review"
    MEDICAL_REVIEW = "medical_review"
    MEDICAL_ACCOUNT_REVIEW = "medical_account_review"


class DocumentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: int | str
    title: str | None = None
    correspondent: str | None = None
    document_type: str | None = None
    document_date: str | None = None
    date_label: str | None = None
    tags: list[str] = Field(default_factory=list)
    account_identifier_display: str | None = None
    patient_name: str | None = None


_ACCOUNT_CONTEXTS = {
    DocumentSummaryContext.ACCOUNT_REVIEW,
    DocumentSummaryContext.MEDICAL_ACCOUNT_REVIEW,
}
_MEDICAL_CONTEXTS = {
    DocumentSummaryContext.MEDICAL_REVIEW,
    DocumentSummaryContext.MEDICAL_ACCOUNT_REVIEW,
}


def build_document_summary(
    source: dict[str, Any],
    *,
    context: DocumentSummaryContext = DocumentSummaryContext.GENERAL,
) -> dict[str, Any]:
    """Normalize supported source shapes while enforcing summary privacy policy."""
    document_id = _first(source, "document_id", "id", "paperless_document_id")
    if document_id is None:
        raise ValueError("document summary requires a document ID")

    document_date, date_label = _document_date(source)
    account_display = None
    if context in _ACCOUNT_CONTEXTS:
        account_display = mask_account_identifier(
            _first(
                source,
                "account_identifier_display",
                "account_identifier",
                "account_hint",
            )
        )

    patient_name = None
    if context in _MEDICAL_CONTEXTS:
        patient_name = _text(source.get("patient_name"))

    summary = DocumentSummary(
        document_id=document_id,
        title=_text(_first(source, "title", "document_title")),
        correspondent=_text(
            _first(
                source,
                "correspondent_name",
                "correspondent",
                "provider_name",
                "provider",
            )
        ),
        # Paperless-native names are the only accepted document-type source.
        document_type=_text(
            _first(source, "document_type_name", "paperless_document_type")
        ),
        document_date=document_date,
        date_label=date_label,
        tags=_tags(source.get("tags")),
        account_identifier_display=account_display,
        patient_name=patient_name,
    )
    return summary.model_dump(exclude_none=True)


def _first(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _date_text(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return _text(value)


def _document_date(source: dict[str, Any]) -> tuple[str | None, str | None]:
    date_fields = (
        ("document_date", "Document date"),
        ("statement_date", "Statement date"),
        ("date_of_service", "Date of service"),
        ("created_date", "Created"),
        ("created_at", "Created"),
        ("created", "Created"),
    )
    for key, label in date_fields:
        value = _date_text(source.get(key))
        if value:
            return value, label
    return None, None


def _tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    for item in value:
        candidate = item.get("name") if isinstance(item, dict) else item
        normalized = _text(candidate)
        if normalized and normalized not in tags:
            tags.append(normalized)
    return tags
