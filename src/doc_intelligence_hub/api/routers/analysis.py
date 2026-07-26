"""Analysis Engine API — rule management and manual execution.

Endpoints:
    POST /api/analysis/execute          — Run rules by trigger type
    POST /api/analysis/execute/{rule_id} — Run a single rule manually
    GET  /api/analysis/rules            — List all rules with status
    GET  /api/analysis/rules/{rule_id}  — Get rule detail
    PUT  /api/analysis/rules/{rule_id}  — Update rule config
    POST /api/analysis/rules            — Create custom rule
    DELETE /api/analysis/rules/{rule_id} — Delete custom rule
    GET  /api/analysis/stats            — Execution statistics
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from doc_intelligence_hub.modules.analysis.models import (
    ExecuteRequest,
    ExecuteResponse,
    RuleCreateRequest,
    RuleListResponse,
    RuleResponse,
    RuleUpdateRequest,
    TriggerType,
)
from doc_intelligence_hub.modules.analysis.rule_executor import (
    execute_rule,
    execute_scheduled_batch,
    execute_trigger,
)
from doc_intelligence_hub.modules.analysis.rule_registry import (
    create_custom_rule,
    delete_custom_rule,
    get_rule,
    list_rules,
    update_rule_config,
)

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.post("/execute", response_model=ExecuteResponse)
async def trigger_execution(request: ExecuteRequest) -> dict[str, Any]:
    """Execute rules by trigger type, with optional rule ID filter."""
    result = await execute_trigger(
        trigger_type=request.trigger_type,
        document_id=request.document_id,
        rule_ids=request.rule_ids,
        dry_run=request.dry_run,
    )
    return result


@router.post("/execute/scheduled/{schedule_type}")
async def execute_scheduled(schedule_type: str) -> dict[str, Any]:
    """Execute scheduled batch analysis (daily/weekly/monthly).

    Maps schedule_type to trigger type:
        daily  → schedule (daily cron rules)
        weekly → schedule (weekly cron rules)
        monthly → schedule (monthly cron rules)
    """
    if schedule_type not in ("daily", "weekly", "monthly"):
        raise HTTPException(
            status_code=400, detail="schedule_type must be 'daily', 'weekly', or 'monthly'"
        )
    result = await execute_scheduled_batch(TriggerType.SCHEDULE)
    result["schedule_type"] = schedule_type
    return result


@router.post("/execute/{rule_id}")
async def execute_single_rule(
    rule_id: str,
    document_id: int | None = Query(None, description="Paperless document ID for context"),
    dry_run: bool = Query(False, description="If true, don't persist results"),
) -> dict[str, Any]:
    """Execute a single rule manually."""
    result = await execute_rule(rule_id, document_id=document_id, dry_run=dry_run)
    if not result.get("success") and result.get("error"):
        if "not found" in result["error"]:
            raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/rules", response_model=RuleListResponse)
async def get_rules(
    tier: str | None = Query(None, description="Filter by tier: basic, llm, n8n"),
    enabled: bool | None = Query(None, description="Filter by enabled state"),
) -> dict[str, Any]:
    """List all analysis rules with their current status."""
    rules = list_rules()

    if tier:
        rules = [r for r in rules if r.tier.value == tier]
    if enabled is not None:
        rules = [r for r in rules if r.enabled == enabled]

    return {
        "rules": [_rule_to_response(r) for r in rules],
        "total": len(rules),
    }


@router.get("/rules/{rule_id}", response_model=RuleResponse)
async def get_rule_detail(rule_id: str) -> dict[str, Any]:
    """Get detailed configuration for a single rule."""
    rule = get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return _rule_to_response(rule)


@router.put("/rules/{rule_id}", response_model=RuleResponse)
async def update_rule(rule_id: str, request: RuleUpdateRequest) -> dict[str, Any]:
    """Update a rule's configuration (enable/disable, params, routing)."""
    updates = request.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    rule = update_rule_config(rule_id, updates)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")

    return _rule_to_response(rule)


@router.post("/rules", response_model=RuleResponse, status_code=201)
async def create_rule(request: RuleCreateRequest) -> dict[str, Any]:
    """Create a new custom analysis rule."""
    existing = get_rule(request.id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Rule '{request.id}' already exists")

    rule = create_custom_rule(request.model_dump())
    return _rule_to_response(rule)


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str) -> dict[str, Any]:
    """Delete a custom rule (built-in rules cannot be deleted)."""
    deleted = delete_custom_rule(rule_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Custom rule '{rule_id}' not found (only custom rules can be deleted)",
        )
    return {"deleted": True, "rule_id": rule_id}


@router.get("/stats")
async def get_stats() -> dict[str, Any]:
    """Get analysis engine execution statistics."""
    rules = list_rules()

    total_rules = len(rules)
    enabled_rules = sum(1 for r in rules if r.enabled)
    total_insights = sum(r.insight_count for r in rules)

    by_tier = {}
    for r in rules:
        tier = r.tier.value
        by_tier[tier] = by_tier.get(tier, 0) + 1

    recent_runs = [
        {
            "rule_id": r.id,
            "rule_name": r.name,
            "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
            "last_run_status": r.last_run_status,
            "insight_count": r.insight_count,
        }
        for r in rules
        if r.last_run_at
    ]
    recent_runs.sort(key=lambda x: x["last_run_at"] or "", reverse=True)

    return {
        "total_rules": total_rules,
        "enabled_rules": enabled_rules,
        "total_insights_generated": total_insights,
        "by_tier": by_tier,
        "recent_runs": recent_runs[:20],
    }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _rule_to_response(rule: Any) -> dict[str, Any]:
    """Convert a RuleConfig to API response dict."""
    return {
        "id": rule.id,
        "name": rule.name,
        "description": rule.description,
        "tier": rule.tier.value,
        "enabled": rule.enabled,
        "trigger": rule.trigger.model_dump(),
        "params": rule.params,
        "routing": rule.routing.model_dump(),
        "display": rule.display.model_dump(),
        "source": rule.source,
        "last_run_at": rule.last_run_at.isoformat() if rule.last_run_at else None,
        "last_run_status": rule.last_run_status,
        "insight_count": rule.insight_count,
    }
