from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from statistics import median
from string import Formatter
from typing import Any, Iterable

from doc_intelligence_hub.modules.statements.correspondent_models import (
    AcquisitionChannelSuggestion,
    AcquisitionSource,
    AnalysisExpectationMode,
    Cadence,
    CorrespondentPolicyAnalysis,
    CorrespondentProfile,
    DocumentExpectation,
    ExpectationEvidence,
    MetadataPolicy,
    MetadataPolicySuggestion,
    MissingTitleFieldFinding,
    SeriesPolicySuggestion,
    TagFamilySuggestion,
    TitleConvention,
    TitleConventionSuggestion,
    TitleRenderExample,
)
from doc_intelligence_hub.modules.statements.utils import normalize_title

_TEMPORAL_TOKEN = re.compile(r"\b\d{4}(?:-\d{2}(?:-\d{2})?| Q[1-4])\b")
_SENSITIVE_NUMBER = re.compile(r"\d{5,}")
_GROUPED_SENSITIVE_NUMBER = re.compile(r"\b(?:\d{2,4}[- ]){1,}\d{2,4}\b")
_ALPHANUMERIC_IDENTIFIER = re.compile(
    r"\b(?=[A-Za-z0-9-]{6,}\b)(?=(?:[A-Za-z0-9-]*\d){4})"
    r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b"
)
_SUBJECT_FAMILIES = frozenset(
    {"animal", "child", "dog", "entity", "patient", "person", "pet", "subject"}
)
_ONE_OFF_MARKERS = re.compile(
    r"\b(one[\s-]?time|final|closing|closure|cancellation|termination)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _ObservedDocument:
    document_id: int
    title: str
    created: date | None
    added: date | None
    tag_ids: tuple[int, ...]
    tag_names: tuple[str, ...]
    document_type_id: int | None
    document_type_name: str | None
    raw: dict[str, Any]
    period: str | None = None
    account_hint: str | None = None


@dataclass(frozen=True)
class _AnalysisGroup:
    key: str
    documents: tuple[_ObservedDocument, ...]
    statement_series_id: str | None = None
    source_statement_series_id: str | None = None
    series_name: str | None = None
    series_frequency: str | None = None
    candidate_series: bool = False
    manually_curated: bool = False


def analyze_correspondent_policy(
    *,
    profile: CorrespondentProfile,
    raw_documents: list[dict[str, Any]],
    tag_names: dict[int, str],
    document_type_names: dict[int, str],
    series: list[dict[str, Any]],
    series_documents: dict[str, list[dict[str, Any]]],
    series_overrides: dict[str, list[dict[str, Any]]],
    expectations: list[DocumentExpectation],
    acquisition_sources: list[AcquisitionSource],
    mail_rules: list[dict[str, Any]] | None = None,
) -> CorrespondentPolicyAnalysis:
    documents = _normalize_documents(raw_documents, tag_names, document_type_names)
    groups, unassigned = _build_groups(documents, series, series_documents)
    expectations_by_series = {
        item.statement_series_id: item
        for item in expectations
        if item.statement_series_id is not None
    }
    acquisition_by_id = {item.id: item for item in acquisition_sources}
    matching_mail_rule_count = sum(
        _positive_int(item.get("assign_correspondent")) == profile.correspondent_id
        and item.get("enabled", True) is not False
        for item in (mail_rules or [])
    )

    suggestions = [
        _analyze_group(
            profile=profile,
            group=group,
            expectation=expectations_by_series.get(group.statement_series_id),
            acquisition_by_id=acquisition_by_id,
            overrides=series_overrides.get(group.statement_series_id or "", []),
            matching_mail_rule_count=matching_mail_rule_count,
        )
        for group in groups
    ]
    observed_dates = sorted(item.created for item in documents if item.created is not None)
    reason_codes: list[str] = []
    if not documents:
        reason_codes.append("no_documents")
    if any(item.expectation_mode == "unknown" for item in suggestions):
        reason_codes.append("contains_unknown_behavior")
    if len([group for group in groups if group.statement_series_id]) > 1:
        reason_codes.append("multiple_existing_series")

    return CorrespondentPolicyAnalysis(
        correspondent_id=profile.correspondent_id,
        correspondent_name=profile.current_name,
        document_count=len(documents),
        observed_from=observed_dates[0] if observed_dates else None,
        observed_to=observed_dates[-1] if observed_dates else None,
        suggestions=suggestions,
        unassigned_document_ids=sorted(unassigned),
        reason_codes=reason_codes,
    )


def _normalize_documents(
    raw_documents: list[dict[str, Any]],
    tag_names: dict[int, str],
    document_type_names: dict[int, str],
) -> list[_ObservedDocument]:
    normalized = []
    for item in raw_documents:
        document_id = _positive_int(item.get("id"))
        if document_id is None:
            continue
        tag_ids = tuple(
            sorted(
                {
                    tag_id
                    for raw_tag_id in item.get("tags", [])
                    if (tag_id := _positive_int(raw_tag_id)) is not None
                }
            )
        )
        document_type_id = _positive_int(item.get("document_type"))
        normalized.append(
            _ObservedDocument(
                document_id=document_id,
                title=str(item.get("title") or "").strip(),
                created=_parse_date(item.get("created")),
                added=_parse_date(item.get("added")),
                tag_ids=tag_ids,
                tag_names=tuple(tag_names.get(tag_id, "") for tag_id in tag_ids),
                document_type_id=document_type_id,
                document_type_name=document_type_names.get(document_type_id)
                if document_type_id
                else None,
                raw=item,
                account_hint=_safe_label(item.get("account_hint")),
            )
        )
    return sorted(normalized, key=lambda item: item.document_id)


def _build_groups(
    documents: list[_ObservedDocument],
    series: list[dict[str, Any]],
    series_documents: dict[str, list[dict[str, Any]]],
) -> tuple[list[_AnalysisGroup], list[int]]:
    by_id = {item.document_id: item for item in documents}
    assigned: set[int] = set()
    groups: list[_AnalysisGroup] = []
    for item in sorted(series, key=lambda value: str(value["id"])):
        series_id = str(item["id"])
        members = []
        for membership in series_documents.get(series_id, []):
            document_id = _positive_int(membership.get("document_id"))
            document = by_id.get(document_id) if document_id is not None else None
            if document is None:
                continue
            assigned.add(document.document_id)
            members.append(
                _ObservedDocument(
                    **{
                        **document.__dict__,
                        "period": _safe_label(membership.get("period_label")),
                        "account_hint": _safe_label(membership.get("account_hint")),
                    }
                )
            )
        manually_curated = bool(item.get("manually_curated"))
        hint_groups: dict[str, list[_ObservedDocument]] = defaultdict(list)
        for member in members:
            if member.account_hint:
                hint_groups[member.account_hint].append(member)
        can_suggest_split = (
            not manually_curated
            and len(hint_groups) > 1
            and sum(len(items) for items in hint_groups.values()) == len(members)
            and all(len(items) >= 2 for items in hint_groups.values())
        )
        if can_suggest_split:
            for account_hint, hinted_members in sorted(hint_groups.items()):
                groups.append(
                    _AnalysisGroup(
                        key=f"account-hint:{series_id}:{account_hint.casefold()}",
                        documents=tuple(
                            sorted(hinted_members, key=lambda value: value.document_id)
                        ),
                        source_statement_series_id=series_id,
                        series_name=_safe_label(account_hint),
                        series_frequency=_safe_label(item.get("frequency")),
                        candidate_series=True,
                    )
                )
        elif members:
            groups.append(
                _AnalysisGroup(
                    key=f"series:{series_id}",
                    documents=tuple(sorted(members, key=lambda value: value.document_id)),
                    statement_series_id=series_id,
                    series_name=_safe_label(item.get("name")),
                    series_frequency=_safe_label(item.get("frequency")),
                    manually_curated=manually_curated,
                )
            )

    candidates: dict[tuple[str, str, str], list[_ObservedDocument]] = defaultdict(list)
    for document in documents:
        if document.document_id in assigned:
            continue
        kind = _document_kind(document.document_type_name, document.title)
        subject = _subject_label(document.tag_names) or ""
        title_pattern = normalize_title(document.title) or kind
        discriminator = document.account_hint or subject
        candidates[(kind, discriminator.casefold(), title_pattern)].append(document)

    for (kind, subject, title_pattern), members in sorted(candidates.items()):
        label = subject or title_pattern
        groups.append(
            _AnalysisGroup(
                key=f"candidate:{kind}:{subject}:{title_pattern}",
                documents=tuple(sorted(members, key=lambda value: value.document_id)),
                series_name=_safe_label(label),
                candidate_series=True,
            )
        )

    grouped_ids = {document.document_id for group in groups for document in group.documents}
    unassigned = [item.document_id for item in documents if item.document_id not in grouped_ids]
    return groups, unassigned


def _analyze_group(
    *,
    profile: CorrespondentProfile,
    group: _AnalysisGroup,
    expectation: DocumentExpectation | None,
    acquisition_by_id: dict[str, AcquisitionSource],
    overrides: list[dict[str, Any]],
    matching_mail_rule_count: int,
) -> SeriesPolicySuggestion:
    documents = list(group.documents)
    confirmed_expectation = (
        expectation if expectation and expectation.status == "confirmed" else None
    )
    kind = confirmed_expectation.kind if confirmed_expectation else _dominant_kind(documents, group)
    mode, cadence, cadence_confidence, cadence_reasons = _infer_behavior(documents)
    if confirmed_expectation:
        mode = confirmed_expectation.expectation_mode
        cadence = confirmed_expectation.cadence
        cadence_confidence = 1.0
        cadence_reasons = ["user_confirmed_policy"]

    observed_dates = sorted(item.created for item in documents if item.created is not None)
    evidence_reasons = list(cadence_reasons)
    if group.manually_curated or overrides:
        evidence_reasons.append("user_curated_series")
    if group.candidate_series:
        evidence_reasons.append(
            "account_hint_candidate"
            if group.source_statement_series_id
            else "normalized_title_candidate"
        )

    title = _infer_title_convention(
        profile=profile,
        group=group,
        kind=kind,
        preferred=confirmed_expectation.title_convention if confirmed_expectation else None,
    )
    metadata = _infer_metadata_policy(
        documents,
        preferred=confirmed_expectation.metadata_policy if confirmed_expectation else None,
    )
    acquisition = _infer_acquisition(
        documents,
        acquisition_by_id.get(confirmed_expectation.acquisition_source_id)
        if confirmed_expectation and confirmed_expectation.acquisition_source_id
        else None,
        matching_mail_rule_count,
    )

    return SeriesPolicySuggestion(
        statement_series_id=group.statement_series_id,
        source_statement_series_id=group.source_statement_series_id,
        series_discriminator=_redact_sensitive_numbers(group.series_name),
        candidate_series=group.candidate_series,
        existing_expectation_id=expectation.id if expectation else None,
        kind=kind,
        expectation_mode=mode,
        cadence=cadence,
        evidence=ExpectationEvidence(
            source="user" if confirmed_expectation else "paperless",
            reason_codes=sorted(set(evidence_reasons)),
            confidence=round(cadence_confidence, 2),
            sample_size=len(documents),
            observed_from=observed_dates[0] if observed_dates else None,
            observed_to=observed_dates[-1] if observed_dates else None,
        ),
        document_ids=sorted(item.document_id for item in documents),
        title=title,
        metadata=metadata,
        acquisition=acquisition,
    )


def _infer_behavior(
    documents: list[_ObservedDocument],
) -> tuple[AnalysisExpectationMode, Cadence | None, float, list[str]]:
    dates = sorted(set(item.created for item in documents if item.created is not None))
    if len(documents) == 1 and _has_one_off_evidence(documents[0]):
        return "one_off", None, 0.85, ["explicit_one_off_marker"]
    if len(dates) < 3:
        return "unknown", None, 0.2 if dates else 0.0, ["insufficient_date_evidence"]

    month_gaps = [
        (later.year - earlier.year) * 12 + later.month - earlier.month
        for earlier, later in zip(dates, dates[1:], strict=False)
    ]
    frequencies = (
        (1, "recurring", "monthly"),
        (3, "periodic", "quarterly"),
        (12, "periodic", "annual"),
    )
    scored = [
        (sum(gap == expected for gap in month_gaps) / len(month_gaps), mode, frequency)
        for expected, mode, frequency in frequencies
    ]
    consistency, mode, frequency = max(scored, key=lambda item: (item[0], item[2]))
    if consistency >= 0.8:
        day_values = [item.day for item in dates]
        expected_day = round(median(day_values)) if max(day_values) - min(day_values) <= 5 else None
        delays = [
            (item.added - item.created).days
            for item in documents
            if item.added is not None and item.created is not None and item.added >= item.created
        ]
        cadence = Cadence(
            frequency=frequency,
            expected_day=expected_day,
            availability_delay_days=round(median(delays)) if delays else 0,
            grace_period_days=5,
        )
        confidence = min(0.98, 0.55 + consistency * 0.25 + min(len(dates), 8) * 0.025)
        return mode, cadence, confidence, [f"{frequency}_date_pattern"]

    recognized = {1, 3, 12}
    if any(gap in recognized for gap in month_gaps):
        return "unknown", None, 0.25, ["contradictory_cadence_evidence"]
    return "irregular", None, min(0.85, 0.45 + len(dates) * 0.05), ["non_periodic_date_pattern"]


def _infer_title_convention(
    *,
    profile: CorrespondentProfile,
    group: _AnalysisGroup,
    kind: str,
    preferred: TitleConvention | None,
) -> TitleConventionSuggestion:
    values = [
        _title_values(profile.current_name, group.series_name, kind, group.series_frequency, item)
        for item in group.documents
    ]
    if preferred is not None:
        template = preferred.template
        date_basis = preferred.date_basis
        reason_codes = ["user_confirmed_title_convention"]
    else:
        template, date_basis = _select_title_template(group.documents, values)
        reason_codes = ["deterministic_allowlisted_template"]

    required = _template_fields(template)
    findings = [
        MissingTitleFieldFinding(
            document_id=document.document_id,
            missing_fields=[field for field in required if values[index].get(field) in (None, "")],
        )
        for index, document in enumerate(group.documents)
        if any(values[index].get(field) in (None, "") for field in required)
    ]
    rendered: dict[int, str] = {}
    matched: list[int] = []
    overlong: list[int] = []
    for document, document_values in zip(group.documents, values, strict=True):
        if any(document_values.get(field) in (None, "") for field in required):
            continue
        result = _render_template(template, document_values)
        if len(result) > 128:
            overlong.append(document.document_id)
            continue
        rendered[document.document_id] = result
        if _comparable_title(document.title) == _comparable_title(result):
            matched.append(document.document_id)

    eligible_count = len(group.documents) - len(findings)
    coverage = len(matched) / eligible_count if eligible_count else 0.0
    if coverage < 0.5:
        reason_codes.append("low_historical_coverage")
    if findings:
        reason_codes.append("missing_required_fields")
    if overlong:
        reason_codes.append("rendered_title_too_long")

    example_candidates = sorted(
        group.documents,
        key=lambda item: (item.document_id not in matched, item.document_id),
    )
    examples = [
        TitleRenderExample(
            document_id=document.document_id,
            before=_redact_sensitive_numbers(document.title)[:128],
            after=_redact_sensitive_numbers(rendered[document.document_id])[:128],
        )
        for document in example_candidates
        if document.document_id in rendered
    ][:3]
    example = examples[0].after if examples else _synthetic_example(template, values)
    convention = TitleConvention(template=template, date_basis=date_basis, example=example)
    sample_factor = min(len(group.documents), 5) / 5
    confidence = min(1.0, coverage * 0.75 + sample_factor * 0.25)

    return TitleConventionSuggestion(
        convention=convention,
        coverage=round(coverage, 2),
        confidence=round(confidence, 2),
        exception_document_ids=sorted(
            document.document_id
            for document in group.documents
            if document.document_id not in matched or document.document_id in overlong
        ),
        examples=examples,
        missing_required_fields=findings,
        reason_codes=sorted(set(reason_codes)),
    )


def _select_title_template(
    documents: tuple[_ObservedDocument, ...],
    values: list[dict[str, str | date | None]],
) -> tuple[str, str]:
    available = {
        field
        for field in ("correspondent", "series", "subject", "kind", "period", "document_date")
        if sum(bool(item.get(field)) for item in values) / len(values) >= 0.8
    }
    sequences = [
        ("correspondent", "series", "subject", "kind", "period"),
        ("correspondent", "series", "kind", "period"),
        ("series", "kind", "period"),
        ("correspondent", "subject", "kind", "document_date"),
        ("correspondent", "kind", "document_date"),
        ("subject", "kind", "document_date"),
        ("kind", "document_date"),
        ("series", "period"),
    ]
    candidates: list[tuple[float, int, bool, str, str]] = []
    for sequence in sequences:
        fields = tuple(field for field in sequence if field in available)
        if not fields or "kind" not in fields:
            continue
        date_basis = "period" if "period" in fields else "document_date"
        for separator in (" - ", " | ", " "):
            template = separator.join(f"{{{field}}}" for field in fields)
            matches = 0
            eligible = 0
            for document, document_values in zip(documents, values, strict=True):
                if any(not document_values.get(field) for field in fields):
                    continue
                eligible += 1
                rendered = _render_template(template, document_values)
                matches += _comparable_title(document.title) == _comparable_title(rendered)
            coverage = matches / eligible if eligible else 0.0
            candidates.append((coverage, len(fields), separator == " - ", template, date_basis))
    if candidates:
        _, _, _, template, date_basis = max(
            candidates, key=lambda item: (item[0], item[1], item[2], item[3])
        )
        return template, date_basis
    return "{kind} - {document_date}", "document_date"


def _infer_metadata_policy(
    documents: list[_ObservedDocument],
    *,
    preferred: MetadataPolicy | None,
) -> MetadataPolicySuggestion:
    if preferred is not None and (
        preferred.all_of
        or preferred.any_of
        or preferred.none_of
        or preferred.required_document_type_id is not None
    ):
        return MetadataPolicySuggestion(
            policy=preferred,
            confidence=1.0,
            reason_codes=["user_confirmed_metadata_policy"],
        )
    if not documents:
        return MetadataPolicySuggestion(
            policy=MetadataPolicy(),
            confidence=0.0,
            reason_codes=["insufficient_metadata_evidence"],
        )

    tag_sets = [set(item.tag_ids) for item in documents]
    all_of = set.intersection(*tag_sets) if tag_sets else set()
    family_members: dict[str, dict[int, str]] = defaultdict(dict)
    family_document_hits: dict[str, set[int]] = defaultdict(set)
    for document in documents:
        for tag_id, tag_name in zip(document.tag_ids, document.tag_names, strict=True):
            if not tag_name:
                continue
            family = _tag_family(tag_name)
            if family is None:
                continue
            family_members[family][tag_id] = tag_name
            family_document_hits[family].add(document.document_id)

    families = []
    any_of: set[int] = set()
    for family in sorted(family_members):
        members = family_members[family]
        coverage = len(family_document_hits[family]) / len(documents)
        if len(members) < 2 or coverage < 0.8:
            continue
        child_ids = sorted(members)
        if all_of & set(child_ids):
            continue
        families.append(
            TagFamilySuggestion(
                family=_redact_sensitive_numbers(family) or "tag",
                child_tag_ids=child_ids,
                child_tag_names=[
                    _redact_sensitive_numbers(members[tag_id]) or "" for tag_id in child_ids
                ],
                coverage=round(coverage, 2),
                reason_codes=["child_tag_family_coverage"],
            )
        )
        any_of.update(child_ids)

    type_counts = Counter(
        item.document_type_id for item in documents if item.document_type_id is not None
    )
    document_type_id = None
    type_confidence = 0.0
    if type_counts:
        document_type_id, count = type_counts.most_common(1)[0]
        type_confidence = count / len(documents)
        if type_confidence < 0.8 or len(documents) < 2:
            document_type_id = None
            type_confidence = 0.0

    reasons = []
    if all_of:
        reasons.append("tags_present_on_all_documents")
    if families:
        reasons.append("tag_family_coverage")
        if len(families) > 1:
            reasons.append("multiple_tag_families_require_separate_rules")
    if document_type_id is not None:
        reasons.append("document_type_consistency")
    if not reasons:
        reasons.append("insufficient_metadata_consistency")
    constraint_confidences = [1.0 if all_of else 0.0, type_confidence]
    constraint_confidences.extend(family.coverage for family in families)
    confidence = max(constraint_confidences)
    return MetadataPolicySuggestion(
        policy=MetadataPolicy(
            all_of=sorted(all_of),
            any_of=sorted(any_of) if len(families) == 1 else [],
            required_document_type_id=document_type_id,
        ),
        confidence=round(confidence, 2),
        required_tag_families=families,
        reason_codes=reasons,
    )


def _infer_acquisition(
    documents: list[_ObservedDocument],
    preferred: AcquisitionSource | None,
    matching_mail_rule_count: int,
) -> AcquisitionChannelSuggestion:
    if preferred is not None:
        return AcquisitionChannelSuggestion(
            channel=preferred.channel,
            delivery_mode=preferred.delivery_mode,
            confidence=1.0,
            reason_codes=["user_confirmed_acquisition_source"],
            sample_size=len(documents),
        )
    evidence = [channel for item in documents if (channel := _document_channel(item.raw))]
    if matching_mail_rule_count:
        if any(channel != "paperless_mail" for channel in evidence):
            return AcquisitionChannelSuggestion(
                confidence=0.25,
                reason_codes=["contradictory_acquisition_evidence"],
                sample_size=len(evidence) + matching_mail_rule_count,
            )
        return AcquisitionChannelSuggestion(
            channel="paperless_mail",
            delivery_mode="push",
            confidence=min(0.95, 0.8 + matching_mail_rule_count * 0.03),
            reason_codes=["configured_mail_rule_evidence"],
            sample_size=len(evidence) + matching_mail_rule_count,
        )
    if not evidence:
        return AcquisitionChannelSuggestion(
            confidence=0.0,
            reason_codes=["no_source_or_mail_rule_evidence"],
            sample_size=0,
        )
    counts = Counter(evidence)
    channel, count = counts.most_common(1)[0]
    coverage = count / len(evidence)
    if len(counts) > 1 or coverage < 0.8:
        return AcquisitionChannelSuggestion(
            confidence=round(coverage * 0.4, 2),
            reason_codes=["contradictory_acquisition_evidence"],
            sample_size=len(evidence),
        )
    return AcquisitionChannelSuggestion(
        channel=channel,
        delivery_mode="push",
        confidence=round(min(0.95, 0.55 + coverage * 0.3 + min(count, 5) * 0.02), 2),
        reason_codes=[
            "mail_rule_evidence" if channel == "paperless_mail" else "ingestion_source_evidence"
        ],
        sample_size=len(evidence),
    )


def _document_channel(item: dict[str, Any]) -> str | None:
    if item.get("mail_rule") is not None or item.get("mail_rule_id") is not None:
        return "paperless_mail"
    source_values = [
        item.get("ingestion_source"),
        item.get("source"),
        item.get("created_by"),
    ]
    normalized = " ".join(str(value).casefold() for value in source_values if value is not None)
    if "mail" in normalized or "email" in normalized:
        return "paperless_mail"
    if "api" in normalized:
        return "direct_api"
    if item.get("storage_path") is not None or item.get("storage_path_id") is not None:
        return "linked_storage"
    return None


def _title_values(
    correspondent: str,
    series_name: str | None,
    kind: str,
    series_frequency: str | None,
    document: _ObservedDocument,
) -> dict[str, str | date | None]:
    return {
        "correspondent": _redact_sensitive_numbers(correspondent),
        "series": _redact_sensitive_numbers(series_name),
        "kind": kind.replace("_", " ").title(),
        "period": document.period or _period_from_date(document.created, series_frequency),
        "document_date": document.created,
        "subject": _subject_label(document.tag_names),
    }


def _period_from_date(value: date | None, frequency: str | None) -> str | None:
    if value is None:
        return None
    if frequency == "quarterly":
        return f"{value.year} Q{(value.month - 1) // 3 + 1}"
    if frequency == "annual":
        return str(value.year)
    return value.strftime("%Y-%m")


def _dominant_kind(documents: list[_ObservedDocument], group: _AnalysisGroup) -> str:
    if group.statement_series_id is not None:
        return "statement"
    kinds = Counter(_document_kind(item.document_type_name, item.title) for item in documents)
    return kinds.most_common(1)[0][0] if kinds else "other"


def _document_kind(document_type: str | None, title: str) -> str:
    haystack = f"{document_type or ''} {title}".casefold()
    for kind in ("statement", "invoice", "bill", "receipt", "record"):
        if re.search(rf"\b{kind}\b", haystack):
            return kind
    return "other"


def _has_one_off_evidence(document: _ObservedDocument) -> bool:
    return bool(_ONE_OFF_MARKERS.search(" ".join((document.title, *document.tag_names))))


def _subject_label(tag_names: Iterable[str]) -> str | None:
    for name in sorted(tag_names, key=str.casefold):
        parts = re.split(r"[:/]", name, maxsplit=1)
        if len(parts) == 2 and parts[0].strip().casefold() in _SUBJECT_FAMILIES:
            return parts[1].strip() or None
    return None


def _tag_family(tag_name: str) -> str | None:
    parts = re.split(r"[:/]", tag_name, maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        return None
    return parts[0].strip()


def _template_fields(template: str) -> list[str]:
    return sorted(
        {
            field_name
            for _, field_name, _, _ in Formatter().parse(template)
            if field_name is not None
        }
    )


def _render_template(template: str, values: dict[str, str | date | None]) -> str:
    rendered = template.format(
        **{
            field: value.isoformat() if isinstance(value, date) else value
            for field, value in values.items()
        }
    )
    return rendered


def _synthetic_example(
    template: str,
    values: list[dict[str, str | date | None]],
) -> str:
    sample = dict(values[0]) if values else {}
    defaults: dict[str, str] = {
        "correspondent": "Correspondent",
        "series": "Series",
        "kind": "Document",
        "period": "2026-01",
        "document_date": "2026-01-01",
        "subject": "Subject",
    }
    return _redact_sensitive_numbers(
        template.format(
            **{field: sample.get(field) or default for field, default in defaults.items()}
        )
    )[:128]


def _comparable_title(value: str) -> str:
    return re.sub(r"\s+", " ", (_redact_sensitive_numbers(value) or "").strip()).casefold()


def _redact_sensitive_numbers(value: Any) -> str | None:
    if value is None:
        return None
    temporal_tokens: list[str] = []

    def preserve_temporal(match: re.Match[str]) -> str:
        temporal_tokens.append(match.group(0))
        return f"__TEMPORAL_{chr(65 + len(temporal_tokens) - 1)}__"

    redacted = _TEMPORAL_TOKEN.sub(preserve_temporal, str(value))
    redacted = _GROUPED_SENSITIVE_NUMBER.sub("****", redacted)
    redacted = _ALPHANUMERIC_IDENTIFIER.sub("****", redacted)
    redacted = _SENSITIVE_NUMBER.sub("****", redacted)
    for index, token in enumerate(temporal_tokens):
        redacted = redacted.replace(f"__TEMPORAL_{chr(65 + index)}__", token)
    return redacted


def _safe_label(value: Any) -> str | None:
    if value is None:
        return None
    label = str(value).strip()
    return label or None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
