"""SQLite persistence for the Analysis Engine — insights and insight history.

Mirrors the pattern used by the Triage module (SQLAlchemy ORM + singleton engine).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


_DEFAULT_DB_URL = "sqlite:///data/analysis.db"
_db_url: str = _DEFAULT_DB_URL


class Base(DeclarativeBase):
    pass


class Insight(Base):
    """A single insight produced by a rule execution."""

    __tablename__ = "insights"
    __table_args__ = (
        Index("idx_insights_route", "route", "status"),
        Index("idx_insights_series", "series_id"),
        Index("idx_insights_rule", "rule_id", "created_at"),
        Index("idx_insights_created", "created_at"),
    )

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    rule_id = Column(String, nullable=False)
    rule_name = Column(String, nullable=False)

    # Classification
    insight_type = Column(
        String, nullable=False
    )  # comparison, anomaly, trend, compliance, extraction
    route = Column(String, nullable=False)  # informational, actionable
    severity = Column(String, default="info")  # info, notice, warning, critical

    # Content
    title = Column(String, nullable=False)
    summary = Column(Text, nullable=True)
    detail_json = Column("detail", Text, nullable=False, default="{}")
    highlight_data_json = Column("highlight_data", Text, nullable=True)

    # Context
    series_id = Column(String, nullable=True)
    document_ids_json = Column(
        "document_ids", Text, nullable=True
    )  # JSON array of Paperless doc IDs
    correspondent = Column(String, nullable=True)

    # Lifecycle
    status = Column(String, default="new")  # new, viewed, acknowledged, archived, superseded
    triage_item_id = Column(String, nullable=True)
    mc_alert_id = Column(String, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    viewed_at = Column(DateTime, nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)

    # Recurrence
    period = Column(String, nullable=True)  # 'Jun 2024', 'Q2 2024', etc.
    supersedes_id = Column(String, nullable=True)


class InsightHistory(Base):
    """Historical metric data for charting trends."""

    __tablename__ = "insight_history"
    __table_args__ = (Index("idx_history_series", "series_id", "metric_name", "period"),)

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    rule_id = Column(String, nullable=False)
    series_id = Column(String, nullable=True)
    period = Column(String, nullable=False)  # 'Jan 2024', 'Feb 2024', etc.
    metric_name = Column(String, nullable=False)  # 'total_amount', 'closing_balance', etc.
    metric_value = Column(Float, nullable=True)
    metadata_json = Column("metadata", Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


class RuleState(Base):
    """Persisted runtime state for rules — overrides, custom rules, run history."""

    __tablename__ = "rule_states"
    __table_args__ = (Index("idx_rule_states_source", "source"),)

    id = Column(String, primary_key=True)  # Same as rule_id
    enabled = Column(Integer, default=1)  # 1 = enabled, 0 = disabled
    params_json = Column("params", Text, nullable=True)
    routing_json = Column("routing", Text, nullable=True)
    display_json = Column("display", Text, nullable=True)
    source = Column(String, default="builtin")  # builtin, yaml, custom

    # Full rule definition (only for custom rules)
    definition_json = Column("definition", Text, nullable=True)

    last_run_at = Column(DateTime, nullable=True)
    last_run_status = Column(String, nullable=True)
    insight_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC))


# ------------------------------------------------------------------
# Engine / session helpers
# ------------------------------------------------------------------

_engine = None


def set_db_url(url: str) -> None:
    global _db_url, _engine
    _db_url = url
    _engine = None


def configure(url: str) -> None:
    """Configure the database URL (alias for set_db_url)."""
    set_db_url(url)


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
    """Create all tables if they don't exist."""
    engine = get_engine()
    Base.metadata.create_all(engine)


# ------------------------------------------------------------------
# Insight CRUD
# ------------------------------------------------------------------


