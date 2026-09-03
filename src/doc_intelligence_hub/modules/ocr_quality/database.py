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
)
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
    """Candidate OCR result for a document (issue #18, slices 1 and 2).

    The candidate PDF/text bytes live on disk under
    ``settings.candidate_storage_dir`` (keyed by ``candidate_id``); only
    checksums/paths are persisted here, matching the "no raw OCR text in the
    DB" precedent set by ``DocumentAssessment``. The ``apply_*``/``applied_*``
    columns below are written only by ``application_service.py`` — nothing
    in ``candidate_service.py`` (slice 1) writes to Paperless or sets them.
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
    scorer_version = Column(String, nullable=True)

    comparison_id = Column(String, nullable=True, index=True)
    blocking_findings = Column(JSON, nullable=True)
    text_diff_summary = Column(JSON, nullable=True)
    overlay_score_delta = Column(Float, nullable=True)
    machine_score_delta = Column(Float, nullable=True)
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

    # --- Apply/rollback (slice 2) ---
    apply_attempts = Column(Integer, nullable=False, default=0)
    apply_last_error = Column(String, nullable=True)
    paperless_task_id = Column(String, nullable=True)
    applied_paperless_version_id = Column(Integer, nullable=True)
    applied_at = Column(DateTime, nullable=True)
    invalidation_recorded = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OcrApplicationLock(Base):
    """Document-scoped lock so at most one apply/rollback runs at a time.

    Survives process restarts (it's a DB row, not an in-memory lock). A
    lock older than ``candidate_apply_lock_ttl_seconds`` is considered
    stale (its holder presumably crashed) and is reclaimable by a new
    request rather than blocking forever.
    """

    __tablename__ = "ocr_quality_application_locks"

    document_id = Column(Integer, primary_key=True)
    locked_by = Column(String, nullable=False)
    locked_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False, index=True)
    # What operation holds the lock, for diagnostics ("apply" | "rollback").
    operation = Column(String, nullable=False, default="apply")
    candidate_id = Column(String, nullable=True)


class OcrApplicationEvent(Base):
    """Audit trail for every apply/rollback attempt against Paperless.

    Written regardless of outcome (including failures) so there is always a
    durable record of what OWL attempted against a document's Paperless
    version history, independent of the candidate row's own current state.
    """

    __tablename__ = "ocr_quality_application_events"
    __table_args__ = (Index("idx_application_event_document", "document_id", "created_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, nullable=False, index=True)
    candidate_id = Column(String, nullable=True, index=True)
    action = Column(String, nullable=False)  # "apply" | "rollback"
    actor = Column(String, nullable=False)
    outcome = Column(String, nullable=False)  # "success" | "failure"
    error_message = Column(String, nullable=True)
    paperless_task_id = Column(String, nullable=True)
    previous_version_id = Column(Integer, nullable=True)
    new_version_id = Column(Integer, nullable=True)
    invalidation_recorded = Column(Boolean, nullable=False, default=False)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


def get_engine():
    return create_engine(settings.database_url, echo=False)


def get_session() -> Session:
    engine = get_engine()
    session_local = sessionmaker(bind=engine)
    return session_local()


def init_db() -> None:
    """Create all tables if they don't exist."""
    engine = get_engine()
    Base.metadata.create_all(engine)
