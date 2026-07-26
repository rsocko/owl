"""Insight Router — routes rule execution results to the appropriate destination.

Evaluates escalation conditions, stores insights in the DB, creates triage
queue items for actionable insights, and emits MC alerts when flagged.
"""

from __future__ import annotations

import logging
import operator
import re
from datetime import UTC, datetime
from typing import Any

from doc_intelligence_hub.modules.analysis import database as db
from doc_intelligence_hub.modules.analysis.models import (
    EscalationCondition,
    InsightRoute,
    InsightSeverity,
    RuleConfig,
    RuleExecutionResult,
)

logger = logging.getLogger(__name__)

# Severity ordering for escalation (highest wins)
_SEVERITY_ORDER = {
    InsightSeverity.INFO: 0,
    InsightSeverity.NOTICE: 1,
    InsightSeverity.WARNING: 2,
    InsightSeverity.CRITICAL: 3,
}


def route_result(rule: RuleConfig, result: RuleExecutionResult) -> dict[str, Any]:
    """Route a rule execution result to the appropriate destination.

    Returns a dict describing the routing outcome:
        {
            "insight_id": "...",
            "route": "informational" | "actionable",
            "severity": "info" | "notice" | "warning" | "critical",
            "triage_item_id": "..." | None,
            "mc_alert_id": "..." | None,
            "superseded_id": "..." | None,
        }
    """
    if not result.success:
        return {"insight_id": None, "route": None, "error": result.error}

    # Determine route and severity via escalation evaluation
    route, severity, mc_alert = _evaluate_routing(rule, result)

    # Deduplication — supersede previous insight for same rule+series+period
    superseded_id = db.supersede_insight(
        rule_id=rule.id,
        series_id=result.series_id,
        period=result.period,
    )

    # Clean up downstream references for superseded insight
    if superseded_id:
        _resolve_superseded_downstream(superseded_id)

    # Store the insight
    insight = db.create_insight(
        rule_id=rule.id,
        rule_name=rule.name,
        insight_type=result.insight_type.value if result.insight_type else "extraction",
        route=route.value,
        severity=severity.value,
        title=result.title,
        summary=result.summary,
        detail=result.detail,
        highlight_data=result.highlight_data,
        series_id=result.series_id,
        document_ids=result.document_ids,
        correspondent=result.correspondent,
        period=result.period,
        supersedes_id=superseded_id,
    )

    insight_id = insight["id"]
    triage_item_id = None
    mc_alert_id = None

    # Record history entries for trend metrics
    for metric_name, metric_value in result.metric_values.items():
        if result.period:
            db.create_history_entry(
                rule_id=rule.id,
                series_id=result.series_id,
                period=result.period,
                metric_name=metric_name,
                metric_value=metric_value,
            )

    # Route to triage queue if actionable
    if route == InsightRoute.ACTIONABLE:
        triage_item_id = _create_triage_item(rule, result, insight_id, severity)
        if triage_item_id:
            db.set_insight_triage_id(insight_id, triage_item_id)

    # Emit MC alert if flagged
    if mc_alert:
        mc_alert_id = _emit_mc_alert(rule, result, insight_id, severity)
        if mc_alert_id:
            db.set_insight_mc_alert_id(insight_id, mc_alert_id)

    # Update rule state with run results
    db.upsert_rule_state(
        rule.id,
        last_run_at=datetime.now(UTC),
        last_run_status="ok",
        insight_count_increment=1,
    )

    return {
        "insight_id": insight_id,
        "route": route.value,
        "severity": severity.value,
        "triage_item_id": triage_item_id,
        "mc_alert_id": mc_alert_id,
        "superseded_id": superseded_id,
    }


# ------------------------------------------------------------------
# Escalation evaluation
# ------------------------------------------------------------------


def _evaluate_routing(
    rule: RuleConfig, result: RuleExecutionResult
) -> tuple[InsightRoute, InsightSeverity, bool]:
    """Evaluate routing rules and escalation conditions.

    Returns (route, severity, mc_alert).
    """
    default_route = rule.routing.default
    severity = result.suggested_severity
    mc_alert = False

    escalations = rule.routing.escalation
    if not escalations:
        return default_route, severity, mc_alert

    # Evaluate each escalation condition, pick the highest severity match
    best_match: EscalationCondition | None = None
    best_severity_rank = -1

    for esc in escalations:
        if _evaluate_condition(esc.condition, result.metric_values):
            rank = _SEVERITY_ORDER.get(esc.severity, 0)
            if rank > best_severity_rank:
                best_match = esc
                best_severity_rank = rank

    if best_match:
        return best_match.route, best_match.severity, best_match.mc_alert

    return default_route, severity, mc_alert


def _evaluate_condition(condition: str, metrics: dict[str, float]) -> bool:
    """Evaluate a simple condition expression against metric values.

    Supports: >, <, >=, <=, ==, !=, and, or
    Examples:
        "pct_change > 50"
        "pct_change > 100"
        "days_late > 30"
        "score < 50"
        "trend_direction == 'increasing' and pct_change_6mo > 20"
    """
    try:
        # Handle 'and' / 'or' compound conditions
        if " and " in condition:
            parts = condition.split(" and ")
            return all(_evaluate_single_condition(p.strip(), metrics) for p in parts)
        if " or " in condition:
            parts = condition.split(" or ")
            return any(_evaluate_single_condition(p.strip(), metrics) for p in parts)

        return _evaluate_single_condition(condition, metrics)

    except Exception as exc:
        logger.warning("Failed to evaluate condition '%s': %s", condition, exc)
        return False


