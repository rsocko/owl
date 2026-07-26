"""Tests for core.alerts — emit, dedup, module helpers, cleanup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from doc_intelligence_hub.core.alerts import (
    Alert,
    cleanup_old_alerts,
    configure,
    emit_alert,
    emit_action_queue_alerts,
    emit_eob_alerts,
    emit_statement_alerts,
    check_eob_due_dates,
    get_session,
    init_db,
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Each test gets its own in-memory alerts database."""
    configure(f"sqlite:///{tmp_path / 'alerts.db'}")
    init_db()
    yield


# ------------------------------------------------------------------
# emit_alert basics
# ------------------------------------------------------------------


class TestEmitAlert:
    def test_creates_alert(self):
        alert = emit_alert(
            alert_type="test",
            severity="medium",
            module="statements",
            title="Test alert",
        )
        assert alert is not None
        assert alert.id is not None
        assert alert.title == "Test alert"
        assert alert.severity == "medium"

    def test_invalid_severity_defaults_to_medium(self):
        alert = emit_alert(
            alert_type="test",
            severity="banana",
            module="statements",
            title="Bad severity",
        )
        assert alert is not None
        assert alert.severity == "medium"

    def test_dedup_skips_identical_unresolved(self):
        a1 = emit_alert(alert_type="dup", severity="low", module="eob", title="Same")
        a2 = emit_alert(alert_type="dup", severity="low", module="eob", title="Same")
        assert a1 is not None
        assert a2 is None  # deduplicated

    def test_dedup_allows_after_resolve(self):
        a1 = emit_alert(alert_type="dup", severity="low", module="eob", title="Same")
        # Manually resolve
        db = get_session()
        try:
            row = db.query(Alert).get(a1.id)
            row.resolved_at = datetime.now(UTC)
            db.commit()
        finally:
            db.close()

        a2 = emit_alert(alert_type="dup", severity="low", module="eob", title="Same")
        assert a2 is not None

    def test_dedup_disabled(self):
        a1 = emit_alert(
            alert_type="dup", severity="low", module="eob", title="Same", deduplicate=False
        )
        a2 = emit_alert(
            alert_type="dup", severity="low", module="eob", title="Same", deduplicate=False
        )
        assert a1 is not None
        assert a2 is not None
        assert a1.id != a2.id

    def test_metadata_stored_as_json(self):
        alert = emit_alert(
            alert_type="test",
            severity="info",
            module="action_queue",
            title="With meta",
            metadata={"key": "value"},
        )
        assert alert is not None
        assert '"key"' in alert.metadata_json


# ------------------------------------------------------------------
# Module-specific emitters
# ------------------------------------------------------------------


class TestEmitStatementAlerts:
    def test_missing_statement_high_severity(self):
        recs = [{"status": "missing", "days_late": 20, "provider_name": "Xcel"}]
        count = emit_statement_alerts(recs)
        assert count == 1
        db = get_session()
        try:
            alert = db.query(Alert).first()
            assert alert.severity == "high"
            assert "Xcel" in alert.title
        finally:
            db.close()

    def test_missing_statement_medium_severity(self):
        recs = [{"status": "missing", "days_late": 5, "provider_name": "Gas Co"}]
        count = emit_statement_alerts(recs)
        assert count == 1
        db = get_session()
        try:
            assert db.query(Alert).first().severity == "medium"
        finally:
            db.close()

    def test_overdue_statement(self):
        recs = [{"status": "overdue", "days_late": 10, "provider_name": "Water"}]
        count = emit_statement_alerts(recs)
        assert count == 1

    def test_ignores_ok_status(self):
        recs = [{"status": "ok", "provider_name": "Fine"}]
        count = emit_statement_alerts(recs)
        assert count == 0


class TestEmitEobAlerts:
    def test_unmatched_eob(self):
        count = emit_eob_alerts(
            unmatched_eobs=[{"provider_name": "Aetna", "document_id": 42}]
        )
        assert count == 1

    def test_low_confidence_match(self):
        count = emit_eob_alerts(
            low_confidence_matches=[
                {"eob_document_id": 1, "bill_document_id": 2, "score": 45}
            ]
        )
        assert count == 1

    def test_high_confidence_match(self):
        count = emit_eob_alerts(
            high_confidence_matches=[
                {"eob_document_id": 1, "bill_document_id": 2, "score": 95}
            ]
        )
        assert count == 1

    def test_empty_lists(self):
        count = emit_eob_alerts()
        assert count == 0


class TestCheckEobDueDates:
    def test_overdue_bill(self):
        yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
        bills = [{"document_id": 1, "provider_name": "Dr. Smith", "due_date": yesterday}]
        count = check_eob_due_dates(bills)
        assert count == 1

    def test_due_soon_bill(self):
        soon = (datetime.now(UTC).date() + timedelta(days=3)).isoformat()
        bills = [{"document_id": 2, "provider_name": "Clinic", "due_date": soon}]
        count = check_eob_due_dates(bills)
        assert count == 1

    def test_paid_bill_skipped(self):
        yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
        bills = [{"document_id": 3, "provider_name": "X", "due_date": yesterday, "payment_status": "paid"}]
        count = check_eob_due_dates(bills)
        assert count == 0

    def test_no_due_date_skipped(self):
        bills = [{"document_id": 4, "provider_name": "Y"}]
        count = check_eob_due_dates(bills)
        assert count == 0


class TestEmitActionQueueAlerts:
    def test_pending_action_emits_info(self):
        actions = [{"id": 1, "title": "File it", "urgency": "LOW", "status": "pending"}]
        count = emit_action_queue_alerts(actions)
        assert count == 1

    def test_completed_action_skipped(self):
        actions = [{"id": 2, "title": "Done", "urgency": "LOW", "status": "completed"}]
        count = emit_action_queue_alerts(actions)
        assert count == 0

    def test_overdue_critical_action(self):
        yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
        actions = [
            {"id": 3, "title": "Pay bill", "urgency": "CRITICAL", "status": "pending", "due_date": yesterday}
        ]
        count = emit_action_queue_alerts(actions)
        assert count == 1
        db = get_session()
        try:
            alert = db.query(Alert).first()
            assert alert.severity == "critical"
        finally:
            db.close()


# ------------------------------------------------------------------
# Cleanup
# ------------------------------------------------------------------


class TestCleanupOldAlerts:
    def test_resolves_stale_alerts(self):
        emit_alert(alert_type="old", severity="low", module="eob", title="Old one")
        # Backdate it
        db = get_session()
        try:
            alert = db.query(Alert).first()
            alert.created_at = datetime.now(UTC) - timedelta(days=60)
            db.commit()
        finally:
            db.close()

        resolved = cleanup_old_alerts(days=30)
        assert resolved == 1

    def test_keeps_recent_alerts(self):
        emit_alert(alert_type="new", severity="low", module="eob", title="Fresh")
        resolved = cleanup_old_alerts(days=30)
        assert resolved == 0
