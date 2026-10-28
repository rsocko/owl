from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from doc_intelligence_hub.modules.statements.correspondent_models import (
    AvailabilityWindow,
    Cadence,
    CalendarRule,
    CorrespondentAnalysisResult,
    EscalationPolicy,
    ExpectationEvidence,
    FulfillmentRule,
    MetadataPolicy,
    TitleConvention,
)

DocumentKind = Literal[
    "statement",
    "invoice",
    "bill",
    "receipt",
    "tax_document",
    "renewal",
    "record",
    "other",
]
SourceDisposition = Literal[
    "expectations_configured",
    "no_series_expected",
    "waiting_for_history",
    "ignored_retired",
]
SourceReviewState = Literal["needs_review", "resolved"]
SourceLifecycle = Literal["active", "orphaned", "retired"]
ReopenReason = Literal[
    "new_source",
    "new_document",
    "repeatable_pattern_detected",
    "external_relationship_proposed",
    "series_membership_changed",
    "metadata_changed",
    "correspondent_orphaned",
    "correspondent_relinked",
    "waiting_revisit_due",
    "manual",
]
ExpectationMode = Literal["recurring", "periodic", "one_off", "irregular", "not_expected"]
ExpectationStatus = Literal["suggested", "confirmed", "dismissed", "retired"]
ExpectationImportance = Literal["routine", "important", "critical"]
MonitoringMode = Literal["none", "presence", "deadline"]
CoverageState = Literal[
    "upcoming",
    "due_soon",
    "missing",
    "fulfilled",
    "fulfilled_late",
    "unmonitored",
    "cancelled",
]
ExternalResolution = Literal["unresolved", "linked", "not_expected", "deferred"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DocumentSourceUpdate(ContractModel):
    disposition: SourceDisposition
    waiting_revisit_date: date | None = None
    aliases: list[str] | None = None
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        aliases = sorted({item.strip() for item in value if item.strip()}, key=str.casefold)
        if len(aliases) > 50 or any(len(item) > 200 for item in aliases):
            raise ValueError("aliases may contain at most 50 names of 200 characters each")
        return aliases

    @model_validator(mode="after")
    def validate_waiting_date(self) -> DocumentSourceUpdate:
        if self.disposition == "waiting_for_history" and self.waiting_revisit_date is None:
            raise ValueError("waiting_for_history requires waiting_revisit_date")
        if self.disposition != "waiting_for_history" and self.waiting_revisit_date is not None:
            raise ValueError("waiting_revisit_date is only valid for waiting_for_history")
        return self


class DocumentSource(ContractModel):
    correspondent_id: int = Field(gt=0)
    current_name: str = Field(min_length=1, max_length=500)
    review_state: SourceReviewState
    disposition: SourceDisposition | None = None
    lifecycle: SourceLifecycle = "active"
    reopen_reasons: list[ReopenReason] = Field(default_factory=list)
    waiting_revisit_date: date | None = None
    aliases: list[str] = Field(default_factory=list)
    notes: str | None = None
    last_evidence_at: datetime | None = None
    last_reviewed_at: datetime | None = None
    orphaned_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SourceReopenRequest(ContractModel):
    reason: ReopenReason
    evidence_at: datetime | None = None
    detail: str | None = Field(default=None, max_length=500)


class DocumentSeriesCreate(ContractModel):
    source_correspondent_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=200)
    kind: DocumentKind
    discriminator: str | None = Field(default=None, max_length=200)
    document_ids: list[int] = Field(default_factory=list, max_length=5000)

    @field_validator("document_ids")
    @classmethod
    def normalize_document_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("Paperless document IDs must be positive")
        return sorted(set(value))


