"""SQLite persistence for the Triage Queue — queue items, correction events, and CRUD operations.

Mirrors the pattern used by the EOB Matching module (SQLAlchemy ORM + init_db).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Optional

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
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


_DEFAULT_DB_URL = "sqlite:///data/triage.db"
_db_url: str = _DEFAULT_DB_URL


class Base(DeclarativeBase):
    pass


class TriageQueueItem(Base):
    """A single item in the triage queue awaiting human review."""

    __tablename__ = "triage_queue"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    item_type = Column(String, nullable=False)  # 'eob_match_review', 'grouping_anomaly', 'orphan_document'
    priority = Column(Integer, default=50)  # 0-100, higher = more urgent
    status = Column(String, default="pending")  # 'pending', 'deferred', 'resolved', 'dismissed'
    source = Column(String, nullable=False)  # 'auto_flag', 'user_request', 'scheduled_scan'
    target_type = Column(String, nullable=False)  # 'eob_match', 'statement_series', 'document'
    target_id = Column(String, nullable=False)
    reason = Column(Text, nullable=True)
    metadata_json = Column("metadata", Text, nullable=True)  # JSON blob
    deferred_until = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_action = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


class CorrectionEvent(Base):
    """Audit trail entry for all user corrections."""

    __tablename__ = "correction_events"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    event_type = Column(String, nullable=False)
    target_type = Column(String, nullable=False)
    target_id = Column(String, nullable=False)
    payload_json = Column("payload", Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    created_by = Column(String, default="user")


class ExtractionCorrection(Base):
    """Tracks field-level corrections and confirmations for extracted document metadata."""

    __tablename__ = "extraction_corrections"
    __table_args__ = (
        Index("idx_corrections_field", "field_name", "correction_type"),
        Index("idx_corrections_document", "document_id"),
    )

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    document_id = Column(Integer, nullable=False)
    field_name = Column(String, nullable=False)
    original_value = Column(Text, nullable=True)
    corrected_value = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)  # original extraction confidence 0-100
    correction_type = Column(String, nullable=False)  # 'confirmed', 'corrected', 'added'
    source_region_json = Column("source_region", Text, nullable=True)  # bounding box / OCR region JSON
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    created_by = Column(String, default="user")


# ------------------------------------------------------------------
# Engine / session helpers
# ------------------------------------------------------------------

_engine = None


def set_db_url(url: str) -> None:
    global _db_url, _engine
    _db_url = url
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
    """Create all tables if they don't exist."""
    engine = get_engine()
    Base.metadata.create_all(engine)


# ------------------------------------------------------------------
# CRUD operations
# ------------------------------------------------------------------


