"""Database models for the analysis invalidation / staleness mechanism (issue #114).

Every table here is privacy-safe by construction: only document/version
identifiers, checksums, hashes, enum reason codes, and timestamps are ever
persisted. No OCR text, document bodies, or raw metadata values (titles,
correspondent names, tag names, etc.) are stored — only sha256 digests of
the metadata fields a given downstream module declares it depends on.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


class DocumentVersionIdentity(Base):
    """Last known accepted content identity for a document.

    Used only to compute the previous->new transition when a new
    invalidation is recorded (so duplicate delivery of the same transition
    can be deduplicated, while a rollback — a transition with a different
    ``previous_checksum`` — always produces a new invalidation cycle).
    """

    __tablename__ = "analysis_invalidation_document_version_identity"

    document_id = Column(Integer, primary_key=True)
    content_checksum = Column(String, nullable=False)
    metadata_fingerprint = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class InvalidationEvent(Base):
    """A durable, privacy-safe record that a document's accepted version
    (or relevant metadata) changed and downstream analysis should be
    reconsidered.
    """

    __tablename__ = "analysis_invalidation_events"
    __table_args__ = (UniqueConstraint("dedup_key", name="uq_invalidation_dedup_key"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, nullable=False, index=True)
    reason = Column(String, nullable=False, index=True)
    previous_checksum = Column(String, nullable=True)
    accepted_checksum = Column(String, nullable=False)
    metadata_fingerprint = Column(String, nullable=True)
    # sha256 over (document_id, previous_checksum, accepted_checksum,
    # metadata_fingerprint, reason) — makes exact-duplicate delivery of the
    # same transition idempotent without ever needing to inspect content.
    dedup_key = Column(String, nullable=False)
    # Free-text-free provenance tag only, e.g. "manual:api", "manual:cli",
    # "simulated" — never a user-identifying value.
    triggered_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class StaleMark(Base):
    """Audit trail: one row per (invalidation event, module) that was told
    its cached analysis for a document is now stale. Retained permanently —
    ``resolved_at`` is set (never deleted) once the module records a fresh
    replacement fingerprint.
    """

    __tablename__ = "analysis_invalidation_stale_marks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    invalidation_event_id = Column(Integer, nullable=False, index=True)
    document_id = Column(Integer, nullable=False, index=True)
    module_name = Column(String, nullable=False, index=True)
    reason = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by_fingerprint_id = Column(Integer, nullable=True)


class ModuleAnalysisFingerprint(Base):
    """One module's versioned analysis fingerprint for one document.

    Append-only: ``record_fingerprint`` always inserts a new row rather than
    updating in place, so a stale fingerprint remains in the table for audit
    even after a fresh replacement is recorded. The "current" fingerprint for
    a (document, module) pair is the most recent row (highest ``id``).
    """

    __tablename__ = "analysis_invalidation_module_fingerprints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, nullable=False, index=True)
    module_name = Column(String, nullable=False, index=True)
    module_version = Column(String, nullable=False)
    config_hash = Column(String, nullable=False)
    content_checksum = Column(String, nullable=False)
    # sha256 digest of only the metadata field values this module depends
    # on — never the raw values themselves.
    metadata_fingerprint = Column(String, nullable=True)
    # Names only (e.g. ["title", "correspondent"]) — field names are not
    # sensitive, only their values are.
    metadata_fields = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default="fresh", index=True)
    stale_reason = Column(String, nullable=True)
    computed_at = Column(DateTime, default=datetime.utcnow, index=True)


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
