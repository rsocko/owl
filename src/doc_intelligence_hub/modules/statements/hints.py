"""Provider hints processing.

Applies user-defined hints (split, merge, rename, ignore, define) to
the automatically discovered provider list.
"""

from __future__ import annotations


from doc_intelligence_hub.modules.statements.config import ProviderHint
from doc_intelligence_hub.modules.statements.models import (
    AnalysisPattern,
    DocumentRecord,
    ProviderCandidate,
)
from doc_intelligence_hub.modules.statements.utils import normalize_title, slugify


def apply_hints(
    providers: list[ProviderCandidate],
    documents: list[DocumentRecord],
    hints: list[ProviderHint],
) -> list[ProviderCandidate]:
    """Apply provider hints to modify the discovered provider list."""
    result = list(providers)

    for hint in hints:
        if hint.action == "ignore":
            result = _apply_ignore(result, hint)
        elif hint.action == "rename":
            result = _apply_rename(result, hint)
        elif hint.action == "split":
            result = _apply_split(result, documents, hint)
        elif hint.action == "merge":
            result = _apply_merge(result, hint)
        elif hint.action == "define":
            result = _apply_define(result, documents, hint)

    result.sort(key=lambda p: (p.provider_name.lower(), p.normalized_title))
    return result


def _apply_ignore(
    providers: list[ProviderCandidate], hint: ProviderHint
) -> list[ProviderCandidate]:
    """Remove providers matching the hint."""
    return [p for p in providers if not _matches_hint(p, hint)]


def _apply_rename(
    providers: list[ProviderCandidate], hint: ProviderHint
) -> list[ProviderCandidate]:
    """Rename providers matching the hint."""
    if not hint.rename_to:
        return providers
    result = []
    for p in providers:
        if _matches_hint(p, hint):
            p = p.model_copy(
                update={
                    "provider_name": hint.rename_to,
                    "provider_key": slugify(f"{hint.rename_to}-{p.normalized_title}"),
                }
            )
        result.append(p)
    return result


def _apply_split(
    providers: list[ProviderCandidate],
    documents: list[DocumentRecord],
    hint: ProviderHint,
) -> list[ProviderCandidate]:
    """Split a provider into sub-groups based on title matching."""
    if not hint.groups:
        return providers

    result = []
    for p in providers:
        if not _matches_hint(p, hint):
            result.append(p)
            continue

        # Find all documents that belong to this provider's correspondent
        provider_docs = [
            d
            for d in documents
            if d.correspondent_name
            and d.correspondent_name.lower() == (hint.correspondent or "").lower()
        ]

        split_successful = False
        for group in hint.groups:
            match_term = group.title_match.lower()
            group_docs = [d for d in provider_docs if match_term in d.title.lower()]

            if len(group_docs) >= 2:
                ordered = sorted(group_docs, key=lambda d: d.created)
                split_provider = ProviderCandidate(
                    provider_key=slugify(f"{hint.correspondent}-{group.name}"),
                    provider_name=group.name,
                    correspondent_id=p.correspondent_id,
                    document_count=len(group_docs),
                    normalized_title=normalize_title(group_docs[0].title),
                    title_consistency=1.0,
                    pattern=p.pattern.model_copy(),
                    sample_document_ids=[d.id for d in ordered[-3:]],
                    first_seen=ordered[0].created,
                    last_seen=ordered[-1].created,
                )
                result.append(split_provider)
                split_successful = True

        # Keep the original if no splits worked
        if not split_successful:
            result.append(p)

    return result


def _apply_merge(providers: list[ProviderCandidate], hint: ProviderHint) -> list[ProviderCandidate]:
    """Merge multiple providers into one."""
    if not hint.merge_keys or len(hint.merge_keys) < 2:
        return providers

    merge_set = set(hint.merge_keys)
    to_merge = [p for p in providers if p.provider_key in merge_set]
    remaining = [p for p in providers if p.provider_key not in merge_set]

    if len(to_merge) < 2:
        return providers

    # Use the first as the base, combine stats
    base = to_merge[0]
    all_doc_ids = []
    total_count = 0
    earliest = base.first_seen
    latest = base.last_seen

    for p in to_merge:
        all_doc_ids.extend(p.sample_document_ids)
        total_count += p.document_count
        if p.first_seen < earliest:
            earliest = p.first_seen
        if p.last_seen > latest:
            latest = p.last_seen

    merged_name = hint.rename_to or base.provider_name
    merged = ProviderCandidate(
        provider_key=slugify(f"{merged_name}-merged"),
        provider_name=merged_name,
        correspondent_id=base.correspondent_id,
        document_count=total_count,
        normalized_title=base.normalized_title,
        title_consistency=base.title_consistency,
        pattern=base.pattern.model_copy(),
        sample_document_ids=all_doc_ids[-3:],
        first_seen=earliest,
        last_seen=latest,
    )

    remaining.append(merged)
    return remaining


def _apply_define(
    providers: list[ProviderCandidate],
    documents: list[DocumentRecord],
    hint: ProviderHint,
) -> list[ProviderCandidate]:
    """Manually define a provider from documents matching criteria."""
    if not hint.correspondent:
        return providers

    # Find matching documents
    match_docs = [
        d
        for d in documents
        if d.correspondent_name and d.correspondent_name.lower() == hint.correspondent.lower()
    ]

    # If groups are specified, filter further
    if hint.groups:
        filtered = []
        for group in hint.groups:
            match_term = group.title_match.lower()
            filtered.extend(d for d in match_docs if match_term in d.title.lower())
        match_docs = filtered

    if len(match_docs) < 2:
        return providers

    ordered = sorted(match_docs, key=lambda d: d.created)
    provider_name = hint.rename_to or hint.correspondent
    frequency = hint.frequency or "monthly"

    defined = ProviderCandidate(
        provider_key=slugify(f"{provider_name}-defined"),
        provider_name=provider_name,
        correspondent_id=ordered[0].correspondent_id,
        document_count=len(ordered),
        normalized_title=normalize_title(ordered[0].title),
        title_consistency=1.0,
        pattern=AnalysisPattern(
            frequency=frequency,
            pattern_type="fixed_day" if hint.anchor_day else "variable",
            confidence=1.0,  # User-defined = full confidence
            anchor_day=hint.anchor_day,
            variance_days=2,
            grace_period_days=5,
        ),
        sample_document_ids=[d.id for d in ordered[-3:]],
        first_seen=ordered[0].created,
        last_seen=ordered[-1].created,
    )

    # Remove any auto-detected provider that overlaps
    result = [p for p in providers if not _matches_hint(p, hint)]
    result.append(defined)
    return result


def _matches_hint(provider: ProviderCandidate, hint: ProviderHint) -> bool:
    """Check if a provider matches the hint's targeting criteria."""
    if hint.provider_key and provider.provider_key == hint.provider_key:
        return True
    if hint.correspondent:
        return provider.provider_name.lower() == hint.correspondent.lower()
    return False
