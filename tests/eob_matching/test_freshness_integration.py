"""EOB Matching as a downstream-module integration for issue #114
(analysis invalidation / staleness).

Covers: a document whose extraction previously failed is always retried
(no fingerprint was ever recorded for it, so it stays eligible); a
document whose checksum changes is reprocessed without ``--force``
(self-healing staleness); and a rollback to an earlier checksum is
treated as stale again rather than a no-op duplicate, mirroring the
dedup/rollback semantics in ``tests/analysis_invalidation/test_service.py``.
"""

from __future__ import annotations

import pytest

from doc_intelligence_hub.core.paperless import PaperlessClient
from doc_intelligence_hub.modules.analysis_invalidation import config as ai_config
from doc_intelligence_hub.modules.analysis_invalidation.database import (
    init_db as ai_init_db,
)
from doc_intelligence_hub.modules.analysis_invalidation.models import FreshnessStatus
from doc_intelligence_hub.modules.analysis_invalidation.service import AnalysisFreshnessService
from doc_intelligence_hub.modules.eob_matching import extractor as extractor_module
from doc_intelligence_hub.modules.eob_matching.cli import (
    EOB_MATCHING_MODULE_NAME,
    EOB_MATCHING_MODULE_VERSION,
    _run_pipeline,
)
from doc_intelligence_hub.modules.eob_matching.database import (
    BillRecord,
    EOBRecord,
    get_session,
)
from doc_intelligence_hub.modules.eob_matching.database import (
    configure as eob_configure,
)
from doc_intelligence_hub.modules.eob_matching.database import (
    init_db as eob_init_db,
)

EOB_TEXT = """
UnitedHealthcare
Explanation of Benefits
This is not a bill.
Date of Service: 01/15/2024
Amount Your Plan Pays: $144.00
"""

BILL_TEXT = """
City Medical Center
Invoice Number: INV-1001
Amount Due: $36.00
Balance Due: $36.00
Due Date: 02/10/2024
Please remit payment to the address below.
"""


@pytest.fixture()
def db(tmp_path):
    """Isolated eob_matching + analysis-invalidation databases for these tests."""
    original_ai_url = ai_config.settings.database_url

    eob_configure(f"sqlite:///{tmp_path / 'test_eob_matching.db'}")
    ai_config.settings.database_url = f"sqlite:///{tmp_path / 'test_analysis_invalidation.db'}"

    eob_init_db()
    ai_init_db()
    yield
    ai_config.settings.database_url = original_ai_url


def _wire_client(monkeypatch, docs_by_id: dict[int, dict]):
    """Point PaperlessClient.list_documents at fake documents for these tests."""

    async def fake_list_documents(self, **kwargs):
        return list(docs_by_id.values())

    monkeypatch.setattr(PaperlessClient, "list_documents", fake_list_documents)


def _wire_failing_extractor(monkeypatch, fail_ids: set[str]):
    """Make ``extract_eob`` raise for document ids in ``fail_ids`` (mutable —
    the caller can clear it between runs), delegating to the real
    implementation otherwise.
    """
    real_extract_eob = extractor_module.extract_eob

    def wrapped(content, document_id):
        if document_id in fail_ids:
            raise RuntimeError("simulated extraction failure")
        return real_extract_eob(content, document_id)

    monkeypatch.setattr("doc_intelligence_hub.modules.eob_matching.cli.extract_eob", wrapped)


async def _run(*, limit: int = 50, skip_processed: bool = True, use_llm: bool = False):
    await _run_pipeline(
        paperless_url="http://paperless.test",
        paperless_token="token",
        tags=None,
        correspondent=None,
        document_type=None,
        created_after=None,
        created_before=None,
        limit=limit,
        output_path=None,
        verbose=False,
        write_to_paperless=False,
        skip_processed=skip_processed,
        use_llm=use_llm,
    )


def _doc_ids(model) -> set[int]:
    session = get_session()
    try:
        return {row.document_id for row in session.query(model.document_id).all()}
    finally:
        session.close()


