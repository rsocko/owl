"""OCR quality baseline inventory API router (issues #25, #29, #30, #115).

The GET endpoints surface run status/progress, redacted aggregate reports,
and per-document review data for the OWL OCR Quality review UI. The POST
"trigger" endpoints below start Stage-1/Stage-2 background runs on the
already-running OWL API process (issue #30, Phase 7 — manual entry points
only; scheduled/event-driven triggers, candidate-run triggers, and n8n
integration are out of scope here). No endpoint in this router ever mutates
Paperless or returns raw OCR text — only aggregate or per-document
metadata/score fields already computed by the ``ocr_quality`` module's scan
pipeline.

Endpoints:
    GET  /api/ocr-quality/runs                   — List known runs (most recent first)
    GET  /api/ocr-quality/runs/{run_id}           — Single run status/progress
    GET  /api/ocr-quality/runs/{run_id}/report    — Redacted aggregate report for a run
    POST /api/ocr-quality/runs                    — Start a Stage-1 corpus scan (background)
    POST /api/ocr-quality/runs/{run_id}/resume    — Resume an interrupted Stage-1 run (background)
    POST /api/ocr-quality/runs/{run_id}/sample    — Start a Stage-2 stratified sample (background)
    GET  /api/ocr-quality/distribution            — Corpus-wide review-status/score snapshot
    GET  /api/ocr-quality/documents               — Filterable/paginated review queue
    GET  /api/ocr-quality/documents/{document_id} — Single document's full assessment detail

All three POST endpoints are fire-and-forget: they validate input, reject
duplicate concurrently-active runs, and return the ``run_id`` immediately.
The scan/sample itself continues in a FastAPI ``BackgroundTasks`` job — the
HTTP request is never held open for the full scan duration. Progress is then
visible via the GET endpoints above, which read the same SQLite tables the
CLI (``doc_intelligence_hub.modules.ocr_quality.cli``) writes to.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from doc_intelligence_hub.api.routers import make_paperless_client
from doc_intelligence_hub.core.paperless import PaperlessClient
from doc_intelligence_hub.modules.ocr_quality.config import settings as ocr_quality_settings
from doc_intelligence_hub.modules.ocr_quality.database import InventoryRun
from doc_intelligence_hub.modules.ocr_quality.database import get_session as get_ocr_quality_session
from doc_intelligence_hub.modules.ocr_quality.database import init_db as init_ocr_quality_db
from doc_intelligence_hub.modules.ocr_quality.models import RunStage, RunStatus
from doc_intelligence_hub.modules.ocr_quality.service import (
    OcrQualityInventoryService,
    _digest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ocr-quality", tags=["ocr-quality"])

# Run IDs currently scheduled as a ``BackgroundTasks`` job *in this process*.
# Used together with the DB ``status`` column for idempotency checks: a
# ``running`` row left behind by a crashed/restarted process is not in this
# set, so it's treated as resumable rather than a permanent block. This is a
# best-effort, single-process safeguard (matches the single long-lived OWL
# container deployment) — not a cross-process lock.
_active_run_ids: set[str] = set()


def _service() -> OcrQualityInventoryService:
    init_ocr_quality_db()
    return OcrQualityInventoryService(None, get_ocr_quality_session)  # type: ignore[arg-type]


def _run_to_dict(run: InventoryRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "stage": run.stage,
        "status": run.status,
        "counts": dict(run.counts or {}),
        "throughput_docs_per_second": run.throughput_docs_per_second,
        "seed": run.seed,
        "source_run_id": run.source_run_id,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.get("/runs")
async def list_runs(limit: int = 20) -> dict[str, Any]:
    """List the most recent OCR quality inventory runs (status only)."""
    init_ocr_quality_db()
    db = get_ocr_quality_session()
    try:
        runs = (
            db.query(InventoryRun)
            .order_by(InventoryRun.started_at.desc())
            .limit(max(1, min(limit, 200)))
            .all()
        )
        return {"runs": [_run_to_dict(r) for r in runs], "redacted": True}
    finally:
        db.close()


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    """Get status/progress for a single run."""
    init_ocr_quality_db()
    db = get_ocr_quality_session()
    try:
        run = db.query(InventoryRun).filter_by(run_id=run_id).one_or_none()
        if run is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": f"Unknown run {run_id}"},
            )
        return _run_to_dict(run)
    finally:
        db.close()


@router.get("/runs/{run_id}/report")
async def get_run_report(run_id: str) -> dict[str, Any]:
    """Get the redacted, aggregate-only report for a run."""
    init_ocr_quality_db()
    service = OcrQualityInventoryService(None, get_ocr_quality_session)  # type: ignore[arg-type]
    try:
        return service.build_aggregate_report(run_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": str(exc)}
        ) from exc


class StartRunRequest(BaseModel):
    """Stage-1 corpus scan trigger options, mirroring the CLI's ``run`` command."""

    batch_size: int | None = Field(default=None, ge=1, le=1000)
    tags: list[str] = Field(default_factory=list)
    correspondent: str | None = None


