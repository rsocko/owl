from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

from pydantic import SecretStr

from doc_intelligence_hub.modules.action_queue.database import Action, Obligation, get_session
from doc_intelligence_hub.modules.action_queue.payment_reconciliation import (
    TyrionBillMatchResponseV2,
)

RECEIPT_HEADERS = {"Authorization": "Bearer receipt-intake-test-token"}
MC_HEADERS = {"Authorization": "Bearer mc-secret"}


def _seed_obligation() -> None:
    db = get_session()
    try:
        db.add(
            Obligation(
                id="api-obligation",
                status="open",
                expected_amount_minor=10_000,
                currency="USD",
            )
        )
        db.commit()
    finally:
        db.close()


def _ambiguous_snapshot() -> dict:
    return {
        "contract_version": "1.0",
        "source_system": "tyrion_bill_match",
        "connector_ref": "opaque-connector",
        "source_generation": "api-generation-1",
        "source_as_of": "2026-10-10T12:00:00Z",
        "completeness": "partial",
        "obligation_id": "api-obligation",
        "evidence": [
            {
                "evidence_ref": "api-evidence-reference",
                "kind": "bill",
                "amount_minor": 10_000,
                "currency": "USD",
                "source_state": "ambiguous",
                "match_state": "ambiguous",
                "payment_status": "ambiguous",
                "reason_codes": ["close_candidates"],
            }
        ],
    }


