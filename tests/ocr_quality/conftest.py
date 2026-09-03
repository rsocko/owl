"""Shared fixtures/helpers for OCR quality module tests."""

from __future__ import annotations

from doc_intelligence_hub.modules.ocr_quality.pdf_types import ImageBox, PdfPageData, WordBox


def make_word(
    text: str, x0: float, top: float, x1: float, bottom: float, order: int, angle: float = 0.0
) -> WordBox:
    return WordBox(
        text=text, x0=x0, top=top, x1=x1, bottom=bottom, order_index=order, angle_degrees=angle
    )


def words_for_line(
    words: list[str], top: float, start_x: float = 10.0, order_start: int = 0, char_w: float = 10.0
) -> list[WordBox]:
    """Lay out words left-to-right on one line, in natural reading order."""
    result = []
    x = start_x
    for i, w in enumerate(words):
        width = max(len(w) * char_w * 0.6, char_w)
        result.append(make_word(w, x, top, x + width, top + 12.0, order_start + i))
        x += width + 5.0
    return result


def make_digital_page(
    page_number: int = 1, width: float = 600.0, height: float = 800.0
) -> PdfPageData:
    """A clean digital-native page: text only, no images, well-ordered."""
    words: list[WordBox] = []
    order = 0
    for top in (50.0, 70.0, 90.0):
        line_words = words_for_line(
            ["This", "is", "a", "clean", "digital", "line", "of", "text."],
            top=top,
            order_start=order,
        )
        words.extend(line_words)
        order += len(line_words)
    return PdfPageData(
        page_number=page_number, width=width, height=height, words=words, char_count=200
    )


def make_scanned_overlay_page(
    page_number: int = 1,
    width: float = 600.0,
    height: float = 800.0,
    misaligned: bool = False,
) -> PdfPageData:
    """A scanned page: one full-page image plus an OCR text overlay."""
    image = ImageBox(x0=0.0, top=0.0, x1=width, bottom=height)
    words: list[WordBox] = []
    order = 0
    tops = [50.0, 70.0, 90.0, 110.0]
    for top in tops:
        base_top = top if not misaligned else top + height  # push words off the image
        line_words = words_for_line(
            ["Scanned", "overlay", "text", "line", "here"], top=base_top, order_start=order
        )
        words.extend(line_words)
        order += len(line_words)
    return PdfPageData(
        page_number=page_number,
        width=width,
        height=height,
        words=words,
        images=[image],
        char_count=180,
    )


def make_digital_page_with_small_image(
    page_number: int = 1, width: float = 600.0, height: float = 800.0
) -> PdfPageData:
    """A digital-native page with ordinary text plus a small incidental image.

    Mimics a real-world case (e.g. a lease agreement with a small company
    logo): the image covers a small corner of the page, far below the
    "scanned/image-dominated" coverage threshold, and none of the body text
    sits anywhere near it.
    """
    logo = ImageBox(x0=20.0, top=20.0, x1=70.0, bottom=50.0)  # 50x30 corner logo
    words: list[WordBox] = []
    order = 0
    for top in (300.0, 320.0, 340.0):
        line_words = words_for_line(
            ["Ordinary", "body", "text", "far", "from", "the", "logo."],
            top=top,
            order_start=order,
        )
        words.extend(line_words)
        order += len(line_words)
    return PdfPageData(
        page_number=page_number,
        width=width,
        height=height,
        words=words,
        images=[logo],
        char_count=180,
    )


def make_image_only_page(
    page_number: int = 1, width: float = 600.0, height: float = 800.0
) -> PdfPageData:
    """A scanned page with no OCR text layer at all."""
    image = ImageBox(x0=0.0, top=0.0, x1=width, bottom=height)
    return PdfPageData(
        page_number=page_number, width=width, height=height, images=[image], char_count=0
    )


def make_error_page(page_number: int = 1) -> PdfPageData:
    return PdfPageData(
        page_number=page_number, width=0.0, height=0.0, error="could not decode stream"
    )


def make_minimal_pdf_bytes(text: str = "Hello World", width: int = 200, height: int = 100) -> bytes:
    """A tiny hand-built, single-page PDF with a real text-showing content stream.

    Deliberately has no xref table — pdfminer/pdfplumber recover such
    documents by scanning for object markers, which lets this stay a small,
    dependency-free fixture instead of requiring a PDF-writing library.
    """
    stream = f"BT /F1 12 Tf 10 50 Td ({text}) Tj ET".encode()
    pdf = f"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj
4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
5 0 obj << /Length {len(stream)} >>
stream
""".encode()
    pdf += stream + b"\nendstream\nendobj\ntrailer << /Size 6 /Root 1 0 R >>\n%%EOF"
    return pdf


def make_rotated_text_pdf_bytes(
    text: str = "Vertical",
    width: int = 200,
    height: int = 200,
    angle_degrees: float = 90.0,
) -> bytes:
    """A tiny hand-built, single-page PDF whose text is drawn via an explicit
    ``Tm`` (set text matrix) rotation, instead of the default identity
    matrix ``make_minimal_pdf_bytes`` uses.

    Mirrors ``make_minimal_pdf_bytes``'s xref-less construction; used to
    exercise the per-word rotation-angle derivation in ``pdf_loader.py``
    against a real, pdfplumber-parseable rotated text-rendering matrix.
    """
    import math

    theta = math.radians(angle_degrees)
    a, b = math.cos(theta), math.sin(theta)
    c, d = -math.sin(theta), math.cos(theta)
    stream = f"BT /F1 12 Tf {a:.6f} {b:.6f} {c:.6f} {d:.6f} 50 50 Tm ({text}) Tj ET".encode()
    pdf = f"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj
4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
5 0 obj << /Length {len(stream)} >>
stream
""".encode()
    pdf += stream + b"\nendstream\nendobj\ntrailer << /Size 6 /Root 1 0 R >>\n%%EOF"
    return pdf
