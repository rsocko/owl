from __future__ import annotations

import hashlib
from datetime import date
from string import Formatter
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

ProfileReviewStatus = Literal["unreviewed", "reviewed", "ignored"]
ProfileLifecycleStatus = Literal["active", "orphaned", "retired"]
DocumentKind = Literal["statement", "invoice", "bill", "receipt", "record", "other"]
ExpectationMode = Literal["recurring", "periodic", "one_off", "irregular", "not_expected"]
ExpectationStatus = Literal["suggested", "confirmed", "dismissed", "retired"]
CadenceFrequency = Literal["monthly", "quarterly", "annual"]
EvidenceSource = Literal["paperless", "user", "legacy_override"]
SuggestedExpectationMode = Literal[
    "recurring", "periodic", "one_off", "irregular", "not_expected", "unknown"
]
AcquisitionChannel = Literal[
    "paperless_mail",
    "email_manual",
    "direct_api",
    "portal_manual",
    "snail_mail",
    "linked_storage",
    "unknown",
]
DeliveryMode = Literal["push", "pull", "physical"]
AutomationState = Literal["not_applicable", "candidate", "available", "configured", "blocked"]
BrowserFeasibility = Literal["not_assessed", "likely", "mfa_or_captcha", "unsupported"]

TITLE_FIELDS = frozenset({"correspondent", "series", "kind", "period", "document_date", "subject"})


class PolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def paperless_deployment_identity(base_url: str) -> str:
    """Return a stable, non-reversible identity for one Paperless deployment."""
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Paperless deployment URL must be an absolute HTTP(S) URL")
    port = f":{parsed.port}" if parsed.port is not None else ""
    path = parsed.path.rstrip("/")
    canonical = f"{parsed.scheme.lower()}://{parsed.hostname.lower()}{port}{path}"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"paperless:{digest[:24]}"


class TitleConvention(PolicyModel):
    template: str = Field(min_length=1, max_length=256)
    date_basis: Literal["period", "document_date"]
    example: str = Field(min_length=1, max_length=128)

    @field_validator("template")
    @classmethod
    def validate_template(cls, value: str) -> str:
        fields: set[str] = set()
        try:
            parsed = Formatter().parse(value)
            for _, field_name, format_spec, conversion in parsed:
                if field_name is None:
                    continue
                if field_name not in TITLE_FIELDS:
                    raise ValueError(f"Unsupported title field: {field_name}")
                if format_spec or conversion:
                    raise ValueError("Title fields do not support formatting or conversion")
                fields.add(field_name)
        except ValueError:
            raise
        if not fields:
            raise ValueError("Title convention must contain at least one allowed field")
        return value

    def render(self, values: dict[str, str | date | None]) -> str:
        required = {
            field_name
            for _, field_name, _, _ in Formatter().parse(self.template)
            if field_name is not None
        }
        missing = sorted(field for field in required if values.get(field) in (None, ""))
        if missing:
            raise ValueError(f"Missing required title fields: {', '.join(missing)}")
        rendered = self.template.format(
            **{
                field: value.isoformat() if isinstance(value, date) else value
                for field, value in values.items()
            }
        )
        if len(rendered) > 128:
            raise ValueError("Rendered title exceeds Paperless's 128-character limit")
        return rendered


class MetadataPolicy(PolicyModel):
    all_of: list[int] = Field(default_factory=list)
    any_of: list[int] = Field(default_factory=list)
    none_of: list[int] = Field(default_factory=list)
    required_document_type_id: int | None = Field(default=None, gt=0)

    @field_validator("all_of", "any_of", "none_of")
    @classmethod
    def normalize_tag_ids(cls, value: list[int]) -> list[int]:
        if any(tag_id <= 0 for tag_id in value):
            raise ValueError("Paperless tag IDs must be positive")
        return sorted(set(value))

    @model_validator(mode="after")
    def validate_no_conflicts(self) -> MetadataPolicy:
        required = set(self.all_of) | set(self.any_of)
        conflicts = required & set(self.none_of)
        if conflicts:
            raise ValueError(f"Tag IDs cannot be both required and forbidden: {sorted(conflicts)}")
        return self

    def tags_satisfy(self, tag_ids: list[int]) -> bool:
        assigned = set(tag_ids)
        return (
            set(self.all_of) <= assigned
            and (not self.any_of or bool(set(self.any_of) & assigned))
            and not bool(set(self.none_of) & assigned)
        )


