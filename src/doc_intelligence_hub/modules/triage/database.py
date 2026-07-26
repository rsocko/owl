"""SQLite persistence for the Triage Queue — queue items, correction events, and CRUD operations.

Mirrors the pattern used by the EOB Matching module (SQLAlchemy ORM + init_db).
"""

from __future__ import annotations

import contextlib
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

_DEFAULT_DB_URL = "sqlite:///data/triage.db"
_db_url: str = _DEFAULT_DB_URL


class Base(DeclarativeBase):
    pass


class TriageQueueItem(Base):
    """A single item in the triage queue awaiting human review."""

    __tablename__ = "triage_queue"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    item_type = Column(
        String, nullable=False, index=True
    )  # 'eob_match_review', 'grouping_anomaly', 'orphan_document'
    priority = Column(Integer, default=50)  # 0-100, higher = more urgent
    status = Column(
        String, default="pending", index=True
    )  # 'pending', 'deferred', 'resolved', 'dismissed'
    source = Column(String, nullable=False)  # 'auto_flag', 'user_request', 'scheduled_scan'
    target_type = Column(String, nullable=False)  # 'eob_match', 'statement_series', 'document'
    target_id = Column(String, nullable=False, index=True)
    reason = Column(Text, nullable=True)
    metadata_json = Column("metadata", Text, nullable=True)  # JSON blob
    deferred_until = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_action = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


class DocumentDuplicate(Base):
    """A detected pair of potentially duplicate documents."""

    __tablename__ = "document_duplicates"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    doc_a_id = Column(Integer, nullable=False)
    doc_b_id = Column(Integer, nullable=False)
    similarity_score = Column(Float, nullable=False)
    breakdown_json = Column("breakdown", Text, nullable=True)  # JSON: per-signal scores
    status = Column(
        String, default="pending"
    )  # 'pending', 'true_duplicate', 'superseded', 'not_duplicate'
    primary_doc_id = Column(Integer, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


class CorrectionEvent(Base):
    """Audit trail entry for all user corrections."""

    __tablename__ = "correction_events"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    event_type = Column(String, nullable=False, index=True)
    target_type = Column(String, nullable=False, index=True)
    target_id = Column(String, nullable=False)
    payload_json = Column("payload", Text, nullable=False)
    paperless_synced = Column(Integer, default=0)  # 0=not synced, 1=synced
    paperless_synced_at = Column(DateTime, nullable=True)
    undone = Column(Integer, default=0)  # 0=active, 1=undone
    undone_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    created_by = Column(String, default="user")


class NotificationConfig(Base):
    """Notification configuration for triage alerts and digests."""

    __tablename__ = "notification_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel = Column(String, nullable=False, unique=True)  # 'email_digest', 'mc_alerts', 'mc_badge'
    enabled = Column(Integer, default=1)  # 0=disabled, 1=enabled
    config_json = Column("config", Text, nullable=True)  # channel-specific config (e.g. schedule)
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC))


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
    source_region_json = Column(
        "source_region", Text, nullable=True
    )  # bounding box / OCR region JSON
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


