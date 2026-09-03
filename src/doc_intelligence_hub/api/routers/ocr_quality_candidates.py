"""OCR candidate generation/comparison/staging/apply API router (issue #18).

Slice 1 (generation/comparison/staging) never writes to Paperless. Slice 2
adds the apply/rollback path: accepting a candidate here only transitions it
to ``APPLYING`` and dispatches
``OcrCandidateApplicationService.apply_candidate`` as a background task
(mirroring the existing generation dispatch pattern below) — that service is
the only place in this package that ever writes to Paperless. Rejecting a
candidate remains OWL-only and synchronous.

Endpoints:
    POST /api/ocr-quality/candidates                    — Request candidate generation for a batch
    GET  /api/ocr-quality/candidates                     — List/filter candidates
    GET  /api/ocr-quality/candidates/{candidate_id}      — Candidate detail incl. comparison
    POST /api/ocr-quality/candidates/{candidate_id}/decision — Accept (dispatches apply) / reject (OWL-only)
    POST /api/ocr-quality/candidates/{candidate_id}/cancel   — Best-effort cancel of a pending candidate
    POST /api/ocr-quality/documents/{document_id}/rollback   — Roll back to a prior Paperless version
    POST /api/ocr-quality/candidates/{candidate_id}/retry-invalidation — Retry recording downstream
        invalidation for a candidate stuck in ACCEPTED_PENDING_INVALIDATION

    Region-level inspection for a candidate's own stored PDF (issue #134 x
    issue #18 — connects the region-inspection viewer to candidate
    comparison). Read-only; reuses ``region_inspection.py``'s existing
    word-flagging/page-rendering logic unchanged, sourced from the
    candidate's on-disk artifact instead of a Paperless fetch:
    GET  /api/ocr-quality/candidates/{candidate_id}/regions            — Word boxes + flags for one page
    GET  /api/ocr-quality/candidates/{candidate_id}/pages/{page}/image — Rendered page PNG
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator

from doc_intelligence_hub.api.routers import make_paperless_client
from doc_intelligence_hub.core.paperless import PaperlessClient
from doc_intelligence_hub.modules.ocr_quality import region_inspection
from doc_intelligence_hub.modules.ocr_quality.application_service import (
    OcrCandidateApplicationService,
)
from doc_intelligence_hub.modules.ocr_quality.candidate_models import (
    CandidateState,
    Decision,
    EngineName,
)
from doc_intelligence_hub.modules.ocr_quality.candidate_service import (
    BatchCapExceeded,
    OcrCandidateService,
    UnsupportedProvider,
)
from doc_intelligence_hub.modules.ocr_quality.database import get_session as get_ocr_quality_session
from doc_intelligence_hub.modules.ocr_quality.database import init_db as init_ocr_quality_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ocr-quality", tags=["ocr-quality-candidates"])


def _service(client: PaperlessClient) -> OcrCandidateService:
    init_ocr_quality_db()
    return OcrCandidateService(client, get_ocr_quality_session)


def _application_service(client: PaperlessClient) -> OcrCandidateApplicationService:
    init_ocr_quality_db()
    return OcrCandidateApplicationService(client, get_ocr_quality_session)


class RequestCandidatesBody(BaseModel):
    """Batch candidate-generation request. Caps are enforced server-side —
    there is no way to request more than the configured document/page caps,
    and there is no "accept all" action anywhere in this API.
    """

    document_ids: list[int] = Field(min_length=1)
    engines: list[EngineName] = Field(
        default_factory=lambda: [EngineName.OCRMYPDF_TESSERACT],
        description="One or more engines to generate independently. Outputs are never merged.",
    )
    settings: dict[str, Any] | None = Field(
        default=None, description="Provider-specific settings, e.g. {'language': 'eng'}."
    )
    actor: str = Field(default="system", description="Reviewer identity for audit purposes.")


def _validate_actor(value: str) -> str:
    """Shared validator: there is no auth system yet (issue #22, separate),
    so this is a lightweight "who did this" guard, not authentication —
    but it must not be blank, or every audit-trail row silently defaults
    to an uninformative value.
    """
    if not value or not value.strip():
        raise ValueError("actor is required and must not be blank")
    return value.strip()


class DecisionBody(BaseModel):
    decision: Decision
    reason: str | None = None
    actor: str = Field(description="Reviewer identity for audit purposes; required.")

    _validate_actor = field_validator("actor")(_validate_actor)


class RollbackBody(BaseModel):
    target_candidate_id: str | None = Field(
        default=None,
        description=(
            "A previously-accepted candidate's applied version to restore. If omitted, "
            "rolls back to whatever version was current immediately before the most "
            "recently accepted candidate for this document (or the Paperless "
            "root/original if there is no prior accepted candidate)."
        ),
    )
    actor: str = Field(description="Reviewer identity for audit purposes; required.")

    _validate_actor = field_validator("actor")(_validate_actor)


class RetryInvalidationBody(BaseModel):
    actor: str = Field(description="Reviewer identity for audit purposes; required.")

    _validate_actor = field_validator("actor")(_validate_actor)


async def _run_candidate_generation(client: PaperlessClient, candidate_id: str) -> None:
    try:
        service = OcrCandidateService(client, get_ocr_quality_session)
        await service.run_generation_for_candidate(candidate_id)
    except Exception:
        logger.exception("Candidate generation %s failed unexpectedly", candidate_id)
    finally:
        await client.aclose()


async def _run_candidate_apply(client: PaperlessClient, candidate_id: str, actor: str) -> None:
    try:
        service = _application_service(client)
        await service.apply_candidate(candidate_id, actor=actor)
    except Exception:
        logger.exception("Candidate apply %s failed unexpectedly", candidate_id)
    finally:
        await client.aclose()


@router.post("/candidates", status_code=202)
async def request_candidates(
    request: Request, body: RequestCandidatesBody, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Request candidate generation for an explicit, capped document batch.

    Returns immediately with the created ``candidate_id``s in ``REQUESTED``
    state; generation continues as background tasks. No Paperless document is
    modified by this call or by the generation it schedules.
    """
    client = make_paperless_client(request, timeout=60.0)
    service = _service(client)
    try:
        candidate_ids = await service.request_candidates(
            document_ids=body.document_ids,
            engines=[e.value for e in body.engines],
            provider_settings=body.settings,
            actor=body.actor,
        )
    except (BatchCapExceeded, UnsupportedProvider) as exc:
        await client.aclose()
        raise HTTPException(
            status_code=422, detail={"code": "batch_invalid", "message": str(exc)}
        ) from exc
    except ValueError as exc:
        await client.aclose()
        raise HTTPException(
            status_code=400, detail={"code": "invalid_request", "message": str(exc)}
        ) from exc

    for candidate_id in candidate_ids:
        # Each candidate gets its own PaperlessClient/background task so one
        # slow/failed generation cannot block or crash the others.
        background_tasks.add_task(
            _run_candidate_generation, make_paperless_client(request, timeout=60.0), candidate_id
        )
    await client.aclose()

    return {"candidate_ids": candidate_ids, "count": len(candidate_ids)}


@router.get("/candidates")
async def list_candidates(
    document_id: int | None = None, state: str | None = None
) -> dict[str, Any]:
    """List candidates, optionally filtered by document or state."""
    init_ocr_quality_db()
    service = OcrCandidateService(None, get_ocr_quality_session)  # type: ignore[arg-type]
    return service.list_candidates(document_id=document_id, state=state)


@router.get("/candidates/{candidate_id}")
async def get_candidate(candidate_id: str) -> dict[str, Any]:
    """Full candidate detail, including its comparison result if READY+."""
    init_ocr_quality_db()
    service = OcrCandidateService(None, get_ocr_quality_session)  # type: ignore[arg-type]
    detail = service.get_candidate(candidate_id)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": f"Unknown candidate {candidate_id}"},
        )
    return detail


