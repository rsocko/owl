"""Database models and connection management."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

# Valid action statuses (lifecycle states)
VALID_STATUSES = {"pending", "acknowledged", "completed", "snoozed", "dismissed", "not_an_action"}

# 3-tier severity model (derived from urgency for display)
VALID_SEVERITIES = {"critical", "focus", "safe"}

# Valid action types
VALID_ACTION_TYPES = {
    "PAY",
    "RESPOND",
    "FILE",
    "REVIEW",
    "SHARE",
    "SCHEDULE",
    "SIGN",
    "ARCHIVE",
    "TASK",
}


class Base(DeclarativeBase):
    pass


class Action(Base):
    """An action recommendation extracted from a Paperless document."""

    __tablename__ = "actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, nullable=False, index=True)
    document_title = Column(String, nullable=False)
    action_type = Column(String, nullable=False)  # PAY, RESPOND, FILE, REVIEW, etc.
    title = Column(String, nullable=False)
    summary = Column(Text)
    due_date = Column(Date, nullable=True)
    amount = Column(Float, nullable=True)
    document_amount = Column(Float, nullable=True)
    document_due_date = Column(Date, nullable=True)
    document_amount_overridden = Column(Boolean, nullable=False, default=False)
    document_due_date_overridden = Column(Boolean, nullable=False, default=False)
    urgency = Column(String, default="LOW")  # CRITICAL, HIGH, MEDIUM, LOW
    severity = Column(String, default="safe")  # critical, focus, safe (3-tier display bucket)
    confidence = Column(Integer, default=0)
    risk_score = Column(Integer, default=0)  # 0-100 composite risk score
    status = Column(
        String, default="pending", index=True
    )  # pending, acknowledged, completed, snoozed, dismissed, not_an_action
    last_synced_status = Column(String, nullable=True)  # What we last wrote to Paperless
    correspondent = Column(String, nullable=True)
    document_date = Column(Date, nullable=True)  # Paperless "created" date (document date)
    document_type = Column(String, nullable=True)  # Paperless document type name
    tags = Column(JSON, nullable=True)  # Paperless tags as JSON array of strings
    extracted_data = Column(JSON, nullable=True)  # Full extraction payload from Ollama
    ai_reasoning = Column(Text, nullable=True)
    recommended_cta = Column(
        String, nullable=True
    )  # AI-recommended call-to-action (e.g., "pay-online", "open-document")
    action_ready = Column(Boolean, nullable=False, default=True, index=True)
    review_state = Column(String, nullable=False, default="ready", index=True)
    review_item_id = Column(String, nullable=True, index=True)
    action_index = Column(Integer, nullable=True)
    is_primary = Column(Boolean, nullable=False, default=True)
    parent_action_id = Column(Integer, nullable=True, index=True)
    superseded_by_action_id = Column(Integer, nullable=True, index=True)
    version = Column(Integer, default=1, nullable=False)  # Optimistic locking
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)
    snoozed_until = Column(DateTime, nullable=True)  # When snooze expires; null = not snoozed


class ActionFeedback(Base):
    """User feedback on action items — trains the classifier over time.

    Records false positives, misclassifications, and other correction signals
    that can be used to tune confidence thresholds and retrain models.
    """

    __tablename__ = "action_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    action_id = Column(Integer, nullable=False, index=True)
    feedback_type = Column(
        String, nullable=False
    )  # not_an_action, misclassified, wrong_urgency, wrong_amount
    original_action_type = Column(String, nullable=True)  # What it was classified as
    corrected_action_type = Column(
        String, nullable=True
    )  # What user says it should be (if misclassified)
    original_urgency = Column(String, nullable=True)
    corrected_urgency = Column(String, nullable=True)
    original_amount = Column(Float, nullable=True)
    corrected_amount = Column(Float, nullable=True)
    reason = Column(Text, nullable=True)  # Optional user explanation
    created_at = Column(DateTime, default=datetime.utcnow)


class ProcessingHistory(Base):
    """Tracks which documents have been processed to avoid duplicates."""

    __tablename__ = "processing_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, nullable=False, unique=True, index=True)
    document_checksum = Column(String, nullable=True)  # Detect content changes
    processed_at = Column(DateTime, default=datetime.utcnow)
    ollama_model = Column(String, nullable=True)
    success = Column(Integer, default=1)  # 1=success, 0=failed
    error_message = Column(Text, nullable=True)

    # Disposition: what happened when we processed this document
    # action_created | no_action_needed | no_action_sync_failed | unreadable | low_confidence
    disposition = Column(String, default="action_created")

    # Basic text quality metrics (free data for future OCR quality pipeline)
    content_length = Column(Integer, nullable=True)  # chars in OCR text
    word_count = Column(Integer, nullable=True)
    text_quality_score = Column(Integer, nullable=True)  # 0-100 heuristic


class QueueConfiguration(Base):
    """Durable user configuration for Action Queue intake and resolution."""

    __tablename__ = "action_queue_configuration"

    id = Column(Integer, primary_key=True, default=1)
    scan_mode = Column(String, nullable=False, default="tags")
    monitor_tags = Column(JSON, nullable=False, default=lambda: ["Inbox"])
    saved_view_id = Column(Integer, nullable=True)
    confidence_threshold = Column(Integer, nullable=False, default=70)
    document_limit = Column(Integer, nullable=True)
    rate_limit_delay = Column(Float, nullable=False, default=0.25)
    remove_source_tag_on_resolve = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def get_engine():
    return create_engine(settings.database_url, echo=False)


def get_session() -> Session:
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


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
        "actions": [
            ("risk_score", "INTEGER DEFAULT 0"),
            ("version", "INTEGER DEFAULT 1 NOT NULL"),
            ("severity", "TEXT DEFAULT 'safe'"),
            ("recommended_cta", "TEXT"),
            ("action_ready", "BOOLEAN DEFAULT 1 NOT NULL"),
            ("review_state", "TEXT DEFAULT 'ready' NOT NULL"),
            ("review_item_id", "TEXT"),
            ("acknowledged_at", "TIMESTAMP"),
            ("snoozed_until", "TIMESTAMP"),
            ("document_date", "DATE"),
            ("document_type", "TEXT"),
            ("tags", "TEXT"),  # JSON array stored as TEXT in SQLite
            ("document_amount", "REAL"),
            ("document_due_date", "DATE"),
            ("document_amount_overridden", "BOOLEAN DEFAULT 0 NOT NULL"),
            ("document_due_date_overridden", "BOOLEAN DEFAULT 0 NOT NULL"),
            ("action_index", "INTEGER"),
            ("is_primary", "BOOLEAN DEFAULT 1 NOT NULL"),
            ("parent_action_id", "INTEGER"),
            ("superseded_by_action_id", "INTEGER"),
        ],
        "action_feedback": [
            ("original_urgency", "TEXT"),
            ("corrected_urgency", "TEXT"),
            ("original_amount", "REAL"),
            ("corrected_amount", "REAL"),
        ],
    }

    with engine.begin() as conn:
        for table, columns in _expected_additions.items():
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            adding_action_identity = table == "actions" and "action_index" not in existing
            for col_name, col_ddl in columns:
                if col_name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"))
            if adding_action_identity:
                conn.execute(
                    text(
                        """
                        UPDATE actions
                        SET action_index = (
                            SELECT COUNT(*)
                            FROM actions AS older
                            WHERE older.document_id = actions.document_id
                              AND older.id < actions.id
                        )
                        WHERE action_index IS NULL
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        UPDATE actions
                        SET is_primary = CASE WHEN action_index = 0 THEN 1 ELSE 0 END
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        UPDATE actions
                        SET document_amount = (
                            SELECT chosen.amount
                            FROM actions AS chosen
                            WHERE chosen.document_id = actions.document_id
                            ORDER BY chosen.action_index, chosen.id
                            LIMIT 1
                        ),
                        document_due_date = (
                            SELECT chosen.due_date
                            FROM actions AS chosen
                            WHERE chosen.document_id = actions.document_id
                            ORDER BY chosen.action_index, chosen.id
                            LIMIT 1
                        )
                        """
                    )
                )
