from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from doc_intelligence_hub.core.resilience import PaperlessError
from doc_intelligence_hub.modules.receipt_intake import database
from doc_intelligence_hub.modules.receipt_intake.config import settings
from doc_intelligence_hub.modules.receipt_intake.models import (
    AttemptState,
    IntakeMetadata,
    IntakeOutcome,
    SourceChannel,
)
from doc_intelligence_hub.modules.receipt_intake.service import (
    IntakeValidationError,
    ReceiptIntakeService,
    is_monarch_recovery_document,
)


class FakeGateway:
    def __init__(self):
        self.uploads: list[tuple[bytes, str, Path]] = []
        self.task = {"status": "success", "related_document": 101}
        self.existing_by_checksum: dict[str, dict] = {}
        self.upload_error: Exception | None = None

    async def upload_document(self, path: Path, media_type: str) -> str:
        self.uploads.append((path.read_bytes(), media_type, path))
        if self.upload_error:
            raise self.upload_error
        return "task-fixture"

    async def get_task(self, task_id: str):
        assert task_id == "task-fixture"
        return self.task

    async def find_document_by_checksum(self, checksum: str):
        return self.existing_by_checksum.get(checksum)


class BlockingGateway(FakeGateway):
    def __init__(self):
        super().__init__()
        self.upload_started = asyncio.Event()
        self.release_upload = asyncio.Event()

    async def upload_document(self, path: Path, media_type: str) -> str:
        self.uploads.append((path.read_bytes(), media_type, path))
        self.upload_started.set()
        await self.release_upload.wait()
        return "task-fixture"


async def chunks(content: bytes):
    midpoint = len(content) // 2
    yield content[:midpoint]
    yield content[midpoint:]


def metadata(
    occurrence: str,
    *,
    normalized: str | None = None,
    semantic: str | None = None,
) -> IntakeMetadata:
    return IntakeMetadata(
        source_channel=SourceChannel.EMAIL_ATTACHMENT,
        source_occurrence_id=occurrence * 64,
        connector_ref="connector-fixture",
        normalized_content_fingerprint=normalized,
        normalized_content_version="normalized-v1" if normalized else None,
        semantic_fingerprint=semantic,
        semantic_fingerprint_version="semantic-v1" if semantic else None,
    )


@pytest.fixture()
def ledger(tmp_path):
    original_url = settings.database_url
    original_interval = settings.task_poll_interval_seconds
    settings.database_url = f"sqlite:///{tmp_path / 'receipt-intake.db'}"
    settings.task_poll_interval_seconds = 0
    database.init_db()
    yield
    settings.database_url = original_url
    settings.task_poll_interval_seconds = original_interval


@pytest.mark.asyncio
async def test_new_intake_is_canonicalized_and_temporary_bytes_are_removed(ledger):
    gateway = FakeGateway()
    service = ReceiptIntakeService(gateway)

    result = await service.intake(
        metadata("a"),
        chunks(b"invented receipt fixture"),
        media_type="application/pdf",
    )

    assert result.outcome == IntakeOutcome.NEW_CANONICAL
    assert result.attempt_state == AttemptState.ACCEPTED
    assert result.canonical_document_ref.startswith("document_")
    assert result.retry_safe is True
    assert gateway.uploads[0][:2] == (b"invented receipt fixture", "application/pdf")
    assert not gateway.uploads[0][2].exists()

    with database.transaction() as connection:
        row = connection.execute("SELECT * FROM receipt_intake_attempts").fetchone()
        assert set(row.keys()).isdisjoint({"content", "filename", "ocr", "subject"})
        assert row["byte_count"] == len(b"invented receipt fixture")


