"""Durable lifecycle helpers for historical relationship scans."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError

from doc_intelligence_hub.modules.triage.database import RelationshipScanRun, get_session

ACTIVE_SCAN_STATUSES = ("queued", "running")
ACTIVE_SCAN_SLOT = "historical_relationship_scan"


class ScanAlreadyActiveError(RuntimeError):
    def __init__(self, run: dict[str, Any], scope_key: str | None):
        super().__init__(run["run_id"])
        self.run = run
        self.scope_key = scope_key


def _to_dict(run: RelationshipScanRun) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "status": run.status,
        "counts": json.loads(run.counts_json or "{}"),
        "errors": json.loads(run.errors_json or "[]"),
        "projection": json.loads(run.projection_json) if run.projection_json else None,
        "scope": {"kind": run.scope_kind or "unknown"},
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def recover_abandoned_runs(active_run_ids: set[str], *, stale_after_hours: int = 24) -> None:
    """Release only old active rows, without racing a worker in another API process."""
    session = get_session()
    try:
        cutoff = datetime.now(UTC) - timedelta(hours=stale_after_hours)
        abandoned = (
            session.query(RelationshipScanRun)
            .filter(
                RelationshipScanRun.status.in_(ACTIVE_SCAN_STATUSES),
                RelationshipScanRun.created_at < cutoff,
            )
            .all()
        )
        changed = False
        for run in abandoned:
            if run.id in active_run_ids:
                continue
            run.status = "failed"
            run.errors_json = json.dumps(
                [{"code": "scan_interrupted", "message": "The scan was interrupted."}]
            )
            run.active_slot = None
            run.finished_at = datetime.now(UTC)
            changed = True
        if changed:
            session.commit()
    finally:
        session.close()


def create_scan_run(
    *,
    scope_key: str | None = None,
    scope_kind: str = "unknown",
) -> dict[str, Any]:
    session = get_session()
    try:
        active = (
            session.query(RelationshipScanRun)
            .filter(RelationshipScanRun.status.in_(ACTIVE_SCAN_STATUSES))
            .order_by(RelationshipScanRun.created_at.desc())
            .first()
        )
        if active:
            raise ScanAlreadyActiveError(_to_dict(active), active.scope_key)
        run = RelationshipScanRun(
            scope_key=scope_key,
            scope_kind=scope_kind,
            active_slot=ACTIVE_SCAN_SLOT,
        )
        session.add(run)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            active = (
                session.query(RelationshipScanRun)
                .filter(RelationshipScanRun.active_slot == ACTIVE_SCAN_SLOT)
                .one()
            )
            raise ScanAlreadyActiveError(_to_dict(active), active.scope_key) from None
        session.refresh(run)
        return _to_dict(run)
    finally:
        session.close()


def get_scan_run(run_id: str) -> dict[str, Any] | None:
    session = get_session()
    try:
        run = session.query(RelationshipScanRun).filter_by(id=run_id).one_or_none()
        return _to_dict(run) if run else None
    finally:
        session.close()


def update_scan_run(
    run_id: str,
    *,
    status: str | None = None,
    counts: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
    projection: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    session = get_session()
    try:
        run = session.query(RelationshipScanRun).filter_by(id=run_id).one_or_none()
        if run is None:
            return None
        if status:
            run.status = status
            if status == "running" and run.started_at is None:
                run.started_at = datetime.now(UTC)
            if status in {"completed", "failed"}:
                run.finished_at = datetime.now(UTC)
                run.active_slot = None
        if counts is not None:
            run.counts_json = json.dumps(counts)
        if errors is not None:
            run.errors_json = json.dumps(errors)
        if projection is not None:
            run.projection_json = json.dumps(projection)
        session.commit()
        session.refresh(run)
        return _to_dict(run)
    finally:
        session.close()
