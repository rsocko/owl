"""Normalized, privacy-scoped document summaries for API review surfaces."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from doc_intelligence_hub.core.paperless import mask_account_identifier


class DocumentSummaryContext(StrEnum):
    GENERAL = "general"
    ACCOUNT_REVIEW = "account_review"


class DocumentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: int | str
    title: str | None = None
    correspondent: str | None = None
    document_type: str | None = None
    document_date: str | None = None
    tags: list[str] = Field(default_factory=list)
    account_identifier_display: str | None = None


def build_document_summary(
    source: dict[str, Any],
    *,
    context: DocumentSummaryContext = DocumentSummaryContext.GENERAL,
) -> dict[str, Any]:
    """Normalize common source shapes and enforce account-identifier egress policy."""
    document_id = _first(source, "document_id", "id", "paperless_document_id")
    if document_id is None:
        raise ValueError("document summary requires a document ID")

    correspondent = _first(
        source,
        "correspondent",
        "correspondent_name",
        "provider_name",
        "provider",
    )
    if not isinstance(correspondent, str):
        correspondent = None

    account_display = None
    if context is DocumentSummaryContext.ACCOUNT_REVIEW:
        account_display = mask_account_identifier(
            _first(
                source,
                "account_identifier_display",
                "account_identifier",
                "account_hint",
            )
        )

    summary = DocumentSummary(
        document_id=document_id,
        title=_text(_first(source, "title", "document_title")),
        correspondent=_text(correspondent),
        document_type=_text(
            _first(
                source,
                "document_type",
                "normalized_document_type",
                "document_classification",
                "doc_type",
                "type",
            )
        ),
        document_date=_date_text(
            _first(
                source,
                "document_date",
                "statement_date",
                "date_of_service",
                "created_date",
                "created_at",
            )
        ),
        tags=_tags(source.get("tags")),
        account_identifier_display=account_display,
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
