"""SQLite persistence for EOB Matching — classifications, extractions, and match results.

Mirrors the pattern used by the Action Queue module (SQLAlchemy ORM + init_db).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


_DEFAULT_DB_URL = "sqlite:///data/eob_matching.db"
_db_url: str = _DEFAULT_DB_URL


class Base(DeclarativeBase):
    pass


class MatchingRun(Base):
    """Record of each pipeline execution."""

    __tablename__ = "matching_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    finished_at = Column(DateTime, nullable=True)
    documents_scanned = Column(Integer, default=0)
    eobs_found = Column(Integer, default=0)
    bills_found = Column(Integer, default=0)
    matches_found = Column(Integer, default=0)
    high_confidence = Column(Integer, default=0)
    medium_confidence = Column(Integer, default=0)
    low_confidence = Column(Integer, default=0)
    tags_filter = Column(String, nullable=True)
    correspondent_filter = Column(String, nullable=True)


class EOBRecord(Base):
    """A classified + extracted EOB document."""

    __tablename__ = "eob_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, nullable=False, index=True)
    run_id = Column(Integer, nullable=True, index=True)
    title = Column(String, nullable=True)
    classification_score = Column(Float, default=0.0)
    insurance_company = Column(String, nullable=True)
    policy_number = Column(String, nullable=True)
    patient_name = Column(String, nullable=True)
    claim_number = Column(String, nullable=True)
    date_of_service = Column(String, nullable=True)
    provider_name = Column(String, nullable=True)
    total_billed = Column(Float, nullable=True)
    total_allowed = Column(Float, nullable=True)
    total_plan_pays = Column(Float, nullable=True)
    total_patient_responsibility = Column(Float, nullable=True)
    services_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("document_id", "run_id", name="uq_eob_doc_run"),
    )


class BillRecord(Base):
    """A classified + extracted Bill document."""

    __tablename__ = "bill_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, nullable=False, index=True)
    run_id = Column(Integer, nullable=True, index=True)
    title = Column(String, nullable=True)
    classification_score = Column(Float, default=0.0)
    provider_name = Column(String, nullable=True)
    patient_name = Column(String, nullable=True)
    invoice_number = Column(String, nullable=True)
    date_of_service = Column(String, nullable=True)
    due_date = Column(String, nullable=True)
    total_amount = Column(Float, nullable=True)
    balance_due = Column(Float, nullable=True)
    payment_status = Column(String, nullable=True)
    services_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("document_id", "run_id", name="uq_bill_doc_run"),
    )


class MatchRecord(Base):
    """A confirmed or candidate EOB↔Bill match."""

    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=True, index=True)
    eob_document_id = Column(Integer, nullable=False, index=True)
    bill_document_id = Column(Integer, nullable=False, index=True)
    score = Column(Float, nullable=False)
    confidence = Column(String, nullable=False)  # HIGH, MEDIUM, LOW
    breakdown_date = Column(Float, default=0.0)
    breakdown_provider = Column(Float, default=0.0)
    breakdown_patient = Column(Float, default=0.0)
    breakdown_amount = Column(Float, default=0.0)
    breakdown_procedures = Column(Float, default=0.0)
    status = Column(String, default="candidate")  # candidate, confirmed, rejected
    linked_in_paperless = Column(Integer, default=0)  # 1 = custom fields written
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    confirmed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("eob_document_id", "bill_document_id", "run_id", name="uq_match_pair_run"),
    )


# ------------------------------------------------------------------
# Engine / session helpers
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
    """Create all tables if they don't exist."""
    engine = get_engine()
    Base.metadata.create_all(engine)


def configure(database_url: str) -> None:
    """Override the database URL (call before any DB access)."""
    global _db_url, _engine
    _db_url = database_url
    _engine = None  # Reset engine so next call uses new URL


# ------------------------------------------------------------------
# Convenience helpers for the CLI / pipeline
# ------------------------------------------------------------------


def store_run(session: Session, run: MatchingRun) -> MatchingRun:
    """Persist a MatchingRun and return it with populated id."""
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def store_eob(session: Session, record: EOBRecord) -> EOBRecord:
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def store_bill(session: Session, record: BillRecord) -> BillRecord:
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def store_match(session: Session, record: MatchRecord) -> MatchRecord:
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def latest_runs(session: Session, limit: int = 10) -> list[MatchingRun]:
    return (
        session.query(MatchingRun)
        .order_by(MatchingRun.started_at.desc())
        .limit(limit)
        .all()
    )


def pending_matches(session: Session) -> list[MatchRecord]:
    return (
        session.query(MatchRecord)
        .filter_by(status="candidate")
        .order_by(MatchRecord.score.desc())
        .all()
    )


def confirmed_matches(session: Session) -> list[MatchRecord]:
    return session.query(MatchRecord).filter_by(status="confirmed").all()
