from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from doc_intelligence_hub.modules.statements.correspondent_models import (
    paperless_deployment_identity,
)
from doc_intelligence_hub.modules.statements.database import Database
from doc_intelligence_hub.modules.statements.payee_review import (
    PayeePatternProjectionV1,
    PayeeReviewAction,
    PayeeReviewService,
    TyrionPayeePatternsClient,
)

DEPLOYMENT_ID = paperless_deployment_identity("http://paperless.test")


def _projection(
    generation: str = "generation-1",
    *,
    display_name: str = "Example Utility",
    activity: str = "active",
) -> PayeePatternProjectionV1:
    return PayeePatternProjectionV1.model_validate(
        {
            "contractVersion": "1",
            "connectorRef": "opaque-connector",
            "sourceGeneration": generation,
            "sourceAsOf": "2026-10-08T12:00:00Z",
            "completeness": "complete",
            "payees": [
                {
                    "payeeRef": "opaque-payee",
                    "displayName": display_name,
                    "activity": activity,
                    "classification": "recurring-variable",
                    "observationCount": 8,
                    "observationWindow": {
                        "firstObservedOn": "2026-01-01",
                        "lastObservedOn": "2026-09-30",
                    },
                    "intervalEvidence": {
                        "sampleCount": 7,
                        "medianDays": 30,
                        "minimumDays": 27,
                        "maximumDays": 34,
                    },
                    "confidence": 0.85,
                    "basis": ["interval_pattern"],
                    "provenance": {
                        "transactionHistory": True,
                        "monarchRecurring": True,
                    },
                    "monarchConfirmedRecurring": {
                        "active": True,
                        "cadence": "monthly",
                    },
                }
            ],
        }
    )


def test_projection_is_exact_and_rejects_transactions_or_document_cadence() -> None:
    payload = _projection().model_dump(by_alias=True)
    payload["payees"][0]["transactions"] = [{"amount": 10}]
    with pytest.raises(ValidationError):
        PayeePatternProjectionV1.model_validate(payload)

    payload = _projection().model_dump(by_alias=True)
    payload["payees"][0]["documentCadence"] = "monthly"
    with pytest.raises(ValidationError):
        PayeePatternProjectionV1.model_validate(payload)


@pytest.mark.asyncio
async def test_client_uses_settled_generation_replay_contract() -> None:
    seen: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen
        seen = request
        return httpx.Response(
            200,
            json=_projection("generation/one").model_dump(by_alias=True, mode="json"),
        )

    client = TyrionPayeePatternsClient("https://tyrion.test")
    await client.close()
    client._client = httpx.AsyncClient(  # noqa: SLF001
        base_url="https://tyrion.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.fetch("generation/one", "opaque-connector")
    finally:
        await client.close()

    assert result.source_generation == "generation/one"
    assert seen is not None
    assert seen.url.raw_path.startswith(b"/api/connector/v1/payee-patterns/generation%2Fone")
    assert seen.url.params["connectorRef"] == "opaque-connector"


def test_review_identity_history_and_no_documents_decision_are_durable(tmp_path) -> None:
    service = PayeeReviewService(Database(str(tmp_path / "statements.db")), DEPLOYMENT_ID)
    try:
        service.database.reconcile_correspondents(
            DEPLOYMENT_ID, [{"id": 42, "name": "Example Utility"}]
        )
        first = service.replace_snapshot(_projection())
        candidate = service.list_queue()[0]

        reviewed = service.review(
            candidate.id,
            PayeeReviewAction(
                document_decision="no_documents_expected",
                mappings=[{"correspondent_id": 42}],
                notes="Paperless documents are not produced.",
            ),
        )
        service.replace_snapshot(_projection("generation-2", display_name="Renamed Utility"))
        reconciled = service.list_queue(status="reviewed")[0]
        history = service.list_history(candidate.id)

        assert first.active_candidates == 1
        assert reviewed.document_decision == "no_documents_expected"
        assert reconciled.id == candidate.id
        assert reconciled.display_hint == "Renamed Utility"
        assert reconciled.document_decision == "no_documents_expected"
        assert reconciled.mappings[0].correspondent_id == 42
        assert len(history) == 1
        assert history[0].previous_state["document_decision"] == "unknown"
        assert history[0].new_state["document_decision"] == "no_documents_expected"
    finally:
        service.close()


def test_stale_projection_is_recorded_without_overwriting_current_state(tmp_path) -> None:
    service = PayeeReviewService(Database(str(tmp_path / "statements.db")), DEPLOYMENT_ID)
    try:
        current = PayeePatternProjectionV1.model_validate(
            {
                **_projection("current", display_name="Current Utility").model_dump(
                    by_alias=True, mode="json"
                ),
                "sourceAsOf": "2026-10-08T12:00:00Z",
            }
        )
        stale = PayeePatternProjectionV1.model_validate(
            {
                **_projection("stale", display_name="Stale Utility").model_dump(
                    by_alias=True, mode="json"
                ),
                "sourceAsOf": "2026-09-01T12:00:00Z",
            }
        )
        service.replace_snapshot(current)
        result = service.replace_snapshot(stale)

        assert result.idempotent is True
        assert service.list_queue()[0].display_hint == "Current Utility"
        assert service.replace_snapshot(stale).idempotent is True
    finally:
        service.close()
