"""Unified alerts system for Document Intelligence Hub.

Provides a shared SQLAlchemy-backed alerts table that all modules
(Statements, EOB Matching, Action Queue) can write to via emitter
functions. Alerts are surfaced through /api/insights/alerts endpoints
and consumed by Mission Control's connector.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)

_DEFAULT_DB_URL = "sqlite:///data/alerts.db"
_db_url: str = _DEFAULT_DB_URL


class Base(DeclarativeBase):
    pass


class Alert(Base):
    """Unified alert record emitted by any DI Hub module."""

    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_type = Column(String, nullable=False, index=True)
    severity = Column(String, nullable=False, index=True)  # critical, high, medium, low, info
    module = Column(String, nullable=False, index=True)  # statements, eob, action_queue
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    action_url = Column(String, nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    acknowledged_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)


# ------------------------------------------------------------------
# Engine / session helpers (mirrors eob_matching/database.py pattern)
# ------------------------------------------------------------------

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_db_url, echo=False)
    return _engine


def get_session() -> Session:
    engine = get_engine()
    factory = sessionmaker(bind=engine)
    return factory()


def init_db():
    """Create alert tables if they don't exist."""
    engine = get_engine()
    Base.metadata.create_all(engine)


def configure(database_url: str) -> None:
    """Override the database URL (call before any DB access)."""
    global _db_url, _engine
    _db_url = database_url
    _engine = None


# ------------------------------------------------------------------
# Alert emitter — called by individual modules
# ------------------------------------------------------------------

VALID_SEVERITIES = ("critical", "high", "medium", "low", "info")
VALID_MODULES = ("statements", "eob", "action_queue", "analysis")


def emit_alert(
    *,
    alert_type: str,
    severity: str,
    module: str,
    title: str,
    description: str | None = None,
    action_url: str | None = None,
    metadata: dict[str, Any] | None = None,
    deduplicate: bool = True,
) -> Alert | None:
    """Create a new alert record.

    Args:
        alert_type: Machine-readable type (e.g. 'missing_statement', 'unmatched_eob').
        severity: One of critical, high, medium, low, info.
        module: Source module — statements, eob, or action_queue.
        title: Human-readable short title.
        description: Longer explanation (optional).
        action_url: Deep-link for the user to act on (optional).
        metadata: Extra structured data stored as JSON (optional).
        deduplicate: If True, skip creation when an identical unresolved alert
                     (same type + module + title) already exists.

    Returns:
        The created Alert, or None if deduplicated away.
    """
    if severity not in VALID_SEVERITIES:
        logger.warning("Invalid severity '%s' — defaulting to 'medium'", severity)
        severity = "medium"
    if module not in VALID_MODULES:
        logger.warning("Invalid module '%s' for alert", module)

    init_db()
    db = get_session()
    try:
        if deduplicate:
            existing = (
                db.query(Alert)
                .filter_by(alert_type=alert_type, module=module, title=title)
                .filter(Alert.resolved_at.is_(None))
                .first()
            )
            if existing is not None:
                return None

        alert = Alert(
            alert_type=alert_type,
            severity=severity,
            module=module,
            title=title,
            description=description,
            action_url=action_url,
            metadata_json=json.dumps(metadata) if metadata else None,
        )
        db.add(alert)
        db.commit()
        db.refresh(alert)
        logger.info("Alert emitted: [%s] %s — %s", severity.upper(), module, title)
        return alert
    except Exception:
        db.rollback()
        logger.exception("Failed to emit alert")
        return None
    finally:
        db.close()


# ------------------------------------------------------------------
# Module-specific emitter helpers
# ------------------------------------------------------------------


