from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import date

from doc_intelligence_hub.modules.statements.config import AnalysisConfig
from doc_intelligence_hub.modules.statements.models import (
    AnalysisPattern,
    DiscoveryDiagnosticEntry,
    DiscoveryDiagnosticResult,
    DiscoveryResult,
    DocumentRecord,
    ProviderCandidate,
)
from doc_intelligence_hub.modules.statements.utils import (
    is_last_business_day,
    is_last_day_of_month,
    normalize_title,
    slugify,
)


def discover_providers(documents: list[DocumentRecord], config: AnalysisConfig) -> DiscoveryResult:
    grouped: dict[tuple[int | None, str], list[DocumentRecord]] = defaultdict(list)
    for document in documents:
        grouped[(document.correspondent_id, document.correspondent_name)].append(document)

    providers: list[ProviderCandidate] = []
    for (correspondent_id, correspondent_name), provider_docs in grouped.items():
        providers.extend(
            analyze_correspondent(correspondent_id, correspondent_name, provider_docs, config)
        )

    providers.sort(
        key=lambda item: (item.provider_name.lower(), item.normalized_title, item.last_seen),
        reverse=False,
    )
    return DiscoveryResult(analyzed_documents=len(documents), providers=providers)


def debug_discovery(
    documents: list[DocumentRecord], config: AnalysisConfig, limit: int = 20
) -> DiscoveryDiagnosticResult:
    grouped: dict[tuple[int | None, str], list[DocumentRecord]] = defaultdict(list)
    for document in documents:
        grouped[(document.correspondent_id, document.correspondent_name)].append(document)

    diagnostics: list[DiscoveryDiagnosticEntry] = []
    accepted_count = 0
    for (correspondent_id, correspondent_name), provider_docs in grouped.items():
        current = _diagnose_correspondent(
            correspondent_id, correspondent_name, provider_docs, config
        )
        accepted_count += sum(1 for item in current if item.status == "accepted")
        diagnostics.extend(current)

    diagnostics.sort(
        key=lambda item: (
            item.status != "accepted",
            -item.document_count,
            item.correspondent_name.lower(),
            item.normalized_title,
        )
    )
    return DiscoveryDiagnosticResult(
        analyzed_documents=len(documents),
        accepted_providers=accepted_count,
        groups=diagnostics[:limit],
    )


def analyze_correspondent(
    correspondent_id: int | None,
    correspondent_name: str,
    documents: list[DocumentRecord],
    config: AnalysisConfig,
) -> list[ProviderCandidate]:
    statement_like_documents = [
        document for document in documents if _is_statement_like(document, config)
    ]
    if len(statement_like_documents) < config.min_documents_for_pattern:
        return []

    grouped_by_title: dict[str, list[DocumentRecord]] = defaultdict(list)
    for document in statement_like_documents:
        normalized_title = normalize_title(document.title)
        if not normalized_title:
            continue
        grouped_by_title[normalized_title].append(document)

    candidates: list[ProviderCandidate] = []
    for normalized_title, grouped_documents in grouped_by_title.items():
        if len(grouped_documents) < config.min_documents_for_pattern:
            continue
        candidate = analyze_group(
            correspondent_id,
            correspondent_name,
            grouped_documents,
            config,
            normalized_title_hint=normalized_title,
        )
        if candidate is not None:
            candidates.append(candidate)

    if candidates:
        return candidates

    candidate = analyze_group(
        correspondent_id, correspondent_name, statement_like_documents, config
    )
    return [candidate] if candidate is not None else []


def _diagnose_correspondent(
    correspondent_id: int | None,
    correspondent_name: str,
    documents: list[DocumentRecord],
    config: AnalysisConfig,
) -> list[DiscoveryDiagnosticEntry]:
    statement_like_documents = [
        document for document in documents if _is_statement_like(document, config)
    ]
    if not statement_like_documents:
        return []

    grouped_by_title: dict[str, list[DocumentRecord]] = defaultdict(list)
    for document in statement_like_documents:
        normalized_title = normalize_title(document.title)
        if normalized_title:
            grouped_by_title[normalized_title].append(document)

    return [
        _build_diagnostic_entry(
            correspondent_id, correspondent_name, normalized_title, grouped_documents, config
        )
        for normalized_title, grouped_documents in grouped_by_title.items()
    ]


