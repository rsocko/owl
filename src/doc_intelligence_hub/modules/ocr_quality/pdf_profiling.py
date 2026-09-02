"""Stage-2 page-aware PDF profiling.

Classifies a sampled document's PDF pages as digital text, scanned with an
OCR overlay (treated the same as digital text for page-level purposes —
selectable text is present either way), or having no text layer at all, and
rolls that up into a document-level profile (digital / scanned-with-overlay /
no-text / mixed).

PDF bytes and any extracted page text are used only in-memory to compute
booleans/counts and are never persisted or logged.
"""

from __future__ import annotations

import io
import logging

from .models import DocProfileResult, DocumentProfile, PageSignal, ReasonCode

logger = logging.getLogger(__name__)


class PdfProfilingError(Exception):
    """Raised when a PDF cannot be opened/parsed. Message must stay safe."""


def extract_page_signals(pdf_bytes: bytes, *, max_pages: int = 50) -> list[PageSignal]:
    """Open PDF bytes in-memory and return per-page text/image presence.

    Only booleans are retained — no page text, coordinates, or images are
    kept beyond this function's scope.
    """
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency is required
        raise PdfProfilingError("pdfplumber is not installed") from exc

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            signals: list[PageSignal] = []
            for page in pdf.pages[:max_pages]:
                try:
                    text = page.extract_text() or ""
                    has_text = len(text.strip()) > 0
                except Exception:  # pragma: no cover - defensive, pdfminer quirks
                    has_text = False
                try:
                    has_image = len(page.images) > 0
                except Exception:  # pragma: no cover - defensive
                    has_image = False
                signals.append(PageSignal(has_text=has_text, has_image=has_image))
            return signals
    except Exception as exc:
        message = type(exc).__name__
        if "encrypt" in message.lower() or "password" in str(exc).lower():
            raise PdfProfilingError("PDF is encrypted or password protected") from exc
        raise PdfProfilingError(f"PDF could not be parsed ({message})") from exc


def classify_pages(page_signals: list[PageSignal]) -> DocProfileResult:
    """Pure classification of page signals into a document-level profile.

    Per-page heuristic:
    - text + no image  -> digital (natively created text page)
    - text + image     -> scanned with an OCR/text overlay over a page image
    - no text          -> no-text page (image-only or blank)

    Document-level rollup:
    - ``digital_text``: every page is digital.
    - ``scanned_with_overlay``: every page is a scanned+overlay page.
    - ``no_text``: no page has a text layer at all.
    - ``mixed``: page categories differ within the document.
    """
    if not page_signals:
        return DocProfileResult(
            profile=DocumentProfile.UNKNOWN,
            page_count=0,
            digital_pages=0,
            scanned_overlay_pages=0,
            no_text_pages=0,
            reason_codes=(ReasonCode.PDF_PARSE_FAILED,),
        )

    page_count = len(page_signals)
    digital_pages = sum(1 for p in page_signals if p.has_text and not p.has_image)
    scanned_overlay_pages = sum(1 for p in page_signals if p.has_text and p.has_image)
    no_text_pages = sum(1 for p in page_signals if not p.has_text)

    if digital_pages == page_count:
        profile = DocumentProfile.DIGITAL_TEXT
    elif scanned_overlay_pages == page_count:
        profile = DocumentProfile.SCANNED_WITH_OVERLAY
    elif no_text_pages == page_count:
        profile = DocumentProfile.NO_TEXT
    else:
        profile = DocumentProfile.MIXED

    return DocProfileResult(
        profile=profile,
        page_count=page_count,
        digital_pages=digital_pages,
        scanned_overlay_pages=scanned_overlay_pages,
        no_text_pages=no_text_pages,
        reason_codes=(ReasonCode.OK,),
    )


def profile_pdf(pdf_bytes: bytes, *, max_pages: int = 50) -> DocProfileResult:
    """Convenience wrapper: extract page signals then classify them."""
    signals = extract_page_signals(pdf_bytes, max_pages=max_pages)
    return classify_pages(signals)
