"""Analysis Engine — Pydantic models for rules, insights, and API contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# ------------------------------------------------------------------
# Enums
# ------------------------------------------------------------------


class RuleTier(str, Enum):
    BASIC = "basic"
    LLM = "llm"
    N8N = "n8n"


class TriggerType(str, Enum):
    DOCUMENT_ADDED = "document_added"
    SCHEDULE = "schedule"
    MANUAL = "manual"


class InsightType(str, Enum):
    COMPARISON = "comparison"
    ANOMALY = "anomaly"
    TREND = "trend"
    COMPLIANCE = "compliance"
    EXTRACTION = "extraction"


class InsightRoute(str, Enum):
    INFORMATIONAL = "informational"
    ACTIONABLE = "actionable"


class InsightSeverity(str, Enum):
    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    CRITICAL = "critical"


class InsightStatus(str, Enum):
    NEW = "new"
    VIEWED = "viewed"
    ACKNOWLEDGED = "acknowledged"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"


class CardType(str, Enum):
    COMPARISON = "comparison"
    TREND = "trend"
    ALERT = "alert"
    SUMMARY = "summary"
    TABLE = "table"


# ------------------------------------------------------------------
# Rule Configuration Models
# ------------------------------------------------------------------


class TriggerFilter(BaseModel):
    document_type: str | list[str] | None = None
    tags: list[str] | None = None
    correspondent: str | None = None


class RuleTrigger(BaseModel):
    type: TriggerType
    filter: TriggerFilter | None = None
    cron: str | None = None  # For schedule triggers


class EscalationCondition(BaseModel):
    condition: str  # e.g. "pct_change > 50"
    route: InsightRoute = InsightRoute.ACTIONABLE
    severity: InsightSeverity = InsightSeverity.WARNING
    mc_alert: bool = False


class RuleRouting(BaseModel):
    default: InsightRoute = InsightRoute.INFORMATIONAL
    escalation: list[EscalationCondition] | None = None


class RuleDisplay(BaseModel):
    card_type: CardType = CardType.SUMMARY
    highlight_fields: list[str] | None = None


class RuleConfig(BaseModel):
    """Full rule configuration — loaded from YAML or stored in DB."""

    id: str
    name: str
    description: str = ""
    tier: RuleTier = RuleTier.BASIC
    enabled: bool = True

    trigger: RuleTrigger
    context: list[str | dict[str, Any]] | None = None  # Context requirements
    analyzer: str = ""  # e.g. "builtin:spend_comparison" or "llm:classify"
    params: dict[str, Any] = Field(default_factory=dict)

    routing: RuleRouting = Field(default_factory=RuleRouting)
    display: RuleDisplay = Field(default_factory=RuleDisplay)

    # Runtime metadata (not stored in YAML)
    source: str = "builtin"  # 'builtin', 'yaml', 'custom'
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    insight_count: int = 0


# ------------------------------------------------------------------
# Context & Execution Models
# ------------------------------------------------------------------


class ContextData(BaseModel):
    """Data assembled by the Context Builder for rule evaluation."""

    current_document: dict[str, Any] | None = None
    series_history: list[dict[str, Any]] = Field(default_factory=list)
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
    related_matches: list[dict[str, Any]] = Field(default_factory=list)
    series_info: dict[str, Any] | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class RuleExecutionResult(BaseModel):
    """Output of a single rule execution."""

    rule_id: str
    success: bool = True
    error: str | None = None

    # Insight data (populated on success)
    insight_type: InsightType | None = None
    title: str = ""
    summary: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    highlight_data: dict[str, Any] | None = None

    # Routing hints from the rule's own analysis
    suggested_severity: InsightSeverity = InsightSeverity.INFO
    metric_values: dict[str, float] = Field(default_factory=dict)

    # Context refs
    series_id: str | None = None
    document_ids: list[int] = Field(default_factory=list)
    correspondent: str | None = None
    period: str | None = None  # e.g. "Jun 2024"


# ------------------------------------------------------------------
# API Request/Response Models
# ------------------------------------------------------------------


class InsightResponse(BaseModel):
    id: str
    rule_id: str
    rule_name: str
    insight_type: str
    route: str
    severity: str
    title: str
    summary: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    highlight_data: dict[str, Any] | None = None
    series_id: str | None = None
    document_ids: list[int] = Field(default_factory=list)
    correspondent: str | None = None
    status: str = "new"
    triage_item_id: str | None = None
    mc_alert_id: str | None = None
    created_at: str | None = None
    viewed_at: str | None = None
    acknowledged_at: str | None = None
    period: str | None = None
    supersedes_id: str | None = None


class InsightListResponse(BaseModel):
    insights: list[InsightResponse]
    total: int
    offset: int = 0
    limit: int = 50


class InsightSummaryResponse(BaseModel):
    total: int = 0
    new: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_route: dict[str, int] = Field(default_factory=dict)


class RuleResponse(BaseModel):
    id: str
    name: str
    description: str = ""
    tier: str
    enabled: bool = True
    trigger: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    routing: dict[str, Any] = Field(default_factory=dict)
    display: dict[str, Any] = Field(default_factory=dict)
    source: str = "builtin"
    last_run_at: str | None = None
    last_run_status: str | None = None
    insight_count: int = 0


class RuleListResponse(BaseModel):
    rules: list[RuleResponse]
    total: int


class ExecuteRequest(BaseModel):
    rule_ids: list[str] | None = None  # None = all matching rules
    trigger_type: TriggerType = TriggerType.MANUAL
    document_id: int | None = None  # For document_added triggers
    dry_run: bool = False


class ExecuteResponse(BaseModel):
    rules_executed: int = 0
    insights_created: int = 0
    errors: list[dict[str, str]] = Field(default_factory=list)
    results: list[dict[str, Any]] = Field(default_factory=list)


class RuleUpdateRequest(BaseModel):
    enabled: bool | None = None
    params: dict[str, Any] | None = None
    routing: dict[str, Any] | None = None
    display: dict[str, Any] | None = None


class RuleCreateRequest(BaseModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$")
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    tier: RuleTier = RuleTier.BASIC
    trigger: dict[str, Any]
    context: list[str | dict[str, Any]] | None = None
    analyzer: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    routing: dict[str, Any] = Field(default_factory=dict)
    display: dict[str, Any] = Field(default_factory=dict)


class BulkInsightActionRequest(BaseModel):
    action: str  # 'acknowledge' or 'archive'
    insight_ids: list[str] = Field(max_length=200)