def analyze_group(
    correspondent_id: int | None,
    correspondent_name: str,
    documents: list[DocumentRecord],
    config: AnalysisConfig,
    normalized_title_hint: str | None = None,
) -> ProviderCandidate | None:
    eligible_documents = [
        document for document in documents if _is_statement_like(document, config)
    ]
    if len(eligible_documents) < config.min_documents_for_pattern:
        return None

    ordered = sorted(eligible_documents, key=lambda item: item.created)
    if len(ordered) < 2:
        return None

    frequency = _classify_frequency([document.created for document in ordered], config)
    if frequency is None:
        return None

    normalized = [normalize_title(document.title) for document in ordered]
    normalized_counts = Counter(pattern for pattern in normalized if pattern)
    if not normalized_counts:
        return None

    dominant_pattern, dominant_count = normalized_counts.most_common(1)[0]
    if normalized_title_hint and normalized_title_hint in normalized_counts:
        dominant_pattern = normalized_title_hint
        dominant_count = normalized_counts[normalized_title_hint]
    title_consistency = dominant_count / len(normalized)
    if title_consistency < config.minimum_title_consistency:
        return None

    pattern = _build_pattern(
        frequency, [document.created for document in ordered], config.default_grace_period_days
    )
    provider_name = correspondent_name.strip() if correspondent_name else ""
    if not provider_name or provider_name.lower() == "unknown":
        provider_name = dominant_pattern.title()
    statement_name = dominant_pattern.title()

    return ProviderCandidate(
        provider_key=slugify(f"{correspondent_name}-{dominant_pattern}"),
        provider_name=provider_name,
        statement_name=statement_name,
        correspondent_id=correspondent_id,
        document_count=len(ordered),
        normalized_title=dominant_pattern,
        title_consistency=round(title_consistency, 2),
        pattern=pattern,
        sample_document_ids=[document.id for document in ordered[-3:]],
        first_seen=ordered[0].created,
        last_seen=ordered[-1].created,
    )


def _is_statement_like(document: DocumentRecord, config: AnalysisConfig) -> bool:
    lowered_tags = {tag.lower() for tag in document.tags}
    if lowered_tags.intersection({tag.lower() for tag in config.allowed_tags}):
        return True
    # Document type matching: keyword heuristics ALWAYS apply, and the configured
    # mapping can add additional types (e.g. types whose names don't contain a keyword).
    if document.document_type:
        if any(
            keyword in document.document_type.lower()
            for keyword in ("statement", "bill", "invoice", "eob")
        ):
            return True
        if (
            config.enabled_document_type_names is not None
            and document.document_type in config.enabled_document_type_names
        ):
            return True
    normalized = normalize_title(document.title)
    return any(
        keyword in normalized
        for keyword in ("statement", "bill", "invoice", "eob", "explanation of benefits")
    )


def _classify_frequency(intervals: list[int], config: AnalysisConfig) -> str | None:
    dates = sorted(intervals)
    if len(dates) < config.min_documents_for_pattern:
        return None

    monthly_coverage, monthly_density = _coverage_and_density(dates, _month_index)
    quarterly_coverage, quarterly_density = _coverage_and_density(dates, _quarter_index)
    annual_coverage, annual_density = _coverage_and_density(dates, lambda value: value.year)

    if monthly_coverage >= 0.6 and monthly_density <= 1.5:
        return "monthly"
    if quarterly_coverage >= 0.6 and quarterly_density <= 1.5:
        return "quarterly"
    if annual_coverage >= 0.8 and annual_density <= 1.2:
        return "annual"
    return None