class ProfileDefaults(PolicyModel):
    title_convention: TitleConvention | None = None
    metadata_policy: MetadataPolicy | None = None


class ObservedSummary(PolicyModel):
    document_count: int = Field(default=0, ge=0)
    document_type_counts: dict[str, int] = Field(default_factory=dict)
    title_pattern_count: int = Field(default=0, ge=0)
    tag_family_counts: dict[str, int] = Field(default_factory=dict)
    candidate_series_count: int = Field(default=0, ge=0)


class Cadence(PolicyModel):
    frequency: CadenceFrequency
    expected_day: int | None = Field(default=None, ge=1, le=31)
    availability_delay_days: int = Field(default=0, ge=0, le=365)
    grace_period_days: int = Field(default=5, ge=0, le=90)


class ExpectationEvidence(PolicyModel):
    source: EvidenceSource
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    confidence: float | None = Field(default=None, ge=0, le=1)
    sample_size: int = Field(default=0, ge=0)
    observed_from: date | None = None
    observed_to: date | None = None

    @field_validator("reason_codes")
    @classmethod
    def normalize_reason_codes(cls, value: list[str]) -> list[str]:
        normalized = []
        for reason in value:
            if not reason or len(reason) > 64 or not reason.replace("_", "").isalnum():
                raise ValueError("Evidence reason codes must be short alphanumeric identifiers")
            normalized.append(reason.lower())
        return sorted(set(normalized))

    @model_validator(mode="after")
    def validate_observed_range(self) -> ExpectationEvidence:
        if self.observed_from and self.observed_to and self.observed_from > self.observed_to:
            raise ValueError("observed_from must not be after observed_to")
        return self


class AcquisitionSourceBase(PolicyModel):
    channel: AcquisitionChannel
    delivery_mode: DeliveryMode
    instructions: str | None = Field(default=None, max_length=2000)
    portal_url: str | None = Field(default=None, max_length=2048)
    automation_state: AutomationState = "not_applicable"
    connector_type: str | None = Field(default=None, max_length=100)
    connector_ref: str | None = Field(default=None, max_length=200)
    availability_delay_days: int | None = Field(default=None, ge=0, le=365)
    last_success_at: str | None = None
    browser_feasibility: BrowserFeasibility = "not_assessed"

    @field_validator("portal_url")
    @classmethod
    def validate_safe_portal_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "portal_url must be an absolute HTTP(S) URL without credentials, query, or fragment"
            )
        return value.rstrip("/")


class AcquisitionSourceCreate(AcquisitionSourceBase):
    pass


class AcquisitionSourceUpdate(PolicyModel):
    channel: AcquisitionChannel | None = None
    delivery_mode: DeliveryMode | None = None
    instructions: str | None = Field(default=None, max_length=2000)
    portal_url: str | None = Field(default=None, max_length=2048)
    automation_state: AutomationState | None = None
    connector_type: str | None = Field(default=None, max_length=100)
    connector_ref: str | None = Field(default=None, max_length=200)
    availability_delay_days: int | None = Field(default=None, ge=0, le=365)
    last_success_at: str | None = None
    browser_feasibility: BrowserFeasibility | None = None

    @field_validator("portal_url")
    @classmethod
    def validate_safe_portal_url(cls, value: str | None) -> str | None:
        return AcquisitionSourceBase.validate_safe_portal_url(value)


class AcquisitionSource(AcquisitionSourceBase):
    id: str
    created_at: str | None = None
    updated_at: str | None = None


class CorrespondentProfileUpdate(PolicyModel):
    review_status: ProfileReviewStatus | None = None
    lifecycle_status: Literal["retired"] | None = None
    aliases: list[str] | None = None
    notes: str | None = Field(default=None, max_length=4000)
    profile_defaults: ProfileDefaults | None = None
    observed_summary: ObservedSummary | None = None
    last_analyzed_at: str | None = None
    last_reviewed_at: str | None = None

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        aliases = sorted({alias.strip() for alias in value if alias.strip()}, key=str.casefold)
        if len(aliases) > 50 or any(len(alias) > 200 for alias in aliases):
            raise ValueError("aliases may contain at most 50 names of 200 characters each")
        return aliases


