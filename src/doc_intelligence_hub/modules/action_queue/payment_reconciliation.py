"""Action Queue-owned payment evidence, relationship, and review lifecycle."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import (
    Action,
    ActionLifecycleEvent,
    Obligation,
    ObligationPaymentEdge,
    PaymentActionReceipt,
    PaymentCheckRun,
    PaymentEvidence,
    PaymentEvidenceGeneration,
    PaymentEvidenceSource,
    PaymentReconciliationCase,
    PaymentRelationshipEvent,
)
from .lifecycle import transition_action_status

EvidenceSource = Literal["tyrion_receipt", "tyrion_bill_match", "legacy_receipt"]
MatchState = Literal[
    "matched",
    "no_match",
    "ambiguous",
    "conflict",
    "source_unavailable",
    "projection_failed",
    "stale",
    "not_applicable",
]
PaymentStatus = Literal["paid", "pending", "unmatched", "ambiguous", "unknown"]
CaseAction = Literal[
    "confirm",
    "reject",
    "replace",
    "clear",
    "defer",
    "reopen",
    "mark_not_applicable",
    "deliver_attention",
    "acknowledge_attention",
]

_EXCEPTION_CASES = frozenset(
    {
        "unmatched",
        "ambiguous",
        "source_unavailable",
        "projection_failed",
        "stale",
        "conflict",
        "not_applicable",
        "partial_payment",
        "double_payment",
    }
)
_SOURCE_REFERENCE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_RESERVED_SOURCE_REFERENCES = frozenset({"__proto__", "constructor", "prototype"})


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    value = uuid.uuid5(uuid.NAMESPACE_URL, ":".join(("owl", prefix, *parts))).hex
    return f"{prefix}_{value}"


def _validate_source_reference(value: str | None) -> str | None:
    if value in _RESERVED_SOURCE_REFERENCES:
        raise ValueError("source reference contains a reserved value")
    return value


class NormalizedPaymentEvidence(BaseModel):
    """One bounded evidence row; content and raw source records are forbidden."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence_ref: str = Field(min_length=8, max_length=200)
    canonical_document_ref: str | None = Field(default=None, min_length=8, max_length=200)
    transaction_ref: str | None = Field(default=None, min_length=8, max_length=200)
    receipt_ref: str | None = Field(default=None, min_length=8, max_length=200)
    kind: Literal["invoice", "bill", "receipt", "posted_transaction", "pending_transaction"]
    payee_hint: str | None = Field(default=None, max_length=120)
    account_display_name: str | None = Field(default=None, max_length=120)
    account_last_four: str | None = Field(default=None, pattern=r"^[0-9]{4}$")
    score_basis_points: int | None = Field(default=None, ge=0, le=10_000)
    rank: int | None = Field(default=None, ge=1, le=100)
    score_gap_basis_points: int | None = Field(default=None, ge=0, le=10_000)
    receipt_association: dict | None = None
    amount_minor: int | None = Field(default=None, ge=0, le=100_000_000_000)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    evidence_date: date | None = None
    reference_present: bool = False
    reference_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_state: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    match_state: MatchState
    payment_status: PaymentStatus
    confidence: Literal["high", "medium", "low"] | None = None
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    source_revision: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("reason_codes")
    @classmethod
    def validate_reason_codes(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value))
        if any(
            not item
            or len(item) > 64
            or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for character in item)
            for item in normalized
        ):
            raise ValueError("reason_codes must contain bounded lowercase identifiers")
        return normalized

    @model_validator(mode="after")
    def validate_amount(self) -> NormalizedPaymentEvidence:
        if (self.amount_minor is None) != (self.currency is None):
            raise ValueError("amount_minor and currency must be supplied together")
        return self


class PaymentEvidenceSnapshot(BaseModel):
    """Versioned source snapshot accepted by the OWL reconciliation aggregate."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    contract_version: Literal["1.0"] = "1.0"
    source_system: EvidenceSource
    connector_ref: str = Field(min_length=8, max_length=200)
    source_generation: str = Field(min_length=1, max_length=200)
    source_as_of: datetime
    completeness: Literal["complete", "partial"] = "partial"
    obligation_id: str = Field(min_length=1, max_length=200)
    evidence: list[NormalizedPaymentEvidence] = Field(max_length=100)

    @model_validator(mode="after")
    def validate_unique_evidence(self) -> PaymentEvidenceSnapshot:
        if self.source_as_of.tzinfo is None:
            raise ValueError("source_as_of must include a UTC offset")
        refs = [item.evidence_ref for item in self.evidence]
        if len(refs) != len(set(refs)):
            raise ValueError("evidence_ref values must be unique")
        return self


class TyrionIntakeResultV1(BaseModel):
    """Exact OWL intake result nested by Tyrion Receipt Evidence v1."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"]
    intake_ref: str = Field(min_length=1, max_length=160, pattern=_SOURCE_REFERENCE_PATTERN)
    outcome: Literal[
        "new_canonical",
        "source_occurrence_reused",
        "exact_hash_reused",
        "normalized_content_review",
        "semantic_review_candidate",
        "paperless_duplicate",
        "upload_outcome_unknown",
        "upload_retryable",
        "upload_failed",
    ]
    attempt_state: Literal[
        "pending", "accepted", "unknown", "retryable", "failed", "review_required"
    ]
    canonical_document_ref: str | None = Field(
        default=None, min_length=1, max_length=160, pattern=_SOURCE_REFERENCE_PATTERN
    )
    review_ref: str | None = Field(
        default=None, min_length=1, max_length=160, pattern=_SOURCE_REFERENCE_PATTERN
    )
    reason_codes: list[str] = Field(max_length=8)
    source_channel: Literal[
        "email_attachment",
        "email_body",
        "scanner_job",
        "phone_upload",
        "manual_upload",
        "provider_api",
        "monarch_recovery",
    ]
    source_occurrence_version: str = Field(
        min_length=1, max_length=32, pattern=r"^[a-z0-9][a-z0-9._-]*$"
    )
    source_as_of: datetime | None
    retry_safe: bool
    external_replica_eligible: bool

    _validate_refs = field_validator("intake_ref", "canonical_document_ref", "review_ref")(
        _validate_source_reference
    )


class TyrionNativeReceiptEvidenceV1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_by_alias=True,
        validate_by_name=False,
    )

    receipt_source: Literal["upload", "email"] = Field(alias="receiptSource")
    receipt_state: Literal["processing", "awaiting_match", "matched", "failed"] = Field(
        alias="receiptState"
    )
    transaction_ref: str | None = Field(
        alias="transactionRef",
        default=None,
        min_length=1,
        max_length=160,
        pattern=_SOURCE_REFERENCE_PATTERN,
    )
    attachment_count: int = Field(alias="attachmentCount", ge=0, le=8)
    source_as_of: datetime | None = Field(alias="sourceAsOf")
    observed_at: datetime = Field(alias="observedAt")

    _validate_transaction_ref = field_validator("transaction_ref")(_validate_source_reference)


