"""Rule Executor — orchestrates rule execution.

Accepts trigger events, finds matching rules, builds context, executes rules,
and routes results. Supports single-rule manual execution and batch scheduled runs.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from doc_intelligence_hub.modules.analysis import database as db
from doc_intelligence_hub.modules.analysis.context_builder import build_batch_context, build_context
from doc_intelligence_hub.modules.analysis.insight_router import route_result
from doc_intelligence_hub.modules.analysis.models import (
    RuleConfig,
    RuleExecutionResult,
    TriggerType,
)
from doc_intelligence_hub.modules.analysis.rule_registry import get_rule, get_rules_for_trigger
from doc_intelligence_hub.modules.analysis.rules.base import get_rule_class

logger = logging.getLogger(__name__)


async def execute_rule(rule_id: str, *, document_id: int | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Execute a single rule manually.

    Args:
        rule_id: The rule to execute.
        document_id: Optional document ID (for document_added context).
        dry_run: If True, execute but don't route/persist results.

    Returns:
        Execution result dict.
    """
    rule = get_rule(rule_id)
    if not rule:
        return {"success": False, "error": f"Rule '{rule_id}' not found"}

    if not rule.enabled:
        return {"success": False, "error": f"Rule '{rule_id}' is disabled"}

    return await _run_single_rule(rule, document_id=document_id, dry_run=dry_run)


async def execute_trigger(
    trigger_type: TriggerType,
    *,
    document_id: int | None = None,
    document_type: str | None = None,
    rule_ids: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute all rules matching a trigger type.

    Args:
        trigger_type: The trigger that fired.
        document_id: Document ID for document_added triggers.
        document_type: Document type filter for matching rules.
        rule_ids: Optional explicit list of rules to run (overrides trigger matching).
        dry_run: If True, execute but don't route/persist.

    Returns:
        Summary of execution results.
    """
    # Get matching rules
    if rule_ids:
        rules = [get_rule(rid) for rid in rule_ids]
        rules = [r for r in rules if r and r.enabled]
    else:
        rules = get_rules_for_trigger(trigger_type, document_type=document_type)

    if not rules:
        return {
            "rules_executed": 0,
            "insights_created": 0,
            "errors": [],
            "results": [],
            "message": "No matching rules found",
        }

    results = []
    errors = []
    insights_created = 0

    for rule in rules:
        try:
            outcome = await _run_single_rule(rule, document_id=document_id, dry_run=dry_run)
            results.append(outcome)
            if outcome.get("insight_id"):
                insights_created += 1
        except Exception as exc:
            error_msg = f"Rule '{rule.id}' failed: {exc}"
            logger.error(error_msg)
            errors.append({"rule_id": rule.id, "error": str(exc)})

            # Update rule state with error
            db.upsert_rule_state(
                rule.id,
                last_run_at=datetime.now(UTC),
                last_run_status=f"error: {exc}",
            )

    return {
        "rules_executed": len(rules),
        "insights_created": insights_created,
        "errors": errors,
        "results": results,
    }


async def execute_scheduled_batch(trigger_type: TriggerType) -> dict[str, Any]:
    """Execute all scheduled rules as a batch (e.g., daily/weekly/monthly).

    For scheduled rules, the context builder fetches all relevant series
    and runs the rule against each one.
    """
    rules = get_rules_for_trigger(trigger_type)

    if not rules:
        return {"rules_executed": 0, "insights_created": 0, "errors": [], "results": []}

    results = []
    errors = []
    insights_created = 0

    for rule in rules:
        try:
            # For scheduled rules, build context for each series
            contexts = await build_batch_context(rule)

            if not contexts:
                logger.debug("No contexts generated for scheduled rule '%s'", rule.id)
                continue

            for ctx in contexts:
                try:
                    result = await _execute_rule_with_context(rule, ctx)
                    if not result.success:
                        continue

                    # Route the result
                    routing_outcome = route_result(rule, result)
                    results.append(routing_outcome)

                    if routing_outcome.get("insight_id"):
                        insights_created += 1

                except Exception as exc:
                    logger.warning("Rule '%s' failed for one context: %s", rule.id, exc)

            # Update rule state
            db.upsert_rule_state(
                rule.id,
                last_run_at=datetime.now(UTC),
                last_run_status="ok",
            )

        except Exception as exc:
            error_msg = f"Scheduled rule '{rule.id}' failed: {exc}"
            logger.error(error_msg)
            errors.append({"rule_id": rule.id, "error": str(exc)})
            db.upsert_rule_state(
                rule.id,
                last_run_at=datetime.now(UTC),
                last_run_status=f"error: {exc}",
            )

    return {
        "rules_executed": len(rules),
        "insights_created": insights_created,
        "errors": errors,
        "results": results,
    }


# ------------------------------------------------------------------
# Internal execution
# ------------------------------------------------------------------


async def _run_single_rule(
    rule: RuleConfig,
    *,
    document_id: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute a single rule and optionally route the result."""
    # Build context
    ctx = await build_context(rule, document_id=document_id)

    # Execute
    result = await _execute_rule_with_context(rule, ctx)

    if not result.success:
        db.upsert_rule_state(
            rule.id,
            last_run_at=datetime.now(UTC),
            last_run_status=result.error or "no_result",
        )
        return {
            "rule_id": rule.id,
            "success": False,
            "error": result.error,
            "dry_run": dry_run,
        }

    if dry_run:
        return {
            "rule_id": rule.id,
            "success": True,
            "dry_run": True,
            "title": result.title,
            "summary": result.summary,
            "insight_type": result.insight_type.value if result.insight_type else None,
            "severity": result.suggested_severity.value,
            "metric_values": result.metric_values,
        }

    # Route the result
    routing_outcome = route_result(rule, result)
    routing_outcome["rule_id"] = rule.id
    routing_outcome["success"] = True
    return routing_outcome


async def _execute_rule_with_context(rule: RuleConfig, ctx: Any) -> RuleExecutionResult:
    """Instantiate the rule class and execute it with context."""
    rule_cls = get_rule_class(rule.id)

    if rule_cls:
        instance = rule_cls(rule)
        return await instance.execute(ctx)

    # Fallback: if no class registered, check if it's a known analyzer type
    analyzer = rule.analyzer
    if analyzer.startswith("llm:"):
        # Try to find an LLM rule by analyzer suffix
        from doc_intelligence_hub.modules.analysis.rules.llm_rules import DocumentClassification

        instance = DocumentClassification(rule)
        return await instance.execute(ctx)

    if analyzer.startswith("n8n:"):
        from doc_intelligence_hub.modules.analysis.rules.n8n_rules import N8nWebhookRule

        instance = N8nWebhookRule(rule)
        return await instance.execute(ctx)

    return RuleExecutionResult(
        rule_id=rule.id,
        success=False,
        error=f"No rule implementation found for '{rule.id}' (analyzer: {analyzer})",
    )
