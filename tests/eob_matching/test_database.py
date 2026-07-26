"""Tests for EOB Matching database persistence layer."""

import json
from datetime import UTC, datetime

import pytest

from doc_intelligence_hub.modules.eob_matching.database import (
    Base,
    BillRecord,
    EOBRecord,
    MatchRecord,
    MatchingRun,
    confirmed_matches,
    configure,
    get_engine,
    get_session,
    init_db,
    latest_runs,
    pending_matches,
    store_bill,
    store_eob,
    store_match,
    store_run,
)


@pytest.fixture()
def db():
    """Create an in-memory SQLite database for each test."""
    configure("sqlite:///:memory:")
    init_db()
    session = get_session()
    yield session
    session.close()


class TestMatchingRun:
    def test_create_and_retrieve_run(self, db):
        run = store_run(db, MatchingRun(
            documents_scanned=50,
            eobs_found=10,
            bills_found=15,
            matches_found=5,
            high_confidence=2,
            medium_confidence=2,
            low_confidence=1,
            tags_filter="medical",
        ))
        assert run.id is not None
        assert run.documents_scanned == 50
        assert run.tags_filter == "medical"
        assert run.started_at is not None

    def test_latest_runs_returns_most_recent(self, db):
        for i in range(5):
            store_run(db, MatchingRun(documents_scanned=i * 10))
        runs = latest_runs(db, limit=3)
        assert len(runs) == 3
        # Most recent first
        assert runs[0].documents_scanned == 40

    def test_run_finished_at_updated(self, db):
        run = store_run(db, MatchingRun(documents_scanned=10))
        assert run.finished_at is None
        run.finished_at = datetime.now(UTC)
        db.commit()
        db.refresh(run)
        assert run.finished_at is not None


class TestEOBRecord:
    def test_store_eob_record(self, db):
        run = store_run(db, MatchingRun())
        eob = store_eob(db, EOBRecord(
            document_id=42,
            run_id=run.id,
            title="Test EOB",
            classification_score=85.0,
            insurance_company="UnitedHealthcare",
            patient_name="John Doe",
            provider_name="Dr. Smith",
            date_of_service="2024-01-15",
            total_patient_responsibility=150.00,
        ))
        assert eob.id is not None
        assert eob.document_id == 42
        assert eob.insurance_company == "UnitedHealthcare"

    def test_eob_services_json(self, db):
        services = [{"description": "Office Visit", "cpt_code": "99213", "amount": 150.0}]
        eob = store_eob(db, EOBRecord(
            document_id=43,
            services_json=json.dumps(services),
        ))
        parsed = json.loads(eob.services_json)
        assert len(parsed) == 1
        assert parsed[0]["cpt_code"] == "99213"


class TestBillRecord:
    def test_store_bill_record(self, db):
        bill = store_bill(db, BillRecord(
            document_id=100,
            title="Medical Bill",
            provider_name="City Hospital",
            patient_name="Jane Doe",
            balance_due=250.00,
            payment_status="unpaid",
        ))
        assert bill.id is not None
        assert bill.balance_due == 250.00


class TestMatchRecord:
    def test_store_match(self, db):
        match = store_match(db, MatchRecord(
            eob_document_id=42,
            bill_document_id=100,
            score=87.5,
            confidence="HIGH",
            breakdown_date=95.0,
            breakdown_provider=90.0,
            breakdown_patient=85.0,
            breakdown_amount=80.0,
            breakdown_procedures=75.0,
        ))
        assert match.id is not None
        assert match.score == 87.5
        assert match.status == "candidate"

    def test_pending_matches(self, db):
        store_match(db, MatchRecord(
            eob_document_id=1, bill_document_id=2,
            score=90.0, confidence="HIGH",
        ))
        store_match(db, MatchRecord(
            eob_document_id=3, bill_document_id=4,
            score=60.0, confidence="LOW",
        ))
        candidates = pending_matches(db)
        assert len(candidates) == 2
        # Sorted by score desc
        assert candidates[0].score == 90.0

    def test_confirmed_matches(self, db):
        m = store_match(db, MatchRecord(
            eob_document_id=1, bill_document_id=2,
            score=85.0, confidence="HIGH",
        ))
        assert len(confirmed_matches(db)) == 0

        m.status = "confirmed"
        m.confirmed_at = datetime.now(UTC)
        db.commit()
        assert len(confirmed_matches(db)) == 1

    def test_rejected_match_not_in_pending(self, db):
        m = store_match(db, MatchRecord(
            eob_document_id=1, bill_document_id=2,
            score=55.0, confidence="LOW",
        ))
        m.status = "rejected"
        db.commit()
        assert len(pending_matches(db)) == 0
        assert len(confirmed_matches(db)) == 0

    def test_match_has_payment_fields(self, db):
        m = store_match(db, MatchRecord(
            eob_document_id=1, bill_document_id=2,
            score=85.0, confidence="HIGH",
        ))
        assert m.payment_status == "unpaid"
        assert m.paid_amount == 0.0
        assert m.paid_date is None