class DocumentSeriesUpdate(ContractModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    discriminator: str | None = Field(default=None, max_length=200)
    document_ids: list[int] | None = Field(default=None, max_length=5000)

    @field_validator("document_ids")
    @classmethod
    def normalize_document_ids(cls, value: list[int] | None) -> list[int] | None:
        return None if value is None else DocumentSeriesCreate.normalize_document_ids(value)


class DocumentSeries(ContractModel):
    id: str
    source_correspondent_id: int
    name: str
    kind: DocumentKind
    discriminator: str | None = None
    document_ids: list[int] = Field(default_factory=list)
    membership_revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class SeriesMemberPeriod(ContractModel):
    document_date: date
    period_key: str = Field(min_length=1, max_length=100)


class SeriesAnalysisDismiss(ContractModel):
    document_ids: list[int] = Field(min_length=1, max_length=500)

    @field_validator("document_ids")
    @classmethod
    def normalize_document_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("Paperless document IDs must be positive")
        return sorted(set(value))


class SourceSeriesAnalysisRequest(ContractModel):
    signal_id: str | None = Field(default=None, min_length=1, max_length=200)


class SourceSeriesAnalysisDocument(ContractModel):
    id: int = Field(gt=0)
    title: str
    created: date
    document_type: str | None = None
    tags: list[str] = Field(default_factory=list)
    account_identifier_display: str | None = None
    account_identifier_source: Literal["stored", "extracted", "extraction_failed"] | None = None


class SeriesMembersAdd(ContractModel):
    document_ids: list[int] = Field(min_length=1, max_length=5000)

    @field_validator("document_ids")
    @classmethod
    def normalize_document_ids(cls, value: list[int]) -> list[int]:
        return DocumentSeriesCreate.normalize_document_ids(value)


class SourceSeriesAnalysisResult(CorrespondentAnalysisResult):
    signal_id: str | None = None
    signal_label: str | None = None
    low_evidence_document_count: int = Field(default=0, ge=0)
    documents: list[SourceSeriesAnalysisDocument] = Field(default_factory=list)


class DocumentExpectationCreate(ContractModel):
    source_correspondent_id: int = Field(gt=0)
    series_id: str | None = Field(default=None, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    kind: DocumentKind
    expectation_mode: ExpectationMode
    status: ExpectationStatus = "suggested"
    cadence: Cadence | None = None
    importance: ExpectationImportance = "routine"
    monitoring_mode: MonitoringMode = "none"
    calendar_rule: CalendarRule | None = None
    availability_window: AvailabilityWindow | None = None
    grace_period_days: int = Field(default=0, ge=0, le=365)
    escalation_policy: EscalationPolicy = Field(default_factory=EscalationPolicy)
    fulfillment_rule: FulfillmentRule = Field(default_factory=FulfillmentRule)
    evidence: ExpectationEvidence
    title_convention: TitleConvention | None = None
    metadata_policy: MetadataPolicy = Field(default_factory=MetadataPolicy)
    acquisition_source_id: str | None = None
    acquisition_notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_policy(self) -> DocumentExpectationCreate:
        if self.expectation_mode in {"irregular", "not_expected"}:
            if self.monitoring_mode != "none":
                raise ValueError(f"{self.expectation_mode} expectations cannot enable monitoring")
            if self.cadence is not None:
                raise ValueError(f"{self.expectation_mode} expectations cannot define cadence")
        if self.expectation_mode in {"recurring", "periodic"}:
            if self.status == "confirmed" and self.cadence is None:
                raise ValueError("confirmed recurring or periodic expectations require cadence")
        if self.expectation_mode == "one_off":
            if self.monitoring_mode not in {"none", "deadline"}:
                raise ValueError("one_off expectations use deadline monitoring")
            if self.status == "confirmed" and (
                self.calendar_rule is None or self.calendar_rule.rule_type != "one_off"
            ):
                raise ValueError("confirmed one_off expectations require a one_off calendar rule")
        elif self.calendar_rule is not None and self.calendar_rule.rule_type == "one_off":
            raise ValueError("only one_off expectations can use a one_off calendar rule")
        if self.monitoring_mode == "deadline" and self.calendar_rule is None:
            raise ValueError("deadline monitoring requires a calendar rule")
        if self.series_id is None and self.fulfillment_rule.match_series:
            raise ValueError(
                "expectations without a reviewed series cannot require series matching"
            )
        return self


class DocumentExpectationUpdate(ContractModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    series_id: str | None = Field(default=None, min_length=1, max_length=64)
    expectation_mode: ExpectationMode | None = None
    status: ExpectationStatus | None = None
    cadence: Cadence | None = None
    importance: ExpectationImportance | None = None
    monitoring_mode: MonitoringMode | None = None
    calendar_rule: CalendarRule | None = None
    availability_window: AvailabilityWindow | None = None
    grace_period_days: int | None = Field(default=None, ge=0, le=365)
    escalation_policy: EscalationPolicy | None = None
    fulfillment_rule: FulfillmentRule | None = None
    evidence: ExpectationEvidence | None = None
    title_convention: TitleConvention | None = None
    metadata_policy: MetadataPolicy | None = None
    acquisition_source_id: str | None = None
    acquisition_notes: str | None = Field(default=None, max_length=4000)


class DocumentExpectation(DocumentExpectationCreate):
    id: str
    created_at: datetime
    updated_at: datetime


class ExternalSignalRelationshipUpdate(ContractModel):
    source_correspondent_id: int | None = Field(default=None, gt=0)
    expectation_ids: list[str] = Field(default_factory=list, max_length=100)
    resolution: ExternalResolution
    safe_label: str | None = Field(default=None, max_length=200)
    revisit_date: date | None = None

    @model_validator(mode="after")
    def validate_relationship(self) -> ExternalSignalRelationshipUpdate:
        self.expectation_ids = list(dict.fromkeys(self.expectation_ids))
        if self.expectation_ids and self.source_correspondent_id is None:
            raise ValueError("expectation links require a source link")
        if self.resolution == "linked" and self.source_correspondent_id is None:
            raise ValueError("linked relationships require a source")
        if self.resolution in {"not_expected", "unresolved"} and self.expectation_ids:
            raise ValueError(f"{self.resolution} relationships cannot link expectations")
        if self.resolution == "deferred" and self.revisit_date is None:
            raise ValueError("deferred relationships require revisit_date")
        return self


class ExternalSignalRelationship(ExternalSignalRelationshipUpdate):
    signal_id: str
    updated_at: datetime


class ExternalSignalQueueItem(ContractModel):
    id: str
    kind: Literal["accountStatementCandidate", "recurringDocumentCandidate"]
    active: bool
    display_hint: str
    confidence: float = Field(ge=0, le=1)
    basis: list[str]
    source_as_of: datetime
    account_name: str | None = None
    institution_name: str | None = None
    account_type: str | None = None
    account_last_four: str | None = None
    relationship: ExternalSignalRelationship | None = None


class CoverageOccurrence(ContractModel):
    id: str
    expectation_id: str
    source_correspondent_id: int
    source_name: str
    expectation_name: str
    kind: DocumentKind
    period_key: str
    due_start: date
    due_end: date
    importance: ExpectationImportance
    grace_period_days: int = Field(ge=0)
    grace_extended_until: date | None = None
    state: CoverageState
    fulfilled_document_id: int | None = None
    fulfilled_at: datetime | None = None
    evaluated_at: datetime


class CoverageEvaluationRequest(ContractModel):
    as_of: date
    horizon_start: date | None = None


class CoverageGraceExtension(ContractModel):
    extend_until: date


class CoverageNearMatch(ContractModel):
    document_id: int = Field(gt=0)
    title: str
    document_date: date | None = None
    correspondent_id: int | None = None
    document_type: str | None = None
    already_assigned: bool = False


class CoverageOccurrenceDetail(ContractModel):
    occurrence: CoverageOccurrence
    expectation: DocumentExpectation
    series: DocumentSeries | None = None
    recent_document_ids: list[int] = Field(default_factory=list)


class CoverageReassignment(ContractModel):
    document_id: int = Field(gt=0)
    document_date: date