class CorrespondentProfile(PolicyModel):
    correspondent_id: int = Field(gt=0)
    current_name: str = Field(min_length=1, max_length=500)
    review_status: ProfileReviewStatus = "unreviewed"
    lifecycle_status: ProfileLifecycleStatus = "active"
    aliases: list[str] = Field(default_factory=list)
    notes: str | None = None
    profile_defaults: ProfileDefaults = Field(default_factory=ProfileDefaults)
    observed_summary: ObservedSummary = Field(default_factory=ObservedSummary)
    last_analyzed_at: str | None = None
    last_reviewed_at: str | None = None
    orphaned_at: str | None = None
    relinked_from_correspondent_id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


class DocumentExpectationBase(PolicyModel):
    kind: DocumentKind
    document_type_id: int | None = Field(default=None, gt=0)
    statement_series_id: str | None = Field(default=None, min_length=1, max_length=200)
    document_ids: list[int] = Field(default_factory=list)
    series_discriminator: str | None = Field(default=None, max_length=200)
    expectation_mode: ExpectationMode
    status: ExpectationStatus = "suggested"
    cadence: Cadence | None = None
    evidence: ExpectationEvidence
    title_convention: TitleConvention | None = None
    metadata_policy: MetadataPolicy = Field(default_factory=MetadataPolicy)
    acquisition_source_id: str | None = None

    @field_validator("document_ids")
    @classmethod
    def normalize_document_ids(cls, value: list[int]) -> list[int]:
        if any(document_id <= 0 for document_id in value):
            raise ValueError("Paperless document IDs must be positive")
        return sorted(set(value))

    @model_validator(mode="after")
    def validate_policy_shape(self) -> DocumentExpectationBase:
        if self.kind == "statement" and self.statement_series_id is None:
            raise ValueError("Statement expectations must bind to a StatementSeries")
        if self.expectation_mode in {"one_off", "irregular", "not_expected"} and self.cadence:
            raise ValueError(f"{self.expectation_mode} expectations cannot define cadence")
        if self.status == "confirmed" and self.expectation_mode in {"recurring", "periodic"}:
            if self.cadence is None:
                raise ValueError("Confirmed recurring or periodic expectations require cadence")
        if (
            self.status == "confirmed"
            and self.kind != "statement"
            and self.expectation_mode != "not_expected"
            and not self.document_ids
        ):
            raise ValueError(
                "Confirmed non-statement expectations require durable document membership"
            )
        return self

    def can_emit_missing_alert(self, profile_lifecycle: ProfileLifecycleStatus = "active") -> bool:
        return (
            profile_lifecycle == "active"
            and self.status == "confirmed"
            and self.expectation_mode in {"recurring", "periodic"}
            and self.cadence is not None
        )


class DocumentExpectationCreate(DocumentExpectationBase):
    pass


class DocumentExpectationUpdate(PolicyModel):
    document_type_id: int | None = Field(default=None, gt=0)
    document_ids: list[int] | None = None
    series_discriminator: str | None = Field(default=None, max_length=200)
    expectation_mode: ExpectationMode | None = None
    status: ExpectationStatus | None = None
    cadence: Cadence | None = None
    evidence: ExpectationEvidence | None = None
    title_convention: TitleConvention | None = None
    metadata_policy: MetadataPolicy | None = None
    acquisition_source_id: str | None = None

    @field_validator("document_ids")
    @classmethod
    def normalize_document_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        return DocumentExpectationBase.normalize_document_ids(value)


class DocumentExpectation(DocumentExpectationBase):
    id: str
    correspondent_id: int = Field(gt=0)
    legacy_provider_key: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


PolicyViolationCode = Literal[
    "missing_all_of",
    "missing_any_of",
    "forbidden_tags",
    "wrong_document_type",
    "title_mismatch",
    "title_missing_fields",
    "title_too_long",
]


class DocumentMetadataSnapshot(PolicyModel):
    title: str = Field(max_length=128)
    tag_ids: list[int] = Field(default_factory=list)
    tag_names: list[str] = Field(default_factory=list)
    document_type_id: int | None = None
    document_type_name: str | None = None


class PaperlessDocumentPatch(PolicyModel):
    title: str | None = Field(default=None, max_length=128)
    tags: list[int] | None = None
    document_type: int | None = Field(default=None, gt=0)

    @model_serializer
    def serialize_patch(self) -> dict[str, str | int | list[int]]:
        return {
            key: value
            for key, value in {
                "title": self.title,
                "tags": self.tags,
                "document_type": self.document_type,
            }.items()
            if value is not None
        }


