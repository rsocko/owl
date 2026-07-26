"""Tests for basic rule execution."""

from __future__ import annotations

import pytest

from doc_intelligence_hub.modules.analysis.models import (
    ContextData,
    InsightType,
    RuleConfig,
    RuleTier,
    RuleTrigger,
    TriggerType,
)
from doc_intelligence_hub.modules.analysis.rules.basic_rules import (
    EobMatchReview,
    MissingStatement,
    MonthlySpendComparison,
    SpendSpike,
    StatementReceived,
)


def _make_config(rule_id: str, **params) -> RuleConfig:
    return RuleConfig(
        id=rule_id,
        name=f"Test {rule_id}",
        tier=RuleTier.BASIC,
        trigger=RuleTrigger(type=TriggerType.MANUAL),
        params=params,
    )


class TestMonthlySpendComparison:
    @pytest.mark.asyncio
    async def test_spend_above_average(self):
        config = _make_config("monthly-spend-comparison", comparison_window=3)
        rule = MonthlySpendComparison(config)

        ctx = ContextData(
            current_document={"id": 100, "total_amount": 3000, "correspondent": {"name": "Chase"}},
            series_history=[
                {"total_amount": 2000},
                {"total_amount": 2200},
                {"total_amount": 1800},
            ],
        )

        result = await rule.execute(ctx)
        assert result.success is True
        assert result.insight_type == InsightType.COMPARISON
        assert result.metric_values["pct_change"] > 0
        assert "above" in result.title.lower()

    @pytest.mark.asyncio
    async def test_insufficient_history(self):
        config = _make_config("monthly-spend-comparison", comparison_window=3)
        rule = MonthlySpendComparison(config)

        ctx = ContextData(
            current_document={"id": 100, "total_amount": 3000},
            series_history=[{"total_amount": 2000}],
        )

        result = await rule.execute(ctx)
        assert result.success is False
        assert "insufficient" in result.error.lower()

    @pytest.mark.asyncio
    async def test_no_amount_field(self):
        config = _make_config("monthly-spend-comparison")
        rule = MonthlySpendComparison(config)

        ctx = ContextData(current_document={"id": 100, "title": "No amount"})
        result = await rule.execute(ctx)
        assert result.success is False


class TestEobMatchReview:
    @pytest.mark.asyncio
    async def test_low_confidence_flagged(self):
        config = _make_config("eob-match-review", confidence_threshold=75)
        rule = EobMatchReview(config)

        ctx = ContextData(
            current_document={"id": 200},
            related_matches=[{"score": 60, "bill_id": 300}],
        )

        result = await rule.execute(ctx)
        assert result.success is True
        assert "60%" in result.title

    @pytest.mark.asyncio
    async def test_high_confidence_skipped(self):
        config = _make_config("eob-match-review", confidence_threshold=75)
        rule = EobMatchReview(config)

        ctx = ContextData(
            current_document={"id": 200},
            related_matches=[{"score": 90, "bill_id": 300}],
        )

        result = await rule.execute(ctx)
        assert result.success is False


class TestMissingStatement:
    @pytest.mark.asyncio
    async def test_missing_statement_detected(self):
        config = _make_config("missing-statement", grace_days=7)
        rule = MissingStatement(config)

        ctx = ContextData(
            series_info={
                "id": "s1",
                "name": "Chase Sapphire",
                "recurrence": "monthly",
                "last_seen": "2024-01-01T00:00:00",
            }
        )

        result = await rule.execute(ctx)
        assert result.success is True
        assert "missing" in result.title.lower()

    @pytest.mark.asyncio
    async def test_no_series_info(self):
        config = _make_config("missing-statement")
        rule = MissingStatement(config)

        ctx = ContextData()
        result = await rule.execute(ctx)
        assert result.success is False


class TestStatementReceived:
    @pytest.mark.asyncio
    async def test_statement_received(self):
        config = _make_config("statement-received")
        rule = StatementReceived(config)

        ctx = ContextData(
            current_document={
                "id": 100,
                "correspondent": {"name": "Comcast"},
                "created": "2024-06-15T00:00:00",
            },
        )

        result = await rule.execute(ctx)
        assert result.success is True
        assert "received" in result.title.lower()


class TestSpendSpike:
    @pytest.mark.asyncio
    async def test_spike_detected(self):
        config = _make_config("spend-spike", spike_threshold_pct=30)
        rule = SpendSpike(config)

        ctx = ContextData(
            current_document={"id": 100, "total_amount": 2000, "correspondent": {"name": "Amex"}},
            series_history=[
                {"total_amount": 1000},
                {"total_amount": 1100},
                {"total_amount": 900},
            ],
        )

        result = await rule.execute(ctx)
        assert result.success is True
        assert "spike" in result.title.lower()

    @pytest.mark.asyncio
    async def test_no_spike(self):
        config = _make_config("spend-spike", spike_threshold_pct=30)
        rule = SpendSpike(config)

        ctx = ContextData(
            current_document={"id": 100, "total_amount": 1050},
            series_history=[
                {"total_amount": 1000},
                {"total_amount": 1100},
            ],
        )

        result = await rule.execute(ctx)
        assert result.success is False
