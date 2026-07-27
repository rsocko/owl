"""Financial reconciliation utilities for statement series.

Provides balance-based matching logic and variance detection for statement
series with financial fields populated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doc_intelligence_hub.modules.statements.models import SeriesDocument, TimelineEntry


@dataclass
class ReconciliationResult:
    """Result of reconciling a statement series' financial data."""

    series_id: str
    total_documents: int
    documents_with_amounts: int
    total_amount: float
    average_amount: float
    amount_variance: float
    anomalies: list[ReconciliationAnomaly]
    balance_continuity_score: float


@dataclass
class ReconciliationAnomaly:
    """A detected financial anomaly within a statement series."""

    document_id: str
    anomaly_type: str  # "amount_spike", "balance_gap", "missing_amount"
    description: str
    severity: str  # "low", "medium", "high"
    expected_value: float | None = None
    actual_value: float | None = None


def reconcile_series(
    documents: list[SeriesDocument],
    *,
    variance_threshold: float | None = None,
) -> ReconciliationResult:
    """Reconcile financial data within a statement series.

    Checks for:
    - Amount consistency (flags unusual spikes/drops)
    - Balance continuity (closing balance of N should ≈ opening balance of N+1)
    - Missing financial data

    Args:
        documents: Documents in the series, ordered by statement_date.
        variance_threshold: Override for amount variance detection (default: 2x std dev).
    """
    if not documents:
        return ReconciliationResult(
            series_id="",
            total_documents=0,
            documents_with_amounts=0,
            total_amount=0.0,
            average_amount=0.0,
            amount_variance=0.0,
            anomalies=[],
            balance_continuity_score=0.0,
        )

    anomalies: list[ReconciliationAnomaly] = []

    # Filter to documents with financial data
    docs_with_amounts = [d for d in documents if d.statement_amount is not None]
    amounts = [d.statement_amount for d in docs_with_amounts if d.statement_amount is not None]

    if not amounts:
        return ReconciliationResult(
            series_id=documents[0].series_id if documents else "",
            total_documents=len(documents),
            documents_with_amounts=0,
            total_amount=0.0,
            average_amount=0.0,
            amount_variance=0.0,
            anomalies=[
                ReconciliationAnomaly(
                    document_id="",
                    anomaly_type="missing_amount",
                    description="No documents have financial amounts populated",
                    severity="medium",
                )
            ],
            balance_continuity_score=0.0,
        )

    total = sum(amounts)
    avg = total / len(amounts)

    # Calculate variance
    variance = (sum((a - avg) ** 2 for a in amounts) / len(amounts)) ** 0.5
    threshold = variance_threshold if variance_threshold is not None else (2.0 * variance)

    # Detect amount anomalies
    for doc in docs_with_amounts:
        assert doc.statement_amount is not None
        deviation = abs(doc.statement_amount - avg)
        if threshold > 0 and deviation > threshold:
            severity = "high" if deviation > 3 * variance else "medium"
            anomalies.append(ReconciliationAnomaly(
                document_id=doc.document_id,
                anomaly_type="amount_spike",
                description=(
                    f"Amount ${doc.statement_amount:.2f} deviates from average "
                    f"${avg:.2f} by ${deviation:.2f}"
                ),
                severity=severity,
                expected_value=avg,
                actual_value=doc.statement_amount,
            ))

    # Check balance continuity
    balance_checks = 0
    balance_matches = 0
    sorted_docs = sorted(
        [d for d in documents if d.closing_balance is not None or d.opening_balance is not None],
        key=lambda d: d.statement_date or "",
    )

    for i in range(1, len(sorted_docs)):
        prev = sorted_docs[i - 1]
        curr = sorted_docs[i]
        if prev.closing_balance is not None and curr.opening_balance is not None:
            balance_checks += 1
            gap = abs(prev.closing_balance - curr.opening_balance)
            if gap < 0.01:
                balance_matches += 1
            else:
                anomalies.append(ReconciliationAnomaly(
                    document_id=curr.document_id,
                    anomaly_type="balance_gap",
                    description=(
                        f"Opening balance ${curr.opening_balance:.2f} doesn't match "
                        f"previous closing balance ${prev.closing_balance:.2f} (gap: ${gap:.2f})"
                    ),
                    severity="high" if gap > avg * 0.1 else "medium",
                    expected_value=prev.closing_balance,
                    actual_value=curr.opening_balance,
                ))

    continuity_score = (balance_matches / balance_checks) if balance_checks > 0 else 0.0

    return ReconciliationResult(
        series_id=documents[0].series_id if documents else "",
        total_documents=len(documents),
        documents_with_amounts=len(docs_with_amounts),
        total_amount=total,
        average_amount=avg,
        amount_variance=variance,
        anomalies=anomalies,
        balance_continuity_score=continuity_score,
    )
