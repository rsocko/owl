from __future__ import annotations

from pydantic import SecretStr

from doc_intelligence_hub.modules.action_queue.database import Obligation, get_session

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
