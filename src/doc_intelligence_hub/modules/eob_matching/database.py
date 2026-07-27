"""SQLite persistence for EOB Matching — classifications, extractions, and match results.

Mirrors the pattern used by the Action Queue module (SQLAlchemy ORM + init_db).
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

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
    status = Column(String, default="unmatched")  # unmatched, orphan, paid
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    last_processed_at = Column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("document_id", "run_id", name="uq_eob_doc_run"),)


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
    last_processed_at = Column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("document_id", "run_id", name="uq_bill_doc_run"),)


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
    notes = Column(String, nullable=True)
    # Payment tracking
    payment_status = Column(String, default="unpaid")  # unpaid, partial, paid, overpaid
    paid_amount = Column(Float, default=0.0)
    paid_date = Column(DateTime, nullable=True)
    user_status = Column(String, default="unreviewed")  # unreviewed, confirmed, rejected, override
    reviewed_at = Column(DateTime, nullable=True)
    user_notes = Column(Text, nullable=True)
    # Billing error classification (ARCH-09)
    error_type = Column(String, nullable=True)  # BillingErrorType enum value
    error_details = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("eob_document_id", "bill_document_id", "run_id", name="uq_match_pair_run"),
    )


class PaymentRecord(Base):
    """Individual payment recorded against a confirmed match."""

    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, nullable=False, index=True)
    amount = Column(Float, nullable=False)
    paid_date = Column(DateTime, nullable=True)
    method = Column(String, nullable=True)  # e.g. check, online, insurance
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))


class MatchEvent(Base):
    """Audit log entry tracking match lifecycle transitions."""

    __tablename__ = "match_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, nullable=False, index=True)
    event_type = Column(
        String, nullable=False
    )  # auto_matched, flagged, reviewed, confirmed, rejected, reset
    actor = Column(String, nullable=False, default="system")  # "system" or "user"
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))


class BenchmarkRun(Base):
    """Record of each benchmark execution (manual or scheduled)."""

    __tablename__ = "benchmark_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    finished_at = Column(DateTime, nullable=True)
    documents_tested = Column(Integer, default=0)
    models_tested = Column(Integer, default=0)
    trigger = Column(String, default="manual")  # manual, scheduled
    status = Column(String, default="running")  # running, completed, failed


class BenchmarkModelResult(Base):
    """Per-model results from a single benchmark run."""

    __tablename__ = "benchmark_model_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=False, index=True)
    model = Column(String, nullable=False)
    documents_tested = Column(Integer, default=0)
    avg_time_seconds = Column(Float, default=0.0)
    success_rate = Column(Float, default=0.0)
    avg_confidence = Column(Float, default=0.0)
    total_time_seconds = Column(Float, default=0.0)
    estimated_cost_usd = Column(Float, nullable=True)
    results_json = Column(Text, nullable=True)  # JSON array of per-document results
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (UniqueConstraint("run_id", "model", name="uq_benchmark_run_model"),)


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

    # Map of table -> list of (column_name, column_ddl_suffix)
    _expected_additions: dict[str, list[tuple[str, str]]] = {
        "matches": [
            ("status", "TEXT DEFAULT 'candidate'"),
            ("linked_in_paperless", "INTEGER DEFAULT 0"),
            ("confirmed_at", "DATETIME"),
            ("notes", "TEXT"),
            ("payment_status", "TEXT DEFAULT 'unpaid'"),
            ("paid_amount", "REAL DEFAULT 0.0"),
            ("paid_date", "DATETIME"),
            ("user_status", "TEXT DEFAULT 'unreviewed'"),
            ("reviewed_at", "DATETIME"),
            ("user_notes", "TEXT"),
            ("error_type", "TEXT"),
            ("error_details", "TEXT"),
        ],
        "eob_records": [
            ("status", "TEXT DEFAULT 'unmatched'"),
            ("last_processed_at", "DATETIME"),
        ],
        "bill_records": [
            ("last_processed_at", "DATETIME"),
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


def last_successful_run(session: Session) -> MatchingRun | None:
    """Return the most recent MatchingRun that completed successfully (has finished_at)."""
    return (
        session.query(MatchingRun)
        .filter(MatchingRun.finished_at.isnot(None))
        .order_by(MatchingRun.finished_at.desc())
        .first()
    )


def latest_runs(session: Session, limit: int = 10) -> list[MatchingRun]:
    return session.query(MatchingRun).order_by(MatchingRun.started_at.desc()).limit(limit).all()


def pending_matches(session: Session) -> list[MatchRecord]:
    return (
        session.query(MatchRecord)
        .filter_by(status="candidate")
        .order_by(MatchRecord.score.desc())
        .all()
    )


def confirmed_matches(session: Session) -> list[MatchRecord]:
    return session.query(MatchRecord).filter_by(status="confirmed").all()


def add_match_event(
    session: Session,
    match_id: int,
    event_type: str,
    actor: str = "system",
    detail: str | None = None,
) -> MatchEvent:
    """Record a lifecycle event for a match."""
    event = MatchEvent(
        match_id=match_id,
        event_type=event_type,
        actor=actor,
        detail=detail,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def get_match_events(session: Session, match_id: int) -> list[MatchEvent]:
    """Return all events for a match, oldest first."""
    return (
        session.query(MatchEvent)
        .filter_by(match_id=match_id)
        .order_by(MatchEvent.created_at.asc())
        .all()
    )


# ------------------------------------------------------------------
# ------------------------------------------------------------------
# Benchmark persistence helpers
# ------------------------------------------------------------------


def store_benchmark_run(session: Session, run: BenchmarkRun) -> BenchmarkRun:
    """Persist a BenchmarkRun and return it with populated id."""
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def store_benchmark_result(session: Session, result: BenchmarkModelResult) -> BenchmarkModelResult:
    """Persist a BenchmarkModelResult and return it with populated id."""
    session.add(result)
    session.commit()
    session.refresh(result)
    return result


def latest_benchmark_runs(session: Session, limit: int = 20) -> list[BenchmarkRun]:
    """Return recent benchmark runs, newest first."""
    return session.query(BenchmarkRun).order_by(BenchmarkRun.started_at.desc()).limit(limit).all()


def get_benchmark_run(session: Session, run_id: int) -> BenchmarkRun | None:
    """Return a single benchmark run by id."""
    return session.query(BenchmarkRun).filter_by(id=run_id).first()


def get_benchmark_results(session: Session, run_id: int) -> list[BenchmarkModelResult]:
    """Return all model results for a benchmark run."""
    return (
        session.query(BenchmarkModelResult)
        .filter_by(run_id=run_id)
        .order_by(BenchmarkModelResult.success_rate.desc())
        .all()
    )


def get_benchmark_model_history(
    session: Session,
    model: str,
    limit: int = 20,
) -> list[BenchmarkModelResult]:
    """Return recent benchmark results for a specific model, newest first."""
    return (
        session.query(BenchmarkModelResult)
        .filter_by(model=model)
        .join(BenchmarkRun, BenchmarkRun.id == BenchmarkModelResult.run_id)
        .order_by(BenchmarkRun.started_at.desc())
        .limit(limit)
        .all()
    )


def get_previous_benchmark_results(
    session: Session,
    current_run_id: int,
) -> list[BenchmarkModelResult]:
    """Return model results from the benchmark run immediately before *current_run_id*."""
    current = session.query(BenchmarkRun).filter_by(id=current_run_id).first()
    if current is None:
        return []
    previous = (
        session.query(BenchmarkRun)
        .filter(BenchmarkRun.id != current_run_id)
        .filter(BenchmarkRun.started_at < current.started_at)
        .filter(BenchmarkRun.status == "completed")
        .order_by(BenchmarkRun.started_at.desc())
        .first()
    )
    if previous is None:
        return []
    return get_benchmark_results(session, previous.id)


# ------------------------------------------------------------------
# Payment helpers
# ------------------------------------------------------------------


def record_payment(
    session: Session,
    match_id: int,
    amount: float,
    paid_date: datetime | None = None,
    method: str | None = None,
    notes: str | None = None,
) -> PaymentRecord:
    """Record a payment against a match and update the match's payment totals."""
    payment = PaymentRecord(
        match_id=match_id,
        amount=amount,
        paid_date=paid_date or datetime.now(UTC),
        method=method,
        notes=notes,
    )
    session.add(payment)

    # Update match aggregate
    match = session.query(MatchRecord).filter_by(id=match_id).first()
    if match:
        match.paid_amount = (match.paid_amount or 0.0) + amount
        match.paid_date = payment.paid_date
        match.payment_status = _compute_payment_status(session, match)

    add_match_event(
        session,
        match_id,
        event_type="payment_recorded",
        actor="user",
        detail=f"Payment of ${amount:.2f} recorded" + (f" via {method}" if method else ""),
    )
    # Note: add_match_event commits; refresh payment after
    session.refresh(payment)
    return payment


