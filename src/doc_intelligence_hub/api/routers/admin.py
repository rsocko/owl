"""Admin API router — scoring weights, schedule config, match debugging."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from doc_intelligence_hub.core.scheduler import DEFAULT_SCHEDULES

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
            detail={"code": "invalid_weights", "message": f"Weights must sum to ~1.0, got {total:.2f}"},
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
