"""Tests for the rule executor orchestration layer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doc_intelligence_hub.modules.analysis.models import (
    ContextData,
    InsightSeverity,
    InsightType,
    RuleConfig,
    RuleExecutionResult,
    RuleRouting,
    RuleTier,
    RuleTrigger,
    TriggerType,
)
from doc_intelligence_hub.modules.analysis.rule_executor import (
    _execute_rule_with_context,
    execute_rule,
    execute_trigger,
)


def _make_rule(
    rule_id: str = "test-rule", enabled: bool = True, trigger_type: str = "manual"
) -> RuleConfig:
    return RuleConfig(
        id=rule_id,
        name="Test Rule",
        tier=RuleTier.BASIC,
        enabled=enabled,
        trigger=RuleTrigger(type=TriggerType(trigger_type)),
        routing=RuleRouting(),
    )


def _make_result(rule_id: str = "test-rule", success: bool = True, **kwargs) -> RuleExecutionResult:
    defaults = {
        "insight_type": InsightType.COMPARISON,
        "title": "Test Insight",
        "summary": "Test summary",
        "suggested_severity": InsightSeverity.INFO,
        "metric_values": {"pct_change": 25.0},
    }
    defaults.update(kwargs)
    return RuleExecutionResult(rule_id=rule_id, success=success, **defaults)


class TestExecuteRule:
    """Tests for execute_rule() — manual single-rule execution."""

    @pytest.mark.asyncio
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.get_rule")
    async def test_rule_not_found(self, mock_get_rule):
        mock_get_rule.return_value = None
        result = await execute_rule("nonexistent")
        assert result["success"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.get_rule")
    async def test_disabled_rule(self, mock_get_rule):
        mock_get_rule.return_value = _make_rule(enabled=False)
        result = await execute_rule("test-rule")
        assert result["success"] is False
        assert "disabled" in result["error"]

    @pytest.mark.asyncio
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.route_result")
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.build_context")
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.get_rule_class")
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.get_rule")
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.db")
    async def test_successful_execution(
        self, mock_db, mock_get_rule, mock_cls, mock_build, mock_route
    ):
        rule = _make_rule()
        mock_get_rule.return_value = rule
        mock_build.return_value = ContextData()

        # Mock rule class
        mock_instance = AsyncMock()
        mock_instance.execute.return_value = _make_result()
        mock_cls.return_value = MagicMock(return_value=mock_instance)

        mock_route.return_value = {"insight_id": "ins-1", "route": "informational"}

        result = await execute_rule("test-rule")
        assert result["success"] is True
        assert result["insight_id"] == "ins-1"

    @pytest.mark.asyncio
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.build_context")
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.get_rule_class")
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.get_rule")
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.db")
    async def test_dry_run_does_not_route(self, mock_db, mock_get_rule, mock_cls, mock_build):
        rule = _make_rule()
        mock_get_rule.return_value = rule
        mock_build.return_value = ContextData()

        mock_instance = AsyncMock()
        mock_instance.execute.return_value = _make_result()
        mock_cls.return_value = MagicMock(return_value=mock_instance)

        result = await execute_rule("test-rule", dry_run=True)
        assert result["dry_run"] is True
        assert result["success"] is True
        assert "insight_id" not in result  # No routing in dry run


class TestExecuteTrigger:
    """Tests for execute_trigger() — batch trigger execution."""

    @pytest.mark.asyncio
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.get_rules_for_trigger")
    async def test_no_matching_rules(self, mock_get_rules):
        mock_get_rules.return_value = []
        result = await execute_trigger(TriggerType.DOCUMENT_ADDED)
        assert result["rules_executed"] == 0
        assert result["message"] == "No matching rules found"

    @pytest.mark.asyncio
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.route_result")
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.build_context")
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.get_rule_class")
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.get_rules_for_trigger")
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.db")
    async def test_multiple_rules_executed(
        self, mock_db, mock_get_rules, mock_cls, mock_build, mock_route
    ):
        rules = [_make_rule("rule-1"), _make_rule("rule-2")]
        mock_get_rules.return_value = rules
        mock_build.return_value = ContextData()

        mock_instance = AsyncMock()
        mock_instance.execute.return_value = _make_result()
        mock_cls.return_value = MagicMock(return_value=mock_instance)
        mock_route.return_value = {"insight_id": "ins-1", "route": "informational"}

        result = await execute_trigger(TriggerType.DOCUMENT_ADDED)
        assert result["rules_executed"] == 2
        assert result["insights_created"] == 2

    @pytest.mark.asyncio
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.route_result")
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.build_context")
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.get_rule_class")
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.get_rules_for_trigger")
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.db")
    async def test_one_rule_fails_others_continue(
        self, mock_db, mock_get_rules, mock_cls, mock_build, mock_route
    ):
        rules = [_make_rule("rule-1"), _make_rule("rule-2")]
        mock_get_rules.return_value = rules
        mock_build.return_value = ContextData()

        call_count = 0

        async def side_effect(ctx):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Boom")
            return _make_result("rule-2")

        mock_instance = AsyncMock()
        mock_instance.execute.side_effect = side_effect
        mock_cls.return_value = MagicMock(return_value=mock_instance)
        mock_route.return_value = {"insight_id": "ins-1", "route": "informational"}

        result = await execute_trigger(TriggerType.DOCUMENT_ADDED)
        assert result["rules_executed"] == 2
        assert len(result["errors"]) == 1
        assert result["errors"][0]["rule_id"] == "rule-1"


class TestExecuteRuleWithContext:
    """Tests for the rule dispatch logic."""

    @pytest.mark.asyncio
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.get_rule_class")
    async def test_registered_class_used(self, mock_cls):
        rule = _make_rule()
        ctx = ContextData()

        mock_instance = AsyncMock()
        mock_instance.execute.return_value = _make_result()
        mock_cls.return_value = MagicMock(return_value=mock_instance)

        result = await _execute_rule_with_context(rule, ctx)
        assert result.success is True
        mock_instance.execute.assert_called_once_with(ctx)

    @pytest.mark.asyncio
    @patch("doc_intelligence_hub.modules.analysis.rule_executor.get_rule_class")
    async def test_no_class_no_analyzer_returns_error(self, mock_cls):
        mock_cls.return_value = None
        rule = _make_rule()
        rule.analyzer = ""
        ctx = ContextData()

        result = await _execute_rule_with_context(rule, ctx)
        assert result.success is False
        assert "No rule implementation found" in result.error
