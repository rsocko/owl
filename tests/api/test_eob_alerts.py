"""Tests for EOB-specific alert emission — high-confidence matches and due-date checks.

Covers:
    - emit_eob_alerts() with high_confidence_matches parameter
    - check_eob_due_dates() for approaching, overdue, and paid bills
    - POST /api/eob/check-due-dates endpoint
    - Deduplication of alerts
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from doc_intelligence_hub.core.alerts import (
    Alert,
    check_eob_due_dates,
    emit_eob_alerts,
    get_session as get_alerts_session,
)
from doc_intelligence_hub.modules.eob_matching.database import (
    BillRecord,
    get_session as get_eob_session,
)


class TestEmitEobAlertsHighConfidence:
    """Tests for high-confidence match alert emission."""

    def test_high_confidence_match_emits_info_alert(self, app):
        count = emit_eob_alerts(
            high_confidence_matches=[
                {
                    "eob_document_id": 100,
                    "bill_document_id": 200,
                    "score": 95.0,
                    "confidence": "HIGH",
                },
            ],
        )
        assert count == 1

        db = get_alerts_session()
        try:
            alert = db.query(Alert).filter_by(alert_type="new_high_confidence_match").first()
            assert alert is not None
            assert alert.severity == "info"
            assert alert.module == "eob"
            assert "EOB #100" in alert.title
            assert "Bill #200" in alert.title
            assert "95.0%" in alert.description
            assert alert.action_url == "/eob/matches?eob=100&bill=200"
        finally:
            db.close()

    def test_multiple_high_confidence_matches(self, app):
        count = emit_eob_alerts(
            high_confidence_matches=[
                {"eob_document_id": 100, "bill_document_id": 200, "score": 95.0, "confidence": "HIGH"},
                {"eob_document_id": 101, "bill_document_id": 201, "score": 88.0, "confidence": "HIGH"},
            ],
        )
        assert count == 2

    def test_high_confidence_deduplication(self, app):
        match_data = [
            {"eob_document_id": 100, "bill_document_id": 200, "score": 95.0, "confidence": "HIGH"},
        ]
        count1 = emit_eob_alerts(high_confidence_matches=match_data)
        count2 = emit_eob_alerts(high_confidence_matches=match_data)
        assert count1 == 1
        assert count2 == 0  # Deduplicated

    def test_combined_alert_types(self, app):
        count = emit_eob_alerts(
            unmatched_eobs=[{"document_id": 300, "provider_name": "Aetna"}],
            low_confidence_matches=[
                {"eob_document_id": 102, "bill_document_id": 202, "score": 55.0, "confidence": "LOW"},
            ],
            high_confidence_matches=[
                {"eob_document_id": 100, "bill_document_id": 200, "score": 95.0, "confidence": "HIGH"},
            ],
        )
        assert count == 3


class TestCheckEobDueDates:
    """Tests for check_eob_due_dates() function."""

    def test_overdue_bill_emits_high_alert(self, app):
        yesterday = (date.today() - timedelta(days=5)).isoformat()
        count = check_eob_due_dates([
            {
                "document_id": 200,
                "provider_name": "Dr. Smith",
                "due_date": yesterday,
                "payment_status": None,
                "balance_due": 150.00,
            },
        ])
        assert count == 1

        db = get_alerts_session()
        try:
            alert = db.query(Alert).filter_by(alert_type="bill_overdue").first()
            assert alert is not None
            assert alert.severity == "high"
            assert "Dr. Smith" in alert.title
            assert "$150.00" in alert.title
            assert "day(s) late" in alert.description
        finally:
            db.close()

    def test_due_soon_bill_emits_medium_alert(self, app):
        soon = (date.today() + timedelta(days=5)).isoformat()
        count = check_eob_due_dates([
            {
                "document_id": 201,
                "provider_name": "City Hospital",
                "due_date": soon,
                "payment_status": None,
                "balance_due": 75.50,
            },
        ])
        assert count == 1

        db = get_alerts_session()
        try:
            alert = db.query(Alert).filter_by(alert_type="bill_due_soon").first()
            assert alert is not None
            assert alert.severity == "medium"
            assert "City Hospital" in alert.title
            assert "day(s) remaining" in alert.description
        finally:
            db.close()

    def test_paid_bill_skipped(self, app):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        count = check_eob_due_dates([
            {
                "document_id": 202,
                "provider_name": "Pharmacy",
                "due_date": yesterday,
                "payment_status": "paid",
                "balance_due": 0,
            },
        ])
        assert count == 0

    def test_bill_due_far_future_no_alert(self, app):
        far_future = (date.today() + timedelta(days=30)).isoformat()
        count = check_eob_due_dates([
            {
                "document_id": 203,
                "provider_name": "Lab Corp",
                "due_date": far_future,
                "payment_status": None,
                "balance_due": 200.00,
            },
        ])
        assert count == 0

    def test_bill_no_due_date_skipped(self, app):
        count = check_eob_due_dates([
            {
                "document_id": 204,
                "provider_name": "Clinic",
                "due_date": None,
                "payment_status": None,
                "balance_due": 50.00,
            },
        ])
        assert count == 0

    def test_custom_due_soon_days(self, app):
        future_14 = (date.today() + timedelta(days=14)).isoformat()
        # With default 7 days, this should NOT trigger
        count_default = check_eob_due_dates([
            {
                "document_id": 205,
                "provider_name": "Specialist",
                "due_date": future_14,
                "payment_status": None,
                "balance_due": 300.00,
            },
        ])
        assert count_default == 0

        # With 15 days threshold, it SHOULD trigger
        count_custom = check_eob_due_dates(
            [
                {
                    "document_id": 206,
                    "provider_name": "Specialist",
                    "due_date": future_14,
                    "payment_status": None,
                    "balance_due": 300.00,
                },
            ],
            due_soon_days=15,
        )
        assert count_custom == 1

    def test_due_date_deduplication(self, app):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        bill = {
            "document_id": 207,
            "provider_name": "Dentist",
            "due_date": yesterday,
            "payment_status": None,
            "balance_due": 100.00,
        }
        count1 = check_eob_due_dates([bill])
        count2 = check_eob_due_dates([bill])
        assert count1 == 1
        assert count2 == 0  # Deduplicated


class TestCheckDueDatesEndpoint:
    """Tests for POST /api/eob/check-due-dates."""

    def test_check_due_dates_empty(self, client):
        resp = client.post("/api/eob/check-due-dates", json={"due_soon_days": 7})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["bills_checked"] == 0
        assert data["alerts_emitted"] == 0

    def test_check_due_dates_with_overdue_bill(self, client):
        # Seed an overdue bill
        db = get_eob_session()
        try:
            yesterday = (date.today() - timedelta(days=2)).isoformat()
            db.add(BillRecord(
                document_id=300,
                title="Overdue bill",
                provider_name="Test Provider",
                due_date=yesterday,
                payment_status=None,
                balance_due=250.00,
            ))
            db.commit()
        finally:
            db.close()

        resp = client.post("/api/eob/check-due-dates", json={"due_soon_days": 7})
        assert resp.status_code == 200
        data = resp.json()
        assert data["bills_checked"] >= 1
        assert data["alerts_emitted"] >= 1

    def test_check_due_dates_skips_paid(self, client):
        db = get_eob_session()
        try:
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            db.add(BillRecord(
                document_id=301,
                title="Paid bill",
                provider_name="Paid Provider",
                due_date=yesterday,
                payment_status="paid",
                balance_due=0,
            ))
            db.commit()
        finally:
            db.close()

        resp = client.post("/api/eob/check-due-dates", json={"due_soon_days": 7})
        assert resp.status_code == 200
        data = resp.json()
        # paid bills are excluded from the query
        assert data["alerts_emitted"] == 0

    def test_check_due_dates_default_body(self, client):
        """Endpoint works with no request body (defaults to 7 days)."""
        resp = client.post("/api/eob/check-due-dates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["due_soon_days"] == 7
