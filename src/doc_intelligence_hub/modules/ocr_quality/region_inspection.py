"""On-demand word/region-level inspection for a single document (issue #134).

Computes per-word geometry and heuristic pass/fail flags for one document's
PDF page(s), fetched and parsed **on demand** when a reviewer opens a
document's detail page — never precomputed or persisted for the whole
corpus (that's the ~8,900-document Stage 1/2 scan territory owned by
``service.py``/``overlay_scoring.py``).

Reuses ``pdf_loader.py`` (issue #29) for geometry extraction. The per-word
flag heuristics mirror ``overlay_scoring.py``'s ``bounds_sanity``,
``duplicate_overlap``, and ``alignment`` signals, plus a
``content_plausibility`` flag mirroring ``machine_scoring.py``'s
character/glyph-corruption checks (e.g. "(cid:N)" placeholders, replacement
characters) and low per-word OCR confidence — evaluated per-word instead of
aggregated into a single document-level score, so a reviewer can see *which*
words tripped a signal rather than only the aggregate.

A tiny in-process TTL cache holds raw PDF bytes per document for a short
window (default 5 minutes, capped entry count) so that opening the regions
endpoint and the page-image endpoint back-to-back — or flipping between a
few pages — doesn't re-download the PDF from Paperless every time. This is
explicitly not a durable/corpus-wide cache: entries expire quickly and only
ever exist for documents a reviewer actually opened.
"""

from __future__ import annotations

import io
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

from doc_intelligence_hub.modules.ocr_quality.pdf_loader import load_pdf_pages
from doc_intelligence_hub.modules.ocr_quality.pdf_types import PdfPageData, WordBox

logger = logging.getLogger(__name__)

# Padding (page-relative points) applied around image regions when checking
# per-word alignment — mirrors ``overlay_scoring._ALIGNMENT_PADDING``.
_ALIGNMENT_PADDING = 5.0

# A page's embedded image(s) must cover at least this fraction of the page
# area for the per-word alignment check to apply at all — mirrors
# ``profiling.py``'s ``_SCANNED_IMAGE_COVERAGE_THRESHOLD`` convention for
# telling a scanned page (the whole page IS an image OCR text should overlay)
# apart from a digital page with only a small incidental image (a logo,
# signature line, stamp, ...). Without this guard, a page like a digital
# lease with a small company logo would have nearly every word — anywhere
# far from the logo — falsely flagged, since almost no real body text sits
# inside the logo's bounds.
_SCANNED_IMAGE_COVERAGE_THRESHOLD = 0.5

# Two word boxes are considered duplicates of each other if their centers
# are within this many points and the text matches — mirrors
# ``overlay_scoring._DUPLICATE_DISTANCE_TOLERANCE``.
_DUPLICATE_DISTANCE_TOLERANCE = 1.0

# Mirrors ``machine_scoring._CID_ARTIFACT_RE``: PDF text extractors fall back
# to literal "(cid:123)" placeholders per glyph when a font's ToUnicode CMap
# is missing/incomplete. Checked per-word here (rather than importing the
# private constant from ``machine_scoring.py``) so a reviewer can see exactly
# *which* words carry the corruption, not just that the document as a whole
# does.
_CID_ARTIFACT_RE = re.compile(r"\(cid:\d+\)")
_REPLACEMENT_CHAR = "\ufffd"

# A word's OCR engine confidence (0-1) below this is considered implausible
# on its own, independent of its text content. Only meaningful for
# providers that report per-word confidence (e.g. Azure Document
# Intelligence) — ``None`` (digitally-extracted PDF text) never trips this.
_LOW_CONFIDENCE_THRESHOLD = 0.5

DEFAULT_PAGE_IMAGE_DPI = 150
MIN_PAGE_IMAGE_DPI = 72
MAX_PAGE_IMAGE_DPI = 300


# ---------------------------------------------------------------------------
# Short-lived in-process PDF byte cache
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    pdf_bytes: bytes
    expires_at: float


class _RawPdfCache:
    """Tiny TTL cache for raw PDF bytes, keyed by an opaque cache key."""

    def __init__(self, ttl_seconds: float = 300.0, max_entries: int = 8) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._entries: dict[str, _CacheEntry] = {}

    def get(self, key: str) -> bytes | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at < time.monotonic():
            self._entries.pop(key, None)
            return None
        return entry.pdf_bytes

    def set(self, key: str, pdf_bytes: bytes) -> None:
        if key not in self._entries and len(self._entries) >= self._max_entries:
            oldest_key = min(self._entries, key=lambda k: self._entries[k].expires_at)
            self._entries.pop(oldest_key, None)
        self._entries[key] = _CacheEntry(
            pdf_bytes=pdf_bytes, expires_at=time.monotonic() + self._ttl
        )

    def clear(self) -> None:
        self._entries.clear()


_raw_pdf_cache = _RawPdfCache()


