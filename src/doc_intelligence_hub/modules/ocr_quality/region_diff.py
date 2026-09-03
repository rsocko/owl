"""Box-level diffing between two word-box lists for the same page.

Connects issue #134's region-inspection viewer with issue #18's candidate
comparison: given the word-box geometry for the same page from two sources
(the current live document and a candidate, or two candidates), figures out
which words *geometrically* disagree between the two — not just which words
each source individually flags via ``region_inspection.py``'s heuristics
(untouched by this module).

Matching heuristic (confirmed with reviewer before implementation): greedy
nearest-match combining text similarity and positional proximity into a
single score, highest-scoring candidate pairs claimed first. A word in
source A with no acceptable match in source B is ``removed_from_b``
(present in A, absent in B); a word in source B with no acceptable match in
A is ``added_in_b``; a matched pair whose centers are farther apart than a
page-size-relative threshold is ``shifted``. Matched pairs within tolerance
are considered unchanged and omitted from the result entirely — nothing to
highlight there.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any

_NON_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)

# Combined match score below this is not considered a match at all — the
# two words are treated as unrelated (one added, one removed) rather than
# a badly-shifted/misread pair.
_MIN_MATCH_SCORE = 0.35

# Weight given to text similarity vs. positional proximity when scoring a
# candidate match. Text carries slightly more signal: a word that reads
# identically but is a few points off is far more likely "the same word,
# minor extraction noise" than two different words that happen to sit near
# each other.
_TEXT_WEIGHT = 0.6
_POSITION_WEIGHT = 0.4

# A matched pair whose center-point distance exceeds this fraction of the
# page diagonal is classified "shifted" rather than left as unchanged.
_SHIFT_THRESHOLD_FRACTION = 0.01


@dataclass(frozen=True)
class DiffWord:
    """Minimal word-box shape this module needs — a subset of
    ``region_inspection``'s per-word output, so callers can pass either raw
    ``WordBox``-derived dicts or the full ``/regions`` payload's word
    entries without reshaping.
    """

    text: str
    x0: float
    top: float
    x1: float
    bottom: float


@dataclass(frozen=True)
class ShiftedPair:
    index_a: int
    index_b: int
    distance: float


@dataclass(frozen=True)
class DiffResult:
    """Indices are positions in the ``words_a``/``words_b`` lists as given."""

    removed_from_b: list[int]
    added_in_b: list[int]
    shifted: list[ShiftedPair]

    def to_dict(self) -> dict[str, Any]:
        return {
            "removed_from_b": self.removed_from_b,
            "added_in_b": self.added_in_b,
            "shifted": [
                {"index_a": p.index_a, "index_b": p.index_b, "distance": round(p.distance, 2)}
                for p in self.shifted
            ],
        }


def _normalize_text(text: str) -> str:
    return _NON_WORD_RE.sub("", text).lower()


def _center(word: DiffWord) -> tuple[float, float]:
    return ((word.x0 + word.x1) / 2.0, (word.top + word.bottom) / 2.0)


def _distance(a: DiffWord, b: DiffWord) -> float:
    ax, ay = _center(a)
    bx, by = _center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _match_score(a: DiffWord, b: DiffWord, *, diagonal: float) -> float:
    text_ratio = difflib.SequenceMatcher(
        None, _normalize_text(a.text), _normalize_text(b.text)
    ).ratio()
    if diagonal <= 0:
        position_score = 1.0 if _distance(a, b) == 0 else 0.0
    else:
        position_score = max(0.0, 1.0 - (_distance(a, b) / diagonal))
    return _TEXT_WEIGHT * text_ratio + _POSITION_WEIGHT * position_score


def diff_word_boxes(
    words_a: list[DiffWord],
    words_b: list[DiffWord],
    *,
    page_width: float,
    page_height: float,
) -> DiffResult:
    """Diff two word-box lists for the same page.

    Never raises for empty inputs — an empty ``words_a`` means every word
    in ``words_b`` is ``added_in_b`` (and vice versa), which is a valid
    (if extreme) result, not an error.
    """
    diagonal = (page_width**2 + page_height**2) ** 0.5
    shift_threshold = diagonal * _SHIFT_THRESHOLD_FRACTION if diagonal > 0 else 0.0

    # Score every plausible pair once, then greedily claim matches
    # highest-score-first so the best matches on the page are never
    # starved by an earlier, weaker, arbitrary pairing.
    scored_pairs: list[tuple[float, int, int]] = []
    for i, a in enumerate(words_a):
        for j, b in enumerate(words_b):
            score = _match_score(a, b, diagonal=diagonal)
            if score >= _MIN_MATCH_SCORE:
                scored_pairs.append((score, i, j))
    scored_pairs.sort(key=lambda pair: pair[0], reverse=True)

    matched_a: dict[int, int] = {}
    matched_b: dict[int, int] = {}
    for _score, i, j in scored_pairs:
        if i in matched_a or j in matched_b:
            continue
        matched_a[i] = j
        matched_b[j] = i

    removed_from_b = [i for i in range(len(words_a)) if i not in matched_a]
    added_in_b = [j for j in range(len(words_b)) if j not in matched_b]

    shifted: list[ShiftedPair] = []
    for i, j in matched_a.items():
        distance = _distance(words_a[i], words_b[j])
        if distance > shift_threshold:
            shifted.append(ShiftedPair(index_a=i, index_b=j, distance=distance))
    shifted.sort(key=lambda p: p.index_a)

    return DiffResult(
        removed_from_b=sorted(removed_from_b),
        added_in_b=sorted(added_in_b),
        shifted=shifted,
    )


def diff_word_boxes_from_dicts(
    words_a: list[dict[str, Any]],
    words_b: list[dict[str, Any]],
    *,
    page_width: float,
    page_height: float,
) -> DiffResult:
    """Convenience wrapper for API callers passing plain word dicts (e.g.
    the ``words`` entries from a ``/regions`` payload)."""

    def _to_word(raw: dict[str, Any]) -> DiffWord:
        return DiffWord(
            text=str(raw.get("text", "")),
            x0=float(raw.get("x0", 0.0)),
            top=float(raw.get("top", 0.0)),
            x1=float(raw.get("x1", 0.0)),
            bottom=float(raw.get("bottom", 0.0)),
        )

    return diff_word_boxes(
        [_to_word(w) for w in words_a],
        [_to_word(w) for w in words_b],
        page_width=page_width,
        page_height=page_height,
    )


__all__ = [
    "DiffResult",
    "DiffWord",
    "ShiftedPair",
    "diff_word_boxes",
    "diff_word_boxes_from_dicts",
]
