"""OCR candidate generation/comparison/staging API router (issue #18, slice 1).

No endpoint in this router ever writes to Paperless — see
``doc_intelligence_hub.modules.ocr_quality.candidate_service`` for the
enforced invariant. Candidate generation is dispatched as a
``BackgroundTasks`` job, mirroring the existing Stage-1/2 corpus-scan
endpoints in ``ocr_quality.py``. Applying an accepted candidate as the new
Paperless version is a later slice (gated on issue #114) and has no endpoint
here.

Endpoints:
    POST /api/ocr-quality/candidates                    — Request candidate generation for a batch
    GET  /api/ocr-quality/candidates                     — List/filter candidates
    GET  /api/ocr-quality/candidates/{candidate_id}      — Candidate detail incl. comparison
    POST /api/ocr-quality/candidates/{candidate_id}/decision — Accept/reject (OWL-only, no Paperless write)
    POST /api/ocr-quality/candidates/{candidate_id}/cancel   — Best-effort cancel of a pending candidate
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field

from doc_intelligence_hub.api.routers import make_paperless_client
from doc_intelligence_hub.core.paperless import PaperlessClient
from doc_intelligence_hub.modules.ocr_quality.candidate_models import Decision, EngineName
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


class DecisionBody(BaseModel):
    decision: Decision
    reason: str | None = None
    actor: str = "system"


async def _run_candidate_generation(client: PaperlessClient, candidate_id: str) -> None:
    try:
        service = OcrCandidateService(client, get_ocr_quality_session)
        await service.run_generation_for_candidate(candidate_id)
    except Exception:
        logger.exception("Candidate generation %s failed unexpectedly", candidate_id)
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
        raise HTTPException(status_code=422, detail={"code": "batch_invalid", "message": str(exc)}) from exc
    except ValueError as exc:
        await client.aclose()
        raise HTTPException(status_code=400, detail={"code": "invalid_request", "message": str(exc)}) from exc

    for candidate_id in candidate_ids:
        # Each candidate gets its own PaperlessClient/background task so one
        # slow/failed generation cannot block or crash the others.
        background_tasks.add_task(
            _run_candidate_generation, make_paperless_client(request, timeout=60.0), candidate_id
        )
    await client.aclose()

    return {"candidate_ids": candidate_ids, "count": len(candidate_ids)}


@router.get("/candidates")
async def list_candidates(document_id: int | None = None, state: str | None = None) -> dict[str, Any]:
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
            status_code=404, detail={"code": "not_found", "message": f"Unknown candidate {candidate_id}"}
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
            status_code=404, detail={"code": "not_found", "message": f"Unknown candidate {candidate_id}"}
        )
    return text


@router.post("/candidates/{candidate_id}/decision")
async def decide_candidate(request: Request, candidate_id: str, body: DecisionBody) -> dict[str, Any]:
    """Accept or reject a READY candidate.

    This endpoint records the decision in OWL's own tables only. It never
    updates the live Paperless document — applying an accepted candidate as
    the new Paperless version is a later slice gated on issue #114. Accepting
    re-checks the document hasn't changed since comparison (a read-only GET)
    and fails if it has, per the design doc's freshness invariant.
    """
    client = make_paperless_client(request, timeout=30.0)
    service = _service(client)
    try:
        return await service.decide_candidate(
            candidate_id, decision=body.decision, reason=body.reason, actor=body.actor
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_decision", "message": str(exc)}) from exc
    finally:
        await client.aclose()


@router.post("/candidates/{candidate_id}/cancel")
async def cancel_candidate(candidate_id: str) -> dict[str, Any]:
    """Best-effort cancellation of a REQUESTED/RUNNING candidate."""
    init_ocr_quality_db()
    service = OcrCandidateService(None, get_ocr_quality_session)  # type: ignore[arg-type]
    try:
        return service.cancel_candidate(candidate_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_state", "message": str(exc)}) from exc
