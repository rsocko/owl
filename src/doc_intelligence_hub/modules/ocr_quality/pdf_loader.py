"""``pdfplumber`` adapter — converts raw PDF bytes into :mod:`pdf_types` data.

Per-page parse failures are captured as an errored :class:`PdfPageData`
instead of raising, so a single corrupt page does not abort profiling of the
rest of a document.
"""

from __future__ import annotations

import contextlib
import io
import logging
import math

from doc_intelligence_hub.modules.ocr_quality.pdf_types import ImageBox, PdfPageData, WordBox

logger = logging.getLogger(__name__)

# A word's derived rotation angle within this many degrees of 0 is snapped
# to exactly 0.0 -- numerical noise from the matrix math, not a real skew.
_ANGLE_SNAP_TOLERANCE_DEGREES = 0.5

# Bbox-containment tolerance (page-relative points) used when matching a
# representative character to a word's already-computed bounding box.
_CHAR_MATCH_TOLERANCE = 0.5


def load_pdf_pages(pdf_bytes: bytes) -> list[PdfPageData]:
    """Parse PDF bytes into a list of :class:`PdfPageData`.

    Returns an empty list if the document itself cannot be opened at all
    (e.g. not a PDF, zero-length, encrypted without a usable password). A
    document that opens but has individually broken pages instead yields one
    errored :class:`PdfPageData` per broken page.
    """
    try:
        import pdfplumber
    except ImportError:  # pragma: no cover - dependency is declared in pyproject
        logger.warning("pdfplumber is not installed; cannot parse PDF geometry.")
        return []

    try:
        pdf = pdfplumber.open(io.BytesIO(pdf_bytes))
    except Exception as exc:
        logger.info("Could not open PDF for geometry analysis: %s", exc)
        return []

    pages: list[PdfPageData] = []
    try:
        for index, page in enumerate(pdf.pages):
            pages.append(_load_page(index + 1, page))
    finally:
        with contextlib.suppress(Exception):
            pdf.close()

    return pages


def _load_page(page_number: int, page) -> PdfPageData:
    try:
        width = float(page.width or 0.0)
        height = float(page.height or 0.0)
        rotation = int(getattr(page, "rotation", 0) or 0)

        chars = getattr(page, "chars", None) or []

        words: list[WordBox] = []
        for order_index, w in enumerate(page.extract_words(use_text_flow=False) or []):
            words.append(
                WordBox(
                    text=w.get("text", ""),
                    x0=float(w.get("x0", 0.0)),
                    top=float(w.get("top", 0.0)),
                    x1=float(w.get("x1", 0.0)),
                    bottom=float(w.get("bottom", 0.0)),
                    order_index=order_index,
                    angle_degrees=_word_angle_degrees(w, chars),
                )
            )

        images: list[ImageBox] = []
        for im in getattr(page, "images", []) or []:
            images.append(
                ImageBox(
                    x0=float(im.get("x0", 0.0)),
                    top=float(im.get("top", 0.0)),
                    x1=float(im.get("x1", 0.0)),
                    bottom=float(im.get("bottom", 0.0)),
                )
            )

        char_count = len(getattr(page, "chars", None) or [])

        return PdfPageData(
            page_number=page_number,
            width=width,
            height=height,
            words=words,
            images=images,
            rotation=rotation,
            char_count=char_count,
        )
    except Exception as exc:  # noqa: BLE001 - convert any per-page failure to a profile marker
        logger.info("Error parsing page %d: %s", page_number, exc)
        return PdfPageData(
            page_number=page_number,
            width=0.0,
            height=0.0,
            error=str(exc),
        )


def _word_angle_degrees(word: dict, chars: list) -> float:
    """Derive one word's rotation angle from a representative glyph's PDF
    text-rendering matrix.

    ``extract_words()`` only returns each word's aggregate bounding box, not
    its constituent chars, so a representative character is located by bbox
    containment against the page's already-parsed ``chars`` list. Reading
    the angle straight off a glyph's own rendering matrix -- rather than
    relying on ``extract_words``' own grouping heuristics, which only
    reliably distinguish upright vs. non-upright (cardinal-rotation) text --
    lets this support arbitrary skew angles, not just 90 deg multiples.

    Returns ``0.0`` (normal/upright) if no matching character is found.
    """
    x0 = word.get("x0", 0.0)
    top = word.get("top", 0.0)
    x1 = word.get("x1", 0.0)
    bottom = word.get("bottom", 0.0)

    for ch in chars:
        matrix = ch.get("matrix")
        if not matrix:
            continue
        if (
            x0 - _CHAR_MATCH_TOLERANCE <= ch.get("x0", 0.0)
            and ch.get("x1", 0.0) <= x1 + _CHAR_MATCH_TOLERANCE
            and top - _CHAR_MATCH_TOLERANCE <= ch.get("top", 0.0)
            and ch.get("bottom", 0.0) <= bottom + _CHAR_MATCH_TOLERANCE
        ):
            angle = math.degrees(math.atan2(matrix[1], matrix[0]))
            if abs(angle) < _ANGLE_SNAP_TOLERANCE_DEGREES:
                return 0.0
            return angle

    return 0.0
