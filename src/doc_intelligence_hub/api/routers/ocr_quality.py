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
    POST /api/ocr-quality/runs/{run_id}/cancel    — Request cooperative cancellation of a run
    GET  /api/ocr-quality/distribution            — Corpus-wide review-status/score snapshot
    GET  /api/ocr-quality/documents               — Filterable/paginated/sortable review queue
    GET  /api/ocr-quality/documents/{document_id} — Single document's full assessment detail
    POST /api/ocr-quality/documents/{document_id}/stage2 — Force Stage-2 for one document (inline)
    GET  /api/ocr-quality/downstream-outcomes     — Distinct downstream_outcome values (filter dropdown)

    Region-level inspection (issue #134, Part 1 — read-only, on-demand):
    GET  /api/ocr-quality/documents/{document_id}/regions            — Word boxes + flags for one page
    GET  /api/ocr-quality/documents/{document_id}/pages/{page}/image — Rendered page PNG

    Box-level diffing (connects issue #134's region inspection with issue
    #18's candidate comparison — read-only, stateless):
    POST /api/ocr-quality/regions/diff — Diff two word-box lists for the same page

    Manual annotations (issue #134, Part 2 — the only mutation endpoints
    added by this feature; they mutate OWL's own local annotation table
    only, never Paperless or the OCR quality assessment tables):
    GET    /api/ocr-quality/documents/{document_id}/annotations                   — List annotations
    POST   /api/ocr-quality/documents/{document_id}/annotations                   — Create an annotation
    PATCH  /api/ocr-quality/documents/{document_id}/annotations/{annotation_id}   — Edit an annotation
    DELETE /api/ocr-quality/documents/{document_id}/annotations/{annotation_id}   — Delete an annotation

All three POST endpoints are fire-and-forget: they validate input, reject
duplicate concurrently-active runs, and return the ``run_id`` immediately.
The scan/sample itself continues in a FastAPI ``BackgroundTasks`` job — the
HTTP request is never held open for the full scan duration. Progress is then
visible via the GET endpoints above, which read the same SQLite tables the
CLI (``doc_intelligence_hub.modules.ocr_quality.cli``) writes to.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from doc_intelligence_hub.api.routers import make_paperless_client
from doc_intelligence_hub.core.datetime_utils import serialize_utc_datetime
from doc_intelligence_hub.core.paperless import PaperlessClient
from doc_intelligence_hub.modules.ocr_quality import annotations as annotations_service
from doc_intelligence_hub.modules.ocr_quality import region_diff, region_inspection
from doc_intelligence_hub.modules.ocr_quality.config import settings as ocr_quality_settings
from doc_intelligence_hub.modules.ocr_quality.database import InventoryRun
from doc_intelligence_hub.modules.ocr_quality.database import get_session as get_ocr_quality_session
from doc_intelligence_hub.modules.ocr_quality.database import init_db as init_ocr_quality_db
from doc_intelligence_hub.modules.ocr_quality.models import (
    ReasonCode,
    RunStage,
    RunStatus,
    RunTrigger,
)
from doc_intelligence_hub.modules.ocr_quality.service import (
    ManualStage2DocumentNotFoundError,
    ManualStage2ProfilingFailedError,
    OcrQualityInventoryService,
    RunConflictError,
    _digest,
    emit_run_alert,
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

# document_ids currently running a forced single-document Stage-2 trigger
# (see ``force_stage2_analysis``) *in this process* — guards against a second
# concurrent trigger for the same document while the first is still inline.
_active_manual_stage2_document_ids: set[int] = set()


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
        "started_at": serialize_utc_datetime(run.started_at),
        "finished_at": serialize_utc_datetime(run.finished_at),
        "actor": run.actor,
        "trigger": run.trigger,
        "correlation_id": run.correlation_id,
        "cancel_requested": run.cancel_requested,
        "cancelled_at": serialize_utc_datetime(run.cancelled_at),
        "retry_count": run.retry_count,
        "max_retries": run.max_retries,
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
    actor: str = Field(default="system", description="Reviewer/system identity for audit purposes.")
    correlation_id: str | None = Field(
        default=None, description="Caller-supplied correlation id, echoed back on every response."
    )


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
    actor: str = Field(default="system", description="Reviewer/system identity for audit purposes.")
    correlation_id: str | None = Field(
        default=None, description="Caller-supplied correlation id, echoed back on every response."
    )


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
            emit_run_alert(run, reason="failed")
    finally:
        db.close()


async def _execute_corpus_scan(
    client: PaperlessClient,
    *,
    run_id: str,
    batch_size: int,
    resume: bool,
    scope_params: dict[str, Any] | None,
    actor: str = "system",
    trigger: RunTrigger | str = RunTrigger.MANUAL,
    correlation_id: str | None = None,
) -> None:
    _active_run_ids.add(run_id)
    try:
        service = OcrQualityInventoryService(client, get_ocr_quality_session)
        await service.run_corpus_scan(
            batch_size=batch_size,
            run_id=run_id,
            resume=resume,
            scope_params=scope_params or None,
            actor=actor,
            trigger=trigger,
            correlation_id=correlation_id,
        )
    except RunConflictError:
        logger.info("OCR quality corpus scan %s superseded by conflicting run", run_id)
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
    actor: str = "system",
    trigger: RunTrigger | str = RunTrigger.MANUAL,
    correlation_id: str | None = None,
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
            actor=actor,
            trigger=trigger,
            correlation_id=correlation_id,
        )
    except RunConflictError:
        logger.info("OCR quality stratified sample %s superseded by conflicting run", run_id)
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
        actor=body.actor,
        trigger=RunTrigger.MANUAL,
        correlation_id=body.correlation_id,
    )
    return {
        "run_id": run_id,
        "stage": RunStage.STAGE_1_CORPUS_SCAN.value,
        "status": RunStatus.RUNNING.value,
        "scheduled_at": serialize_utc_datetime(datetime.now(UTC)),
        "actor": body.actor,
        "correlation_id": body.correlation_id,
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
        "scheduled_at": serialize_utc_datetime(datetime.now(UTC)),
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
        actor=body.actor,
        trigger=RunTrigger.MANUAL,
        correlation_id=body.correlation_id,
    )
    return {
        "run_id": sample_run_id,
        "source_run_id": run_id,
        "stage": RunStage.STAGE_2_STRATIFIED_SAMPLE.value,
        "status": RunStatus.RUNNING.value,
        "scheduled_at": serialize_utc_datetime(datetime.now(UTC)),
        "actor": body.actor,
        "correlation_id": body.correlation_id,
    }


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, Any]:
    """Request cooperative cancellation of a running Stage-1/Stage-2 run (issue #30).

    The run's own loop observes ``cancel_requested`` between pages/documents
    and stops cleanly — cancellation is not instantaneous, but is always
    observable via ``GET /runs/{run_id}`` afterwards. Returns 404 if the run
    is unknown, 409 if it's already in a terminal state.
    """
    init_ocr_quality_db()
    service = OcrQualityInventoryService(None, get_ocr_quality_session)  # type: ignore[arg-type]
    try:
        return service.request_cancellation(run_id)
    except ValueError as exc:
        message = str(exc)
        if message.startswith("Unknown run"):
            raise HTTPException(
                status_code=404, detail={"code": "not_found", "message": message}
            ) from exc
        raise HTTPException(
            status_code=409, detail={"code": "run_not_cancellable", "message": message}
        ) from exc


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
    resolved: bool | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = Query(None, pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Filterable, paginated review queue over the latest per-document assessment.

    All filters are optional and combine with AND. Only metadata and score
    fields are returned — never raw OCR text. ``sort_by``/``sort_dir`` support
    server-side column sorting (see ``OcrQualityInventoryService._SORTABLE_COLUMNS``
    for the allowed column keys); an unrecognized ``sort_by`` falls back to
    the default ordering.
    """
    return _service().list_document_assessments(
        review_status=review_status,
        document_type=document_type,
        correspondent=correspondent,
        document_profile=document_profile,
        downstream_outcome=downstream_outcome,
        resolved=resolved,
        created_after=created_after,
        created_before=created_before,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )


@router.get("/downstream-outcomes")
async def list_downstream_outcomes() -> dict[str, Any]:
    """Distinct downstream-outcome values in the corpus, for the filter dropdown."""
    return {"downstream_outcomes": _service().list_downstream_outcomes()}


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


@router.post("/documents/{document_id}/stage2")
async def force_stage2_analysis(
    request: Request,
    document_id: int,
    max_pages: int | None = None,
    actor: str = "system",
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Force Stage-2 PDF profiling + quality re-score for one document on demand.

    Unlike ``POST /runs/{run_id}/sample`` (a corpus-wide random stratified
    sample), this targets exactly one ``document_id`` — e.g. triggered from
    the document detail page. Requires the document to already have a
    Stage-1 ``DocumentAssessment`` (404 otherwise — the detail page itself
    can't be open without one). Runs inline, not as a ``BackgroundTasks``
    job: a single document's PDF fetch + profile is fast, so the request
    just returns the refreshed document detail payload directly instead of
    a ``run_id`` to poll. Refuses a second concurrent trigger for the same
    document — returns 409 instead. Repeated delivery for an unchanged
    document version + ``max_pages`` returns the prior result instead of
    re-fetching (issue #30 idempotency). Only ever reads from Paperless
    (``get_document_preview``) — never writes anything back to it.
    """
    init_ocr_quality_db()
    if document_id in _active_manual_stage2_document_ids:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stage2_already_in_progress",
                "message": f"A forced Stage-2 analysis for document {document_id} is already running.",
                "document_id": document_id,
            },
        )

    client = make_paperless_client(request, timeout=30.0)
    service = OcrQualityInventoryService(client, get_ocr_quality_session)
    _active_manual_stage2_document_ids.add(document_id)
    try:
        return await service.run_manual_stage2(
            document_id=document_id,
            max_pages=max_pages or ocr_quality_settings.pdf_profile_max_pages,
            actor=actor,
            trigger=RunTrigger.MANUAL,
            correlation_id=correlation_id,
        )
    except ManualStage2DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "not_found",
                "message": (
                    f"No OCR quality assessment for document {document_id}; "
                    "run a Stage-1 corpus scan first."
                ),
            },
        ) from exc
    except ManualStage2ProfilingFailedError as exc:
        is_parse_failure = exc.reason_code == ReasonCode.PDF_PARSE_FAILED.value
        status_code = 422 if is_parse_failure else 502
        raise HTTPException(
            status_code=status_code,
            detail={
                "code": "pdf_parse_failed" if is_parse_failure else "paperless_fetch_failed",
                "message": f"Stage-2 profiling failed for document {document_id}.",
                "reason_code": exc.reason_code,
            },
        ) from exc
    except RunConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "run_already_in_progress",
                "message": f"An equivalent Stage-2 run for document {document_id} is already active.",
                "run_id": exc.run_id,
            },
        ) from exc
    finally:
        _active_manual_stage2_document_ids.discard(document_id)
        await client.aclose()


