"""LLM-powered analysis rules.

These rules use the AI gateway (via core/llm.py) for fuzzy analysis,
document classification, and coverage analysis. Cost: ~$0.02-0.05 per analysis.
"""

from __future__ import annotations

import logging
from typing import Any

from doc_intelligence_hub.modules.analysis.models import (
    ContextData,
    InsightSeverity,
    InsightType,
    RuleExecutionResult,
)
from doc_intelligence_hub.modules.analysis.rules.base import AnalysisRule, register_rule

logger = logging.getLogger(__name__)


@register_rule("document-classification")
class DocumentClassification(AnalysisRule):
    """Classify unknown or ambiguous documents using LLM analysis."""

    async def execute(self, context: ContextData) -> RuleExecutionResult:
        doc = context.current_document
        if not doc:
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="No current document")

        # Build classification prompt
        doc_title = doc.get("title", "")
        doc_content = doc.get("content", "")[:2000]  # Limit context size
        doc_tags = doc.get("tags", [])
        correspondent = doc.get("correspondent", {})
        corr_name = correspondent.get("name", "") if isinstance(correspondent, dict) else str(correspondent or "")

        categories = self.get_param("categories", [
            "statement", "bill", "eob", "receipt", "letter", "notice", "contract", "other"
        ])

        prompt = (
            f"Classify this document into one of these categories: {', '.join(categories)}\n\n"
            f"Title: {doc_title}\n"
            f"Correspondent: {corr_name}\n"
            f"Tags: {', '.join(str(t) for t in doc_tags) if doc_tags else 'none'}\n"
            f"Content preview:\n{doc_content}\n\n"
            "Respond with JSON: {\"category\": \"<category>\", \"confidence\": <0-100>, \"reasoning\": \"<brief explanation>\"}"
        )

        try:
            from doc_intelligence_hub.core.llm import chat_json

            result = await chat_json(
                prompt=prompt,
                system="You are a document classification expert. Analyze the document and classify it accurately.",
                temperature=0.1,
                max_tokens=200,
            )

            category = result.get("category", "other")
            confidence = result.get("confidence", 50)
            reasoning = result.get("reasoning", "")

            severity = InsightSeverity.INFO if confidence >= 80 else InsightSeverity.NOTICE

            return RuleExecutionResult(
                rule_id=self.config.id,
                success=True,
                insight_type=InsightType.EXTRACTION,
                title=f"Document classified: {category} ({confidence}% confidence)",
                summary=f"'{doc_title}' classified as {category}. {reasoning}",
                detail={
                    "category": category,
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "document_title": doc_title,
                    "correspondent": corr_name,
                },
                suggested_severity=severity,
                metric_values={"confidence": float(confidence)},
                document_ids=[doc["id"]] if "id" in doc else [],
                correspondent=corr_name or None,
            )

        except Exception as exc:
            logger.error("LLM classification failed: %s", exc)
            return RuleExecutionResult(rule_id=self.config.id, success=False, error=f"LLM call failed: {exc}")


@register_rule("coverage-analysis")
class CoverageAnalysis(AnalysisRule):
    """Analyze insurance coverage patterns from EOB data using LLM."""

    async def execute(self, context: ContextData) -> RuleExecutionResult:
        history = context.series_history
        series_info = context.series_info

        if len(history) < 2:
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="Insufficient history for analysis")

        # Build analysis prompt from EOB history
        eob_summaries = []
        for h in history[:10]:  # Last 10 documents
            summary = {
                "date": h.get("created", h.get("added", "")),
                "amount": h.get("total_amount") or h.get("amount"),
                "provider": h.get("correspondent", {}).get("name", "") if isinstance(h.get("correspondent"), dict) else str(h.get("correspondent", "")),
            }
            eob_summaries.append(summary)

        series_name = ""
        if series_info:
            series_name = series_info.get("name", series_info.get("provider_name", ""))

        prompt = (
            f"Analyze insurance coverage patterns for {series_name or 'this account'}.\n\n"
            f"Recent EOB/claim history (newest first):\n"
        )
        for s in eob_summaries:
            prompt += f"- Date: {s['date']}, Amount: ${s['amount']}, Provider: {s['provider']}\n"

        prompt += (
            "\nProvide analysis as JSON:\n"
            "{\n"
            "  \"trend\": \"increasing|decreasing|stable\",\n"
            "  \"avg_claim_amount\": <number>,\n"
            "  \"notable_patterns\": [\"pattern1\", \"pattern2\"],\n"
            "  \"recommendations\": [\"rec1\", \"rec2\"],\n"
            "  \"summary\": \"<one-line summary>\"\n"
            "}"
        )

        try:
            from doc_intelligence_hub.core.llm import chat_json

            result = await chat_json(
                prompt=prompt,
                system="You are a healthcare cost analyst. Analyze EOB/claim patterns and provide actionable insights.",
                temperature=0.2,
                max_tokens=500,
            )

            trend = result.get("trend", "stable")
            summary_text = result.get("summary", "Coverage analysis complete")
            series_id = series_info.get("id") if series_info else None

            return RuleExecutionResult(
                rule_id=self.config.id,
                success=True,
                insight_type=InsightType.TREND,
                title=f"{series_name or 'Insurance'} Coverage Analysis — {trend.title()} trend",
                summary=summary_text,
                detail={
                    "trend": trend,
                    "avg_claim_amount": result.get("avg_claim_amount"),
                    "notable_patterns": result.get("notable_patterns", []),
                    "recommendations": result.get("recommendations", []),
                    "eob_count": len(eob_summaries),
                },
                highlight_data={
                    "trend": trend,
                    "avg_claim": f"${result.get('avg_claim_amount', 0):,.2f}",
                },
                suggested_severity=InsightSeverity.INFO,
                series_id=series_id,
                correspondent=series_name or None,
            )

        except Exception as exc:
            logger.error("LLM coverage analysis failed: %s", exc)
            return RuleExecutionResult(rule_id=self.config.id, success=False, error=f"LLM call failed: {exc}")
