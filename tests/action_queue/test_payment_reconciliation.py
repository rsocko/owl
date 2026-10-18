from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from doc_intelligence_hub.modules.action_queue.config import settings
from doc_intelligence_hub.modules.action_queue.database import (
    Action,
    Obligation,
    ObligationDocument,
    ObligationPaymentEdge,
    PaymentCheckRun,
    PaymentEvidence,
    PaymentEvidenceGeneration,
    PaymentReconciliationCase,
    PaymentRelationshipEvent,
    get_session,
    init_db,
)
from doc_intelligence_hub.modules.action_queue.obligations import sync_obligation_status
from doc_intelligence_hub.modules.action_queue.payment_reconciliation import (
    IdempotencyConflictError,
    PaymentCheckInProgressError,
    PaymentCheckRequest,
    PaymentEvidenceSnapshot,
    PaymentReconciliationService,
    PaymentReviewAction,
    RevisionConflictError,
    TyrionBillCheckRequestV2,
    TyrionBillMatchingClient,
    TyrionBillMatchIngestRequest,
    TyrionBillMatchResponseV2,
    TyrionInvalidResponseError,
    TyrionReceiptEvidenceIngestRequest,
)


@pytest.fixture()
def db(tmp_path):
    original = settings.database_url
    settings.database_url = f"sqlite:///{tmp_path / 'actions.db'}"
    init_db()
    session = get_session()
    try:
        yield session
    finally:
        session.close()
        settings.database_url = original


def _obligation(db, *, expected: int | None = 10_000, status: str = "open") -> Obligation:
    obligation = Obligation(
        id="obligation-1",
        status=status,
        expected_amount_minor=expected,
        currency="USD" if expected is not None else None,
    )
    db.add(obligation)
    db.commit()
    return obligation


def _pay_action(db, obligation: Obligation, *, action_id: int = 1) -> Action:
    action = Action(
        id=action_id,
        document_id=100 + action_id,
        document_title="Utility bill",
        action_type="PAY",
        title="Pay utility bill",
        amount=100.00,
        due_date=date(2026, 10, 20),
        document_date=date(2026, 10, 10),
        payee_or_owner="City Utilities",
        status="pending",
        action_ready=True,
        obligation_id=obligation.id,
    )
    db.add(action)
    db.commit()
    return action


def _snapshot(
    *,
    generation: str = "generation-1",
    source_as_of: datetime | None = None,
    evidence_ref: str = "evidence-reference-1",
    amount_minor: int = 10_000,
    payment_status: str = "paid",
    match_state: str = "matched",
    confidence: str | None = "high",
) -> PaymentEvidenceSnapshot:
    return PaymentEvidenceSnapshot.model_validate(
        {
            "contract_version": "1.0",
            "source_system": "tyrion_bill_match",
            "connector_ref": "opaque-connector",
            "source_generation": generation,
            "source_as_of": (source_as_of or datetime(2026, 10, 10, tzinfo=UTC)).isoformat(),
            "completeness": "partial",
            "obligation_id": "obligation-1",
            "evidence": [
                {
                    "evidence_ref": evidence_ref,
                    "transaction_ref": f"transaction-{evidence_ref}",
                    "kind": "posted_transaction",
                    "amount_minor": amount_minor,
                    "currency": "USD",
                    "evidence_date": "2026-10-10",
                    "source_state": "posted",
                    "match_state": match_state,
                    "payment_status": payment_status,
                    "confidence": confidence,
                    "reason_codes": ["amount_exact", "date_exact"],
                }
            ],
        }
    )


