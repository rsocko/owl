from __future__ import annotations

import calendar
from datetime import date, timedelta

from doc_intelligence_hub.modules.statements.models import (
    ProviderCandidate,
    Recommendation,
    RecommendationResult,
)
from doc_intelligence_hub.modules.statements.utils import add_months, last_business_day, month_end


def build_recommendations(
    providers: list[ProviderCandidate],
    as_of: date,
    max_inactive_cycles: int = 6,
    max_recommendations_per_provider: int = 1,
) -> RecommendationResult:
    recommendations: list[Recommendation] = []
    for provider in providers:
        if not _is_provider_active(provider, as_of, max_inactive_cycles):
            continue
        recommendations.extend(
            _provider_recommendations(provider, as_of, max_recommendations_per_provider)
        )

    recommendations.sort(key=lambda item: (item.priority, item.expected_date), reverse=True)
    return RecommendationResult(as_of=as_of, recommendations=recommendations)


def _is_provider_active(provider: ProviderCandidate, as_of: date, max_inactive_cycles: int) -> bool:
    if max_inactive_cycles <= 0:
        return True

    cursor = provider.last_seen
    for _ in range(max_inactive_cycles):
        cursor = _next_expected_date(cursor, provider)
    latest_allowed_date = cursor + timedelta(
        days=provider.pattern.grace_period_days + provider.pattern.variance_days
    )
    return as_of <= latest_allowed_date


def _provider_recommendations(
    provider: ProviderCandidate,
    as_of: date,
    max_recommendations_per_provider: int,
) -> list[Recommendation]:
    expected_dates = _expected_dates_between(provider, as_of)
    if max_recommendations_per_provider > 0:
        expected_dates = expected_dates[-max_recommendations_per_provider:]
    recommendations: list[Recommendation] = []

    for expected_date in expected_dates:
        earliest_date = expected_date - timedelta(days=1)
        latest_date = expected_date + timedelta(
            days=provider.pattern.grace_period_days + provider.pattern.variance_days
        )
        if as_of <= latest_date:
            status = "missing"
            priority = min(8, 5 + max(0, (as_of - expected_date).days))
        else:
            status = "overdue"
            priority = 10

        recommendations.append(
            Recommendation(
                provider_key=provider.provider_key,
                provider_name=provider.provider_name,
                expected_date=expected_date,
                earliest_date=earliest_date,
                latest_date=latest_date,
                status=status,
                priority=priority,
                days_late=max(0, (as_of - expected_date).days),
            )
        )
    return recommendations


def _expected_dates_between(provider: ProviderCandidate, as_of: date) -> list[date]:
    expected_dates: list[date] = []
    cursor = _next_expected_date(provider.last_seen, provider)
    while cursor <= as_of:
        expected_dates.append(cursor)
        cursor = _next_expected_date(cursor, provider)
    return expected_dates


def _next_expected_date(last_date: date, provider: ProviderCandidate) -> date:
    frequency = provider.pattern.frequency
    if frequency == "monthly":
        base = add_months(last_date, 1)
    elif frequency == "quarterly":
        base = add_months(last_date, 3)
    else:
        base = date(
            last_date.year + 1,
            last_date.month,
            min(last_date.day, calendar.monthrange(last_date.year + 1, last_date.month)[1]),
        )

    if provider.pattern.pattern_type == "last_day":
        return month_end(base)
    if provider.pattern.pattern_type == "last_business_day":
        return last_business_day(base)
    if provider.pattern.anchor_day is None:
        return base
    last_day = calendar.monthrange(base.year, base.month)[1]
    return date(base.year, base.month, min(provider.pattern.anchor_day, last_day))
