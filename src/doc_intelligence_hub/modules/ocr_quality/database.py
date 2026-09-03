"""Database models for the OCR quality baseline inventory."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


class InventoryRun(Base):
    """One Stage-1 or Stage-2 run — the reproducibility record."""

    __tablename__ = "ocr_quality_runs"

    run_id = Column(String, primary_key=True)
    stage = Column(String, nullable=False, index=True)
    scope_digest = Column(String, nullable=False)
    config_digest = Column(String, nullable=False)
    instance_digest = Column(String, nullable=False)
    signal_version = Column(String, nullable=False)
    seed = Column(String, nullable=True)  # Stage 2 sampling seed
    source_run_id = Column(String, nullable=True, index=True)  # Stage 2 -> Stage 1 run
    cursor = Column(String, nullable=True)
    status = Column(String, nullable=False, default="running", index=True)
    counts = Column(JSON, nullable=True)
    throughput_docs_per_second = Column(Float, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)

    # --- Issue #30 shared run/state contract fields ---
    # Who/what asked for this run and how (see ``models.RunTrigger``).
    actor = Column(String, nullable=True, default="system")
    trigger = Column(String, nullable=True, default="manual", index=True)
    # Caller-supplied or auto-generated correlation id, echoed back on every
    # response so an external caller (e.g. n8n) can trace a request across
    # retries/logs without OWL ever exposing raw OCR content.
    correlation_id = Column(String, nullable=True, index=True)
    # Digest of (stage, scope, config[, document/version]) used to detect
    # repeated delivery of the same effective request — see
    # ``service._compute_idempotency_key``.
    idempotency_key = Column(String, nullable=True, index=True)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    cancelled_at = Column(DateTime, nullable=True)
    # Bounded per-document retry budget for this run (not a run-level
    # retry — a whole run is never silently retried).
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)


class DocumentAssessment(Base):
    """Stage-1 per-document signals. No raw OCR text is stored here."""

    __tablename__ = "ocr_quality_document_assessments"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "document_version_key",
            "scorer_version",
            name="uq_ocr_assessment_document_version_scorer",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, nullable=False, index=True)  # most recent run that wrote this row
    first_seen_run_id = Column(String, nullable=False)
    document_id = Column(Integer, nullable=False, index=True)
    document_version_key = Column(String, nullable=False)  # Paperless `modified`/checksum proxy
    scorer_version = Column(String, nullable=False, index=True)

    content_length = Column(Integer, nullable=False, default=0)
    word_count = Column(Integer, nullable=False, default=0)
    non_ascii_ratio = Column(Float, nullable=False, default=0.0)
    whitespace_ratio = Column(Float, nullable=False, default=0.0)
    repetition_ratio = Column(Float, nullable=False, default=0.0)
    avg_token_length = Column(Float, nullable=False, default=0.0)
    distinct_token_ratio = Column(Float, nullable=False, default=0.0)
    table_shape_hint = Column(Boolean, nullable=False, default=False)
    code_shape_hint = Column(Boolean, nullable=False, default=False)
    preliminary_score = Column(Integer, nullable=False, default=0)

    disposition = Column(String, nullable=False, default="assessed", index=True)
    reason_codes = Column(JSON, nullable=True)

    document_type = Column(String, nullable=True, index=True)
    correspondent = Column(String, nullable=True, index=True)
    document_created = Column(String, nullable=True)  # ISO date/datetime, Paperless `created`
    legacy_action_queue_score = Column(Integer, nullable=True)
    downstream_outcome = Column(String, nullable=True, index=True)

    # Issue #29 multidimensional scorer output. Populated by
    # ``assess_document`` — machine-only (text) during Stage 1, then
    # overwritten with overlay+machine scores once Stage 2 fetches PDF
    # bytes for a sampled document. Null until scored.
    overlay_score = Column(Float, nullable=True)
    machine_score = Column(Float, nullable=True)
    review_status = Column(String, nullable=True, index=True)
    reasons = Column(JSON, nullable=True)
    document_profile = Column(JSON, nullable=True)
    quality_scorer_version = Column(String, nullable=True, index=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SampleSelection(Base):
    """Stage-2 deterministic sample membership."""

    __tablename__ = "ocr_quality_sample_selections"
    __table_args__ = (UniqueConstraint("run_id", "document_id", name="uq_ocr_sample_run_document"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, nullable=False, index=True)  # Stage 2 run id
    source_run_id = Column(String, nullable=False, index=True)  # Stage 1 run sampled from
    document_id = Column(Integer, nullable=False, index=True)
    stratum_key = Column(String, nullable=False, index=True)
    selection_rank = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class PdfProfile(Base):
    """Stage-2 page-aware PDF profiling result for a sampled document."""

    __tablename__ = "ocr_quality_pdf_profiles"
    __table_args__ = (
        UniqueConstraint("run_id", "document_id", name="uq_ocr_pdf_profile_run_document"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, nullable=False, index=True)
    document_id = Column(Integer, nullable=False, index=True)
    profile_version = Column(String, nullable=False)
    profile = Column(String, nullable=False, index=True)
    page_count = Column(Integer, nullable=False, default=0)
    digital_pages = Column(Integer, nullable=False, default=0)
    scanned_overlay_pages = Column(Integer, nullable=False, default=0)
    no_text_pages = Column(Integer, nullable=False, default=0)
    reason_codes = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class DocumentAnnotation(Base):
    """A reviewer-drawn bounding-box annotation on one page of one document.

    OWL-local only (issue #134, Part 2) — never mutates Paperless or the
    OCR quality assessment tables. ``label`` is free text; the frontend
    offers a small suggested set ("wrong", "key_data", "table_region",
    "other") but does not enforce an enum at the API/DB layer so reviewers
    can record ad hoc categories without a schema change.
    """

    __tablename__ = "ocr_quality_document_annotations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, nullable=False, index=True)
    page = Column(Integer, nullable=False, default=1)
    x0 = Column(Float, nullable=False)
    top = Column(Float, nullable=False)
    x1 = Column(Float, nullable=False)
    bottom = Column(Float, nullable=False)
    label = Column(String, nullable=False)
    note = Column(String, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RunFailure(Base):
    """Per-document failure/skip record — safe reason codes only."""

    __tablename__ = "ocr_quality_run_failures"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, nullable=False, index=True)
    document_id = Column(Integer, nullable=True, index=True)
    stage = Column(String, nullable=False)
    reason_code = Column(String, nullable=False)
    error_type = Column(String, nullable=True)  # exception class name only
    occurred_at = Column(DateTime, default=datetime.utcnow)


class OcrQualityCandidate(Base):
    """Candidate OCR result for a document (issue #18, slice 1).

    Storage-only: no field here ever reflects a Paperless write. The actual
    candidate PDF/text bytes live on disk under ``settings.candidate_storage_dir``
    (keyed by ``candidate_id``); only checksums/paths are persisted here,
    matching the "no raw OCR text in the DB" precedent set by
    ``DocumentAssessment``. Applying an accepted candidate to Paperless and
    the accompanying ``InvalidationRecord`` (issue #114) are a later slice —
    this table intentionally has no such column yet.
    """

    __tablename__ = "ocr_quality_candidates"
    __table_args__ = (
        Index("idx_candidate_document_state", "document_id", "state"),
        Index("idx_candidate_state_expires", "state", "expires_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(String, nullable=False, unique=True, index=True)
    document_id = Column(Integer, nullable=False, index=True)
    source_version_id = Column(String, nullable=True)
    source_checksum = Column(String, nullable=False)

    state = Column(String, nullable=False, default="requested", index=True)

    engine = Column(String, nullable=False)
    model_version = Column(String, nullable=False)
    settings = Column(JSON, nullable=False, default=dict)

    candidate_pdf_checksum = Column(String, nullable=True)
    candidate_text_checksum = Column(String, nullable=True)

    page_count = Column(Integer, nullable=False, default=0)
    runtime_seconds = Column(Float, nullable=True)
    cost_estimate = Column(Float, nullable=True)
    provider_operation_id = Column(String, nullable=True)

    overlay_score = Column(Float, nullable=True)
    machine_score = Column(Float, nullable=True)
    content_score = Column(Float, nullable=True)
    scorer_version = Column(String, nullable=True)

    comparison_id = Column(String, nullable=True, index=True)
    blocking_findings = Column(JSON, nullable=True)
    text_diff_summary = Column(JSON, nullable=True)
    overlay_score_delta = Column(Float, nullable=True)
    machine_score_delta = Column(Float, nullable=True)
    content_score_delta = Column(Float, nullable=True)
    comparison_performed_at = Column(DateTime, nullable=True)

    actor = Column(String, nullable=False, default="system")
    decision = Column(String, nullable=True)  # "accepted" | "rejected"
    decision_reason = Column(String, nullable=True)
    decided_at = Column(DateTime, nullable=True)

    failure_reason = Column(String, nullable=True)

    requested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    retention_window_days = Column(Integer, nullable=False, default=30)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def get_engine():
    return create_engine(settings.database_url, echo=False)


def get_session() -> Session:
    engine = get_engine()
    session_local = sessionmaker(bind=engine)
    return session_local()


def _literal_default_sql(column: Column) -> str | None:
    """Render a column's scalar Python-side default as SQL literal, if possible.

    Only handles simple, non-callable defaults (``Column(..., default=<scalar>)``)
    — which covers every column this module has ever added. Returns ``None``
    when there's no usable literal (callable default, or no default at all).
    """
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    value = default.arg
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _add_column_ddl(engine: Engine, column: Column) -> str:
    """Build the ``ADD COLUMN`` clause for one missing column."""
    column_type = column.type.compile(dialect=engine.dialect)
    default_sql = _literal_default_sql(column)
    parts = [f'"{column.name}"', column_type]
    # SQLite refuses to add a NOT NULL column to a non-empty table unless a
    # DEFAULT is given. If we can't derive one, fall back to nullable rather
    # than fail the whole migration (never blocks startup, never loses rows).
    if not column.nullable and default_sql is not None:
        parts.append("NOT NULL")
    if default_sql is not None:
        parts.append(f"DEFAULT {default_sql}")
    return " ".join(parts)


def _add_missing_columns(engine: Engine) -> None:
    """Idempotently add model columns missing from already-existing tables.

    This codebase has no migration framework (Alembic or otherwise) —
    ``Base.metadata.create_all()`` only creates missing *tables*, it never
    adds columns to a table that already exists. When a change adds columns
    to an existing model (e.g. issue #30's shared run/state contract fields
    on ``InventoryRun``), a database whose table predates that change is left
    without the new columns and every query against it fails with
    ``OperationalError: no such column`` (this broke the production
    ``/api/ocr-quality/runs`` list — see PR #153/issue #30 follow-up).

    This is a SQLite-only stopgap (the only backend this module supports):
    it only ever *adds* columns, never drops/renames/alters existing ones,
    and is a safe no-op when a table doesn't exist yet (``create_all()``
    already created it correctly) or already has every column.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                ddl = _add_column_ddl(engine, column)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}'))


def init_db() -> None:
    """Create all tables if they don't exist, and backfill missing columns.

    Safe to call on every startup/request (as every router already does):
    creating tables is a no-op once they exist, and ``_add_missing_columns``
    is a no-op once a table already has every column its model defines.
    """
    engine = get_engine()
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