def configure(url: str) -> None:
    """Configure the database URL (alias for set_db_url, matching EOB module pattern)."""
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
    """Create all tables if they don't exist, and migrate missing columns."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate_missing_columns(engine)


def _migrate_missing_columns(engine):
    """Add columns that were introduced after initial table creation.

    SQLAlchemy's ``create_all`` only creates tables — it never alters them.
    We inspect the live schema and issue ALTER TABLE for anything missing.
    """
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text

    inspector = sa_inspect(engine)

    _expected_additions: dict[str, list[tuple[str, str]]] = {
        "correction_events": [
            ("paperless_synced", "INTEGER DEFAULT 0"),
            ("paperless_synced_at", "DATETIME"),
            ("undone", "INTEGER DEFAULT 0"),
            ("undone_at", "DATETIME"),
        ],
    }

    with engine.begin() as conn:
        for table, columns in _expected_additions.items():
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for col_name, col_ddl in columns:
                if col_name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"))


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
            query = query.order_by(
                TriageQueueItem.priority.desc(), TriageQueueItem.created_at.asc()
            )

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


def resolve_queue_item(
    item_id: str, action: str, payload: dict | None = None
) -> dict[str, Any] | None:
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


def bulk_resolve_items(item_ids: list[str], action: str, payload: dict | None = None) -> int:
    """Resolve multiple triage queue items in a single transaction. Returns count of affected items."""
    import json

    session = get_session()
    try:
        items = (
            session.query(TriageQueueItem)
            .filter(
                TriageQueueItem.id.in_(item_ids),
                TriageQueueItem.status == "pending",
            )
            .all()
        )

        now = datetime.now(UTC)
        for item in items:
            item.status = "resolved"
            item.resolved_at = now
            item.resolved_action = action
            session.add(
                CorrectionEvent(
                    event_type=f"triage_{action}",
                    target_type=item.target_type,
                    target_id=item.target_id,
                    payload_json=json.dumps(payload or {}),
                )
            )

        session.commit()
        return len(items)
    finally:
        session.close()


def bulk_defer_items(item_ids: list[str], until: str | None = None) -> int:
    """Defer multiple triage queue items in a single transaction. Returns count of affected items."""
    from datetime import timedelta

    session = get_session()
    try:
        items = (
            session.query(TriageQueueItem)
            .filter(
                TriageQueueItem.id.in_(item_ids),
                TriageQueueItem.status == "pending",
            )
            .all()
        )

        defer_until = (
            datetime.fromisoformat(until) if until else datetime.now(UTC) + timedelta(days=7)
        )
        for item in items:
            item.status = "deferred"
            item.deferred_until = defer_until

        session.commit()
        return len(items)
    finally:
        session.close()


def bulk_dismiss_items(item_ids: list[str]) -> int:
    """Dismiss multiple triage queue items in a single transaction. Returns count of affected items."""
    session = get_session()
    try:
        items = (
            session.query(TriageQueueItem)
            .filter(
                TriageQueueItem.id.in_(item_ids),
                TriageQueueItem.status == "pending",
            )
            .all()
        )

        now = datetime.now(UTC)
        for item in items:
            item.status = "dismissed"
            item.resolved_at = now
            item.resolved_action = "dismissed"

        session.commit()
        return len(items)
    finally:
        session.close()


def bulk_confirm_by_threshold(min_confidence: int) -> int:
    """Confirm all pending EOB match items with score_pct >= threshold. Returns count of affected items."""
    import json

    session = get_session()
    try:
        # Get all pending eob_match_review items
        items = (
            session.query(TriageQueueItem)
            .filter(
                TriageQueueItem.item_type == "eob_match_review",
                TriageQueueItem.status == "pending",
            )
            .all()
        )

        now = datetime.now(UTC)
        count = 0
        for item in items:
            meta = {}
            if item.metadata_json:
                try:
                    parsed = json.loads(item.metadata_json)
                    if isinstance(parsed, dict):
                        meta = parsed
                except (json.JSONDecodeError, TypeError):
                    continue
            score = meta.get("score_pct")
            if isinstance(score, (int, float)) and score >= min_confidence:
                item.status = "resolved"
                item.resolved_at = now
                item.resolved_action = "confirm"
                session.add(
                    CorrectionEvent(
                        event_type="triage_bulk_confirm_threshold",
                        target_type=item.target_type,
                        target_id=item.target_id,
                        payload_json=json.dumps(
                            {"min_confidence": min_confidence, "score_pct": score}
                        ),
                    )
                )
                count += 1

        session.commit()
        return count
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


# ------------------------------------------------------------------
# DocumentDuplicate CRUD
# ------------------------------------------------------------------


def _duplicate_to_dict(dup: DocumentDuplicate) -> dict[str, Any]:
    """Convert a DocumentDuplicate to a plain dict for JSON serialization."""
    import json

    breakdown = None
    if dup.breakdown_json:
        try:
            breakdown = json.loads(dup.breakdown_json)
        except (json.JSONDecodeError, TypeError):
            breakdown = dup.breakdown_json

    return {
        "id": dup.id,
        "doc_a_id": dup.doc_a_id,
        "doc_b_id": dup.doc_b_id,
        "similarity_score": dup.similarity_score,
        "breakdown": breakdown,
        "status": dup.status,
        "primary_doc_id": dup.primary_doc_id,
        "resolved_at": dup.resolved_at.isoformat() if dup.resolved_at else None,
        "created_at": dup.created_at.isoformat() if dup.created_at else None,
    }


def create_duplicate_pair(
    *,
    doc_a_id: int,
    doc_b_id: int,
    similarity_score: float,
    breakdown: dict | None = None,
) -> dict[str, Any]:
    """Create a new duplicate pair record."""
    import json

    session = get_session()
    try:
        dup = DocumentDuplicate(
            doc_a_id=doc_a_id,
            doc_b_id=doc_b_id,
            similarity_score=similarity_score,
            breakdown_json=json.dumps(breakdown) if breakdown else None,
        )
        session.add(dup)
        session.commit()
        session.refresh(dup)
        return _duplicate_to_dict(dup)
    finally:
        session.close()


def list_duplicate_pairs(
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List duplicate pairs with optional status filter."""
    session = get_session()
    try:
        query = session.query(DocumentDuplicate)
        if status:
            query = query.filter(DocumentDuplicate.status == status)
        else:
            query = query.filter(DocumentDuplicate.status == "pending")
        query = query.order_by(DocumentDuplicate.similarity_score.desc())
        pairs = query.offset(offset).limit(limit).all()
        return [_duplicate_to_dict(p) for p in pairs]
    finally:
        session.close()