def cache_key_for_document(document_id: int) -> str:
    return str(document_id)


def peek_cached_pdf_bytes(document_id: int) -> bytes | None:
    """Return cached PDF bytes for ``document_id`` if still warm, else ``None``."""
    return _raw_pdf_cache.get(cache_key_for_document(document_id))


def store_pdf_bytes(document_id: int, pdf_bytes: bytes) -> None:
    _raw_pdf_cache.set(cache_key_for_document(document_id), pdf_bytes)


def clear_cache() -> None:
    """Test/debug hook — clears the whole in-process cache."""
    _raw_pdf_cache.clear()


# ---------------------------------------------------------------------------
# Per-word flag heuristics
# ---------------------------------------------------------------------------


def _is_image_dominated_page(page: PdfPageData) -> bool:
    """Is this page "scanned/image-dominated" — where per-word alignment is meaningful?

    Mirrors ``profiling.py``'s ``_profile_page`` classification: a page is
    treated as scanned (OCR text overlaying a full-page image) only when its
    embedded image(s) cover at least ``_SCANNED_IMAGE_COVERAGE_THRESHOLD`` of
    the page area — as opposed to a digital page with only a small incidental
    image alongside real digital text.
    """
    if page.area <= 0 or not page.images:
        return False
    image_area = sum(
        max(img.x1 - img.x0, 0.0) * max(img.bottom - img.top, 0.0) for img in page.images
    )
    return (image_area / page.area) >= _SCANNED_IMAGE_COVERAGE_THRESHOLD


def _is_implausible_word_text(text: str) -> bool:
    """Mirrors ``machine_scoring._char_script_plausibility``'s corruption
    checks, evaluated for a single word instead of the whole document: a
    "(cid:N)" glyph-ID placeholder, a Unicode replacement character, or a
    control/surrogate/private-use/unassigned character.
    """
    if _CID_ARTIFACT_RE.search(text):
        return True
    for ch in text:
        if ch == _REPLACEMENT_CHAR:
            return True
        if ch in "\n\r\t":
            continue
        # Cc = control, Cs = surrogate, Co = private use, Cn = unassigned.
        if unicodedata.category(ch) in ("Cc", "Cs", "Co", "Cn"):
            return True
    return False


def _word_flags(
    page: PdfPageData,
    word: WordBox,
    *,
    seen_words: list[WordBox],
    page_is_image_dominated: bool,
) -> list[str]:
    """Compute which signal categories this single word trips, if any."""
    flags: list[str] = []

    if (
        page.width > 0
        and page.height > 0
        and not (
            0 <= word.x0 <= word.x1 <= page.width and 0 <= word.top <= word.bottom <= page.height
        )
    ):
        flags.append("bounds_sanity")

    is_duplicate = any(
        word.text == other.text
        and abs(word.x0 - other.x0) <= _DUPLICATE_DISTANCE_TOLERANCE
        and abs(word.top - other.top) <= _DUPLICATE_DISTANCE_TOLERANCE
        for other in seen_words
    )
    if is_duplicate:
        flags.append("duplicate_overlap")

    # Alignment is only meaningful on scanned/image-dominated pages — a
    # purely digital page (even one with a small incidental image, e.g. a
    # logo) has no scanned image to misalign against, mirroring
    # ``overlay_scoring._alignment_signal`` returning ``None`` (rather than a
    # false "misaligned") when there is nothing meaningful to check.
    if page_is_image_dominated:
        padded_images = [
            (
                img.x0 - _ALIGNMENT_PADDING,
                img.top - _ALIGNMENT_PADDING,
                img.x1 + _ALIGNMENT_PADDING,
                img.bottom + _ALIGNMENT_PADDING,
            )
            for img in page.images
        ]
        cx = (word.x0 + word.x1) / 2.0
        cy = (word.top + word.bottom) / 2.0
        if not any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in padded_images):
            flags.append("alignment")

    # Content-plausibility: does this specific word's text/confidence look
    # like real extracted content, independent of where it sits on the page?
    # Generalizes the "(cid:N)" glyph-ID case (issue: docs #2346/#5483) to
    # any per-word implausible-character corruption, plus low OCR-engine
    # confidence when the provider reports one.
    if _is_implausible_word_text(word.text) or (
        word.confidence is not None and word.confidence < _LOW_CONFIDENCE_THRESHOLD
    ):
        flags.append("content_plausibility")

    return flags


# Maps a per-word flag category to the document-level scorer reason code(s)
# it can be cross-referenced against. "content_plausibility" pulls from the
# *machine* (text-content) component rather than the overlay (geometry)
# component the other three flags mirror.
_FLAG_REASON_CODES: dict[str, tuple[str, ...]] = {
    "bounds_sanity": ("overlay.bounds_sanity",),
    "duplicate_overlap": ("overlay.duplicate_overlap",),
    "alignment": ("overlay.alignment",),
    "content_plausibility": (
        "machine.cid_glyph_artifacts",
        "machine.char_script_plausibility",
        "machine.token_whitespace_quality",
    ),
}


