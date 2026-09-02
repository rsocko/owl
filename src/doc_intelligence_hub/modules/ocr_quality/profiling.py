"""Page-aware document profiling.

Classifies each page independently so mixed image/text PDFs are not treated
as wholly digital or wholly scanned, and derives document-level summaries
(dominant classification, content shape, short-document flag) used as
context by both the overlay and machine scorers.
"""

from __future__ import annotations

import re

from doc_intelligence_hub.modules.ocr_quality.pdf_types import PdfPageData
from doc_intelligence_hub.modules.ocr_quality.scoring_config import DEFAULT_CONFIG, ScoringConfig
from doc_intelligence_hub.modules.ocr_quality.scoring_models import (
    ContentShape,
    DocumentProfile,
    PageClassification,
    PageProfile,
)

# A page image covering more than this fraction of the page area, alongside
# extractable text, is treated as a scanned page with a text overlay rather
# than a digital page with an incidental inline image (logo, signature, ...).
_SCANNED_IMAGE_COVERAGE_THRESHOLD = 0.5

_LABEL_VALUE_RE = re.compile(r"^[\w \-/()#]{1,40}:\s*\S")
_MULTI_SPACE_COLUMNS_RE = re.compile(r"(\t| {2,})\S+(\t| {2,})\S+")
_CODE_SYMBOL_RE = re.compile(r"[{};=<>]|=>|::|\+\+|def |function |class \w+")
_SENTENCE_RE = re.compile(r"^[A-Z0-9\"'(].{15,300}[.!?]\"?$")


def build_document_profile(
    *,
    pdf_pages: list[PdfPageData] | None = None,
    text_content: str | None = None,
    metadata: dict | None = None,
    config: ScoringConfig | None = None,
) -> DocumentProfile:
    """Build a page-aware :class:`DocumentProfile` from whatever inputs exist.

    Either ``pdf_pages`` or ``text_content`` (or both) may be provided. When
    neither is provided, an empty/unsupported profile is returned rather than
    guessing.
    """
    cfg = config or DEFAULT_CONFIG
    metadata = metadata or {}

    pages: list[PageProfile] = []
    if pdf_pages:
        for page in pdf_pages:
            pages.append(_profile_page(page))

    total_chars = sum(p.char_count for p in pages) if pages else len(text_content or "")

    reconstructed_text = text_content
    if reconstructed_text is None and pdf_pages:
        reconstructed_text = _reconstruct_text(pdf_pages)

    return DocumentProfile(
        page_count=len(pages) if pages else (1 if text_content else 0),
        pages=pages,
        dominant_classification=_dominant_classification(pages),
        content_shape=_classify_content_shape(reconstructed_text, cfg),
        language_hint=metadata.get("language_hint"),
        producer=metadata.get("producer"),
        is_short_document=total_chars < cfg.short_document_char_threshold,
        has_pdf_geometry=bool(pdf_pages),
    )


def _profile_page(page: PdfPageData) -> PageProfile:
    if page.error:
        return PageProfile(
            page_number=page.page_number,
            classification=PageClassification.UNSUPPORTED_ERROR,
            char_count=0,
            word_count=0,
            error=page.error,
        )

    has_text = page.has_text
    has_images = page.has_images
    area = page.area

    text_coverage: float | None = None
    if area > 0 and page.words:
        word_area = sum(max(w.x1 - w.x0, 0.0) * max(w.bottom - w.top, 0.0) for w in page.words)
        text_coverage = min(word_area / area, 1.0)

    image_coverage: float | None = None
    if area > 0 and page.images:
        image_area = sum(max(i.x1 - i.x0, 0.0) * max(i.bottom - i.top, 0.0) for i in page.images)
        image_coverage = min(image_area / area, 1.0)

    if has_text and has_images:
        classification = (
            PageClassification.SCANNED_WITH_OVERLAY
            if (image_coverage or 0.0) >= _SCANNED_IMAGE_COVERAGE_THRESHOLD
            else PageClassification.MIXED
        )
    elif has_text:
        classification = PageClassification.DIGITAL_TEXT
    else:
        # No extractable text at all (whether or not an image is present):
        # neither overlay nor machine-extraction signals exist for this page.
        classification = PageClassification.IMAGE_NO_TEXT

    return PageProfile(
        page_number=page.page_number,
        classification=classification,
        text_coverage=text_coverage,
        image_coverage=image_coverage,
        char_count=page.char_count,
        word_count=len(page.words),
        rotation=page.rotation,
    )


def _dominant_classification(pages: list[PageProfile]) -> PageClassification | None:
    if not pages:
        return None

    non_error = [
        p.classification for p in pages if p.classification != PageClassification.UNSUPPORTED_ERROR
    ]
    if not non_error:
        return PageClassification.UNSUPPORTED_ERROR

    distinct = set(non_error)
    if len(distinct) > 1:
        return PageClassification.MIXED
    return next(iter(distinct))


def reconstruct_text_from_pages(pdf_pages: list[PdfPageData]) -> str:
    """Best-effort text reconstruction from page geometry, in reading order.

    Public helper so callers (e.g. the scorer) can fall back to this when no
    separate extracted-text string is available.
    """
    return _reconstruct_text(pdf_pages)


def _reconstruct_text(pdf_pages: list[PdfPageData]) -> str:
    lines = []
    for page in pdf_pages:
        ordered_words = sorted(page.words, key=lambda w: w.order_index)
        if ordered_words:
            lines.append(" ".join(w.text for w in ordered_words))
    return "\n".join(lines)


def _classify_content_shape(text: str | None, config: ScoringConfig) -> ContentShape:
    if not text or not text.strip():
        return ContentShape.UNKNOWN

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ContentShape.UNKNOWN

    n = len(lines)
    table_hits = sum(
        1 for line in lines if _LABEL_VALUE_RE.match(line) or _MULTI_SPACE_COLUMNS_RE.search(line)
    )
    code_hits = sum(1 for line in lines if _CODE_SYMBOL_RE.search(line))
    prose_hits = sum(1 for line in lines if _SENTENCE_RE.match(line))

    scores = {
        ContentShape.TABLE_OR_FORM: table_hits / n,
        ContentShape.CODE_HEAVY: code_hits / n,
        ContentShape.PROSE: prose_hits / n,
    }
    qualifying = {shape: score for shape, score in scores.items() if score >= 0.2}

    if not qualifying:
        # No strong structural signal either way; fall back to PROSE for
        # substantial free text rather than leaving a common case UNKNOWN.
        return ContentShape.PROSE if n >= 3 else ContentShape.UNKNOWN

    best_shape = max(qualifying, key=qualifying.get)
    best_score = qualifying[best_shape]
    contenders = [s for s, v in qualifying.items() if best_score - v <= 0.1]
    if len(contenders) > 1:
        return ContentShape.MIXED
    return best_shape
