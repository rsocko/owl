"""Exact private Tyrion receipt-broker v1 client."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .models import GatewayResult, GatewayState, SourceDocumentReadError

_SUBMIT_PATH = "/api/internal/v1/finance/receipt-broker/replicas"
_MAX_DOCUMENT_BYTES = 2_097_152
_MEDIA_TYPES = frozenset({"application/pdf", "image/jpeg", "image/png"})
_REASON_CODE = re.compile(r"^[a-z0-9_]{1,64}$")


class NativeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receiptSource: Literal["upload", "email"]
    receiptState: Literal["processing", "awaiting_match", "matched", "failed"]
    transactionRef: str | None
    attachmentCount: int = Field(ge=0)
    sourceAsOf: str | None
    observedAt: str


class BrokerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brokerContractVersion: Literal["1.0"]
    idempotencyKey: str
    outcome: Literal["acknowledged", "processing", "duplicate", "retryable", "unknown"]
    acknowledged: bool
    retrySafe: bool
    reconcileRequired: bool
    replicaRef: str | None
    replicaLifecycle: str
    revision: int = Field(ge=0)
    nativeEvidence: NativeEvidence | None
    reasonCodes: list[str] = Field(default_factory=list, max_length=16)


class BrokerError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class BrokerErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: BrokerError


class TyrionReceiptBrokerClient:
    def __init__(
        self,
        *,
        authority: str,
        api_token: str,
        submit_timeout_seconds: float = 100.0,
        reconcile_timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if authority != "http://tyrion-operations-ui:3000":
            raise ValueError("tyrion_receipt_broker_authority_must_be_private")
        if not api_token:
            raise ValueError("tyrion_receipt_broker_token_required")
        self._submit_timeout = submit_timeout_seconds
        self._reconcile_timeout = reconcile_timeout_seconds
        self._client = httpx.AsyncClient(
            base_url=authority,
            headers={"Authorization": f"Bearer {api_token}"},
            transport=transport,
        )

    async def submit(
        self,
        *,
        idempotency_key: str,
        canonical_document_ref: str,
        source_ref: str,
        expected_revision: int | None,
        content: AsyncIterator[bytes],
        media_type: str,
    ) -> GatewayResult:
        if media_type not in _MEDIA_TYPES:
            return GatewayResult(
                state=GatewayState.REJECTED,
                reason_code="unsupported_document_media_type",
            )
        body = bytearray()
        try:
            async for chunk in content:
                body.extend(chunk)
                if len(body) > _MAX_DOCUMENT_BYTES:
                    return GatewayResult(
                        state=GatewayState.REJECTED,
                        reason_code="document_too_large",
                    )
        except httpx.HTTPError as exc:
            raise SourceDocumentReadError("source_document_read_failed") from exc
        headers = {
            "Content-Type": media_type,
            "Idempotency-Key": idempotency_key,
            "X-OWL-Canonical-Document-Ref": canonical_document_ref,
            "X-OWL-Source-Ref": source_ref,
        }
        if expected_revision is not None:
            headers["X-Tyrion-Expected-Revision"] = str(expected_revision)
        response = await self._client.post(
            _SUBMIT_PATH,
            headers=headers,
            content=bytes(body),
            timeout=self._submit_timeout,
        )
        return self._parse_response(response, idempotency_key=idempotency_key)

    async def reconcile(
        self,
        *,
        idempotency_key: str,
        expected_revision: int | None,
    ) -> GatewayResult:
        key = quote(idempotency_key, safe="")
        headers = {}
        if expected_revision is not None:
            headers["X-Tyrion-Expected-Revision"] = str(expected_revision)
        response = await self._client.post(
            f"{_SUBMIT_PATH}/{key}/reconcile",
            headers=headers,
            timeout=self._reconcile_timeout,
        )
        if response.status_code == 404:
            envelope = BrokerErrorEnvelope.model_validate(response.json())
            return GatewayResult(
                state=GatewayState.NOT_FOUND,
                reason_code=(
                    envelope.error.code
                    if _REASON_CODE.fullmatch(envelope.error.code)
                    else "reconcile_not_found"
                ),
            )
        return self._parse_response(response, idempotency_key=idempotency_key)

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _parse_response(
        response: httpx.Response,
        *,
        idempotency_key: str,
    ) -> GatewayResult:
        if response.status_code in {200, 201, 202}:
            payload = BrokerResponse.model_validate(response.json())
            if payload.idempotencyKey != idempotency_key:
                raise ValueError("tyrion_idempotency_key_mismatch")
            return GatewayResult(
                state=_gateway_state(payload),
                acknowledgement_ref=payload.replicaRef,
                reason_code=_bounded_reason(payload.reasonCodes),
                revision=payload.revision,
                acknowledged=payload.acknowledged,
                reconcile_required=payload.reconcileRequired,
            )

        envelope = BrokerErrorEnvelope.model_validate(response.json())
        reason_code = (
            envelope.error.code
            if _REASON_CODE.fullmatch(envelope.error.code)
            else "tyrion_request_rejected"
        )
        if response.status_code == 503:
            return GatewayResult(
                state=GatewayState.RETRYABLE,
                reason_code=reason_code,
            )
        if response.status_code in {401, 403, 409, 413, 415, 422}:
            return GatewayResult(
                state=GatewayState.REJECTED,
                reason_code=reason_code,
            )
        response.raise_for_status()
        raise AssertionError("unreachable")


def _gateway_state(payload: BrokerResponse) -> GatewayState:
    if payload.outcome == "unknown":
        return GatewayState.UNKNOWN
    if payload.outcome == "retryable":
        return GatewayState.RETRYABLE
    if payload.reconcileRequired or payload.outcome == "processing":
        return GatewayState.PROCESSING
    if payload.acknowledged:
        return GatewayState.ACKNOWLEDGED
    return GatewayState.RETRYABLE


def _bounded_reason(reason_codes: list[str]) -> str | None:
    for code in reason_codes:
        if _REASON_CODE.fullmatch(code):
            return code
    return None
