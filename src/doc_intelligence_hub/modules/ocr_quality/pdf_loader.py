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

# Threshold (degrees) used to reclassify a char as "upright" (nearest
# cardinal direction is 0/180) vs. "rotated" (nearest cardinal is 90/270)
# for word-clustering purposes -- see ``_is_upright_angle`` below.
_UPRIGHT_BUCKET_THRESHOLD_DEGREES = 45.0


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
        for order_index, w in enumerate(_extract_words(chars)):
            words.append(
                WordBox(
                    text=w.get("text", ""),
                    x0=float(w.get("x0", 0.0)),
                    top=float(w.get("top", 0.0)),
                    x1=float(w.get("x1", 0.0)),
                    bottom=float(w.get("bottom", 0.0)),
                    order_index=order_index,
                    angle_degrees=_word_angle_degrees(w),
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


def _extract_words(chars: list) -> list:
    """Cluster a page's chars into words, correctly handling rotated text.

    ``pdfplumber``'s own ``page.extract_words()`` already contains working
    clustering logic for both upright and rotated (e.g. vertical sidebar)
    text -- it branches per char on pdfminer's ``LTChar.upright`` flag and
    uses a different clustering axis for each bucket. The problem is that
    flag itself: it's defined as ``a*d*scaling > 0 and b*c <= 0`` on the
    char's text matrix, which for a pure rotation reduces to ``cos(angle)**2
    > 0`` -- i.e. it is ``False`` (non-upright) *only* for a rotation of
    *exactly* +/-90/270 degrees. Any angle merely close to 90 (89.5, 85, 80
    deg -- the realistic case for a scanned or slightly skewed vertical
    sidebar) is still classified ``upright=True`` and run through normal
    horizontal-line clustering, which fails for chars actually stacked
    vertically: it either fragments the run into one "word" per character,
    or occasionally mis-merges chars into an oversized, wrongly-oriented
    bounding box.

    This re-derives ``upright`` per char from its own rendering matrix
    (bucketed by nearest cardinal direction, not pdfminer's cardinal-*exact*
    check) before delegating to ``pdfplumber``'s own ``WordExtractor`` --
    reusing its already-correct clustering rather than reimplementing it.
    For any page where no char's corrected classification differs from
    pdfminer's own (i.e. every normal upright-text page), this produces
    byte-for-byte identical output to today's ``page.extract_words()``.
    """
    try:
        from pdfplumber.utils.text import WordExtractor
    except ImportError:  # pragma: no cover - pdfplumber is a declared dependency
        return []

    corrected_chars = []
    for ch in chars:
        matrix = ch.get("matrix")
        if matrix:
            ch = {**ch, "upright": _is_upright_angle(_char_angle_degrees(ch))}
        corrected_chars.append(ch)

    return WordExtractor(use_text_flow=False).extract_words(corrected_chars, return_chars=True) or []


def _char_angle_degrees(ch: dict) -> float:
    """Raw rotation angle (degrees) of one char's own PDF text-rendering matrix."""
    matrix = ch.get("matrix")
    if not matrix:
        return 0.0
    return math.degrees(math.atan2(matrix[1], matrix[0]))


def _is_upright_angle(angle_degrees: float) -> bool:
    """Is this angle's nearest cardinal direction 0/180 (upright) rather than
    90/270 (rotated)?

    A 45 deg bucket threshold, applied to the char's *own* matrix-derived
    angle rather than pdfminer's cardinal-exact ``upright`` flag, correctly
    classifies "genuinely vertical, but not bit-exact 90 deg" text (the
    realistic real-world case) as rotated.
    """
    normalized = abs(angle_degrees) % 180.0
    distance_from_horizontal = min(normalized, 180.0 - normalized)
    return distance_from_horizontal <= _UPRIGHT_BUCKET_THRESHOLD_DEGREES


def _word_angle_degrees(word: dict) -> float:
    """Derive one word's rotation angle from one of its own glyphs' PDF
    text-rendering matrix.

    ``word["chars"]`` (populated via ``WordExtractor(..., return_chars=True)``
    in ``_extract_words``) gives direct access to the word's own member
    chars, so no bbox-matching against the page's separately-parsed char
    list is needed. Reading the angle straight off a glyph's own rendering
    matrix -- rather than relying on ``extract_words``' own grouping
    heuristics -- lets this support arbitrary skew angles, not just 90 deg
    multiples.

    Returns ``0.0`` (normal/upright) if the word has no chars with a matrix.
    """
    for ch in word.get("chars", []) or []:
        if not ch.get("matrix"):
            continue
        angle = _char_angle_degrees(ch)
        if abs(angle) < _ANGLE_SNAP_TOLERANCE_DEGREES:
            return 0.0
        return angle

    return 0.0
