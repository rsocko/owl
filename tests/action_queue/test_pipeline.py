"""Tests for pipeline resiliency: LLM timeouts/fallback, per-document error
isolation, and the overall pipeline duration timeout.
"""

import asyncio
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from doc_intelligence_hub.modules.action_queue import analyzer as analyzer_module
from doc_intelligence_hub.modules.action_queue.config import settings as aq_settings
from doc_intelligence_hub.modules.action_queue.database import (
    Action,
    ProcessingHistory,
    get_session,
    init_db,
)
from doc_intelligence_hub.modules.action_queue.pipeline import Pipeline

VALID_EXTRACTION = {
    "actions": [
        {
            "action_type": "PAY",
            "title": "Pay bill",
            "summary": "test action",
            "due_date": None,
            "amount": 50.0,
            "urgency": "MEDIUM",
            "confidence": 90,
        }
    ],
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


@pytest.mark.asyncio
async def test_all_documents_scope_does_not_apply_monitor_tags():
    pipeline = Pipeline()
    pipeline.paperless.list_documents = AsyncMock(return_value=[])

    await pipeline._fetch_documents(
        document_id=None,
        saved_view_id=None,
        tag_override=None,
        all_documents=True,
        created_after="2026-01-01",
        created_before=None,
        added_after=None,
        added_before=None,
        correspondent="Utility Co",
        document_type=None,
        fetch_limit=None,
    )

    pipeline.paperless.list_documents.assert_awaited_once_with(
        created_after="2026-01-01",
        created_before=None,
        added_after=None,
        added_before=None,
        correspondent="Utility Co",
        document_type=None,
        limit=None,
    )


@pytest.mark.asyncio
async def test_tag_scope_splits_comma_separated_tag_names():
    pipeline = Pipeline()
    pipeline.paperless.list_documents = AsyncMock(return_value=[])

    await pipeline._fetch_documents(
        document_id=None,
        saved_view_id=None,
        tag_override="Inbox, Receipt",
        all_documents=False,
        created_after=None,
        created_before=None,
        added_after=None,
        added_before=None,
        correspondent=None,
        document_type=None,
        fetch_limit=25,
    )

    pipeline.paperless.list_documents.assert_awaited_once_with(
        tags=["Inbox", "Receipt"],
        limit=25,
    )


@pytest.fixture()
def db(tmp_path):
    """Isolated SQLite database for pipeline runs."""
    original = aq_settings.database_url
    from doc_intelligence_hub.modules.analysis_invalidation import config as ai_config
    from doc_intelligence_hub.modules.analysis_invalidation.database import (
        init_db as ai_init_db,
    )
    from doc_intelligence_hub.modules.triage import database as triage_database

    aq_settings.database_url = f"sqlite:///{tmp_path / 'test_pipeline.db'}"
    triage_database.configure(f"sqlite:///{tmp_path / 'test_triage.db'}")
    original_ai_db_url = ai_config.settings.database_url
    ai_config.settings.database_url = f"sqlite:///{tmp_path / 'test_analysis_invalidation.db'}"
    init_db()
    triage_database.init_db()
    ai_init_db()
    yield
    aq_settings.database_url = original
    triage_database.configure("sqlite:///data/triage.db")
    ai_config.settings.database_url = original_ai_db_url


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
        result = await analyzer.analyze_document(
            {"id": 1, "title": "Test Doc", "content": "some content"}
        )
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
        monkeypatch.setattr(
            pipeline.fallback_analyzer, "analyze_document", lambda doc: VALID_EXTRACTION
        )

        stats = await pipeline.run(force=True, dry_run=False)

        assert stats["failed"] == 1
        assert stats["processed"] == 2
        assert len(stats["errors"]) == 1
        assert stats["errors"][0]["document_id"] == 2
        assert "simulated fetch failure" in stats["errors"][0]["error"]
        assert stats["timed_out"] is False

    @pytest.mark.asyncio
    async def test_reanalysis_releases_database_during_paperless_enrichment(self, db, monkeypatch):
        pipeline = Pipeline()
        enrichment_started = asyncio.Event()
        finish_enrichment = asyncio.Event()

        monkeypatch.setattr(aq_settings, "write_to_paperless", True)
        monkeypatch.setattr(pipeline.paperless, "list_correspondents", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            pipeline.paperless,
            "fetch_all_metadata",
            AsyncMock(return_value=({}, {}, {})),
        )
        monkeypatch.setattr(
            pipeline.paperless,
            "list_custom_fields",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            pipeline.paperless,
            "list_documents",
            AsyncMock(return_value=_make_docs(1)),
        )
        monkeypatch.setattr(
            pipeline.paperless,
            "get_document_content",
            AsyncMock(return_value="Invoice: payment of $50.00 is due this month."),
        )
        monkeypatch.setattr(pipeline.analyzer, "health_check", AsyncMock(return_value=False))
        monkeypatch.setattr(
            pipeline.fallback_analyzer,
            "analyze_document",
            lambda doc: VALID_EXTRACTION,
        )
        pipeline.enricher.ensure_custom_fields_exist = AsyncMock(return_value={})
        monkeypatch.setattr(
            "doc_intelligence_hub.modules.action_queue.pipeline.project_obligations_to_paperless",
            AsyncMock(
                return_value={
                    "synced": True,
                    "attempted": 1,
                    "succeeded": 1,
                    "failed": 0,
                    "documents": [1],
                    "errors": [],
                    "error": None,
                }
            ),
        )

        async def paused_enrichment(*args, **kwargs):
            enrichment_started.set()
            await finish_enrichment.wait()

        pipeline.enricher.enrich_document = paused_enrichment
        run_task = asyncio.create_task(pipeline.run(force=True, dry_run=False))

        await asyncio.wait_for(enrichment_started.wait(), timeout=2)
        session = get_session()
        try:
            action = session.query(Action).filter_by(document_id=1).one()
            action.summary = "Updated while Paperless enrichment was running"
            session.commit()
        finally:
            session.close()
            finish_enrichment.set()

        stats = await asyncio.wait_for(run_task, timeout=2)
        assert stats["processed"] == 1

    def test_sqlite_engine_uses_concurrency_pragmas(self, db):
        from doc_intelligence_hub.modules.action_queue.database import get_engine

        engine = get_engine()
        with engine.connect() as connection:
            journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
            busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()

        assert journal_mode == "wal"
        assert busy_timeout == 30_000

    @pytest.mark.asyncio
    async def test_deleted_source_is_confirmed_before_action_is_hidden(self, db):
        session = get_session()
        try:
            session.add(
                Action(
                    document_id=42,
                    document_title="Deleted bill",
                    action_type="PAY",
                    title="Pay deleted bill",
                    status="pending",
                )
            )
            session.add(ProcessingHistory(document_id=42, success=1))
            session.commit()
        finally:
            session.close()

        pipeline = Pipeline()
        request = httpx.Request("GET", "http://paperless.test/api/documents/42/")
        response = httpx.Response(404, request=request)
        pipeline.paperless.get_document = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "not found",
                request=request,
                response=response,
            )
        )

        assert await pipeline._reconcile_deleted_sources(set()) == 1

        session = get_session()
        try:
            action = session.query(Action).filter_by(document_id=42).one()
            assert action.status == "source_deleted"
            assert action.action_ready is False
            assert session.query(ProcessingHistory).filter_by(document_id=42).first() is None
        finally:
            session.close()

    @pytest.mark.asyncio
    async def test_transient_source_lookup_failure_preserves_action(self, db):
        session = get_session()
        try:
            session.add(
                Action(
                    document_id=42,
                    document_title="Temporarily unavailable bill",
                    action_type="PAY",
                    title="Pay bill",
                    status="pending",
                )
            )
            session.commit()
        finally:
            session.close()

        pipeline = Pipeline()
        pipeline.paperless.get_document = AsyncMock(
            side_effect=httpx.ConnectError("Paperless unavailable")
        )

        assert await pipeline._reconcile_deleted_sources(set()) == 0

        session = get_session()
        try:
            action = session.query(Action).filter_by(document_id=42).one()
            assert action.status == "pending"
            assert action.action_ready is True
        finally:
            session.close()

    def test_resolves_paperless_metadata_for_analysis(self):
        pipeline = Pipeline()
        pipeline._correspondent_cache = {7: "City Utilities"}
        pipeline._doc_type_cache = {5: "Receipt"}
        pipeline._tag_cache = {9: "Inbox", 12: "Utilities"}
        document = {
            "correspondent": 7,
            "document_type": 5,
            "tags": [9, 12],
        }

        pipeline._resolve_document_metadata(document)

        assert document["correspondent_name"] == "City Utilities"
        assert document["document_type_name"] == "Receipt"
        assert document["tag_names"] == ["Inbox", "Utilities"]

    @pytest.mark.asyncio
    async def test_repeated_non_force_run_skips_processed_document(self, db, monkeypatch):
        docs = _make_docs(1)
        analyze_calls = 0

        async def fake_list_correspondents(self):
            return []

        async def fake_fetch_all_metadata(self, **kwargs):
            return {}, {}, {}

        async def fake_list_documents(self, **kwargs):
            return docs

        async def fake_get_document_content(self, document_id):
            return "Invoice: your payment of $50.00 is due by the end of the month."

        async def fake_health_check(self):
            return False

        def fake_analyze(self, document):
            nonlocal analyze_calls
            analyze_calls += 1
            return VALID_EXTRACTION

        monkeypatch.setattr(aq_settings, "write_to_paperless", False)
        monkeypatch.setattr(
            "doc_intelligence_hub.core.paperless.PaperlessClient.list_correspondents",
            fake_list_correspondents,
        )
        monkeypatch.setattr(
            "doc_intelligence_hub.core.paperless.PaperlessClient.fetch_all_metadata",
            fake_fetch_all_metadata,
        )
        monkeypatch.setattr(
            "doc_intelligence_hub.core.paperless.PaperlessClient.list_documents",
            fake_list_documents,
        )
        monkeypatch.setattr(
            "doc_intelligence_hub.core.paperless.PaperlessClient.get_document_content",
            fake_get_document_content,
        )
        monkeypatch.setattr(
            "doc_intelligence_hub.modules.action_queue.analyzer.OllamaAnalyzer.health_check",
            fake_health_check,
        )
        monkeypatch.setattr(
            "doc_intelligence_hub.modules.action_queue.fallback_analyzer.RuleBasedAnalyzer.analyze_document",
            fake_analyze,
        )

        first = await Pipeline().run(force=False)
        repeated = await Pipeline().run(force=False)

        assert first["processed"] == 1
        assert repeated["processed"] == 0
        assert repeated["skipped"] == 1
        assert analyze_calls == 1

    @pytest.mark.asyncio
    async def test_failed_action_enrichment_remains_retryable(self, db, monkeypatch):
        from doc_intelligence_hub.modules.action_queue.database import ProcessingHistory

        pipeline = Pipeline()
        monkeypatch.setattr(aq_settings, "write_to_paperless", True)
        monkeypatch.setattr(pipeline.paperless, "list_correspondents", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            pipeline.paperless,
            "fetch_all_metadata",
            AsyncMock(return_value=({}, {}, {})),
        )
        monkeypatch.setattr(
            pipeline.paperless,
            "list_documents",
            AsyncMock(return_value=_make_docs(1)),
        )
        monkeypatch.setattr(
            pipeline.paperless,
            "get_document_content",
            AsyncMock(return_value="Invoice: payment of $50.00 is due."),
        )
        monkeypatch.setattr(pipeline.analyzer, "health_check", AsyncMock(return_value=False))
        monkeypatch.setattr(
            pipeline.fallback_analyzer,
            "analyze_document",
            lambda doc: VALID_EXTRACTION,
        )
        pipeline.enricher.ensure_custom_fields_exist = AsyncMock(return_value={})
        pipeline.enricher.enrich_document = AsyncMock(
            side_effect=RuntimeError("Paperless rejected custom fields")
        )

        stats = await pipeline.run(force=True, dry_run=False)

        session = get_session()
        try:
            history = session.query(ProcessingHistory).filter_by(document_id=1).one()
            assert history.success == 0
            assert history.disposition == "action_enrichment_failed"
            assert history.error_message == "Paperless rejected custom fields"
        finally:
            session.close()
        assert stats["processed"] == 1
        assert stats["enrichment_failed"] == 1

    def test_store_action_resolves_numeric_tag_ids(self, db):
        pipeline = Pipeline()
        pipeline._correspondent_cache = {}
        pipeline._doc_type_cache = {}
        pipeline._tag_cache = {9: "Inbox", 343: "Utilities"}
        session = get_session()
        try:
            pipeline._store_action(
                session,
                {"id": 42, "title": "Electric Bill", "tags": [9, 343]},
                VALID_EXTRACTION["actions"][0],
                VALID_EXTRACTION["document_assessment"],
            )
            session.commit()

            action = session.query(Action).filter_by(document_id=42).one()
            assert action.tags == ["Inbox", "Utilities"]
        finally:
            session.close()

    def test_store_action_does_not_transfer_corrections_by_ordinal(self, db):
        pipeline = Pipeline()
        pipeline._correspondent_cache = {}
        pipeline._doc_type_cache = {}
        pipeline._tag_cache = {}
        session = get_session()
        try:
            protected = Action(
                document_id=42,
                document_title="Multi-action notice",
                action_type="PAY",
                title="Pay accepted invoice",
                amount=395.0,
                status="pending",
                action_ready=True,
                review_state="ready",
                human_corrected_fields=["title", "amount"],
                action_index=0,
            )
            session.add(protected)
            session.commit()

            created = pipeline._store_action(
                session,
                {"id": 42, "title": "Multi-action notice", "tags": []},
                {
                    "action_type": "RESPOND",
                    "title": "Respond to new notice",
                    "summary": "Newly inferred sibling",
                    "due_date": None,
                    "amount": None,
                    "urgency": "LOW",
                    "confidence": 90,
                },
                VALID_EXTRACTION["document_assessment"],
                action_index=0,
            )
            session.commit()

            assert created.id != protected.id
            assert created.action_index == 1
            assert protected.title == "Pay accepted invoice"
            assert protected.amount == 395.0
        finally:
            session.close()

    @pytest.mark.asyncio
    async def test_low_confidence_action_routes_to_needs_review(self, db, monkeypatch):
        pipeline = Pipeline()
        extraction = {
            **VALID_EXTRACTION,
            "actions": [{**VALID_EXTRACTION["actions"][0], "confidence": 55}],
            "document_assessment": {
                **VALID_EXTRACTION["document_assessment"],
                "overall_confidence": 55,
            },
        }
        monkeypatch.setattr(aq_settings, "write_to_paperless", False)
        monkeypatch.setattr(aq_settings, "confidence_threshold", 70)
        monkeypatch.setattr(pipeline.paperless, "list_correspondents", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            pipeline.paperless, "fetch_all_metadata", AsyncMock(return_value=({}, {}, {}))
        )
        monkeypatch.setattr(
            pipeline.paperless, "list_documents", AsyncMock(return_value=_make_docs(1))
        )
        monkeypatch.setattr(
            pipeline.paperless,
            "get_document_content",
            AsyncMock(return_value="Account number 123456789. Payment of $50 is due."),
        )
        monkeypatch.setattr(pipeline.analyzer, "health_check", AsyncMock(return_value=False))
        monkeypatch.setattr(
            pipeline.fallback_analyzer, "analyze_document", lambda document: extraction
        )

        stats = await pipeline.run(force=True, dry_run=False)

        session = get_session()
        try:
            action = session.query(Action).one()
            assert action.action_ready is False
            assert action.review_state == "needs_review"
            assert action.review_item_id
            assert action.extracted_data["account_identifier"] == "ending 6789"
            assert "account_number" not in action.extracted_data
        finally:
            session.close()
        assert stats["processed"] == 0
        assert stats["no_action"] == 1
        assert stats["documents_scanned"] == 1
        assert stats["outcomes"] == {
            "action_required": 0,
            "no_action": 0,
            "needs_review": 1,
            "unreadable": 0,
            "failed": 0,
        }

    @pytest.mark.asyncio
    async def test_receipt_with_balance_due_routes_to_needs_review(self, db, monkeypatch):
        pipeline = Pipeline()
        document = {
            "id": 1,
            "title": "Payment receipt",
            "document_type": "Receipt",
            "tag_names": ["Inbox"],
            "added": "2026-01-01",
        }
        monkeypatch.setattr(aq_settings, "write_to_paperless", False)
        monkeypatch.setattr(aq_settings, "confidence_threshold", 70)
        monkeypatch.setattr(pipeline.paperless, "list_correspondents", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            pipeline.paperless, "fetch_all_metadata", AsyncMock(return_value=({}, {}, {}))
        )
        monkeypatch.setattr(
            pipeline.paperless, "list_documents", AsyncMock(return_value=[document])
        )
        monkeypatch.setattr(
            pipeline.paperless,
            "get_document_content",
            AsyncMock(return_value="We received your payment. Remaining balance due: $42.00."),
        )
        monkeypatch.setattr(pipeline.analyzer, "health_check", AsyncMock(return_value=False))

        stats = await pipeline.run(force=True, dry_run=False)

        session = get_session()
        try:
            action = session.query(Action).one()
            assert action.action_type == "REVIEW"
            assert action.action_ready is False
            assert action.review_state == "needs_review"
            assert action.review_item_id
        finally:
            session.close()
        assert stats["outcomes"]["needs_review"] == 1

    @pytest.mark.asyncio
    async def test_reanalysis_preserves_accepted_human_correction(self, db, monkeypatch):
        pipeline = Pipeline()
        reanalysis = {
            **VALID_EXTRACTION,
            "actions": [
                {
                    **VALID_EXTRACTION["actions"][0],
                    "title": "Model changed title",
                    "amount": None,
                    "confidence": 45,
                }
            ],
            "document_assessment": {
                **VALID_EXTRACTION["document_assessment"],
                "overall_confidence": 45,
            },
        }
        monkeypatch.setattr(aq_settings, "write_to_paperless", False)
        monkeypatch.setattr(aq_settings, "confidence_threshold", 70)
        monkeypatch.setattr(pipeline.paperless, "list_correspondents", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            pipeline.paperless, "fetch_all_metadata", AsyncMock(return_value=({}, {}, {}))
        )
        monkeypatch.setattr(
            pipeline.paperless, "list_documents", AsyncMock(return_value=_make_docs(1))
        )
        monkeypatch.setattr(
            pipeline.paperless,
            "get_document_content",
            AsyncMock(return_value="Electrician invoice. Amount due $395."),
        )
        monkeypatch.setattr(pipeline.analyzer, "health_check", AsyncMock(return_value=False))
        monkeypatch.setattr(
            pipeline.fallback_analyzer,
            "analyze_document",
            lambda document: reanalysis,
        )
        session = get_session()
        try:
            session.add(
                Action(
                    document_id=1,
                    document_title="Electrician invoice",
                    action_type="PAY",
                    title="Pay Electrician Invoice",
                    summary="Accepted by user",
                    amount=395.0,
                    urgency="MEDIUM",
                    confidence=80,
                    status="pending",
                    action_ready=True,
                    review_state="ready",
                    human_reviewed=False,
                    human_corrected_fields=[
                        "action_type",
                        "title",
                        "summary",
                        "due_date",
                        "amount",
                        "urgency",
                    ],
                    action_index=0,
                )
            )
            session.commit()
        finally:
            session.close()

        await pipeline.run(force=True, dry_run=False)

        session = get_session()
        try:
            action = session.query(Action).filter_by(document_id=1).one()
            assert action.title == "Pay Electrician Invoice"
            assert action.amount == 395.0
            assert action.action_ready is True
            assert action.review_state == "ready"
            assert action.review_item_id is None
            assert action.confidence == 45
        finally:
            session.close()

    @pytest.mark.asyncio
    async def test_successful_reanalysis_promotes_existing_review_action(self, db, monkeypatch):
        from doc_intelligence_hub.modules.triage.database import (
            create_action_classification_review,
            get_queue_item,
        )

        pipeline = Pipeline()
        monkeypatch.setattr(aq_settings, "write_to_paperless", False)
        monkeypatch.setattr(aq_settings, "confidence_threshold", 70)
        monkeypatch.setattr(pipeline.paperless, "list_correspondents", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            pipeline.paperless, "fetch_all_metadata", AsyncMock(return_value=({}, {}, {}))
        )
        monkeypatch.setattr(
            pipeline.paperless, "list_documents", AsyncMock(return_value=_make_docs(1))
        )
        monkeypatch.setattr(
            pipeline.paperless,
            "get_document_content",
            AsyncMock(return_value="Invoice. Amount due $50."),
        )
        monkeypatch.setattr(pipeline.analyzer, "health_check", AsyncMock(return_value=False))
        monkeypatch.setattr(
            pipeline.fallback_analyzer,
            "analyze_document",
            lambda document: VALID_EXTRACTION,
        )
        session = get_session()
        try:
            action = Action(
                document_id=1,
                document_title="Invoice",
                action_type="PAY",
                title="Uncertain payment",
                amount=None,
                status="pending",
                action_ready=False,
                review_state="needs_review",
                action_index=0,
            )
            session.add(action)
            session.commit()
            review_item = create_action_classification_review(
                action_id=action.id,
                document_id=1,
                confidence=45,
                reason="Previous analysis required review",
                metadata={"action_id": action.id},
            )
            action.review_item_id = review_item["id"]
            session.commit()
        finally:
            session.close()

        await pipeline.run(force=True, dry_run=False)

        session = get_session()
        try:
            action = session.query(Action).filter_by(document_id=1).one()
            assert action.amount == 50.0
            assert action.action_ready is True
            assert action.review_state == "ready"
            assert action.review_item_id is None
        finally:
            session.close()
        assert get_queue_item(review_item["id"])["status"] == "resolved"

    @pytest.mark.asyncio
    async def test_no_action_result_preserves_only_human_corrected_sibling(self, db, monkeypatch):
        pipeline = Pipeline()
        no_action_extraction = {
            "actions": [],
            "document_assessment": {
                "overall_confidence": 95,
                "requires_action": False,
                "text_quality": "good",
            },
        }
        monkeypatch.setattr(aq_settings, "write_to_paperless", False)
        monkeypatch.setattr(pipeline.paperless, "list_correspondents", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            pipeline.paperless, "fetch_all_metadata", AsyncMock(return_value=({}, {}, {}))
        )
        monkeypatch.setattr(
            pipeline.paperless, "list_documents", AsyncMock(return_value=_make_docs(1))
        )
        monkeypatch.setattr(
            pipeline.paperless,
            "get_document_content",
            AsyncMock(return_value="Informational notice only."),
        )
        monkeypatch.setattr(pipeline.analyzer, "health_check", AsyncMock(return_value=False))
        monkeypatch.setattr(
            pipeline.fallback_analyzer,
            "analyze_document",
            lambda document: no_action_extraction,
        )
        session = get_session()
        try:
            session.add_all(
                [
                    Action(
                        document_id=1,
                        document_title="Notice",
                        action_type="PAY",
                        title="Pay corrected amount",
                        amount=395.0,
                        status="pending",
                        action_ready=True,
                        review_state="ready",
                        human_corrected_fields=["amount"],
                        action_index=0,
                    ),
                    Action(
                        document_id=1,
                        document_title="Notice",
                        action_type="REVIEW",
                        title="Review model guess",
                        status="pending",
                        action_ready=True,
                        review_state="ready",
                        action_index=1,
                    ),
                ]
            )
            session.commit()
        finally:
            session.close()

        await pipeline.run(force=True, dry_run=False)

        session = get_session()
        try:
            corrected, unreviewed = session.query(Action).order_by(Action.action_index.asc()).all()
            assert corrected.status == "pending"
            assert corrected.action_ready is True
            assert corrected.amount == 395.0
            assert unreviewed.status == "not_an_action"
            assert unreviewed.action_ready is False
            assert unreviewed.review_state == "resolved_no_action"
        finally:
            session.close()

    @pytest.mark.asyncio
    async def test_no_action_classification_updates_paperless(self, db, monkeypatch):
        pipeline = Pipeline()
        no_action_extraction = {
            "actions": [],
            "document_assessment": {
                "overall_confidence": 95,
                "requires_action": False,
                "text_quality": "good",
            },
        }
        session = get_session()
        try:
            session.add(
                Action(
                    document_id=1,
                    document_title="Previously classified bill",
                    action_type="PAY",
                    title="Pay $50",
                    status="pending",
                    action_ready=True,
                    review_state="ready",
                    version=1,
                )
            )
            session.commit()
        finally:
            session.close()

        monkeypatch.setattr(aq_settings, "write_to_paperless", True)
        monkeypatch.setattr(pipeline.paperless, "list_correspondents", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            pipeline.paperless,
            "fetch_all_metadata",
            AsyncMock(return_value=({}, {}, {})),
        )
        monkeypatch.setattr(
            pipeline.paperless,
            "list_documents",
            AsyncMock(return_value=_make_docs(1)),
        )
        monkeypatch.setattr(
            pipeline.paperless,
            "get_document_content",
            AsyncMock(return_value="Informational notice only."),
        )
        monkeypatch.setattr(pipeline.analyzer, "health_check", AsyncMock(return_value=False))
        monkeypatch.setattr(
            pipeline.fallback_analyzer,
            "analyze_document",
            lambda doc: no_action_extraction,
        )
        pipeline.enricher.ensure_custom_fields_exist = AsyncMock(return_value={})
        pipeline.enricher.sync_status = AsyncMock()

        stats = await pipeline.run(force=True, dry_run=False)

        assert stats["no_action"] == 1
        assert stats["documents_scanned"] == 1
        assert stats["outcomes"]["no_action"] == 1
        pipeline.enricher.sync_status.assert_awaited_once_with(1, "not_an_action")
        session = get_session()
        try:
            action = session.query(Action).filter_by(document_id=1).one()
            assert action.status == "not_an_action"
            assert action.action_ready is False
            assert action.review_state == "resolved_no_action"
            assert action.review_item_id is None
            assert action.last_synced_status == "not_an_action"
            assert action.version == 2
        finally:
            session.close()

    @pytest.mark.asyncio
    async def test_failed_no_action_sync_remains_retryable(self, db, monkeypatch):
        from doc_intelligence_hub.modules.action_queue.database import (
            ProcessingHistory,
            get_session,
        )

        pipeline = Pipeline()
        no_action_extraction = {
            "actions": [],
            "document_assessment": {
                "overall_confidence": 95,
                "requires_action": False,
                "text_quality": "good",
            },
        }

        monkeypatch.setattr(aq_settings, "write_to_paperless", True)
        monkeypatch.setattr(pipeline.paperless, "list_correspondents", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            pipeline.paperless,
            "fetch_all_metadata",
            AsyncMock(return_value=({}, {}, {})),
        )
        monkeypatch.setattr(
            pipeline.paperless,
            "list_documents",
            AsyncMock(return_value=_make_docs(1)),
        )
        monkeypatch.setattr(
            pipeline.paperless,
            "get_document_content",
            AsyncMock(return_value="Informational notice only."),
        )
        monkeypatch.setattr(pipeline.analyzer, "health_check", AsyncMock(return_value=False))
        monkeypatch.setattr(
            pipeline.fallback_analyzer,
            "analyze_document",
            lambda doc: no_action_extraction,
        )
        pipeline.enricher.ensure_custom_fields_exist = AsyncMock(return_value={})
        pipeline.enricher.sync_status = AsyncMock(side_effect=RuntimeError("Paperless unavailable"))

        stats = await pipeline.run(force=True, dry_run=False)

        session = get_session()
        try:
            history = session.query(ProcessingHistory).filter_by(document_id=1).one()
            assert history.success == 0
            assert history.disposition == "no_action_sync_failed"
        finally:
            session.close()
        assert stats["enrichment_failed"] == 1

    @pytest.mark.asyncio
    async def test_unavailable_enrichment_keeps_no_action_retryable(self, db, monkeypatch):
        from doc_intelligence_hub.modules.action_queue.database import (
            ProcessingHistory,
            get_session,
        )

        pipeline = Pipeline()
        no_action_extraction = {
            "actions": [],
            "document_assessment": {
                "overall_confidence": 95,
                "requires_action": False,
                "text_quality": "good",
            },
        }

        monkeypatch.setattr(aq_settings, "write_to_paperless", True)
        monkeypatch.setattr(pipeline.paperless, "list_correspondents", AsyncMock(return_value=[]))
        monkeypatch.setattr(
            pipeline.paperless,
            "fetch_all_metadata",
            AsyncMock(return_value=({}, {}, {})),
        )
        monkeypatch.setattr(
            pipeline.paperless,
            "list_documents",
            AsyncMock(return_value=_make_docs(1)),
        )
        monkeypatch.setattr(
            pipeline.paperless,
            "get_document_content",
            AsyncMock(return_value="Informational notice only."),
        )
        monkeypatch.setattr(pipeline.analyzer, "health_check", AsyncMock(return_value=False))
        monkeypatch.setattr(
            pipeline.fallback_analyzer,
            "analyze_document",
            lambda doc: no_action_extraction,
        )
        pipeline.enricher.ensure_custom_fields_exist = AsyncMock(
            side_effect=RuntimeError("Custom fields unavailable")
        )
        pipeline.enricher.sync_status = AsyncMock()

        stats = await pipeline.run(force=True, dry_run=False)

        session = get_session()
        try:
            history = session.query(ProcessingHistory).filter_by(document_id=1).one()
            assert history.success == 0
            assert history.disposition == "no_action_sync_failed"
        finally:
            session.close()
        pipeline.enricher.sync_status.assert_not_awaited()
        assert stats["enrichment_failed"] == 1


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
        monkeypatch.setattr(
            pipeline.fallback_analyzer, "analyze_document", lambda doc: VALID_EXTRACTION
        )

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
        monkeypatch.setattr(
            pipeline.fallback_analyzer, "analyze_document", lambda doc: VALID_EXTRACTION
        )

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
        monkeypatch.setattr(
            pipeline.fallback_analyzer, "analyze_document", lambda doc: VALID_EXTRACTION
        )

        await pipeline.run(force=True, dry_run=False)

        assert received_kwargs.get("limit") is None

    @pytest.mark.asyncio
    async def test_non_force_fetch_limit_uses_small_multiplier(self, db, monkeypatch):
        """When `force` is False, the fetch buffer should be a small multiple
        (limit*3) of the requested limit — not a large buffer like limit*10
        (min 50) — so we don't pull in large amounts of data before local
        already-processed filtering.
        """
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
        monkeypatch.setattr(
            pipeline.fallback_analyzer, "analyze_document", lambda doc: VALID_EXTRACTION
        )

        await pipeline.run(force=False, dry_run=False, limit=5)

        assert received_kwargs.get("limit") == 15  # 5 * 3, not max(5*10, 50) == 50


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

        async def fake_fetch_all_metadata():
            return ({}, {}, {})

        async def hanging_list_documents(**kwargs):
            await asyncio.sleep(5)  # simulate a Paperless instance that never responds in time
            return []  # pragma: no cover - never reached

        monkeypatch.setattr(pipeline.paperless, "list_correspondents", fake_list_correspondents)
        monkeypatch.setattr(pipeline.paperless, "fetch_all_metadata", fake_fetch_all_metadata)
        monkeypatch.setattr(
            pipeline.paperless,
            "list_custom_fields",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(pipeline.paperless, "list_documents", hanging_list_documents)

        start = time.monotonic()
        stats = await pipeline.run(force=True, dry_run=False, limit=5)
        elapsed = time.monotonic() - start

        assert stats.get("fetch_timed_out") is True
        assert stats["processed"] == 0
        # Should abort well before the simulated 5s hang.
        assert elapsed < 2.0