def list_queue_items(
    *,
    item_type: str | None = None,
    status: str | None = None,
    sort: str = "priority",
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List triage queue items with optional filters."""
    session = get_session()
    try:
        query = session.query(TriageQueueItem)

        if item_type:
            query = query.filter(TriageQueueItem.item_type == item_type)
        if status:
            query = query.filter(TriageQueueItem.status == status)
        else:
            query = query.filter(TriageQueueItem.status == "pending")

        if sort == "created_at":
            query = query.order_by(TriageQueueItem.created_at.asc())
        elif sort == "type":
            query = query.order_by(TriageQueueItem.item_type.asc(), TriageQueueItem.priority.desc())
        else:
            query = query.order_by(TriageQueueItem.priority.desc(), TriageQueueItem.created_at.asc())

        items = query.offset(offset).limit(limit).all()
        return [_item_to_dict(item) for item in items]
    finally:
        session.close()


def get_queue_item(item_id: str) -> dict[str, Any] | None:
    """Get a single triage queue item by ID."""
    session = get_session()
    try:
        item = session.query(TriageQueueItem).filter(TriageQueueItem.id == item_id).first()
        return _item_to_dict(item) if item else None
    finally:
        session.close()


def resolve_queue_item(item_id: str, action: str, payload: dict | None = None) -> dict[str, Any] | None:
    """Resolve a triage queue item with the given action."""
    import json

    session = get_session()
    try:
        item = session.query(TriageQueueItem).filter(TriageQueueItem.id == item_id).first()
        if not item:
            return None

        item.status = "resolved"
        item.resolved_at = datetime.now(UTC)
        item.resolved_action = action

        # Record correction event
        event = CorrectionEvent(
            event_type=f"triage_{action}",
            target_type=item.target_type,
            target_id=item.target_id,
            payload_json=json.dumps(payload or {}),
        )
        session.add(event)
        session.commit()
        return _item_to_dict(item)
    finally:
        session.close()


def defer_queue_item(item_id: str, until: str | None = None) -> dict[str, Any] | None:
    """Defer a triage queue item until a given timestamp."""
    from datetime import timedelta

    session = get_session()
    try:
        item = session.query(TriageQueueItem).filter(TriageQueueItem.id == item_id).first()
        if not item:
            return None

        item.status = "deferred"
        if until:
            item.deferred_until = datetime.fromisoformat(until)
        else:
            item.deferred_until = datetime.now(UTC) + timedelta(days=7)

        session.commit()
        return _item_to_dict(item)
    finally:
        session.close()


def dismiss_queue_item(item_id: str) -> dict[str, Any] | None:
    """Dismiss a triage queue item (mark as not needing review)."""
    session = get_session()
    try:
        item = session.query(TriageQueueItem).filter(TriageQueueItem.id == item_id).first()
        if not item:
            return None

        item.status = "dismissed"
        item.resolved_at = datetime.now(UTC)
        item.resolved_action = "dismissed"
        session.commit()
        return _item_to_dict(item)
    finally:
        session.close()


def get_queue_stats() -> dict[str, Any]:
    """Get counts of queue items by type and status."""
    session = get_session()
    try:
        rows = (
            session.query(
                TriageQueueItem.item_type,
                TriageQueueItem.status,
                func.count(TriageQueueItem.id),
            )
            .group_by(TriageQueueItem.item_type, TriageQueueItem.status)
            .all()
        )

        by_type: dict[str, int] = {}
        by_status: dict[str, int] = {}
        total = 0

        for item_type, status, count in rows:
            by_type[item_type] = by_type.get(item_type, 0) + count
            by_status[status] = by_status.get(status, 0) + count
            total += count

        return {
            "total": total,
            "by_type": by_type,
            "by_status": by_status,
            "pending": by_status.get("pending", 0),
        }
    finally:
        session.close()


def create_queue_item(
    *,
    item_type: str,
    source: str,
    target_type: str,
    target_id: str,
    reason: str | None = None,
    metadata: dict | None = None,
    priority: int = 50,
) -> dict[str, Any]:
    """Create a new triage queue item."""
    import json

    session = get_session()
    try:
        item = TriageQueueItem(
            item_type=item_type,
            source=source,
            target_type=target_type,
            target_id=target_id,
            reason=reason,
            metadata_json=json.dumps(metadata) if metadata else None,
            priority=priority,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return _item_to_dict(item)
    finally:
        session.close()


def undo_resolution(item_id: str) -> dict[str, Any] | None:
    """Undo a resolve/dismiss action — reset item back to pending."""
    session = get_session()
    try:
        item = session.query(TriageQueueItem).filter(TriageQueueItem.id == item_id).first()
        if not item:
            return None

        item.status = "pending"
        item.resolved_at = None
        item.resolved_action = None
        item.deferred_until = None
        session.commit()
        return _item_to_dict(item)
    finally:
        session.close()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _item_to_dict(item: TriageQueueItem) -> dict[str, Any]:
    """Convert a TriageQueueItem to a plain dict for JSON serialization."""
    import json

    metadata = None
    if item.metadata_json:
        try:
            metadata = json.loads(item.metadata_json)
        except (json.JSONDecodeError, TypeError):
            metadata = item.metadata_json

    return {
        "id": item.id,
        "item_type": item.item_type,
        "priority": item.priority,
        "status": item.status,
        "source": item.source,
        "target_type": item.target_type,
        "target_id": item.target_id,
        "reason": item.reason,
        "metadata": metadata,
        "deferred_until": item.deferred_until.isoformat() if item.deferred_until else None,
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
        "resolved_action": item.resolved_action,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _correction_to_dict(c: ExtractionCorrection) -> dict[str, Any]:
    """Convert an ExtractionCorrection to a plain dict for JSON serialization."""
    import json

    source_region = None
    if c.source_region_json:
        try:
            source_region = json.loads(c.source_region_json)
        except (json.JSONDecodeError, TypeError):
            source_region = c.source_region_json

    return {
        "id": c.id,
        "document_id": c.document_id,
        "field_name": c.field_name,
        "original_value": c.original_value,
        "corrected_value": c.corrected_value,
        "confidence": c.confidence,
        "correction_type": c.correction_type,
        "source_region": source_region,
        "notes": c.notes,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "created_by": c.created_by,
    }


# ------------------------------------------------------------------
# Extraction Correction CRUD
# ------------------------------------------------------------------


def create_extraction_correction(
    *,
    document_id: int,
    field_name: str,
    original_value: str | None = None,
    corrected_value: str | None = None,
    confidence: float | None = None,
    correction_type: str,
    source_region: dict | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Create an extraction correction record."""
    import json

    session = get_session()
    try:
        correction = ExtractionCorrection(
            document_id=document_id,
            field_name=field_name,
            original_value=original_value,
            corrected_value=corrected_value,
            confidence=confidence,
            correction_type=correction_type,
            source_region_json=json.dumps(source_region) if source_region else None,
            notes=notes,
        )
        session.add(correction)
        session.commit()
        session.refresh(correction)
        return _correction_to_dict(correction)
    finally:
        session.close()


def get_corrections_for_document(document_id: int) -> list[dict[str, Any]]:
    """Get all extraction corrections for a document, newest first."""
    session = get_session()
    try:
        rows = (
            session.query(ExtractionCorrection)
            .filter(ExtractionCorrection.document_id == document_id)
            .order_by(ExtractionCorrection.created_at.desc())
            .all()
        )
        return [_correction_to_dict(c) for c in rows]
    finally:
        session.close()


def list_recent_corrections(
    *,
    limit: int = 100,
    offset: int = 0,
    correction_type: str | None = None,
    field_name: str | None = None,
) -> list[dict[str, Any]]:
    """List recent extraction corrections for training data export."""
    session = get_session()
    try:
        query = session.query(ExtractionCorrection)
        if correction_type:
            query = query.filter(ExtractionCorrection.correction_type == correction_type)
        if field_name:
            query = query.filter(ExtractionCorrection.field_name == field_name)
        query = query.order_by(ExtractionCorrection.created_at.desc())
        rows = query.offset(offset).limit(limit).all()
        return [_correction_to_dict(c) for c in rows]
    finally:
        session.close()