@pytest.mark.asyncio
async def test_source_occurrence_and_exact_hash_are_idempotent(ledger):
    gateway = FakeGateway()
    service = ReceiptIntakeService(gateway)
    first = await service.intake(metadata("a"), chunks(b"same bytes"), media_type="image/png")
    repeated = await service.intake(metadata("a"), chunks(b"same bytes"), media_type="image/png")
    exact = await service.intake(metadata("b"), chunks(b"same bytes"), media_type="image/png")

    assert first.outcome == IntakeOutcome.NEW_CANONICAL
    assert repeated.outcome == IntakeOutcome.SOURCE_OCCURRENCE_REUSED
    assert exact.outcome == IntakeOutcome.EXACT_HASH_REUSED
    assert first.canonical_document_ref == repeated.canonical_document_ref
    assert first.canonical_document_ref == exact.canonical_document_ref
    assert len(gateway.uploads) == 1


@pytest.mark.asyncio
async def test_concurrent_same_occurrence_has_single_upload_owner(ledger):
    gateway = BlockingGateway()
    service = ReceiptIntakeService(gateway)
    first = asyncio.create_task(
        service.intake(metadata("a"), chunks(b"same bytes"), media_type="image/png")
    )
    await gateway.upload_started.wait()
    second = await service.intake(metadata("a"), chunks(b"same bytes"), media_type="image/png")
    gateway.release_upload.set()
    accepted = await first

    assert second.attempt_state == AttemptState.PENDING
    assert accepted.attempt_state == AttemptState.ACCEPTED
    assert len(gateway.uploads) == 1


@pytest.mark.asyncio
async def test_occurrence_reuse_with_different_bytes_is_conflict(ledger):
    service = ReceiptIntakeService(FakeGateway())
    await service.intake(metadata("a"), chunks(b"first"), media_type="application/pdf")

    with pytest.raises(IntakeValidationError, match="source_occurrence_hash_conflict"):
        await service.intake(metadata("a"), chunks(b"second"), media_type="application/pdf")


@pytest.mark.asyncio
async def test_occurrence_reuse_with_different_channel_is_conflict(ledger):
    service = ReceiptIntakeService(FakeGateway())
    await service.intake(metadata("a"), chunks(b"same"), media_type="application/pdf")
    conflicting = metadata("a").model_copy(update={"source_channel": SourceChannel.SCANNER_JOB})

    with pytest.raises(IntakeValidationError, match="source_occurrence_identity_conflict"):
        await service.intake(conflicting, chunks(b"same"), media_type="application/pdf")


@pytest.mark.asyncio
async def test_normalized_match_pauses_before_upload_and_retains_tombstone(ledger):
    gateway = FakeGateway()
    service = ReceiptIntakeService(gateway)
    fingerprint = "1" * 64
    await service.intake(
        metadata("a", normalized=fingerprint),
        chunks(b"original rendering"),
        media_type="application/pdf",
    )
    result = await service.intake(
        metadata("b", normalized=fingerprint),
        chunks(b"rescanned rendering"),
        media_type="application/pdf",
    )

    assert result.outcome == IntakeOutcome.NORMALIZED_CONTENT_REVIEW
    assert result.attempt_state == AttemptState.REVIEW_REQUIRED
    assert result.review_ref
    assert len(gateway.uploads) == 1

    service.reject_proposal(result.review_ref)
    with database.transaction() as connection:
        proposal = connection.execute(
            "SELECT proposal_state, resolved_at FROM receipt_match_proposals"
            " WHERE proposal_ref = ?",
            (result.review_ref,),
        ).fetchone()
    assert proposal["proposal_state"] == "rejected"
    assert proposal["resolved_at"] is not None

    gateway.task = {"status": "success", "related_document": 103}
    accepted = await service.intake(
        metadata("b", normalized=fingerprint),
        chunks(b"rescanned rendering"),
        media_type="application/pdf",
    )
    assert accepted.attempt_state == AttemptState.ACCEPTED
    assert len(gateway.uploads) == 2


