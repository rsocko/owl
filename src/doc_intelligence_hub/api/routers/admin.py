"""Admin API router — scoring weights, schedule config, match debugging."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

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


@router.get("/schedules")
async def get_schedules(request: Request) -> dict[str, Any]:
    """Get current schedule configuration."""
    schedules = getattr(request.app.state, "admin_schedules", None)
    if schedules is None:
        schedules = {
            "action_queue": {"cron": "0 */6 * * *", "limit": 50, "enabled": True},
            "eob_matching": {"cron": "0 2 * * *", "limit": 200, "enabled": True},
        }
    return schedules


class ScheduleConfig(BaseModel):
    cron: str = "0 */6 * * *"
    limit: int = Field(default=50, ge=1, le=500)
    enabled: bool = True


class SchedulesUpdate(BaseModel):
    action_queue: ScheduleConfig | None = None
    eob_matching: ScheduleConfig | None = None


@router.put("/schedules")
async def update_schedules(request: Request, body: SchedulesUpdate) -> dict[str, Any]:
    """Update schedule configuration (persists for this server session)."""
    current = getattr(request.app.state, "admin_schedules", None) or {
        "action_queue": {"cron": "0 */6 * * *", "limit": 50, "enabled": True},
        "eob_matching": {"cron": "0 2 * * *", "limit": 200, "enabled": True},
    }
    if body.action_queue:
        current["action_queue"] = body.action_queue.model_dump()
    if body.eob_matching:
        current["eob_matching"] = body.eob_matching.model_dump()
    request.app.state.admin_schedules = current
    return {"status": "ok", "schedules": current}
