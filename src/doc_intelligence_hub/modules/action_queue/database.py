"""Database models and connection management."""

import uuid
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
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

# Valid action statuses (lifecycle states)
VALID_STATUSES = {
    "pending",
    "acknowledged",
    "completed",
    "snoozed",
    "dismissed",
    "not_an_action",
    "source_deleted",
}

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


class Obligation(Base):
    """A real-world obligation represented by one Action Queue action and many documents."""

    __tablename__ = "obligations"

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    identity_key = Column(String, nullable=True, index=True)
    primary_action_id = Column(Integer, nullable=True, index=True)
    authoritative_document_id = Column(Integer, nullable=True, index=True)
    relationship_explanation = Column(Text, nullable=True)
    relationship_rules = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default="open", index=True)
    completion_suggested = Column(Boolean, nullable=False, default=False)
    suggestion_reason = Column(Text, nullable=True)
    expected_amount_minor = Column(Integer, nullable=True)
    currency = Column(String, nullable=True)
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    payee_or_owner = Column(String, nullable=True)
    reference = Column(String, nullable=True)
    origin = Column(String, nullable=False, default="inferred")
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
    human_reviewed = Column(Boolean, nullable=False, default=False, index=True)
    human_corrected_fields = Column(JSON, nullable=False, default=list)
    analysis_identity = Column(String, nullable=True, index=True)
    action_index = Column(Integer, nullable=True)
    is_primary = Column(Boolean, nullable=False, default=True)
    parent_action_id = Column(Integer, nullable=True, index=True)
    superseded_by_action_id = Column(Integer, nullable=True, index=True)
    obligation_id = Column(String, nullable=True, index=True)
    relationship_base_snapshot = Column(JSON, nullable=True)
    version = Column(Integer, default=1, nullable=False)  # Optimistic locking
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)
    snoozed_until = Column(DateTime, nullable=True)  # When snooze expires; null = not snoozed


class ActionLifecycleEvent(Base):
    """Append-only action history, including corrective undo transitions."""

    __tablename__ = "action_lifecycle_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    action_id = Column(Integer, nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    previous_status = Column(String, nullable=True)
    new_status = Column(String, nullable=True)
    action_version = Column(Integer, nullable=False)
    actor = Column(String, nullable=False, default="user")
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class IntakeSuggestionSnapshot(Base):
    """Cached Paperless suggestions for one source document revision."""

    __tablename__ = "intake_suggestion_snapshots"

    document_id = Column(Integer, primary_key=True)
    source_modified = Column(String, nullable=False)
    suggestions = Column(JSON, nullable=False, default=list)
    source_states = Column(JSON, nullable=False, default=dict)
    fetched_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ObligationDocument(Base):
    """A document attached to an obligation timeline."""

    __tablename__ = "obligation_documents"
    __table_args__ = (
        UniqueConstraint("obligation_id", "document_id", name="uq_obligation_document"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    obligation_id = Column(String, nullable=False, index=True)
    document_id = Column(Integer, nullable=False, index=True)
    role = Column(String, nullable=False, default="supporting")
    title = Column(String, nullable=True)
    document_type = Column(String, nullable=True)
    correspondent = Column(String, nullable=True)
    document_date = Column(Date, nullable=True)
    amount = Column(Float, nullable=True)
    reference_number = Column(String, nullable=True)
    confidence = Column(Float, nullable=False, default=1.0)
    source = Column(String, nullable=False, default="action_queue")
    excluded = Column(Boolean, nullable=False, default=False)
    excluded_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PaymentEvidence(Base):
    """Privacy-bounded payment evidence owned by the Action Queue aggregate."""

    __tablename__ = "payment_evidence"
    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "connector_ref",
            "evidence_ref",
            name="uq_payment_evidence_source_identity",
        ),
    )

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    source_system = Column(String, nullable=False, index=True)
    connector_ref = Column(String, nullable=False)
    evidence_ref = Column(String, nullable=False)
    canonical_document_ref = Column(String, nullable=True, index=True)
    transaction_ref = Column(String, nullable=True)
    receipt_ref = Column(String, nullable=True)
    kind = Column(String, nullable=False)
    payee_hint = Column(String, nullable=True)
    account_display_name = Column(String, nullable=True)
    account_last_four = Column(String, nullable=True)
    score_basis_points = Column(Integer, nullable=True)
    rank = Column(Integer, nullable=True)
    score_gap_basis_points = Column(Integer, nullable=True)
    receipt_association = Column(JSON, nullable=True)
    amount_minor = Column(Integer, nullable=True)
    currency = Column(String, nullable=True)
    evidence_date = Column(Date, nullable=True)
    reference_present = Column(Boolean, nullable=False, default=False)
    reference_digest = Column(String, nullable=True)
    source_state = Column(String, nullable=False)
    match_state = Column(String, nullable=False)
    payment_status = Column(String, nullable=False)
    confidence = Column(String, nullable=True)
    reason_codes = Column(JSON, nullable=False, default=list)
    source_generation = Column(String, nullable=False)
    source_revision = Column(String, nullable=True)
    source_as_of = Column(DateTime, nullable=False, index=True)
    active = Column(Boolean, nullable=False, default=True, index=True)
    payload_digest = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ObligationPaymentEdge(Base):
    """Current and historical association between an obligation and payment evidence."""

    __tablename__ = "obligation_payment_edges"
    __table_args__ = (
        UniqueConstraint("obligation_id", "evidence_id", name="uq_obligation_payment_evidence"),
    )

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    obligation_id = Column(String, nullable=False, index=True)
    evidence_id = Column(String, nullable=False, index=True)
    state = Column(String, nullable=False, default="proposed", index=True)
    provenance = Column(String, nullable=False, default="automatic")
    allocated_amount_minor = Column(Integer, nullable=True)
    confidence = Column(String, nullable=True)
    reason_codes = Column(JSON, nullable=False, default=list)
    manual_precedence = Column(Boolean, nullable=False, default=False)
    revision = Column(Integer, nullable=False, default=1)
    replaced_by_edge_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    removed_at = Column(DateTime, nullable=True)


