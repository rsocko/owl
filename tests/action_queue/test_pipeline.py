"""Tests for pipeline resiliency: LLM timeouts/fallback, per-document error
isolation, and the overall pipeline duration timeout.
"""

import asyncio
import time

import pytest

from doc_intelligence_hub.modules.action_queue import analyzer as analyzer_module
from doc_intelligence_hub.modules.action_queue.config import settings as aq_settings
from doc_intelligence_hub.modules.action_queue.database import init_db
from doc_intelligence_hub.modules.action_queue.pipeline import Pipeline


VALID_EXTRACTION = {
    "actions": [{
        "action_type": "PAY",
        "title": "Pay bill",
        "summary": "test action",
        "due_date": None,
        "amount": 50.0,
        "urgency": "MEDIUM",
        "confidence": 90,
    }],
    "document_assessment": {
        "primary_action_index": 0,
        "correspondent": "Test Co",
        "overall_confidence": 90,
        "requires_action": True,
        "reasoning": "test",
        "text_quality": "good",
        "extracted_data": {},
    },
}


def _make_docs(n: int) -> list[dict]:
    """Documents without pre-fetched content, forcing get_document_content() calls."""
    return [
        {"id": i, "title": f"Doc {i}", "tag_names": ["Inbox"], "added": "2026-01-01"}
        for i in range(1, n + 1)
    ]


@pytest.fixture()
def db(tmp_path):
    """Isolated SQLite database for pipeline runs."""
    original = aq_settings.database_url
    aq_settings.database_url = f"sqlite:///{tmp_path / 'test_pipeline.db'}"
    init_db()
    yield
    aq_settings.database_url = original


