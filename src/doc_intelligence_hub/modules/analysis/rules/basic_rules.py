"""Built-in basic (threshold/field comparison) analysis rules.

These rules are free and fast — no LLM or external API calls required.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from doc_intelligence_hub.modules.analysis.models import (
    ContextData,
    InsightSeverity,
    InsightType,
    RuleExecutionResult,
)
from doc_intelligence_hub.modules.analysis.rules.base import AnalysisRule, register_rule

logger = logging.getLogger(__name__)


@register_rule("monthly-spend-comparison")
class MonthlySpendComparison(AnalysisRule):
    """Compare a statement's total amount to the rolling average for the series."""

    async def execute(self, context: ContextData) -> RuleExecutionResult:
        doc = context.current_document
        history = context.series_history

        if not doc:
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="No current document")

        # Extract the current amount
        current_amount = _get_amount(doc)
        if current_amount is None:
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="No amount field found")

        # Calculate rolling average from history (excluding current doc)
        window = self.get_param("comparison_window", 3)
        current_id = doc.get("id")
        history_amounts = [
            _get_amount(h) for h in history
            if _get_amount(h) is not None and h.get("id") != current_id
        ]

        if len(history_amounts) < 2:
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="Insufficient history for comparison")

        avg_amounts = history_amounts[:window]
        avg = sum(avg_amounts) / len(avg_amounts) if avg_amounts else 0

        pct_change = ((current_amount - avg) / avg * 100) if avg > 0 else 0
        direction = "above" if pct_change > 0 else "below"

        correspondent = doc.get("correspondent", {})
        corr_name = correspondent.get("name", "") if isinstance(correspondent, dict) else str(correspondent or "")
        period = _extract_period(doc)
        series_id = context.series_info.get("id") if context.series_info else None

        return RuleExecutionResult(
            rule_id=self.config.id,
            success=True,
            insight_type=InsightType.COMPARISON,
            title=f"{corr_name or 'Statement'}: {period or 'Current'} spend {abs(pct_change):.0f}% {direction} average",
            summary=f"Total ${current_amount:,.2f} vs ${avg:,.2f} rolling {window}-month average ({pct_change:+.1f}%)",
            detail={
                "current_amount": current_amount,
                "average_amount": round(avg, 2),
                "comparison_window": window,
                "pct_change": round(pct_change, 1),
                "direction": direction,
                "history_amounts": [round(a, 2) for a in history_amounts],
            },
            highlight_data={
                "total_amount": f"${current_amount:,.2f}",
                "pct_change": f"{pct_change:+.1f}%",
                "average": f"${avg:,.2f}",
            },
            suggested_severity=InsightSeverity.INFO,
            metric_values={"pct_change": abs(pct_change), "current_amount": current_amount, "average_amount": avg},
            series_id=series_id,
            document_ids=[doc["id"]] if "id" in doc else [],
            correspondent=corr_name or None,
            period=period,
        )


@register_rule("eob-match-review")
class EobMatchReview(AnalysisRule):
    """Flag EOB matches below a confidence threshold for human review."""

    async def execute(self, context: ContextData) -> RuleExecutionResult:
        doc = context.current_document
        matches = context.related_matches

        if not doc:
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="No current document")

        threshold = self.get_param("confidence_threshold", 75)

        if not matches:
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="No matches found for document")

        # Find the best match
        best_match = max(matches, key=lambda m: m.get("score", 0))
        score = best_match.get("score", 0)

        if score >= threshold:
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="Match confidence above threshold")

        severity = InsightSeverity.WARNING if score < 50 else InsightSeverity.NOTICE

        return RuleExecutionResult(
            rule_id=self.config.id,
            success=True,
            insight_type=InsightType.COMPLIANCE,
            title=f"Low-confidence EOB match ({score}%)",
            summary=f"EOB document #{doc.get('id', '?')} matched with {score}% confidence (threshold: {threshold}%)",
            detail={
                "score": score,
                "threshold": threshold,
                "match": best_match,
                "candidate_count": len(matches),
            },
            suggested_severity=severity,
            metric_values={"score": score, "threshold": threshold},
            document_ids=[doc["id"]] if "id" in doc else [],
        )


