"""Admin API router — scoring weights, schedule config, match debugging, data cleanup."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from doc_intelligence_hub.core.scheduler import DEFAULT_SCHEDULES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Default EOB matching weights (must sum to 1.0)
_DEFAULT_WEIGHTS: dict[str, float] = {
    "date": 0.30,
    "provider": 0.25,
    "patient": 0.20,
    "amount": 0.15,
    "procedures": 0.10,
}


class WeightsUpdate(BaseModel):
    date: float = Field(default=0.30, ge=0, le=1)
    provider: float = Field(default=0.25, ge=0, le=1)
    patient: float = Field(default=0.20, ge=0, le=1)
    amount: float = Field(default=0.15, ge=0, le=1)
    procedures: float = Field(default=0.10, ge=0, le=1)


@router.get("/weights")
async def get_weights(request: Request) -> dict[str, float]:
    """Get current EOB matching scoring weights."""
    weights = getattr(request.app.state, "eob_weights", None)
    if weights is None:
        return dict(_DEFAULT_WEIGHTS)
    return weights


@router.put("/weights")
async def update_weights(request: Request, body: WeightsUpdate) -> dict[str, Any]:
    """Update EOB matching scoring weights (persists for this server session)."""
    weights = {
        "date": round(body.date, 2),
        "provider": round(body.provider, 2),
        "patient": round(body.patient, 2),
        "amount": round(body.amount, 2),
        "procedures": round(body.procedures, 2),
    }
    total = sum(weights.values())
    if abs(total - 1.0) > 0.05:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_weights",
                "message": f"Weights must sum to ~1.0, got {total:.2f}",
            },
        )
    request.app.state.eob_weights = weights
    return {"status": "ok", "weights": weights, "sum": round(total, 2)}


_DEFAULT_SCHEDULES = DEFAULT_SCHEDULES


@router.get("/schedules")
async def get_schedules(request: Request) -> dict[str, Any]:
    """Get current schedule configuration for all DI Hub modules.

    If the built-in scheduler is running, returns live schedule state
    including next_run and last_run info.
    """
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler and scheduler.running:
        return scheduler.get_schedules()
    # Fallback: return static config
    schedules = getattr(request.app.state, "admin_schedules", None)
    if schedules is None:
        schedules = {k: dict(v) for k, v in _DEFAULT_SCHEDULES.items()}
    return schedules


class ScheduleConfig(BaseModel):
    cron: str = "0 */6 * * *"
    limit: int | None = Field(default=None, ge=1, le=500)
    enabled: bool = True


class SchedulesUpdate(BaseModel):
    statement_discovery: ScheduleConfig | None = None
    statement_gap_check: ScheduleConfig | None = None
    action_queue: ScheduleConfig | None = None
    eob_matching: ScheduleConfig | None = None


@router.put("/schedules")
async def update_schedules(request: Request, body: SchedulesUpdate) -> dict[str, Any]:
    """Update schedule configuration and reschedule jobs on the live scheduler."""
    scheduler = getattr(request.app.state, "scheduler", None)

    # Build merged config
    if scheduler and scheduler.running:
        current = scheduler.get_schedules()
    else:
        current = getattr(request.app.state, "admin_schedules", None) or {
            k: dict(v) for k, v in _DEFAULT_SCHEDULES.items()
        }

    for key in ("statement_discovery", "statement_gap_check", "action_queue", "eob_matching"):
        update = getattr(body, key, None)
        if update:
            merged = {**current.get(key, {}), **update.model_dump(exclude_none=True)}
            current[key] = merged
            # Live-update the scheduler if running
            if scheduler and scheduler.running:
                scheduler.update_schedule(key, merged)

    request.app.state.admin_schedules = current

    # Return live state if scheduler is active
    if scheduler and scheduler.running:
        return {"status": "ok", "schedules": scheduler.get_schedules()}
    return {"status": "ok", "schedules": current}


# ------------------------------------------------------------------
# Data retention / cleanup
# ------------------------------------------------------------------


class CleanupRequest(BaseModel):
    dry_run: bool = Field(
        default=True, description="Preview what would be deleted without actually deleting."
    )


class RetentionPolicyResponse(BaseModel):
    processing_history_days: int = Field(description="0 = keep forever (infinite)")
    alerts_days: int = Field(description="0 = keep forever (infinite)")
    actions_days: int = Field(description="0 = keep forever (infinite)")
    matches_days: int = Field(description="0 = keep forever (infinite)")
    discovery_runs_days: int = Field(description="0 = keep forever (infinite)")


class RetentionPolicyUpdate(BaseModel):
    processing_history_days: int = Field(default=90, ge=0, description="0 = keep forever")
    alerts_days: int = Field(default=30, ge=0, description="0 = keep forever")
    actions_days: int = Field(default=365, ge=0, description="0 = keep forever")
    matches_days: int = Field(default=365, ge=0, description="0 = keep forever")
    discovery_runs_days: int = Field(default=365, ge=0, description="0 = keep forever")


def _get_effective_retention(request: Request):
    """Return the effective RetentionConfig, merging runtime overrides."""
    from doc_intelligence_hub.core.retention import load_retention_config

    cfg = load_retention_config()
    overrides = getattr(request.app.state, "retention_overrides", None)
    if overrides:
        for key in (
            "processing_history_days",
            "alerts_days",
            "actions_days",
            "matches_days",
            "discovery_runs_days",
        ):
            if key in overrides:
                setattr(cfg, key, overrides[key])
    return cfg


@router.get("/retention", response_model=RetentionPolicyResponse)
async def get_retention_policy(request: Request) -> RetentionPolicyResponse:
    """Return the current data retention policy (config + runtime overrides)."""
    cfg = _get_effective_retention(request)
    return RetentionPolicyResponse(
        processing_history_days=cfg.processing_history_days,
        alerts_days=cfg.alerts_days,
        actions_days=cfg.actions_days,
        matches_days=cfg.matches_days,
        discovery_runs_days=cfg.discovery_runs_days,
    )


@router.put("/retention")
async def update_retention_policy(request: Request, body: RetentionPolicyUpdate) -> dict[str, Any]:
    """Update retention policy (persists for this server session).

    Set any value to ``0`` for infinite retention (never delete).
    Restart the service to reset to config file / env defaults.
    """
    overrides = {
        "processing_history_days": body.processing_history_days,
        "alerts_days": body.alerts_days,
        "actions_days": body.actions_days,
        "matches_days": body.matches_days,
        "discovery_runs_days": body.discovery_runs_days,
    }
    request.app.state.retention_overrides = overrides
    logger.info("Retention policy updated: %s", overrides)
    return {"status": "ok", "retention": overrides}


@router.post("/cleanup")
async def run_cleanup(request: Request, body: CleanupRequest) -> dict[str, Any]:
    """Trigger a data cleanup across all DI Hub modules.

    With ``dry_run=true`` (default), returns a preview of what *would*
    be deleted.  Set ``dry_run=false`` to actually delete stale records
    and VACUUM the databases.
    """
    from doc_intelligence_hub.core.retention import run_cleanup as _run_cleanup

    cfg = _get_effective_retention(request)
    logger.info("Admin cleanup triggered (dry_run=%s)", body.dry_run)
    result = _run_cleanup(dry_run=body.dry_run, config=cfg)
    return {"status": "ok", **result.to_dict()}


@router.get("/storage")
async def get_storage_stats() -> dict[str, Any]:
    """Return storage usage breakdown by database and table.

    Shows file sizes, row counts per table, and module groupings
    so users can assess which data types are growing and whether
    retention settings are appropriate.
    """
    from doc_intelligence_hub.core.retention import get_storage_stats as _get_storage_stats

    return _get_storage_stats().to_dict()