@pytest.mark.asyncio
async def test_review_required_replay_preserves_review_contract(ledger):
    gateway = FakeGateway()
    service = ReceiptIntakeService(gateway)
    fingerprint = "3" * 64
    await service.intake(
        metadata("a", normalized=fingerprint),
        chunks(b"original"),
        media_type="application/pdf",
    )
    review = await service.intake(
        metadata("b", normalized=fingerprint),
        chunks(b"rescan"),
        media_type="application/pdf",
    )
    replay = await service.intake(
        metadata("b", normalized=fingerprint),
        chunks(b"rescan"),
        media_type="application/pdf",
    )
    assert replay.outcome == IntakeOutcome.NORMALIZED_CONTENT_REVIEW
    assert replay.review_ref == review.review_ref


@pytest.mark.asyncio
async def test_semantic_match_never_suppresses_canonical_ingestion(ledger):
    gateway = FakeGateway()
    service = ReceiptIntakeService(gateway)
    fingerprint = "2" * 64
    await service.intake(
        metadata("a", semantic=fingerprint),
        chunks(b"order confirmation"),
        media_type="application/pdf",
    )
    gateway.task = {"status": "success", "related_document": 102}
    result = await service.intake(
        metadata("b", semantic=fingerprint),
        chunks(b"payment receipt"),
        media_type="application/pdf",
    )

    assert result.outcome == IntakeOutcome.SEMANTIC_REVIEW_CANDIDATE
    assert result.attempt_state == AttemptState.ACCEPTED
    assert result.review_ref
    assert len(gateway.uploads) == 2


@pytest.mark.asyncio
async def test_paperless_duplicate_is_explicit_outcome(ledger):
    gateway = FakeGateway()
    gateway.task = {
        "status": "failure",
        "error_code": "duplicate",
        "related_document": 303,
    }
    result = await ReceiptIntakeService(gateway).intake(
        metadata("a"),
        chunks(b"duplicate fixture"),
        media_type="application/pdf",
    )

    assert result.outcome == IntakeOutcome.PAPERLESS_DUPLICATE
    assert result.attempt_state == AttemptState.ACCEPTED
    assert result.canonical_document_ref


@pytest.mark.asyncio
async def test_retryable_failure_uses_same_occurrence_with_bounded_retry(ledger):
    gateway = FakeGateway()
    gateway.task = {"status": "failure", "error_code": "temporary"}
    service = ReceiptIntakeService(gateway)
    first = await service.intake(
        metadata("a"), chunks(b"retry fixture"), media_type="application/pdf"
    )
    assert first.outcome == IntakeOutcome.UPLOAD_RETRYABLE
    assert first.attempt_state == AttemptState.RETRYABLE

    gateway.task = {"status": "success", "related_document": 304}
    accepted = await service.intake(
        metadata("a"), chunks(b"retry fixture"), media_type="application/pdf"
    )
    assert accepted.attempt_state == AttemptState.ACCEPTED
    assert accepted.canonical_document_ref
    assert len(gateway.uploads) == 2


@pytest.mark.asyncio
async def test_unknown_upload_is_not_blindly_retried_and_can_be_verified(ledger):
    gateway = FakeGateway()
    gateway.upload_error = TimeoutError()
    service = ReceiptIntakeService(gateway)
    content = b"unknown outcome fixture"
    result = await service.intake(metadata("a"), chunks(content), media_type="application/pdf")

    assert result.outcome == IntakeOutcome.UPLOAD_OUTCOME_UNKNOWN
    assert result.retry_safe is False

    gateway.existing_by_checksum[result_hash(content)] = {"id": 404}
    verified = await service.intake(metadata("a"), chunks(content), media_type="application/pdf")
    assert verified.attempt_state == AttemptState.ACCEPTED
    assert verified.canonical_document_ref
    assert len(gateway.uploads) == 1


@pytest.mark.asyncio
async def test_task_poll_failure_becomes_unknown_not_pending(ledger):
    gateway = FakeGateway()
    gateway.get_task = AsyncMock(side_effect=PaperlessError("fixture timeout"))
    service = ReceiptIntakeService(gateway)
    result = await service.intake(
        metadata("a"), chunks(b"poll failure"), media_type="application/pdf"
    )
    assert result.attempt_state == AttemptState.UNKNOWN
    assert result.outcome == IntakeOutcome.UPLOAD_OUTCOME_UNKNOWN
    assert result.reason_codes == ["paperless_task_query_unknown"]


