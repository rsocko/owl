"""Analysis invalidation / staleness API router (issue #114).

Manual/programmatic entry points for the analysis-invalidation mechanism.
There is no real production trigger yet — issue #18's "apply an accepted
OCR candidate" step (which will call the equivalent service methods
directly once built) does not exist. Until then, ``simulate-version-change``
below is the supported way to exercise this end-to-end, alongside the
bounded manual-invalidation endpoint for operators.

No endpoint here ever contains OCR text, document bodies, or raw metadata
values — only document/version identifiers, checksums, hashes, and reason
codes (see ``analysis_invalidation.database`` module docstring).

Endpoints:
    POST /api/analysis-invalidation/simulate-version-change  — simulate a version change
    POST /api/analysis-invalidation/invalidate                — bounded manual invalidation
    GET  /api/analysis-invalidation/documents/{document_id}    — per-module freshness status
    GET  /api/analysis-invalidation/events                     — recent invalidation events (redacted)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from doc_intelligence_hub.modules.analysis_invalidation.config import settings
from doc_intelligence_hub.modules.analysis_invalidation.database import init_db
from doc_intelligence_hub.modules.analysis_invalidation.models import InvalidationReason
from doc_intelligence_hub.modules.analysis_invalidation.scopes import (
    resolve_low_confidence_failed_document_ids,
)
from doc_intelligence_hub.modules.analysis_invalidation.service import AnalysisFreshnessService

router = APIRouter(prefix="/api/analysis-invalidation", tags=["analysis-invalidation"])


def _service() -> AnalysisFreshnessService:
    init_db()
    return AnalysisFreshnessService()


class SimulateVersionChangeRequest(BaseModel):
    """Simulate an accepted OCR version change for one document.

    ``metadata_fields`` should only contain fields a downstream module might
    depend on (e.g. title, correspondent, document_type, tags) — values are
    hashed, never stored raw.
    """

    document_id: int
    checksum: str = Field(min_length=1)
    metadata_fields: dict[str, Any] | None = None


class InvalidateRequest(BaseModel):
    """Bounded manual invalidation request.

    Exactly one of ``all``, ``scope``, or ``document_ids`` must be set.
    """

    all: bool = False
    scope: str | None = Field(default=None, pattern="^(low_confidence_failed)$")
    document_ids: list[int] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1)


@router.post("/simulate-version-change", status_code=201)
async def simulate_version_change(body: SimulateVersionChangeRequest) -> dict[str, Any]:
    """Simulate "this document's accepted OCR version changed" (issue #18 stand-in)."""
    service = _service()
    return service.simulate_version_change(
        document_id=body.document_id,
        new_checksum=body.checksum,
        metadata_fields=body.metadata_fields,
        triggered_by="simulated:api",
    )


@router.post("/invalidate", status_code=202)
async def invalidate(body: InvalidateRequest) -> dict[str, Any]:
    """Force invalidation for all known documents, a named scope, or specific IDs.

    Never bypasses the configured batch limit — ``limit`` can only tighten
    it, not loosen it.
    """
    chosen = [body.all, bool(body.scope), bool(body.document_ids)]
    if sum(bool(c) for c in chosen) != 1:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_scope",
                "message": "Specify exactly one of all=true, scope, or document_ids.",
            },
        )

    effective_limit = (
        min(body.limit, settings.max_manual_invalidation_batch)
        if body.limit
        else settings.max_manual_invalidation_batch
    )

    service = _service()
    if body.all:
        document_ids = service.list_known_document_ids(limit=effective_limit)
        reason = InvalidationReason.MANUAL_ALL
    elif body.scope == "low_confidence_failed":
        document_ids = resolve_low_confidence_failed_document_ids(limit=effective_limit)
        reason = InvalidationReason.MANUAL_SCOPE
    else:
        document_ids = body.document_ids[:effective_limit]
        reason = InvalidationReason.MANUAL_DOCUMENT

    if not document_ids:
        return {"invalidated_count": 0, "results": []}

    try:
        return service.manual_invalidate(
            document_ids=document_ids, reason=reason, triggered_by="manual:api"
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "batch_too_large", "message": str(exc)}
        ) from exc


@router.get("/documents/{document_id}")
async def get_document_status(document_id: int) -> dict[str, Any]:
    """Per-module freshness/status for one document (redacted, aggregate-only)."""
    return _service().get_document_status(document_id)


@router.get("/events")
async def list_events(
    document_id: int | None = None, limit: int = Query(50, ge=1, le=500)
) -> dict[str, Any]:
    """Recent invalidation events (redacted — ids/reasons/timestamps only)."""
    return {
        "events": _service().list_events(document_id=document_id, limit=limit),
        "redacted": True,
    }