# ---------------------------------------------------------------------------
# Region-level inspection (issue #134, Part 1) — read-only, on-demand
# ---------------------------------------------------------------------------


async def _fetch_pdf_bytes(request: Request, document_id: int) -> bytes:
    """Get PDF bytes for a document, reusing the short-lived in-process cache.

    Downloads from Paperless (via the existing preview endpoint, same
    convention as ``DocumentPreview``/``DocumentViewerModal``) only on a
    cache miss — never precomputed for the whole corpus.
    """
    cached = region_inspection.peek_cached_pdf_bytes(document_id)
    if cached is not None:
        return cached

    client = make_paperless_client(request, timeout=30.0)
    try:
        pdf_bytes, _content_type = await client.get_document_preview(document_id)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "paperless_fetch_failed",
                "message": f"Could not fetch document {document_id} from Paperless.",
            },
        ) from exc
    finally:
        await client.aclose()

    region_inspection.store_pdf_bytes(document_id, pdf_bytes)
    return pdf_bytes


@router.get("/documents/{document_id}/regions")
async def get_document_regions(
    request: Request, document_id: int, page: int = Query(1, ge=1)
) -> dict[str, Any]:
    """Word-level geometry + heuristic flags for one page of one document.

    Computed on demand: downloads (or reuses a briefly cached copy of) the
    document's PDF and parses page geometry with the same ``pdf_loader``
    used by the Stage 2 profiler — nothing here is precomputed for the
    whole corpus. Each word is flagged ``duplicate_overlap``,
    ``bounds_sanity``, ``alignment``, and/or ``content_plausibility`` using
    the same heuristics as ``overlay_scoring.py``/``machine_scoring.py``,
    evaluated per-word, and cross-referenced against this document's stored
    scorer ``reasons`` where the flag category matches.
    """
    pdf_bytes = await _fetch_pdf_bytes(request, document_id)

    assessment = _service().get_document_assessment(document_id)
    document_reasons = (assessment or {}).get("reasons") or []

    regions = region_inspection.build_page_regions(
        pdf_bytes=pdf_bytes, page_number=page, document_reasons=document_reasons
    )
    if regions is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "page_not_found",
                "message": f"Document {document_id} has no page {page} with parseable geometry.",
            },
        )
    return regions