def _coverage_and_density(dates: list[date], indexer) -> tuple[float, float]:
    first = indexer(dates[0])
    last = indexer(dates[-1])
    span = (last - first) + 1
    if span <= 0:
        return 0.0, float("inf")
    covered_periods = {indexer(value) for value in dates}
    covered = len(covered_periods)
    coverage = covered / span
    density = len(dates) / covered if covered else float("inf")
    return coverage, density


def _month_index(value: date) -> int:
    return value.year * 12 + value.month


def _quarter_index(value: date) -> int:
    quarter = ((value.month - 1) // 3) + 1
    return value.year * 4 + quarter


def _build_diagnostic_entry(
    correspondent_id: int | None,
    correspondent_name: str,
    normalized_title: str,
    documents: list[DocumentRecord],
    config: AnalysisConfig,
) -> DiscoveryDiagnosticEntry:
    ordered = sorted(documents, key=lambda item: item.created)
    if len(ordered) < config.min_documents_for_pattern:
        return DiscoveryDiagnosticEntry(
            correspondent_id=correspondent_id,
            correspondent_name=correspondent_name,
            normalized_title=normalized_title,
            document_count=len(ordered),
            status="rejected",
            reason="too_few_documents",
            sample_document_ids=[document.id for document in ordered[-3:]],
            first_seen=ordered[0].created,
            last_seen=ordered[-1].created,
        )

    frequency = _classify_frequency([document.created for document in ordered], config)
    if frequency is None:
        return DiscoveryDiagnosticEntry(
            correspondent_id=correspondent_id,
            correspondent_name=correspondent_name,
            normalized_title=normalized_title,
            document_count=len(ordered),
            status="rejected",
            reason="coverage_not_supported",
            sample_document_ids=[document.id for document in ordered[-3:]],
            first_seen=ordered[0].created,
            last_seen=ordered[-1].created,
        )

    candidate = analyze_group(
        correspondent_id,
        correspondent_name,
        ordered,
        config,
        normalized_title_hint=normalized_title,
    )
    if candidate is None:
        return DiscoveryDiagnosticEntry(
            correspondent_id=correspondent_id,
            correspondent_name=correspondent_name,
            normalized_title=normalized_title,
            document_count=len(ordered),
            status="rejected",
            reason="group_rejected",
            detected_frequency=frequency,
            sample_document_ids=[document.id for document in ordered[-3:]],
            first_seen=ordered[0].created,
            last_seen=ordered[-1].created,
        )

    return DiscoveryDiagnosticEntry(
        correspondent_id=correspondent_id,
        correspondent_name=candidate.provider_name,
        normalized_title=normalized_title,
        document_count=len(ordered),
        status="accepted",
        reason="accepted",
        detected_frequency=candidate.pattern.frequency,
        sample_document_ids=candidate.sample_document_ids,
        first_seen=candidate.first_seen,
        last_seen=candidate.last_seen,
    )


def _build_pattern(frequency: str, dates: list[date], grace_period_days: int) -> AnalysisPattern:
    days = [value.day for value in dates]
    variance = round(statistics.pstdev(days)) if len(days) > 1 else 0
    interval_days = [(dates[index + 1] - dates[index]).days for index in range(len(dates) - 1)]
    interval_variance = statistics.pstdev(interval_days) if len(interval_days) > 1 else 0
    confidence = max(0.55, min(0.99, 1.0 - (interval_variance / 10)))

    if sum(1 for value in dates if is_last_day_of_month(value)) / len(dates) >= 0.8:
        pattern_type = "last_day"
        anchor_day = None
    elif sum(1 for value in dates if is_last_business_day(value)) / len(dates) >= 0.7:
        pattern_type = "last_business_day"
        anchor_day = None
    elif variance <= 2:
        pattern_type = "fixed_day"
        anchor_day = round(statistics.mean(days))
    else:
        pattern_type = "variable"
        anchor_day = round(statistics.mean(days))

    return AnalysisPattern(
        frequency=frequency,
        pattern_type=pattern_type,
        confidence=round(confidence, 2),
        anchor_day=anchor_day,
        variance_days=max(variance, 1 if pattern_type == "variable" else 0),
        grace_period_days=grace_period_days,
    )