def emit_statement_alerts(recommendations: list[dict[str, Any]]) -> int:
    """Emit alerts from statement tracker recommendations.

    Args:
        recommendations: List of recommendation dicts with keys:
            provider_name, status, days_late, expected_date, provider_key.

    Returns:
        Number of alerts emitted.
    """
    count = 0
    for rec in recommendations:
        status = rec.get("status", "")
        days_late = rec.get("days_late", 0)
        provider = rec.get("provider_name", "Unknown")

        if status == "missing":
            severity = "high" if days_late > 14 else "medium"
            alert = emit_alert(
                alert_type="missing_statement",
                severity=severity,
                module="statements",
                title=f"Missing statement from {provider}",
                description=f"Expected by {rec.get('expected_date', '?')}, now {days_late} days late.",
                metadata={
                    "provider_key": rec.get("provider_key"),
                    "provider_name": provider,
                    "expected_date": str(rec.get("expected_date", "")),
                    "days_late": days_late,
                },
            )
            if alert:
                count += 1
        elif status == "overdue":
            alert = emit_alert(
                alert_type="statement_gap",
                severity="medium",
                module="statements",
                title=f"Statement gap detected: {provider}",
                description=f"Statement overdue by {days_late} days (expected {rec.get('expected_date', '?')}).",
                metadata={
                    "provider_key": rec.get("provider_key"),
                    "provider_name": provider,
                    "days_late": days_late,
                },
            )
            if alert:
                count += 1
    return count


def emit_eob_alerts(
    *,
    unmatched_eobs: list[dict[str, Any]] | None = None,
    low_confidence_matches: list[dict[str, Any]] | None = None,
    high_confidence_matches: list[dict[str, Any]] | None = None,
) -> int:
    """Emit alerts from EOB matching results.

    Returns:
        Number of alerts emitted.
    """
    count = 0
    for eob in (unmatched_eobs or []):
        provider = eob.get("provider_name") or eob.get("provider") or "Unknown"
        doc_id = eob.get("document_id", "?")
        alert = emit_alert(
            alert_type="unmatched_eob",
            severity="medium",
            module="eob",
            title=f"Unmatched EOB from {provider}",
            description=f"EOB (doc #{doc_id}) has no confirmed bill match.",
            metadata={"document_id": doc_id, "provider": provider},
        )
        if alert:
            count += 1

    for match in (low_confidence_matches or []):
        alert = emit_alert(
            alert_type="low_confidence_match",
            severity="low",
            module="eob",
            title=f"Low-confidence EOB↔Bill match (score {match.get('score', '?')}%)",
            description=f"EOB #{match.get('eob_document_id', '?')} ↔ Bill #{match.get('bill_document_id', '?')}",
            metadata={
                "eob_document_id": match.get("eob_document_id"),
                "bill_document_id": match.get("bill_document_id"),
                "score": match.get("score"),
                "confidence": match.get("confidence"),
            },
        )
        if alert:
            count += 1

    for match in (high_confidence_matches or []):
        eob_id = match.get("eob_document_id", "?")
        bill_id = match.get("bill_document_id", "?")
        score = match.get("score", "?")
        alert = emit_alert(
            alert_type="new_high_confidence_match",
            severity="info",
            module="eob",
            title=f"New high-confidence match: EOB #{eob_id} ↔ Bill #{bill_id}",
            description=f"Auto-matched with {score}% confidence — ready for review.",
            action_url=f"/eob/matches?eob={eob_id}&bill={bill_id}",
            metadata={
                "eob_document_id": eob_id,
                "bill_document_id": bill_id,
                "score": score,
                "confidence": "HIGH",
            },
        )
        if alert:
            count += 1

    return count


# Due-date alert thresholds
_DUE_SOON_DAYS = 7