@router.get("/documents/{document_id}/pages/{page}/image")
async def get_document_page_image(
    request: Request,
    document_id: int,
    page: int,
    dpi: int = Query(
        region_inspection.DEFAULT_PAGE_IMAGE_DPI,
        ge=region_inspection.MIN_PAGE_IMAGE_DPI,
        le=region_inspection.MAX_PAGE_IMAGE_DPI,
    ),
) -> Response:
    """Render one page of a document as a PNG image, for overlay display.

    Fetched/rendered on demand (same short-lived cache as ``/regions``) —
    never precomputed for the whole corpus.
    """
    if page < 1:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_page", "message": "page must be >= 1"}
        )
    pdf_bytes = await _fetch_pdf_bytes(request, document_id)
    rendered = region_inspection.render_page_image(pdf_bytes=pdf_bytes, page_number=page, dpi=dpi)
    if rendered is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "page_not_found",
                "message": f"Document {document_id} has no renderable page {page}.",
            },
        )
    png_bytes, _width_px, _height_px = rendered
    return Response(content=png_bytes, media_type="image/png")


# ---------------------------------------------------------------------------
# Box-level diffing between two word-box lists for the same page (connects
# issue #134's region inspection with issue #18's candidate comparison) —
# read-only, stateless, no Paperless or candidate-storage access at all;
# callers already have both word lists from their own /regions calls.
# ---------------------------------------------------------------------------