class TestAnalyzerTimeout:
    """LLM_TIMEOUT_SECONDS behavior: a hung call should time out, retry once, then give up."""

    @pytest.mark.asyncio
    async def test_hanging_llm_call_times_out_and_falls_back(self, monkeypatch):
        monkeypatch.setattr(aq_settings, "llm_timeout_seconds", 0.05)

        call_count = 0

        async def hanging_chat_json(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(5)  # simulate a hung LLM call
            return {"actions": []}  # pragma: no cover - never reached

        monkeypatch.setattr(analyzer_module, "chat_json", hanging_chat_json)

        analyzer = analyzer_module.OllamaAnalyzer()
        start = time.monotonic()
        result = await analyzer.analyze_document({"id": 1, "title": "Test Doc", "content": "some content"})
        elapsed = time.monotonic() - start

        # Both the initial attempt and the retry should time out well before the 5s sleep,
        # returning None so the pipeline can fall back to the rule-based analyzer.
        assert result is None
        assert elapsed < 3.0
        assert call_count == 2  # initial attempt + one retry


class TestPipelineErrorIsolation:
    """One document raising an exception must not abort the rest of the run."""

    @pytest.mark.asyncio
    async def test_one_bad_document_does_not_stop_others(self, db, monkeypatch):
        docs = _make_docs(3)
        pipeline = Pipeline()

        monkeypatch.setattr(aq_settings, "write_to_paperless", False)

        async def fake_list_correspondents():
            return []

        async def fake_list_documents(**kwargs):
            return docs

        async def fake_get_document_content(doc_id):
            if doc_id == 2:
                raise RuntimeError("simulated fetch failure")
            return "Invoice: your payment of $50.00 is due by the end of the month."

        async def fake_health_check():
            return False  # force the rule-based fallback path, no real LLM calls

        monkeypatch.setattr(pipeline.paperless, "list_correspondents", fake_list_correspondents)
        monkeypatch.setattr(pipeline.paperless, "list_documents", fake_list_documents)
        monkeypatch.setattr(pipeline.paperless, "get_document_content", fake_get_document_content)
        monkeypatch.setattr(pipeline.analyzer, "health_check", fake_health_check)
        monkeypatch.setattr(pipeline.fallback_analyzer, "analyze_document", lambda doc: VALID_EXTRACTION)

        stats = await pipeline.run(force=True, dry_run=False)

        assert stats["failed"] == 1
        assert stats["processed"] == 2
        assert len(stats["errors"]) == 1
        assert stats["errors"][0]["document_id"] == 2
        assert "simulated fetch failure" in stats["errors"][0]["error"]
        assert stats["timed_out"] is False


class TestPipelineOverallTimeout:
    """PIPELINE_MAX_DURATION_SECONDS behavior: stop processing once exceeded."""

    @pytest.mark.asyncio
    async def test_pipeline_stops_after_max_duration(self, db, monkeypatch):
        docs = _make_docs(5)
        pipeline = Pipeline()

        monkeypatch.setattr(aq_settings, "pipeline_max_duration_seconds", 0.15)
        monkeypatch.setattr(aq_settings, "write_to_paperless", False)

        async def fake_list_correspondents():
            return []

        async def fake_list_documents(**kwargs):
            return docs

        async def fake_get_document_content(doc_id):
            await asyncio.sleep(0.1)  # simulate slow per-document processing
            return "Invoice: your payment of $50.00 is due by the end of the month."

        async def fake_health_check():
            return False

        monkeypatch.setattr(pipeline.paperless, "list_correspondents", fake_list_correspondents)
        monkeypatch.setattr(pipeline.paperless, "list_documents", fake_list_documents)
        monkeypatch.setattr(pipeline.paperless, "get_document_content", fake_get_document_content)
        monkeypatch.setattr(pipeline.analyzer, "health_check", fake_health_check)
        monkeypatch.setattr(pipeline.fallback_analyzer, "analyze_document", lambda doc: VALID_EXTRACTION)

        stats = await pipeline.run(force=True, dry_run=False)

        assert stats["timed_out"] is True
        assert stats["skipped"] > 0
        assert stats["processed"] + stats["skipped"] == 5
        assert stats["processed"] < 5  # did not get through all documents


class TestPipelineFetchLimit:
    """When `limit` is provided, it should be pushed down into the Paperless fetch."""

    @pytest.mark.asyncio
    async def test_limit_is_forwarded_to_list_documents(self, db, monkeypatch):
        docs = _make_docs(2)
        pipeline = Pipeline()

        monkeypatch.setattr(aq_settings, "write_to_paperless", False)

        received_kwargs = {}

        async def fake_list_correspondents():
            return []

        async def fake_list_documents(**kwargs):
            received_kwargs.update(kwargs)
            return docs

        async def fake_get_document_content(doc_id):
            return "Invoice: your payment of $50.00 is due by the end of the month."

        async def fake_health_check():
            return False

        monkeypatch.setattr(pipeline.paperless, "list_correspondents", fake_list_correspondents)
        monkeypatch.setattr(pipeline.paperless, "list_documents", fake_list_documents)
        monkeypatch.setattr(pipeline.paperless, "get_document_content", fake_get_document_content)
        monkeypatch.setattr(pipeline.analyzer, "health_check", fake_health_check)
        monkeypatch.setattr(pipeline.fallback_analyzer, "analyze_document", lambda doc: VALID_EXTRACTION)

        await pipeline.run(force=True, dry_run=False, limit=5)

        # force=True means no post-fetch filtering is needed, so the exact
        # requested limit should be pushed straight down to the Paperless query.
        assert received_kwargs.get("limit") == 5

    @pytest.mark.asyncio
    async def test_no_limit_forwards_none(self, db, monkeypatch):
        docs = _make_docs(2)
        pipeline = Pipeline()

        monkeypatch.setattr(aq_settings, "write_to_paperless", False)

        received_kwargs = {}

        async def fake_list_correspondents():
            return []

        async def fake_list_documents(**kwargs):
            received_kwargs.update(kwargs)
            return docs

        async def fake_get_document_content(doc_id):
            return "Invoice: your payment of $50.00 is due by the end of the month."

        async def fake_health_check():
            return False

        monkeypatch.setattr(pipeline.paperless, "list_correspondents", fake_list_correspondents)
        monkeypatch.setattr(pipeline.paperless, "list_documents", fake_list_documents)
        monkeypatch.setattr(pipeline.paperless, "get_document_content", fake_get_document_content)
        monkeypatch.setattr(pipeline.analyzer, "health_check", fake_health_check)
        monkeypatch.setattr(pipeline.fallback_analyzer, "analyze_document", lambda doc: VALID_EXTRACTION)

        await pipeline.run(force=True, dry_run=False)

        assert received_kwargs.get("limit") is None


class TestPipelineFetchTimeout:
    """The fetch phase itself must be bounded by pipeline_fetch_timeout_seconds,
    independent of the per-document analysis timeout loop.
    """

    @pytest.mark.asyncio
    async def test_fetch_phase_timeout_aborts_run(self, db, monkeypatch):
        pipeline = Pipeline()

        monkeypatch.setattr(aq_settings, "pipeline_fetch_timeout_seconds", 0.05)
        monkeypatch.setattr(aq_settings, "write_to_paperless", False)

        async def fake_list_correspondents():
            return []

        async def hanging_list_documents(**kwargs):
            await asyncio.sleep(5)  # simulate a Paperless instance that never responds in time
            return []  # pragma: no cover - never reached

        monkeypatch.setattr(pipeline.paperless, "list_correspondents", fake_list_correspondents)
        monkeypatch.setattr(pipeline.paperless, "list_documents", hanging_list_documents)

        start = time.monotonic()
        stats = await pipeline.run(force=True, dry_run=False, limit=5)
        elapsed = time.monotonic() - start

        assert stats.get("fetch_timed_out") is True
        assert stats["processed"] == 0
        # Should abort well before the simulated 5s hang.
        assert elapsed < 2.0
