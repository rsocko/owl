from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class DocumentRecord(BaseModel):
    id: int
    title: str
    correspondent_id: int | None = None
    correspondent_name: str = "Unknown"
    document_type: str | None = None
    created: date
    added: str | None = None
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