@router.get("/candidates/{candidate_id}/text")
async def get_candidate_text(request: Request, candidate_id: str) -> dict[str, Any]:
    """Side-by-side extracted text for a candidate: current (live Paperless
    OCR text, fetched read-only) vs. candidate (from its own artifact).

    Read-only in every sense — no Paperless write, and does not itself
    change the candidate's state or decision.
    """
    client = make_paperless_client(request, timeout=30.0)
    service = _service(client)
    try:
        text = await service.get_candidate_text(candidate_id)
    finally:
        await client.aclose()
    if text is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": f"Unknown candidate {candidate_id}"},
        )
    return text


@router.post("/candidates/{candidate_id}/decision")
async def decide_candidate(
    request: Request, candidate_id: str, body: DecisionBody, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Accept or reject a READY candidate.

    Rejecting only records the decision in OWL's own tables — it never
    touches Paperless. Accepting re-checks the document hasn't changed since
    comparison (a read-only GET) and fails if it has, per the design doc's
    freshness invariant; on success the candidate moves to ``APPLYING`` and
    this endpoint returns immediately — the actual Paperless write happens
    in a background task (``OcrCandidateApplicationService.apply_candidate``).
    Poll ``GET /candidates/{candidate_id}`` for the outcome (``ACCEPTED``
    with ``applied_paperless_version_id`` set, or back to ``READY``/``FAILED``
    with ``apply_last_error`` on failure).
    """
    client = make_paperless_client(request, timeout=30.0)
    service = _service(client)
    try:
        result = await service.decide_candidate(
            candidate_id, decision=body.decision, reason=body.reason, actor=body.actor
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "invalid_decision", "message": str(exc)}
        ) from exc
    finally:
        await client.aclose()

    if result.get("state") == CandidateState.APPLYING.value:
        background_tasks.add_task(
            _run_candidate_apply,
            make_paperless_client(request, timeout=60.0),
            candidate_id,
            body.actor,
        )
    return result


@router.post("/documents/{document_id}/rollback")
async def rollback_document(
    request: Request, document_id: int, body: RollbackBody
) -> dict[str, Any]:
    """Roll back a document to a prior Paperless version.

    Synchronous — a rollback is a small, bounded number of ``DELETE`` calls
    against Paperless's document-version API, not a re-OCR cycle. Restores
    both the Paperless version and durably re-records downstream
    invalidation (issue #114) for the restored checksum.
    """
    client = make_paperless_client(request, timeout=60.0)
    service = _application_service(client)
    try:
        result = await service.rollback(
            document_id, actor=body.actor, target_candidate_id=body.target_candidate_id
        )
    finally:
        await client.aclose()
    if result.get("error"):
        raise HTTPException(
            status_code=400, detail={"code": "rollback_failed", "message": result["error"]}
        )
    return result


@router.post("/candidates/{candidate_id}/retry-invalidation")
async def retry_invalidation(
    request: Request, candidate_id: str, body: RetryInvalidationBody
) -> dict[str, Any]:
    """Retry recording downstream invalidation (issue #114) for a candidate
    stuck in ``ACCEPTED_PENDING_INVALIDATION``.

    The Paperless version this candidate applied was already durably
    written and is never touched here — this only retries the bookkeeping
    step that failed. On success the candidate becomes ``ACCEPTED``.
    """
    client = make_paperless_client(request, timeout=30.0)
    service = _application_service(client)
    try:
        result = await service.retry_invalidation(candidate_id, actor=body.actor)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "invalid_state", "message": str(exc)}
        ) from exc
    finally:
        await client.aclose()
    if result.get("error"):
        raise HTTPException(
            status_code=400, detail={"code": "retry_failed", "message": result["error"]}
        )
    return result


@router.post("/candidates/{candidate_id}/cancel")
async def cancel_candidate(candidate_id: str) -> dict[str, Any]:
    """Best-effort cancellation of a REQUESTED/RUNNING candidate."""
    init_ocr_quality_db()
    service = OcrCandidateService(None, get_ocr_quality_session)  # type: ignore[arg-type]
    try:
        return service.cancel_candidate(candidate_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "invalid_state", "message": str(exc)}
        ) from exc


# ---------------------------------------------------------------------------
# Region-level inspection for a candidate's stored PDF (issue #134 x #18) —
# read-only, on-demand, reusing region_inspection.py's flagging/rendering
# logic unchanged; only the PDF byte source differs from the live-document
# endpoints in ocr_quality.py.
# ---------------------------------------------------------------------------


def _candidate_service() -> OcrCandidateService:
    init_ocr_quality_db()
    return OcrCandidateService(None, get_ocr_quality_session)  # type: ignore[arg-type]


def _require_candidate_pdf_bytes(candidate_id: str) -> bytes:
    service = _candidate_service()
    detail = service.get_candidate(candidate_id)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": f"Unknown candidate {candidate_id}"},
        )
    pdf_bytes = service.get_candidate_pdf_bytes(candidate_id)
    if pdf_bytes is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "no_pdf_artifact",
                "message": f"Candidate {candidate_id} has no stored PDF artifact yet "
                f"(state: {detail.get('state')}).",
            },
        )
    return pdf_bytes


@router.get("/candidates/{candidate_id}/regions")
async def get_candidate_regions(candidate_id: str, page: int = Query(1, ge=1)) -> dict[str, Any]:
    """Word-level geometry + heuristic flags for one page of a candidate's PDF.

    Mirrors ``GET /documents/{document_id}/regions`` (issue #134, Part 1)
    but sources bytes from the candidate's own on-disk artifact instead of
    a Paperless fetch. No document-level scorer ``reasons`` cross-reference
    is available for candidates in this slice, so ``matched_reasons`` is
    always empty here.
    """
    pdf_bytes = _require_candidate_pdf_bytes(candidate_id)
    regions = region_inspection.build_page_regions(pdf_bytes=pdf_bytes, page_number=page)
    if regions is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "page_not_found",
                "message": f"Candidate {candidate_id} has no page {page} with parseable geometry.",
            },
        )
    return regions


@router.get("/candidates/{candidate_id}/pages/{page}/image")
async def get_candidate_page_image(
    candidate_id: str,
    page: int,
    dpi: int = Query(
        region_inspection.DEFAULT_PAGE_IMAGE_DPI,
        ge=region_inspection.MIN_PAGE_IMAGE_DPI,
        le=region_inspection.MAX_PAGE_IMAGE_DPI,
    ),
) -> Response:
    """Render one page of a candidate's PDF as a PNG image, for overlay display."""
    if page < 1:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_page", "message": "page must be >= 1"}
        )
    pdf_bytes = _require_candidate_pdf_bytes(candidate_id)
    rendered = region_inspection.render_page_image(pdf_bytes=pdf_bytes, page_number=page, dpi=dpi)
    if rendered is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "page_not_found",
                "message": f"Candidate {candidate_id} has no renderable page {page}.",
            },
        )
    png_bytes, _width, _height = rendered
    return Response(content=png_bytes, media_type="image/png")
