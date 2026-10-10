"""Protected OWL payment-reconciliation aggregate and Mission Control projection."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from doc_intelligence_hub.api.routers import (
    require_mission_control_auth,
    require_receipt_intake_auth,
)
from doc_intelligence_hub.modules.action_queue.database import get_session, init_db
from doc_intelligence_hub.modules.action_queue.payment_reconciliation import (
    IdempotencyConflictError,
    PaymentActionResult,
    PaymentEvidenceSnapshot,
    PaymentReconciliationService,
    PaymentReviewAction,
    PaymentReviewItem,
    PaymentSnapshotResult,
    RevisionConflictError,
    TyrionBillMatchIngestRequest,
    TyrionReceiptEvidenceIngestRequest,
)

router = APIRouter(tags=["payment-reconciliation"])


def _raise_service_error(exc: Exception) -> None:
    if isinstance(exc, KeyError):
        raise HTTPException(
            status_code=404,
            detail={"code": str(exc.args[0]), "message": "Payment reconciliation item not found."},
        ) from exc
    if isinstance(exc, RevisionConflictError):
        raise HTTPException(
            status_code=409,
            detail={
                "code": str(exc),
                "message": "The payment reconciliation aggregate changed; read it again.",
            },
        ) from exc
    if isinstance(exc, IdempotencyConflictError):
        raise HTTPException(
            status_code=409,
            detail={
                "code": str(exc),
                "message": "The idempotency key was already used for another action.",
            },
        ) from exc
    raise HTTPException(
        status_code=409,
        detail={"code": str(exc), "message": "Payment reconciliation request was rejected."},
    ) from exc


@router.post(
    "/api/payment-reconciliation/v1/evidence",
    response_model=PaymentSnapshotResult,
    dependencies=[Depends(require_receipt_intake_auth)],
)
async def ingest_payment_evidence(body: PaymentEvidenceSnapshot) -> PaymentSnapshotResult:
    """Ingest one normalized, byte-free evidence generation."""
    init_db()
    db = get_session()
    try:
        try:
            return PaymentReconciliationService(db).ingest_snapshot(body)
        except (KeyError, ValueError) as exc:
            _raise_service_error(exc)
    finally:
        db.close()


@router.post(
    "/api/payment-reconciliation/v1/tyrion-receipt-evidence",
    response_model=PaymentSnapshotResult,
    dependencies=[Depends(require_receipt_intake_auth)],
)
async def ingest_tyrion_receipt_evidence(
    body: TyrionReceiptEvidenceIngestRequest,
) -> PaymentSnapshotResult:
    """Adapt the exact Tyrion ReceiptEvidenceResponseV1 into OWL evidence."""
    return await ingest_payment_evidence(body.to_snapshot())


@router.post(
    "/api/payment-reconciliation/v1/tyrion-bill-match",
    response_model=PaymentSnapshotResult,
    dependencies=[Depends(require_receipt_intake_auth)],
)
async def ingest_tyrion_bill_match(
    body: TyrionBillMatchIngestRequest,
) -> PaymentSnapshotResult:
    """Adapt the exact Tyrion Bill Matching v1 result without rescoring it."""
    return await ingest_payment_evidence(body.to_snapshot())


@router.get(
    "/api/payment-reconciliation/v1/reviews",
    response_model=list[PaymentReviewItem],
    dependencies=[Depends(require_receipt_intake_auth)],
)
async def list_payment_reviews(
    active_only: bool = True,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[PaymentReviewItem]:
    init_db()
    db = get_session()
    try:
        rows = PaymentReconciliationService(db).list_cases(
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
        return [PaymentReviewItem.model_validate(row) for row in rows]
    finally:
        db.close()


@router.get(
    "/api/payment-reconciliation/v1/reviews/{case_id}",
    response_model=PaymentReviewItem,
    dependencies=[Depends(require_receipt_intake_auth)],
)
async def get_payment_review(case_id: str) -> PaymentReviewItem:
    init_db()
    db = get_session()
    try:
        try:
            return PaymentReviewItem.model_validate(
                PaymentReconciliationService(db).get_case(case_id)
            )
        except KeyError as exc:
            _raise_service_error(exc)
    finally:
        db.close()


@router.post(
    "/api/payment-reconciliation/v1/reviews/{case_id}/actions",
    response_model=PaymentActionResult,
    dependencies=[Depends(require_receipt_intake_auth)],
)
async def act_on_payment_review(
    case_id: str,
    body: PaymentReviewAction,
) -> PaymentActionResult:
    init_db()
    db = get_session()
    try:
        try:
            result = PaymentReconciliationService(db).act(case_id, body, actor="owl")
            return PaymentActionResult.model_validate(result)
        except (KeyError, ValueError) as exc:
            _raise_service_error(exc)
    finally:
        db.close()


@router.get(
    "/api/mc/v1/payment-reconciliation-reviews",
    response_model=list[PaymentReviewItem],
    dependencies=[Depends(require_mission_control_auth)],
)
async def mc_list_payment_reviews(
    active_only: bool = True,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[PaymentReviewItem]:
    return await list_payment_reviews(active_only=active_only, limit=limit, offset=offset)


@router.get(
    "/api/mc/v1/payment-reconciliation-reviews/{case_id}",
    response_model=PaymentReviewItem,
    dependencies=[Depends(require_mission_control_auth)],
)
async def mc_get_payment_review(case_id: str) -> PaymentReviewItem:
    return await get_payment_review(case_id)


@router.post(
    "/api/mc/v1/payment-reconciliation-reviews/{case_id}/actions",
    response_model=PaymentActionResult,
    dependencies=[Depends(require_mission_control_auth)],
)
async def mc_act_on_payment_review(
    case_id: str,
    body: PaymentReviewAction,
) -> PaymentActionResult:
    init_db()
    db = get_session()
    try:
        try:
            result = PaymentReconciliationService(db).act(case_id, body, actor="mission_control")
            return PaymentActionResult.model_validate(result)
        except (KeyError, ValueError) as exc:
            _raise_service_error(exc)
    finally:
        db.close()
