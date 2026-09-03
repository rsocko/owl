"""Action Queue as the reference downstream-module integration for issue #114
(analysis invalidation / staleness).

Covers: fresh documents are skipped on repeat runs without ``--force``; a
document whose analysis previously failed is always retried (no fingerprint
was ever recorded for it, so it stays "unknown"/eligible); a document whose
checksum changes is retried without ``--force`` (self-healing staleness);
and a successful re-analysis does not disturb any other document's state
(no partial-failure "rollback" of unrelated results).
"""

from __future__ import annotations

import pytest

from doc_intelligence_hub.modules.action_queue.config import settings as aq_settings
from doc_intelligence_hub.modules.action_queue.database import ProcessingHistory, init_db
from doc_intelligence_hub.modules.action_queue.pipeline import (
    ACTION_QUEUE_ANALYSIS_MODULE_NAME,
    Pipeline,
)
from doc_intelligence_hub.modules.analysis_invalidation import config as ai_config
from doc_intelligence_hub.modules.analysis_invalidation.database import (
    init_db as ai_init_db,
)
from doc_intelligence_hub.modules.analysis_invalidation.models import FreshnessStatus
from doc_intelligence_hub.modules.analysis_invalidation.service import AnalysisFreshnessService

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


@pytest.fixture()
def db(tmp_path):
    """Isolated action-queue + analysis-invalidation databases for these tests."""
    from doc_intelligence_hub.modules.triage import database as triage_database

    original_aq_url = aq_settings.database_url
    original_ai_url = ai_config.settings.database_url

    aq_settings.database_url = f"sqlite:///{tmp_path / 'test_pipeline.db'}"
    triage_database.configure(f"sqlite:///{tmp_path / 'test_triage.db'}")
    ai_config.settings.database_url = f"sqlite:///{tmp_path / 'test_analysis_invalidation.db'}"

    init_db()
    triage_database.init_db()
    ai_init_db()
    yield
    aq_settings.database_url = original_aq_url
    triage_database.configure("sqlite:///data/triage.db")
    ai_config.settings.database_url = original_ai_url


def _wire_pipeline(
    monkeypatch,
    pipeline: Pipeline,
    docs_by_id: dict[int, dict],
    *,
    fail_ids=(),
    calls: list | None = None,
):
    """Point a Pipeline at fake Paperless/analyzer dependencies for these tests."""

    async def fake_list_correspondents():
        return []

    async def fake_list_documents(**kwargs):
        return list(docs_by_id.values())

    async def fake_get_document_content(document_id):
        if calls is not None:
            calls.append(document_id)
        if document_id in fail_ids:
            raise RuntimeError("simulated analysis failure")
        return "Invoice: your payment of $50.00 is due by the end of the month."

    async def fake_health_check():
        return False  # force the deterministic rule-based fallback path

    monkeypatch.setattr(aq_settings, "write_to_paperless", False)
    monkeypatch.setattr(pipeline.paperless, "list_correspondents", fake_list_correspondents)
    monkeypatch.setattr(pipeline.paperless, "list_documents", fake_list_documents)
    monkeypatch.setattr(pipeline.paperless, "get_document_content", fake_get_document_content)
    monkeypatch.setattr(pipeline.analyzer, "health_check", fake_health_check)
    monkeypatch.setattr(
        pipeline.fallback_analyzer, "analyze_document", lambda doc: VALID_EXTRACTION
    )