def check_eob_due_dates(
    bills: list[dict[str, Any]],
    *,
    due_soon_days: int = _DUE_SOON_DAYS,
) -> int:
    """Check bill due dates and emit alerts for approaching or overdue bills.

    Args:
        bills: List of bill dicts with keys: document_id, provider_name,
               due_date (str YYYY-MM-DD or date), payment_status, balance_due.
        due_soon_days: Number of days threshold for "due soon" alerts.

    Returns:
        Number of alerts emitted.
    """
    from datetime import date as date_type

    today = datetime.now(UTC).date()
    count = 0

    for bill in bills:
        # Skip paid bills
        payment_status = (bill.get("payment_status") or "").lower()
        if payment_status == "paid":
            continue

        due_date_raw = bill.get("due_date")
        if not due_date_raw:
            continue

        try:
            if isinstance(due_date_raw, str):
                due = date_type.fromisoformat(due_date_raw)
            else:
                due = due_date_raw
        except (ValueError, TypeError):
            continue

        days_until = (due - today).days
        doc_id = bill.get("document_id", "?")
        provider = bill.get("provider_name") or "Unknown"
        balance = bill.get("balance_due")
        balance_str = f" (${balance:.2f})" if balance is not None else ""

        if days_until < 0:
            # Overdue
            days_late = abs(days_until)
            alert = emit_alert(
                alert_type="bill_overdue",
                severity="high",
                module="eob",
                title=f"Bill overdue: {provider}{balance_str}",
                description=f"Bill (doc #{doc_id}) was due {due.isoformat()}, now {days_late} day(s) late.",
                action_url=f"/eob/bills/{doc_id}",
                metadata={
                    "document_id": doc_id,
                    "provider_name": provider,
                    "due_date": due.isoformat(),
                    "days_late": days_late,
                    "balance_due": balance,
                },
            )
            if alert:
                count += 1
        elif days_until <= due_soon_days:
            # Due soon
            alert = emit_alert(
                alert_type="bill_due_soon",
                severity="medium",
                module="eob",
                title=f"Bill due soon: {provider}{balance_str}",
                description=f"Bill (doc #{doc_id}) is due {due.isoformat()} ({days_until} day(s) remaining).",
                action_url=f"/eob/bills/{doc_id}",
                metadata={
                    "document_id": doc_id,
                    "provider_name": provider,
                    "due_date": due.isoformat(),
                    "days_until_due": days_until,
                    "balance_due": balance,
                },
            )
            if alert:
                count += 1

    return count


def emit_action_queue_alerts(actions: list[dict[str, Any]]) -> int:
    """Emit alerts from action queue items.

    Args:
        actions: List of action dicts with keys:
            id, title, urgency, status, due_date, document_title, action_type.

    Returns:
        Number of alerts emitted.
    """
    count = 0
    today = datetime.now(UTC).date()

    for action in actions:
        urgency = (action.get("urgency") or "low").upper()
        status = action.get("status", "")
        action_title = action.get("title") or action.get("document_title") or "Untitled"
        action_id = action.get("id")

        if status != "pending":
            continue

        # Overdue critical actions → critical alert
        due_date = action.get("due_date")
        is_overdue = False
        if due_date:
            try:
                from datetime import date as date_type
                if isinstance(due_date, str):
                    due = date_type.fromisoformat(due_date)
                else:
                    due = due_date
                is_overdue = due < today
            except (ValueError, TypeError):
                pass

        if urgency == "CRITICAL" and is_overdue:
            alert = emit_alert(
                alert_type="overdue_critical_action",
                severity="critical",
                module="action_queue",
                title=f"Overdue critical action: {action_title}",
                description=f"Action #{action_id} was due {due_date} and requires immediate attention.",
                metadata={
                    "action_id": action_id,
                    "due_date": str(due_date),
                    "action_type": action.get("action_type"),
                },
            )
            if alert:
                count += 1
        elif urgency in ("CRITICAL", "HIGH") and is_overdue:
            alert = emit_alert(
                alert_type="overdue_action",
                severity="high",
                module="action_queue",
                title=f"Overdue action: {action_title}",
                description=f"Action #{action_id} was due {due_date}.",
                metadata={
                    "action_id": action_id,
                    "due_date": str(due_date),
                    "urgency": urgency,
                },
            )
            if alert:
                count += 1
        else:
            alert = emit_alert(
                alert_type="new_action",
                severity="info",
                module="action_queue",
                title=f"New action: {action_title}",
                description=f"Action #{action_id} ({action.get('action_type', 'review')}) is pending.",
                metadata={
                    "action_id": action_id,
                    "action_type": action.get("action_type"),
                    "urgency": urgency,
                },
            )
            if alert:
                count += 1
    return count


