from __future__ import annotations

import httpx
import pytest

from doc_intelligence_hub.modules.monarch_delivery.client import (
    TyrionReceiptBrokerClient,
)


async def content(value: bytes = b"%PDF fixture"):
    yield value


def broker_body(**overrides):
    payload = {
        "brokerContractVersion": "1.0",
        "idempotencyKey": "stable-key-123456",
        "outcome": "acknowledged",
        "acknowledged": True,
        "retrySafe": False,
        "reconcileRequired": False,
        "replicaRef": "opaque-replica",
        "replicaLifecycle": "matched",
        "revision": 3,
        "nativeEvidence": {
            "receiptSource": "upload",
            "receiptState": "matched",
            "transactionRef": None,
            "attachmentCount": 1,
            "sourceAsOf": None,
            "observedAt": "2026-10-11T00:00:00Z",
        },
        "reasonCodes": ["bridge_readback_acknowledged"],
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_submit_uses_exact_raw_body_and_headers():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=broker_body())

    client = TyrionReceiptBrokerClient(
        authority="http://tyrion-operations-ui:3000",
        api_token="secret",
        transport=httpx.MockTransport(handler),
    )
    result = await client.submit(
        idempotency_key="stable-key-123456",
        canonical_document_ref="canonical-ref",
        source_ref="source-ref",
        expected_revision=None,
        content=content(),
        media_type="application/pdf",
    )

    assert result.state == "acknowledged"
    assert result.acknowledged is True
    assert result.revision == 3
    assert seen[0].url.path == "/api/internal/v1/finance/receipt-broker/replicas"
    assert seen[0].content == b"%PDF fixture"
    assert seen[0].headers["authorization"] == "Bearer secret"
    assert seen[0].headers["content-type"] == "application/pdf"
    assert seen[0].headers["idempotency-key"] == "stable-key-123456"
    assert seen[0].headers["x-owl-canonical-document-ref"] == "canonical-ref"
    assert seen[0].headers["x-owl-source-ref"] == "source-ref"
    assert "x-tyrion-expected-revision" not in seen[0].headers
    await client.aclose()


@pytest.mark.asyncio
async def test_reconcile_omits_unknown_revision_and_maps_missing_key():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            404,
            json={"error": {"code": "receipt_broker_not_found", "message": "Not found"}},
        )

    client = TyrionReceiptBrokerClient(
        authority="http://tyrion-operations-ui:3000",
        api_token="secret",
        transport=httpx.MockTransport(handler),
    )
    result = await client.reconcile(
        idempotency_key="stable:key:123456",
        expected_revision=None,
    )

    assert result.state == "not_found"
    assert seen[0].url.path.endswith("/stable:key:123456/reconcile")
    assert "x-tyrion-expected-revision" not in seen[0].headers
    await client.aclose()


@pytest.mark.asyncio
async def test_reconcile_sends_known_revision_and_preserves_processing_ack():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            202,
            json=broker_body(
                outcome="processing",
                acknowledged=True,
                reconcileRequired=True,
                revision=4,
            ),
        )

    client = TyrionReceiptBrokerClient(
        authority="http://tyrion-operations-ui:3000",
        api_token="secret",
        transport=httpx.MockTransport(handler),
    )
    result = await client.reconcile(
        idempotency_key="stable-key-123456",
        expected_revision=3,
    )

    assert result.state == "processing"
    assert result.acknowledged is True
    assert result.reconcile_required is True
    assert seen[0].headers["x-tyrion-expected-revision"] == "3"
    await client.aclose()


@pytest.mark.asyncio
async def test_submit_rejects_oversize_before_network_call():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    client = TyrionReceiptBrokerClient(
        authority="http://tyrion-operations-ui:3000",
        api_token="secret",
        transport=httpx.MockTransport(handler),
    )
    result = await client.submit(
        idempotency_key="stable-key-123456",
        canonical_document_ref="canonical-ref",
        source_ref="source-ref",
        expected_revision=None,
        content=content(b"x" * 2_097_153),
        media_type="application/pdf",
    )

    assert result.state == "rejected"
    assert result.reason_code == "document_too_large"
    assert calls == 0
    await client.aclose()


def test_client_rejects_non_private_authority():
    with pytest.raises(ValueError, match="authority_must_be_private"):
        TyrionReceiptBrokerClient(
            authority="https://example.com",
            api_token="secret",
        )