class TestPaymentTracking:
    def _make_confirmed_match(self, db, bill_balance=250.0):
        """Helper to create a confirmed match with a linked bill."""
        from doc_intelligence_hub.modules.eob_matching.database import PaymentRecord
        store_bill(db, BillRecord(
            document_id=100,
            provider_name="City Hospital",
            balance_due=bill_balance,
            total_amount=bill_balance,
            payment_status="unpaid",
        ))
        store_eob(db, EOBRecord(
            document_id=42,
            total_patient_responsibility=bill_balance,
        ))
        m = store_match(db, MatchRecord(
            eob_document_id=42,
            bill_document_id=100,
            score=90.0,
            confidence="HIGH",
            status="confirmed",
            confirmed_at=datetime.now(UTC),
        ))
        return m

    def test_record_payment_updates_match(self, db):
        from doc_intelligence_hub.modules.eob_matching.database import record_payment
        m = self._make_confirmed_match(db, bill_balance=250.0)
        payment = record_payment(db, match_id=m.id, amount=100.0, method="check")

        assert payment.id is not None
        assert payment.amount == 100.0
        assert payment.method == "check"

        db.refresh(m)
        assert m.paid_amount == 100.0
        assert m.payment_status == "partial"

    def test_full_payment_marks_paid(self, db):
        from doc_intelligence_hub.modules.eob_matching.database import record_payment
        m = self._make_confirmed_match(db, bill_balance=200.0)
        record_payment(db, match_id=m.id, amount=200.0)

        db.refresh(m)
        assert m.paid_amount == 200.0
        assert m.payment_status == "paid"

    def test_overpayment_detected(self, db):
        from doc_intelligence_hub.modules.eob_matching.database import record_payment
        m = self._make_confirmed_match(db, bill_balance=100.0)
        record_payment(db, match_id=m.id, amount=150.0)

        db.refresh(m)
        assert m.paid_amount == 150.0
        assert m.payment_status == "overpaid"

    def test_multiple_payments_aggregate(self, db):
        from doc_intelligence_hub.modules.eob_matching.database import record_payment, get_payments_for_match
        m = self._make_confirmed_match(db, bill_balance=300.0)
        record_payment(db, match_id=m.id, amount=100.0)
        record_payment(db, match_id=m.id, amount=100.0)

        db.refresh(m)
        assert m.paid_amount == 200.0
        assert m.payment_status == "partial"

        payments = get_payments_for_match(db, m.id)
        assert len(payments) == 2

    def test_payment_summary(self, db):
        from doc_intelligence_hub.modules.eob_matching.database import record_payment, payment_summary
        m1 = self._make_confirmed_match(db, bill_balance=200.0)
        # Create second confirmed match with different doc ids
        store_bill(db, BillRecord(
            document_id=200,
            balance_due=300.0,
            total_amount=300.0,
        ))
        m2 = store_match(db, MatchRecord(
            eob_document_id=42,
            bill_document_id=200,
            score=85.0,
            confidence="HIGH",
            status="confirmed",
            confirmed_at=datetime.now(UTC),
        ))

        record_payment(db, match_id=m1.id, amount=200.0)  # fully paid
        record_payment(db, match_id=m2.id, amount=100.0)  # partial

        summary = payment_summary(db)
        assert summary["total_due"] == 500.0  # 200 + 300
        assert summary["total_paid"] == 300.0  # 200 + 100
        assert summary["paid_count"] == 1
        assert summary["partial_count"] == 1

    def test_payment_creates_match_event(self, db):
        from doc_intelligence_hub.modules.eob_matching.database import record_payment, get_match_events
        m = self._make_confirmed_match(db)
        record_payment(db, match_id=m.id, amount=50.0, method="online")

        events = get_match_events(db, m.id)
        payment_events = [e for e in events if e.event_type == "payment_recorded"]
        assert len(payment_events) == 1
        assert "50.00" in payment_events[0].detail
        assert "online" in payment_events[0].detail