def get_duplicate_pair(pair_id: str) -> dict[str, Any] | None:
    """Get a single duplicate pair by ID."""
    session = get_session()
    try:
        dup = session.query(DocumentDuplicate).filter(DocumentDuplicate.id == pair_id).first()
        return _duplicate_to_dict(dup) if dup else None
    finally:
        session.close()


def resolve_duplicate_pair(
    pair_id: str,
    resolution: str,
    primary_doc_id: int | None = None,
) -> dict[str, Any] | None:
    """Resolve a duplicate pair with the given resolution status."""
    import json

    session = get_session()
    try:
        dup = session.query(DocumentDuplicate).filter(DocumentDuplicate.id == pair_id).first()
        if not dup:
            return None

        dup.status = resolution
        dup.primary_doc_id = primary_doc_id
        dup.resolved_at = datetime.now(UTC)

        # Record correction event
        event = CorrectionEvent(
            event_type=f"duplicate_{resolution}",
            target_type="document_duplicate",
            target_id=pair_id,
            payload_json=json.dumps(
                {
                    "resolution": resolution,
                    "primary_doc_id": primary_doc_id,
                    "doc_a_id": dup.doc_a_id,
                    "doc_b_id": dup.doc_b_id,
                }
            ),
        )
        session.add(event)
        session.commit()
        return _duplicate_to_dict(dup)
    finally:
        session.close()


def find_existing_duplicate_pair(doc_a_id: int, doc_b_id: int) -> dict[str, Any] | None:
    """Check if a duplicate pair already exists for the two document IDs (in either order)."""
    session = get_session()
    try:
        dup = (
            session.query(DocumentDuplicate)
            .filter(
                (
                    (DocumentDuplicate.doc_a_id == doc_a_id)
                    & (DocumentDuplicate.doc_b_id == doc_b_id)
                )
                | (
                    (DocumentDuplicate.doc_a_id == doc_b_id)
                    & (DocumentDuplicate.doc_b_id == doc_a_id)
                )
            )
            .first()
        )
        return _duplicate_to_dict(dup) if dup else None
    finally:
        session.close()


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


# ------------------------------------------------------------------
# Dashboard stats helpers
# ------------------------------------------------------------------


def get_dashboard_stats() -> dict[str, Any]:
    """Aggregate dashboard metrics: key stats, queue breakdown, and activity summary."""
    from datetime import timedelta

    session = get_session()
    try:
        now = datetime.now(UTC)
        month_ago = now - timedelta(days=30)

        # Pending count
        pending = session.query(TriageQueueItem).filter(TriageQueueItem.status == "pending").count()

        # Triaged this month (resolved items)
        triaged_this_month = (
            session.query(TriageQueueItem)
            .filter(
                TriageQueueItem.status == "resolved",
                TriageQueueItem.resolved_at >= month_ago,
            )
            .count()
        )

        # Queue breakdown by type (pending only)
        type_rows = (
            session.query(TriageQueueItem.item_type, func.count(TriageQueueItem.id))
            .filter(TriageQueueItem.status == "pending")
            .group_by(TriageQueueItem.item_type)
            .all()
        )
        queue_breakdown = [{"type": t, "count": c} for t, c in type_rows]

        # Status breakdown
        status_rows = (
            session.query(TriageQueueItem.status, func.count(TriageQueueItem.id))
            .group_by(TriageQueueItem.status)
            .all()
        )
        by_status = dict(status_rows)

        # Correction events count (for accuracy calculation)
        total_corrections = (
            session.query(CorrectionEvent)
            .filter(
                CorrectionEvent.undone == 0,
            )
            .count()
        )

        confirmed_corrections = (
            session.query(CorrectionEvent)
            .filter(
                CorrectionEvent.event_type.in_(["triage_confirm", "triage_bulk_confirm_threshold"]),
                CorrectionEvent.undone == 0,
            )
            .count()
        )

        return {
            "pending_count": pending,
            "triaged_this_month": triaged_this_month,
            "queue_breakdown": queue_breakdown,
            "by_status": by_status,
            "total_corrections": total_corrections,
            "confirmed_corrections": confirmed_corrections,
        }
    finally:
        session.close()


