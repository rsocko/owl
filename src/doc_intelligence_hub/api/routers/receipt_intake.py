"""Protected, byte-streaming canonical receipt intake contract."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from doc_intelligence_hub.api.routers import (
    make_paperless_client,
    require_receipt_intake_auth,
)
from doc_intelligence_hub.modules.receipt_intake.models import (
    ExternalReplicaResult,
    ExternalReplicaUpdate,
    IntakeMetadata,
    IntakeResult,
    SourceChannel,
)
from doc_intelligence_hub.modules.receipt_intake.service import (
    IntakeValidationError,
    ReceiptIntakeService,
)

router = APIRouter(
    prefix="/api/receipt-intake/v1",
    tags=["receipt-intake"],
    dependencies=[Depends(require_receipt_intake_auth)],
)


def _service(request: Request) -> ReceiptIntakeService:
    return ReceiptIntakeService(make_paperless_client(request, timeout=30))


@router.post(
    "/occurrences",
    response_model=IntakeResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_occurrence(
    request: Request,
    source_channel: Annotated[SourceChannel, Header(alias="X-OWL-Source-Channel")],
    source_occurrence_id: Annotated[
        str, Header(alias="X-OWL-Source-Occurrence", pattern=r"^[a-f0-9]{64}$")
    ],
    source_occurrence_version: Annotated[
        str,
        Header(
            alias="X-OWL-Source-Occurrence-Version",
            pattern=r"^[a-z0-9][a-z0-9._-]{0,31}$",
        ),
    ] = "1",
    connector_ref: Annotated[str | None, Header(alias="X-OWL-Connector-Ref")] = None,
    source_observed_at: Annotated[datetime | None, Header(alias="X-OWL-Source-Observed-At")] = None,
    transform_version: Annotated[str | None, Header(alias="X-OWL-Transform-Version")] = None,
    normalized_content_fingerprint: Annotated[
        str | None,
        Header(alias="X-OWL-Normalized-Fingerprint", pattern=r"^[a-f0-9]{64}$"),
    ] = None,
    normalized_content_version: Annotated[
        str | None, Header(alias="X-OWL-Normalized-Version")
    ] = None,
    semantic_fingerprint: Annotated[
        str | None,
        Header(alias="X-OWL-Semantic-Fingerprint", pattern=r"^[a-f0-9]{64}$"),
    ] = None,
    semantic_fingerprint_version: Annotated[
        str | None, Header(alias="X-OWL-Semantic-Version")
    ] = None,
) -> IntakeResult:
    """Stream temporary bytes to Paperless and retain only bounded provenance."""
    media_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    metadata = IntakeMetadata(
        source_channel=source_channel,
        source_occurrence_version=source_occurrence_version,
        source_occurrence_id=source_occurrence_id,
        connector_ref=connector_ref,
        source_observed_at=source_observed_at,
        transform_version=transform_version,
        normalized_content_fingerprint=normalized_content_fingerprint,
        normalized_content_version=normalized_content_version,
        semantic_fingerprint=semantic_fingerprint,
        semantic_fingerprint_version=semantic_fingerprint_version,
    )
    try:
        return await _service(request).intake(metadata, request.stream(), media_type=media_type)
    except IntakeValidationError as exc:
        code = str(exc)
        status_code = (
            409
            if code in {"source_occurrence_hash_conflict", "source_occurrence_identity_conflict"}
            else 422
        )
        raise HTTPException(
            status_code=status_code,
            detail={
                "code": code,
                "message": "Receipt intake request did not satisfy the bounded contract.",
            },
        ) from exc


@router.get("/occurrences/{intake_ref}", response_model=IntakeResult)
async def get_intake_status(request: Request, intake_ref: str) -> IntakeResult:
    """Read a bounded result; raw source and Paperless identifiers are excluded."""
    try:
        return _service(request).get_status(intake_ref)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "intake_not_found", "message": "Receipt intake was not found."},
        ) from exc


@router.get("/lookup", response_model=IntakeResult)
async def lookup_intake(
    request: Request,
    source_occurrence_id: Annotated[
        str | None,
        Header(alias="X-OWL-Source-Occurrence", pattern=r"^[a-f0-9]{64}$"),
    ] = None,
    blob_sha256: Annotated[
        str | None,
        Header(alias="X-OWL-Blob-SHA256", pattern=r"^[a-f0-9]{64}$"),
    ] = None,
) -> IntakeResult:
    """Look up a bounded result by occurrence or exact hash before any retry."""
    try:
        result = _service(request).lookup(
            source_occurrence_id=source_occurrence_id,
            blob_sha256=blob_sha256,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_lookup", "message": str(exc)},
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "intake_not_found", "message": "Receipt intake was not found."},
        )
    return result


@router.post("/occurrences/{intake_ref}/reconcile", response_model=IntakeResult)
async def reconcile_unknown(request: Request, intake_ref: str) -> IntakeResult:
    """Query Paperless by exact hash after an unknown upload; never re-upload."""
    try:
        return await _service(request).reconcile_unknown(intake_ref)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "intake_not_found", "message": "Receipt intake was not found."},
        ) from exc


@router.post("/external-replicas", response_model=ExternalReplicaResult)
async def record_external_replica(
    request: Request,
    body: ExternalReplicaUpdate,
) -> ExternalReplicaResult:
    """Record Tyrion/Monarch lifecycle using OWL's independent replica revision."""
    try:
        return _service(request).record_external_replica(
            canonical_ref=body.canonical_document_ref,
            external_system=body.external_system,
            external_replica_ref=body.external_replica_ref,
            lifecycle_state=body.lifecycle_state,
            source_revision=str(body.source_revision),
            expected_revision=body.expected_revision,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "canonical_receipt_not_found",
                "message": "Canonical receipt was not found.",
            },
        ) from exc
    except ValueError as exc:
        code = str(exc)
        raise HTTPException(
            status_code=409,
            detail={
                "code": code,
                "message": "External replica revision or identity conflicted.",
            },
        ) from exc
