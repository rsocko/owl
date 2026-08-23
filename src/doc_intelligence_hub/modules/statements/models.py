from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class DocumentRecord(BaseModel):
    id: int
    title: str
    correspondent_id: int | None = None
    correspondent_name: str = "Unknown"
    document_type_id: int | None = None
    document_type: str | None = None
    created: date
    added: str | None = None
    tag_ids: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    original_file_name: str | None = None


class AnalysisPattern(BaseModel):
    frequency: Literal["monthly", "quarterly", "annual"]
    pattern_type: Literal["fixed_day", "last_day", "last_business_day", "variable"]
    confidence: float
    anchor_day: int | None = None
    variance_days: int = 0
    grace_period_days: int = 5


class ProviderCandidate(BaseModel):
    provider_key: str
    provider_name: str
    statement_name: str | None = None
    correspondent_id: int | None = None
    document_count: int
    normalized_title: str
    title_consistency: float
    pattern: AnalysisPattern
    sample_document_ids: list[int]
    first_seen: date
    last_seen: date


class Recommendation(BaseModel):
    provider_key: str
    provider_name: str
    expected_date: date
    earliest_date: date
    latest_date: date
    status: Literal["missing", "overdue"]
    priority: int
    days_late: int


class DiscoveryResult(BaseModel):
    analyzed_documents: int
    providers: list[ProviderCandidate]


class DiscoveryDiagnosticEntry(BaseModel):
    correspondent_id: int | None = None
    correspondent_name: str
    normalized_title: str
    document_count: int
    status: Literal["accepted", "rejected"]
    reason: str
    detected_frequency: Literal["monthly", "quarterly", "annual"] | None = None
    sample_document_ids: list[int]
    first_seen: date
    last_seen: date


class DiscoveryDiagnosticResult(BaseModel):
    analyzed_documents: int
    accepted_providers: int
    groups: list[DiscoveryDiagnosticEntry]


class RecommendationResult(BaseModel):
    as_of: date
    recommendations: list[Recommendation]


# ---------------------------------------------------------------------------
# Statement series grouping models
# ---------------------------------------------------------------------------


class StatementSeries(BaseModel):
    """A grouping of documents into a recurring statement series."""

    id: str
    name: str
    correspondent_id: int | None = None
    correspondent_name: str = "Unknown"
    frequency: str = "monthly"
    account_identifier: str | None = None
    manually_curated: bool = False
    document_count: int = 0
    first_seen: str | None = None
    last_seen: str | None = None
    created_at: str | None = None
    # Financial reconciliation fields (ARCH-08)
    currency: str = "USD"
    expected_amount: float | None = None
    amount_variance_threshold: float | None = None


class SeriesDocument(BaseModel):
    """A document belonging to a statement series."""

    series_id: str
    document_id: str
    title: str | None = None
    statement_date: str | None = None
    period_label: str | None = None
    account_hint: str | None = None
    added_at: str | None = None
    # Financial reconciliation fields (ARCH-08)
    statement_amount: float | None = None
    opening_balance: float | None = None
    closing_balance: float | None = None
    currency: str | None = None


class TimelineEntry(BaseModel):
    """A single data point on the series timeline."""

    document_id: str
    title: str | None = None
    statement_date: str | None = None
    period_label: str | None = None
    account_hint: str | None = None
    gap_before_days: int | None = None
    # Financial reconciliation fields (ARCH-08)
    statement_amount: float | None = None
    opening_balance: float | None = None
    closing_balance: float | None = None
    balance_delta: float | None = None


class SuggestedSplitGroup(BaseModel):
    """A suggested group of documents that share the same account hint."""

    account_hint: str
    document_ids: list[str]


class SeriesDetail(BaseModel):
    """Full detail view of a statement series."""

    series: StatementSeries
    documents: list[SeriesDocument] = Field(default_factory=list)
    timeline: list[TimelineEntry] = Field(default_factory=list)
    similar_series: list[StatementSeries] = Field(default_factory=list)
    anomaly_indicators: list[str] = Field(default_factory=list)
    suggested_split_groups: list[SuggestedSplitGroup] = Field(default_factory=list)


class SplitSeriesRequest(BaseModel):
    """Request to split documents from one series into a new one."""

    document_ids: list[str]
    new_series_name: str
    account_identifier: str | None = None


class MergeSeriesRequest(BaseModel):
    """Request to merge one series into another."""

    source_series_id: str
    target_series_id: str


class ReassignDocumentRequest(BaseModel):
    """Request to move a single document to a different series."""

    document_id: str
    target_series_id: str


class RenameSeriesRequest(BaseModel):
    """Request to rename a series and/or set its account identifier."""

    name: str | None = None
    account_identifier: str | None = None
