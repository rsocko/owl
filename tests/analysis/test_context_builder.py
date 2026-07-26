"""Tests for the context builder module."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from doc_intelligence_hub.modules.analysis.context_builder import (
    build_batch_context,
    build_context,
)
from doc_intelligence_hub.modules.analysis.models import (
    RuleConfig,
    RuleRouting,
    RuleTier,
    RuleTrigger,
    TriggerType,
)


def _make_rule(context: list | None = None, trigger_type: str = "manual") -> RuleConfig:
    return RuleConfig(
        id="test-rule",
        name="Test Rule",
        tier=RuleTier.BASIC,
        enabled=True,
        trigger=RuleTrigger(type=TriggerType(trigger_type)),
        context=context,
        routing=RuleRouting(),
    )


class TestBuildContext:
    """Tests for build_context() — single document context assembly."""

    @pytest.mark.asyncio
    @patch("doc_intelligence_hub.modules.analysis.context_builder._fetch_document")
    async def test_fetches_document_when_requested(self, mock_fetch):
        mock_fetch.return_value = {"id": 42, "title": "Test Doc"}
        rule = _make_rule(context=["current_document"])

        ctx = await build_context(rule, document_id=42)
        assert ctx.current_document is not None
        assert ctx.current_document["id"] == 42
        mock_fetch.assert_called_once_with(42)

    @pytest.mark.asyncio
    @patch("doc_intelligence_hub.modules.analysis.context_builder._fetch_document")
    async def test_returns_none_when_fetch_fails(self, mock_fetch):
        """After our fix, _fetch_document returns None on failure instead of a fake dict."""
        mock_fetch.return_value = None
        rule = _make_rule(context=["current_document"])

        ctx = await build_context(rule, document_id=42)
        assert ctx.current_document is None

    @pytest.mark.asyncio
    async def test_no_document_id_skips_fetch(self):
        rule = _make_rule(context=["current_document"])
        ctx = await build_context(rule)
        assert ctx.current_document is None

    @pytest.mark.asyncio
    @patch("doc_intelligence_hub.modules.analysis.context_builder._fetch_document")
    @patch("doc_intelligence_hub.modules.analysis.context_builder._fetch_series_info")
    @patch("doc_intelligence_hub.modules.analysis.context_builder._fetch_series_history")
    @patch("doc_intelligence_hub.modules.analysis.context_builder._extract_series_id")
    async def test_fetches_series_history(self, mock_series_id, mock_history, mock_info, mock_doc):
        mock_doc.return_value = {"id": 1, "correspondent": {"name": "BCBS"}}
        mock_series_id.return_value = "series-1"
        mock_info.return_value = {"id": "series-1", "name": "BCBS Monthly"}
        mock_history.return_value = [{"id": 1}, {"id": 2}]

        rule = _make_rule(context=["current_document", {"series_history": 6}])
        ctx = await build_context(rule, document_id=1)

        assert ctx.series_info is not None
        assert len(ctx.series_history) == 2
        mock_history.assert_called_once_with("series-1", limit=6)

    @pytest.mark.asyncio
    @patch("doc_intelligence_hub.modules.analysis.context_builder._fetch_document")
    async def test_params_passed_as_extra(self, mock_doc):
        mock_doc.return_value = {"id": 1}
        rule = _make_rule(context=["current_document"])
        rule.params = {"threshold": 0.85}

        ctx = await build_context(rule, document_id=1)
        assert ctx.extra.get("threshold") == 0.85


class TestBuildBatchContext:
    """Tests for build_batch_context() — scheduled batch context assembly."""

    @pytest.mark.asyncio
    @patch("doc_intelligence_hub.modules.analysis.context_builder._fetch_series_history")
    @patch("doc_intelligence_hub.modules.analysis.context_builder._fetch_all_series")
    async def test_returns_context_per_series(self, mock_all_series, mock_history):
        mock_all_series.return_value = [
            {"id": "s1", "name": "Series 1"},
            {"id": "s2", "name": "Series 2"},
        ]
        mock_history.return_value = [
            {"id": 1, "total_amount": 100},
            {"id": 2, "total_amount": 200},
        ]

        rule = _make_rule(trigger_type="schedule")
        contexts = await build_batch_context(rule)

        assert len(contexts) == 2
        assert contexts[0].series_info["id"] == "s1"
        assert contexts[0].current_document is not None  # Most recent from history

    @pytest.mark.asyncio
    @patch("doc_intelligence_hub.modules.analysis.context_builder._fetch_all_series")
    async def test_empty_series_list(self, mock_all_series):
        mock_all_series.return_value = []
        rule = _make_rule(trigger_type="schedule")
        contexts = await build_batch_context(rule)
        assert len(contexts) == 0
