from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from datetime import UTC, date, datetime

from doc_intelligence_hub.core.extractors.account_numbers import (
    normalize_masked_account_identifier,
)
from doc_intelligence_hub.modules.statements.correspondent_models import (
    AccountIdentifierAnalysis,
    AcquisitionSuggestion,
    Cadence,
    CorrespondentAnalysisResult,
    DocumentKind,
    ExpectationEvidence,
    ExpectationPolicySuggestion,
    MetadataPolicy,
    MetadataPolicySuggestion,
    ObservedSummary,
    SuggestedExpectationMode,
    TitleConvention,
    TitleConventionSuggestion,
    TitleRenderExample,
)
from doc_intelligence_hub.modules.statements.models import DocumentRecord
from doc_intelligence_hub.modules.statements.utils import normalize_title, slugify

_KIND_KEYWORDS: tuple[tuple[DocumentKind, tuple[str, ...]], ...] = (
    ("statement", ("statement",)),
    ("invoice", ("invoice",)),
    ("bill", ("bill",)),
    ("receipt", ("receipt",)),
    ("record", ("record", "eob", "explanation of benefits")),
)


def analyze_correspondent_policy(
    correspondent_id: int,
    correspondent_name: str,
    documents: list[DocumentRecord],
    statement_series: list[dict],
    *,
    analyzed_at: datetime | None = None,
    account_identifier_extraction_requested: bool = False,
) -> CorrespondentAnalysisResult:
    """Build deterministic, explainable policy suggestions from Paperless metadata."""
    profile_documents = [
        document for document in documents if document.correspondent_id == correspondent_id
    ]
    matching_series = [
        series
        for series in statement_series
        if series.get("correspondent_id") == correspondent_id
        or (
            series.get("correspondent_id") is None
            and str(series.get("correspondent_name", "")).casefold()
            == correspondent_name.casefold()
        )
    ]
    series_by_document = {
        str(document_id): series
        for series in matching_series
        for document_id in series.get("document_ids", [])
    }
    identifier_counts = Counter(
        identifier
        for document in profile_documents
        for identifier in _account_identifiers(document.title)
    )
    groups: dict[tuple[DocumentKind, str], list[DocumentRecord]] = defaultdict(list)
    group_series: dict[tuple[DocumentKind, str], dict] = {}
    for document in profile_documents:
        series = series_by_document.get(str(document.id))
        kind: DocumentKind = "statement" if series else classify_document_kind(document)
        normalized = _normalized_document_title(document) or kind
        account_key = document.account_identifier or _account_group_key(
            document.title, identifier_counts
        )
        group_key = (
            f"series:{series['id']}"
            if series
            else f"title:{normalized}:account:{account_key or 'none'}"
        )
        key = (kind, group_key)
        groups[key].append(document)
        if series:
            group_series[key] = series

    prepared_groups = [
        (
            kind,
            group_key,
            _dominant_title_pattern(grouped_documents) or kind,
            grouped_documents,
        )
        for (kind, group_key), grouped_documents in sorted(
            groups.items(), key=lambda item: (item[0][0], item[0][1])
        )
    ]
    duplicate_counts = Counter(
        (kind, normalized_title) for kind, _, normalized_title, _ in prepared_groups
    )
    duplicate_indexes: Counter[tuple[DocumentKind, str]] = Counter()
    suggestions = []
    for kind, group_key, normalized_title, grouped_documents in prepared_groups:
        duplicate_key = (kind, normalized_title)
        duplicate_indexes[duplicate_key] += 1
        candidate_number = (
            duplicate_indexes[duplicate_key] if duplicate_counts[duplicate_key] > 1 else None
        )
        suggestions.append(
            _build_suggestion(
                correspondent_id,
                correspondent_name,
                kind,
                normalized_title,
                grouped_documents,
                matching_series,
                bound_series=group_series.get((kind, group_key)),
                candidate_number=candidate_number,
            )
        )

    observed_summary = ObservedSummary(
        document_count=len(profile_documents),
        document_type_counts=dict(
            sorted(
                Counter(
                    document.document_type or "Unknown" for document in profile_documents
                ).items()
            )
        ),
        title_pattern_count=len({normalize_title(item.title) for item in profile_documents}),
        tag_family_counts=_tag_family_counts(profile_documents),
        candidate_series_count=len(suggestions),
    )
    timestamp = (analyzed_at or datetime.now(UTC)).isoformat()
    return CorrespondentAnalysisResult(
        correspondent_id=correspondent_id,
        correspondent_name=correspondent_name,
        analyzed_at=timestamp,
        observed_summary=observed_summary,
        account_identifiers=AccountIdentifierAnalysis(
            extraction_requested=account_identifier_extraction_requested,
            stored_document_count=sum(
                document.account_identifier_source == "stored"
                for document in profile_documents
            ),
            extracted_document_count=sum(
                document.account_identifier_source == "extracted"
                for document in profile_documents
            ),
            unresolved_document_count=sum(
                document.account_identifier is None for document in profile_documents
            ),
            extraction_failed_document_count=sum(
                document.account_identifier_source == "extraction_failed"
                for document in profile_documents
            ),
        ),
        suggestions=suggestions,
    )


