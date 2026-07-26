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