@pytest.mark.asyncio
async def test_spool_cancellation_removes_temporary_file(ledger, tmp_path, monkeypatch):
    temporary_path = tmp_path / "cancelled-receipt.tmp"

    def fake_mkstemp(*args, **kwargs):
        import os

        descriptor = os.open(temporary_path, os.O_CREAT | os.O_RDWR)
        return descriptor, str(temporary_path)

    async def cancelled_body():
        yield b"partial"
        raise asyncio.CancelledError

    monkeypatch.setattr(
        "doc_intelligence_hub.modules.receipt_intake.service.tempfile.mkstemp",
        fake_mkstemp,
    )
    with pytest.raises(asyncio.CancelledError):
        await ReceiptIntakeService(FakeGateway()).intake(
            metadata("a"), cancelled_body(), media_type="application/pdf"
        )
    assert not temporary_path.exists()


@pytest.mark.asyncio
async def test_external_replica_lifecycle_is_byte_free(ledger):
    gateway = FakeGateway()
    service = ReceiptIntakeService(gateway)
    result = await service.intake(
        metadata("a"), chunks(b"replica fixture"), media_type="application/pdf"
    )
    replica = service.record_external_replica(
        canonical_ref=result.canonical_document_ref,
        external_system="monarch",
        external_replica_ref="opaque-receipt-fixture",
        lifecycle_state="processing",
        source_revision="fixture-revision",
    )
    updated = service.record_external_replica(
        canonical_ref=result.canonical_document_ref,
        external_system="monarch",
        external_replica_ref="opaque-receipt-fixture",
        lifecycle_state="matched",
        source_revision="fixture-revision-2",
        expected_revision=replica.revision,
    )
    assert updated.replica_ref == replica.replica_ref
    assert updated.revision == 2

    with pytest.raises(ValueError, match="revision_conflict"):
        service.record_external_replica(
            canonical_ref=result.canonical_document_ref,
            external_system="monarch",
            external_replica_ref="opaque-receipt-fixture",
            lifecycle_state="settled",
            expected_revision=1,
        )

    gateway.task = {"status": "success", "related_document": 102}
    other = await service.intake(
        metadata("b"), chunks(b"other replica fixture"), media_type="application/pdf"
    )
    with pytest.raises(ValueError, match="canonical_conflict"):
        service.record_external_replica(
            canonical_ref=other.canonical_document_ref,
            external_system="monarch",
            external_replica_ref="opaque-receipt-fixture",
            lifecycle_state="matched",
            expected_revision=2,
        )


@pytest.mark.asyncio
async def test_monarch_recovery_prevents_structural_resubmission_loop(ledger):
    gateway = FakeGateway()
    service = ReceiptIntakeService(gateway)
    recovery_metadata = IntakeMetadata(
        source_channel=SourceChannel.MONARCH_RECOVERY,
        source_occurrence_id="f" * 64,
        connector_ref="connector-fixture",
    )
    result = await service.intake(
        recovery_metadata,
        chunks(b"recovered fixture"),
        media_type="application/pdf",
    )
    assert result.external_replica_eligible is False
    assert is_monarch_recovery_document(101) is True
    assert is_monarch_recovery_document(999) is False


def result_hash(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()


def test_schema_migration_is_idempotent_and_versioned(ledger):
    database.init_db()
    with database.transaction() as connection:
        version = connection.execute("SELECT version FROM receipt_schema_version").fetchone()
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert version["version"] == 1
    assert {
        "receipt_intake_attempts",
        "receipt_canonical_documents",
        "receipt_match_proposals",
        "receipt_external_replicas",
        "receipt_audit_events",
    }.issubset(tables)


def test_database_rejects_duplicate_source_occurrence(ledger):
    with database.transaction() as connection:
        columns = connection.execute("PRAGMA table_info(receipt_intake_attempts)").fetchall()
    assert any(column["name"] == "source_occurrence_id" for column in columns)