def _build_suggestion(
    correspondent_id: int,
    correspondent_name: str,
    kind: DocumentKind,
    normalized_title: str,
    documents: list[DocumentRecord],
    statement_series: list[dict],
    *,
    bound_series: dict | None = None,
    candidate_number: int | None = None,
) -> ExpectationPolicySuggestion:
    ordered = sorted(documents, key=lambda item: (item.created, item.id))
    mode, cadence, cadence_confidence, cadence_reason = _suggest_cadence(ordered)
    base_discriminator = (
        str(bound_series["name"])
        if bound_series
        else _series_discriminator(normalized_title, kind, correspondent_name)
    )
    discriminator = (
        f"{base_discriminator} (Candidate {candidate_number})"
        if candidate_number is not None and bound_series is None
        else base_discriminator
    )
    bound_series_id = (
        str(bound_series["id"])
        if bound_series
        else _match_statement_series(discriminator, normalized_title, statement_series)
    )
    reason_codes = ["paperless_history", cadence_reason]
    if bound_series_id:
        reason_codes.append("existing_statement_series")

    return ExpectationPolicySuggestion(
        suggestion_key=slugify(
            f"{correspondent_id}-{kind}-{bound_series_id or normalized_title}"
            f"-{candidate_number or ''}"
        ),
        kind=kind,
        series_discriminator=discriminator,
        statement_series_id=bound_series_id if kind == "statement" else None,
        expectation_mode=mode,
        cadence=cadence,
        evidence=ExpectationEvidence(
            source="paperless",
            reason_codes=reason_codes,
            confidence=cadence_confidence,
            sample_size=len(ordered),
            observed_from=ordered[0].created,
            observed_to=ordered[-1].created,
        ),
        title=_suggest_title_convention(
            correspondent_name, discriminator, kind, normalized_title, ordered, cadence
        ),
        metadata=_suggest_metadata_policy(ordered),
        acquisition=AcquisitionSuggestion(
            channel="unknown",
            reason_codes=["ingestion_source_unavailable"],
        ),
        document_ids=[document.id for document in ordered],
        sample_document_ids=[document.id for document in ordered[-3:]],
    )


def _dominant_title_pattern(documents: list[DocumentRecord]) -> str:
    patterns = Counter(_normalized_document_title(document) for document in documents)
    patterns.pop("", None)
    return patterns.most_common(1)[0][0] if patterns else ""


def _account_group_key(title: str, identifier_counts: Counter[str]) -> str | None:
    identifiers = [
        identifier
        for identifier in _account_identifiers(title)
        if identifier_counts[identifier] >= 2
    ]
    normalized = [
        normalize_masked_account_identifier(f"ending {identifier}") for identifier in identifiers
    ]
    return "|".join(identifier for identifier in normalized if identifier) or None


def _account_identifiers(title: str) -> list[str]:
    without_dates = re.sub(
        r"\b(?:19|20)\d{2}(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?\b",
        " ",
        title,
    )
    return re.findall(r"\b\d{3,12}\b", without_dates)


def _normalized_document_title(document: DocumentRecord) -> str:
    return normalize_title(
        _redact_account_identifiers(document.title, document.account_identifier, replacement=" ")
    )


