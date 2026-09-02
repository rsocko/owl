"""OCR quality baseline inventory API router — read-only (issue #25).

All endpoints are GET-only: they surface run status/progress and redacted
aggregate reports for runs produced by the ``ocr_quality`` module's CLI.
This router never triggers a scan or mutates Paperless — it only reads the
module's own local SQLite state.

Endpoints:
    GET /api/ocr-quality/runs              — List known runs (most recent first)
    GET /api/ocr-quality/runs/{run_id}      — Single run status/progress
    GET /api/ocr-quality/runs/{run_id}/report — Redacted aggregate report
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from doc_intelligence_hub.modules.ocr_quality.database import InventoryRun
from doc_intelligence_hub.modules.ocr_quality.database import get_session as get_ocr_quality_session
from doc_intelligence_hub.modules.ocr_quality.database import init_db as init_ocr_quality_db
from doc_intelligence_hub.modules.ocr_quality.service import OcrQualityInventoryService

router = APIRouter(prefix="/api/ocr-quality", tags=["ocr-quality"])


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