def _no_match_response(run_id: str, document_id: int) -> TyrionBillMatchResponseV2:
    return TyrionBillMatchResponseV2.model_validate(
        {
            "contractVersion": "2.0",
            "requestRef": run_id,
            "billRef": f"paperless:{document_id}",
            "matchStatus": "noMatch",
            "paymentStatus": "unmatched",
            "selectedTransactionRef": None,
            "candidates": [],
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


def _matched_response_with_alternatives(
    run_id: str,
    document_id: int,
) -> TyrionBillMatchResponseV2:
    def candidate(
        rank: int,
        transaction_ref: str,
        payee: str,
        score: int,
        score_gap: int | None,
    ) -> dict:
        return {
            "rank": rank,
            "transactionRef": transaction_ref,
            "transactionDate": "2026-10-18",
            "transactionState": "posted",
            "payee": payee,
            "amountMinor": 10_000,
            "currency": "USD",
            "accountDisplayName": "Household checking",
            "accountLastFour": None,
            "scoreBasisPoints": score,
            "confidence": "high" if rank == 1 else "medium",
            "scoreGapBasisPoints": score_gap,
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
                    "strength": "strong" if rank == 1 else "partial",
                    "contributionBasisPoints": 1400 if rank == 1 else 900,
                },
                {
                    "kind": "account",
                    "strength": "notProvided",
                    "contributionBasisPoints": 500,
                },
            ],
        }

    return TyrionBillMatchResponseV2.model_validate(
        {
            "contractVersion": "2.0",
            "requestRef": run_id,
            "billRef": f"paperless:{document_id}",
            "matchStatus": "matched",
            "paymentStatus": "paid",
            "selectedTransactionRef": "transaction-selected",
            "candidates": [
                candidate(1, "transaction-selected", "City Utilities", 9600, 900),
                candidate(2, "transaction-alternative", "City Services", 8700, None),
            ],
            "searchContext": {
                "anchorDate": "2026-10-20",
                "windowStart": "2026-10-11",
                "windowEnd": "2026-10-18",
                "dateWindowDays": 7,
                "amountToleranceMinor": 200,
                "sourceAsOf": "2026-10-18T12:00:00Z",
                "searchedAt": "2026-10-18T12:01:00Z",
                "eligibleAccountCount": 3,
                "candidatePoolSize": 18,
            },
        }
    )


def test_tyrion_v2_response_accepts_nullable_candidate_metadata() -> None:
    response = TyrionBillMatchResponseV2.model_validate(
        {
            "contractVersion": "2.0",
            "requestRef": "payment-check-response-contract",
            "billRef": "paperless:101",
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
                    "amountMinor": 10_000,
                    "currency": "USD",
                    "accountDisplayName": "Household checking",
                    "accountLastFour": None,
                    "scoreBasisPoints": 9600,
                    "confidence": "high",
                    "scoreGapBasisPoints": None,
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
                "windowStart": "2026-10-20",
                "windowEnd": "2026-10-20",
                "dateWindowDays": 0,
                "amountToleranceMinor": 100,
                "sourceAsOf": "2026-10-18T12:00:00Z",
                "searchedAt": "2026-10-18T12:01:00Z",
                "eligibleAccountCount": 3,
                "candidatePoolSize": 1,
            },
        }
    )

    assert response.candidates[0].account_last_four is None
    assert response.candidates[0].score_gap_basis_points is None
    assert response.search_context.date_window_days == 0