def emit_benchmark_alerts(
    *,
    current_results: list[dict[str, Any]],
    previous_results: list[dict[str, Any]] | None = None,
    success_rate_drop_threshold: float = 0.10,
    confidence_drop_threshold: float = 0.10,
    min_success_rate: float = 0.50,
    min_confidence: float = 0.40,
) -> int:
    """Emit alerts from benchmark results, detecting regressions and low performance.

    Args:
        current_results: List of model result dicts with keys:
            model, success_rate, avg_confidence, documents_tested.
        previous_results: Optional list of prior run's model results for comparison.
        success_rate_drop_threshold: Alert if success_rate drops by this amount (0.10 = 10pp).
        confidence_drop_threshold: Alert if avg_confidence drops by this amount.
        min_success_rate: Alert if any model's success_rate is below this.
        min_confidence: Alert if any model's avg_confidence is below this.

    Returns:
        Number of alerts emitted.
    """
    count = 0
    prev_by_model = {}
    if previous_results:
        prev_by_model = {r.get("model"): r for r in previous_results}

    for result in current_results:
        model = result.get("model", "unknown")
        success_rate = result.get("success_rate", 0.0)
        avg_confidence = result.get("avg_confidence", 0.0)
        docs_tested = result.get("documents_tested", 0)

        # Absolute threshold alerts
        if success_rate < min_success_rate and docs_tested > 0:
            alert = emit_alert(
                alert_type="benchmark_low_success_rate",
                severity="high",
                module="eob",
                title=f"Low benchmark success rate: {model}",
                description=(
                    f"Model {model} achieved only {success_rate:.0%} success rate "
                    f"across {docs_tested} documents (threshold: {min_success_rate:.0%})."
                ),
                metadata={
                    "model": model,
                    "success_rate": success_rate,
                    "threshold": min_success_rate,
                    "documents_tested": docs_tested,
                },
            )
            if alert:
                count += 1

        if avg_confidence < min_confidence and success_rate > 0:
            alert = emit_alert(
                alert_type="benchmark_low_confidence",
                severity="medium",
                module="eob",
                title=f"Low benchmark confidence: {model}",
                description=(
                    f"Model {model} averaged {avg_confidence:.3f} confidence "
                    f"(threshold: {min_confidence:.2f})."
                ),
                metadata={
                    "model": model,
                    "avg_confidence": avg_confidence,
                    "threshold": min_confidence,
                },
            )
            if alert:
                count += 1

        # Regression alerts (comparison with previous run)
        prev = prev_by_model.get(model)
        if prev is None:
            continue

        prev_success = prev.get("success_rate", 0.0)
        prev_confidence = prev.get("avg_confidence", 0.0)

        success_drop = prev_success - success_rate
        if success_drop >= success_rate_drop_threshold and prev_success > 0:
            alert = emit_alert(
                alert_type="benchmark_regression_success_rate",
                severity="high",
                module="eob",
                title=f"Benchmark regression: {model} success rate dropped",
                description=(
                    f"Model {model} success rate dropped from {prev_success:.0%} "
                    f"to {success_rate:.0%} (Δ {success_drop:.0%})."
                ),
                metadata={
                    "model": model,
                    "previous_success_rate": prev_success,
                    "current_success_rate": success_rate,
                    "drop": round(success_drop, 4),
                },
                deduplicate=False,
            )
            if alert:
                count += 1

        confidence_drop = prev_confidence - avg_confidence
        if confidence_drop >= confidence_drop_threshold and prev_confidence > 0:
            alert = emit_alert(
                alert_type="benchmark_regression_confidence",
                severity="medium",
                module="eob",
                title=f"Benchmark regression: {model} confidence dropped",
                description=(
                    f"Model {model} avg confidence dropped from {prev_confidence:.3f} "
                    f"to {avg_confidence:.3f} (Δ {confidence_drop:.3f})."
                ),
                metadata={
                    "model": model,
                    "previous_confidence": prev_confidence,
                    "current_confidence": avg_confidence,
                    "drop": round(confidence_drop, 4),
                },
                deduplicate=False,
            )
            if alert:
                count += 1

    return count


# ------------------------------------------------------------------
# Cleanup / retention
# ------------------------------------------------------------------


def cleanup_old_alerts(days: int = 30) -> int:
    """Auto-resolve alerts older than `days` that are still open.

    Returns:
        Number of alerts resolved.
    """
    init_db()
    db = get_session()
    try:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        stale = (
            db.query(Alert)
            .filter(Alert.resolved_at.is_(None))
            .filter(Alert.created_at < cutoff)
            .all()
        )
        now = datetime.now(UTC)
        for alert in stale:
            alert.resolved_at = now
        db.commit()
        if stale:
            logger.info("Auto-resolved %d stale alerts (older than %d days)", len(stale), days)
        return len(stale)
    except Exception:
        db.rollback()
        logger.exception("Failed to cleanup old alerts")
        return 0
    finally:
        db.close()
