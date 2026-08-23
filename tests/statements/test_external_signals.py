from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from doc_intelligence_hub.modules.statements.correspondent_models import (
    DocumentExpectationSignalsV1,
)
from doc_intelligence_hub.modules.statements.external_signals import (
    DocumentExpectationSignalsClient,
)


@pytest.mark.asyncio
async def test_client_uses_configured_bearer_token() -> None:
    client = DocumentExpectationSignalsClient(
        "https://tyrion.test",
        api_token="test-token",
    )
    try:
        assert client._client.headers["Authorization"] == "Bearer test-token"
    finally:
        await client.close()


def test_projection_rejects_non_contract_snake_case_keys() -> None:
    with pytest.raises(ValidationError):
        DocumentExpectationSignalsV1.model_validate(
            {
                "contract_version": "1",
                "connector_ref": "opaque-connector",
                "source_generation": "generation-1",
                "source_as_of": "2026-08-23T00:00:00Z",
                "completeness": "complete",
                "signals": [],
            }
        )


def test_projection_requires_null_advisory_timing() -> None:
    base_signal = {
        "seriesRef": "opaque-account",
        "kind": "accountStatementCandidate",
        "active": True,
        "displayHint": "Credit account",
        "cadence": None,
        "nextExpectedDate": None,
        "confidence": 0.6,
        "basis": ["active_non_cash_account"],
    }
    payload = {
        "contractVersion": "1",
        "connectorRef": "opaque-connector",
        "sourceGeneration": "generation-1",
        "sourceAsOf": "2026-08-23T00:00:00Z",
        "completeness": "complete",
        "signals": [base_signal],
    }

    DocumentExpectationSignalsV1.model_validate(payload)
    with pytest.raises(ValidationError):
        DocumentExpectationSignalsV1.model_validate(
            {**payload, "signals": [{**base_signal, "cadence": "monthly"}]}
        )
    with pytest.raises(ValidationError):
        DocumentExpectationSignalsV1.model_validate(
            {**payload, "signals": [{**base_signal, "nextExpectedDate": "2026-09-01"}]}
        )
    with pytest.raises(ValidationError):
        DocumentExpectationSignalsV1.model_validate(
            {
                **payload,
                "signals": [
                    {key: value for key, value in base_signal.items() if key != "nextExpectedDate"}
                ],
            }
        )


@pytest.mark.asyncio
async def test_client_pulls_generation_addressed_projection() -> None:
    seen_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return httpx.Response(
            200,
            json={
                "contractVersion": "1",
                "connectorRef": "opaque connector",
                "sourceGeneration": "generation/one",
                "sourceAsOf": "2026-08-23T00:00:00Z",
                "completeness": "complete",
                "signals": [],
            },
        )

    client = DocumentExpectationSignalsClient("https://tyrion.test")
    await client.close()
    client._client = httpx.AsyncClient(  # noqa: SLF001 - transport injection for contract test
        base_url="https://tyrion.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        snapshot = await client.fetch("generation/one")
    finally:
        await client.close()

    assert snapshot.source_generation == "generation/one"
    assert seen_request is not None
    assert seen_request.url.raw_path.startswith(
        b"/api/connector/v1/document-expectation-signals/generation%2Fone"
    )
    assert "connectorRef" not in seen_request.url.params


@pytest.mark.asyncio
async def test_client_pulls_latest_projection_without_generation_input() -> None:
    seen_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return httpx.Response(
            200,
            json={
                "contractVersion": "1",
                "connectorRef": "opaque connector",
                "sourceGeneration": "generation-2",
                "sourceAsOf": "2026-08-23T00:00:00Z",
                "completeness": "complete",
                "signals": [],
            },
        )

    client = DocumentExpectationSignalsClient("https://tyrion.test")
    await client.close()
    client._client = httpx.AsyncClient(  # noqa: SLF001 - transport injection for contract test
        base_url="https://tyrion.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        snapshot = await client.fetch_latest()
    finally:
        await client.close()

    assert snapshot.source_generation == "generation-2"
    assert seen_request is not None
    assert seen_request.url.path == "/api/connector/v1/document-expectation-signals"
    assert "connectorRef" not in seen_request.url.params