class ResumeRunRequest(BaseModel):
    """Stage-1 resume options.

    Must match the original run's scope/batch_size exactly — the service
    refuses to resume if scope, configuration, or the Paperless instance
    changed since the run started.
    """

    batch_size: int | None = Field(default=None, ge=1, le=1000)
    tags: list[str] = Field(default_factory=list)
    correspondent: str | None = None


class StartSampleRequest(BaseModel):
    """Stage-2 stratified sample trigger options, mirroring the CLI's ``sample`` command."""

    sample_size: int | None = Field(default=None, ge=1)
    seed: str | None = None
    min_per_stratum: int | None = Field(default=None, ge=0)
    max_pages: int | None = Field(default=None, ge=1)


async def _resolve_scope_params(
    client: PaperlessClient, *, tags: list[str], correspondent: str | None
) -> dict[str, Any]:
    """Resolve human-friendly tag/correspondent names into Paperless filter params.

    Mirrors ``ocr_quality.cli._resolve_scope_params`` so a manually-triggered
    run computes the exact same ``scope_digest`` a CLI-triggered run with the
    same options would.
    """
    scope_params: dict[str, Any] = {}
    if tags:
        all_tags = await client.list_tags()
        wanted = {t.lower() for t in tags}
        tag_ids = [str(t["id"]) for t in all_tags if str(t.get("name", "")).lower() in wanted]
        if tag_ids:
            scope_params["tags__id__in"] = ",".join(tag_ids)
    if correspondent:
        all_correspondents = await client.list_correspondents()
        match = next(
            (
                c
                for c in all_correspondents
                if str(c.get("name", "")).lower() == correspondent.lower()
            ),
            None,
        )
        if match:
            scope_params["correspondent__id"] = match["id"]
    return scope_params


def _find_active_run(
    db: Session, *, stage: str, scope_digest: str | None = None, source_run_id: str | None = None
) -> InventoryRun | None:
    """Find a run of the given stage that's both DB-``running`` and actively
    scheduled in this process (see ``_active_run_ids``).
    """
    query = db.query(InventoryRun).filter(
        InventoryRun.stage == stage, InventoryRun.status == RunStatus.RUNNING.value
    )
    if scope_digest is not None:
        query = query.filter(InventoryRun.scope_digest == scope_digest)
    if source_run_id is not None:
        query = query.filter(InventoryRun.source_run_id == source_run_id)
    for run in query.order_by(InventoryRun.started_at.desc()).all():
        if run.run_id in _active_run_ids:
            return run
    return None


def _mark_run_failed(run_id: str) -> None:
    db = get_ocr_quality_session()
    try:
        run = db.query(InventoryRun).filter_by(run_id=run_id).one_or_none()
        if run is not None and run.status == RunStatus.RUNNING.value:
            run.status = RunStatus.FAILED.value
            run.finished_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


