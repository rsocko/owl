"""Tests for the insight routing logic."""

from __future__ import annotations

import pytest

from doc_intelligence_hub.modules.analysis import database as db
from doc_intelligence_hub.modules.analysis.insight_router import (
    _evaluate_condition,
    _evaluate_routing,
    route_result,
)
from doc_intelligence_hub.modules.analysis.models import (
    EscalationCondition,
    InsightRoute,
    InsightSeverity,
    InsightType,
    RuleConfig,
    RuleExecutionResult,
    RuleRouting,
    RuleTier,
    RuleTrigger,
    TriggerType,
)


@pytest.fixture(autouse=True)
def _setup_db(tmp_path):
    db.configure(f"sqlite:///{tmp_path}/test_analysis.db")
    db.init_db()

    # Also init triage DB for routing tests
    from doc_intelligence_hub.modules.triage.database import set_db_url as triage_set_url
    from doc_intelligence_hub.modules.triage.database import init_db as triage_init

    triage_set_url(f"sqlite:///{tmp_path}/test_triage.db")
    triage_init()
    yield


def _make_rule(**overrides) -> RuleConfig:
    defaults = {
        "id": "test-rule",
        "name": "Test Rule",
        "tier": RuleTier.BASIC,
        "trigger": RuleTrigger(type=TriggerType.MANUAL),
        "routing": RuleRouting(default=InsightRoute.INFORMATIONAL),
    }
    defaults.update(overrides)
    return RuleConfig(**defaults)


def _make_result(**overrides) -> RuleExecutionResult:
    defaults = {
        "rule_id": "test-rule",
        "success": True,
        "insight_type": InsightType.COMPARISON,
        "title": "Test insight",
        "summary": "Test summary",
    }
    defaults.update(overrides)
    return RuleExecutionResult(**defaults)


class TestConditionEvaluation:
    def test_greater_than(self):
        assert _evaluate_condition("pct_change > 50", {"pct_change": 75}) is True
        assert _evaluate_condition("pct_change > 50", {"pct_change": 30}) is False

    def test_less_than(self):
        assert _evaluate_condition("score < 75", {"score": 60}) is True
        assert _evaluate_condition("score < 75", {"score": 90}) is False

    def test_equals(self):
        assert _evaluate_condition("days_late == 30", {"days_late": 30}) is True
        assert _evaluate_condition("days_late == 30", {"days_late": 31}) is False

    def test_and_condition(self):
        metrics = {"pct_change": 25, "amount": 5000}
        assert _evaluate_condition("pct_change > 20 and amount > 3000", metrics) is True
        assert _evaluate_condition("pct_change > 20 and amount > 10000", metrics) is False

    def test_or_condition(self):
        metrics = {"pct_change": 10, "days_late": 45}
        assert _evaluate_condition("pct_change > 50 or days_late > 30", metrics) is True
        assert _evaluate_condition("pct_change > 50 or days_late > 60", metrics) is False

    def test_missing_metric(self):
        assert _evaluate_condition("pct_change > 50", {}) is False

    def test_invalid_condition(self):
        assert _evaluate_condition("this is not valid", {"x": 1}) is False


class TestRoutingEvaluation:
    def test_default_route_no_escalation(self):
        rule = _make_rule(routing=RuleRouting(default=InsightRoute.INFORMATIONAL))
        result = _make_result()

        route, severity, mc_alert = _evaluate_routing(rule, result)
        assert route == InsightRoute.INFORMATIONAL
        assert mc_alert is False

    def test_escalation_triggers(self):
        rule = _make_rule(
            routing=RuleRouting(
                default=InsightRoute.INFORMATIONAL,
                escalation=[
                    EscalationCondition(condition="pct_change > 50", route=InsightRoute.ACTIONABLE, severity=InsightSeverity.WARNING, mc_alert=True),
                ],
            )
        )
        result = _make_result(metric_values={"pct_change": 75})

        route, severity, mc_alert = _evaluate_routing(rule, result)
        assert route == InsightRoute.ACTIONABLE
        assert severity == InsightSeverity.WARNING
        assert mc_alert is True

    def test_highest_severity_escalation_wins(self):
        rule = _make_rule(
            routing=RuleRouting(
                default=InsightRoute.INFORMATIONAL,
                escalation=[
                    EscalationCondition(condition="pct_change > 50", severity=InsightSeverity.WARNING),
                    EscalationCondition(condition="pct_change > 100", severity=InsightSeverity.CRITICAL, mc_alert=True),
                ],
            )
        )
        result = _make_result(metric_values={"pct_change": 150})

        route, severity, mc_alert = _evaluate_routing(rule, result)
        assert severity == InsightSeverity.CRITICAL
        assert mc_alert is True

    def test_no_escalation_when_condition_not_met(self):
        rule = _make_rule(
            routing=RuleRouting(
                default=InsightRoute.INFORMATIONAL,
                escalation=[
                    EscalationCondition(condition="pct_change > 50", severity=InsightSeverity.WARNING),
                ],
            )
        )
        result = _make_result(metric_values={"pct_change": 20})

        route, severity, mc_alert = _evaluate_routing(rule, result)
        assert route == InsightRoute.INFORMATIONAL


class TestRouteResult:
    def test_informational_insight_stored(self):
        rule = _make_rule()
        result = _make_result(period="Jun 2024")

        outcome = route_result(rule, result)
        assert outcome["insight_id"] is not None
        assert outcome["route"] == "informational"
        assert outcome["triage_item_id"] is None

        # Verify in DB
        insight = db.get_insight(outcome["insight_id"])
        assert insight is not None
        assert insight["route"] == "informational"

    def test_actionable_insight_creates_triage_item(self):
        rule = _make_rule(routing=RuleRouting(default=InsightRoute.ACTIONABLE))
        result = _make_result(period="Jun 2024")

        outcome = route_result(rule, result)
        assert outcome["route"] == "actionable"
        assert outcome["triage_item_id"] is not None

    def test_failed_result_returns_error(self):
        rule = _make_rule()
        result = _make_result(success=False, error="No data")

        outcome = route_result(rule, result)
        assert outcome["insight_id"] is None
        assert outcome["error"] == "No data"

    def test_deduplication_supersedes_old_insight(self):
        rule = _make_rule()
        result = _make_result(period="Jun 2024", series_id="s1")

        outcome1 = route_result(rule, result)
        outcome2 = route_result(rule, result)

        assert outcome2["superseded_id"] == outcome1["insight_id"]

        old = db.get_insight(outcome1["insight_id"])
        assert old["status"] == "superseded"

    def test_history_entries_created(self):
        rule = _make_rule()
        result = _make_result(
            period="Jun 2024",
            series_id="s1",
            metric_values={"total_amount": 2500.0, "pct_change": 15.0},
        )

        route_result(rule, result)

        entries = db.get_history_for_series("s1")
        assert len(entries) == 2
        metric_names = {e["metric_name"] for e in entries}
        assert "total_amount" in metric_names
        assert "pct_change" in metric_names