@pytest.mark.asyncio
async def test_tyrion_client_exchanges_the_published_v2_contract() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = request.read().decode()
        payload = json.loads(captured["payload"])
        return httpx.Response(
            200,
            json={
                "contractVersion": "2.0",
                "requestRef": payload["requestRef"],
                "billRef": payload["billRef"],
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
                        "amountMinor": 10_000,
                        "currency": "USD",
                        "accountDisplayName": "Household checking",
                        "accountLastFour": None,
                        "scoreBasisPoints": 9600,
                        "confidence": "high",
                        "scoreGapBasisPoints": None,
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
                    "windowStart": "2026-10-10",
                    "windowEnd": "2026-10-18",
                    "dateWindowDays": 30,
                    "amountToleranceMinor": 100,
                    "sourceAsOf": "2026-10-18T12:00:00Z",
                    "searchedAt": "2026-10-18T12:01:00Z",
                    "eligibleAccountCount": 3,
                    "candidatePoolSize": 1,
                },
            },
        )

    request = TyrionBillCheckRequestV2(
        requestRef="payment-check-client-contract",
        billRef="paperless:101",
        payee="City Utilities",
        expectedAmountMinor=10_000,
        currency="USD",
        dueDate=date(2026, 10, 20),
    )
    response = await TyrionBillMatchingClient(
        url="https://tyrion.test/api/connector/v1/bill-matches",
        api_token="bridge-token",
        transport=httpx.MockTransport(handler),
    ).check(request)

    payload = json.loads(captured["payload"])
    assert captured["authorization"] == "Bearer " + "bridge-token"
    assert payload == {
        "contractVersion": "2.0",
        "requestRef": "payment-check-client-contract",
        "billRef": "paperless:101",
        "payee": "City Utilities",
        "expectedAmountMinor": 10_000,
        "currency": "USD",
        "dueDate": "2026-10-20",
        "accountScope": "configuredEligible",
        "candidateLimit": 10,
    }
    assert response.candidates[0].account_last_four is None
    assert response.candidates[0].score_gap_basis_points is None


@pytest.mark.asyncio
async def test_tyrion_client_rejects_a_response_outside_the_v2_contract() -> None:
    request = TyrionBillCheckRequestV2(
        requestRef="payment-check-invalid-response",
        billRef="paperless:101",
        payee="City Utilities",
        expectedAmountMinor=10_000,
        currency="USD",
        dueDate=date(2026, 10, 20),
    )
    client = TyrionBillMatchingClient(
        url="https://tyrion.test/api/connector/v1/bill-matches",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"contractVersion": "2.0"})
        ),
    )

    with pytest.raises(TyrionInvalidResponseError, match="tyrion_response_invalid"):
        await client.check(request)


def test_tyrion_receipt_contract_is_exact_and_never_claims_payment_status() -> None:
    payload = {
        "obligation_id": "obligation-1",
        "connector_ref": "opaque-connector",
        "evidence": {
            "receiptContractVersion": "1.0",
            "intake": {
                "schema_version": "1.0",
                "intake_ref": "intake_opaque",
                "outcome": "new_canonical",
                "attempt_state": "accepted",
                "canonical_document_ref": "canonical_opaque",
                "review_ref": None,
                "reason_codes": [],
                "source_channel": "manual_upload",
                "source_occurrence_version": "1",
                "source_as_of": "2026-10-10T12:00:00Z",
                "retry_safe": True,
                "external_replica_eligible": True,
            },
            "replicaRef": "receipt-v1_opaque",
            "replicaLifecycle": "matched",
            "revision": 2,
            "nativeEvidence": {
                "receiptSource": "upload",
                "receiptState": "matched",
                "transactionRef": "transaction-v1_opaque",
                "attachmentCount": 1,
                "sourceAsOf": "2026-10-10T12:01:00Z",
                "observedAt": "2026-10-10T12:02:00Z",
            },
            "reviewRequired": False,
            "reasonCodes": [],
        },
    }
    request = TyrionReceiptEvidenceIngestRequest.model_validate(payload)
    normalized = request.to_snapshot().evidence[0]

    assert normalized.match_state == "matched"
    assert normalized.payment_status == "unknown"
    assert normalized.transaction_ref == "transaction-v1_opaque"

    payload["evidence"]["nativeEvidence"]["signedUrl"] = "https://forbidden.test"
    with pytest.raises(ValidationError):
        TyrionReceiptEvidenceIngestRequest.model_validate(payload)
    payload["evidence"]["nativeEvidence"].pop("signedUrl")
    payload["evidence"]["replicaRef"] = "constructor"
    with pytest.raises(ValidationError):
        TyrionReceiptEvidenceIngestRequest.model_validate(payload)


