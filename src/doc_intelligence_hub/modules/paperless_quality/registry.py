"""Stable registry for OWL-managed Paperless quality saved views."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class QualityViewKey(str, Enum):
    INBOX = "inbox"
    MISSING_CORRESPONDENT = "missing_correspondent"
    MISSING_DOCUMENT_TYPE = "missing_document_type"
    NO_TAGS = "no_tags"
    RECORD = "record"
    OTHER = "other"
    MANUAL_MISSING_STORAGE_PATH = "manual_missing_storage_path"
    EOB_MISSING_HOUSEHOLD_MEMBER = "eob_missing_household_member"
    ACCOUNT_IDENTIFIER_MISSING_OR_CONFLICTING = "account_identifier_missing_or_conflicting"
    DUPLICATE_CORRESPONDENT_CANDIDATES = "duplicate_correspondent_candidates"
    RECENTLY_ADDED_AWAITING_REVIEW = "recently_added_awaiting_review"


@dataclass(frozen=True)
class QualityViewDefinition:
    key: QualityViewKey
    label: str
    exact_count_requires_scan: bool = False

    @property
    def name(self) -> str:
        return f"OWL - {self.label}"


QUALITY_VIEW_REGISTRY = {
    definition.key: definition
    for definition in (
        QualityViewDefinition(QualityViewKey.INBOX, "Inbox"),
        QualityViewDefinition(QualityViewKey.MISSING_CORRESPONDENT, "Missing correspondent"),
        QualityViewDefinition(QualityViewKey.MISSING_DOCUMENT_TYPE, "Missing document type"),
        QualityViewDefinition(QualityViewKey.NO_TAGS, "No tags"),
        QualityViewDefinition(QualityViewKey.RECORD, "Record"),
        QualityViewDefinition(QualityViewKey.OTHER, "Other"),
        QualityViewDefinition(
            QualityViewKey.MANUAL_MISSING_STORAGE_PATH,
            "Manuals missing storage path",
        ),
        QualityViewDefinition(
            QualityViewKey.EOB_MISSING_HOUSEHOLD_MEMBER,
            "EOB missing household member",
        ),
        QualityViewDefinition(
            QualityViewKey.ACCOUNT_IDENTIFIER_MISSING_OR_CONFLICTING,
            "Account Identifier missing or conflicting",
            exact_count_requires_scan=True,
        ),
        QualityViewDefinition(
            QualityViewKey.DUPLICATE_CORRESPONDENT_CANDIDATES,
            "Duplicate correspondent candidates",
        ),
        QualityViewDefinition(
            QualityViewKey.RECENTLY_ADDED_AWAITING_REVIEW,
            "Recently added awaiting review",
        ),
    )
}


def quality_view_key_from_name(name: str) -> str | None:
    for key, definition in QUALITY_VIEW_REGISTRY.items():
        if name == definition.name:
            return key.value
    return None
