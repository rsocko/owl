"""Database models for the OCR quality baseline inventory."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
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