def _matching_document_reasons(
    flags: list[str], document_reasons: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Cross-reference this word's flags against the document's stored scorer reasons.

    ``document_reasons`` is the already-computed ``DocumentAssessment.reasons``
    list (combined ``overlay.<signal>``/``machine.<signal>`` coded entries
    from ``overlay_scoring.py``/``machine_scoring.py``). A word is considered
    to be "referenced" by a document-level reason when its flag category maps
    (via ``_FLAG_REASON_CODES``) to that reason's code — an approximation,
    since the stored reasons are page/document-level, not per-word, but it
    surfaces the relevant explanation text next to the specific word that
    likely caused it.
    """
    if not flags or not document_reasons:
        return []
    codes = {code for flag in flags for code in _FLAG_REASON_CODES.get(flag, ())}
    return [reason for reason in document_reasons if reason.get("code") in codes]


# ---------------------------------------------------------------------------
# Page region payload
# ---------------------------------------------------------------------------


def build_page_regions_from_pages(
    pages: list[PdfPageData],
    *,
    page_number: int,
    document_reasons: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Build the region-inspection payload from already-parsed page geometry.

    Split out from :func:`build_page_regions` so the flagging/cross-reference
    logic can be unit tested against the same hand-built ``PdfPageData``
    fixtures used elsewhere in this module's test suite, without needing a
    real PDF byte stream.
    """
    if not pages:
        return None
    page = next((p for p in pages if p.page_number == page_number), None)
    if page is None:
        return None

    document_reasons = document_reasons or []
    seen_words: list[WordBox] = []
    words_out: list[dict[str, Any]] = []
    page_is_image_dominated = _is_image_dominated_page(page)
    for word in page.words:
        flags = _word_flags(
            page, word, seen_words=seen_words, page_is_image_dominated=page_is_image_dominated
        )
        seen_words.append(word)
        words_out.append(
            {
                "text": word.text,
                "x0": word.x0,
                "top": word.top,
                "x1": word.x1,
                "bottom": word.bottom,
                "confidence": word.confidence,
                "angle": word.angle_degrees,
                "flagged": bool(flags),
                "flag_reasons": flags,
                "matched_reasons": _matching_document_reasons(flags, document_reasons),
            }
        )

    return {
        "page": page_number,
        "page_count": len(pages),
        "width": page.width,
        "height": page.height,
        "error": page.error,
        "words": words_out,
    }


def build_page_regions(
    *,
    pdf_bytes: bytes,
    page_number: int,
    document_reasons: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Build the region-inspection payload for one page of one document.

    Returns ``None`` if the PDF has no parseable geometry at all, or the
    requested page number doesn't exist.
    """
    pages = load_pdf_pages(pdf_bytes)
    return build_page_regions_from_pages(
        pages, page_number=page_number, document_reasons=document_reasons
    )


# ---------------------------------------------------------------------------
# Page image rendering
# ---------------------------------------------------------------------------


def render_page_image(
    *, pdf_bytes: bytes, page_number: int, dpi: int = DEFAULT_PAGE_IMAGE_DPI
) -> tuple[bytes, int, int] | None:
    """Render one page of the PDF to PNG bytes at the given resolution.

    Returns ``(png_bytes, width_px, height_px)``, or ``None`` if the page
    doesn't exist or rendering fails (e.g. an undecodable embedded image) —
    callers should surface that as a 404/502, not crash the request.

    Uses ``pdfplumber``'s built-in ``Page.to_image()`` (backed by
    ``pypdfium2``, already an install dependency of the pinned
    ``pdfplumber>=0.11.0`` — no new dependency needed).
    """
    try:
        import pdfplumber
    except ImportError:  # pragma: no cover - dependency is declared in pyproject
        logger.warning("pdfplumber is not installed; cannot render page image.")
        return None

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if page_number < 1 or page_number > len(pdf.pages):
                return None
            page = pdf.pages[page_number - 1]
            page_image = page.to_image(resolution=dpi)
            pil_image = page_image.original
            buffer = io.BytesIO()
            pil_image.save(buffer, format="PNG")
            return buffer.getvalue(), pil_image.width, pil_image.height
    except Exception as exc:  # noqa: BLE001 - convert any rendering failure to a safe None
        logger.info("Could not render page %d image: %s", page_number, exc)
        return None


__all__ = [
    "DEFAULT_PAGE_IMAGE_DPI",
    "MIN_PAGE_IMAGE_DPI",
    "MAX_PAGE_IMAGE_DPI",
    "build_page_regions",
    "build_page_regions_from_pages",
    "cache_key_for_document",
    "clear_cache",
    "peek_cached_pdf_bytes",
    "render_page_image",
    "store_pdf_bytes",
]