def test_bill_match_adapter_reuses_tyrion_decision_without_rescoring() -> None:
    request = TyrionBillMatchIngestRequest.model_validate(
        {
            "obligation_id": "obligation-1",
            "connector_ref": "opaque-connector",
            "source_generation": "bill-generation-1",
            "source_as_of": "2026-10-10T12:00:00Z",
            "canonical_document_ref": "canonical_opaque",
            "expected_amount_minor": 10_000,
            "currency": "USD",
            "response": {
                "contractVersion": "1.0",
                "billRef": "bill-opaque",
                "matchStatus": "matched",
                "paymentStatus": "paid",
                "selectedTransactionRef": "transaction-opaque",
                "candidates": [
                    {
                        "transactionRef": "transaction-opaque",
                        "transactionDate": "2026-10-09",
                        "transactionState": "posted",
                        "scoreBasisPoints": 9300,
                        "confidence": "high",
                        "signals": [
                            {
                                "kind": "amount",
                                "strength": "exact",
                                "contributionBasisPoints": 5500,
                            },
                            {
                                "kind": "date",
                                "strength": "exact",
                                "contributionBasisPoints": 2500,
                            },
                            {
                                "kind": "payee",
                                "strength": "strong",
                                "contributionBasisPoints": 1300,
                            },
                            {
                                "kind": "account",
                                "strength": "notProvided",
                                "contributionBasisPoints": 500,
                            },
                        ],
                    }
                ],
            },
        }
    )
    normalized = request.to_snapshot().evidence[0]

    assert normalized.payment_status == "paid"
    assert normalized.confidence == "high"
    assert normalized.reason_codes == [
        "amount_exact",
        "date_exact",
        "payee_strong",
        "account_notprovided",
    ]
    assert normalized.amount_minor == 10_000

    invalid = request.model_dump(by_alias=True, mode="json")
    invalid["response"]["selectedTransactionRef"] = "different-transaction"
    with pytest.raises(ValidationError):
        TyrionBillMatchIngestRequest.model_validate(invalid)
    invalid = request.model_dump(by_alias=True, mode="json")
    invalid["response"]["candidates"][0]["signals"][0]["contributionBasisPoints"] = 5501
    with pytest.raises(ValidationError):
        TyrionBillMatchIngestRequest.model_validate(invalid)
    invalid = request.model_dump(by_alias=True, mode="json")
    invalid["response"]["billRef"] = "prototype"
    with pytest.raises(ValidationError):
        TyrionBillMatchIngestRequest.model_validate(invalid)


def test_replay_freshness_and_generation_collision_are_fail_closed(db) -> None:
    _obligation(db)
    service = PaymentReconciliationService(db)
    current = _snapshot()

    applied = service.ingest_snapshot(current)
    replayed = service.ingest_snapshot(current)
    stale = service.ingest_snapshot(
        _snapshot(
            generation="older-generation",
            source_as_of=datetime(2026, 10, 1, tzinfo=UTC),
            amount_minor=8_000,
        )
    )

    assert applied.disposition == "applied"
    assert replayed.disposition == "replayed"
    assert stale.disposition == "stale"
    assert db.query(PaymentEvidence).one().amount_minor == 10_000
    assert db.query(PaymentReconciliationCase).filter_by(case_kind="stale").one().active

    collision = current.model_copy(deep=True)
    collision.evidence[0].amount_minor = 9_000
    with pytest.raises(ValueError, match="source_generation_payload_conflict"):
        service.ingest_snapshot(collision)


def test_posted_evidence_suggests_but_does_not_settle_obligation(db) -> None:
    obligation = _obligation(db)
    result = PaymentReconciliationService(db).ingest_snapshot(_snapshot())
    db.refresh(obligation)

    assert result.active_edge_count == 1
    assert obligation.status == "payment_detected"
    assert obligation.completion_suggested is True
    assert obligation.status != "settled"


