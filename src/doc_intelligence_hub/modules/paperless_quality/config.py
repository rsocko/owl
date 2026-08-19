"""Typed, deployment-private configuration for Paperless quality operations."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator


class AccountFieldBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_field_id: PositiveInt
    canonical_data_type: str
    legacy_field_ids: list[PositiveInt] = Field(default_factory=list)


class DocumentTypeBindings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record: PositiveInt
    other: PositiveInt
    manual: PositiveInt
    eob: PositiveInt


class StoragePathBindings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manual: PositiveInt


class QualityConfig(BaseModel):
    """IDs are intentionally supplied only by protected deployment inventory."""

    model_config = ConfigDict(extra="forbid")

    expected_origin: str
    owner_id: PositiveInt
    document_types: DocumentTypeBindings
    storage_paths: StoragePathBindings
    account_identifier: AccountFieldBinding
    household_member_tag_ids: list[PositiveInt] = Field(min_length=1)
    review_complete_tag_ids: list[PositiveInt] = Field(min_length=1)
    duplicate_correspondent_ids: list[PositiveInt] = Field(default_factory=list)
    recent_window_days: int = Field(default=14, ge=1, le=90)
    expected_counts: dict[str, int] = Field(default_factory=dict)

    @field_validator("expected_origin")
    @classmethod
    def normalize_origin(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith("https://"):
            raise ValueError("expected_origin must use https")
        return normalized

    @field_validator("household_member_tag_ids", "review_complete_tag_ids")
    @classmethod
    def unique_required_ids(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("tag IDs must be unique")
        return value

    @field_validator("duplicate_correspondent_ids")
    @classmethod
    def unique_correspondent_ids(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("correspondent IDs must be unique")
        return value


def load_quality_config(path: str | Path) -> QualityConfig:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return QualityConfig.model_validate(payload)
