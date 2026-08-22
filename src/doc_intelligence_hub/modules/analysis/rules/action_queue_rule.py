"""Rule action that sends a matched document through the Action Queue pipeline."""

from __future__ import annotations

from doc_intelligence_hub.modules.action_queue.fast_path import trigger_fast_path_analysis
from doc_intelligence_hub.modules.analysis.models import (
    ContextData,
    InsightSeverity,
    InsightType,
    RuleExecutionResult,
)
from doc_intelligence_hub.modules.analysis.rules.base import AnalysisRule, register_rule


@register_rule("action-queue-trigger")
class ActionQueueTriggerRule(AnalysisRule):
    """Run Action Queue analysis immediately for the triggering document."""

    async def execute(self, context: ContextData) -> RuleExecutionResult:
        document = context.current_document
        document_id = document.get("id") if document else None
        if (
            isinstance(document_id, bool)
            or not isinstance(document_id, int)
            or document_id <= 0
        ):
            return RuleExecutionResult(
                rule_id=self.config.id,
                success=False,
                error="A positive Paperless document ID is required",
            )

        dry_run = bool(context.extra.get("_execution_dry_run")) or bool(
            self.get_param("dry_run", False)
        )
        outcome = await trigger_fast_path_analysis(
            document_id,
            force=bool(self.get_param("force", False)),
            dry_run=dry_run,
        )

        if outcome.status == "rejected":
            return RuleExecutionResult(
                rule_id=self.config.id,
                success=False,
                error=outcome.reason or "Fast-path analysis was rejected",
                document_ids=[document_id],
            )

        pipeline_result = outcome.pipeline_result or {}
        already_processed = (
            outcome.status == "completed"
            and pipeline_result.get("processed", 0) == 0
            and pipeline_result.get("skipped", 0) > 0
        )

        if outcome.status == "already_pending":
            title = "Action analysis already queued"
            summary = f"Document #{document_id} already has fast-path analysis pending."
            effective_status = "already_pending"
        elif already_processed:
            title = "Action analysis already completed"
            summary = f"Document #{document_id} was previously processed by the Action Queue."
            effective_status = "already_processed"
        else:
            title = "Action analysis completed"
            summary = f"Document #{document_id} was processed through the Action Queue fast path."
            effective_status = outcome.status

        return RuleExecutionResult(
            rule_id=self.config.id,
            should_route=effective_status not in {"already_pending", "already_processed"},
            insight_type=InsightType.EXTRACTION,
            title=title,
            summary=summary,
            detail={
                "fast_path_status": effective_status,
                "pipeline_result": outcome.pipeline_result,
            },
            suggested_severity=InsightSeverity.INFO,
            document_ids=[document_id],
        )
