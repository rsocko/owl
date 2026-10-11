"""Privacy-bounded delivery contracts exposed by OWL."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SourceDocumentReadError(RuntimeError):
    """Paperless stream failed before Tyrion received a request."""


class DeliveryState(StrEnum):
    NOT_SUBMITTED = "not_submitted"
    SUBMITTING = "submitting"
    UNKNOWN = "unknown"
    PROCESSING = "processing"
    ACKNOWLEDGED = "acknowledged"
    RETRYABLE = "retryable"
    FAILED = "failed"
    BLOCKED_RECOVERY = "blocked_recovery"


class GatewayState(StrEnum):
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"
    PROCESSING = "processing"
    ACKNOWLEDGED = "acknowledged"
    RETRYABLE = "retryable"
    REJECTED = "rejected"


class GatewayResult(BaseModel):
    state: GatewayState
    acknowledgement_ref: str | None = Field(default=None, min_length=1, max_length=500)
    reason_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,64}$")
    revision: int | None = Field(default=None, ge=0)
    acknowledged: bool = False
    reconcile_required: bool = False


class MonarchDeliveryResult(BaseModel):
    schema_version: str = "1.0"
    delivery_ref: str
    state: DeliveryState
    attempt_count: int = Field(ge=0)
    reason_code: str | None = None
    can_send: bool
    can_reconcile: bool
    can_retry: bool
    display_tag_pending: bool = False
    acknowledged: bool = False
