"""Versioned contracts for metadata inventory, migration, and reporting."""

from __future__ import annotations

import dataclasses
import json
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

SUMMARY_SCHEMA_VERSION = "1.0"
PROTECTED_SCHEMA_VERSION = "1.0"
FIELD_REVIEW_SCHEMA_VERSION = "1.0"


class RunMode(str, Enum):
    INVENTORY = "inventory"
    PREPARE = "prepare"
    BACKFILL = "backfill"


class CompletionState(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"


class MigrationAction(str, Enum):
    NONE = "none"
    CREATE_FIELD = "create_field"
    BACKFILL_VALUE = "backfill_value"
    REVIEW = "review"


class MigrationResult(str, Enum):
    PROPOSED = "proposed"
    APPLIED = "applied"
    RECONCILED = "reconciled"
    SKIPPED = "skipped"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"


class ReasonCode(str, Enum):
    CANONICAL_PRESENT = "canonical_present"
    NO_LEGACY_VALUE = "no_legacy_value"
    MISSING_CANONICAL = "missing_canonical"
    INCOMPATIBLE_SCHEMA = "incompatible_schema"
    VALUE_CONFLICT = "value_conflict"
    INVALID_VALUE = "invalid_value"
    READY = "ready"
    ALREADY_APPLIED = "already_applied"
    WRITE_FAILED = "write_failed"
    VERIFY_FAILED = "verify_failed"


class FieldDisposition(str, Enum):
    KEEP = "keep"
    MIGRATE_THEN_RETIRE = "migrate_then_retire"
    RETIRE_WHEN_EMPTY = "retire_when_empty"
    PRESERVE_UNMANAGED = "preserve_unmanaged"
    REVIEW_REQUIRED = "review_required"


class FieldReviewState(str, Enum):
    ABSENT = "absent"
    RETAINED = "retained"
    ELIGIBLE = "eligible"
    BLOCKED_POPULATED = "blocked_populated"
    BLOCKED_DEPENDENCY = "blocked_dependency"
    INCOMPATIBLE = "incompatible"
    DUPLICATE = "duplicate"
    DELETED = "deleted"


@dataclasses.dataclass(frozen=True)
class FieldReview:
    stable_key: str
    field_name: str
    role: str
    disposition: FieldDisposition
    state: FieldReviewState
    deployed_count: int
    expected_types: tuple[str, ...]
    observed_types: tuple[str, ...]
    non_empty_documents: int
    owl_covered_documents: int
    uncovered_documents: int
    saved_view_references: int
    document_type_references: int
    workflow_references: int
    diagnostic_codes: tuple[str, ...] = ()


@dataclasses.dataclass
class FieldReviewSummary:
    run_id: str
    mode: str
    started_at: str
    fields: list[FieldReview]
    unmanaged_fields: int
    apply: bool
    finished_at: str | None = None
    deleted_fields: int = 0
    cleared_document_values: int = 0
    eligible_fields: int = 0
    blocked_populated: int = 0
    blocked_dependencies: int = 0
    review_required: int = 0
    redacted: bool = True
    exit_status: int = 0
    schema_version: str = FIELD_REVIEW_SCHEMA_VERSION

    def finish(self) -> None:
        self.finished_at = datetime.now(UTC).isoformat()
        self.eligible_fields = sum(
            field.state in {FieldReviewState.ELIGIBLE, FieldReviewState.DELETED}
            for field in self.fields
        )
        self.deleted_fields = sum(field.state is FieldReviewState.DELETED for field in self.fields)
        self.blocked_populated = sum(
            field.state is FieldReviewState.BLOCKED_POPULATED for field in self.fields
        )
        self.blocked_dependencies = sum(
            field.state is FieldReviewState.BLOCKED_DEPENDENCY for field in self.fields
        )
        self.review_required = sum(
            field.state
            in {
                FieldReviewState.DUPLICATE,
                FieldReviewState.INCOMPATIBLE,
            }
            for field in self.fields
        )
        self.exit_status = 2 if self.review_required else 0

    def to_json(self) -> str:
        return json.dumps(to_json_safe(self), indent=2, sort_keys=True)


@dataclasses.dataclass(frozen=True)
class FieldCompatibility:
    stable_key: str
    canonical_present: bool
    expected_types: tuple[str, ...]
    observed_type: str | None
    alias_count: int
    diagnostic_codes: tuple[str, ...]
    proposed_action: MigrationAction
    reason_code: ReasonCode


@dataclasses.dataclass(frozen=True)
class ProtectedRecord:
    document_id: int
    stable_key: str
    action: MigrationAction
    result: MigrationResult
    reason_code: ReasonCode
    idempotency_key: str
    source_field_id: int | None = None
    target_field_id: int | None = None
    before_value: Any = None
    after_value: Any = None
    error_code: str | None = None
    retry_eligible: bool = False
    retry_count: int = 0
    recorded_at: str = dataclasses.field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclasses.dataclass
class SanitizedSummary:
    registry_digest: str
    run_id: str
    mode: RunMode
    batch_size: int
    started_at: str
    finished_at: str | None = None
    completion_state: CompletionState = CompletionState.RUNNING
    counts: Counter[str] = dataclasses.field(default_factory=Counter)
    counts_by_key: dict[str, Counter[str]] = dataclasses.field(default_factory=dict)
    compatibility: list[FieldCompatibility] = dataclasses.field(default_factory=list)
    redacted: bool = True
    next_step: str | None = None
    exit_status: int = 0
    schema_version: str = SUMMARY_SCHEMA_VERSION

    def add(self, key: str, result: MigrationResult, reason: ReasonCode) -> None:
        self.counts[result.value] += 1
        grouped = self.counts_by_key.setdefault(key, Counter())
        grouped[result.value] += 1
        grouped[f"reason:{reason.value}"] += 1

    def finish(self) -> None:
        self.finished_at = datetime.now(UTC).isoformat()
        if self.counts[MigrationResult.FAILED.value]:
            self.completion_state = CompletionState.FAILED
            self.next_step = "Retry eligible failures; inspect protected audit records."
            self.exit_status = 1
        elif self.counts[MigrationResult.REVIEW_REQUIRED.value]:
            self.completion_state = CompletionState.REVIEW_REQUIRED
            self.next_step = "Resolve protected review items before another apply run."
            self.exit_status = 2
        else:
            self.completion_state = CompletionState.COMPLETED
            self.next_step = "No unresolved migration outcomes."
            self.exit_status = 0

    def to_json(self) -> str:
        return json.dumps(to_json_safe(self), indent=2, sort_keys=True)


def to_json_safe(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            field.name: to_json_safe(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Counter):
        return dict(value)
    if isinstance(value, dict):
        return {str(key): to_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_safe(item) for item in value]
    return value