_COMPARISON_OPS = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}

_CONDITION_RE = re.compile(r"^(\w+)\s*(>=|<=|!=|==|>|<)\s*(.+)$")


def _evaluate_single_condition(condition: str, metrics: dict[str, float]) -> bool:
    """Evaluate a single comparison condition."""
    match = _CONDITION_RE.match(condition.strip())
    if not match:
        return False

    field, op_str, value_str = match.groups()
    op_func = _COMPARISON_OPS.get(op_str)
    if not op_func:
        return False

    # Get the field value from metrics
    field_value = metrics.get(field)
    if field_value is None:
        return False

    # Parse the comparison value
    value_str = value_str.strip().strip("'\"")
    try:
        compare_value: float | str = float(value_str)
    except ValueError:
        compare_value = value_str
        field_value_str: str | float = str(field_value)
        return op_func(field_value_str, compare_value)

    return op_func(field_value, compare_value)


# ------------------------------------------------------------------
# Supersede cleanup
# ------------------------------------------------------------------


def _resolve_superseded_downstream(superseded_id: str) -> None:
    """Clean up triage items and MC alerts linked to a superseded insight."""
    try:
        superseded = db.get_insight(superseded_id)
        if not superseded:
            return

        # Resolve linked triage item
        triage_id = superseded.get("triage_item_id")
        if triage_id:
            try:
                from doc_intelligence_hub.modules.triage.database import resolve_queue_item

                resolve_queue_item(triage_id, resolution="superseded")
                logger.debug(
                    "Resolved triage item %s for superseded insight %s", triage_id, superseded_id
                )
            except Exception as exc:
                logger.warning("Could not resolve triage item %s: %s", triage_id, exc)

        # Dismiss linked MC alert
        mc_alert_id = superseded.get("mc_alert_id")
        if mc_alert_id:
            try:
                from doc_intelligence_hub.core.alerts import dismiss_alert

                dismiss_alert(mc_alert_id)
                logger.debug(
                    "Dismissed MC alert %s for superseded insight %s", mc_alert_id, superseded_id
                )
            except Exception as exc:
                logger.warning("Could not dismiss MC alert %s: %s", mc_alert_id, exc)

    except Exception as exc:
        logger.warning("Failed to clean up superseded insight %s: %s", superseded_id, exc)


# ------------------------------------------------------------------
# Triage queue integration
# ------------------------------------------------------------------


def _create_triage_item(
    rule: RuleConfig, result: RuleExecutionResult, insight_id: str, severity: InsightSeverity
) -> str | None:
    """Create a triage queue item for an actionable insight."""
    try:
        from doc_intelligence_hub.modules.triage.database import create_queue_item

        priority_map = {
            InsightSeverity.CRITICAL: 90,
            InsightSeverity.WARNING: 70,
            InsightSeverity.NOTICE: 50,
            InsightSeverity.INFO: 30,
        }

        item = create_queue_item(
            item_type=f"insight:{rule.id}",
            source="analysis_engine",
            target_type="insight",
            target_id=insight_id,
            reason=result.summary,
            metadata={
                "insight_type": result.insight_type.value if result.insight_type else None,
                "rule_name": rule.name,
                "severity": severity.value,
                "document_ids": result.document_ids,
            },
            priority=priority_map.get(severity, 50),
        )
        logger.info("Created triage item %s for insight %s", item["id"], insight_id)
        return item["id"]

    except Exception as exc:
        logger.error("Failed to create triage item for insight %s: %s", insight_id, exc)
        return None


# ------------------------------------------------------------------
# Mission Control alert integration
# ------------------------------------------------------------------


def _emit_mc_alert(
    rule: RuleConfig, result: RuleExecutionResult, insight_id: str, severity: InsightSeverity
) -> str | None:
    """Emit a Mission Control alert for an escalated insight."""
    try:
        from doc_intelligence_hub.core.alerts import emit_alert

        severity_map = {
            InsightSeverity.CRITICAL: "critical",
            InsightSeverity.WARNING: "high",
            InsightSeverity.NOTICE: "medium",
            InsightSeverity.INFO: "low",
        }

        alert = emit_alert(
            alert_type=f"insight:{rule.id}",
            severity=severity_map.get(severity, "medium"),
            module="analysis",
            title=result.title,
            description=result.summary,
            action_url=f"/insights/{insight_id}",
            metadata={
                "insight_id": insight_id,
                "rule_id": rule.id,
                "insight_type": result.insight_type.value if result.insight_type else None,
                "document_ids": result.document_ids,
            },
        )
        if alert:
            logger.info("Emitted MC alert for insight %s", insight_id)
            return str(alert.id)
        return None

    except Exception as exc:
        logger.error("Failed to emit MC alert for insight %s: %s", insight_id, exc)
        return None