class RegionDiffWordRequest(BaseModel):
    text: str = ""
    x0: float = 0.0
    top: float = 0.0
    x1: float = 0.0
    bottom: float = 0.0


class RegionDiffRequest(BaseModel):
    words_a: list[RegionDiffWordRequest] = Field(
        default_factory=list, description="Word boxes from the first ('A') source."
    )
    words_b: list[RegionDiffWordRequest] = Field(
        default_factory=list, description="Word boxes from the second ('B') source."
    )
    page_width: float = Field(
        description="Shared page width (points) both word lists are relative to."
    )
    page_height: float = Field(
        description="Shared page height (points) both word lists are relative to."
    )


@router.post("/regions/diff")
async def diff_regions(body: RegionDiffRequest) -> dict[str, Any]:
    """Diff two word-box lists for the same page.

    Single implementation of the box-matching heuristic (greedy nearest
    neighbor combining text similarity + positional proximity) shared by
    every comparison the frontend might render — current-vs-candidate or
    candidate-vs-candidate. See ``region_diff.py`` for the full heuristic
    write-up.
    """
    result = region_diff.diff_word_boxes_from_dicts(
        [w.model_dump() for w in body.words_a],
        [w.model_dump() for w in body.words_b],
        page_width=body.page_width,
        page_height=body.page_height,
    )
    return result.to_dict()


# ---------------------------------------------------------------------------
# Manual annotations (issue #134, Part 2) — the only mutation endpoints in
# this router; they mutate OWL's own local annotation table only, never
# Paperless or the OCR quality assessment tables.
# ---------------------------------------------------------------------------


class AnnotationCreateRequest(BaseModel):
    page: int = Field(default=1, ge=1)
    x0: float
    top: float
    x1: float
    bottom: float
    label: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=2000)
    created_by: str | None = Field(default=None, max_length=200)


class AnnotationUpdateRequest(BaseModel):
    page: int | None = Field(default=None, ge=1)
    x0: float | None = None
    top: float | None = None
    x1: float | None = None
    bottom: float | None = None
    label: str | None = Field(default=None, min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=2000)


def _annotations_session_factory() -> Any:
    init_ocr_quality_db()
    return get_ocr_quality_session


@router.get("/documents/{document_id}/annotations")
async def list_document_annotations(
    document_id: int, page: int | None = Query(default=None, ge=1)
) -> dict[str, Any]:
    """List saved manual annotations for a document, optionally filtered by page."""
    session_factory = _annotations_session_factory()
    return {
        "annotations": annotations_service.list_annotations(
            session_factory, document_id=document_id, page=page
        )
    }


@router.post("/documents/{document_id}/annotations", status_code=201)
async def create_document_annotation(
    document_id: int, body: AnnotationCreateRequest
) -> dict[str, Any]:
    """Create a manual bounding-box annotation for a document/page."""
    session_factory = _annotations_session_factory()
    return annotations_service.create_annotation(
        session_factory,
        document_id=document_id,
        page=body.page,
        x0=body.x0,
        top=body.top,
        x1=body.x1,
        bottom=body.bottom,
        label=body.label,
        note=body.note,
        created_by=body.created_by,
    )


@router.patch("/documents/{document_id}/annotations/{annotation_id}")
async def update_document_annotation(
    document_id: int, annotation_id: int, body: AnnotationUpdateRequest
) -> dict[str, Any]:
    """Edit an existing annotation's bbox, label, or note."""
    session_factory = _annotations_session_factory()
    updated = annotations_service.update_annotation(
        session_factory,
        document_id=document_id,
        annotation_id=annotation_id,
        updates=body.model_dump(exclude_unset=True),
    )
    if updated is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "not_found",
                "message": f"No annotation {annotation_id} for document {document_id}",
            },
        )
    return updated


@router.delete("/documents/{document_id}/annotations/{annotation_id}", status_code=204)
async def delete_document_annotation(document_id: int, annotation_id: int) -> Response:
    """Delete an annotation."""
    session_factory = _annotations_session_factory()
    deleted = annotations_service.delete_annotation(
        session_factory, document_id=document_id, annotation_id=annotation_id
    )
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "not_found",
                "message": f"No annotation {annotation_id} for document {document_id}",
            },
        )
    return Response(status_code=204)