class PolicyPatchOperation(PolicyModel):
    expectation_id: str
    document_id: int = Field(gt=0)
    expected: DocumentMetadataSnapshot
    patch: PaperlessDocumentPatch


class PolicyViolationPreview(PolicyModel):
    preview_id: str
    operation: PolicyPatchOperation
    proposed: DocumentMetadataSnapshot
    violations: list[PolicyViolationCode]
    unresolved_violations: list[PolicyViolationCode] = Field(default_factory=list)
    missing_title_fields: list[str] = Field(default_factory=list)


class ExpectationPolicyPreview(PolicyModel):
    expectation_id: str
    correspondent_id: int = Field(gt=0)
    matched_document_count: int = Field(ge=0)
    compliant_document_count: int = Field(ge=0)
    findings: list[PolicyViolationPreview] = Field(default_factory=list)


class SelectedPolicyOperation(PolicyModel):
    preview_id: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    operation: PolicyPatchOperation


class PolicyApplyRequest(PolicyModel):
    actor: str = Field(default="user", min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=200)
    operations: list[SelectedPolicyOperation] = Field(min_length=1, max_length=100)


class PolicyOperationResult(PolicyModel):
    preview_id: str
    document_id: int = Field(gt=0)
    status: Literal["succeeded", "failed"]
    audit_event_id: str | None = None
    error_code: str | None = None
    message: str


class PolicyApplyResponse(PolicyModel):
    expectation_id: str
    results: list[PolicyOperationResult]


class PolicyUndoRequest(SelectedPolicyOperation):
    actor: str = Field(default="user", min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=200)


class CorrespondentSyncResult(PolicyModel):
    created: int
    updated: int
    orphaned: int
    restored: int
    legacy_migrated: int = 0
    legacy_review_required: int = 0


class RelinkProfileRequest(PolicyModel):
    correspondent_id: int = Field(gt=0)


class LegacyOverrideReviewItem(PolicyModel):
    provider_key: str
    resolution_status: Literal["migrated", "review_required", "unmigrated"]
    reason_code: str
    expectation_id: str | None = None


class IdentityResolution(PolicyModel):
    status: Literal["resolved", "ambiguous", "unmapped"]
    canonical_key: str | None = None
    expectation: DocumentExpectation | None = None


class TitleRenderExample(PolicyModel):
    document_id: int = Field(gt=0)
    before: str = Field(max_length=128)
    after: str | None = Field(default=None, max_length=128)
    missing_fields: list[str] = Field(default_factory=list)


class TitleConventionSuggestion(PolicyModel):
    convention: TitleConvention | None = None
    coverage: float = Field(ge=0, le=1)
    exception_document_ids: list[int] = Field(default_factory=list)
    examples: list[TitleRenderExample] = Field(default_factory=list, max_length=3)
    reason_codes: list[str] = Field(default_factory=list)


class MetadataPolicySuggestion(PolicyModel):
    policy: MetadataPolicy = Field(default_factory=MetadataPolicy)
    tag_names: dict[int, str] = Field(default_factory=dict)
    required_document_type_name: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)


class AcquisitionSuggestion(PolicyModel):
    channel: AcquisitionChannel = "unknown"
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)


class ExpectationPolicySuggestion(PolicyModel):
    suggestion_key: str
    kind: DocumentKind
    series_discriminator: str
    statement_series_id: str | None = None
    expectation_mode: SuggestedExpectationMode
    cadence: Cadence | None = None
    evidence: ExpectationEvidence
    title: TitleConventionSuggestion
    metadata: MetadataPolicySuggestion
    acquisition: AcquisitionSuggestion
    document_ids: list[int] = Field(default_factory=list)
    sample_document_ids: list[int] = Field(default_factory=list, max_length=3)


class CorrespondentAnalysisResult(PolicyModel):
    correspondent_id: int = Field(gt=0)
    correspondent_name: str
    analyzed_at: str
    observed_summary: ObservedSummary
    suggestions: list[ExpectationPolicySuggestion] = Field(default_factory=list)


def json_model(model_type: type[PolicyModel], value: str | None, default: Any) -> Any:
    if not value:
        return default
    return model_type.model_validate_json(value)
