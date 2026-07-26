from datetime import date

from doc_intelligence_hub.modules.statements.config import load_config
from doc_intelligence_hub.modules.statements.detector import discover_providers
from doc_intelligence_hub.modules.statements.models import AnalysisPattern, ProviderCandidate
from doc_intelligence_hub.modules.statements.paperless import load_fixture_documents
from doc_intelligence_hub.modules.statements.recommendations import build_recommendations


def test_build_recommendations_marks_expected_missing_items() -> None:
    config = load_config("config/config.fixture.yaml")
    documents = load_fixture_documents(config.source.fixture_path)
    discovery = discover_providers(documents, config.analysis)

    result = build_recommendations(discovery.providers, date(2026, 5, 12))
    by_provider = {item.provider_name: item for item in result.recommendations}

    assert by_provider["Chase Visa"].expected_date.isoformat() == "2026-05-03"
    assert by_provider["Chase Visa"].status == "overdue"
    assert by_provider["City Utilities"].expected_date.isoformat() == "2026-04-18"
    assert by_provider["City Utilities"].status == "overdue"


def test_build_recommendations_ignores_stale_historical_providers() -> None:
    stale_provider = ProviderCandidate(
        provider_key="legacy-bill",
        provider_name="Legacy Bill",
        correspondent_id=1,
        document_count=12,
        normalized_title="legacy bill",
        title_consistency=1.0,
        pattern=AnalysisPattern(
            frequency="monthly",
            pattern_type="fixed_day",
            confidence=0.9,
            anchor_day=15,
            variance_days=0,
            grace_period_days=5,
        ),
        sample_document_ids=[1, 2, 3],
        first_seen=date(2020, 1, 15),
        last_seen=date(2020, 12, 15),
    )

    active_provider = ProviderCandidate(
        provider_key="current-bill",
        provider_name="Current Bill",
        correspondent_id=2,
        document_count=6,
        normalized_title="current bill",
        title_consistency=1.0,
        pattern=AnalysisPattern(
            frequency="monthly",
            pattern_type="fixed_day",
            confidence=0.9,
            anchor_day=15,
            variance_days=0,
            grace_period_days=5,
        ),
        sample_document_ids=[4, 5, 6],
        first_seen=date(2025, 10, 15),
        last_seen=date(2026, 3, 15),
    )

    result = build_recommendations(
        [stale_provider, active_provider], date(2026, 5, 12), max_inactive_cycles=6
    )

    provider_names = {item.provider_name for item in result.recommendations}
    assert "Current Bill" in provider_names
    assert "Legacy Bill" not in provider_names


def test_build_recommendations_returns_only_latest_backlog_item_per_provider_by_default() -> None:
    provider = ProviderCandidate(
        provider_key="quarterly-water",
        provider_name="Quarterly Water",
        correspondent_id=3,
        document_count=4,
        normalized_title="quarterly water bill",
        title_consistency=1.0,
        pattern=AnalysisPattern(
            frequency="quarterly",
            pattern_type="fixed_day",
            confidence=0.9,
            anchor_day=1,
            variance_days=0,
            grace_period_days=5,
        ),
        sample_document_ids=[7, 8, 9],
        first_seen=date(2025, 1, 1),
        last_seen=date(2025, 9, 1),
    )

    result = build_recommendations([provider], date(2026, 4, 8), max_inactive_cycles=6)

    assert len(result.recommendations) == 1
    assert result.recommendations[0].provider_name == "Quarterly Water"
    assert result.recommendations[0].expected_date == date(2026, 3, 1)


def test_build_recommendations_can_return_multiple_backlog_items_when_requested() -> None:
    provider = ProviderCandidate(
        provider_key="quarterly-water",
        provider_name="Quarterly Water",
        correspondent_id=3,
        document_count=4,
        normalized_title="quarterly water bill",
        title_consistency=1.0,
        pattern=AnalysisPattern(
            frequency="quarterly",
            pattern_type="fixed_day",
            confidence=0.9,
            anchor_day=1,
            variance_days=0,
            grace_period_days=5,
        ),
        sample_document_ids=[7, 8, 9],
        first_seen=date(2025, 1, 1),
        last_seen=date(2025, 9, 1),
    )

    result = build_recommendations(
        [provider],
        date(2026, 4, 8),
        max_inactive_cycles=6,
        max_recommendations_per_provider=3,
    )

    assert [item.expected_date for item in result.recommendations] == [
        date(2026, 3, 1),
        date(2025, 12, 1),
    ]
