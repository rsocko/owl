"""Document-level Paperless delivery to Monarch through private Tyrion."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from doc_intelligence_hub.api.routers import make_paperless_client, raise_api_error
from doc_intelligence_hub.core.resilience import PaperlessError
from doc_intelligence_hub.modules.monarch_delivery.client import (
    TyrionReceiptBrokerClient,
)
from doc_intelligence_hub.modules.monarch_delivery.config import settings
from doc_intelligence_hub.modules.monarch_delivery.models import MonarchDeliveryResult
from doc_intelligence_hub.modules.monarch_delivery.service import (
    DeliveryOutcomeUnknownError,
    MonarchDeliveryService,
)
from doc_intelligence_hub.modules.receipt_intake.service import (
    is_monarch_recovery_document,
)

router = APIRouter(prefix="/api/documents", tags=["monarch-delivery"])


class ConfirmedDeliveryRequest(BaseModel):
    confirmed: Literal[True]


class _UnavailableGateway:
    async def submit(self, **kwargs):
        raise RuntimeError("monarch_delivery_not_configured")

    async def reconcile(self, **kwargs):
        raise RuntimeError("monarch_delivery_not_configured")


def _identity_secret() -> str:
    if settings.identity_secret is None:
        raise_api_error(
            503,
            "monarch_delivery_identity_not_configured",
            "Configure OWL_MONARCH_DELIVERY_IDENTITY_SECRET before using Monarch delivery.",
        )
    return settings.identity_secret.get_secret_value()


def _gateway(*, required: bool) -> TyrionReceiptBrokerClient | _UnavailableGateway:
    if settings.tyrion_api_token is None:
        if required:
            raise_api_error(
                503,
                "monarch_delivery_auth_not_configured",
                "Configure OWL_MONARCH_DELIVERY_TYRION_API_TOKEN before sending.",
            )
        return _UnavailableGateway()
    return TyrionReceiptBrokerClient(
        authority=settings.tyrion_authority,
        api_token=settings.tyrion_api_token.get_secret_value(),
        submit_timeout_seconds=settings.submit_timeout_seconds,
        reconcile_timeout_seconds=settings.reconcile_timeout_seconds,
    )


def _service(*, require_gateway: bool) -> tuple[MonarchDeliveryService, object]:
    gateway = _gateway(required=require_gateway)
    service = MonarchDeliveryService(
        gateway,
        deployment_id=settings.deployment_id,
        identity_secret=_identity_secret(),
        write_enabled=settings.write_enabled,
    )
    return service, gateway


async def _close_gateway(gateway: object) -> None:
    close = getattr(gateway, "aclose", None)
    if close is not None:
        await close()


def _content_factory(
    paperless: object,
    document_id: int,
) -> Callable[[], AbstractAsyncContextManager[tuple[AsyncIterator[bytes], str]]]:
    return lambda: paperless.stream_document_download(document_id)


def _tag_hook(paperless: object, document_id: int):
    tag = (settings.sent_tag or "").strip()
    if not tag:
        return None

    async def apply_tag() -> None:
        await paperless.add_tags_to_document(document_id, [tag])

    return apply_tag


@router.get("/{document_id}/monarch-delivery", response_model=MonarchDeliveryResult)
async def get_monarch_delivery(document_id: int) -> MonarchDeliveryResult:
    service, gateway = _service(require_gateway=False)
    try:
        return service.status(document_id)
    finally:
        await _close_gateway(gateway)


@router.post("/{document_id}/monarch-delivery", response_model=MonarchDeliveryResult)
async def send_to_monarch(
    document_id: int,
    body: ConfirmedDeliveryRequest,
    request: Request,
    response: Response,
) -> MonarchDeliveryResult:
    del body
    if not settings.write_enabled:
        raise_api_error(
            503,
            "monarch_delivery_write_disabled",
            "Monarch delivery is disabled by the OWL write gate.",
        )
    service, gateway = _service(require_gateway=True)
    paperless = make_paperless_client(request, timeout=settings.submit_timeout_seconds)
    try:
        await paperless.get_document(document_id)
        result = await service.send(
            document_id,
            content_factory=_content_factory(paperless, document_id),
            recovery_origin=is_monarch_recovery_document(document_id),
            on_acknowledged=_tag_hook(paperless, document_id),
        )
        if result.state in {"submitting", "unknown", "processing"}:
            response.status_code = 202
        return result
    except DeliveryOutcomeUnknownError:
        response.status_code = 202
        return service.status(document_id)
    except PermissionError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": str(exc), "message": "Monarch delivery is disabled."},
        ) from exc
    except (httpx.HTTPError, PaperlessError) as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "paperless_document_unavailable",
                "message": "The canonical Paperless document could not be read.",
            },
        ) from exc
    finally:
        await paperless.aclose()
        await _close_gateway(gateway)


@router.post(
    "/{document_id}/monarch-delivery/reconcile",
    response_model=MonarchDeliveryResult,
)
async def reconcile_monarch_delivery(
    document_id: int,
    request: Request,
    response: Response,
) -> MonarchDeliveryResult:
    service, gateway = _service(require_gateway=True)
    paperless = make_paperless_client(request, timeout=settings.reconcile_timeout_seconds)
    try:
        result = await service.reconcile(
            document_id,
            on_acknowledged=_tag_hook(paperless, document_id),
        )
        if result.state in {"unknown", "processing"}:
            response.status_code = 202
        return result
    finally:
        await paperless.aclose()
        await _close_gateway(gateway)


@router.post(
    "/{document_id}/monarch-delivery/retry",
    response_model=MonarchDeliveryResult,
)
async def retry_monarch_delivery(
    document_id: int,
    body: ConfirmedDeliveryRequest,
    request: Request,
    response: Response,
) -> MonarchDeliveryResult:
    del body
    if not settings.write_enabled:
        raise_api_error(
            503,
            "monarch_delivery_write_disabled",
            "Monarch delivery is disabled by the OWL write gate.",
        )
    service, gateway = _service(require_gateway=True)
    paperless = make_paperless_client(request, timeout=settings.submit_timeout_seconds)
    try:
        await paperless.get_document(document_id)
        if is_monarch_recovery_document(document_id):
            return await service.send(
                document_id,
                content_factory=_content_factory(paperless, document_id),
                recovery_origin=True,
            )
        result = await service.retry(
            document_id,
            content_factory=_content_factory(paperless, document_id),
            on_acknowledged=_tag_hook(paperless, document_id),
        )
        if result.state in {"submitting", "unknown", "processing"}:
            response.status_code = 202
        return result
    except (httpx.HTTPError, PaperlessError) as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "paperless_document_unavailable",
                "message": "The canonical Paperless document could not be read.",
            },
        ) from exc
    finally:
        await paperless.aclose()
        await _close_gateway(gateway)
