from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest

from doc_intelligence_hub.modules.monarch_delivery import database
from doc_intelligence_hub.modules.monarch_delivery.config import settings
from doc_intelligence_hub.modules.monarch_delivery.models import GatewayResult, GatewayState
from doc_intelligence_hub.modules.monarch_delivery.service import MonarchDeliveryService


class FakeGateway:
    def __init__(self) -> None:
        self.submit_result = GatewayResult(
            state=GatewayState.ACKNOWLEDGED,
            acknowledgement_ref="external-secret-id",
            revision=1,
            acknowledged=True,
        )
        self.reconcile_result = GatewayResult(state=GatewayState.NOT_FOUND)
        self.submit_calls = 0
        self.reconcile_calls = 0
        self.timeout_submit = False

    async def submit(
        self,
        *,
        idempotency_key,
        canonical_document_ref,
        source_ref,
        expected_revision,
        content,
        media_type,
    ):
        self.submit_calls += 1
        assert len(idempotency_key) == 64
        assert len(canonical_document_ref) == 64
        assert len(source_ref) == 64
        assert media_type == "application/pdf"
        assert b"".join([chunk async for chunk in content]) == b"fixture"
        if self.timeout_submit:
            raise httpx.ReadTimeout("unknown")
        return self.submit_result

    async def reconcile(self, *, idempotency_key, expected_revision):
        self.reconcile_calls += 1
        assert len(idempotency_key) == 64
        return self.reconcile_result


@asynccontextmanager
async def content_factory():
    async def chunks():
        yield b"fixture"

    yield chunks(), "application/pdf"


@pytest.fixture(autouse=True)
def ledger(tmp_path):
    original = settings.database_url
    settings.database_url = f"sqlite:///{tmp_path / 'monarch.db'}"
    database.init_db()
    yield
    settings.database_url = original


@pytest.mark.asyncio
async def test_acknowledgement_is_idempotent_and_stores_no_external_identifier():
    gateway = FakeGateway()
    service = MonarchDeliveryService(
        gateway,
        deployment_id="test",
        identity_secret="test-secret-value",
        write_enabled=True,
    )

    first = await service.send(
        42,
        content_factory=content_factory,
        recovery_origin=False,
    )
    second = await service.send(
        42,
        content_factory=content_factory,
        recovery_origin=False,
    )

    assert first.state == "acknowledged"
    assert second.state == "acknowledged"
    assert gateway.submit_calls == 1
    with database.transaction() as connection:
        row = connection.execute("SELECT * FROM monarch_document_deliveries").fetchone()
        assert "external-secret-id" not in repr(dict(row))
        assert row["acknowledgement_ref_digest"]


@pytest.mark.asyncio
async def test_unknown_outcome_reconciles_without_blind_resubmit():
    gateway = FakeGateway()
    gateway.timeout_submit = True
    service = MonarchDeliveryService(
        gateway,
        deployment_id="test",
        identity_secret="test-secret-value",
        write_enabled=True,
    )

    unknown = await service.send(42, content_factory=content_factory, recovery_origin=False)
    reconciled = await service.send(42, content_factory=content_factory, recovery_origin=False)

    assert unknown.state == "unknown"
    assert reconciled.state == "retryable"
    assert gateway.submit_calls == 1
    assert gateway.reconcile_calls == 1


@pytest.mark.asyncio
async def test_retry_reconciles_before_recreating():
    gateway = FakeGateway()
    gateway.timeout_submit = True
    service = MonarchDeliveryService(
        gateway,
        deployment_id="test",
        identity_secret="test-secret-value",
        write_enabled=True,
    )
    await service.send(42, content_factory=content_factory, recovery_origin=False)

    gateway.timeout_submit = False
    retried = await service.retry(42, content_factory=content_factory)

    assert retried.state == "acknowledged"
    assert gateway.reconcile_calls == 1
    assert gateway.submit_calls == 2


@pytest.mark.asyncio
async def test_recovery_origin_is_structurally_blocked():
    gateway = FakeGateway()
    service = MonarchDeliveryService(
        gateway,
        deployment_id="test",
        identity_secret="test-secret-value",
        write_enabled=True,
    )

    result = await service.send(42, content_factory=content_factory, recovery_origin=True)

    assert result.state == "blocked_recovery"
    assert result.reason_code == "monarch_recovery_loop_prevented"
    assert gateway.submit_calls == 0


@pytest.mark.asyncio
async def test_write_gate_defaults_to_denial():
    service = MonarchDeliveryService(
        FakeGateway(),
        deployment_id="test",
        identity_secret="test-secret-value",
        write_enabled=False,
    )

    with pytest.raises(PermissionError, match="monarch_delivery_write_disabled"):
        await service.send(42, content_factory=content_factory, recovery_origin=False)


@pytest.mark.asyncio
async def test_paperless_download_failure_is_retryable_not_unknown():
    gateway = FakeGateway()
    service = MonarchDeliveryService(
        gateway,
        deployment_id="test",
        identity_secret="test-secret-value",
        write_enabled=True,
    )

    @asynccontextmanager
    async def failed_content_factory():
        raise httpx.ReadError("Paperless unavailable")
        yield

    result = await service.send(
        42,
        content_factory=failed_content_factory,
        recovery_origin=False,
    )

    assert result.state == "retryable"
    assert result.reason_code == "paperless_download_retryable"
    assert gateway.submit_calls == 0


@pytest.mark.asyncio
async def test_tag_failure_does_not_erase_authoritative_acknowledgement():
    service = MonarchDeliveryService(
        FakeGateway(),
        deployment_id="test",
        identity_secret="test-secret-value",
        write_enabled=True,
    )

    async def fail_tag():
        raise RuntimeError("Paperless unavailable")

    result = await service.send(
        42,
        content_factory=content_factory,
        recovery_origin=False,
        on_acknowledged=fail_tag,
    )

    assert result.state == "acknowledged"
    assert result.display_tag_pending is True


def test_restart_recovers_interrupted_submission_as_unknown():
    with database.transaction(immediate=True) as connection:
        connection.execute(
            """
            INSERT INTO monarch_document_deliveries (
                delivery_ref, document_ref_digest, source_ref, idempotency_key, state,
                attempt_count, created_at, updated_at
            ) VALUES (
                'delivery', 'document', 'source', 'idempotency',
                'submitting', 1, 'now', 'now'
            )
            """
        )

    assert database.recover_interrupted_submissions() == 1
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT state, reason_code FROM monarch_document_deliveries"
        ).fetchone()
    assert row["state"] == "unknown"
    assert row["reason_code"] == "interrupted_submit_requires_reconciliation"