def create_insight(
    *,
    rule_id: str,
    rule_name: str,
    insight_type: str,
    route: str,
    severity: str = "info",
    title: str,
    summary: str | None = None,
    detail: dict[str, Any] | None = None,
    highlight_data: dict[str, Any] | None = None,
    series_id: str | None = None,
    document_ids: list[int] | None = None,
    correspondent: str | None = None,
    period: str | None = None,
    supersedes_id: str | None = None,
) -> dict[str, Any]:
    """Create a new insight record."""
    session = get_session()
    try:
        insight = Insight(
            rule_id=rule_id,
            rule_name=rule_name,
            insight_type=insight_type,
            route=route,
            severity=severity,
            title=title,
            summary=summary,
            detail_json=json.dumps(detail or {}),
            highlight_data_json=json.dumps(highlight_data) if highlight_data else None,
            series_id=series_id,
            document_ids_json=json.dumps(document_ids or []),
            correspondent=correspondent,
            period=period,
            supersedes_id=supersedes_id,
        )
        session.add(insight)
        session.commit()
        session.refresh(insight)
        return _insight_to_dict(insight)
    finally:
        session.close()


def get_insight(insight_id: str) -> dict[str, Any] | None:
    """Get a single insight by ID."""
    session = get_session()
    try:
        row = session.query(Insight).filter(Insight.id == insight_id).first()
        return _insight_to_dict(row) if row else None
    finally:
        session.close()