def get_activity_feed(limit: int = 20) -> list[dict[str, Any]]:
    """Get recent correction events for the activity feed."""
    import json

    session = get_session()
    try:
        events = (
            session.query(CorrectionEvent)
            .filter(CorrectionEvent.undone == 0)
            .order_by(CorrectionEvent.created_at.desc())
            .limit(limit)
            .all()
        )
        result = []
        for e in events:
            payload = {}
            if e.payload_json:
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    payload = json.loads(e.payload_json)
            result.append(
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "target_type": e.target_type,
                    "target_id": e.target_id,
                    "payload": payload,
                    "paperless_synced": bool(e.paperless_synced),
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                    "created_by": e.created_by,
                }
            )
        return result
    finally:
        session.close()


def get_match_rate_trend(months: int = 6) -> list[dict[str, Any]]:
    """Calculate match rate trend by month from correction events.

    Uses a single query with in-Python bucketing to avoid N+1 query overhead.
    """
    from datetime import timedelta

    session = get_session()
    try:
        now = datetime.now(UTC)
        earliest = now - timedelta(days=30 * months)

        # Single query: fetch all relevant events in the time window
        events = (
            session.query(CorrectionEvent.event_type, CorrectionEvent.created_at)
            .filter(
                CorrectionEvent.created_at >= earliest,
                CorrectionEvent.event_type.in_(
                    [
                        "triage_confirm",
                        "triage_reject",
                        "triage_bulk_confirm_threshold",
                    ]
                ),
                CorrectionEvent.undone == 0,
            )
            .all()
        )

        # Build month buckets
        confirm_types = {"triage_confirm", "triage_bulk_confirm_threshold"}
        buckets: dict[int, dict[str, int]] = {}
        for i in range(months):
            buckets[i] = {"total": 0, "confirmed": 0}

        for event_type, created_at in events:
            if created_at is None:
                continue
            days_ago = (now - created_at).days
            bucket_idx = months - 1 - (days_ago // 30)
            if 0 <= bucket_idx < months:
                buckets[bucket_idx]["total"] += 1
                if event_type in confirm_types:
                    buckets[bucket_idx]["confirmed"] += 1

        trend = []
        for i in range(months):
            month_start = now - timedelta(days=30 * (months - i))
            total = buckets[i]["total"]
            confirmed = buckets[i]["confirmed"]
            rate = round((confirmed / total * 100) if total > 0 else 0)
            trend.append(
                {
                    "month": month_start.strftime("%b"),
                    "rate": rate,
                    "confirmed": confirmed,
                    "total": total,
                }
            )
        return trend
    finally:
        session.close()


# ------------------------------------------------------------------
# Correction history CRUD
# ------------------------------------------------------------------


def list_correction_events(
    *,
    limit: int = 50,
    offset: int = 0,
    event_type: str | None = None,
    target_type: str | None = None,
    include_undone: bool = False,
) -> list[dict[str, Any]]:
    """List correction events for the history view."""
    import json

    session = get_session()
    try:
        query = session.query(CorrectionEvent)
        if not include_undone:
            query = query.filter(CorrectionEvent.undone == 0)
        if event_type:
            # Match related event types (e.g. triage_confirm also matches bulk_confirm_threshold)
            if event_type == "triage_confirm":
                query = query.filter(
                    CorrectionEvent.event_type.in_(
                        ["triage_confirm", "triage_bulk_confirm_threshold"]
                    )
                )
            else:
                query = query.filter(CorrectionEvent.event_type == event_type)
        if target_type:
            query = query.filter(CorrectionEvent.target_type == target_type)
        query = query.order_by(CorrectionEvent.created_at.desc())
        events = query.offset(offset).limit(limit).all()

        result = []
        for e in events:
            payload = {}
            if e.payload_json:
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    payload = json.loads(e.payload_json)
            result.append(
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "target_type": e.target_type,
                    "target_id": e.target_id,
                    "payload": payload,
                    "paperless_synced": bool(e.paperless_synced),
                    "paperless_synced_at": e.paperless_synced_at.isoformat()
                    if e.paperless_synced_at
                    else None,
                    "undone": bool(e.undone),
                    "undone_at": e.undone_at.isoformat() if e.undone_at else None,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                    "created_by": e.created_by,
                }
            )
        return result
    finally:
        session.close()


