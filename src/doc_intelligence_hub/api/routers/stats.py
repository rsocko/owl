"""Stats router — aggregate metrics across all DI modules for MC integration.

Provides the `/api/stats` endpoint consumed by Mission Control's KPI cards,
Insights page, and connector health status.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query, Request

from doc_intelligence_hub.api.routers import get_loaded_statement_config, make_paperless_client
from doc_intelligence_hub.modules.action_queue.config import settings as aq_settings
from doc_intelligence_hub.modules.action_queue.database import (
    Action,
    ProcessingHistory,
    get_session as get_aq_session,
    init_db as aq_init_db,
)
from doc_intelligence_hub.modules.eob_matching.database import (
    MatchRecord,
    MatchingRun,
    get_session as get_eob_session,
    init_db as eob_init_db,
)
from doc_intelligence_hub.modules.statements.database import Database as StatementsDB

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _period_start(period: str) -> datetime:
    """Calculate the start datetime for the given period."""
    now = datetime.now(UTC)
    if period == "week":
        return now - timedelta(days=7)
    elif period == "quarter":
        return now - timedelta(days=90)
    else:  # month (default)
        return now - timedelta(days=30)


def _action_queue_stats(period_start: datetime) -> dict[str, Any]:
    """Gather action queue statistics."""
    try:
        aq_init_db()
        db = get_aq_session()
        try:
            pending = db.query(Action).filter_by(status="pending").count()
            critical = (
                db.query(Action)
                .filter_by(status="pending")
                .filter(Action.urgency.in_(["critical", "CRITICAL"]))
                .count()
            )
            completed_this_period = (
                db.query(Action)
                .filter_by(status="completed")
                .filter(Action.completed_at >= period_start)
                .count()
            )
            return {
                "pending": pending,
                "critical": critical,
                "completed_this_period": completed_this_period,
            }
        finally:
            db.close()
    except Exception:
        return {"pending": 0, "critical": 0, "completed_this_period": 0}


def _documents_stats(period_start: datetime) -> dict[str, Any]:
    """Gather document processing statistics from the action queue history."""
    try:
        aq_init_db()
        db = get_aq_session()
        try:
            total_processed = db.query(ProcessingHistory).count()
            added_this_period = (
                db.query(ProcessingHistory)
                .filter(ProcessingHistory.processed_at >= period_start)
                .count()
            )
            return {
                "total_processed": total_processed,
                "added_this_period": added_this_period,
            }
        finally:
            db.close()
    except Exception:
        return {"total_processed": 0, "added_this_period": 0}


def _statements_stats(request: Request) -> dict[str, Any]:
    """Gather statement tracking statistics from the latest recommendation run."""
    try:
        config = get_loaded_statement_config(request)
        if config is None:
            return {"tracked": 0, "missing": 0}

        db = StatementsDB(config.runtime.database_path)
        try:
            discovery = db.load_latest_discovery()
            tracked = len(discovery.providers) if discovery else 0

            recommendations = db.load_latest_recommendations()
            missing = 0
            if recommendations:
                missing = sum(
                    1 for r in recommendations.recommendations if r.status == "missing"
                )

            return {"tracked": tracked, "missing": missing}
        finally:
            db.close()
    except Exception:
        return {"tracked": 0, "missing": 0}


def _eob_stats() -> dict[str, Any]:
    """Gather EOB matching statistics."""
    try:
        eob_init_db()
        db = get_eob_session()
        try:
            matched = db.query(MatchRecord).filter_by(status="confirmed").count()
            unmatched = db.query(MatchRecord).filter_by(status="candidate").count()

            # Calculate unresolved patient responsibility from unmatched EOBs
            from doc_intelligence_hub.modules.eob_matching.database import EOBRecord

            # Get EOB document IDs that have confirmed matches
            matched_eob_ids = [
                r.eob_document_id
                for r in db.query(MatchRecord.eob_document_id)
                .filter_by(status="confirmed")
                .all()
            ]

            # Sum patient responsibility for unmatched EOBs
            unresolved_query = db.query(EOBRecord).filter(
                EOBRecord.total_patient_responsibility.isnot(None)
            )
            if matched_eob_ids:
                unresolved_query = unresolved_query.filter(
                    EOBRecord.document_id.notin_(matched_eob_ids)
                )

            unresolved_amount = sum(
                eob.total_patient_responsibility or 0.0
                for eob in unresolved_query.all()
            )

            return {
                "matched": matched,
                "unmatched": unmatched,
                "unresolved_amount": round(unresolved_amount, 2),
            }
        finally:
            db.close()
    except Exception:
        return {"matched": 0, "unmatched": 0, "unresolved_amount": 0.0}


def _module_status_action_queue(request: Request) -> dict[str, Any]:
    """Get action queue module health status."""
    try:
        aq_init_db()
        db = get_aq_session()
        try:
            item_count = db.query(Action).filter_by(status="pending").count()
            last_run = request.app.state.last_queue_status or {}
            last_sync = last_run.get("finished_at") or last_run.get("started_at")

            status = "healthy"
            if last_run.get("status") == "error":
                status = "degraded"

            return {
                "name": "action-queue",
                "status": status,
                "last_sync": last_sync,
                "item_count": item_count,
            }
        finally:
            db.close()
    except Exception:
        return {
            "name": "action-queue",
            "status": "down",
            "last_sync": None,
            "item_count": 0,
            "detail": "Unable to connect to action queue database.",
        }


def _module_status_statements(request: Request) -> dict[str, Any]:
    """Get statement tracker module health status."""
    try:
        config = get_loaded_statement_config(request)
        if config is None:
            return {
                "name": "statements",
                "status": "down",
                "last_sync": None,
                "item_count": 0,
                "detail": "Statement tracker config not loaded.",
            }

        db = StatementsDB(config.runtime.database_path)
        try:
            conn = db.connect()
            row = conn.execute(
                "SELECT run_at FROM recommendation_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            last_sync = row["run_at"] if row else None

            recommendations = db.load_latest_recommendations()
            item_count = len(recommendations.recommendations) if recommendations else 0

            return {
                "name": "statements",
                "status": "healthy",
                "last_sync": last_sync,
                "item_count": item_count,
            }
        finally:
            db.close()
    except Exception:
        return {
            "name": "statements",
            "status": "down",
            "last_sync": None,
            "item_count": 0,
            "detail": "Unable to read statement tracker database.",
        }


def _module_status_eob(request: Request) -> dict[str, Any]:
    """Get EOB matching module health status."""
    try:
        eob_init_db()
        db = get_eob_session()
        try:
            last_run = (
                db.query(MatchingRun)
                .order_by(MatchingRun.started_at.desc())
                .first()
            )
            last_sync = last_run.finished_at.isoformat() if last_run and last_run.finished_at else None

            item_count = db.query(MatchRecord).filter_by(status="candidate").count()

            return {
                "name": "eob-matching",
                "status": "healthy",
                "last_sync": last_sync,
                "item_count": item_count,
            }
        finally:
            db.close()
    except Exception:
        return {
            "name": "eob-matching",
            "status": "down",
            "last_sync": None,
            "item_count": 0,
            "detail": "Unable to read EOB matching database.",
        }


@router.get("")
async def get_stats(
    request: Request,
    period: str = Query(default="month", pattern=r"^(week|month|quarter)$"),
) -> dict[str, Any]:
    """Aggregate statistics across all DI modules.

    Returns action counts, document processing stats, statement tracking
    metrics, EOB matching metrics, and per-module health status.

    Consumed by Mission Control's KPI cards and Insights integration.
    """
    period_start = _period_start(period)

    actions = _action_queue_stats(period_start)
    documents = _documents_stats(period_start)
    statements = _statements_stats(request)
    eob = _eob_stats()

    modules = [
        _module_status_action_queue(request),
        _module_status_statements(request),
        _module_status_eob(request),
    ]

    return {
        "actions": actions,
        "documents": documents,
        "statements": statements,
        "eob": eob,
        "modules": modules,
    }