def list_insights(
    *,
    route: str | None = None,
    status: str | None = None,
    rule_id: str | None = None,
    series_id: str | None = None,
    severity: str | None = None,
    insight_type: str | None = None,
    correspondent: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """List insights with optional filters. Returns (items, total_count)."""
    session = get_session()
    try:
        query = session.query(Insight)

        # Exclude superseded by default
        if status:
            query = query.filter(Insight.status == status)
        else:
            query = query.filter(Insight.status != "superseded")

        if route:
            query = query.filter(Insight.route == route)
        if rule_id:
            query = query.filter(Insight.rule_id == rule_id)
        if series_id:
            query = query.filter(Insight.series_id == series_id)
        if severity:
            query = query.filter(Insight.severity == severity)
        if insight_type:
            query = query.filter(Insight.insight_type == insight_type)
        if correspondent:
            query = query.filter(Insight.correspondent == correspondent)
        if since:
            query = query.filter(Insight.created_at >= since)
        if until:
            query = query.filter(Insight.created_at <= until)

        total = query.count()
        items = query.order_by(Insight.created_at.desc()).offset(offset).limit(limit).all()
        return [_insight_to_dict(i) for i in items], total
    finally:
        session.close()


def update_insight_status(insight_id: str, status: str) -> dict[str, Any] | None:
    """Update an insight's lifecycle status."""
    session = get_session()
    try:
        row = session.query(Insight).filter(Insight.id == insight_id).first()
        if not row:
            return None

        row.status = status
        now = datetime.now(UTC)
        if status == "viewed":
            row.viewed_at = now
        elif status == "acknowledged":
            row.acknowledged_at = now

        session.commit()
        return _insight_to_dict(row)
    finally:
        session.close()


def bulk_update_insight_status(insight_ids: list[str], status: str) -> int:
    """Bulk update insight statuses. Returns count of affected rows."""
    session = get_session()
    try:
        rows = session.query(Insight).filter(Insight.id.in_(insight_ids)).all()
        now = datetime.now(UTC)
        for row in rows:
            row.status = status
            if status == "viewed":
                row.viewed_at = now
            elif status == "acknowledged":
                row.acknowledged_at = now
        session.commit()
        return len(rows)
    finally:
        session.close()


def supersede_insight(rule_id: str, series_id: str | None, period: str | None) -> str | None:
    """Mark previous insight for same rule+series+period as superseded.

    Returns the ID of the superseded insight, or None.
    """
    if not period:
        return None

    session = get_session()
    try:
        query = session.query(Insight).filter(
            Insight.rule_id == rule_id,
            Insight.period == period,
            Insight.status != "superseded",
        )
        if series_id:
            query = query.filter(Insight.series_id == series_id)

        existing = query.order_by(Insight.created_at.desc()).first()
        if existing:
            existing.status = "superseded"
            session.commit()
            return existing.id
        return None
    finally:
        session.close()


def get_insight_summary() -> dict[str, Any]:
    """Get aggregate insight stats for the dashboard."""
    session = get_session()
    try:
        # Exclude superseded
        base = session.query(Insight).filter(Insight.status != "superseded")

        total = base.count()
        new_count = base.filter(Insight.status == "new").count()

        type_rows = (
            base.with_entities(Insight.insight_type, func.count(Insight.id))
            .group_by(Insight.insight_type)
            .all()
        )
        severity_rows = (
            base.with_entities(Insight.severity, func.count(Insight.id))
            .group_by(Insight.severity)
            .all()
        )
        route_rows = (
            base.with_entities(Insight.route, func.count(Insight.id)).group_by(Insight.route).all()
        )

        return {
            "total": total,
            "new": new_count,
            "by_type": {t: c for t, c in type_rows},
            "by_severity": {s: c for s, c in severity_rows},
            "by_route": {r: c for r, c in route_rows},
        }
    finally:
        session.close()


def get_mc_alerts() -> list[dict[str, Any]]:
    """Get insights flagged for Mission Control (mc_alert=true, status=new)."""
    session = get_session()
    try:
        rows = (
            session.query(Insight)
            .filter(
                Insight.mc_alert_id.isnot(None),
                Insight.status == "new",
            )
            .order_by(Insight.created_at.desc())
            .all()
        )
        return [_insight_to_dict(r) for r in rows]
    finally:
        session.close()


def set_insight_triage_id(insight_id: str, triage_item_id: str) -> None:
    """Link an insight to its triage queue item."""
    session = get_session()
    try:
        row = session.query(Insight).filter(Insight.id == insight_id).first()
        if row:
            row.triage_item_id = triage_item_id
            session.commit()
    finally:
        session.close()


def set_insight_mc_alert_id(insight_id: str, mc_alert_id: str) -> None:
    """Link an insight to its MC alert."""
    session = get_session()
    try:
        row = session.query(Insight).filter(Insight.id == insight_id).first()
        if row:
            row.mc_alert_id = mc_alert_id
            session.commit()
    finally:
        session.close()


# ------------------------------------------------------------------
# Insight History CRUD
# ------------------------------------------------------------------


def create_history_entry(
    *,
    rule_id: str,
    series_id: str | None = None,
    period: str,
    metric_name: str,
    metric_value: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a history data point for trend charting.

    Upserts on (rule_id, series_id, period, metric_name) to avoid
    duplicate entries on reruns.
    """
    session = get_session()
    try:
        # Check for existing entry with same key
        query = session.query(InsightHistory).filter(
            InsightHistory.rule_id == rule_id,
            InsightHistory.metric_name == metric_name,
            InsightHistory.period == period,
        )
        if series_id:
            query = query.filter(InsightHistory.series_id == series_id)
        else:
            query = query.filter(InsightHistory.series_id.is_(None))

        existing = query.first()
        if existing:
            existing.metric_value = metric_value
            if metadata:
                existing.metadata_json = json.dumps(metadata)
            session.commit()
            session.refresh(existing)
            return _history_to_dict(existing)

        entry = InsightHistory(
            rule_id=rule_id,
            series_id=series_id,
            period=period,
            metric_name=metric_name,
            metric_value=metric_value,
            metadata_json=json.dumps(metadata) if metadata else None,
        )
        session.add(entry)
        session.commit()
        session.refresh(entry)
        return _history_to_dict(entry)
    finally:
        session.close()


def get_history_for_series(
    series_id: str,
    metric_name: str | None = None,
    limit: int = 24,
) -> list[dict[str, Any]]:
    """Get history entries for a series, optionally filtered by metric."""
    session = get_session()
    try:
        query = session.query(InsightHistory).filter(InsightHistory.series_id == series_id)
        if metric_name:
            query = query.filter(InsightHistory.metric_name == metric_name)
        rows = query.order_by(InsightHistory.created_at.desc()).limit(limit).all()
        return [_history_to_dict(r) for r in rows]
    finally:
        session.close()


# ------------------------------------------------------------------
# Rule State CRUD
# ------------------------------------------------------------------


def get_rule_state(rule_id: str) -> dict[str, Any] | None:
    """Get persisted state for a rule."""
    session = get_session()
    try:
        row = session.query(RuleState).filter(RuleState.id == rule_id).first()
        return _rule_state_to_dict(row) if row else None
    finally:
        session.close()


def upsert_rule_state(
    rule_id: str,
    *,
    enabled: bool | None = None,
    params: dict[str, Any] | None = None,
    routing: dict[str, Any] | None = None,
    display: dict[str, Any] | None = None,
    source: str | None = None,
    definition: dict[str, Any] | None = None,
    last_run_at: datetime | None = None,
    last_run_status: str | None = None,
    insight_count_increment: int = 0,
) -> dict[str, Any]:
    """Create or update persisted rule state."""
    session = get_session()
    try:
        row = session.query(RuleState).filter(RuleState.id == rule_id).first()
        if not row:
            row = RuleState(id=rule_id)
            session.add(row)

        if enabled is not None:
            row.enabled = 1 if enabled else 0
        if params is not None:
            row.params_json = json.dumps(params)
        if routing is not None:
            row.routing_json = json.dumps(routing)
        if display is not None:
            row.display_json = json.dumps(display)
        if source is not None:
            row.source = source
        if definition is not None:
            row.definition_json = json.dumps(definition)
        if last_run_at is not None:
            row.last_run_at = last_run_at
        if last_run_status is not None:
            row.last_run_status = last_run_status
        if insight_count_increment:
            row.insight_count = (row.insight_count or 0) + insight_count_increment

        row.updated_at = datetime.now(UTC)
        session.commit()
        session.refresh(row)
        return _rule_state_to_dict(row)
    finally:
        session.close()


def delete_rule_state(rule_id: str) -> bool:
    """Delete a custom rule's state. Returns True if deleted."""
    session = get_session()
    try:
        row = session.query(RuleState).filter(RuleState.id == rule_id).first()
        if row:
            session.delete(row)
            session.commit()
            return True
        return False
    finally:
        session.close()


def list_rule_states() -> list[dict[str, Any]]:
    """List all persisted rule states."""
    session = get_session()
    try:
        rows = session.query(RuleState).all()
        return [_rule_state_to_dict(r) for r in rows]
    finally:
        session.close()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _parse_json(text: str | None, fallback: Any = None) -> Any:
    if text is None:
        return fallback
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return fallback


def _insight_to_dict(row: Insight) -> dict[str, Any]:
    return {
        "id": row.id,
        "rule_id": row.rule_id,
        "rule_name": row.rule_name,
        "insight_type": row.insight_type,
        "route": row.route,
        "severity": row.severity,
        "title": row.title,
        "summary": row.summary,
        "detail": _parse_json(row.detail_json, {}),
        "highlight_data": _parse_json(row.highlight_data_json),
        "series_id": row.series_id,
        "document_ids": _parse_json(row.document_ids_json, []),
        "correspondent": row.correspondent,
        "status": row.status,
        "triage_item_id": row.triage_item_id,
        "mc_alert_id": row.mc_alert_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "viewed_at": row.viewed_at.isoformat() if row.viewed_at else None,
        "acknowledged_at": row.acknowledged_at.isoformat() if row.acknowledged_at else None,
        "period": row.period,
        "supersedes_id": row.supersedes_id,
    }


def _history_to_dict(row: InsightHistory) -> dict[str, Any]:
    return {
        "id": row.id,
        "rule_id": row.rule_id,
        "series_id": row.series_id,
        "period": row.period,
        "metric_name": row.metric_name,
        "metric_value": row.metric_value,
        "metadata": _parse_json(row.metadata_json),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _rule_state_to_dict(row: RuleState) -> dict[str, Any]:
    return {
        "id": row.id,
        "enabled": bool(row.enabled),
        "params": _parse_json(row.params_json),
        "routing": _parse_json(row.routing_json),
        "display": _parse_json(row.display_json),
        "source": row.source,
        "definition": _parse_json(row.definition_json),
        "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
        "last_run_status": row.last_run_status,
        "insight_count": row.insight_count or 0,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