def test_orphaned_check_expires_with_audit_and_allows_retry(db) -> None:
    obligation = _obligation(db)
    action = _pay_action(db, obligation)
    service = PaymentReconciliationService(db)
    first, _request, replay = service.start_check(
        action.id,
        PaymentCheckRequest(
            revision="v1:1:1",
            idempotency_key="payment-check-first",
        ),
    )
    first.updated_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    db.commit()

    projection = service.payment_projection(action.id)

    db.refresh(first)
    assert projection["state"] == "source_unavailable"
    assert first.status == "source_unavailable"
    assert first.error_code == "tyrion_check_expired"
    assert replay is False
    assert (
        db.query(PaymentRelationshipEvent)
        .filter_by(action_scope=f"payment_check:{first.id}", action="check_failed")
        .one()
        .new_state["error_code"]
        == "tyrion_check_expired"
    )

    retry, _request, replay = service.start_check(
        action.id,
        PaymentCheckRequest(
            revision=projection["revision"],
            idempotency_key="payment-check-retry",
        ),
    )

    assert retry.status == "checking"
    assert retry.id != first.id
    assert replay is False


def test_orphaned_check_uses_the_configured_connector_reference(db) -> None:
    obligation = _obligation(db)
    action = _pay_action(db, obligation)
    service = PaymentReconciliationService(
        db,
        bill_matching_connector_ref="custom-bill-matching",
    )
    run, _request, _replay = service.start_check(
        action.id,
        PaymentCheckRequest(
            revision="v1:1:1",
            idempotency_key="payment-check-custom-connector",
        ),
    )
    run.updated_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    db.commit()

    service.payment_projection(action.id)

    generation = (
        db.query(PaymentEvidenceGeneration)
        .filter_by(source_generation=f"{run.id}:source-unavailable")
        .one()
    )
    assert generation.connector_ref == "custom-bill-matching"


def test_orphaned_check_recovers_from_legacy_partial_failure_generation(db) -> None:
    obligation = _obligation(db, expected=None)
    action = _pay_action(db, obligation)
    service = PaymentReconciliationService(db)
    run, _request, _replay = service.start_check(
        action.id,
        PaymentCheckRequest(
            revision="v1:1:1",
            idempotency_key="payment-check-partial-failure",
        ),
    )
    service.ingest_snapshot(
        PaymentEvidenceSnapshot.model_validate(
            {
                "source_system": "tyrion_bill_match",
                "connector_ref": "tyrion-bill-matching-v2",
                "source_generation": run.id,
                "source_as_of": "2026-10-18T16:20:00Z",
                "completeness": "partial",
                "obligation_id": obligation.id,
                "evidence": [
                    {
                        "evidence_ref": f"{run.id}:source-unavailable",
                        "canonical_document_ref": f"paperless:{action.document_id}",
                        "kind": "bill",
                        "payee_hint": action.payee_or_owner,
                        "amount_minor": 10_000,
                        "currency": "USD",
                        "source_state": "source_unavailable",
                        "match_state": "source_unavailable",
                        "payment_status": "unknown",
                        "reason_codes": ["tyrion_check_expired"],
                    }
                ],
            }
        )
    )
    run = db.query(PaymentCheckRun).filter_by(id=run.id).one()
    run.updated_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    db.commit()

    projection = service.payment_projection(action.id)

    db.refresh(run)
    assert projection["state"] == "source_unavailable"
    assert run.status == "source_unavailable"
    assert run.error_code == "tyrion_check_expired"
    recovered_evidence = (
        db.query(PaymentEvidence).filter_by(evidence_ref=f"{run.id}:source-unavailable").one()
    )
    assert recovered_evidence.amount_minor == 10_000
    assert recovered_evidence.currency == "USD"
    generations = {
        row.source_generation
        for row in db.query(PaymentEvidenceGeneration).filter_by(obligation_id=obligation.id).all()
    }
    assert generations == {run.id, f"{run.id}:source-unavailable"}

    retry, _request, replay = service.start_check(
        action.id,
        PaymentCheckRequest(
            revision=projection["revision"],
            idempotency_key="payment-check-after-partial-failure",
        ),
    )
    assert retry.status == "checking"
    assert replay is False