class TyrionReceiptEvidenceV1(BaseModel):
    """Exact strict Tyrion ReceiptEvidenceResponseV1 contract."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_by_alias=True,
        validate_by_name=False,
    )

    receipt_contract_version: Literal["1.0"] = Field(alias="receiptContractVersion")
    intake: TyrionIntakeResultV1
    replica_ref: str | None = Field(
        alias="replicaRef",
        default=None,
        min_length=1,
        max_length=160,
        pattern=_SOURCE_REFERENCE_PATTERN,
    )
    replica_lifecycle: Literal[
        "not_submitted",
        "submitted",
        "processing",
        "matched",
        "awaiting_transaction",
        "ambiguous",
        "failed",
        "retryable",
        "review",
        "not_applicable",
        "verified",
        "settled",
        "deleted",
        "delete_unknown",
    ] = Field(alias="replicaLifecycle")
    revision: int = Field(ge=0)
    native_evidence: TyrionNativeReceiptEvidenceV1 | None = Field(alias="nativeEvidence")
    review_required: bool = Field(alias="reviewRequired")
    reason_codes: list[str] = Field(alias="reasonCodes", max_length=12)

    _validate_replica_ref = field_validator("replica_ref")(_validate_source_reference)


class TyrionReceiptEvidenceIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    obligation_id: str = Field(min_length=1, max_length=200)
    connector_ref: str = Field(min_length=8, max_length=200)
    evidence: TyrionReceiptEvidenceV1

    def to_snapshot(self) -> PaymentEvidenceSnapshot:
        native = self.evidence.native_evidence
        source_as_of = (
            native.source_as_of
            if native and native.source_as_of
            else native.observed_at
            if native
            else self.evidence.intake.source_as_of or datetime.now(UTC)
        )
        if self.evidence.replica_lifecycle == "ambiguous":
            match_state: MatchState = "ambiguous"
        elif self.evidence.replica_lifecycle in {"failed", "retryable", "delete_unknown"}:
            match_state = "source_unavailable"
        elif self.evidence.replica_lifecycle == "not_applicable":
            match_state = "not_applicable"
        elif native and native.receipt_state == "matched" and native.transaction_ref is not None:
            match_state = "matched"
        else:
            match_state = "no_match"
        evidence_ref = self.evidence.replica_ref or self.evidence.intake.intake_ref
        return PaymentEvidenceSnapshot(
            source_system="tyrion_receipt",
            connector_ref=self.connector_ref,
            source_generation=(f"{self.evidence.intake.intake_ref}:{self.evidence.revision}"),
            source_as_of=source_as_of,
            completeness="partial",
            obligation_id=self.obligation_id,
            evidence=[
                NormalizedPaymentEvidence(
                    evidence_ref=evidence_ref,
                    canonical_document_ref=self.evidence.intake.canonical_document_ref,
                    transaction_ref=native.transaction_ref if native else None,
                    receipt_ref=self.evidence.replica_ref,
                    kind="receipt",
                    source_state=self.evidence.replica_lifecycle,
                    match_state=match_state,
                    payment_status="unknown",
                    confidence="high" if match_state == "matched" else None,
                    reason_codes=list(dict.fromkeys(self.evidence.reason_codes))[:20],
                    source_revision=str(self.evidence.revision),
                )
            ],
        )


class TyrionBillMatchSignalV1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_by_alias=True,
        validate_by_name=False,
    )

    kind: Literal["amount", "date", "payee", "account"]
    strength: Literal["exact", "strong", "partial", "none", "notProvided"]
    contribution_basis_points: int = Field(alias="contributionBasisPoints", ge=0, le=5_500)


class TyrionBillMatchCandidateV1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_by_alias=True,
        validate_by_name=False,
    )

    transaction_ref: str = Field(
        alias="transactionRef",
        min_length=1,
        max_length=160,
        pattern=_SOURCE_REFERENCE_PATTERN,
    )
    transaction_date: date = Field(alias="transactionDate")
    transaction_state: Literal["posted", "pending"] = Field(alias="transactionState")
    score_basis_points: int = Field(alias="scoreBasisPoints", ge=0, le=10_000)
    confidence: Literal["high", "medium", "low"]
    signals: list[TyrionBillMatchSignalV1] = Field(min_length=4, max_length=4)

    _validate_transaction_ref = field_validator("transaction_ref")(_validate_source_reference)


class TyrionBillMatchResponseV1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_by_alias=True,
        validate_by_name=False,
    )

    contract_version: Literal["1.0"] = Field(alias="contractVersion")
    bill_ref: str = Field(
        alias="billRef",
        min_length=1,
        max_length=160,
        pattern=_SOURCE_REFERENCE_PATTERN,
    )
    match_status: Literal["matched", "noMatch", "ambiguous"] = Field(alias="matchStatus")
    payment_status: Literal["paid", "pending", "unmatched", "ambiguous"] = Field(
        alias="paymentStatus"
    )
    selected_transaction_ref: str | None = Field(
        alias="selectedTransactionRef",
        default=None,
        min_length=1,
        max_length=160,
        pattern=_SOURCE_REFERENCE_PATTERN,
    )
    candidates: list[TyrionBillMatchCandidateV1] = Field(max_length=10)

    _validate_refs = field_validator("bill_ref", "selected_transaction_ref")(
        _validate_source_reference
    )

    @model_validator(mode="after")
    def validate_decision(self) -> TyrionBillMatchResponseV1:
        refs = [candidate.transaction_ref for candidate in self.candidates]
        if len(refs) != len(set(refs)):
            raise ValueError("candidates must contain unique transactionRef values")
        if self.match_status == "matched":
            if (
                self.selected_transaction_ref is None
                or not self.candidates
                or self.candidates[0].transaction_ref != self.selected_transaction_ref
            ):
                raise ValueError("matched response must select the first candidate")
            expected_payment = (
                "paid" if self.candidates[0].transaction_state == "posted" else "pending"
            )
            if self.payment_status != expected_payment:
                raise ValueError("paymentStatus must agree with selected transaction state")
        elif self.selected_transaction_ref is not None:
            raise ValueError("selectedTransactionRef is only valid for matched responses")
        elif self.match_status == "noMatch" and self.payment_status != "unmatched":
            raise ValueError("noMatch requires paymentStatus unmatched")
        elif self.match_status == "ambiguous" and self.payment_status != "ambiguous":
            raise ValueError("ambiguous match requires paymentStatus ambiguous")
        return self


class TyrionBillMatchIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    obligation_id: str = Field(min_length=1, max_length=200)
    connector_ref: str = Field(min_length=8, max_length=200)
    source_generation: str = Field(min_length=1, max_length=200)
    source_as_of: datetime
    canonical_document_ref: str | None = Field(default=None, min_length=8, max_length=200)
    expected_amount_minor: int = Field(ge=1, le=100_000_000_000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    response: TyrionBillMatchResponseV1

    def to_snapshot(self) -> PaymentEvidenceSnapshot:
        selected = (
            self.response.candidates[0]
            if self.response.match_status == "matched" and self.response.candidates
            else None
        )
        match_state: MatchState = {
            "matched": "matched",
            "noMatch": "no_match",
            "ambiguous": "ambiguous",
        }[self.response.match_status]
        reason_codes = (
            [f"{signal.kind}_{signal.strength}".lower() for signal in selected.signals]
            if selected
            else [f"bill_match_{match_state}"]
        )
        evidence_ref = (
            self.response.selected_transaction_ref
            or f"{self.response.bill_ref}:{self.source_generation}"
        )
        return PaymentEvidenceSnapshot(
            source_system="tyrion_bill_match",
            connector_ref=self.connector_ref,
            source_generation=self.source_generation,
            source_as_of=self.source_as_of,
            completeness="partial",
            obligation_id=self.obligation_id,
            evidence=[
                NormalizedPaymentEvidence(
                    evidence_ref=evidence_ref,
                    canonical_document_ref=self.canonical_document_ref,
                    transaction_ref=self.response.selected_transaction_ref,
                    kind=(
                        "posted_transaction"
                        if selected and selected.transaction_state == "posted"
                        else "pending_transaction"
                        if selected
                        else "bill"
                    ),
                    score_basis_points=selected.score_basis_points if selected else None,
                    amount_minor=self.expected_amount_minor,
                    currency=self.currency,
                    evidence_date=selected.transaction_date if selected else None,
                    source_state=selected.transaction_state if selected else match_state,
                    match_state=match_state,
                    payment_status=self.response.payment_status,
                    confidence=selected.confidence if selected else None,
                    reason_codes=reason_codes,
                )
            ],
        )


class PaymentSnapshotResult(BaseModel):
    source_generation: str
    disposition: Literal["applied", "replayed", "stale"]
    evidence_count: int = Field(ge=0)
    active_edge_count: int = Field(ge=0)


class PaymentSourceAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: CaseAction
    method: Literal["POST"]
    url: str
    expected_revision: int = Field(ge=1)


class PaymentObligationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: str
    revision: int = Field(ge=1)
    expected_amount_minor: int | None = Field(default=None, ge=0)
    currency: str | None = None
    completion_suggested: bool


class PaymentEvidenceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    payee_hint: str | None = None
    amount_minor: int | None = Field(default=None, ge=0)
    currency: str | None = None
    evidence_date: str | None = None
    source_system: EvidenceSource
    source_state: str
    match_state: MatchState
    payment_status: PaymentStatus
    confidence: Literal["high", "medium", "low"] | None = None
    reason_codes: list[str]
    source_as_of: str
    edge_state: str | None = None


class PaymentHistoryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    action: str
    actor: str
    previous_state: dict
    new_state: dict
    created_at: str


class PaymentReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0"]
    id: str
    revision: int = Field(ge=1)
    case_kind: str
    state: str
    active: bool
    attention_state: Literal["pending", "delivered", "acknowledged", "settled"]
    source_as_of: str | None
    summary: dict
    obligation: PaymentObligationSummary
    evidence: PaymentEvidenceSummary | None
    owl_deep_link: str
    source_actions: list[PaymentSourceAction]
    history: list[PaymentHistoryEvent] | None = None


class PaymentActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0"]
    action: CaseAction
    case: PaymentReviewItem
    source_acknowledgement: Literal[
        "not_required", "acknowledged", "pending_verification", "failed"
    ]
    authoritative_read_back: bool
    idempotent: bool


class PaymentReviewAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    contract_version: Literal["1.0"] = "1.0"
    action: CaseAction
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
    evidence_id: str | None = Field(default=None, min_length=1, max_length=200)
    replacement_evidence_id: str | None = Field(default=None, min_length=1, max_length=200)
    allocated_amount_minor: int | None = Field(default=None, ge=0, le=100_000_000_000)
    defer_until: datetime | None = None
    attention_delivery_ref: str | None = Field(default=None, min_length=8, max_length=200)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_action_fields(self) -> PaymentReviewAction:
        if self.action in {"confirm", "reject", "clear", "mark_not_applicable"}:
            if not self.evidence_id:
                raise ValueError(f"{self.action} requires evidence_id")
        if self.action == "replace" and (not self.evidence_id or not self.replacement_evidence_id):
            raise ValueError("replace requires evidence_id and replacement_evidence_id")
        if self.action == "defer" and self.defer_until is None:
            raise ValueError("defer requires defer_until")
        if self.action == "deliver_attention" and not self.attention_delivery_ref:
            raise ValueError("deliver_attention requires attention_delivery_ref")
        return self


class PaymentCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    contract_version: Literal["1.0"] = "1.0"
    revision: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")


class TyrionBillCheckRequestV2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )

    contract_version: Literal["2.0"] = Field(default="2.0", alias="contractVersion")
    request_ref: str = Field(alias="requestRef", min_length=8, max_length=200)
    bill_ref: str = Field(alias="billRef", min_length=1, max_length=160)
    payee: str = Field(min_length=1, max_length=120)
    expected_amount_minor: int = Field(
        alias="expectedAmountMinor",
        ge=1,
        le=100_000_000_000,
    )
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    due_date: date | None = Field(default=None, alias="dueDate")
    bill_date: date | None = Field(default=None, alias="billDate")
    reference: str | None = Field(default=None, max_length=160)
    account_scope: Literal["configuredEligible"] = Field(
        default="configuredEligible",
        alias="accountScope",
    )
    date_window_days: int | None = Field(default=None, alias="dateWindowDays", ge=1, le=365)
    amount_tolerance_minor: int | None = Field(
        default=None,
        alias="amountToleranceMinor",
        ge=0,
        le=100_000_000,
    )
    candidate_limit: int | None = Field(default=10, alias="candidateLimit", ge=1, le=25)

    @model_validator(mode="after")
    def validate_anchor_date(self) -> TyrionBillCheckRequestV2:
        if self.due_date is None and self.bill_date is None:
            raise ValueError("dueDate or billDate is required")
        return self


class TyrionReceiptAssociationV2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_by_alias=True,
        validate_by_name=False,
    )

    status: Literal["linked", "differentTransaction"]
    observed_at: datetime = Field(alias="observedAt")


class TyrionBillMatchCandidateV2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_by_alias=True,
        validate_by_name=False,
    )

    rank: int = Field(ge=1, le=25)
    transaction_ref: str = Field(
        alias="transactionRef",
        min_length=1,
        max_length=160,
        pattern=_SOURCE_REFERENCE_PATTERN,
    )
    transaction_date: date = Field(alias="transactionDate")
    transaction_state: Literal["posted", "pending"] = Field(alias="transactionState")
    payee: str = Field(min_length=1, max_length=120)
    amount_minor: int = Field(alias="amountMinor", ge=0, le=100_000_000_000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    account_display_name: str = Field(alias="accountDisplayName", min_length=1, max_length=120)
    account_last_four: str = Field(alias="accountLastFour", pattern=r"^[0-9]{4}$")
    score_basis_points: int = Field(alias="scoreBasisPoints", ge=0, le=10_000)
    confidence: Literal["high", "medium", "low"]
    score_gap_basis_points: int = Field(alias="scoreGapBasisPoints", ge=0, le=10_000)
    signals: list[TyrionBillMatchSignalV1] = Field(min_length=4, max_length=4)
    receipt_association: TyrionReceiptAssociationV2 | None = Field(
        default=None,
        alias="receiptAssociation",
    )

    _validate_transaction_ref = field_validator("transaction_ref")(_validate_source_reference)


class TyrionBillMatchSearchContextV2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_by_alias=True,
        validate_by_name=False,
    )

    anchor_date: date = Field(alias="anchorDate")
    window_start: date = Field(alias="windowStart")
    window_end: date = Field(alias="windowEnd")
    date_window_days: int = Field(alias="dateWindowDays", ge=1, le=365)
    amount_tolerance_minor: int = Field(alias="amountToleranceMinor", ge=0)
    source_as_of: datetime = Field(alias="sourceAsOf")
    searched_at: datetime = Field(alias="searchedAt")
    eligible_account_count: int = Field(alias="eligibleAccountCount", ge=0)
    candidate_pool_size: int = Field(alias="candidatePoolSize", ge=0)


class TyrionBillMatchResponseV2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_by_alias=True,
        validate_by_name=False,
    )

    contract_version: Literal["2.0"] = Field(alias="contractVersion")
    request_ref: str = Field(alias="requestRef", min_length=8, max_length=200)
    bill_ref: str = Field(
        alias="billRef",
        min_length=1,
        max_length=160,
        pattern=_SOURCE_REFERENCE_PATTERN,
    )
    match_status: Literal["matched", "noMatch", "ambiguous"] = Field(alias="matchStatus")
    payment_status: Literal["paid", "pending", "unmatched", "ambiguous"] = Field(
        alias="paymentStatus"
    )
    selected_transaction_ref: str | None = Field(
        alias="selectedTransactionRef",
        default=None,
        min_length=1,
        max_length=160,
        pattern=_SOURCE_REFERENCE_PATTERN,
    )
    candidates: list[TyrionBillMatchCandidateV2] = Field(max_length=25)
    search_context: TyrionBillMatchSearchContextV2 = Field(alias="searchContext")

    @model_validator(mode="after")
    def validate_decision(self) -> TyrionBillMatchResponseV2:
        refs = [candidate.transaction_ref for candidate in self.candidates]
        if len(refs) != len(set(refs)):
            raise ValueError("candidates must contain unique transactionRef values")
        if [candidate.rank for candidate in self.candidates] != list(
            range(1, len(self.candidates) + 1)
        ):
            raise ValueError("candidate ranks must be contiguous and ordered")
        if self.match_status == "matched":
            if (
                self.selected_transaction_ref is None
                or not self.candidates
                or self.candidates[0].transaction_ref != self.selected_transaction_ref
            ):
                raise ValueError("matched response must select the first candidate")
            expected_payment = (
                "paid" if self.candidates[0].transaction_state == "posted" else "pending"
            )
            if self.payment_status != expected_payment:
                raise ValueError("paymentStatus must agree with selected transaction state")
        elif self.selected_transaction_ref is not None:
            raise ValueError("selectedTransactionRef is only valid for matched responses")
        elif self.match_status == "noMatch" and self.payment_status != "unmatched":
            raise ValueError("noMatch requires paymentStatus unmatched")
        elif self.match_status == "ambiguous" and self.payment_status != "ambiguous":
            raise ValueError("ambiguous match requires paymentStatus ambiguous")
        return self


class PaymentCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["2.0"] = "2.0"
    run_id: str
    status: Literal["checking", "completed", "source_unavailable"]
    idempotent: bool
    payment: dict


class PaymentConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    contract_version: Literal["1.0"] = "1.0"
    revision: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")


class RevisionConflictError(ValueError):
    pass


class IdempotencyConflictError(ValueError):
    pass


class TyrionBillMatchingClient:
    def __init__(self, *, url: str, api_token: str | None = None):
        self.url = url
        self.api_token = api_token

    async def check(self, request: TyrionBillCheckRequestV2) -> TyrionBillMatchResponseV2:
        headers = {"Accept": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self.url,
                headers=headers,
                json=request.model_dump(by_alias=True, mode="json"),
            )
            response.raise_for_status()
        return TyrionBillMatchResponseV2.model_validate(response.json())


class PaymentReconciliationService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _action_revision(action: Action, obligation: Obligation) -> str:
        return f"v1:{action.version or 1}:{obligation.revision or 1}"

    def payment_projection(self, action_id: int) -> dict:
        action = self.db.query(Action).filter_by(id=action_id).first()
        if action is None:
            raise KeyError("action_not_found")
        if (action.action_type or "").upper() != "PAY" or not action.obligation_id:
            raise ValueError("payment_check_not_eligible")
        obligation = self.db.query(Obligation).filter_by(id=action.obligation_id).one()
        revision = self._action_revision(action, obligation)
        latest_run = (
            self.db.query(PaymentCheckRun)
            .filter_by(obligation_id=obligation.id)
            .order_by(PaymentCheckRun.updated_at.desc(), PaymentCheckRun.created_at.desc())
            .first()
        )
        cases = (
            self.db.query(PaymentReconciliationCase)
            .filter_by(obligation_id=obligation.id, active=True)
            .order_by(
                PaymentReconciliationCase.updated_at.desc(),
                PaymentReconciliationCase.created_at.desc(),
            )
            .all()
        )
        evidence_rows = (
            self.db.query(PaymentEvidence, ObligationPaymentEdge)
            .join(
                ObligationPaymentEdge,
                ObligationPaymentEdge.evidence_id == PaymentEvidence.id,
            )
            .filter(
                ObligationPaymentEdge.obligation_id == obligation.id,
                ObligationPaymentEdge.state.in_(("active", "proposed")),
                PaymentEvidence.active.is_(True),
                PaymentEvidence.source_system == "tyrion_bill_match",
            )
            .order_by(PaymentEvidence.source_as_of.desc(), PaymentEvidence.id)
            .limit(10)
            .all()
        )
        latest_evidence = evidence_rows[0][0] if evidence_rows else None
        state = "unchecked"
        if latest_run and latest_run.status == "checking":
            state = "checking"
        else:
            case_states = {
                "ambiguous": "multiple_matches",
                "unmatched": "no_match",
                "partial_payment": "partial_payment",
                "source_unavailable": "source_unavailable",
                "projection_failed": "source_unavailable",
                "stale": "stale",
                "conflict": "conflict",
                "double_payment": "possible_double_payment",
            }
            exceptional = next(
                (case_states[case.case_kind] for case in cases if case.case_kind in case_states),
                None,
            )
            if exceptional:
                state = exceptional
            elif latest_evidence and latest_evidence.match_state == "matched":
                state = (
                    "posted_match"
                    if latest_evidence.payment_status == "paid"
                    else "pending_match"
                    if latest_evidence.payment_status == "pending"
                    else "no_match"
                )
            elif latest_evidence and latest_evidence.match_state == "no_match":
                state = "no_match"
        if (
            action.status == "completed"
            and obligation.status == "settled"
            and any(
                edge.manual_precedence and evidence.payment_status == "paid"
                for evidence, edge in evidence_rows
            )
        ):
            state = "confirmed"

        checked_at = None
        raw_search_context = (
            (latest_run.response_payload or {}).get("searchContext")
            if latest_run and latest_run.response_payload
            else None
        )
        if raw_search_context and raw_search_context.get("searchedAt"):
            checked_at = raw_search_context["searchedAt"]
        elif latest_run and latest_run.updated_at:
            checked_at = latest_run.updated_at.isoformat()
        elif latest_evidence:
            checked_at = latest_evidence.source_as_of.isoformat()
        stale_after = (
            (latest_evidence.source_as_of + timedelta(days=7)).isoformat()
            if latest_evidence
            else None
        )
        messages = {
            "unchecked": "Payment has not been checked.",
            "checking": "Checking configured eligible financial accounts.",
            "posted_match": "A posted payment candidate requires confirmation.",
            "pending_match": "A matching transaction is still pending.",
            "multiple_matches": "Multiple plausible payment candidates require review.",
            "no_match": "No matching transaction was found.",
            "partial_payment": "Posted evidence covers only part of the obligation.",
            "source_unavailable": "The payment source could not complete the check.",
            "stale": "The latest payment result is stale.",
            "conflict": "Payment evidence conflicts with another obligation.",
            "possible_double_payment": "Evidence indicates a possible additional payment.",
            "confirmed": "Payment was confirmed and the obligation was completed.",
        }
        candidates = [
            {
                "id": evidence.id,
                "payee": evidence.payee_hint,
                "amount": (
                    evidence.amount_minor / 100 if evidence.amount_minor is not None else None
                ),
                "currency": evidence.currency,
                "transaction_date": (
                    evidence.evidence_date.isoformat() if evidence.evidence_date else None
                ),
                "status": (
                    evidence.source_state
                    if evidence.source_state in {"posted", "pending"}
                    else (
                        "posted"
                        if evidence.payment_status == "paid"
                        else "pending"
                        if evidence.payment_status == "pending"
                        else None
                    )
                ),
                "account_display_name": evidence.account_display_name,
                "account_last_four": evidence.account_last_four,
                "confidence": evidence.confidence,
                "score": (
                    evidence.score_basis_points / 100
                    if evidence.score_basis_points is not None
                    else None
                ),
                "rank": evidence.rank,
                "score_gap_basis_points": evidence.score_gap_basis_points,
                "match_reasons": evidence.reason_codes,
                "receipt_association": evidence.receipt_association,
            }
            for evidence, _edge in evidence_rows
        ]
        confirmation_case = next(
            (
                case
                for case in cases
                if case.case_kind == "confirmation_required"
                and case.evidence_id
                and any(evidence.id == case.evidence_id for evidence, _edge in evidence_rows)
            ),
            None,
        )
        confirmable = state == "posted_match" and confirmation_case is not None
        search_context = None
        if raw_search_context:
            search_context = {
                "anchor_date": raw_search_context.get("anchorDate"),
                "window_start": raw_search_context.get("windowStart"),
                "window_end": raw_search_context.get("windowEnd"),
                "amount_tolerance": (
                    raw_search_context["amountToleranceMinor"] / 100
                    if raw_search_context.get("amountToleranceMinor") is not None
                    else None
                ),
                "date_tolerance_days": raw_search_context.get("dateWindowDays"),
                "source_as_of": raw_search_context.get("sourceAsOf"),
                "searched_at": raw_search_context.get("searchedAt"),
                "eligible_account_count": raw_search_context.get("eligibleAccountCount"),
                "candidate_pool_size": raw_search_context.get("candidatePoolSize"),
            }
        return {
            "state": state,
            "checked_at": checked_at,
            "stale_after": stale_after,
            "confirmed_at": (
                action.completed_at.isoformat() if state == "confirmed" and action.completed_at else None
            ),
            "source": "tyrion_bill_match",
            "revision": revision,
            "message": messages[state],
            "candidates": candidates,
            "search_context": search_context,
            "_confirmation_case_id": confirmation_case.id if confirmable else None,
            "_confirmation_case_revision": (
                confirmation_case.revision if confirmable else None
            ),
        }

    def start_check(
        self,
        action_id: int,
        request: PaymentCheckRequest,
    ) -> tuple[PaymentCheckRun, TyrionBillCheckRequestV2, bool]:
        action = self.db.query(Action).filter_by(id=action_id).first()
        if action is None:
            raise KeyError("action_not_found")
        if (action.action_type or "").upper() != "PAY" or not action.obligation_id:
            raise ValueError("payment_check_not_eligible")
        obligation = self.db.query(Obligation).filter_by(id=action.obligation_id).one()
        if action.amount is None or action.amount <= 0:
            raise ValueError("payment_check_amount_required")
        payee = action.payee_or_owner or action.correspondent
        if not payee:
            raise ValueError("payment_check_payee_required")
        expected_amount_minor = round(action.amount * 100)
        run_id = _stable_id("payment_check", obligation.id, request.idempotency_key)
        tyrion_request = TyrionBillCheckRequestV2(
            requestRef=run_id,
            billRef=f"paperless:{action.document_id}",
            payee=payee,
            expectedAmountMinor=expected_amount_minor,
            currency=obligation.currency or "USD",
            dueDate=action.due_date,
            billDate=action.document_date,
            reference=action.reference,
        )
        request_payload = tyrion_request.model_dump(by_alias=True, mode="json")
        request_digest = _digest(request_payload)
        existing = (
            self.db.query(PaymentCheckRun)
            .filter_by(
                obligation_id=obligation.id,
                idempotency_key=request.idempotency_key,
            )
            .first()
        )
        if existing:
            if existing.request_digest != request_digest:
                raise IdempotencyConflictError("idempotency_key_payload_conflict")
            return existing, tyrion_request, True
        if request.revision != self._action_revision(action, obligation):
            raise RevisionConflictError("payment_check_revision_conflict")
        expected_obligation_revision = obligation.revision or 1
        claimed = (
            self.db.query(Obligation)
            .filter(
                Obligation.id == obligation.id,
                Obligation.revision == expected_obligation_revision,
            )
            .update(
                {Obligation.revision: expected_obligation_revision + 1},
                synchronize_session=False,
            )
        )
        if claimed != 1:
            self.db.rollback()
            raise RevisionConflictError("payment_check_revision_conflict")
        run = PaymentCheckRun(
            id=run_id,
            obligation_id=obligation.id,
            action_id=action.id,
            idempotency_key=request.idempotency_key,
            request_digest=request_digest,
            expected_revision=expected_obligation_revision,
            status="checking",
            request_payload=request_payload,
        )
        self.db.add(run)
        self.db.add(
            PaymentRelationshipEvent(
                id=_stable_id("event", run.id, "checking"),
                action_scope=f"payment_check:{obligation.id}",
                idempotency_key=request.idempotency_key,
                obligation_id=obligation.id,
                action="check_started",
                actor="user",
                provenance="user",
                previous_state={},
                new_state={"state": "checking", "run_id": run.id},
                expected_revision=expected_obligation_revision,
            )
        )
        self.db.commit()
        return run, tyrion_request, False

    def finish_check(
        self,
        run_id: str,
        response: TyrionBillMatchResponseV2,
        *,
        connector_ref: str,
    ) -> dict:
        run = self.db.query(PaymentCheckRun).filter_by(id=run_id).one()
        action = self.db.query(Action).filter_by(id=run.action_id).one()
        if response.request_ref != run.id:
            raise ValueError("tyrion_request_ref_mismatch")
        if response.bill_ref != f"paperless:{action.document_id}":
            raise ValueError("tyrion_bill_ref_mismatch")
        match_state: MatchState = {
            "matched": "matched",
            "noMatch": "no_match",
            "ambiguous": "ambiguous",
        }[response.match_status]
        candidates = (
            response.candidates[:1]
            if response.match_status == "matched"
            else response.candidates
            if response.candidates
            else [None]
        )
        evidence: list[NormalizedPaymentEvidence] = []
        for candidate in candidates:
            reason_codes = (
                [
                    f"{signal.kind}_{signal.strength}".lower()
                    for signal in candidate.signals
                ]
                if candidate
                else [f"bill_match_{match_state}"]
            )
            evidence.append(
                NormalizedPaymentEvidence(
                    evidence_ref=(
                        candidate.transaction_ref
                        if candidate
                        else f"{response.bill_ref}:{run.id}"
                    ),
                    canonical_document_ref=f"paperless:{action.document_id}",
                    transaction_ref=candidate.transaction_ref if candidate else None,
                    kind=(
                        "posted_transaction"
                        if candidate and candidate.transaction_state == "posted"
                        else "pending_transaction"
                        if candidate
                        else "bill"
                    ),
                    payee_hint=candidate.payee if candidate else action.payee_or_owner,
                    account_display_name=(
                        candidate.account_display_name if candidate else None
                    ),
                    account_last_four=candidate.account_last_four if candidate else None,
                    score_basis_points=candidate.score_basis_points if candidate else None,
                    rank=candidate.rank if candidate else None,
                    score_gap_basis_points=(
                        candidate.score_gap_basis_points if candidate else None
                    ),
                    receipt_association=(
                        candidate.receipt_association.model_dump(
                            by_alias=True,
                            mode="json",
                        )
                        if candidate and candidate.receipt_association
                        else None
                    ),
                    amount_minor=(
                        candidate.amount_minor
                        if candidate
                        else round((action.amount or 0) * 100)
                    ),
                    currency=(
                        candidate.currency
                        if candidate
                        else self.db.query(Obligation)
                        .filter_by(id=run.obligation_id)
                        .one()
                        .currency
                        or "USD"
                    ),
                    evidence_date=candidate.transaction_date if candidate else None,
                    source_state=(
                        candidate.transaction_state if candidate else match_state
                    ),
                    match_state=match_state,
                    payment_status=response.payment_status,
                    confidence=candidate.confidence if candidate else None,
                    reason_codes=reason_codes,
                )
            )
        snapshot = PaymentEvidenceSnapshot(
            source_system="tyrion_bill_match",
            connector_ref=connector_ref,
            source_generation=run.id,
            source_as_of=response.search_context.source_as_of,
            completeness="complete",
            obligation_id=run.obligation_id,
            evidence=evidence,
        )
        self._retire_prior_check_cases(run.obligation_id)
        self.ingest_snapshot(snapshot)
        run = self.db.query(PaymentCheckRun).filter_by(id=run_id).one()
        run.status = "completed"
        run.response_payload = response.model_dump(by_alias=True, mode="json")
        projection = self.payment_projection(action.id)
        run.result = {key: value for key, value in projection.items() if not key.startswith("_")}
        self.db.commit()
        return projection

    def fail_check(
        self,
        run_id: str,
        *,
        connector_ref: str,
        error_code: str,
    ) -> dict:
        run = self.db.query(PaymentCheckRun).filter_by(id=run_id).one()
        action = self.db.query(Action).filter_by(id=run.action_id).one()
        obligation = self.db.query(Obligation).filter_by(id=run.obligation_id).one()
        snapshot = PaymentEvidenceSnapshot(
            source_system="tyrion_bill_match",
            connector_ref=connector_ref,
            source_generation=run.id,
            source_as_of=datetime.now(UTC),
            completeness="partial",
            obligation_id=obligation.id,
            evidence=[
                NormalizedPaymentEvidence(
                    evidence_ref=f"{run.id}:source-unavailable",
                    canonical_document_ref=f"paperless:{action.document_id}",
                    kind="bill",
                    payee_hint=action.payee_or_owner or action.correspondent,
                    amount_minor=round((action.amount or 0) * 100) or None,
                    currency=obligation.currency if action.amount else None,
                    source_state="source_unavailable",
                    match_state="source_unavailable",
                    payment_status="unknown",
                    reason_codes=[error_code],
                )
            ],
        )
        self._retire_prior_check_cases(run.obligation_id)
        self.ingest_snapshot(snapshot)
        run = self.db.query(PaymentCheckRun).filter_by(id=run_id).one()
        run.status = "source_unavailable"
        run.error_code = error_code
        projection = self.payment_projection(action.id)
        run.result = {key: value for key, value in projection.items() if not key.startswith("_")}
        self.db.commit()
        return projection

    def ingest_snapshot(self, snapshot: PaymentEvidenceSnapshot) -> PaymentSnapshotResult:
        obligation = self.db.query(Obligation).filter_by(id=snapshot.obligation_id).first()
        if obligation is None:
            raise KeyError("obligation_not_found")
        if snapshot.source_system == "tyrion_bill_match" and snapshot.evidence:
            normalized_bill = snapshot.evidence[0]
            if (
                obligation.expected_amount_minor is None
                and normalized_bill.amount_minor is not None
            ):
                obligation.expected_amount_minor = normalized_bill.amount_minor
                obligation.currency = normalized_bill.currency

        payload = snapshot.model_dump(mode="json")
        payload_digest = _digest(payload)
        prior = (
            self.db.query(PaymentEvidenceGeneration)
            .filter_by(
                source_system=snapshot.source_system,
                connector_ref=snapshot.connector_ref,
                obligation_id=snapshot.obligation_id,
                source_generation=snapshot.source_generation,
            )
            .first()
        )
        if prior:
            if prior.payload_digest != payload_digest:
                raise ValueError("source_generation_payload_conflict")
            return self._snapshot_result(snapshot, "replayed")

        cursor = (
            self.db.query(PaymentEvidenceSource)
            .filter_by(
                source_system=snapshot.source_system,
                connector_ref=snapshot.connector_ref,
                obligation_id=snapshot.obligation_id,
            )
            .first()
        )
        stale = bool(cursor and snapshot.source_as_of < cursor.source_as_of.replace(tzinfo=UTC))
        generation = PaymentEvidenceGeneration(
            source_system=snapshot.source_system,
            connector_ref=snapshot.connector_ref,
            obligation_id=snapshot.obligation_id,
            source_generation=snapshot.source_generation,
            source_as_of=snapshot.source_as_of.replace(tzinfo=None),
            payload_digest=payload_digest,
            disposition="stale" if stale else "applied",
        )
        self.db.add(generation)
        if stale:
            self._upsert_case(
                obligation,
                None,
                "stale",
                {"source_generation": snapshot.source_generation},
                snapshot.source_as_of,
            )
            self.db.commit()
            return self._snapshot_result(snapshot, "stale")

        incoming_refs: set[str] = set()
        for item in snapshot.evidence:
            incoming_refs.add(item.evidence_ref)
            evidence = self._upsert_evidence(snapshot, item, payload_digest)
            self._reconcile_automatic_edge(obligation, evidence)

        if snapshot.completeness == "complete":
            missing = (
                self.db.query(PaymentEvidence, ObligationPaymentEdge)
                .join(
                    ObligationPaymentEdge,
                    ObligationPaymentEdge.evidence_id == PaymentEvidence.id,
                )
                .filter(
                    PaymentEvidence.source_system == snapshot.source_system,
                    PaymentEvidence.connector_ref == snapshot.connector_ref,
                    ObligationPaymentEdge.obligation_id == obligation.id,
                    ObligationPaymentEdge.state.in_(("proposed", "active")),
                )
                .all()
            )
            for evidence, edge in missing:
                if evidence.evidence_ref in incoming_refs:
                    continue
                if not edge.manual_precedence:
                    self._transition_edge(
                        edge,
                        "removed",
                        actor="source",
                        action="source_removed",
                        idempotency_key=f"source:{snapshot.source_generation}:{edge.id}",
                    )

        if cursor is None:
            cursor = PaymentEvidenceSource(
                source_system=snapshot.source_system,
                connector_ref=snapshot.connector_ref,
                obligation_id=snapshot.obligation_id,
                source_generation=snapshot.source_generation,
                source_as_of=snapshot.source_as_of.replace(tzinfo=None),
            )
            self.db.add(cursor)
        else:
            cursor.source_generation = snapshot.source_generation
            cursor.source_as_of = snapshot.source_as_of.replace(tzinfo=None)
        self._recompute_obligation(obligation)
        self.db.commit()
        return self._snapshot_result(snapshot, "applied")

    def list_cases(
        self,
        *,
        active_only: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        query = self.db.query(PaymentReconciliationCase)
        if active_only:
            query = query.filter_by(active=True)
        rows = (
            query.order_by(
                PaymentReconciliationCase.updated_at.desc(),
                PaymentReconciliationCase.id,
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [self._case_dict(row) for row in rows]

    def get_case(self, case_id: str) -> dict:
        case = self.db.query(PaymentReconciliationCase).filter_by(id=case_id).first()
        if case is None:
            raise KeyError("payment_reconciliation_case_not_found")
        return self._case_dict(case, include_history=True)

    def act(
        self,
        case_id: str,
        request: PaymentReviewAction,
        *,
        actor: str,
        complete_action_id: int | None = None,
        expected_action_version: int | None = None,
    ) -> dict:
        case = self.db.query(PaymentReconciliationCase).filter_by(id=case_id).first()
        if case is None:
            raise KeyError("payment_reconciliation_case_not_found")
        action_to_complete = None
        if complete_action_id is not None:
            action_to_complete = (
                self.db.query(Action).filter_by(id=complete_action_id).first()
            )
            if action_to_complete is None:
                raise KeyError("action_not_found")
            if action_to_complete.obligation_id != case.obligation_id:
                raise ValueError("payment_action_obligation_mismatch")
            if (
                expected_action_version is not None
                and (action_to_complete.version or 1) != expected_action_version
            ):
                raise RevisionConflictError("payment_check_revision_conflict")
        payload = request.model_dump(mode="json", exclude_none=True)
        payload_digest = _digest(payload)
        prior = (
            self.db.query(PaymentActionReceipt)
            .filter_by(case_id=case_id, idempotency_key=request.idempotency_key)
            .first()
        )
        if prior:
            if prior.payload_digest != payload_digest:
                raise IdempotencyConflictError("idempotency_key_payload_conflict")
            return {**prior.result, "idempotent": True}
        claimed = (
            self.db.query(PaymentReconciliationCase)
            .filter(
                PaymentReconciliationCase.id == case_id,
                PaymentReconciliationCase.revision == request.expected_revision,
            )
            .update(
                {PaymentReconciliationCase.revision: request.expected_revision + 1},
                synchronize_session=False,
            )
        )
        if claimed != 1:
            self.db.rollback()
            replay = (
                self.db.query(PaymentActionReceipt)
                .filter_by(case_id=case_id, idempotency_key=request.idempotency_key)
                .first()
            )
            if replay and replay.payload_digest == payload_digest:
                return {**replay.result, "idempotent": True}
            raise RevisionConflictError("payment_reconciliation_revision_conflict")
        self.db.expire_all()
        case = self.db.query(PaymentReconciliationCase).filter_by(id=case_id).one()

        obligation = self.db.query(Obligation).filter_by(id=case.obligation_id).one()
        if request.action in {
            "confirm",
            "reject",
            "clear",
            "mark_not_applicable",
            "replace",
        }:
            edge = self._edge_for_action(obligation.id, request.evidence_id)
            if request.action == "confirm":
                conflicting_edge = (
                    self.db.query(ObligationPaymentEdge)
                    .filter(
                        ObligationPaymentEdge.evidence_id == edge.evidence_id,
                        ObligationPaymentEdge.obligation_id != obligation.id,
                        ObligationPaymentEdge.state == "active",
                    )
                    .first()
                )
                if conflicting_edge:
                    raise ValueError("payment_evidence_already_allocated")
                if edge.state == "rejected":
                    edge.state = "proposed"
                edge.allocated_amount_minor = request.allocated_amount_minor
                edge.manual_precedence = True
                edge.provenance = "user"
                self._transition_edge(
                    edge,
                    "active",
                    actor=actor,
                    action="confirmed",
                    idempotency_key=request.idempotency_key,
                    expected_revision=request.expected_revision,
                )
            elif request.action == "reject":
                edge.manual_precedence = True
                edge.provenance = "user"
                self._transition_edge(
                    edge,
                    "rejected",
                    actor=actor,
                    action="rejected",
                    idempotency_key=request.idempotency_key,
                    expected_revision=request.expected_revision,
                )
            elif request.action == "clear":
                edge.manual_precedence = True
                edge.provenance = "user"
                self._transition_edge(
                    edge,
                    "removed",
                    actor=actor,
                    action="cleared",
                    idempotency_key=request.idempotency_key,
                    expected_revision=request.expected_revision,
                )
            elif request.action == "mark_not_applicable":
                edge.manual_precedence = True
                edge.provenance = "user"
                self._transition_edge(
                    edge,
                    "rejected",
                    actor=actor,
                    action="not_applicable",
                    idempotency_key=request.idempotency_key,
                    expected_revision=request.expected_revision,
                )
                self.db.query(PaymentEvidence).filter_by(
                    id=edge.evidence_id
                ).one().match_state = "not_applicable"
            else:
                replacement = self._edge_for_action(obligation.id, request.replacement_evidence_id)
                edge.manual_precedence = True
                replacement.manual_precedence = True
                replacement.provenance = "user"
                self._transition_edge(
                    edge,
                    "replaced",
                    actor=actor,
                    action="replaced",
                    idempotency_key=request.idempotency_key,
                    expected_revision=request.expected_revision,
                )
                self._transition_edge(
                    replacement,
                    "active",
                    actor=actor,
                    action="confirmed",
                    idempotency_key=f"{request.idempotency_key}:replacement",
                    expected_revision=request.expected_revision,
                )
                edge.replaced_by_edge_id = replacement.id
            case.state = "resolved"
            case.active = False
            case.attention_state = "settled"
        elif request.action == "defer":
            case.state = "deferred"
            case.deferred_until = request.defer_until.replace(tzinfo=None)
            self._append_case_event(case, request, actor)
        elif request.action == "reopen":
            case.state = "open"
            case.active = True
            case.deferred_until = None
            self._append_case_event(case, request, actor)
        elif request.action == "deliver_attention":
            case.attention_state = "delivered"
            case.attention_delivery_ref = request.attention_delivery_ref
            self._append_case_event(case, request, actor)
        elif request.action == "acknowledge_attention":
            case.attention_state = "acknowledged"
            self._append_case_event(case, request, actor)

        self._recompute_obligation(obligation)
        if action_to_complete is not None:
            previous_status = action_to_complete.status
            transition_action_status(action_to_complete, "completed")
            action_to_complete.version = (action_to_complete.version or 1) + 1
            self.db.add(
                ActionLifecycleEvent(
                    action_id=action_to_complete.id,
                    event_type="payment_confirmed_and_completed",
                    previous_status=previous_status,
                    new_status="completed",
                    action_version=action_to_complete.version,
                    actor=actor,
                    details={
                        "payment_case_id": case.id,
                        "payment_evidence_id": request.evidence_id,
                    },
                )
            )
            obligation.status = "settled"
            obligation.completion_suggested = False
            obligation.suggestion_reason = None
        self.db.flush()
        authoritative = self._case_dict(case, include_history=True)
        result = {
            "contract_version": "1.0",
            "action": request.action,
            "case": authoritative,
            "source_acknowledgement": "not_required",
            "authoritative_read_back": True,
            "idempotent": False,
        }
        self.db.add(
            PaymentActionReceipt(
                case_id=case_id,
                idempotency_key=request.idempotency_key,
                payload_digest=payload_digest,
                result=result,
            )
        )
        self.db.commit()
        return result

    def confirm_and_complete(
        self,
        action_id: int,
        request: PaymentConfirmRequest,
        *,
        actor: str,
    ) -> dict:
        action = self.db.query(Action).filter_by(id=action_id).first()
        if action is None:
            raise KeyError("action_not_found")
        if not action.obligation_id:
            raise ValueError("payment_check_not_eligible")
        obligation = self.db.query(Obligation).filter_by(id=action.obligation_id).one()
        obligation_case_ids = select(PaymentReconciliationCase.id).where(
            PaymentReconciliationCase.obligation_id == obligation.id
        )
        prior = (
            self.db.query(PaymentActionReceipt)
            .filter(
                PaymentActionReceipt.idempotency_key == request.idempotency_key,
                PaymentActionReceipt.case_id.in_(obligation_case_ids),
            )
            .first()
        )
        if prior:
            return {**prior.result, "idempotent": True}
        if request.revision != self._action_revision(action, obligation):
            raise RevisionConflictError("payment_check_revision_conflict")
        projection = self.payment_projection(action.id)
        case_id = projection.get("_confirmation_case_id")
        case_revision = projection.get("_confirmation_case_revision")
        posted = next(
            (
                candidate
                for candidate in projection["candidates"]
                if candidate["status"] == "posted"
            ),
            None,
        )
        if (
            projection["state"] != "posted_match"
            or not case_id
            or case_revision is None
            or posted is None
        ):
            raise ValueError("payment_match_not_confirmable")
        result = self.act(
            case_id,
            PaymentReviewAction(
                action="confirm",
                expected_revision=case_revision,
                idempotency_key=request.idempotency_key,
                evidence_id=posted["id"],
                allocated_amount_minor=(
                    round(posted["amount"] * 100)
                    if posted["amount"] is not None
                    else None
                ),
            ),
            actor=actor,
            complete_action_id=action.id,
            expected_action_version=action.version or 1,
        )
        return result

    def _retire_prior_check_cases(self, obligation_id: str) -> None:
        for case in (
            self.db.query(PaymentReconciliationCase)
            .filter(
                PaymentReconciliationCase.obligation_id == obligation_id,
                PaymentReconciliationCase.active.is_(True),
                PaymentReconciliationCase.case_kind.in_(
                    (
                        "confirmation_required",
                        "unmatched",
                        "ambiguous",
                        "source_unavailable",
                        "projection_failed",
                        "stale",
                    )
                ),
            )
            .all()
        ):
            case.active = False
            case.state = "resolved"
            case.attention_state = "settled"
            case.revision += 1

    def record_legacy_receipt(
        self,
        obligation: Obligation,
        *,
        document_id: int,
        amount_minor: int | None,
        evidence_date: date | None,
        confidence: float,
        manual: bool,
        rejected: bool = False,
    ) -> PaymentEvidence:
        """Idempotently project an existing receipt link into normalized evidence."""
        evidence_ref = f"legacy-receipt-{document_id}"
        evidence = (
            self.db.query(PaymentEvidence)
            .filter_by(
                source_system="legacy_receipt",
                connector_ref="owl-action-queue",
                evidence_ref=evidence_ref,
            )
            .first()
        )
        if evidence is None:
            evidence = PaymentEvidence(
                id=_stable_id("evidence", "legacy_receipt", evidence_ref),
                source_system="legacy_receipt",
                connector_ref="owl-action-queue",
                evidence_ref=evidence_ref,
                kind="receipt",
                amount_minor=amount_minor,
                evidence_date=evidence_date,
                reference_present=False,
                source_state="linked",
                match_state="matched",
                payment_status="paid",
                confidence="high" if confidence >= 0.75 else "medium",
                reason_codes=["legacy_receipt_link"],
                source_generation="legacy-backfill-v1",
                source_as_of=_utcnow(),
                active=not rejected,
                payload_digest=_digest(
                    {
                        "document_id": document_id,
                        "amount_minor": amount_minor,
                        "manual": manual,
                    }
                ),
            )
            self.db.add(evidence)
            self.db.flush()
        edge = (
            self.db.query(ObligationPaymentEdge)
            .filter_by(obligation_id=obligation.id, evidence_id=evidence.id)
            .first()
        )
        if edge is None:
            edge = ObligationPaymentEdge(
                id=_stable_id("edge", obligation.id, evidence.id),
                obligation_id=obligation.id,
                evidence_id=evidence.id,
                state="rejected" if rejected else "active",
                provenance="user" if manual else "imported_legacy",
                allocated_amount_minor=amount_minor,
                confidence=evidence.confidence,
                reason_codes=evidence.reason_codes,
                manual_precedence=manual or rejected,
                removed_at=_utcnow() if rejected else None,
            )
            self.db.add(edge)
            self.db.flush()
            self._append_event(
                edge,
                action="rejected" if rejected else "imported",
                actor="migration" if not manual else "user",
                idempotency_key=f"legacy:{obligation.id}:{document_id}",
                previous={},
            )
        return evidence

    def backfill_legacy_receipts(self) -> int:
        """Backfill receipt timeline rows without deleting or rewriting legacy records."""
        from .database import ObligationDocument

        rows = self.db.query(ObligationDocument).filter_by(role="receipt").all()
        for row in rows:
            obligation = self.db.query(Obligation).filter_by(id=row.obligation_id).first()
            if obligation is None:
                continue
            self.record_legacy_receipt(
                obligation,
                document_id=row.document_id,
                amount_minor=round(row.amount * 100) if row.amount is not None else None,
                evidence_date=row.document_date,
                confidence=row.confidence,
                manual=row.source == "user_link",
                rejected=bool(row.excluded),
            )
        self.db.flush()
        return len(rows)

    def _upsert_evidence(
        self,
        snapshot: PaymentEvidenceSnapshot,
        item: NormalizedPaymentEvidence,
        snapshot_digest: str,
    ) -> PaymentEvidence:
        evidence = (
            self.db.query(PaymentEvidence)
            .filter_by(
                source_system=snapshot.source_system,
                connector_ref=snapshot.connector_ref,
                evidence_ref=item.evidence_ref,
            )
            .first()
        )
        item_payload = item.model_dump(mode="json")
        item_digest = _digest(
            {
                "snapshot": snapshot_digest,
                "evidence": item_payload,
            }
        )
        values = {
            "canonical_document_ref": item.canonical_document_ref,
            "transaction_ref": item.transaction_ref,
            "receipt_ref": item.receipt_ref,
            "kind": item.kind,
            "payee_hint": item.payee_hint,
            "account_display_name": item.account_display_name,
            "account_last_four": item.account_last_four,
            "score_basis_points": item.score_basis_points,
            "rank": item.rank,
            "score_gap_basis_points": item.score_gap_basis_points,
            "receipt_association": item.receipt_association,
            "amount_minor": item.amount_minor,
            "currency": item.currency,
            "evidence_date": item.evidence_date,
            "reference_present": item.reference_present,
            "reference_digest": item.reference_digest,
            "source_state": item.source_state,
            "match_state": item.match_state,
            "payment_status": item.payment_status,
            "confidence": item.confidence,
            "reason_codes": item.reason_codes,
            "source_generation": snapshot.source_generation,
            "source_revision": item.source_revision,
            "source_as_of": snapshot.source_as_of.replace(tzinfo=None),
            "active": True,
            "payload_digest": item_digest,
        }
        if evidence is None:
            evidence = PaymentEvidence(
                id=_stable_id(
                    "evidence",
                    snapshot.source_system,
                    snapshot.connector_ref,
                    item.evidence_ref,
                ),
                source_system=snapshot.source_system,
                connector_ref=snapshot.connector_ref,
                evidence_ref=item.evidence_ref,
                **values,
            )
            self.db.add(evidence)
            self.db.flush()
        else:
            for name, value in values.items():
                setattr(evidence, name, value)
        return evidence

    def _reconcile_automatic_edge(self, obligation: Obligation, evidence: PaymentEvidence) -> None:
        edge = (
            self.db.query(ObligationPaymentEdge)
            .filter_by(obligation_id=obligation.id, evidence_id=evidence.id)
            .first()
        )
        if edge and edge.manual_precedence:
            return
        conflicting_edge = (
            self.db.query(ObligationPaymentEdge)
            .filter(
                ObligationPaymentEdge.evidence_id == evidence.id,
                ObligationPaymentEdge.obligation_id != obligation.id,
                ObligationPaymentEdge.state == "active",
            )
            .first()
        )
        desired = (
            "active"
            if evidence.match_state == "matched"
            and evidence.payment_status in {"paid", "pending"}
            and evidence.confidence == "high"
            and conflicting_edge is None
            else "proposed"
        )
        if edge is None:
            edge = ObligationPaymentEdge(
                id=_stable_id("edge", obligation.id, evidence.id),
                obligation_id=obligation.id,
                evidence_id=evidence.id,
                state="proposed",
                provenance="automatic",
                allocated_amount_minor=evidence.amount_minor,
                confidence=evidence.confidence,
                reason_codes=evidence.reason_codes,
            )
            self.db.add(edge)
            self.db.flush()
            self._append_event(
                edge,
                action="proposed",
                actor="source",
                idempotency_key=f"source:{evidence.source_generation}:{edge.id}:proposed",
                previous={},
            )
        elif not edge.manual_precedence:
            edge.allocated_amount_minor = evidence.amount_minor
            edge.confidence = evidence.confidence
            edge.reason_codes = evidence.reason_codes
        if edge.state != desired:
            self._transition_edge(
                edge,
                desired,
                actor="source",
                action="source_verified" if desired == "active" else "proposed",
                idempotency_key=f"source:{evidence.source_generation}:{edge.id}:{desired}",
            )
        case_kind = "conflict" if conflicting_edge else self._case_kind(evidence)
        self._resolve_evidence_cases(
            obligation.id,
            evidence.id,
            except_kind=case_kind,
        )
        if case_kind:
            self._upsert_case(
                obligation,
                evidence,
                case_kind,
                {"reason_codes": evidence.reason_codes},
                evidence.source_as_of,
            )

    def _case_kind(self, evidence: PaymentEvidence) -> str | None:
        if evidence.match_state == "matched" and evidence.payment_status in {"paid", "pending"}:
            return "confirmation_required"
        mapping = {
            "no_match": "unmatched",
            "ambiguous": "ambiguous",
            "conflict": "conflict",
            "source_unavailable": "source_unavailable",
            "projection_failed": "projection_failed",
            "stale": "stale",
            "not_applicable": "not_applicable",
        }
        return mapping.get(evidence.match_state)

    def _recompute_obligation(self, obligation: Obligation) -> None:
        for derived_kind in ("partial_payment", "double_payment"):
            for existing in (
                self.db.query(PaymentReconciliationCase)
                .filter_by(
                    obligation_id=obligation.id,
                    case_kind=derived_kind,
                    active=True,
                )
                .all()
            ):
                existing.active = False
                existing.state = "resolved"
                existing.attention_state = "settled"
                existing.revision += 1
        active_edges = (
            self.db.query(ObligationPaymentEdge)
            .filter_by(obligation_id=obligation.id, state="active")
            .all()
        )
        evidence = [
            self.db.query(PaymentEvidence).filter_by(id=edge.evidence_id).one()
            for edge in active_edges
        ]
        posted = [item for item in evidence if item.payment_status == "paid" and item.active]
        obligation.revision = (obligation.revision or 1) + 1
        if obligation.status != "settled":
            obligation.status = "payment_detected" if evidence else "open"

        currencies = {item.currency for item in posted if item.currency}
        total = sum(
            edge.allocated_amount_minor
            if edge.allocated_amount_minor is not None
            else (item.amount_minor or 0)
            for edge, item in zip(active_edges, evidence, strict=True)
            if item in posted
        )
        comparable = (
            obligation.expected_amount_minor is not None
            and obligation.currency is not None
            and currencies <= {obligation.currency}
            and all(item.amount_minor is not None for item in posted)
        )
        has_conflict = any(item.match_state != "matched" for item in evidence)
        if comparable and posted and total >= obligation.expected_amount_minor and not has_conflict:
            obligation.completion_suggested = obligation.status != "settled"
            obligation.suggestion_reason = (
                f"Posted payment evidence covers {total} minor units of the "
                f"{obligation.expected_amount_minor} {obligation.currency} obligation."
            )
        else:
            obligation.completion_suggested = False
            obligation.suggestion_reason = None

        if comparable and posted and total < obligation.expected_amount_minor:
            self._upsert_case(
                obligation,
                posted[-1],
                "partial_payment",
                {
                    "expected_amount_minor": obligation.expected_amount_minor,
                    "allocated_amount_minor": total,
                    "currency": obligation.currency,
                },
                posted[-1].source_as_of,
            )
        if (
            obligation.status == "settled"
            and comparable
            and posted
            and total > obligation.expected_amount_minor
        ):
            self._upsert_case(
                obligation,
                posted[-1],
                "double_payment",
                {
                    "expected_amount_minor": obligation.expected_amount_minor,
                    "allocated_amount_minor": total,
                    "currency": obligation.currency,
                },
                posted[-1].source_as_of,
            )

    def recompute_obligation(self, obligation: Obligation) -> None:
        """Rebuild obligation payment projection from current normalized edges."""
        self._recompute_obligation(obligation)

    def _resolve_evidence_cases(
        self,
        obligation_id: str,
        evidence_id: str,
        *,
        except_kind: str | None,
    ) -> None:
        query = self.db.query(PaymentReconciliationCase).filter_by(
            obligation_id=obligation_id,
            evidence_id=evidence_id,
            active=True,
        )
        if except_kind:
            query = query.filter(PaymentReconciliationCase.case_kind != except_kind)
        for case in query.all():
            case.active = False
            case.state = "resolved"
            case.attention_state = "settled"
            case.revision += 1

    def _upsert_case(
        self,
        obligation: Obligation,
        evidence: PaymentEvidence | None,
        kind: str,
        summary: dict,
        source_as_of: datetime | None,
    ) -> PaymentReconciliationCase:
        evidence_id = evidence.id if evidence else None
        case = (
            self.db.query(PaymentReconciliationCase)
            .filter_by(
                obligation_id=obligation.id,
                evidence_id=evidence_id,
                case_kind=kind,
            )
            .first()
        )
        if case is None:
            case = PaymentReconciliationCase(
                id=_stable_id("payment_review", obligation.id, evidence_id or "none", kind),
                obligation_id=obligation.id,
                evidence_id=evidence_id,
                case_kind=kind,
                summary=summary,
                source_as_of=source_as_of.replace(tzinfo=None) if source_as_of else None,
            )
            self.db.add(case)
        else:
            if case.summary != summary:
                case.summary = summary
                case.revision += 1
            case.active = True
            if case.state == "resolved":
                case.state = "open"
            case.source_as_of = source_as_of.replace(tzinfo=None) if source_as_of else None
        return case

    def _edge_for_action(
        self, obligation_id: str, evidence_id: str | None
    ) -> ObligationPaymentEdge:
        edge = (
            self.db.query(ObligationPaymentEdge)
            .filter_by(obligation_id=obligation_id, evidence_id=evidence_id)
            .first()
        )
        if edge is None:
            raise KeyError("payment_evidence_edge_not_found")
        return edge

    def _transition_edge(
        self,
        edge: ObligationPaymentEdge,
        state: str,
        *,
        actor: str,
        action: str,
        idempotency_key: str,
        expected_revision: int | None = None,
    ) -> None:
        previous = self._edge_state(edge)
        edge.state = state
        edge.revision += 1
        edge.removed_at = _utcnow() if state in {"removed", "rejected", "replaced"} else None
        self._append_event(
            edge,
            action=action,
            actor=actor,
            idempotency_key=idempotency_key,
            previous=previous,
            expected_revision=expected_revision,
        )

    def _append_event(
        self,
        edge: ObligationPaymentEdge,
        *,
        action: str,
        actor: str,
        idempotency_key: str,
        previous: dict,
        expected_revision: int | None = None,
    ) -> None:
        evidence = self.db.query(PaymentEvidence).filter_by(id=edge.evidence_id).first()
        self.db.add(
            PaymentRelationshipEvent(
                action_scope=f"obligation:{edge.obligation_id}",
                idempotency_key=idempotency_key,
                edge_id=edge.id,
                obligation_id=edge.obligation_id,
                evidence_id=edge.evidence_id,
                action=action,
                actor=actor,
                provenance=edge.provenance,
                previous_state=previous,
                new_state=self._edge_state(edge),
                source_generation=evidence.source_generation if evidence else None,
                source_revision=evidence.source_revision if evidence else None,
                expected_revision=expected_revision,
            )
        )

    def _append_case_event(
        self,
        case: PaymentReconciliationCase,
        request: PaymentReviewAction,
        actor: str,
    ) -> None:
        self.db.add(
            PaymentRelationshipEvent(
                action_scope=f"case:{case.id}",
                idempotency_key=request.idempotency_key,
                obligation_id=case.obligation_id,
                evidence_id=case.evidence_id,
                action=request.action,
                actor=actor,
                provenance="mission_control" if actor == "mission_control" else "user",
                previous_state={"revision": case.revision},
                new_state={
                    "state": case.state,
                    "attention_state": case.attention_state,
                    "deferred_until": (
                        case.deferred_until.isoformat() if case.deferred_until else None
                    ),
                },
                expected_revision=request.expected_revision,
            )
        )

    def _edge_state(self, edge: ObligationPaymentEdge) -> dict:
        return {
            "state": edge.state,
            "allocated_amount_minor": edge.allocated_amount_minor,
            "manual_precedence": bool(edge.manual_precedence),
            "revision": edge.revision,
            "replaced_by_edge_id": edge.replaced_by_edge_id,
        }

    def _snapshot_result(
        self,
        snapshot: PaymentEvidenceSnapshot,
        disposition: Literal["applied", "replayed", "stale"],
    ) -> PaymentSnapshotResult:
        evidence_count = (
            self.db.query(PaymentEvidence)
            .join(
                ObligationPaymentEdge,
                ObligationPaymentEdge.evidence_id == PaymentEvidence.id,
            )
            .filter(
                PaymentEvidence.source_system == snapshot.source_system,
                PaymentEvidence.connector_ref == snapshot.connector_ref,
                PaymentEvidence.active.is_(True),
                ObligationPaymentEdge.obligation_id == snapshot.obligation_id,
                ObligationPaymentEdge.state.in_(("proposed", "active")),
            )
            .count()
        )
        active_edge_count = (
            self.db.query(ObligationPaymentEdge)
            .filter_by(obligation_id=snapshot.obligation_id, state="active")
            .count()
        )
        return PaymentSnapshotResult(
            source_generation=snapshot.source_generation,
            disposition=disposition,
            evidence_count=evidence_count,
            active_edge_count=active_edge_count,
        )

    def _case_dict(self, case: PaymentReconciliationCase, *, include_history: bool = False) -> dict:
        obligation = self.db.query(Obligation).filter_by(id=case.obligation_id).one()
        evidence = (
            self.db.query(PaymentEvidence).filter_by(id=case.evidence_id).first()
            if case.evidence_id
            else None
        )
        edge = (
            self.db.query(ObligationPaymentEdge)
            .filter_by(obligation_id=case.obligation_id, evidence_id=case.evidence_id)
            .first()
            if case.evidence_id
            else None
        )
        result = {
            "contract_version": "1.0",
            "id": case.id,
            "revision": case.revision,
            "case_kind": case.case_kind,
            "state": case.state,
            "active": bool(case.active),
            "attention_state": case.attention_state,
            "source_as_of": case.source_as_of.isoformat() if case.source_as_of else None,
            "summary": case.summary,
            "obligation": {
                "id": obligation.id,
                "status": obligation.status,
                "revision": obligation.revision,
                "expected_amount_minor": obligation.expected_amount_minor,
                "currency": obligation.currency,
                "completion_suggested": bool(obligation.completion_suggested),
            },
            "evidence": (
                {
                    "id": evidence.id,
                    "kind": evidence.kind,
                    "payee_hint": evidence.payee_hint,
                    "amount_minor": evidence.amount_minor,
                    "currency": evidence.currency,
                    "evidence_date": (
                        evidence.evidence_date.isoformat() if evidence.evidence_date else None
                    ),
                    "source_system": evidence.source_system,
                    "source_state": evidence.source_state,
                    "match_state": evidence.match_state,
                    "payment_status": evidence.payment_status,
                    "confidence": evidence.confidence,
                    "reason_codes": evidence.reason_codes,
                    "source_as_of": evidence.source_as_of.isoformat(),
                    "edge_state": edge.state if edge else None,
                }
                if evidence
                else None
            ),
            "owl_deep_link": f"#/action-queue?obligation={obligation.id}",
            "source_actions": self._source_actions(case, edge),
        }
        if include_history:
            events = (
                self.db.query(PaymentRelationshipEvent)
                .filter_by(obligation_id=case.obligation_id)
                .order_by(PaymentRelationshipEvent.created_at, PaymentRelationshipEvent.id)
                .all()
            )
            result["history"] = [
                {
                    "id": event.id,
                    "action": event.action,
                    "actor": event.actor,
                    "previous_state": event.previous_state,
                    "new_state": event.new_state,
                    "created_at": event.created_at.isoformat(),
                }
                for event in events
            ]
        return result

    def _source_actions(
        self,
        case: PaymentReconciliationCase,
        edge: ObligationPaymentEdge | None,
    ) -> list[dict]:
        actions: list[str] = []
        if edge:
            if edge.state == "proposed":
                actions.extend(("confirm", "reject"))
            elif edge.state == "active":
                actions.append("clear")
            elif edge.state in {"rejected", "removed"}:
                actions.append("confirm")
        if case.state == "deferred":
            actions.append("reopen")
        else:
            actions.append("defer")
        if case.attention_state == "pending":
            actions.append("deliver_attention")
        elif case.attention_state == "delivered":
            actions.append("acknowledge_attention")
        return [
            {
                "id": action,
                "method": "POST",
                "url": f"/api/mc/v1/payment-reconciliation-reviews/{case.id}/actions",
                "expected_revision": case.revision,
            }
            for action in actions
        ]
