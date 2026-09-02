"""Stage-2 deterministic stratified sampling.

Selects a reproducible sample of documents for PDF profiling, stratified by
preliminary score bucket, document type, correspondent bucket, age bucket,
downstream outcome bucket, and content-length bucket — per the design doc's
Stage 2 stratification list. Selection order within each stratum is decided
by a seeded stable hash of ``(seed, document_id)`` so the same seed always
produces the same sample regardless of input ordering or database engine.
"""

from __future__ import annotations

import dataclasses
import hashlib
from datetime import UTC, datetime


@dataclasses.dataclass(frozen=True)
class SampleCandidate:
    """Minimal view of a Stage-1 assessment needed to build strata."""

    document_id: int
    preliminary_score: int
    document_type: str | None
    correspondent: str | None
    created: str | None  # ISO date/datetime string, may be None
    downstream_outcome: str | None
    content_length: int


@dataclasses.dataclass(frozen=True)
class SampleDecision:
    document_id: int
    stratum_key: str
    selection_rank: int


def _score_bucket(score: int) -> str:
    if score >= 80:
        return "score_high"
    if score >= 50:
        return "score_medium"
    return "score_low"


def _content_length_bucket(length: int) -> str:
    if length < 200:
        return "len_short"
    if length < 2000:
        return "len_medium"
    return "len_long"


def _age_bucket(created: str | None, *, now: datetime | None = None) -> str:
    if not created:
        return "age_unknown"
    try:
        created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return "age_unknown"
    if created_dt.tzinfo is None:
        created_dt = created_dt.replace(tzinfo=UTC)
    reference = now or datetime.now(UTC)
    age_days = (reference - created_dt).days
    if age_days < 0:
        return "age_unknown"
    if age_days <= 365:
        return "age_0_1y"
    if age_days <= 365 * 3:
        return "age_1_3y"
    return "age_3y_plus"


def stratum_key(candidate: SampleCandidate, *, now: datetime | None = None) -> str:
    """Build the deterministic stratum key for one candidate."""
    parts = [
        _score_bucket(candidate.preliminary_score),
        f"type:{candidate.document_type or 'unknown'}",
        f"corr:{candidate.correspondent or 'unknown'}",
        _age_bucket(candidate.created, now=now),
        f"outcome:{candidate.downstream_outcome or 'unknown'}",
        _content_length_bucket(candidate.content_length),
    ]
    return "|".join(parts)


def _selection_hash(seed: str, document_id: int) -> str:
    return hashlib.sha256(f"{seed}:{document_id}".encode()).hexdigest()


def select_stratified_sample(
    candidates: list[SampleCandidate],
    *,
    seed: str,
    target_size: int,
    min_per_stratum: int = 2,
    now: datetime | None = None,
) -> list[SampleDecision]:
    """Deterministically select up to ``target_size`` documents.

    Documents are grouped by :func:`stratum_key`. Each stratum contributes at
    least ``min_per_stratum`` documents (or all it has, if fewer), and the
    remaining budget is allocated proportionally to stratum size. Within a
    stratum, candidates are ordered by a seeded stable hash so the same seed
    and candidate set always produce the same sample, independent of the
    order ``candidates`` is passed in.
    """
    if target_size <= 0 or not candidates:
        return []

    strata: dict[str, list[SampleCandidate]] = {}
    for candidate in candidates:
        strata.setdefault(stratum_key(candidate, now=now), []).append(candidate)

    # Deterministic ordering of strata themselves (sorted by key), and of
    # candidates within a stratum (sorted by seeded hash, tie-broken by id).
    for key in strata:
        strata[key].sort(key=lambda c: (_selection_hash(seed, c.document_id), c.document_id))

    ordered_keys = sorted(strata.keys())
    total_candidates = sum(len(v) for v in strata.values())
    if total_candidates <= target_size:
        selected_keys = {key: strata[key] for key in ordered_keys}
    else:
        # First pass: guaranteed minimum per stratum (capped by stratum size).
        selected_keys = {
            key: strata[key][: min(min_per_stratum, len(strata[key]))] for key in ordered_keys
        }
        remaining_budget = target_size - sum(len(v) for v in selected_keys.values())

        # Second pass: proportional allocation of the remaining budget,
        # largest strata first (ties broken by key for determinism).
        if remaining_budget > 0:
            pool = sorted(
                ordered_keys,
                key=lambda key: (-len(strata[key]), key),
            )
            # Round-robin over strata (largest-first) so remaining budget is
            # spread deterministically rather than exhausted on one stratum.
            progress = True
            while remaining_budget > 0 and progress:
                progress = False
                for key in pool:
                    if remaining_budget <= 0:
                        break
                    already = len(selected_keys[key])
                    if already < len(strata[key]):
                        selected_keys[key] = strata[key][: already + 1]
                        remaining_budget -= 1
                        progress = True
        elif remaining_budget < 0:
            # More strata than budget even at the minimum-per-stratum floor.
            # Trim deterministically from the largest strata first so the
            # overall sample never exceeds target_size.
            trim_pool = sorted(ordered_keys, key=lambda key: (-len(selected_keys[key]), key))
            excess = -remaining_budget
            idx = 0
            while excess > 0:
                key = trim_pool[idx % len(trim_pool)]
                if len(selected_keys[key]) > 0:
                    selected_keys[key] = selected_keys[key][:-1]
                    excess -= 1
                idx += 1
                if idx > 10_000_000:  # pragma: no cover - safety valve
                    break

    decisions: list[SampleDecision] = []
    for key in ordered_keys:
        for rank, candidate in enumerate(selected_keys.get(key, [])):
            decisions.append(
                SampleDecision(
                    document_id=candidate.document_id, stratum_key=key, selection_rank=rank
                )
            )
    # Final deterministic ordering of the overall decision list.
    decisions.sort(key=lambda d: (d.stratum_key, d.selection_rank, d.document_id))
    return decisions[:target_size] if total_candidates > target_size else decisions