def test_strict_aggregate_auth_actions_attention_and_privacy(client, app) -> None:
    _seed_obligation()
    created = client.post(
        "/api/payment-reconciliation/v1/evidence",
        headers=RECEIPT_HEADERS,
        json=_ambiguous_snapshot(),
    )
    assert created.status_code == 200
    assert client.get("/api/mc/v1/payment-reconciliation-reviews").status_code == 503

    app.state.hub_settings.mission_control_api_token = SecretStr("mc-secret")
    queue = client.get(
        "/api/mc/v1/payment-reconciliation-reviews",
        headers=MC_HEADERS,
    )
    assert queue.status_code == 200
    item = queue.json()[0]
    assert item["case_kind"] == "ambiguous"
    assert item["revision"] == 1
    serialized = queue.text.lower()
    assert all(
        forbidden not in serialized
        for forbidden in (
            "transaction_ref",
            "receipt_ref",
            "canonical_document_ref",
            "account",
            "signed_url",
            "ocr",
        )
    )

    delivered = client.post(
        f"/api/mc/v1/payment-reconciliation-reviews/{item['id']}/actions",
        headers=MC_HEADERS,
        json={
            "contract_version": "1.0",
            "action": "deliver_attention",
            "expected_revision": 1,
            "idempotency_key": "delivery-key-1",
            "attention_delivery_ref": "mission-control-delivery-1",
        },
    )
    assert delivered.status_code == 200
    assert delivered.json()["case"]["attention_state"] == "delivered"
    assert delivered.json()["authoritative_read_back"] is True

    stale = client.post(
        f"/api/mc/v1/payment-reconciliation-reviews/{item['id']}/actions",
        headers=MC_HEADERS,
        json={
            "contract_version": "1.0",
            "action": "acknowledge_attention",
            "expected_revision": 1,
            "idempotency_key": "ack-key-1",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "payment_reconciliation_revision_conflict"

    replay = client.post(
        f"/api/mc/v1/payment-reconciliation-reviews/{item['id']}/actions",
        headers=MC_HEADERS,
        json={
            "contract_version": "1.0",
            "action": "deliver_attention",
            "expected_revision": 1,
            "idempotency_key": "delivery-key-1",
            "attention_delivery_ref": "mission-control-delivery-1",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent"] is True


def test_replica_write_back_uses_independent_owl_revision(client, mock_paperless) -> None:
    mock_paperless.upload_document.return_value = "task-fixture"
    mock_paperless.get_task.return_value = {"status": "success", "related_document": 818}
    intake = client.post(
        "/api/receipt-intake/v1/occurrences",
        content=b"%PDF-synthetic receipt",
        headers={
            **RECEIPT_HEADERS,
            "Content-Type": "application/pdf",
            "X-OWL-Source-Channel": "manual_upload",
            "X-OWL-Source-Occurrence": "a" * 64,
            "X-OWL-Source-Occurrence-Version": "1",
            "X-OWL-Source-Observed-At": "2026-10-10T12:00:00Z",
        },
    )
    canonical_ref = intake.json()["canonical_document_ref"]

    first = client.post(
        "/api/receipt-intake/v1/external-replicas",
        headers=RECEIPT_HEADERS,
        json={
            "canonical_document_ref": canonical_ref,
            "external_system": "monarch",
            "external_replica_ref": "receipt-v1_opaque",
            "lifecycle_state": "submitted",
            "source_revision": 1,
            "expected_revision": 0,
        },
    )
    assert first.status_code == 200
    assert first.json()["revision"] == 1

    stale = client.post(
        "/api/receipt-intake/v1/external-replicas",
        headers=RECEIPT_HEADERS,
        json={
            "canonical_document_ref": canonical_ref,
            "external_system": "monarch",
            "external_replica_ref": "receipt-v1_opaque",
            "lifecycle_state": "matched",
            "source_revision": 2,
            "expected_revision": 0,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "external_replica_revision_conflict"


def test_active_check_and_confirmation_complete_action_atomically(client, app) -> None:
    db = get_session()
    try:
        obligation = Obligation(
            id="active-check-obligation",
            status="open",
            expected_amount_minor=8_245,
            currency="USD",
        )
        action = Action(
            id=701,
            document_id=1701,
            document_title="Utility bill",
            action_type="PAY",
            title="Pay utility bill",
            amount=82.45,
            due_date=date(2026, 10, 20),
            document_date=date(2026, 10, 10),
            payee_or_owner="City Utilities",
            correspondent="City Utilities",
            status="pending",
            action_ready=True,
            obligation_id=obligation.id,
        )
        db.add_all([obligation, action])
        db.commit()
    finally:
        db.close()

    app.state.hub_settings.tyrion_bill_matching_url = (
        "http://tyrion.test/api/connector/v1/bill-matches"
    )
    tyrion_response = TyrionBillMatchResponseV2.model_validate(
        {
            "contractVersion": "2.0",
            "requestRef": "placeholder-request-ref",
            "billRef": "paperless:1701",
            "matchStatus": "matched",
            "paymentStatus": "paid",
            "selectedTransactionRef": "transaction-posted-1",
            "candidates": [
                {
                    "rank": 1,
                    "transactionRef": "transaction-posted-1",
                    "transactionDate": "2026-10-18",
                    "transactionState": "posted",
                    "payee": "City Utilities",
                    "amountMinor": 8245,
                    "currency": "USD",
                    "accountDisplayName": "Household checking",
                    "accountLastFour": "1234",
                    "scoreBasisPoints": 9600,
                    "confidence": "high",
                    "scoreGapBasisPoints": 2100,
                    "signals": [
                        {
                            "kind": "amount",
                            "strength": "exact",
                            "contributionBasisPoints": 5500,
                        },
                        {
                            "kind": "date",
                            "strength": "strong",
                            "contributionBasisPoints": 2200,
                        },
                        {
                            "kind": "payee",
                            "strength": "strong",
                            "contributionBasisPoints": 1400,
                        },
                        {
                            "kind": "account",
                            "strength": "partial",
                            "contributionBasisPoints": 500,
                        },
                    ],
                }
            ],
            "searchContext": {
                "anchorDate": "2026-10-20",
                "windowStart": "2026-09-20",
                "windowEnd": "2026-10-27",
                "dateWindowDays": 30,
                "amountToleranceMinor": 100,
                "sourceAsOf": "2026-10-18T12:00:00Z",
                "searchedAt": "2026-10-18T12:01:00Z",
                "eligibleAccountCount": 3,
                "candidatePoolSize": 8,
            },
        }
    )

    listed = client.get("/api/action-queue/actions").json()[0]
    payment = listed["payment_check"]
    check_action = next(
        action for action in listed["source_actions"] if action["id"] == "check_payment"
    )
    assert payment["state"] == "unchecked"

    async def checked(request):
        return tyrion_response.model_copy(update={"request_ref": request.request_ref})

    with patch(
        "doc_intelligence_hub.modules.action_queue.payment_reconciliation."
        "TyrionBillMatchingClient.check",
        new=AsyncMock(side_effect=checked),
    ):
        checked_response = client.post(
            check_action["url"],
            json={
                "revision": check_action["revision"],
                "idempotency_key": check_action["idempotency_key"],
            },
        )

    assert checked_response.status_code == 200
    checked_payment = checked_response.json()["action"]["payment_check"]
    assert checked_payment["state"] == "posted_match"
    assert checked_payment["candidates"][0] == {
        "id": checked_payment["candidates"][0]["id"],
        "payee": "City Utilities",
        "amount": 82.45,
        "currency": "USD",
        "transaction_date": "2026-10-18",
        "status": "posted",
        "account_display_name": "Household checking",
        "account_last_four": "1234",
        "confidence": "high",
        "score": 96.0,
        "rank": 1,
        "score_gap_basis_points": 2100,
        "match_reasons": [
            "amount_exact",
            "date_strong",
            "payee_strong",
            "account_partial",
        ],
        "receipt_association": None,
    }
    assert checked_payment["search_context"]["eligible_account_count"] == 3

    refreshed = client.get("/api/action-queue/actions").json()[0]
    confirm_action = next(
        action
        for action in refreshed["source_actions"]
        if action["id"] == "confirm_payment_and_complete"
    )
    confirmed = client.post(
        confirm_action["url"],
        json={
            "revision": confirm_action["revision"],
            "idempotency_key": confirm_action["idempotency_key"],
        },
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["action"]["status"] == "completed"
    assert confirmed.json()["payment_check"]["state"] == "confirmed"
    db = get_session()
    try:
        stored_action = db.query(Action).filter_by(id=701).one()
        stored_obligation = db.query(Obligation).filter_by(id="active-check-obligation").one()
        assert stored_action.status == "completed"
        assert stored_obligation.status == "settled"
    finally:
        db.close()
