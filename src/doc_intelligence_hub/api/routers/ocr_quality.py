"""OCR quality baseline inventory API router — read-only (issues #25, #29, #115).

All endpoints are GET-only: they surface run status/progress, redacted
aggregate reports, and per-document review data for the OWL OCR Quality
review UI. This router never triggers a scan, never mutates Paperless, and
never returns raw OCR text — only aggregate or per-document metadata/score
fields already computed by the ``ocr_quality`` module's scan pipeline.

Endpoints:
    GET /api/ocr-quality/runs                 — List known runs (most recent first)
    GET /api/ocr-quality/runs/{run_id}         — Single run status/progress
    GET /api/ocr-quality/runs/{run_id}/report  — Redacted aggregate report for a run
    GET /api/ocr-quality/distribution          — Corpus-wide review-status/score snapshot
    GET /api/ocr-quality/documents             — Filterable/paginated review queue
    GET /api/ocr-quality/documents/{document_id} — Single document's full assessment detail
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from doc_intelligence_hub.modules.ocr_quality.database import InventoryRun
from doc_intelligence_hub.modules.ocr_quality.database import get_session as get_ocr_quality_session
from doc_intelligence_hub.modules.ocr_quality.database import init_db as init_ocr_quality_db
from doc_intelligence_hub.modules.ocr_quality.service import OcrQualityInventoryService

router = APIRouter(prefix="/api/ocr-quality", tags=["ocr-quality"])


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
