"""Compare a READY candidate against the current document (issue #18, slice 1).

Per the design doc's "Review and comparison" section, comparison is purely
informational for a human reviewer — a higher text/machine score is evidence,
not authorization. Nothing here ever decides acceptance; it only surfaces
findings. Downstream-extractor regression detection is intentionally noted
but not implemented (tracked in issue #114).
"""

from __future__ import annotations

import difflib
import hashlib

from doc_intelligence_hub.modules.ocr_quality.candidate_models import (
    ComparisonBlockingFinding,
    ComparisonResult,
)
from doc_intelligence_hub.modules.ocr_quality.pdf_loader import load_pdf_pages
from doc_intelligence_hub.modules.ocr_quality.pdf_types import PdfPageData

# Below this per-page text-similarity ratio, a page is considered reordered
# relative to its counterpart at the same index (rather than just re-OCR'd
# with minor differences).
_PAGE_ORDER_SIMILARITY_FLOOR = 0.35

# A machine-score drop of more than this many points (0-100 scale) is a
# blocking regression finding.
_MACHINE_REGRESSION_TOLERANCE = 5.0


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compare_candidate(
    *,
    current_pdf_bytes: bytes,
    current_text: str | None,
    current_overlay_score: float | None,
    current_machine_score: float | None,
    current_content_score: float | None = None,
    candidate_pdf_bytes: bytes | None,
    candidate_text: str | None,
    candidate_overlay_score: float | None,
    candidate_machine_score: float | None,
    candidate_content_score: float | None = None,
    expected_page_count: int | None = None,
) -> ComparisonResult:
    """Run all comparison checks and return a :class:`ComparisonResult`.

    Never raises — an unexpected error while comparing is itself recorded as
    a blocking ``UNKNOWN_ERROR`` finding rather than aborting the caller.
    """
    findings: list[ComparisonBlockingFinding] = []
    current_pages: list[PdfPageData] = []
    candidate_pages: list[PdfPageData] = []

    try:
        current_pages = load_pdf_pages(current_pdf_bytes)

        if not candidate_pdf_bytes:
            findings.append(ComparisonBlockingFinding.NOT_SEARCHABLE_PDF)
        else:
            candidate_pages = load_pdf_pages(candidate_pdf_bytes)
            if not candidate_pages or not any(p.words for p in candidate_pages):
                findings.append(ComparisonBlockingFinding.NOT_SEARCHABLE_PDF)

        current_count = len(current_pages)
        candidate_count = len(candidate_pages)
        if expected_page_count is not None and candidate_count != expected_page_count:
            findings.append(ComparisonBlockingFinding.PAGES_MISSING)
        elif current_count and candidate_count and current_count != candidate_count:
            findings.append(ComparisonBlockingFinding.PAGES_MISSING)

        if current_pages and candidate_pages:
            if _pages_appear_reordered(current_pages, candidate_pages):
                findings.append(ComparisonBlockingFinding.PAGES_REORDERED)
            if not _overlay_roughly_aligned(candidate_pages):
                findings.append(ComparisonBlockingFinding.TEXT_MISALIGNED)

        overlay_delta = _score_delta(current_overlay_score, candidate_overlay_score)
        machine_delta = _score_delta(current_machine_score, candidate_machine_score)
        content_delta = _score_delta(current_content_score, candidate_content_score)
        if machine_delta is not None and machine_delta < -_MACHINE_REGRESSION_TOLERANCE:
            findings.append(ComparisonBlockingFinding.MACHINE_REGRESSION)

        text_diff = _text_diff_summary(current_text or "", candidate_text or "")

        return ComparisonResult(
            source_checksum=checksum(current_pdf_bytes),
            candidate_checksum=checksum(candidate_pdf_bytes) if candidate_pdf_bytes else "",
            blocking_findings=findings,
            page_count_current=current_count,
            page_count_candidate=candidate_count,
            text_diff_summary=text_diff,
            overlay_score_delta=overlay_delta,
            machine_score_delta=machine_delta,
            content_score_delta=content_delta,
        )
    except Exception as exc:  # noqa: BLE001 - comparison failure must not crash the caller
        return ComparisonResult(
            source_checksum=checksum(current_pdf_bytes) if current_pdf_bytes else "",
            candidate_checksum=checksum(candidate_pdf_bytes) if candidate_pdf_bytes else "",
            blocking_findings=[ComparisonBlockingFinding.UNKNOWN_ERROR],
            page_count_current=len(current_pages),
            page_count_candidate=len(candidate_pages),
            performer_notes=f"Comparison failed: {exc}",
        )


def _score_delta(current: float | None, candidate: float | None) -> float | None:
    if current is None or candidate is None:
        return None
    return round(candidate - current, 2)


def _page_text(page: PdfPageData) -> str:
    return " ".join(w.text for w in page.words)


def _pages_appear_reordered(
    current_pages: list[PdfPageData], candidate_pages: list[PdfPageData]
) -> bool:
    """Heuristic: compare same-index pages' text; a low match plus a good
    match against a *different* index suggests reordering rather than just
    re-OCR noise.
    """
    count = min(len(current_pages), len(candidate_pages))
    if count == 0:
        return False

    for i in range(count):
        same_index_ratio = _text_similarity(
            _page_text(current_pages[i]), _page_text(candidate_pages[i])
        )
        if same_index_ratio >= _PAGE_ORDER_SIMILARITY_FLOOR:
            continue
        # Same-index text doesn't match well — check whether it matches a
        # different candidate page much better, which would indicate the
        # pages were reordered rather than merely re-OCR'd differently.
        current_text = _page_text(current_pages[i])
        if not current_text.strip():
            continue
        best_other = max(
            (
                _text_similarity(current_text, _page_text(candidate_pages[j]))
                for j in range(len(candidate_pages))
                if j != i
            ),
            default=0.0,
        )
        if best_other > same_index_ratio + 0.2:
            return True
    return False


def _text_similarity(a: str, b: str) -> float:
    if not a.strip() and not b.strip():
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _overlay_roughly_aligned(candidate_pages: list[PdfPageData]) -> bool:
    """Coarse alignment check: pages with images should also have overlay
    words roughly within the page bounds. Full alignment scoring is owned by
    ``overlay_scoring`` (reused via ``scorer.assess_document`` for the
    candidate's own overlay score) — this is a lightweight blocking gate.
    """
    for page in candidate_pages:
        if not page.images or not page.words:
            continue
        if page.width <= 0 or page.height <= 0:
            continue
        out_of_bounds = sum(
            1
            for w in page.words
            if w.x1 < 0 or w.x0 > page.width or w.bottom < 0 or w.top > page.height
        )
        if page.words and (out_of_bounds / len(page.words)) > 0.5:
            return False
    return True


def _text_diff_summary(current_text: str, candidate_text: str) -> dict[str, object]:
    similarity = _text_similarity(current_text, candidate_text)
    current_lines = current_text.splitlines()
    candidate_lines = candidate_text.splitlines()
    diff = list(difflib.unified_diff(current_lines, candidate_lines, lineterm=""))
    return {
        "similarity": round(similarity, 3),
        "lines_added": sum(
            1 for line in diff if line.startswith("+") and not line.startswith("+++")
        ),
        "lines_removed": sum(
            1 for line in diff if line.startswith("-") and not line.startswith("---")
        ),
        "current_char_count": len(current_text),
        "candidate_char_count": len(candidate_text),
    }