class PaymentRelationshipEvent(Base):
    """Immutable audit event for one payment reconciliation transition."""

    __tablename__ = "payment_relationship_events"
    __table_args__ = (
        UniqueConstraint(
            "action_scope",
            "idempotency_key",
            name="uq_payment_event_idempotency",
        ),
    )

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    action_scope = Column(String, nullable=False)
    idempotency_key = Column(String, nullable=False)
    edge_id = Column(String, nullable=True, index=True)
    obligation_id = Column(String, nullable=False, index=True)
    evidence_id = Column(String, nullable=True, index=True)
    action = Column(String, nullable=False)
    actor = Column(String, nullable=False)
    provenance = Column(String, nullable=False)
    previous_state = Column(JSON, nullable=False, default=dict)
    new_state = Column(JSON, nullable=False, default=dict)
    source_generation = Column(String, nullable=True)
    source_revision = Column(String, nullable=True)
    expected_revision = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class PaymentEvidenceGeneration(Base):
    """Processed source generation used for replay and payload-collision protection."""

    __tablename__ = "payment_evidence_generations"
    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "connector_ref",
            "obligation_id",
            "source_generation",
            name="uq_payment_evidence_generation",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_system = Column(String, nullable=False)
    connector_ref = Column(String, nullable=False)
    obligation_id = Column(String, nullable=False, index=True)
    source_generation = Column(String, nullable=False)
    source_as_of = Column(DateTime, nullable=False)
    payload_digest = Column(String, nullable=False)
    disposition = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class PaymentEvidenceSource(Base):
    """Latest accepted cursor for one payment-evidence source."""

    __tablename__ = "payment_evidence_sources"
    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "connector_ref",
            "obligation_id",
            name="uq_payment_evidence_source",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_system = Column(String, nullable=False)
    connector_ref = Column(String, nullable=False)
    obligation_id = Column(String, nullable=False, index=True)
    source_generation = Column(String, nullable=False)
    source_as_of = Column(DateTime, nullable=False)
    source_revision = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PaymentReconciliationCase(Base):
    """Revisioned review aggregate exposed to specialist and Mission Control APIs."""

    __tablename__ = "payment_reconciliation_cases"
    __table_args__ = (
        UniqueConstraint(
            "obligation_id",
            "evidence_id",
            "case_kind",
            name="uq_payment_reconciliation_case",
        ),
    )

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    obligation_id = Column(String, nullable=False, index=True)
    evidence_id = Column(String, nullable=True, index=True)
    case_kind = Column(String, nullable=False, index=True)
    state = Column(String, nullable=False, default="open", index=True)
    active = Column(Boolean, nullable=False, default=True, index=True)
    revision = Column(Integer, nullable=False, default=1)
    summary = Column(JSON, nullable=False, default=dict)
    attention_state = Column(String, nullable=False, default="pending", index=True)
    attention_delivery_ref = Column(String, nullable=True)
    deferred_until = Column(DateTime, nullable=True)
    source_as_of = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PaymentActionReceipt(Base):
    """Durable idempotency receipt for externally invoked aggregate actions."""

    __tablename__ = "payment_action_receipts"
    __table_args__ = (
        UniqueConstraint("case_id", "idempotency_key", name="uq_payment_case_action_key"),
    )

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    case_id = Column(String, nullable=False, index=True)
    idempotency_key = Column(String, nullable=False)
    payload_digest = Column(String, nullable=False)
    result = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class PaymentCheckRun(Base):
    """Durable active-check command state and bounded Tyrion exchange."""

    __tablename__ = "payment_check_runs"
    __table_args__ = (
        UniqueConstraint(
            "obligation_id",
            "idempotency_key",
            name="uq_payment_check_obligation_key",
        ),
    )

    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    obligation_id = Column(String, nullable=False, index=True)
    action_id = Column(Integer, nullable=False, index=True)
    idempotency_key = Column(String, nullable=False)
    request_digest = Column(String, nullable=False)
    expected_revision = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="checking", index=True)
    request_payload = Column(JSON, nullable=False)
    response_payload = Column(JSON, nullable=True)
    error_code = Column(String, nullable=True)
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ObligationEvent(Base):
    """Auditable history for relationship-driven obligation recalculation."""

    __tablename__ = "obligation_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    obligation_id = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    relationship_id = Column(String, nullable=True, index=True)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


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
    engine_kwargs = {"echo": False}
    if settings.database_url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"timeout": 30}

    engine = create_engine(settings.database_url, **engine_kwargs)
    if settings.database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def configure_sqlite_connection(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=30000")
            finally:
                cursor.close()

    return engine


def get_session() -> Session:
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def init_db():
    """Create all tables if they don't exist, and migrate missing columns."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate_missing_columns(engine)
    from .payment_reconciliation import PaymentReconciliationService

    with Session(engine) as session:
        PaymentReconciliationService(session).backfill_legacy_receipts()
        session.commit()


def _migrate_missing_columns(engine):
    """Add columns that were introduced after initial table creation.

    SQLAlchemy's ``create_all`` only creates tables — it never alters them.
    We inspect the live schema and issue ALTER TABLE for anything missing.
    """
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text

    inspector = sa_inspect(engine)

    _expected_additions: dict[str, list[tuple[str, str]]] = {
        "obligations": [
            ("expected_amount_minor", "INTEGER"),
            ("currency", "TEXT"),
            ("revision", "INTEGER DEFAULT 1 NOT NULL"),
            ("identity_key", "TEXT"),
            ("authoritative_document_id", "INTEGER"),
            ("relationship_explanation", "TEXT"),
            ("relationship_rules", "TEXT"),
        ],
        "actions": [
            ("risk_score", "INTEGER DEFAULT 0"),
            ("version", "INTEGER DEFAULT 1 NOT NULL"),
            ("severity", "TEXT DEFAULT 'safe'"),
            ("recommended_cta", "TEXT"),
            ("action_ready", "BOOLEAN DEFAULT 1 NOT NULL"),
            ("review_state", "TEXT DEFAULT 'ready' NOT NULL"),
            ("review_item_id", "TEXT"),
            ("human_reviewed", "BOOLEAN DEFAULT 0 NOT NULL"),
            ("human_corrected_fields", "TEXT DEFAULT '[]' NOT NULL"),
            ("analysis_identity", "TEXT"),
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
            ("obligation_id", "TEXT"),
            ("relationship_base_snapshot", "TEXT"),
            ("payee_or_owner", "TEXT"),
            ("reference", "TEXT"),
            ("origin", "TEXT DEFAULT 'inferred' NOT NULL"),
        ],
        "action_feedback": [
            ("original_urgency", "TEXT"),
            ("corrected_urgency", "TEXT"),
            ("original_amount", "REAL"),
            ("corrected_amount", "REAL"),
        ],
        "obligation_documents": [
            ("excluded", "BOOLEAN DEFAULT 0 NOT NULL"),
            ("excluded_at", "TIMESTAMP"),
        ],
        "payment_evidence": [
            ("account_display_name", "TEXT"),
            ("account_last_four", "TEXT"),
            ("score_basis_points", "INTEGER"),
            ("rank", "INTEGER"),
            ("score_gap_basis_points", "INTEGER"),
            ("receipt_association", "TEXT"),
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