def _row_count(model, document_id: int) -> int:
    session = get_session()
    try:
        return session.query(model).filter_by(document_id=document_id).count()
    finally:
        session.close()


class TestEobMatchingFreshnessIntegration:
    @pytest.mark.asyncio
    async def test_failed_extraction_is_always_retried(self, db, monkeypatch):
        """A document whose extraction previously failed has no fingerprint,
        so it stays eligible (UNKNOWN) and is retried on the next run without
        --force, without disturbing an unrelated document's recorded state.
        """
        docs = {
            1: {"id": 1, "title": "EOB 1", "checksum": "chk-1", "content": EOB_TEXT},
            2: {"id": 2, "title": "EOB 2", "checksum": "chk-2", "content": EOB_TEXT},
        }
        fail_ids: set[str] = {"2"}
        _wire_failing_extractor(monkeypatch, fail_ids)
        _wire_client(monkeypatch, docs)
        await _run()

        assert _doc_ids(EOBRecord) == {1}

        freshness = AnalysisFreshnessService()
        result_1 = freshness.check_freshness(
            document_id=1,
            module_name=EOB_MATCHING_MODULE_NAME,
            module_version=EOB_MATCHING_MODULE_VERSION,
            config_hash="ignored",
            current_checksum="chk-1",
        )
        assert result_1.status != FreshnessStatus.UNKNOWN

        result_2 = freshness.check_freshness(
            document_id=2,
            module_name=EOB_MATCHING_MODULE_NAME,
            module_version=EOB_MATCHING_MODULE_VERSION,
            config_hash="ignored",
            current_checksum="chk-2",
        )
        assert result_2.status == FreshnessStatus.UNKNOWN

        # Second run (still no --force): doc 2 gets a fresh chance, doc 1 is
        # left alone (untouched by doc 2's earlier failure).
        fail_ids.clear()
        await _run()

        assert _doc_ids(EOBRecord) == {1, 2}

    @pytest.mark.asyncio
    async def test_changed_checksum_is_reprocessed_without_force(self, db, monkeypatch):
        """A document whose accepted version changed self-heals without --force."""
        docs = {1: {"id": 1, "title": "Bill 1", "checksum": "chk-v1", "content": BILL_TEXT}}
        _wire_client(monkeypatch, docs)
        await _run()
        assert _row_count(BillRecord, 1) == 1

        # Repeat with the same checksum: skipped (fresh) — no new row added.
        docs_same = {1: {**docs[1]}}
        _wire_client(monkeypatch, docs_same)
        await _run()
        assert _row_count(BillRecord, 1) == 1

        # Now simulate an accepted OCR version change: checksum differs.
        docs_changed = {1: {**docs[1], "checksum": "chk-v2"}}
        _wire_client(monkeypatch, docs_changed)
        await _run()
        assert _row_count(BillRecord, 1) == 2

    @pytest.mark.asyncio
    async def test_rollback_to_earlier_checksum_is_treated_as_stale_again(self, db, monkeypatch):
        """A -> B -> A is a new stale cycle each time, not a no-op duplicate
        (mirrors TestRollback / TestDuplicateDelivery in
        tests/analysis_invalidation/test_service.py).
        """
        docs_a = {1: {"id": 1, "title": "Bill 1", "checksum": "chk-A", "content": BILL_TEXT}}
        _wire_client(monkeypatch, docs_a)
        await _run()
        assert _row_count(BillRecord, 1) == 1

        docs_b = {1: {**docs_a[1], "checksum": "chk-B"}}
        _wire_client(monkeypatch, docs_b)
        await _run()
        assert _row_count(BillRecord, 1) == 2

        # Rollback: checksum returns to "chk-A" — must be treated as stale
        # (a new cycle), not silently skipped as "already seen this value".
        docs_rollback = {1: {**docs_a[1], "checksum": "chk-A"}}
        _wire_client(monkeypatch, docs_rollback)
        await _run()
        assert _row_count(BillRecord, 1) == 3