def _redact_account_identifiers(
    title: str,
    account_identifier: str | None = None,
    *,
    replacement: str = "[redacted]",
) -> str:
    redacted = title
    normalized_identifier = normalize_masked_account_identifier(account_identifier)
    if normalized_identifier:
        suffix = normalized_identifier.rsplit(" ", 1)[-1]
        redacted = re.sub(
            rf"(?<![A-Za-z0-9])[A-Za-z0-9*Xx.-]*{re.escape(suffix)}(?![A-Za-z0-9])",
            replacement,
            redacted,
            flags=re.IGNORECASE,
        )
    for identifier in _account_identifiers(redacted):
        redacted = re.sub(rf"\b{re.escape(identifier)}\b", replacement, redacted)
    return redacted


def classify_document_kind(document: DocumentRecord) -> DocumentKind:
    evidence = " ".join(
        [
            document.document_type or "",
            document.title,
            *document.tags,
        ]
    ).casefold()
    for kind, keywords in _KIND_KEYWORDS:
        if any(keyword in evidence for keyword in keywords):
            return kind
    return "other"


def _suggest_cadence(
    documents: list[DocumentRecord],
) -> tuple[SuggestedExpectationMode, Cadence | None, float, str]:
    if len(documents) == 1:
        return "unknown", None, 0.15, "insufficient_cadence_evidence"
    if len(documents) == 2:
        return "unknown", None, 0.25, "insufficient_cadence_evidence"

    dates = [document.created for document in documents]
    candidates = (
        ("monthly", _period_metrics(dates, lambda value: value.year * 12 + value.month), 0.6, 1.5),
        (
            "quarterly",
            _period_metrics(dates, lambda value: value.year * 4 + ((value.month - 1) // 3) + 1),
            0.6,
            1.5,
        ),
        ("annual", _period_metrics(dates, lambda value: value.year), 0.8, 1.2),
    )
    for frequency, (coverage, density), minimum_coverage, maximum_density in candidates:
        if coverage >= minimum_coverage and density <= maximum_density:
            days = [value.day for value in dates]
            expected_day = round(statistics.median(days))
            confidence = round(min(0.99, max(0.5, coverage / max(density, 1))), 2)
            mode: SuggestedExpectationMode = "recurring" if frequency == "monthly" else "periodic"
            return (
                mode,
                Cadence(frequency=frequency, expected_day=expected_day),
                confidence,
                f"{frequency}_cadence",
            )
    return "irregular", None, 0.55, "cadence_not_supported"


def _period_metrics(dates: list[date], indexer) -> tuple[float, float]:
    indices = sorted(indexer(value) for value in dates)
    span = indices[-1] - indices[0] + 1
    covered = len(set(indices))
    return covered / span, len(indices) / covered


def _series_discriminator(
    normalized_title: str, kind: DocumentKind, correspondent_name: str
) -> str:
    words = normalized_title.split()
    kind_words = {
        keyword
        for candidate_kind, keywords in _KIND_KEYWORDS
        if candidate_kind == kind
        for keyword in keywords
        if " " not in keyword
    }
    filtered = [word for word in words if word not in kind_words]
    correspondent_words = set(normalize_title(correspondent_name).split())
    without_correspondent = [word for word in filtered if word not in correspondent_words]
    selected = without_correspondent or filtered or words or [kind]
    return " ".join(selected).title()


def _match_statement_series(
    discriminator: str, normalized_title: str, statement_series: list[dict]
) -> str | None:
    exact = [
        str(series["id"])
        for series in statement_series
        if normalize_title(str(series.get("name", "")))
        in {normalize_title(discriminator), normalized_title}
    ]
    return exact[0] if len(exact) == 1 else None


def _suggest_title_convention(
    correspondent_name: str,
    discriminator: str,
    kind: DocumentKind,
    normalized_title: str,
    documents: list[DocumentRecord],
    cadence: Cadence | None,
) -> TitleConventionSuggestion:
    date_field = "period" if cadence is not None else "document_date"
    template = f"{{correspondent}} - {{series}} - {{kind}} - {{{date_field}}}"
    examples: list[TitleRenderExample] = []
    exceptions: list[int] = []
    successful = 0
    convention: TitleConvention | None = None

    values_by_document = [
        (
            document,
            {
                "correspondent": correspondent_name,
                "series": discriminator,
                "kind": kind.replace("_", " ").title(),
                date_field: period_label(document.created, cadence),
            },
        )
        for document in documents
    ]
    try:
        first_render = TitleConvention(
            template=template,
            date_basis=date_field,
            example="pending",
        ).render(values_by_document[-1][1])
        convention = TitleConvention(
            template=template,
            date_basis=date_field,
            example=first_render,
        )
    except ValueError:
        return TitleConventionSuggestion(
            coverage=0,
            exception_document_ids=[document.id for document in documents],
            reason_codes=["title_render_invalid"],
        )

    for document, values in values_by_document:
        missing_fields: list[str] = []
        rendered: str | None = None
        try:
            rendered = convention.render(values)
            rendered_pattern = normalize_title(rendered)
            if normalized_title in rendered_pattern or rendered_pattern in normalized_title:
                successful += 1
            else:
                exceptions.append(document.id)
        except ValueError as exc:
            exceptions.append(document.id)
            missing_fields = _missing_fields(str(exc))
        if len(examples) < 3:
            examples.append(
                TitleRenderExample(
                    document_id=document.id,
                    before=_redact_account_identifiers(
                        document.title, document.account_identifier
                    )[:128],
                    after=rendered,
                    missing_fields=missing_fields,
                )
            )
    return TitleConventionSuggestion(
        convention=convention,
        coverage=round(successful / len(documents), 2),
        exception_document_ids=exceptions,
        examples=examples,
        reason_codes=["deterministic_template", "normalized_title_pattern"],
    )


def period_label(value: date, cadence: Cadence | None) -> str:
    if cadence is None or cadence.frequency == "monthly":
        return value.strftime("%Y-%m") if cadence else value.isoformat()
    if cadence.frequency == "quarterly":
        return f"{value.year}-Q{((value.month - 1) // 3) + 1}"
    return str(value.year)


def _missing_fields(message: str) -> list[str]:
    prefix = "Missing required title fields: "
    if not message.startswith(prefix):
        return []
    return [field.strip() for field in message.removeprefix(prefix).split(",")]


def _suggest_metadata_policy(documents: list[DocumentRecord]) -> MetadataPolicySuggestion:
    tag_names = {
        tag_id: tag_name
        for document in documents
        for tag_id, tag_name in zip(document.tag_ids, document.tags, strict=False)
    }
    tag_sets = [set(document.tag_ids) for document in documents]
    all_of = set.intersection(*tag_sets) if tag_sets else set()
    any_of: set[int] = set()
    family_members: dict[str, set[int]] = defaultdict(set)
    for tag_id, name in tag_names.items():
        family = _tag_family(name)
        if family:
            family_members[family].add(tag_id)
    for members in family_members.values():
        if len(members) > 1 and all(members & assigned for assigned in tag_sets):
            any_of.update(members)
            all_of.difference_update(members)

    type_counts = Counter(
        document.document_type_id for document in documents if document.document_type_id is not None
    )
    required_type_id: int | None = None
    required_type_name: str | None = None
    type_coverage = 0.0
    if type_counts:
        candidate, count = type_counts.most_common(1)[0]
        type_coverage = count / len(documents)
        if type_coverage >= 0.8:
            required_type_id = candidate
            required_type_name = next(
                (
                    document.document_type
                    for document in documents
                    if document.document_type_id == candidate
                ),
                None,
            )

    reason_codes = []
    if all_of:
        reason_codes.append("tags_present_on_all_documents")
    if any_of:
        reason_codes.append("tag_family_present_on_all_documents")
    if required_type_id:
        reason_codes.append("consistent_document_type")
    if not reason_codes:
        reason_codes.append("metadata_inconsistent_or_unavailable")
    return MetadataPolicySuggestion(
        policy=MetadataPolicy(
            all_of=sorted(all_of),
            any_of=sorted(any_of),
            required_document_type_id=required_type_id,
        ),
        tag_names={tag_id: tag_names[tag_id] for tag_id in sorted(all_of | any_of)},
        required_document_type_name=required_type_name,
        confidence=round(
            max(
                len(all_of | any_of) > 0 and 1.0 or 0.0,
                type_coverage,
            ),
            2,
        ),
        reason_codes=reason_codes,
    )


def _tag_family_counts(documents: list[DocumentRecord]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for document in documents:
        for tag in document.tags:
            counts[_tag_family(tag) or tag] += 1
    return dict(sorted(counts.items(), key=lambda item: item[0].casefold()))


def _tag_family(tag_name: str) -> str | None:
    match = re.match(r"^\s*([^:]+):[^:]+\s*$", tag_name)
    return match.group(1).strip() if match else None
