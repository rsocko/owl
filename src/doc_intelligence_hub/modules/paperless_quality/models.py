"""Versioned redacted and protected contracts for quality operations."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from typing import Any

from doc_intelligence_hub.modules.metadata_migration.models import to_json_safe

QUALITY_SCHEMA_VERSION = "1.0"


@dataclasses.dataclass(frozen=True)
class FilterRule:
    rule_type: int
    value: str | None


@dataclasses.dataclass(frozen=True)
class ViewPlan:
    stable_key: str
    name: str
    rules: tuple[FilterRule, ...]
    expected_count: int | None
    observed_count: int
    exact_count: int
    existing_view_id: int | None
    existing_digest: str | None
    action: str
    reason_code: str


@dataclasses.dataclass(frozen=True)
class ManualCandidate:
    document_id: int
    expected_modified: str
    expected_document_type: int
    expected_storage_path: None = None


@dataclasses.dataclass
class ProtectedQualityPlan:
    plan_digest: str
    config_digest: str
    instance_digest: str
    planned_at: str
    views: list[ViewPlan]
    manual_candidates: list[ManualCandidate]
    schema_version: str = QUALITY_SCHEMA_VERSION


@dataclasses.dataclass
class QualitySummary:
    plan_digest: str
    planned_at: str
    views: list[dict[str, Any]]
    completion_state: str
    counts: dict[str, int]
    redacted: bool = True
    schema_version: str = QUALITY_SCHEMA_VERSION

    def to_json(self) -> str:
        return json.dumps(to_json_safe(self), indent=2, sort_keys=True)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
