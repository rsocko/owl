"""Tests for statement financial reconciliation (ARCH-08)."""

from __future__ import annotations

from doc_intelligence_hub.modules.statements.models import SeriesDocument, TimelineEntry
from doc_intelligence_hub.modules.statements.reconciliation import (
    reconcile_series,
)


def _make_doc(
    doc_id: str,
    series_id: str = "series-1",
    statement_date: str | None = None,
    statement_amount: float | None = None,
    opening_balance: float | None = None,
    closing_balance: float | None = None,
) -> SeriesDocument:
    return SeriesDocument(
        series_id=series_id,
        document_id=doc_id,
        title=f"Statement {doc_id}",
        statement_date=statement_date,
        statement_amount=statement_amount,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
    )


class TestReconciliation:
    """Test reconcile_series financial analysis."""

    def test_no_financial_data(self):
        """Returns anomaly when no documents have financial amounts."""
        docs = [
            _make_doc("1", statement_date="2026-01-01"),
            _make_doc("2", statement_date="2026-02-01"),
        ]
        result = reconcile_series(docs)
        assert result.documents_with_amounts == 0
        assert len(result.anomalies) == 1
        assert result.anomalies[0].anomaly_type == "missing_amount"

    def test_consistent_amounts(self):
        """No anomalies when amounts are consistent."""
        docs = [
            _make_doc("1", statement_date="2026-01-01", statement_amount=100.00),
            _make_doc("2", statement_date="2026-02-01", statement_amount=100.00),
            _make_doc("3", statement_date="2026-03-01", statement_amount=100.00),
        ]
        result = reconcile_series(docs)
        assert result.documents_with_amounts == 3
        assert result.total_amount == 300.00
        assert result.average_amount == 100.00
        assert len(result.anomalies) == 0

    def test_amount_spike_detected(self):
        """Detects an unusual amount spike with explicit threshold."""
        docs = [
            _make_doc("1", statement_date="2026-01-01", statement_amount=100.00),
            _make_doc("2", statement_date="2026-02-01", statement_amount=100.00),
            _make_doc("3", statement_date="2026-03-01", statement_amount=100.00),
            _make_doc("4", statement_date="2026-04-01", statement_amount=100.00),
            _make_doc("5", statement_date="2026-05-01", statement_amount=1000.00),
        ]
        # Use explicit threshold to make test deterministic
        result = reconcile_series(docs, variance_threshold=200.0)
        spike_anomalies = [a for a in result.anomalies if a.anomaly_type == "amount_spike"]
        assert len(spike_anomalies) >= 1
        assert spike_anomalies[0].document_id == "5"

    def test_balance_continuity_perfect(self):
        """Perfect continuity score when closing/opening balances match."""
        docs = [
            _make_doc(
                "1",
                statement_date="2026-01-01",
                statement_amount=50.00,
                opening_balance=1000.00,
                closing_balance=1050.00,
            ),
            _make_doc(
                "2",
                statement_date="2026-02-01",
                statement_amount=50.00,
                opening_balance=1050.00,
                closing_balance=1100.00,
            ),
            _make_doc(
                "3",
                statement_date="2026-03-01",
                statement_amount=50.00,
                opening_balance=1100.00,
                closing_balance=1150.00,
            ),
        ]
        result = reconcile_series(docs)
        assert result.balance_continuity_score == 1.0
        balance_gaps = [a for a in result.anomalies if a.anomaly_type == "balance_gap"]
        assert len(balance_gaps) == 0

    def test_balance_gap_detected(self):
        """Detects gaps in balance continuity."""
        docs = [
            _make_doc(
                "1",
                statement_date="2026-01-01",
                statement_amount=50.00,
                opening_balance=1000.00,
                closing_balance=1050.00,
            ),
            _make_doc(
                "2",
                statement_date="2026-02-01",
                statement_amount=50.00,
                opening_balance=1200.00,  # Gap! Should be 1050
                closing_balance=1250.00,
            ),
        ]
        result = reconcile_series(docs)
        assert result.balance_continuity_score == 0.0
        balance_gaps = [a for a in result.anomalies if a.anomaly_type == "balance_gap"]
        assert len(balance_gaps) == 1
        assert balance_gaps[0].document_id == "2"


class TestStatementFinancialFields:
    """Test that financial fields exist on models."""

    def test_series_document_has_financial_fields(self):
        """SeriesDocument has statement_amount, opening/closing balance."""
        doc = SeriesDocument(
            series_id="s1",
            document_id="d1",
            statement_amount=99.99,
            opening_balance=500.00,
            closing_balance=599.99,
            currency="USD",
        )
        assert doc.statement_amount == 99.99
        assert doc.opening_balance == 500.00
        assert doc.closing_balance == 599.99
        assert doc.currency == "USD"

    def test_timeline_entry_has_financial_fields(self):
        """TimelineEntry has financial tracking fields."""
        entry = TimelineEntry(
            document_id="d1",
            statement_amount=150.00,
            opening_balance=1000.00,
            closing_balance=1150.00,
            balance_delta=150.00,
        )
        assert entry.statement_amount == 150.00
        assert entry.balance_delta == 150.00