async def _execute_corpus_scan(
    client: PaperlessClient,
    *,
    run_id: str,
    batch_size: int,
    resume: bool,
    scope_params: dict[str, Any] | None,
) -> None:
    _active_run_ids.add(run_id)
    try:
        service = OcrQualityInventoryService(client, get_ocr_quality_session)
        await service.run_corpus_scan(
            batch_size=batch_size,
            run_id=run_id,
            resume=resume,
            scope_params=scope_params or None,
        )
    except Exception:
        logger.exception("OCR quality corpus scan %s failed", run_id)
        _mark_run_failed(run_id)
    finally:
        _active_run_ids.discard(run_id)
        await client.aclose()


async def _execute_stratified_sample(
    client: PaperlessClient,
    *,
    source_run_id: str,
    run_id: str,
    sample_size: int,
    seed: str,
    min_per_stratum: int,
    pdf_profile_max_pages: int,
) -> None:
    _active_run_ids.add(run_id)
    try:
        service = OcrQualityInventoryService(client, get_ocr_quality_session)
        await service.run_stratified_sample(
            source_run_id=source_run_id,
            sample_size=sample_size,
            seed=seed,
            min_per_stratum=min_per_stratum,
            pdf_profile_max_pages=pdf_profile_max_pages,
            run_id=run_id,
        )
    except Exception:
        logger.exception("OCR quality stratified sample %s failed", run_id)
        _mark_run_failed(run_id)
    finally:
        _active_run_ids.discard(run_id)
        await client.aclose()


@router.post("/runs", status_code=202)
async def start_corpus_scan(
    request: Request, body: StartRunRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Start a Stage-1 full-corpus scan as a background task (issue #30).

    Returns immediately with the new ``run_id``; the scan continues after
    the response is sent. Refuses to start a duplicate scan for the same
    scope while one is already actively running — returns 409 with the
    existing ``run_id`` instead.
    """
    init_ocr_quality_db()
    client = make_paperless_client(request, timeout=30.0)
    scope_params = await _resolve_scope_params(
        client, tags=body.tags, correspondent=body.correspondent
    )
    scope_digest = _digest(scope_params or {})
    batch_size = body.batch_size or ocr_quality_settings.batch_size

    db = get_ocr_quality_session()
    try:
        existing = _find_active_run(
            db, stage=RunStage.STAGE_1_CORPUS_SCAN.value, scope_digest=scope_digest
        )
        if existing is not None:
            await client.aclose()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "run_already_in_progress",
                    "message": "A Stage-1 corpus scan for this scope is already running.",
                    "run_id": existing.run_id,
                },
            )
    finally:
        db.close()

    run_id = str(uuid4())
    background_tasks.add_task(
        _execute_corpus_scan,
        client,
        run_id=run_id,
        batch_size=batch_size,
        resume=False,
        scope_params=scope_params,
    )
    return {
        "run_id": run_id,
        "stage": RunStage.STAGE_1_CORPUS_SCAN.value,
        "status": RunStatus.RUNNING.value,
        "scheduled_at": datetime.utcnow().isoformat(),
    }


@router.post("/runs/{run_id}/resume", status_code=202)
async def resume_corpus_scan(
    request: Request, run_id: str, body: ResumeRunRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Resume an interrupted Stage-1 corpus scan as a background task (issue #30).

    The scope/batch_size options must match the original run — the
    underlying service refuses to resume otherwise. Returns 404 if the run
    is unknown, 409 if it's already actively running in this process.
    """
    init_ocr_quality_db()
    db = get_ocr_quality_session()
    try:
        run = db.query(InventoryRun).filter_by(run_id=run_id).one_or_none()
        if run is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": f"Unknown run {run_id}"},
            )
        if run_id in _active_run_ids:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "run_already_in_progress",
                    "message": f"Run {run_id} is already actively running.",
                    "run_id": run_id,
                },
            )
        if run.status == RunStatus.COMPLETED.value:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "run_already_completed",
                    "message": f"Run {run_id} already completed.",
                    "run_id": run_id,
                },
            )
    finally:
        db.close()

    client = make_paperless_client(request, timeout=30.0)
    scope_params = await _resolve_scope_params(
        client, tags=body.tags, correspondent=body.correspondent
    )
    batch_size = body.batch_size or ocr_quality_settings.batch_size

    background_tasks.add_task(
        _execute_corpus_scan,
        client,
        run_id=run_id,
        batch_size=batch_size,
        resume=True,
        scope_params=scope_params,
    )
    return {
        "run_id": run_id,
        "stage": RunStage.STAGE_1_CORPUS_SCAN.value,
        "status": RunStatus.RUNNING.value,
        "scheduled_at": datetime.utcnow().isoformat(),
    }