@register_rule("series-anomaly")
class SeriesAnomaly(AnalysisRule):
    """Detect anomalies in statement series grouping."""

    async def execute(self, context: ContextData) -> RuleExecutionResult:
        doc = context.current_document
        series_info = context.series_info

        if not doc:
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="No current document")

        if not series_info:
            # Document not assigned to any series — that's an anomaly
            return RuleExecutionResult(
                rule_id=self.config.id,
                success=True,
                insight_type=InsightType.ANOMALY,
                title=f"Ungrouped document #{doc.get('id', '?')}",
                summary="Document does not belong to any recognized statement series",
                detail={"document_id": doc.get("id"), "reason": "no_series_match"},
                suggested_severity=InsightSeverity.NOTICE,
                document_ids=[doc["id"]] if "id" in doc else [],
            )

        # Check for frequency anomaly
        history = context.series_history
        if len(history) >= 3:
            # Check if intervals between documents are consistent
            dates = []
            for h in history:
                d = h.get("created", h.get("created_at", h.get("added")))
                if d:
                    try:
                        if isinstance(d, str):
                            dates.append(datetime.fromisoformat(d.replace("Z", "+00:00")))
                        elif isinstance(d, datetime):
                            dates.append(d)
                    except ValueError:
                        pass

            if len(dates) >= 3:
                intervals = [(dates[i] - dates[i + 1]).days for i in range(len(dates) - 1)]
                avg_interval = sum(intervals) / len(intervals)
                if avg_interval > 0:
                    latest_interval = intervals[0]
                    deviation = abs(latest_interval - avg_interval) / avg_interval * 100
                    if deviation > 50:  # More than 50% deviation from normal interval
                        return RuleExecutionResult(
                            rule_id=self.config.id,
                            success=True,
                            insight_type=InsightType.ANOMALY,
                            title=f"Unusual timing for {series_info.get('name', 'series')}",
                            summary=f"Document arrived {latest_interval} days after previous (avg: {avg_interval:.0f} days)",
                            detail={
                                "latest_interval_days": latest_interval,
                                "average_interval_days": round(avg_interval, 1),
                                "deviation_pct": round(deviation, 1),
                            },
                            suggested_severity=InsightSeverity.NOTICE,
                            series_id=series_info.get("id"),
                            document_ids=[doc["id"]] if "id" in doc else [],
                        )

        return RuleExecutionResult(rule_id=self.config.id, success=False, error="No anomaly detected")


@register_rule("missing-statement")
class MissingStatement(AnalysisRule):
    """Detect missing expected statements based on series recurrence patterns."""

    async def execute(self, context: ContextData) -> RuleExecutionResult:
        series_info = context.series_info

        if not series_info:
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="No series info")

        recurrence = series_info.get("recurrence", "monthly")
        last_seen = series_info.get("last_seen") or series_info.get("last_document_date")

        if not last_seen:
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="No last seen date")

        try:
            if isinstance(last_seen, str):
                last_date = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            else:
                last_date = last_seen
        except (ValueError, TypeError):
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="Invalid last seen date")

        # Calculate expected interval
        interval_days = {"weekly": 7, "biweekly": 14, "monthly": 30, "quarterly": 90, "yearly": 365}
        expected_days = interval_days.get(recurrence, 30)
        grace_days = self.get_param("grace_days", 7)

        now = datetime.now(last_date.tzinfo) if last_date.tzinfo else datetime.now()
        days_since = (now - last_date).days

        if days_since <= expected_days + grace_days:
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="Statement not yet overdue")

        days_late = days_since - expected_days
        series_name = series_info.get("name", series_info.get("provider_name", "Unknown"))
        series_id = series_info.get("id") or series_info.get("series_id")

        severity = InsightSeverity.CRITICAL if days_late > 30 else InsightSeverity.WARNING

        # Generate a stable period key for deduplication — the expected arrival month
        expected_arrival = last_date + timedelta(days=expected_days)
        period = expected_arrival.strftime("%b %Y")

        return RuleExecutionResult(
            rule_id=self.config.id,
            success=True,
            insight_type=InsightType.COMPLIANCE,
            title=f"Missing {recurrence} statement: {series_name}",
            summary=f"Expected every {expected_days} days, last seen {days_since} days ago ({days_late} days late)",
            detail={
                "series_name": series_name,
                "recurrence": recurrence,
                "last_seen": last_date.isoformat(),
                "days_since": days_since,
                "days_late": days_late,
                "expected_interval_days": expected_days,
                "grace_days": grace_days,
            },
            suggested_severity=severity,
            metric_values={"days_late": float(days_late), "days_since": float(days_since)},
            series_id=series_id,
            correspondent=series_name,
            period=period,
        )