def undo_correction_event(event_id: str) -> dict[str, Any] | None:
    """Mark a correction event as undone and revert the corresponding queue item to pending."""
    import json

    session = get_session()
    try:
        event = session.query(CorrectionEvent).filter(CorrectionEvent.id == event_id).first()
        if not event:
            return None

        # Guard: already undone
        if event.undone:
            payload = {}
            if event.payload_json:
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    payload = json.loads(event.payload_json)
            return {
                "id": event.id,
                "event_type": event.event_type,
                "target_type": event.target_type,
                "target_id": event.target_id,
                "payload": payload,
                "undone": True,
                "undone_at": event.undone_at.isoformat() if event.undone_at else None,
                "created_at": event.created_at.isoformat() if event.created_at else None,
                "already_undone": True,
            }

        event.undone = 1
        event.undone_at = datetime.now(UTC)

        # Revert the corresponding triage queue item back to pending
        queue_item = (
            session.query(TriageQueueItem)
            .filter(
                TriageQueueItem.target_type == event.target_type,
                TriageQueueItem.target_id == event.target_id,
                TriageQueueItem.status.in_(["resolved", "dismissed"]),
            )
            .first()
        )
        if queue_item:
            queue_item.status = "pending"
            queue_item.resolved_at = None
            queue_item.resolved_action = None

        session.commit()

        payload = {}
        if event.payload_json:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                payload = json.loads(event.payload_json)

        return {
            "id": event.id,
            "event_type": event.event_type,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "payload": payload,
            "undone": True,
            "undone_at": event.undone_at.isoformat() if event.undone_at else None,
            "created_at": event.created_at.isoformat() if event.created_at else None,
            "queue_item_reverted": queue_item is not None,
        }
    finally:
        session.close()


def mark_correction_synced(event_id: str) -> bool:
    """Mark a correction event as synced to Paperless. Returns True if found."""
    session = get_session()
    try:
        event = session.query(CorrectionEvent).filter(CorrectionEvent.id == event_id).first()
        if not event:
            return False
        event.paperless_synced = 1
        event.paperless_synced_at = datetime.now(UTC)
        session.commit()
        return True
    finally:
        session.close()


# ------------------------------------------------------------------
# Notification config CRUD
# ------------------------------------------------------------------


def get_notification_configs() -> list[dict[str, Any]]:
    """Get all notification channel configurations."""
    import json

    session = get_session()
    try:
        rows = session.query(NotificationConfig).all()
        result = []
        for r in rows:
            config = {}
            if r.config_json:
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    config = json.loads(r.config_json)
            result.append(
                {
                    "id": r.id,
                    "channel": r.channel,
                    "enabled": bool(r.enabled),
                    "config": config,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                }
            )
        return result
    finally:
        session.close()


def upsert_notification_config(
    channel: str, *, enabled: bool = True, config: dict | None = None
) -> dict[str, Any]:
    """Create or update a notification channel configuration."""
    import json

    session = get_session()
    try:
        row = (
            session.query(NotificationConfig).filter(NotificationConfig.channel == channel).first()
        )
        if row:
            row.enabled = 1 if enabled else 0
            if config is not None:
                row.config_json = json.dumps(config)
            row.updated_at = datetime.now(UTC)
        else:
            row = NotificationConfig(
                channel=channel,
                enabled=1 if enabled else 0,
                config_json=json.dumps(config) if config else None,
            )
            session.add(row)
        session.commit()
        session.refresh(row)

        parsed_config = {}
        if row.config_json:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                parsed_config = json.loads(row.config_json)

        return {
            "id": row.id,
            "channel": row.channel,
            "enabled": bool(row.enabled),
            "config": parsed_config,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
    finally:
        session.close()
