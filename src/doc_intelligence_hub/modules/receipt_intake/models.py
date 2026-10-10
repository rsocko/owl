"""Privacy-bounded contracts for canonical receipt intake."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourceChannel(StrEnum):
    EMAIL_ATTACHMENT = "email_attachment"
    EMAIL_BODY = "email_body"
    SCANNER_JOB = "scanner_job"
    PHONE_UPLOAD = "phone_upload"
    MANUAL_UPLOAD = "manual_upload"
    PROVIDER_API = "provider_api"
    MONARCH_RECOVERY = "monarch_recovery"


class AttemptState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"
    FAILED = "failed"
    RETRYABLE = "retryable"
    REVIEW_REQUIRED = "review_required"


class IntakeOutcome(StrEnum):
    NEW_CANONICAL = "new_canonical"
    SOURCE_OCCURRENCE_REUSED = "source_occurrence_reused"
    EXACT_HASH_REUSED = "exact_hash_reused"
    NORMALIZED_CONTENT_REVIEW = "normalized_content_review"
    SEMANTIC_REVIEW_CANDIDATE = "semantic_review_candidate"
    PAPERLESS_DUPLICATE = "paperless_duplicate"
    UPLOAD_OUTCOME_UNKNOWN = "upload_outcome_unknown"
    UPLOAD_RETRYABLE = "upload_retryable"
    UPLOAD_FAILED = "upload_failed"


class ProposalKind(StrEnum):
    NORMALIZED_CONTENT = "normalized_content"
    SEMANTIC = "semantic"


class ProposalState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ExternalReplicaState(StrEnum):
    NOT_SUBMITTED = "not_submitted"
    SUBMITTED = "submitted"
    PROCESSING = "processing"
    MATCHED = "matched"
    AWAITING_TRANSACTION = "awaiting_transaction"
    AMBIGUOUS = "ambiguous"
    FAILED = "failed"
    RETRYABLE = "retryable"
    REVIEW = "review"
    NOT_APPLICABLE = "not_applicable"
    VERIFIED = "verified"
    SETTLED = "settled"
    DELETED = "deleted"
    DELETE_UNKNOWN = "delete_unknown"


class IntakeMetadata(BaseModel):
    """Only bounded, non-content metadata accepted beside temporary bytes."""

    model_config = ConfigDict(extra="forbid")

    source_channel: SourceChannel
    source_occurrence_version: str = Field(default="1", pattern=r"^[a-z0-9][a-z0-9._-]{0,31}$")
    source_occurrence_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    connector_ref: str | None = Field(default=None, min_length=8, max_length=200)
    source_observed_at: datetime | None = None
    transform_version: str | None = Field(default=None, min_length=1, max_length=64)
    normalized_content_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    normalized_content_version: str | None = Field(default=None, min_length=1, max_length=64)
    semantic_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    semantic_fingerprint_version: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("connector_ref")
    @classmethod
    def validate_connector_ref(cls, value: str | None) -> str | None:
        if value is not None and any(character.isspace() for character in value):
            raise ValueError("connector_ref must be opaque and contain no whitespace")
        return value

    def model_post_init(self, __context: object) -> None:
        pairs = (
            (self.normalized_content_fingerprint, self.normalized_content_version),
            (self.semantic_fingerprint, self.semantic_fingerprint_version),
        )
        if any(bool(fingerprint) != bool(version) for fingerprint, version in pairs):
            raise ValueError("each fingerprint requires its algorithm version")


class IntakeResult(BaseModel):
    """Bounded result safe for downstream orchestration."""

    schema_version: str = "1.0"
    intake_ref: str
    outcome: IntakeOutcome
    attempt_state: AttemptState
    canonical_document_ref: str | None = None
    review_ref: str | None = None
    reason_codes: list[str] = Field(default_factory=list, max_length=8)
    source_channel: SourceChannel
    source_occurrence_version: str
    source_as_of: datetime | None = None
    retry_safe: bool
    external_replica_eligible: bool


class LedgerStatus(BaseModel):
    schema_version: str = "1.0"
    intake_ref: str
    outcome: IntakeOutcome
    attempt_state: AttemptState
    canonical_document_ref: str | None
    source_channel: SourceChannel
    source_occurrence_version: str
    source_as_of: datetime | None
    retry_safe: bool
    external_replica_eligible: bool


class ExternalReplicaResult(BaseModel):
    schema_version: str = "1.0"
    replica_ref: str
    canonical_document_ref: str
    lifecycle_state: ExternalReplicaState
    revision: int