def test_active_check_rejects_a_different_idempotency_key(db) -> None:
    obligation = _obligation(db)
    action = _pay_action(db, obligation)
    service = PaymentReconciliationService(db)
    service.start_check(
        action.id,
        PaymentCheckRequest(
            revision="v1:1:1",
            idempotency_key="payment-check-first",
        ),
    )

    with pytest.raises(PaymentCheckInProgressError, match="payment_check_in_progress"):
        service.start_check(
            action.id,
            PaymentCheckRequest(
                revision="v1:1:2",
                idempotency_key="payment-check-overlap",
            ),
        )

    assert db.query(PaymentCheckRun).filter_by(status="checking").count() == 1


def test_failure_status_is_committed_before_projection_build(db, monkeypatch) -> None:
    obligation = _obligation(db)
    action = _pay_action(db, obligation)
    service = PaymentReconciliationService(db)
    run, _request, _replay = service.start_check(
        action.id,
        PaymentCheckRequest(
            revision="v1:1:1",
            idempotency_key="payment-check-failure-order",
        ),
    )
    monkeypatch.setattr(
        service,
        "payment_projection",
        lambda _action_id: (_ for _ in ()).throw(RuntimeError("projection failed")),
    )

    with pytest.raises(RuntimeError, match="projection failed"):
        service.fail_check(
            run.id,
            connector_ref="tyrion-bill-matching-v2",
            error_code="tyrion_check_failed",
        )

    db.expire_all()
    stored = db.query(PaymentCheckRun).filter_by(id=run.id).one()
    assert stored.status == "source_unavailable"
    assert stored.error_code == "tyrion_check_failed"
    assert (
        db.query(PaymentRelationshipEvent)
        .filter_by(action_scope=f"payment_check:{run.id}", action="check_failed")
        .count()
        == 1
    )


def test_completion_status_is_committed_before_projection_build(db, monkeypatch) -> None:
    obligation = _obligation(db)
    action = _pay_action(db, obligation)
    service = PaymentReconciliationService(db)
    run, _request, _replay = service.start_check(
        action.id,
        PaymentCheckRequest(
            revision="v1:1:1",
            idempotency_key="payment-check-completion-order",
        ),
    )
    monkeypatch.setattr(
        service,
        "payment_projection",
        lambda _action_id: (_ for _ in ()).throw(RuntimeError("projection failed")),
    )

    with pytest.raises(RuntimeError, match="projection failed"):
        service.finish_check(
            run.id,
            _no_match_response(run.id, action.document_id),
            connector_ref="tyrion-bill-matching-v2",
        )

    db.expire_all()
    stored = db.query(PaymentCheckRun).filter_by(id=run.id).one()
    assert stored.status == "completed"
    assert stored.response_payload is not None


def test_projection_reports_ranked_alternatives_without_persisting_them_as_payments(db) -> None:
    obligation = _obligation(db)
    action = _pay_action(db, obligation)
    service = PaymentReconciliationService(db)
    run, _request, _replay = service.start_check(
        action.id,
        PaymentCheckRequest(
            revision="v1:1:1",
            idempotency_key="payment-check-ranked-alternatives",
        ),
    )

    projection = service.finish_check(
        run.id,
        _matched_response_with_alternatives(run.id, action.document_id),
        connector_ref="tyrion-bill-matching-v2",
    )

    assert projection["state"] == "posted_match"
    assert [candidate["payee"] for candidate in projection["candidates"]] == [
        "City Utilities",
        "City Services",
    ]
    assert projection["search_context"]["eligible_account_count"] == 3
    assert projection["search_context"]["candidate_pool_size"] == 18
    assert db.query(PaymentEvidence).count() == 1
    assert db.query(PaymentEvidence).one().transaction_ref == "transaction-selected"


