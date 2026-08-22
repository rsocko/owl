"""Tests for the Action Queue trigger rule."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from doc_intelligence_hub.modules.action_queue.fast_path import FastPathResult
from doc_intelligence_hub.modules.analysis.models import (
    ContextData,
    RuleConfig,
    RuleTier,
    RuleTrigger,
    TriggerType,
)
from doc_intelligence_hub.modules.analysis.rules import action_queue_rule as rule_module
from doc_intelligence_hub.modules.analysis.rules.action_queue_rule import ActionQueueTriggerRule


def _make_rule(**params) -> ActionQueueTriggerRule:
    config = RuleConfig(
        id="action-queue-trigger",
        name="Immediate Action Queue Analysis",
        tier=RuleTier.BASIC,
        trigger=RuleTrigger(type=TriggerType.DOCUMENT_ADDED),
        params=params,
    )
    return ActionQueueTriggerRule(config)


@pytest.mark.asyncio
async def test_rule_runs_pipeline_for_valid_document(monkeypatch):
    trigger = AsyncMock(
        return_value=FastPathResult(
            status="completed",
            document_id=42,
            pipeline_result={"processed": 1},
        )
    )
    monkeypatch.setattr(rule_module, "trigger_fast_path_analysis", trigger)

    result = await _make_rule().execute(ContextData(current_document={"id": 42}))

    assert result.success is True
    assert result.detail["fast_path_status"] == "completed"
    trigger.assert_awaited_once_with(42, force=False, dry_run=False)


@pytest.mark.asyncio
async def test_execution_dry_run_overrides_rule_setting(monkeypatch):
    trigger = AsyncMock(
        return_value=FastPathResult(status="completed", document_id=42, pipeline_result={})
    )
    monkeypatch.setattr(rule_module, "trigger_fast_path_analysis", trigger)
    context = ContextData(
        current_document={"id": 42},
        extra={"_execution_dry_run": True},
    )

    await _make_rule(dry_run=False, force=True).execute(context)

    trigger.assert_awaited_once_with(42, force=True, dry_run=True)


@pytest.mark.asyncio
async def test_rule_rejects_missing_document_scope(monkeypatch):
    trigger = AsyncMock()
    monkeypatch.setattr(rule_module, "trigger_fast_path_analysis", trigger)

    result = await _make_rule().execute(ContextData())

    assert result.success is False
    assert "document ID" in result.error
    trigger.assert_not_awaited()


@pytest.mark.asyncio
async def test_rule_surfaces_capacity_rejection(monkeypatch):
    trigger = AsyncMock(
        return_value=FastPathResult(
            status="rejected",
            document_id=42,
            reason="fast-path queue capacity reached",
        )
    )
    monkeypatch.setattr(rule_module, "trigger_fast_path_analysis", trigger)

    result = await _make_rule().execute(ContextData(current_document={"id": 42}))

    assert result.success is False
    assert result.error == "fast-path queue capacity reached"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    [
        (FastPathResult(status="already_pending", document_id=42), "already_pending"),
        (
            FastPathResult(
                status="completed",
                document_id=42,
                pipeline_result={"processed": 0, "skipped": 1},
            ),
            "already_processed",
        ),
    ],
)
async def test_deduplicated_outcome_is_not_routed(monkeypatch, outcome, expected_status):
    monkeypatch.setattr(
        rule_module,
        "trigger_fast_path_analysis",
        AsyncMock(return_value=outcome),
    )

    result = await _make_rule().execute(ContextData(current_document={"id": 42}))

    assert result.success is True
    assert result.should_route is False
    assert result.detail["fast_path_status"] == expected_status
