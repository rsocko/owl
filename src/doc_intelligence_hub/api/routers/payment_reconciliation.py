"""Protected OWL payment-reconciliation aggregate and Mission Control projection."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from doc_intelligence_hub.api.routers import (
    require_mission_control_auth,
    require_receipt_intake_auth,
)
from doc_intelligence_hub.modules.action_queue.database import get_session, init_db
from doc_intelligence_hub.modules.action_queue.payment_reconciliation import (
    IdempotencyConflictError,
    PaymentActionResult,
    PaymentCheckRequest,
    PaymentCheckResult,
    PaymentConfirmRequest,
    PaymentEvidenceSnapshot,
    PaymentReconciliationService,
    PaymentReviewAction,
    PaymentReviewItem,
    PaymentSnapshotResult,
    RevisionConflictError,
    TyrionBillMatchingClient,
    TyrionBillMatchIngestRequest,
    TyrionReceiptEvidenceIngestRequest,
)

router = APIRouter(tags=["payment-reconciliation"])
logger = logging.getLogger(__name__)


def _public_payment_projection(projection: dict) -> dict:
    return {key: value for key, value in projection.items() if not key.startswith("_")}


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


@router.get(
    "/api/payment-reconciliation/v2/actions/{action_id}",
)
async def get_action_payment_check(action_id: int) -> dict:
    init_db()
    db = get_session()
    try:
        try:
            projection = PaymentReconciliationService(db).payment_projection(action_id)
            return {"contract_version": "2.0", "payment_check": _public_payment_projection(projection)}
        except (KeyError, ValueError) as exc:
            _raise_service_error(exc)
    finally:
        db.close()


async def _check_payment(request: Request, action_id: int, body: PaymentCheckRequest) -> dict:
    init_db()
    db = get_session()
    try:
        service = PaymentReconciliationService(db)
        try:
            run, tyrion_request, replay = service.start_check(action_id, body)
            if replay:
                projection = service.payment_projection(action_id)
                return PaymentCheckResult(
                    run_id=run.id,
                    status=run.status,
                    idempotent=True,
                    payment=_public_payment_projection(projection),
                ).model_dump(mode="json")

            settings = request.app.state.hub_settings
            connector_ref = settings.tyrion_bill_matching_connector_ref
            if not settings.tyrion_bill_matching_url:
                projection = service.fail_check(
                    run.id,
                    connector_ref=connector_ref,
                    error_code="tyrion_not_configured",
                )
                return PaymentCheckResult(
                    run_id=run.id,
                    status="source_unavailable",
                    idempotent=False,
                    payment=_public_payment_projection(projection),
                ).model_dump(mode="json")
            token = (
                settings.tyrion_bill_matching_api_token.get_secret_value()
                if settings.tyrion_bill_matching_api_token
                else None
            )
            try:
                response = await TyrionBillMatchingClient(
                    url=settings.tyrion_bill_matching_url,
                    api_token=token,
                ).check(tyrion_request)
            except (httpx.HTTPError, ValidationError) as exc:
                logger.warning("Tyrion payment check failed for action %s: %s", action_id, exc)
                projection = service.fail_check(
                    run.id,
                    connector_ref=connector_ref,
                    error_code="tyrion_source_unavailable",
                )
                return PaymentCheckResult(
                    run_id=run.id,
                    status="source_unavailable",
                    idempotent=False,
                    payment=_public_payment_projection(projection),
                ).model_dump(mode="json")
            try:
                projection = service.finish_check(
                    run.id,
                    response,
                    connector_ref=connector_ref,
                )
            except ValueError as exc:
                if str(exc) not in {
                    "tyrion_request_ref_mismatch",
                    "tyrion_bill_ref_mismatch",
                }:
                    raise
                logger.warning(
                    "Tyrion payment check returned mismatched identifiers for action %s: %s",
                    action_id,
                    exc,
                )
                projection = service.fail_check(
                    run.id,
                    connector_ref=connector_ref,
                    error_code=str(exc),
                )
                return PaymentCheckResult(
                    run_id=run.id,
                    status="source_unavailable",
                    idempotent=False,
                    payment=_public_payment_projection(projection),
                ).model_dump(mode="json")
            return PaymentCheckResult(
                run_id=run.id,
                status="completed",
                idempotent=False,
                payment=_public_payment_projection(projection),
            ).model_dump(mode="json")
        except (KeyError, ValueError) as exc:
            _raise_service_error(exc)
    finally:
        db.close()


@router.post(
    "/api/payment-reconciliation/v2/actions/{action_id}/check-payment",
)
async def check_action_payment(
    request: Request,
    action_id: int,
    body: PaymentCheckRequest,
) -> dict:
    return await _check_payment(request, action_id, body)


@router.post(
    "/api/action-queue/actions/{action_id}/check-payment",
)
async def mc_check_action_payment(
    request: Request,
    action_id: int,
    body: PaymentCheckRequest,
) -> dict:
    from doc_intelligence_hub.api.routers.mc_connector import _serialize_mc_action
    from doc_intelligence_hub.modules.action_queue.database import Action

    result = await _check_payment(request, action_id, body)
    db = get_session()
    try:
        action = db.query(Action).filter_by(id=action_id).one()
        return {
            "action": _serialize_mc_action(request, db, action),
            "idempotent": result["idempotent"],
            "run_id": result["run_id"],
        }
    finally:
        db.close()


async def _confirm_payment(
    request: Request,
    action_id: int,
    body: PaymentConfirmRequest,
    *,
    actor: str,
) -> dict:
    from doc_intelligence_hub.api.routers.mc_connector import _serialize_mc_action
    from doc_intelligence_hub.modules.action_queue.database import Action

    init_db()
    db = get_session()
    try:
        try:
            service = PaymentReconciliationService(db)
            result = service.confirm_and_complete(action_id, body, actor=actor)
            action = db.query(Action).filter_by(id=action_id).one()
            return {
                "contract_version": "2.0",
                "action": _serialize_mc_action(request, db, action),
                "payment_check": _public_payment_projection(
                    service.payment_projection(action_id)
                ),
                "idempotent": result["idempotent"],
            }
        except (KeyError, ValueError) as exc:
            _raise_service_error(exc)
    finally:
        db.close()


@router.post(
    "/api/payment-reconciliation/v2/actions/{action_id}/confirm-payment-and-complete",
)
async def confirm_action_payment(
    request: Request,
    action_id: int,
    body: PaymentConfirmRequest,
) -> dict:
    return await _confirm_payment(request, action_id, body, actor="owl")


@router.post(
    "/api/action-queue/actions/{action_id}/confirm-payment-and-complete",
)
async def mc_confirm_action_payment(
    request: Request,
    action_id: int,
    body: PaymentConfirmRequest,
) -> dict:
    return await _confirm_payment(request, action_id, body, actor="mission_control")
