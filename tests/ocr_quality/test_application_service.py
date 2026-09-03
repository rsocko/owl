"""Tests for :class:`OcrCandidateApplicationService` (issue #18, slice 2).

Covers the design doc's apply/rollback acceptance criteria: improvement
accepted, rejection, missing/reordered pages surfaced (not enforced),
overlay/downstream regression surfaced (not enforced), stale source,
provider/task failure, version-application failure (must not corrupt the
current version), rollback, capped batches (untouched), idempotent retry
after a simulated crash, and concurrent apply attempts.

Uses a fake ``PaperlessClient`` that actually implements Paperless's
document-version semantics (root/version list, ``update_version`` ->
Celery task -> merged version, ``delete_document_version`` auto-promoting
the next-highest version) so assertions exercise real apply/rollback logic,
not just "it doesn't crash". Also uses a *real* ``AnalysisFreshnessService``
against a temporary sqlite DB (not mocked) so the issue #114 integration is
genuinely exercised end-to-end.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from doc_intelligence_hub.modules.analysis_invalidation import config as ai_config
from doc_intelligence_hub.modules.analysis_invalidation.database import (
    InvalidationEvent,
)
from doc_intelligence_hub.modules.analysis_invalidation.database import (
    get_session as get_ai_session,
)
from doc_intelligence_hub.modules.analysis_invalidation.database import (
    init_db as init_ai_db,
)
from doc_intelligence_hub.modules.ocr_quality import config as ocr_quality_config
from doc_intelligence_hub.modules.ocr_quality.application_service import (
    OcrCandidateApplicationService,
)
from doc_intelligence_hub.modules.ocr_quality.candidate_models import (
    CandidateGenResult,
    CandidateState,
    Decision,
)
from doc_intelligence_hub.modules.ocr_quality.candidate_service import (
    OcrCandidateService,
    _load_candidate_pdf_bytes,
)
from doc_intelligence_hub.modules.ocr_quality.database import (
    OcrApplicationEvent,
    OcrApplicationLock,
    OcrQualityCandidate,
    get_session,
    init_db,
)

from .conftest import make_minimal_pdf_bytes


def _checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extract_pdf_text(data: bytes) -> str:
    """Pull the literal ``(...) Tj`` text-showing operands out of a PDF built
    by :func:`make_minimal_pdf_bytes`, mimicking real content extraction
    closely enough for Gap 2's content/search-sync verification tests —
    i.e. Paperless's ``get_document_content`` genuinely reflects whatever
    text was actually in the uploaded PDF, not a synthetic placeholder.
    """
    import re

    matches = re.findall(rb"\((.*?)\)\s*Tj", data)
    return " ".join(m.decode(errors="replace") for m in matches)


class FakePaperlessVersioningClient:
    """Duck-typed fake reproducing Paperless-ngx's document-version API.

    - ``documents[doc_id]`` is the "current" preview bytes for that document.
    - ``versions[doc_id]`` is the ordered list (oldest/root first) of
      ``{id, checksum, is_root, version_label}`` — the last entry is always
      "latest" (mirrors ``version_index`` ordering), and ``documents[doc_id]``
      is kept in sync with the last entry's bytes.
    - ``upload_document_version`` enqueues a task that starts ``"pending"``
      and becomes ``"success"`` after ``task_success_after_polls`` calls to
      ``get_task`` (default 1, i.e. succeeds on first poll) unless
      ``fail_tasks`` is set, in which case it becomes ``"failure"``. These
      are the exact lowercase status strings confirmed live against a real
      Paperless-ngx instance (``GET /api/tasks/?task_id=...`` -> paginated
      ``{"results": [{"status": "success", ...}]}``).
    """

    base_url = "https://paperless.private.invalid"

    def __init__(self, previews: dict[int, bytes]):
        self._version_content: dict[int, bytes] = {}
        self.versions: dict[int, list[dict]] = {}
        for doc_id, b in previews.items():
            root_id = doc_id * 1000
            self.versions[doc_id] = [
                {"id": root_id, "checksum": _checksum(b), "is_root": True, "version_label": None}
            ]
            self._version_content[root_id] = b
        self._next_version_id = (
            max((v["id"] for vs in self.versions.values() for v in vs), default=0) + 1
        )
        self._tasks: dict[str, dict] = {}
        self._next_task_id = 1
        self.task_success_after_polls = 1
        self.fail_tasks = False
        self.raise_on_upload: Exception | None = None
        self.upload_calls: list[int] = []
        self.get_document_preview_calls: list[int] = []
        self.get_document_content_calls: list[int] = []
        self.delete_calls: list[tuple[int, int]] = []
        self.label_calls: list[tuple[int, int, str]] = []
        # Gap 2 testing hook: documents in this set report *stale* content
        # (frozen at the value captured the first time this doc's content was
        # read) for every subsequent read, simulating Paperless's search
        # index/content extraction lagging behind an already-applied version
        # write, regardless of how many times content-verification retries.
        self.content_sync_delayed_documents: set[int] = set()
        self._frozen_content: dict[int, str] = {}

    @property
    def previews(self) -> dict[int, bytes]:
        """Current ("latest") bytes per document, derived from the last
        (highest version_index-equivalent) entry in ``versions``."""
        return {
            doc_id: self._version_content[versions[-1]["id"]]
            for doc_id, versions in self.versions.items()
        }

    @previews.setter
    def previews(self, value: dict[int, bytes]) -> None:
        # Allows tests to simulate the live document changing out from under
        # OWL (e.g. a stale-source scenario) by overwriting the latest
        # version's tracked content in place.
        for doc_id, new_bytes in value.items():
            latest = self.versions[doc_id][-1]
            latest["checksum"] = _checksum(new_bytes)
            self._version_content[latest["id"]] = new_bytes

    async def get_document(self, document_id: int) -> dict:
        return {
            "id": document_id,
            "checksum": _checksum(self.previews[document_id]),
            "modified": "2024-01-01T00:00:00Z",
        }

    async def get_document_preview(self, document_id: int) -> tuple[bytes, str]:
        self.get_document_preview_calls.append(document_id)
        return self.previews[document_id], "application/pdf"

    async def get_document_content(self, document_id: int) -> str:
        self.get_document_content_calls.append(document_id)
        latest = self.versions[document_id][-1]
        live_content = _extract_pdf_text(self._version_content[latest["id"]]) or (
            f"content for checksum {latest['checksum']}"
        )
        if document_id in self.content_sync_delayed_documents:
            # First read freezes what "search" reports; later Paperless
            # writes never show up in subsequent reads for this document.
            return self._frozen_content.setdefault(document_id, live_content)
        return live_content

    async def list_document_versions(self, document_id: int) -> list[dict]:
        return [dict(v) for v in self.versions[document_id]]

    async def upload_document_version(
        self,
        root_document_id: int,
        filename: str,
        content: bytes,
        *,
        version_label: str | None = None,
    ) -> str:
        self.upload_calls.append(root_document_id)
        if self.raise_on_upload is not None:
            raise self.raise_on_upload
        task_id = f"task-{self._next_task_id}"
        self._next_task_id += 1
        self._tasks[task_id] = {
            "task_id": task_id,
            "status": "pending",
            "_polls": 0,
            "_document_id": root_document_id,
            "_content": content,
            "_version_label": version_label,
        }
        return task_id

    async def get_task(self, task_id: str) -> dict | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task["_polls"] += 1
        if task["_polls"] >= self.task_success_after_polls:
            if self.fail_tasks:
                task["status"] = "failure"
            elif task["status"] != "success":
                task["status"] = "success"
                document_id = task["_document_id"]
                new_id = self._next_version_id
                self._next_version_id += 1
                self.versions[document_id].append(
                    {
                        "id": new_id,
                        "checksum": _checksum(task["_content"]),
                        "is_root": False,
                        "version_label": task["_version_label"],
                    }
                )
                self._version_content[new_id] = task["_content"]
        return {"task_id": task_id, "status": task["status"]}

    async def delete_document_version(self, root_document_id: int, version_id: int) -> dict:
        self.delete_calls.append((root_document_id, version_id))
        versions = self.versions[root_document_id]
        match = next((v for v in versions if v["id"] == version_id), None)
        if match is None:
            import httpx

            request = httpx.Request(
                "DELETE", f"/api/documents/{root_document_id}/versions/{version_id}/"
            )
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)
        if match.get("is_root"):
            raise ValueError("Cannot delete the root/original version.")
        versions.remove(match)
        del self._version_content[version_id]
        current = versions[-1]
        return {"result": "OK", "current_version_id": current["id"]}

    async def label_document_version(
        self, root_document_id: int, version_id: int, label: str
    ) -> dict:
        self.label_calls.append((root_document_id, version_id, label))
        for v in self.versions[root_document_id]:
            if v["id"] == version_id:
                v["version_label"] = label
        return {"id": version_id, "version_label": label}

    async def aclose(self) -> None:
        pass


@pytest.fixture()
def ocr_db(tmp_path):
    original = ocr_quality_config.settings.database_url
    ocr_quality_config.settings.database_url = (
        f"sqlite:///{tmp_path / 'application_service_test.db'}"
    )
    init_db()
    yield
    ocr_quality_config.settings.database_url = original


@pytest.fixture()
def ai_db(tmp_path):
    original = ai_config.settings.database_url
    ai_config.settings.database_url = f"sqlite:///{tmp_path / 'application_service_ai_test.db'}"
    init_ai_db()
    yield
    ai_config.settings.database_url = original


@pytest.fixture()
def fake_client(ocr_db):
    return FakePaperlessVersioningClient({1: make_minimal_pdf_bytes("doc one")})


@pytest.fixture()
def app_service(fake_client, ai_db):
    return OcrCandidateApplicationService(fake_client, get_session)


async def _make_applying_candidate(fake_client, *, document_id: int = 1) -> str:
    """Stage a candidate through slice 1 to APPLYING, ready to be applied."""
    candidate_service = OcrCandidateService(fake_client, get_session)
    [candidate_id] = await candidate_service.request_candidates(
        document_ids=[document_id], engines=["ocrmypdf-tesseract-5"]
    )
    fake_gen_result = CandidateGenResult(
        success=True,
        candidate_pdf_bytes=make_minimal_pdf_bytes("candidate text"),
        candidate_text="candidate text",
        page_count=1,
    )
    with patch(
        "doc_intelligence_hub.modules.ocr_quality.candidate_service.OcrMyPdfProvider.generate_candidate",
        new=AsyncMock(return_value=fake_gen_result),
    ):
        await candidate_service.run_generation_for_candidate(candidate_id)
    await candidate_service.decide_candidate(
        candidate_id, decision=Decision.ACCEPTED, reason="looks good", actor="reviewer1"
    )
    return candidate_id


def _row(candidate_id: str) -> OcrQualityCandidate:
    db = get_session()
    try:
        return db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one()
    finally:
        db.close()


class TestApplyCandidateSuccess:
    @pytest.mark.asyncio
    async def test_improvement_accepted_becomes_new_latest_version(self, fake_client, app_service):
        candidate_id = await _make_applying_candidate(fake_client)
        result = await app_service.apply_candidate(candidate_id, actor="reviewer1")

        assert result["state"] == CandidateState.ACCEPTED.value
        assert result["invalidation_recorded"] is True

        row = _row(candidate_id)
        assert row.state == CandidateState.ACCEPTED.value
        assert row.applied_paperless_version_id is not None
        assert row.applied_at is not None
        assert row.invalidation_recorded is True

        # Paperless now serves the candidate's content as latest.
        latest_bytes, _ = await fake_client.get_document_preview(1)
        assert _checksum(latest_bytes) == row.candidate_pdf_checksum

        # Never a duplicate top-level document: only one document id (1) is
        # tracked; the new content is a *version* of it.
        assert set(fake_client.versions.keys()) == {1}
        assert len(fake_client.versions[1]) == 2  # original root + new version
        assert fake_client.label_calls  # audited with an owl-candidate-* label

        # Downstream invalidation (issue #114) was actually recorded.
        db = get_ai_session()
        try:
            events = db.query(InvalidationEvent).filter_by(document_id=1).all()
            assert len(events) == 1
            assert events[0].reason == "version_changed"
            assert events[0].accepted_checksum == row.candidate_pdf_checksum
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_lock_released_after_successful_apply(self, fake_client, app_service):
        candidate_id = await _make_applying_candidate(fake_client)
        await app_service.apply_candidate(candidate_id, actor="reviewer1")

        db = get_session()
        try:
            assert db.query(OcrApplicationLock).filter_by(document_id=1).one_or_none() is None
        finally:
            db.close()

        db = get_session()
        try:
            events = db.query(OcrApplicationEvent).filter_by(document_id=1, action="apply").all()
            assert len(events) == 1
            assert events[0].outcome == "success"
        finally:
            db.close()


class TestApplyCandidateContentVerification:
    """Gap 2: a matching preview-byte checksum alone must not be enough —
    Paperless's extracted content/search index must also reflect the new
    version before the apply is reported successful.
    """

    @pytest.mark.asyncio
    async def test_content_change_confirmed_on_first_check_no_extra_delay(
        self, fake_client, app_service
    ):
        candidate_id = await _make_applying_candidate(fake_client)
        result = await app_service.apply_candidate(candidate_id, actor="reviewer1")

        assert result["state"] == CandidateState.ACCEPTED.value
        # Content was actually read as part of verification (plus the
        # best-effort baseline read before upload).
        assert fake_client.get_document_content_calls.count(1) >= 2

    @pytest.mark.asyncio
    async def test_content_never_reflects_new_version_fails_apply_without_corrupting_version(
        self, fake_client, app_service, monkeypatch
    ):
        # Speed up the bounded retry loop for this deliberately-exhausted case.
        monkeypatch.setattr(ocr_quality_config.settings, "candidate_apply_content_verify_attempts", 2)
        monkeypatch.setattr(ocr_quality_config.settings, "candidate_apply_content_verify_delay_seconds", 0.0)

        candidate_id = await _make_applying_candidate(fake_client)
        fake_client.content_sync_delayed_documents.add(1)

        result = await app_service.apply_candidate(candidate_id, actor="reviewer1")

        assert "error" in result
        assert "content" in result["error"].lower()

        row = _row(candidate_id)
        # Same bounded-retry shape as the existing preview-checksum failure
        # path: not yet terminal on the first failed attempt.
        assert row.state == CandidateState.READY.value
        assert row.apply_attempts == 1
        assert row.apply_last_error

        # The Paperless write itself was NOT rolled back or corrupted — the
        # new version is still Paperless's latest, even though the candidate
        # row is back in READY for a retry.
        assert len(fake_client.versions[1]) == 2
        latest_bytes, _ = await fake_client.get_document_preview(1)
        assert _checksum(latest_bytes) == row.candidate_pdf_checksum


class TestApplyCandidatePendingInvalidation:
    """Gap 3: a candidate must never be reported/persisted as ACCEPTED if
    downstream invalidation didn't durably record after bounded retries —
    it lands in ACCEPTED_PENDING_INVALIDATION instead, without undoing the
    already-successful Paperless version write.
    """

    @pytest.mark.asyncio
    async def test_invalidation_eventually_succeeds_after_retry_reports_accepted(
        self, fake_client, app_service, monkeypatch
    ):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        real_record = app_service._freshness_service.record_invalidation
        calls = {"n": 0}

        def flaky_record_invalidation(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("transient invalidation failure")
            return real_record(*args, **kwargs)

        monkeypatch.setattr(
            app_service._freshness_service, "record_invalidation", flaky_record_invalidation
        )

        candidate_id = await _make_applying_candidate(fake_client)
        result = await app_service.apply_candidate(candidate_id, actor="reviewer1")

        assert result["state"] == CandidateState.ACCEPTED.value
        assert result["invalidation_recorded"] is True
        row = _row(candidate_id)
        assert row.state == CandidateState.ACCEPTED.value
        assert row.invalidation_recorded is True
        # The Paperless version write always succeeded, independent of the
        # invalidation retries.
        assert len(fake_client.versions[1]) == 2

    @pytest.mark.asyncio
    async def test_invalidation_retries_exhausted_lands_in_pending_state_without_rollback(
        self, fake_client, app_service, monkeypatch
    ):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        monkeypatch.setattr(
            app_service._freshness_service,
            "record_invalidation",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("invalidation backend down")),
        )

        candidate_id = await _make_applying_candidate(fake_client)
        result = await app_service.apply_candidate(candidate_id, actor="reviewer1")

        # Not a failure response — the apply itself (Paperless write) DID
        # succeed. Only the reported state reflects the outstanding
        # bookkeeping.
        assert "error" not in result
        assert result["state"] == CandidateState.ACCEPTED_PENDING_INVALIDATION.value
        assert result["invalidation_recorded"] is False

        row = _row(candidate_id)
        assert row.state == CandidateState.ACCEPTED_PENDING_INVALIDATION.value
        assert row.invalidation_recorded is False
        assert row.applied_paperless_version_id is not None

        # The Paperless version swap is never rolled back for a bookkeeping
        # failure — it's still latest.
        assert len(fake_client.versions[1]) == 2
        latest_bytes, _ = await fake_client.get_document_preview(1)
        assert _checksum(latest_bytes) == row.candidate_pdf_checksum

    @pytest.mark.asyncio
    async def test_retry_invalidation_succeeds_transitions_to_accepted(
        self, fake_client, app_service, monkeypatch
    ):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        monkeypatch.setattr(
            app_service._freshness_service,
            "record_invalidation",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")),
        )
        candidate_id = await _make_applying_candidate(fake_client)
        pending_result = await app_service.apply_candidate(candidate_id, actor="reviewer1")
        assert pending_result["state"] == CandidateState.ACCEPTED_PENDING_INVALIDATION.value

        monkeypatch.undo()
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        retry_result = await app_service.retry_invalidation(candidate_id, actor="reviewer2")

        assert "error" not in retry_result
        assert retry_result["state"] == CandidateState.ACCEPTED.value
        assert retry_result["invalidation_recorded"] is True

        row = _row(candidate_id)
        assert row.state == CandidateState.ACCEPTED.value
        assert row.invalidation_recorded is True

        db = get_ai_session()
        try:
            events = db.query(InvalidationEvent).filter_by(document_id=1).all()
            assert len(events) == 1
            assert events[0].reason == "version_changed"
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_retry_invalidation_rejects_blank_actor(self, fake_client, app_service, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        monkeypatch.setattr(
            app_service._freshness_service,
            "record_invalidation",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")),
        )
        candidate_id = await _make_applying_candidate(fake_client)
        await app_service.apply_candidate(candidate_id, actor="reviewer1")

        result = await app_service.retry_invalidation(candidate_id, actor="   ")
        assert "error" in result
        row = _row(candidate_id)
        assert row.state == CandidateState.ACCEPTED_PENDING_INVALIDATION.value

    @pytest.mark.asyncio
    async def test_retry_invalidation_rejects_wrong_state(self, fake_client, app_service):
        candidate_id = await _make_applying_candidate(fake_client)
        await app_service.apply_candidate(candidate_id, actor="reviewer1")
        # This candidate is already fully ACCEPTED (default happy path) —
        # matches the existing convention (e.g. decide_candidate) of raising
        # ValueError for a wrong-state call, which the router translates to
        # a 400.
        with pytest.raises(ValueError, match="accepted_pending_invalidation"):
            await app_service.retry_invalidation(candidate_id, actor="reviewer2")

    @pytest.mark.asyncio
    async def test_rollback_target_accepts_pending_invalidation_candidate(
        self, fake_client, app_service, monkeypatch
    ):
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        monkeypatch.setattr(
            app_service._freshness_service,
            "record_invalidation",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")),
        )
        candidate_id = await _make_applying_candidate(fake_client)
        apply_result = await app_service.apply_candidate(candidate_id, actor="reviewer1")
        assert apply_result["state"] == CandidateState.ACCEPTED_PENDING_INVALIDATION.value

        monkeypatch.undo()
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        rollback_result = await app_service.rollback(1, actor="reviewer1", target_candidate_id=None)
        assert "error" not in rollback_result
        assert len(fake_client.versions[1]) == 1  # rolled back to root


class TestApplyCandidateActorRequired:
    @pytest.mark.asyncio
    async def test_apply_rejects_blank_actor(self, fake_client, app_service):
        candidate_id = await _make_applying_candidate(fake_client)
        result = await app_service.apply_candidate(candidate_id, actor="   ")
        assert "error" in result
        assert not fake_client.upload_calls

    @pytest.mark.asyncio
    async def test_rollback_rejects_blank_actor(self, fake_client, app_service):
        candidate_id = await _make_applying_candidate(fake_client)
        await app_service.apply_candidate(candidate_id, actor="reviewer1")
        result = await app_service.rollback(1, actor="", target_candidate_id=candidate_id)
        assert "error" in result
        assert not fake_client.delete_calls


class TestApplyCandidateRejection:
    @pytest.mark.asyncio
    async def test_rejection_makes_zero_paperless_calls(self, fake_client, ocr_db):
        candidate_service = OcrCandidateService(fake_client, get_session)
        [candidate_id] = await candidate_service.request_candidates(
            document_ids=[1], engines=["ocrmypdf-tesseract-5"]
        )
        fake_gen_result = CandidateGenResult(
            success=True,
            candidate_pdf_bytes=make_minimal_pdf_bytes("candidate text"),
            candidate_text="candidate text",
            page_count=1,
        )
        with patch(
            "doc_intelligence_hub.modules.ocr_quality.candidate_service.OcrMyPdfProvider.generate_candidate",
            new=AsyncMock(return_value=fake_gen_result),
        ):
            await candidate_service.run_generation_for_candidate(candidate_id)

        result = await candidate_service.decide_candidate(
            candidate_id, decision=Decision.REJECTED, reason="bad OCR", actor="reviewer1"
        )
        assert result["state"] == CandidateState.REJECTED.value
        assert not fake_client.upload_calls
        assert not fake_client.delete_calls
        assert len(fake_client.versions[1]) == 1  # unchanged: only the root


class TestApplyCandidateSurfacedFindings:
    @pytest.mark.asyncio
    async def test_blocking_findings_surfaced_but_do_not_block_apply(
        self, fake_client, app_service
    ):
        """Missing/reordered pages, overlay regression, downstream regression are
        evidence surfaced in the comparison, never a hard block — a human
        reviewer's explicit accept is still authoritative (design doc:
        "a higher score alone cannot authorize").
        """
        candidate_id = await _make_applying_candidate(fake_client)
        # Force a blocking finding onto the row directly (comparison-stage
        # detail is covered in test_comparison.py; here we only need to
        # confirm apply proceeds regardless of what findings are present).
        db = get_session()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one()
            row.blocking_findings = ["pages_missing", "machine_regression"]
            db.commit()
        finally:
            db.close()

        result = await app_service.apply_candidate(candidate_id, actor="reviewer1")
        assert result["state"] == CandidateState.ACCEPTED.value

        detail_row = _row(candidate_id)
        assert detail_row.blocking_findings == ["pages_missing", "machine_regression"]


class TestApplyCandidateStaleSource:
    @pytest.mark.asyncio
    async def test_stale_source_checksum_changed_makes_zero_writes(self, fake_client, app_service):
        candidate_id = await _make_applying_candidate(fake_client)
        # Simulate the live Paperless document changing after staging/accept.
        fake_client.previews = {1: make_minimal_pdf_bytes("a completely different document")}

        result = await app_service.apply_candidate(candidate_id, actor="reviewer1")
        assert result.get("error") == "stale_source"

        row = _row(candidate_id)
        assert row.state == CandidateState.FAILED.value
        assert row.failure_reason == "stale_source"
        assert not fake_client.upload_calls
        assert len(fake_client.versions[1]) == 1  # untouched


class TestApplyCandidateProviderFailure:
    @pytest.mark.asyncio
    async def test_task_failure_returns_to_ready_current_version_unchanged(
        self, fake_client, app_service
    ):
        fake_client.fail_tasks = True
        candidate_id = await _make_applying_candidate(fake_client)
        original_checksum = _checksum(fake_client.previews[1])

        result = await app_service.apply_candidate(candidate_id, actor="reviewer1")
        assert "error" in result

        row = _row(candidate_id)
        assert row.state == CandidateState.READY.value  # bounded retry, not terminal yet
        assert row.apply_attempts == 1
        assert row.apply_last_error

        # Current version is completely untouched.
        assert _checksum(fake_client.previews[1]) == original_checksum
        assert len(fake_client.versions[1]) == 1

    @pytest.mark.asyncio
    async def test_apply_attempts_exceeded_becomes_terminal_failed(self, fake_client, app_service):
        fake_client.fail_tasks = True
        candidate_id = await _make_applying_candidate(fake_client)

        for _ in range(ocr_quality_config.settings.candidate_max_apply_attempts):
            db = get_session()
            try:
                row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one()
                row.state = CandidateState.APPLYING.value
                row.paperless_task_id = None
                db.commit()
            finally:
                db.close()
            await app_service.apply_candidate(candidate_id, actor="reviewer1")

        row = _row(candidate_id)
        assert row.state == CandidateState.FAILED.value
        assert row.failure_reason == "apply_attempts_exceeded"

    @pytest.mark.asyncio
    async def test_upload_call_raising_does_not_corrupt_current_version(
        self, fake_client, app_service
    ):
        fake_client.raise_on_upload = RuntimeError("connection reset")
        candidate_id = await _make_applying_candidate(fake_client)
        original_checksum = _checksum(fake_client.previews[1])

        result = await app_service.apply_candidate(candidate_id, actor="reviewer1")
        assert "error" in result

        row = _row(candidate_id)
        assert row.state == CandidateState.READY.value
        assert _checksum(fake_client.previews[1]) == original_checksum
        assert len(fake_client.versions[1]) == 1


class TestApplyCandidateIdempotentRetry:
    @pytest.mark.asyncio
    async def test_retry_after_simulated_crash_does_not_reupload(self, fake_client, app_service):
        """Simulate a crash *after* Paperless successfully accepted the new
        version (the upload task completed) but *before* OWL's own apply
        flow persisted ACCEPTED — i.e. only ``paperless_task_id`` made it to
        the candidate row (exactly what ``_persist_task_id`` writes right
        after a successful upload, before polling/verification/finish). A
        retried apply must resume via the idempotency check (matching the
        already-uploaded version by checksum) rather than re-uploading.
        """
        candidate_id = await _make_applying_candidate(fake_client)

        candidate_pdf_bytes = _load_candidate_pdf_bytes(candidate_id)
        task_id = await fake_client.upload_document_version(
            1, "crash-sim.pdf", candidate_pdf_bytes, version_label="owl-candidate-crash-sim"
        )
        await fake_client.get_task(task_id)  # drive the fake task straight to SUCCESS
        assert len(fake_client.upload_calls) == 1

        db = get_session()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one()
            row.paperless_task_id = task_id
            db.commit()
        finally:
            db.close()

        result = await app_service.apply_candidate(candidate_id, actor="reviewer1")
        assert result.get("state") == CandidateState.ACCEPTED.value, result
        # No second upload: the idempotency check found the existing version
        # by checksum and skipped straight to verification.
        assert len(fake_client.upload_calls) == 1


class TestApplyCandidateConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_apply_attempt_blocked_by_lock(self, fake_client, app_service):
        candidate_id = await _make_applying_candidate(fake_client)

        # Simulate a lock already held by another in-flight request.
        db = get_session()
        try:
            db.add(
                OcrApplicationLock(
                    document_id=1,
                    locked_by="other-process",
                    locked_at=datetime.utcnow(),
                    expires_at=datetime.utcnow() + timedelta(seconds=300),
                    operation="apply",
                    candidate_id="some-other-candidate",
                )
            )
            db.commit()
        finally:
            db.close()

        result = await app_service.apply_candidate(candidate_id, actor="reviewer1")
        assert "error" in result
        assert "already in progress" in result["error"]
        assert not fake_client.upload_calls  # no double-write attempted

        row = _row(candidate_id)
        assert row.state == CandidateState.READY.value
        assert row.apply_attempts == 0  # contention doesn't consume a bounded attempt

    @pytest.mark.asyncio
    async def test_expired_lock_is_reclaimed(self, fake_client, app_service):
        candidate_id = await _make_applying_candidate(fake_client)

        db = get_session()
        try:
            db.add(
                OcrApplicationLock(
                    document_id=1,
                    locked_by="crashed-process",
                    locked_at=datetime.utcnow() - timedelta(seconds=600),
                    expires_at=datetime.utcnow() - timedelta(seconds=300),  # already expired
                    operation="apply",
                    candidate_id="some-other-candidate",
                )
            )
            db.commit()
        finally:
            db.close()

        result = await app_service.apply_candidate(candidate_id, actor="reviewer1")
        assert result["state"] == CandidateState.ACCEPTED.value


class TestRollback:
    @pytest.mark.asyncio
    async def test_rollback_restores_prior_version_and_reinvalidates(
        self, fake_client, app_service
    ):
        candidate_id = await _make_applying_candidate(fake_client)
        apply_result = await app_service.apply_candidate(candidate_id, actor="reviewer1")
        assert apply_result["state"] == CandidateState.ACCEPTED.value
        assert len(fake_client.versions[1]) == 2

        rollback_result = await app_service.rollback(1, actor="reviewer1", target_candidate_id=None)

        assert "error" not in rollback_result
        assert rollback_result["invalidation_recorded"] is True
        # Back down to just the root/original version.
        assert len(fake_client.versions[1]) == 1
        assert fake_client.versions[1][0]["is_root"] is True
        assert len(fake_client.delete_calls) == 1

        # A second invalidation event now exists (rollback), distinct from
        # the original apply's version_changed event.
        db = get_ai_session()
        try:
            events = (
                db.query(InvalidationEvent)
                .filter_by(document_id=1)
                .order_by(InvalidationEvent.id)
                .all()
            )
            assert [e.reason for e in events] == ["version_changed", "rollback"]
        finally:
            db.close()

        db = get_session()
        try:
            audit_events = (
                db.query(OcrApplicationEvent).filter_by(document_id=1, action="rollback").all()
            )
            assert len(audit_events) == 1
            assert audit_events[0].outcome == "success"
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_rollback_with_no_prior_accepted_candidate_falls_back_to_root(
        self, fake_client, ai_db, ocr_db
    ):
        service = OcrCandidateApplicationService(fake_client, get_session)
        result = await service.rollback(1, actor="reviewer1")
        assert "error" not in result
        assert len(fake_client.versions[1]) == 1  # nothing to delete, already at root

    @pytest.mark.asyncio
    async def test_rollback_blocked_by_concurrent_lock(self, fake_client, app_service):
        candidate_id = await _make_applying_candidate(fake_client)
        await app_service.apply_candidate(candidate_id, actor="reviewer1")

        db = get_session()
        try:
            db.add(
                OcrApplicationLock(
                    document_id=1,
                    locked_by="other-process",
                    locked_at=datetime.utcnow(),
                    expires_at=datetime.utcnow() + timedelta(seconds=300),
                    operation="apply",
                    candidate_id=None,
                )
            )
            db.commit()
        finally:
            db.close()

        result = await app_service.rollback(1, actor="reviewer1")
        assert "error" in result
        assert not fake_client.delete_calls


class TestCappedBatchesUnaffected:
    @pytest.mark.asyncio
    async def test_batch_caps_still_enforced_for_generation(self, fake_client, ocr_db, monkeypatch):
        """Apply/rollback add no new batch surface — the existing generation
        caps are untouched by this slice.
        """
        from doc_intelligence_hub.modules.ocr_quality.candidate_service import BatchCapExceeded

        monkeypatch.setattr(ocr_quality_config.settings, "candidate_max_documents_per_batch", 1)
        candidate_service = OcrCandidateService(fake_client, get_session)
        with pytest.raises(BatchCapExceeded):
            await candidate_service.request_candidates(
                document_ids=[1, 2], engines=["ocrmypdf-tesseract-5"]
            )