@router.post("/runs/{run_id}/sample", status_code=202)
async def start_stratified_sample(
    request: Request, run_id: str, body: StartSampleRequest, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Start a Stage-2 stratified sample against a Stage-1 run (issue #30).

    ``run_id`` is the source Stage-1 run. Returns immediately with the new
    Stage-2 ``run_id``; the sample continues after the response is sent.
    Refuses to start a duplicate sample for the same source run while one is
    already actively running — returns 409 with the existing ``run_id``.
    """
    init_ocr_quality_db()
    db = get_ocr_quality_session()
    try:
        source_run = db.query(InventoryRun).filter_by(run_id=run_id).one_or_none()
        if source_run is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": f"Unknown run {run_id}"},
            )
        existing = _find_active_run(
            db, stage=RunStage.STAGE_2_STRATIFIED_SAMPLE.value, source_run_id=run_id
        )
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "run_already_in_progress",
                    "message": "A Stage-2 sample for this source run is already running.",
                    "run_id": existing.run_id,
                },
            )
    finally:
        db.close()

    client = make_paperless_client(request, timeout=30.0)
    sample_run_id = str(uuid4())
    background_tasks.add_task(
        _execute_stratified_sample,
        client,
        source_run_id=run_id,
        run_id=sample_run_id,
        sample_size=body.sample_size or ocr_quality_settings.sample_target_size,
        seed=body.seed or ocr_quality_settings.sample_seed,
        min_per_stratum=(
            body.min_per_stratum
            if body.min_per_stratum is not None
            else ocr_quality_settings.sample_min_per_stratum
        ),
        pdf_profile_max_pages=body.max_pages or ocr_quality_settings.pdf_profile_max_pages,
    )
    return {
        "run_id": sample_run_id,
        "source_run_id": run_id,
        "stage": RunStage.STAGE_2_STRATIFIED_SAMPLE.value,
        "status": RunStatus.RUNNING.value,
        "scheduled_at": datetime.utcnow().isoformat(),
    }


@router.get("/distribution")
async def get_corpus_distribution() -> dict[str, Any]:
    """Privacy-safe corpus-wide review-status/score distribution for the dashboard.

    Computed across the most recent assessment per document (not a single
    run) so it reflects the current state of the corpus regardless of how
    many Stage-1/2 runs produced it.
    """
    return _service().build_corpus_distribution()


@router.get("/documents")
async def list_documents(
    review_status: str | None = None,
    document_type: str | None = None,
    correspondent: str | None = None,
    document_profile: str | None = None,
    downstream_outcome: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Filterable, paginated review queue over the latest per-document assessment.

    All filters are optional and combine with AND. Only metadata and score
    fields are returned — never raw OCR text.
    """
    return _service().list_document_assessments(
        review_status=review_status,
        document_type=document_type,
        correspondent=correspondent,
        document_profile=document_profile,
        downstream_outcome=downstream_outcome,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
        offset=offset,
    )


@router.get("/documents/{document_id}")
async def get_document(document_id: int) -> dict[str, Any]:
    """Full assessment detail for one document: scores, reasons, page profile."""
    detail = _service().get_document_assessment(document_id)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "not_found",
                "message": f"No OCR quality assessment for document {document_id}",
            },
        )
    return detail