@register_rule("statement-received")
class StatementReceived(AnalysisRule):
    """Confirm that a statement arrived on time."""

    async def execute(self, context: ContextData) -> RuleExecutionResult:
        doc = context.current_document
        series_info = context.series_info

        if not doc:
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="No current document")

        correspondent = doc.get("correspondent", {})
        corr_name = correspondent.get("name", "") if isinstance(correspondent, dict) else str(correspondent or "")
        period = _extract_period(doc)

        # Check if it's within expected timing
        timing_note = "on time"
        if series_info:
            expected_day = series_info.get("expected_day")
            if expected_day:
                doc_date = doc.get("created", doc.get("added"))
                if doc_date:
                    try:
                        if isinstance(doc_date, str):
                            dt = datetime.fromisoformat(doc_date.replace("Z", "+00:00"))
                        else:
                            dt = doc_date
                        day_diff = abs(dt.day - int(expected_day))
                        timing_note = f"on time (day {dt.day}, expected day {expected_day})"
                        if day_diff > 5:
                            timing_note = f"late (day {dt.day}, expected day {expected_day})"
                    except (ValueError, TypeError):
                        pass

        current_amount = _get_amount(doc)
        amount_note = f"Amount: ${current_amount:,.2f}" if current_amount else ""

        return RuleExecutionResult(
            rule_id=self.config.id,
            success=True,
            insight_type=InsightType.COMPLIANCE,
            title=f"{corr_name or 'Statement'} — {period or 'Current'} Received",
            summary=f"{timing_note}. {amount_note}".strip(),
            detail={
                "timing": timing_note,
                "amount": current_amount,
                "correspondent": corr_name,
            },
            highlight_data={"amount": f"${current_amount:,.2f}" if current_amount else None},
            suggested_severity=InsightSeverity.INFO,
            document_ids=[doc["id"]] if "id" in doc else [],
            correspondent=corr_name or None,
            period=period,
        )


@register_rule("spend-spike")
class SpendSpike(AnalysisRule):
    """Detect sudden spend increases above a configurable threshold."""

    async def execute(self, context: ContextData) -> RuleExecutionResult:
        doc = context.current_document
        history = context.series_history

        if not doc:
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="No current document")

        current_amount = _get_amount(doc)
        if current_amount is None:
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="No amount field found")

        current_id = doc.get("id")
        history_amounts = [
            _get_amount(h) for h in history
            if _get_amount(h) is not None and h.get("id") != current_id
        ]
        if not history_amounts:
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="No history for comparison")

        avg = sum(history_amounts) / len(history_amounts)
        spike_threshold = self.get_param("spike_threshold_pct", 30)

        if avg == 0:
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="Average is zero")

        pct_change = (current_amount - avg) / avg * 100

        if pct_change < spike_threshold:
            return RuleExecutionResult(rule_id=self.config.id, success=False, error="No spike detected")

        correspondent = doc.get("correspondent", {})
        corr_name = correspondent.get("name", "") if isinstance(correspondent, dict) else str(correspondent or "")

        severity = InsightSeverity.CRITICAL if pct_change > 100 else InsightSeverity.WARNING

        return RuleExecutionResult(
            rule_id=self.config.id,
            success=True,
            insight_type=InsightType.ANOMALY,
            title=f"Spend spike: {corr_name or 'Account'} +{pct_change:.0f}%",
            summary=f"${current_amount:,.2f} vs ${avg:,.2f} average — {pct_change:.0f}% increase",
            detail={
                "current_amount": current_amount,
                "average_amount": round(avg, 2),
                "pct_change": round(pct_change, 1),
                "spike_threshold_pct": spike_threshold,
            },
            suggested_severity=severity,
            metric_values={"pct_change": pct_change, "current_amount": current_amount},
            document_ids=[doc["id"]] if "id" in doc else [],
            correspondent=corr_name or None,
            period=_extract_period(doc),
        )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _get_amount(doc: dict[str, Any]) -> float | None:
    """Extract a monetary amount from a document dict."""
    for key in ("total_amount", "amount", "closing_balance", "amount_due"):
        val = doc.get(key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass

    # Check custom_fields
    custom = doc.get("custom_fields", {})
    if isinstance(custom, dict):
        for key in ("total_amount", "amount", "closing_balance"):
            val = custom.get(key)
            if val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    pass

    return None


def _extract_period(doc: dict[str, Any]) -> str | None:
    """Extract a human-readable period label from a document."""
    period = doc.get("statement_period") or doc.get("period")
    if period:
        return str(period)

    # Try to derive from date
    for key in ("created", "added", "created_at"):
        val = doc.get(key)
        if val:
            try:
                if isinstance(val, str):
                    dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                elif isinstance(val, datetime):
                    dt = val
                else:
                    continue
                return dt.strftime("%b %Y")
            except (ValueError, TypeError):
                pass

    return None