def test_manual_rejection_survives_newer_generation(db) -> None:
    _obligation(db)
    service = PaymentReconciliationService(db)
    service.ingest_snapshot(
        _snapshot(match_state="ambiguous", payment_status="ambiguous", confidence=None)
    )
    case = db.query(PaymentReconciliationCase).filter_by(case_kind="ambiguous").one()
    evidence = db.query(PaymentEvidence).one()

    service.act(
        case.id,
        PaymentReviewAction(
            action="reject",
            expected_revision=case.revision,
            idempotency_key="reject-evidence-1",
            evidence_id=evidence.id,
        ),
        actor="user",
    )
    service.ingest_snapshot(
        _snapshot(
            generation="generation-2",
            source_as_of=datetime(2026, 10, 11, tzinfo=UTC),
        )
    )
    edge = db.query(ObligationPaymentEdge).one()

    assert edge.state == "rejected"
    assert edge.manual_precedence is True


def test_partial_many_payment_and_double_payment_review(db) -> None:
    obligation = _obligation(db, expected=10_000)
    service = PaymentReconciliationService(db)
    service.ingest_snapshot(
        _snapshot(generation="partial-1", evidence_ref="payment-one", amount_minor=4_000)
    )
    assert db.query(PaymentReconciliationCase).filter_by(case_kind="partial_payment").one().active
    service.ingest_snapshot(
        _snapshot(
            generation="partial-2",
            source_as_of=datetime(2026, 10, 11, tzinfo=UTC),
            evidence_ref="payment-two",
            amount_minor=6_000,
        )
    )
    partial = db.query(PaymentReconciliationCase).filter_by(case_kind="partial_payment").one()
    db.refresh(obligation)
    assert partial.active is False
    assert obligation.completion_suggested is True

    obligation.status = "settled"
    db.commit()
    service.ingest_snapshot(
        _snapshot(
            generation="extra-payment",
            source_as_of=datetime(2026, 10, 12, tzinfo=UTC),
            evidence_ref="payment-three",
            amount_minor=2_000,
        )
    )
    double = db.query(PaymentReconciliationCase).filter_by(case_kind="double_payment").one()
    db.refresh(obligation)
    assert double.active is True
    assert obligation.status == "settled"
    assert obligation.completion_suggested is False


def test_expected_revision_and_idempotency_are_authoritative(db) -> None:
    _obligation(db)
    service = PaymentReconciliationService(db)
    service.ingest_snapshot(
        _snapshot(match_state="ambiguous", payment_status="ambiguous", confidence=None)
    )
    case = db.query(PaymentReconciliationCase).filter_by(case_kind="ambiguous").one()
    evidence = db.query(PaymentEvidence).one()
    action = PaymentReviewAction(
        action="confirm",
        expected_revision=case.revision,
        idempotency_key="confirm-evidence-1",
        evidence_id=evidence.id,
        allocated_amount_minor=10_000,
    )

    first = service.act(case.id, action, actor="mission_control")
    replay = service.act(case.id, action, actor="mission_control")

    assert first["authoritative_read_back"] is True
    assert replay["idempotent"] is True
    with pytest.raises(IdempotencyConflictError):
        service.act(
            case.id,
            action.model_copy(update={"action": "reject"}),
            actor="mission_control",
        )
    with pytest.raises(RevisionConflictError):
        service.act(
            case.id,
            action.model_copy(
                update={
                    "idempotency_key": "new-action-key-1",
                    "expected_revision": 1,
                }
            ),
            actor="mission_control",
        )


def test_legacy_receipt_backfill_is_idempotent_and_preserves_exclusion(db) -> None:
    obligation = _obligation(db)
    db.add_all(
        [
            ObligationDocument(
                obligation_id=obligation.id,
                document_id=10,
                role="receipt",
                amount=40.0,
                confidence=0.9,
                source="receipt_match",
            ),
            ObligationDocument(
                obligation_id=obligation.id,
                document_id=11,
                role="receipt",
                amount=60.0,
                confidence=1.0,
                source="user_link",
                excluded=True,
            ),
        ]
    )
    db.commit()
    service = PaymentReconciliationService(db)

    assert service.backfill_legacy_receipts() == 2
    db.commit()
    assert service.backfill_legacy_receipts() == 2
    db.commit()

    assert db.query(PaymentEvidence).count() == 2
    edges = db.query(ObligationPaymentEdge).order_by(ObligationPaymentEdge.evidence_id).all()
    assert len(edges) == 2
    assert {edge.state for edge in edges} == {"active", "rejected"}
    rejected = next(edge for edge in edges if edge.state == "rejected")
    assert rejected.manual_precedence is True


