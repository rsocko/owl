from __future__ import annotations

import pytest

from doc_intelligence_hub.modules.ocr_quality.models import DocumentProfile, PageSignal
from doc_intelligence_hub.modules.ocr_quality.pdf_profiling import (
    PdfProfilingError,
    classify_pages,
    extract_page_signals,
    profile_pdf,
)


class TestClassifyPages:
    def test_empty_page_list_is_unknown(self):
        result = classify_pages([])
        assert result.profile == DocumentProfile.UNKNOWN
        assert result.page_count == 0

    def test_all_digital_pages(self):
        pages = [PageSignal(has_text=True, has_image=False) for _ in range(3)]
        result = classify_pages(pages)
        assert result.profile == DocumentProfile.DIGITAL_TEXT
        assert result.digital_pages == 3
        assert result.scanned_overlay_pages == 0
        assert result.no_text_pages == 0

    def test_all_scanned_with_overlay(self):
        pages = [PageSignal(has_text=True, has_image=True) for _ in range(4)]
        result = classify_pages(pages)
        assert result.profile == DocumentProfile.SCANNED_WITH_OVERLAY
        assert result.scanned_overlay_pages == 4

    def test_all_no_text(self):
        pages = [PageSignal(has_text=False, has_image=True) for _ in range(2)]
        result = classify_pages(pages)
        assert result.profile == DocumentProfile.NO_TEXT
        assert result.no_text_pages == 2

    def test_no_text_page_without_image_still_counts_as_no_text(self):
        pages = [PageSignal(has_text=False, has_image=False)]
        result = classify_pages(pages)
        assert result.profile == DocumentProfile.NO_TEXT

    def test_mixed_pages(self):
        pages = [
            PageSignal(has_text=True, has_image=False),
            PageSignal(has_text=True, has_image=True),
            PageSignal(has_text=False, has_image=True),
        ]
        result = classify_pages(pages)
        assert result.profile == DocumentProfile.MIXED
        assert result.page_count == 3
        assert result.digital_pages == 1
        assert result.scanned_overlay_pages == 1
        assert result.no_text_pages == 1


class TestExtractPageSignals:
    def test_corrupt_bytes_raise_pdf_profiling_error(self):
        with pytest.raises(PdfProfilingError):
            extract_page_signals(b"not a pdf at all", max_pages=10)

    def test_real_digital_pdf_is_classified_as_digital_text(self):
        reportlab = pytest.importorskip("reportlab")
        from io import BytesIO

        from reportlab.pdfgen import canvas  # noqa: F401 (import guarded by importorskip)

        buffer = BytesIO()
        c = reportlab.pdfgen.canvas.Canvas(buffer)
        c.drawString(100, 750, "This is a native, digitally created PDF page.")
        c.showPage()
        c.drawString(100, 750, "Second page with more native text content.")
        c.showPage()
        c.save()
        pdf_bytes = buffer.getvalue()

        result = profile_pdf(pdf_bytes, max_pages=10)
        assert result.profile == DocumentProfile.DIGITAL_TEXT
        assert result.page_count == 2

    def test_max_pages_limits_pages_profiled(self):
        reportlab = pytest.importorskip("reportlab")
        from io import BytesIO

        buffer = BytesIO()
        c = reportlab.pdfgen.canvas.Canvas(buffer)
        for _ in range(5):
            c.drawString(100, 750, "Native text page.")
            c.showPage()
        c.save()
        pdf_bytes = buffer.getvalue()

        result = profile_pdf(pdf_bytes, max_pages=2)
        assert result.page_count == 2