def _compute_payment_status(session: Session, match: MatchRecord) -> str:
    """Derive payment_status by comparing paid_amount to the linked bill's balance_due."""
    bill = (
        session.query(BillRecord)
        .filter_by(document_id=match.bill_document_id)
        .order_by(BillRecord.id.desc())
        .first()
    )
    balance = _bill_balance(bill)
    paid = match.paid_amount or 0.0

    if paid <= 0:
        return "unpaid"
    if balance is None:
        # No bill or no amount info — treat any positive payment as "paid"
        return "paid"
    if paid < balance:
        return "partial"
    if paid > balance:
        return "overpaid"
    return "paid"


def _bill_balance(bill: BillRecord | None) -> float | None:
    """Extract the authoritative balance from a bill, distinguishing 0 from unknown."""
    if bill is None:
        return None
    if bill.balance_due is not None:
        return bill.balance_due
    if bill.total_amount is not None:
        return bill.total_amount
    return None


def get_payments_for_match(session: Session, match_id: int) -> list[PaymentRecord]:
    """Return all payments for a match, oldest first."""
    return (
        session.query(PaymentRecord)
        .filter_by(match_id=match_id)
        .order_by(PaymentRecord.created_at.asc())
        .all()
    )


def payment_summary(session: Session) -> dict:
    """Aggregate payment stats across all confirmed matches."""
    confirmed = session.query(MatchRecord).filter_by(status="confirmed").all()
    if not confirmed:
        return {
            "total_billed": 0.0,
            "total_due": 0.0,
            "total_paid": 0.0,
            "unpaid_count": 0,
            "partial_count": 0,
            "paid_count": 0,
            "overpaid_count": 0,
        }

    total_paid = sum(m.paid_amount or 0.0 for m in confirmed)

    # Batch-load linked bills, keeping latest per document_id
    bill_doc_ids = {m.bill_document_id for m in confirmed}
    bills = session.query(BillRecord).filter(BillRecord.document_id.in_(bill_doc_ids)).all()
    bill_map: dict[int, BillRecord] = {}
    for b in bills:
        bill_map[b.document_id] = b

    total_billed = 0.0
    total_outstanding = 0.0
    for m in confirmed:
        bill = bill_map.get(m.bill_document_id)
        balance = _bill_balance(bill)
        if balance is not None:
            total_billed += balance
            total_outstanding += max(balance - (m.paid_amount or 0.0), 0.0)

    status_counts = Counter(m.payment_status or "unpaid" for m in confirmed)
    return {
        "total_billed": total_billed,
        "total_due": total_outstanding,
        "total_paid": total_paid,
        "unpaid_count": status_counts.get("unpaid", 0),
        "partial_count": status_counts.get("partial", 0),
        "paid_count": status_counts.get("paid", 0),
        "overpaid_count": status_counts.get("overpaid", 0),
    }
