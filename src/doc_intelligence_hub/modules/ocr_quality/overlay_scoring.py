"""Overlay/readability scoring.

Requires PDF byte-derived page geometry (:class:`PdfPageData`). Any signal
that cannot be computed from the available geometry is recorded as
unavailable rather than defaulted to a favorable value, and the remaining
weights are renormalized over whatever signals *are* available. If no PDF
geometry is available at all, the overlay score itself is ``None``.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable

from doc_intelligence_hub.modules.ocr_quality.pdf_types import PdfPageData, WordBox
from doc_intelligence_hub.modules.ocr_quality.scoring_config import DEFAULT_CONFIG, ScoringConfig
from doc_intelligence_hub.modules.ocr_quality.scoring_models import (
    DocumentProfile,
    Reason,
    ScoreComponent,
    Severity,
)

# Average text-ink coverage of a page at/above this fraction earns full
# credit for the page_coverage signal; sparser (but plausible, e.g. a short
# cover letter) coverage scales down proportionally rather than failing
# outright.
_COVERAGE_SATURATION = 0.15

# Padding (page-relative points) applied around image regions when checking
# whether overlay text is roughly aligned with the underlying scanned image.
_ALIGNMENT_PADDING = 5.0

# Two word boxes are considered duplicates of each other if their centers
# are within this many points and the text matches.
_DUPLICATE_DISTANCE_TOLERANCE = 1.0

_SIGNAL_NAMES = (
    "searchable_text",
    "page_coverage",
    "bounds_sanity",
    "duplicate_overlap",
    "alignment",
    "reading_order",
    "page_integrity",
)


def score_overlay(
    *,
    pdf_pages: list[PdfPageData] | None,
    profile: DocumentProfile,
    config: ScoringConfig | None = None,
    expected_page_count: int | None = None,
) -> ScoreComponent:
    """Score overlay/readability quality from page geometry, if available."""
    cfg = config or DEFAULT_CONFIG
    reasons: list[Reason] = []
    unavailable: list[str] = []
    signals: dict[str, float | None] = {name: None for name in _SIGNAL_NAMES}

    if not pdf_pages:
        for name in _SIGNAL_NAMES:
            unavailable.append(name)
        reasons.append(
            Reason(
                code="overlay.no_geometry",
                message="No PDF page geometry was provided; overlay quality cannot be assessed.",
                severity=Severity.INFO,
                component="overlay",
            )
        )
        return ScoreComponent(
            score=None, signals=signals, reasons=reasons, unavailable_signals=unavailable
        )

    usable_pages = [p for p in pdf_pages if not p.error]

    signals["searchable_text"] = _searchable_text_signal(usable_pages)
    signals["page_coverage"] = _page_coverage_signal(usable_pages)
    signals["bounds_sanity"] = _bounds_sanity_signal(usable_pages)
    signals["duplicate_overlap"] = _duplicate_overlap_signal(usable_pages)
    signals["alignment"] = _alignment_signal(usable_pages)
    signals["reading_order"] = _reading_order_signal(usable_pages)
    signals["page_integrity"] = _page_integrity_signal(pdf_pages, expected_page_count)

    weights = cfg.overlay_weights.model_dump()
    weighted_total = 0.0
    weight_sum = 0.0
    for name in _SIGNAL_NAMES:
        value = signals[name]
        weight = weights[name]
        if value is None:
            unavailable.append(name)
            continue
        weighted_total += value * 100.0 * weight
        weight_sum += weight

    score = (weighted_total / weight_sum) if weight_sum > 0 else None

    _append_signal_reasons(signals, reasons)
    if unavailable:
        reasons.append(
            Reason(
                code="overlay.partial_signals",
                message=f"Unavailable overlay signals: {', '.join(unavailable)}.",
                severity=Severity.INFO,
                component="overlay",
            )
        )

    return ScoreComponent(
        score=round(score, 2) if score is not None else None,
        signals=signals,
        reasons=reasons,
        unavailable_signals=unavailable,
    )


def _searchable_text_signal(pages: list[PdfPageData]) -> float | None:
    if not pages:
        return None
    with_text = sum(1 for p in pages if p.has_text)
    return with_text / len(pages)


def _page_coverage_signal(pages: list[PdfPageData]) -> float | None:
    coverages = []
    for page in pages:
        if page.area <= 0 or not page.words:
            continue
        word_area = sum(max(w.x1 - w.x0, 0.0) * max(w.bottom - w.top, 0.0) for w in page.words)
        coverages.append(min(word_area / page.area, 1.0))
    if not coverages:
        return None
    avg = statistics.mean(coverages)
    return min(avg / _COVERAGE_SATURATION, 1.0)


def _bounds_sanity_signal(pages: list[PdfPageData]) -> float | None:
    total = 0
    in_bounds = 0
    for page in pages:
        if not page.words or page.width <= 0 or page.height <= 0:
            continue
        for w in page.words:
            total += 1
            if 0 <= w.x0 <= w.x1 <= page.width and 0 <= w.top <= w.bottom <= page.height:
                in_bounds += 1
    if total == 0:
        return None
    return in_bounds / total


def _duplicate_overlap_signal(pages: list[PdfPageData]) -> float | None:
    total = 0
    duplicates = 0
    for page in pages:
        words = page.words
        total += len(words)
        seen: list[WordBox] = []
        for w in words:
            is_dup = any(
                w.text == other.text
                and abs(w.x0 - other.x0) <= _DUPLICATE_DISTANCE_TOLERANCE
                and abs(w.top - other.top) <= _DUPLICATE_DISTANCE_TOLERANCE
                for other in seen
            )
            if is_dup:
                duplicates += 1
            else:
                seen.append(w)
    if total == 0:
        return None
    return 1.0 - (duplicates / total)


def _alignment_signal(pages: list[PdfPageData]) -> float | None:
    ratios = []
    for page in pages:
        if not page.images or not page.words:
            continue
        padded_images = [
            (
                img.x0 - _ALIGNMENT_PADDING,
                img.top - _ALIGNMENT_PADDING,
                img.x1 + _ALIGNMENT_PADDING,
                img.bottom + _ALIGNMENT_PADDING,
            )
            for img in page.images
        ]
        inside = 0
        for w in page.words:
            cx = (w.x0 + w.x1) / 2.0
            cy = (w.top + w.bottom) / 2.0
            if any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in padded_images):
                inside += 1
        ratios.append(inside / len(page.words))
    if not ratios:
        return None
    return statistics.mean(ratios)


def _reading_order_signal(pages: list[PdfPageData]) -> float | None:
    page_scores = []
    for page in pages:
        words = [w for w in page.words if w.text.strip()]
        if len(words) < 2:
            continue
        heights = [max(w.bottom - w.top, 0.1) for w in words]
        line_tolerance = statistics.median(heights) * 0.5

        by_top = sorted(words, key=lambda w: w.top)
        line_index = 0
        current_line_top = by_top[0].top
        line_of: dict[int, int] = {id(by_top[0]): 0}
        for w in by_top[1:]:
            if w.top - current_line_top > line_tolerance:
                line_index += 1
                current_line_top = w.top
            line_of[id(w)] = line_index

        natural_order = sorted(words, key=lambda w: (line_of[id(w)], w.x0))
        natural_rank = {id(w): rank for rank, w in enumerate(natural_order)}

        native_order = sorted(words, key=lambda w: w.order_index)
        rank_sequence = [natural_rank[id(w)] for w in native_order]

        inversions = _count_inversions(rank_sequence)
        n = len(rank_sequence)
        max_inversions = n * (n - 1) / 2
        page_scores.append(1.0 if max_inversions == 0 else 1.0 - (inversions / max_inversions))

    if not page_scores:
        return None
    return statistics.mean(page_scores)


def _count_inversions(sequence: list[int]) -> int:
    """Count inversions via merge sort in O(n log n)."""

    def sort_count(seq: list[int]) -> tuple[list[int], int]:
        if len(seq) <= 1:
            return seq, 0
        mid = len(seq) // 2
        left, left_inv = sort_count(seq[:mid])
        right, right_inv = sort_count(seq[mid:])
        merged: list[int] = []
        i = j = 0
        split_inv = 0
        while i < len(left) and j < len(right):
            if left[i] <= right[j]:
                merged.append(left[i])
                i += 1
            else:
                merged.append(right[j])
                j += 1
                split_inv += len(left) - i
        merged.extend(left[i:])
        merged.extend(right[j:])
        return merged, left_inv + right_inv + split_inv

    _, total = sort_count(sequence)
    return total


def _page_integrity_signal(
    all_pages: Iterable[PdfPageData], expected_page_count: int | None
) -> float | None:
    pages = list(all_pages)
    if not pages:
        return None
    error_pages = sum(1 for p in pages if p.error)
    non_error_fraction = 1.0 - (error_pages / len(pages))

    match_factor = 1.0
    if expected_page_count is not None and expected_page_count > 0:
        diff = abs(expected_page_count - len(pages))
        match_factor = max(0.0, 1.0 - diff / expected_page_count)

    return non_error_fraction * match_factor


_REASON_MESSAGES: dict[str, tuple[str, Severity]] = {
    "searchable_text": (
        "One or more pages have no searchable/selectable text.",
        Severity.WARNING,
    ),
    "page_coverage": (
        "Text coverage relative to visible page content is low.",
        Severity.WARNING,
    ),
    "bounds_sanity": (
        "Some word boxes fall outside the page bounds.",
        Severity.WARNING,
    ),
    "duplicate_overlap": (
        "Duplicate or overlapping invisible text was detected.",
        Severity.WARNING,
    ),
    "alignment": (
        "Overlay text does not appear well aligned with the page image.",
        Severity.WARNING,
    ),
    "reading_order": (
        "Extracted text order is inconsistent with the visual reading order.",
        Severity.WARNING,
    ),
    "page_integrity": (
        "Pages appear to be missing, reordered, or unreadable.",
        Severity.BLOCKING,
    ),
}
_LOW_SIGNAL_THRESHOLD = 0.6


def _append_signal_reasons(signals: dict[str, float | None], reasons: list[Reason]) -> None:
    for name, value in signals.items():
        if value is None or value >= _LOW_SIGNAL_THRESHOLD:
            continue
        message, severity = _REASON_MESSAGES[name]
        reasons.append(
            Reason(
                code=f"overlay.{name}",
                message=message,
                severity=severity,
                component="overlay",
                value=round(value, 3),
            )
        )
