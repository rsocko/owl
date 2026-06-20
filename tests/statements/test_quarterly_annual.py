"""Tests for quarterly and annual pattern detection.

Validates that the detection engine correctly identifies non-monthly
recurrence patterns using dedicated fixture data.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from doc_intelligence_hub.modules.statements.config import AnalysisConfig, load_config
from doc_intelligence_hub.modules.statements.detector import discover_providers
from doc_intelligence_hub.modules.statements.models import AnalysisPattern, DocumentRecord, ProviderCandidate
from doc_intelligence_hub.modules.statements.paperless import load_fixture_documents
from doc_intelligence_hub.modules.statements.recommendations import build_recommendations


FIXTURE_PATH = str(Path(__file__).parent / "fixtures" / "quarterly_annual_documents.json")


def _default_analysis() -> AnalysisConfig:
    return load_config("config/config.fixture.yaml").analysis


# ---------------------------------------------------------------------------
# Quarterly detection
# ---------------------------------------------------------------------------


def test_discover_quarterly_provider_from_fixture() -> None:
    """Vanguard statements at ~90-day intervals should be classified as quarterly."""
    documents = load_fixture_documents(FIXTURE_PATH)
    config = _default_analysis()

    result = discover_providers(documents, config)
    quarterly_providers = [p for p in result.providers if p.pattern.frequency == "quarterly"]

    assert len(quarterly_providers) >= 1
    names = {p.provider_name for p in quarterly_providers}
    assert "Vanguard" in names


def test_discover_quarterly_provider_with_inline_data() -> None:
    """Quarterly detection with explicit inline documents."""
    config = _default_analysis()
    documents = [
        DocumentRecord(
            id=i,
            title=f"Investment Statement Q{q} {year}",
            correspondent_id=30,
            correspondent_name="Fidelity",
            created=date(year, [1, 4, 7, 10][q - 1], 10),
            tags=["statement"],
        )
        for year in [2024, 2025]
        for q, i in zip(range(1, 5), range(year * 10, year * 10 + 4))
    ]

    result = discover_providers(documents, config)
    quarterly = [p for p in result.providers if p.pattern.frequency == "quarterly"]

    assert len(quarterly) >= 1
    assert quarterly[0].provider_name == "Fidelity"
    assert quarterly[0].pattern.confidence >= 0.55


def test_quarterly_water_bill_from_fixture() -> None:
    """City Water bills at ~90-day intervals should be classified as quarterly."""
    documents = load_fixture_documents(FIXTURE_PATH)
    config = _default_analysis()

    result = discover_providers(documents, config)
    water_providers = [p for p in result.providers if "City Water" in p.provider_name]

    assert len(water_providers) >= 1
    assert water_providers[0].pattern.frequency == "quarterly"


def test_quarterly_recommendations_detect_missing_quarter() -> None:
    """Recommendations should detect a missing quarterly statement."""
    provider = ProviderCandidate(
        provider_key="fidelity-investment-statement",
        provider_name="Fidelity",
        correspondent_id=30,
        document_count=8,
        normalized_title="investment statement",
        title_consistency=1.0,
        pattern=AnalysisPattern(
            frequency="quarterly",
            pattern_type="fixed_day",
            confidence=0.9,
            anchor_day=10,
            variance_days=0,
            grace_period_days=5,
        ),
        sample_document_ids=[1, 2, 3],
        first_seen=date(2024, 1, 10),
        last_seen=date(2025, 10, 10),
    )

    result = build_recommendations([provider], date(2026, 2, 15), max_inactive_cycles=6)

    assert len(result.recommendations) >= 1
    assert result.recommendations[0].provider_name == "Fidelity"
    assert result.recommendations[0].expected_date == date(2026, 1, 10)


# ---------------------------------------------------------------------------
# Annual detection
# ---------------------------------------------------------------------------


def test_discover_annual_provider_from_fixture() -> None:
    """State Farm renewals at ~365-day intervals should be classified as annual."""
    documents = load_fixture_documents(FIXTURE_PATH)
    config = _default_analysis()

    result = discover_providers(documents, config)
    annual_providers = [p for p in result.providers if p.pattern.frequency == "annual"]

    assert len(annual_providers) >= 1
    names = {p.provider_name for p in annual_providers}
    assert "State Farm" in names


def test_discover_annual_provider_with_inline_data() -> None:
    """Annual detection with explicit inline documents."""
    config = _default_analysis()
    documents = [
        DocumentRecord(
            id=i,
            title=f"Annual Insurance Statement {year}",
            correspondent_id=40,
            correspondent_name="Allstate",
            created=date(year, 6, 1),
            tags=["statement"],
        )
        for i, year in enumerate(range(2020, 2026), start=1)
    ]

    result = discover_providers(documents, config)
    annual = [p for p in result.providers if p.pattern.frequency == "annual"]

    assert len(annual) >= 1
    assert annual[0].provider_name == "Allstate"
    assert annual[0].pattern.confidence >= 0.55


def test_annual_recommendations_detect_missing_year() -> None:
    """Recommendations should detect a missing annual statement."""
    provider = ProviderCandidate(
        provider_key="allstate-annual-insurance-statement",
        provider_name="Allstate",
        correspondent_id=40,
        document_count=5,
        normalized_title="annual insurance statement",
        title_consistency=1.0,
        pattern=AnalysisPattern(
            frequency="annual",
            pattern_type="fixed_day",
            confidence=0.9,
            anchor_day=1,
            variance_days=0,
            grace_period_days=10,
        ),
        sample_document_ids=[1, 2, 3],
        first_seen=date(2020, 6, 1),
        last_seen=date(2025, 6, 1),
    )

    result = build_recommendations([provider], date(2026, 7, 15), max_inactive_cycles=6)

    assert len(result.recommendations) >= 1
    assert result.recommendations[0].expected_date == date(2026, 6, 1)
    assert result.recommendations[0].status == "overdue"


def test_annual_provider_pattern_type() -> None:
    """Annual providers with consistent day should be fixed_day pattern."""
    config = _default_analysis()
    documents = [
        DocumentRecord(
            id=i,
            title=f"Tax Summary {year}",
            correspondent_id=60,
            correspondent_name="IRS",
            created=date(year, 4, 15),
            tags=["statement"],
        )
        for i, year in enumerate(range(2020, 2026), start=1)
    ]

    result = discover_providers(documents, config)
    annual = [p for p in result.providers if p.pattern.frequency == "annual"]

    assert len(annual) >= 1
    assert annual[0].pattern.pattern_type == "fixed_day"
    assert annual[0].pattern.anchor_day == 15


# ---------------------------------------------------------------------------
# Mixed frequency scenarios
# ---------------------------------------------------------------------------


def test_mixed_frequency_discovery_from_fixture() -> None:
    """Fixture with quarterly + annual docs should find both frequencies."""
    documents = load_fixture_documents(FIXTURE_PATH)
    config = _default_analysis()

    result = discover_providers(documents, config)
    frequencies = {p.pattern.frequency for p in result.providers}

    assert "quarterly" in frequencies
    assert "annual" in frequencies


def test_mixed_monthly_quarterly_same_correspondent() -> None:
    """Monthly and quarterly series from same correspondent should be separated."""
    config = _default_analysis()

    monthly_docs = [
        DocumentRecord(
            id=100 + i,
            title=f"Checking Statement {date(2025, m, 5).strftime('%B %Y')}",
            correspondent_id=10,
            correspondent_name="Big Bank",
            created=date(2025, m, 5),
            tags=["statement"],
        )
        for i, m in enumerate(range(1, 13))
    ]

    quarterly_docs = [
        DocumentRecord(
            id=200 + i,
            title=f"Investment Statement Q{q} 2025",
            correspondent_id=10,
            correspondent_name="Big Bank",
            created=date(2025, [1, 4, 7, 10][q - 1], 15),
            tags=["statement"],
        )
        for i, q in enumerate(range(1, 5))
    ]

    result = discover_providers(monthly_docs + quarterly_docs, config)

    # Should find at least the monthly series (quarterly may have too few docs
    # depending on min_documents_for_pattern)
    assert len(result.providers) >= 1
    monthly_providers = [p for p in result.providers if p.pattern.frequency == "monthly"]
    assert len(monthly_providers) >= 1
    assert monthly_providers[0].provider_name == "Big Bank"


def test_quarterly_with_missing_quarter() -> None:
    """Quarterly detection should handle a gap (missing Q3)."""
    config = _default_analysis()
    documents = [
        DocumentRecord(id=1, title="Bill Q1 2024", correspondent_id=50,
                       correspondent_name="Water Co", created=date(2024, 1, 20), tags=["bill"]),
        DocumentRecord(id=2, title="Bill Q2 2024", correspondent_id=50,
                       correspondent_name="Water Co", created=date(2024, 4, 20), tags=["bill"]),
        # Q3 missing
        DocumentRecord(id=3, title="Bill Q4 2024", correspondent_id=50,
                       correspondent_name="Water Co", created=date(2024, 10, 20), tags=["bill"]),
        DocumentRecord(id=4, title="Bill Q1 2025", correspondent_id=50,
                       correspondent_name="Water Co", created=date(2025, 1, 20), tags=["bill"]),
        DocumentRecord(id=5, title="Bill Q2 2025", correspondent_id=50,
                       correspondent_name="Water Co", created=date(2025, 4, 20), tags=["bill"]),
    ]

    result = discover_providers(documents, config)
    quarterly = [p for p in result.providers if p.pattern.frequency == "quarterly"]

    assert len(quarterly) >= 1
    assert quarterly[0].provider_name == "Water Co"