class TestActionQueueFreshnessIntegration:
    @pytest.mark.asyncio
    async def test_partial_failure_retries_only_failed_document_next_run(self, db, monkeypatch):
        """One doc succeeds and is recorded fresh; one fails and stays retryable.

        A later non-force run must reprocess only the failed document, and
        the earlier success must not be disturbed by the other document's
        failure (no "rollback" of the otherwise-valid result).
        """
        docs = {
            1: {"id": 1, "title": "Doc 1", "checksum": "chk-1", "tag_names": ["Inbox"]},
            2: {"id": 2, "title": "Doc 2", "checksum": "chk-2", "tag_names": ["Inbox"]},
        }
        pipeline = Pipeline()
        _wire_pipeline(monkeypatch, pipeline, docs, fail_ids={2})

        first = await pipeline.run(force=False, dry_run=False)
        assert first["processed"] == 1
        assert first["failed"] == 1

        freshness = AnalysisFreshnessService()
        result_1 = freshness.check_freshness(
            document_id=1,
            module_name=ACTION_QUEUE_ANALYSIS_MODULE_NAME,
            module_version="action-queue-analyzer-v1",
            config_hash="ignored",
            current_checksum="chk-1",
        )
        # Doc 1 succeeded — it has a real fingerprint (status is FRESH/STALE,
        # never UNKNOWN).
        assert result_1.status != FreshnessStatus.UNKNOWN
        # Doc 2 failed — no fingerprint was ever recorded for it.
        result_2 = freshness.check_freshness(
            document_id=2,
            module_name=ACTION_QUEUE_ANALYSIS_MODULE_NAME,
            module_version="action-queue-analyzer-v1",
            config_hash="ignored",
            current_checksum="chk-2",
        )
        assert result_2.status == FreshnessStatus.UNKNOWN

        # Second run (still no --force): doc 2 gets a fresh chance, doc 1 is
        # left alone (skipped as fresh, unaffected by doc 2's earlier failure).
        docs[2] = {**docs[2]}  # same checksum — genuinely retryable, not "changed"
        pipeline2 = Pipeline()
        second_calls: list[int] = []
        _wire_pipeline(monkeypatch, pipeline2, docs, fail_ids=(), calls=second_calls)
        second = await pipeline2.run(force=False, dry_run=False)

        assert second["processed"] == 1  # only doc 2
        assert second["failed"] == 0
        # Doc 1 was filtered out before any content fetch/analysis happened —
        # its earlier success is untouched by doc 2's retry.
        assert second_calls == [2]

        from doc_intelligence_hub.modules.action_queue.database import get_session

        history_db = get_session()
        try:
            success_ids = {
                row.document_id
                for row in history_db.query(ProcessingHistory.document_id).filter(
                    ProcessingHistory.success == 1
                )
            }
            assert success_ids == {1, 2}
        finally:
            history_db.close()

    @pytest.mark.asyncio
    async def test_changed_checksum_is_reprocessed_without_force(self, db, monkeypatch):
        """A document whose accepted version changed self-heals without --force."""
        docs = {1: {"id": 1, "title": "Doc 1", "checksum": "chk-v1", "tag_names": ["Inbox"]}}
        pipeline = Pipeline()
        _wire_pipeline(monkeypatch, pipeline, docs)
        first = await pipeline.run(force=False, dry_run=False)
        assert first["processed"] == 1

        # Repeat with the same checksum: skipped (fresh).
        docs_same = {1: {**docs[1]}}
        pipeline_same = Pipeline()
        _wire_pipeline(monkeypatch, pipeline_same, docs_same)
        repeat = await pipeline_same.run(force=False, dry_run=False)
        assert repeat["processed"] == 0
        assert repeat["skipped"] == 1

        # Now simulate an accepted OCR version change: checksum differs.
        docs_changed = {1: {**docs[1], "checksum": "chk-v2"}}
        pipeline_changed = Pipeline()
        _wire_pipeline(monkeypatch, pipeline_changed, docs_changed)
        after_change = await pipeline_changed.run(force=False, dry_run=False)

        assert after_change["processed"] == 1
        assert after_change["skipped"] == 0

    @pytest.mark.asyncio
    async def test_rollback_to_earlier_checksum_is_reprocessed_without_force(
        self, db, monkeypatch
    ):
        """A -> B -> A (an OCR candidate rollback) is treated as stale again,
        not silently skipped as "already seen this value" (mirrors the
        equivalent EOB matching and analysis_invalidation rollback tests).
        """
        docs_a = {1: {"id": 1, "title": "Doc 1", "checksum": "chk-A", "tag_names": ["Inbox"]}}
        pipeline_a = Pipeline()
        _wire_pipeline(monkeypatch, pipeline_a, docs_a)
        first = await pipeline_a.run(force=False, dry_run=False)
        assert first["processed"] == 1
        assert first["skipped"] == 0

        # Accept a new OCR candidate: checksum moves to "chk-B".
        docs_b = {1: {**docs_a[1], "checksum": "chk-B"}}
        pipeline_b = Pipeline()
        _wire_pipeline(monkeypatch, pipeline_b, docs_b)
        after_accept = await pipeline_b.run(force=False, dry_run=False)
        assert after_accept["processed"] == 1
        assert after_accept["skipped"] == 0

        # Roll back the OCR candidate: checksum returns to "chk-A" — must be
        # treated as stale (a new cycle), not skipped as previously seen.
        docs_rollback = {1: {**docs_a[1], "checksum": "chk-A"}}
        pipeline_rollback = Pipeline()
        _wire_pipeline(monkeypatch, pipeline_rollback, docs_rollback)
        after_rollback = await pipeline_rollback.run(force=False, dry_run=False)
        assert after_rollback["processed"] == 1
        assert after_rollback["skipped"] == 0