def test_complete_snapshot_is_scoped_to_one_obligation(db) -> None:
    first = _obligation(db)
    second = Obligation(
        id="obligation-2",
        status="open",
        expected_amount_minor=5_000,
        currency="USD",
    )
    db.add(second)
    db.commit()
    service = PaymentReconciliationService(db)
    service.ingest_snapshot(_snapshot(evidence_ref="first-payment"))
    second_snapshot = _snapshot(
        generation="second-generation",
        source_as_of=datetime(2026, 10, 11, tzinfo=UTC),
        evidence_ref="second-payment",
        amount_minor=5_000,
    ).model_copy(update={"obligation_id": second.id})
    service.ingest_snapshot(second_snapshot)

    empty_first = PaymentEvidenceSnapshot(
        source_system="tyrion_bill_match",
        connector_ref="opaque-connector",
        source_generation="first-complete-empty",
        source_as_of=datetime(2026, 10, 12, tzinfo=UTC),
        completeness="complete",
        obligation_id=first.id,
        evidence=[],
    )
    service.ingest_snapshot(empty_first)

    first_edge = db.query(ObligationPaymentEdge).filter_by(obligation_id=first.id).one()
    second_edge = db.query(ObligationPaymentEdge).filter_by(obligation_id=second.id).one()
    assert first_edge.state == "removed"
    assert second_edge.state == "active"
    assert db.query(PaymentEvidence).filter_by(evidence_ref="second-payment").one().active


def test_newer_generation_refreshes_automatic_allocation(db) -> None:
    _obligation(db)
    service = PaymentReconciliationService(db)
    service.ingest_snapshot(_snapshot(amount_minor=4_000))
    service.ingest_snapshot(
        _snapshot(
            generation="generation-2",
            source_as_of=datetime(2026, 10, 11, tzinfo=UTC),
            amount_minor=10_000,
        )
    )

    edge = db.query(ObligationPaymentEdge).one()
    assert edge.allocated_amount_minor == 10_000


def test_reopen_uses_normalized_payment_state_without_legacy_receipt(db) -> None:
    obligation = _obligation(db)
    action = Action(
        document_id=42,
        document_title="Bill",
        action_type="PAY",
        title="Pay bill",
        status="completed",
        obligation_id=obligation.id,
    )
    db.add(action)
    db.commit()
    PaymentReconciliationService(db).ingest_snapshot(_snapshot())
    obligation.status = "settled"
    obligation.completion_suggested = False
    db.commit()

    action.status = "pending"
    sync_obligation_status(db, action)

    assert obligation.status == "payment_detected"
    assert obligation.completion_suggested is True


def test_one_evidence_item_cannot_auto_allocate_to_two_obligations(db) -> None:
    _obligation(db)
    second = Obligation(
        id="obligation-2",
        status="open",
        expected_amount_minor=10_000,
        currency="USD",
    )
    db.add(second)
    db.commit()
    service = PaymentReconciliationService(db)
    service.ingest_snapshot(_snapshot(evidence_ref="shared-payment"))
    second_snapshot = _snapshot(
        generation="second-obligation-generation",
        source_as_of=datetime(2026, 10, 11, tzinfo=UTC),
        evidence_ref="shared-payment",
    ).model_copy(update={"obligation_id": second.id})

    service.ingest_snapshot(second_snapshot)

    edges = (
        db.query(ObligationPaymentEdge)
        .filter(ObligationPaymentEdge.evidence_id == db.query(PaymentEvidence.id).scalar())
        .all()
    )
    assert [edge.state for edge in edges].count("active") == 1
    assert [edge.state for edge in edges].count("proposed") == 1
    assert (
        db.query(PaymentReconciliationCase)
        .filter_by(obligation_id=second.id, case_kind="conflict")
        .one()
        .active
    )
