"""Tests for candidate-generation providers (issue #18, slice 1).

Both providers must never require real binaries/network access to test: they
check availability up front and degrade to ``success=False`` with a clear
``error_message`` rather than raising. Subprocess/SDK calls are mocked here.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence import (
    AzureDocumentIntelligenceProvider,
)
from doc_intelligence_hub.modules.ocr_quality.providers.ocrmypdf_tesseract import OcrMyPdfProvider

from .conftest import make_minimal_pdf_bytes


def _make_real_pdf(text: str = "original", width: int = 144, height: int = 72) -> bytes:
    """A real, well-formed single-page PDF (via reportlab) with a valid xref table.

    ``pypdf`` (used by the Azure provider to build the searchable overlay) is
    strict about xref tables, unlike pdfplumber's recovery parsing — so the
    Azure provider's own tests need a genuinely well-formed PDF, not the
    hand-built xref-less fixture used elsewhere in this test suite.
    """
    import io

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.drawString(5, height / 2, text)
    c.showPage()
    c.save()
    return buf.getvalue()


class TestOcrMyPdfProvider:
    @pytest.mark.asyncio
    async def test_binary_missing_fails_gracefully(self):
        provider = OcrMyPdfProvider(binary="definitely-not-a-real-binary-xyz")
        result = await provider.generate_candidate(
            make_minimal_pdf_bytes(), settings={}, timeout_seconds=5.0
        )
        assert result.success is False
        assert "not found on PATH" in result.error_message

    @pytest.mark.asyncio
    async def test_is_available_false_when_binary_missing(self):
        provider = OcrMyPdfProvider(binary="definitely-not-a-real-binary-xyz")
        available, reason = await provider.is_available()
        assert available is False
        assert reason

    def test_engine_name(self):
        provider = OcrMyPdfProvider()
        assert provider.engine_name == "ocrmypdf-tesseract-5"

    @pytest.mark.asyncio
    async def test_successful_generation_with_mocked_subprocess(self, tmp_path):
        provider = OcrMyPdfProvider(binary="ocrmypdf-stub")
        candidate_pdf = make_minimal_pdf_bytes("OCR'd text")

        async def fake_exec(*cmd, stdout=None, stderr=None):
            # Simulate ocrmypdf writing its output + sidecar files before exiting 0.
            # Locate positional input/output paths (last two non-flag args).
            positional = [a for a in cmd if not a.startswith("--") and a not in ("eng",)]
            out_path = positional[-1]
            sidecar_path = cmd[cmd.index("--sidecar") + 1]
            with open(out_path, "wb") as f:
                f.write(candidate_pdf)
            with open(sidecar_path, "w", encoding="utf-8") as f:
                f.write("OCR'd text")

            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
            return proc

        with (
            patch(
                "doc_intelligence_hub.modules.ocr_quality.providers.ocrmypdf_tesseract.shutil.which",
                return_value="/usr/bin/ocrmypdf-stub",
            ),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=fake_exec)),
        ):
            result = await provider.generate_candidate(
                make_minimal_pdf_bytes("original"),
                settings={"language": "eng"},
                timeout_seconds=5.0,
            )

        assert result.success is True
        assert result.candidate_pdf_bytes == candidate_pdf
        assert result.candidate_text == "OCR'd text"
        assert result.cost_estimate == 0.0

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_fails(self):
        provider = OcrMyPdfProvider(binary="ocrmypdf-stub")

        async def fake_exec(*cmd, stdout=None, stderr=None):
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b"boom"))
            proc.returncode = 1
            return proc

        with (
            patch(
                "doc_intelligence_hub.modules.ocr_quality.providers.ocrmypdf_tesseract.shutil.which",
                return_value="/usr/bin/ocrmypdf-stub",
            ),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=fake_exec)),
        ):
            result = await provider.generate_candidate(
                make_minimal_pdf_bytes(), settings={}, timeout_seconds=5.0
            )

        assert result.success is False
        assert "exited 1" in result.error_message

    @pytest.mark.asyncio
    async def test_timeout_fails_gracefully(self):
        provider = OcrMyPdfProvider(binary="ocrmypdf-stub")

        async def fake_exec(*cmd, stdout=None, stderr=None):
            proc = MagicMock()

            async def _never_completes():
                await asyncio.sleep(10)

            proc.communicate = _never_completes
            proc.kill = MagicMock()
            return proc

        with (
            patch(
                "doc_intelligence_hub.modules.ocr_quality.providers.ocrmypdf_tesseract.shutil.which",
                return_value="/usr/bin/ocrmypdf-stub",
            ),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=fake_exec)),
        ):
            result = await provider.generate_candidate(
                make_minimal_pdf_bytes(), settings={}, timeout_seconds=0.05
            )

        assert result.success is False
        assert "timed out" in result.error_message


class TestAzureDocumentIntelligenceProvider:
    @pytest.mark.asyncio
    async def test_disabled_by_default(self):
        provider = AzureDocumentIntelligenceProvider(endpoint="https://x.invalid", api_key="key")
        available, reason = await provider.is_available()
        assert available is False
        assert "not enabled" in reason

    @pytest.mark.asyncio
    async def test_missing_credentials_fails(self):
        with patch(
            "doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence"
            ".ocr_quality_config.settings.azure_document_intelligence_enabled",
            True,
        ):
            provider = AzureDocumentIntelligenceProvider(endpoint="", api_key="")
            available, reason = await provider.is_available()
        assert available is False
        assert "endpoint/api_key" in reason

    @pytest.mark.asyncio
    async def test_cost_hard_cap_blocks_call(self):
        with (
            patch(
                "doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence"
                ".ocr_quality_config.settings.azure_document_intelligence_enabled",
                True,
            ),
            patch(
                "doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence"
                ".ocr_quality_config.settings.azure_cost_hard_cap_usd",
                0.0,
            ),
        ):
            provider = AzureDocumentIntelligenceProvider(
                endpoint="https://x.invalid", api_key="key"
            )
            result = await provider.generate_candidate(
                make_minimal_pdf_bytes(), settings={}, timeout_seconds=5.0
            )
        assert result.success is False
        assert "exceeds hard cap" in result.error_message

    @pytest.mark.asyncio
    async def test_successful_generation_with_mocked_azure_client(self):
        fake_word = MagicMock(confidence=0.95, content="Hello", polygon=[0, 0, 1, 0, 1, 1, 0, 1])
        fake_line = MagicMock(content="Hello")
        fake_page = MagicMock(
            page_number=1, unit="inch", width=2.0, height=1.0, words=[fake_word], lines=[fake_line]
        )
        fake_result = MagicMock(content="Hello", pages=[fake_page])

        with (
            patch(
                "doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence"
                ".ocr_quality_config.settings.azure_document_intelligence_enabled",
                True,
            ),
            patch(
                "doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence"
                ".AzureDocumentIntelligenceProvider._analyze",
                return_value=(fake_result, "op-123"),
            ),
        ):
            provider = AzureDocumentIntelligenceProvider(
                endpoint="https://x.invalid", api_key="key"
            )
            result = await provider.generate_candidate(
                _make_real_pdf("original", width=144, height=72),
                settings={},
                timeout_seconds=5.0,
            )

        assert result.success is True
        assert result.candidate_text == "Hello"
        assert result.provider_operation_id == "op-123"
        assert result.candidate_pdf_bytes is not None
        assert result.word_confidence["pages"][0]["avg_confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_analyze_error_fails_gracefully(self):
        with (
            patch(
                "doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence"
                ".ocr_quality_config.settings.azure_document_intelligence_enabled",
                True,
            ),
            patch(
                "doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence"
                ".AzureDocumentIntelligenceProvider._analyze",
                side_effect=RuntimeError("network unreachable"),
            ),
        ):
            provider = AzureDocumentIntelligenceProvider(
                endpoint="https://x.invalid", api_key="key"
            )
            result = await provider.generate_candidate(
                make_minimal_pdf_bytes(), settings={}, timeout_seconds=5.0
            )

        assert result.success is False
        assert "network unreachable" in result.error_message


class TestAzureOverlayPrecision:
    """Regression tests for the polygon -> PDF-point conversion (issue #18
    slice 1 follow-up): a self-correcting Azure-units -> PDF-points scale,
    and correct handling of PDF page ``/Rotate`` metadata.
    """

    def test_scale_self_corrects_for_page_size_mismatch(self):
        """Azure's reported page size (in its own unit) may not exactly
        match the PDF's actual point dimensions (rounding, the source PDF
        having been rasterized at a different DPI before being sent to
        Azure, etc.). The scale must be derived from Azure's own reported
        size, not assumed to always be a flat 72pt/inch.
        """
        from doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence import (
            _compute_overlay_scale,
        )

        azure_page = MagicMock(unit="inch", width=5.0, height=3.0)
        # The real PDF page is 300x200pt, not the 360x216pt (5in x 3in) that
        # a flat 72pt/inch conversion would assume.
        scale_x, scale_y = _compute_overlay_scale(
            azure_page, page_width_pts=300.0, page_height_pts=200.0
        )

        assert scale_x == pytest.approx(300.0 / 5.0, rel=1e-9)
        assert scale_y == pytest.approx(200.0 / 3.0, rel=1e-9)
        # The old hardcoded-72 behavior would have produced 72.0 exactly;
        # the self-correcting scale must differ from that here.
        assert scale_x != pytest.approx(72.0)

    def test_scale_pixel_unit_unaffected(self):
        """The "pixel" unit path (already self-correcting before this fix)
        must keep working identically.
        """
        from doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence import (
            _compute_overlay_scale,
        )

        azure_page = MagicMock(unit="pixel", width=850.0, height=1100.0)
        scale_x, scale_y = _compute_overlay_scale(
            azure_page, page_width_pts=612.0, page_height_pts=792.0
        )
        assert scale_x == pytest.approx(612.0 / 850.0, rel=1e-9)
        assert scale_y == pytest.approx(792.0 / 1100.0, rel=1e-9)

    def test_polygon_to_overlay_box_lands_at_expected_pdf_points(self):
        """Given a known scale and polygon, the resulting PDF-point box must
        match hand-computed expected coordinates within a tight tolerance.
        """
        from doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence import (
            _polygon_to_overlay_box,
        )

        scale_x = 300.0 / 360.0
        scale_y = 200.0 / 216.0
        # A word polygon in Azure's inch-space (top-left origin): x in
        # [1.0, 2.0], y in [1.0, 1.3].
        polygon = [1.0, 1.0, 2.0, 1.0, 2.0, 1.3, 1.0, 1.3]

        x0, pdf_y, box_height = _polygon_to_overlay_box(
            polygon, scale_x, scale_y, page_height_pts=200.0
        )

        expected_x0 = 1.0 * scale_x
        expected_y1 = 1.3 * scale_y
        expected_y0 = 1.0 * scale_y
        expected_box_height = max(expected_y1 - expected_y0, 1.0)
        expected_pdf_y = 200.0 - expected_y1

        assert x0 == pytest.approx(expected_x0, rel=1e-9)
        assert box_height == pytest.approx(expected_box_height, rel=1e-9)
        assert pdf_y == pytest.approx(expected_pdf_y, rel=1e-9)

    def test_build_searchable_pdf_handles_rotated_page(self):
        """A page with PDF ``/Rotate 90`` metadata must place the overlay
        word near the visual position Azure reported, not the position it
        would land at if the raw (unrotated) mediabox were used directly.
        """
        import io

        import pdfplumber

        # Raw (unrotated) portrait page: 100pt wide x 200pt tall, with no
        # visible text (so pdfplumber only picks up the invisible overlay).
        import reportlab.pdfgen.canvas as _canvas
        from pypdf import PdfReader, PdfWriter

        from doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence import (
            _build_searchable_pdf,
        )

        raw_buf = io.BytesIO()
        _c = _canvas.Canvas(raw_buf, pagesize=(100, 200))
        _c.showPage()
        _c.save()
        raw_pdf = raw_buf.getvalue()
        reader = PdfReader(io.BytesIO(raw_pdf))
        writer = PdfWriter()
        page = writer.add_page(reader.pages[0])
        page.rotate(90)  # Sets /Rotate 90 -- visual size becomes 200x100.
        rotated_buf = io.BytesIO()
        writer.write(rotated_buf)
        rotated_pdf_bytes = rotated_buf.getvalue()

        # Azure measures the *visual* (post-rotation, landscape) page: its
        # own unit is "inch", and 200pt/72 = 2.7(7)in, 100pt/72 = 1.3(8)in --
        # chosen so the scale factor is exactly 1.0 and only the rotation
        # handling is under test.
        fake_word = MagicMock(
            confidence=0.99,
            content="Hi",
            # Visual top-left corner region, in inches (top-left origin):
            # x in [0.1, 0.5], y in [0.1, 0.3].
            polygon=[0.1, 0.1, 0.5, 0.1, 0.5, 0.3, 0.1, 0.3],
        )
        fake_page = MagicMock(
            page_number=1,
            unit="inch",
            width=200.0 / 72.0,
            height=100.0 / 72.0,
            words=[fake_word],
        )
        fake_result = MagicMock(pages=[fake_page])

        candidate_bytes = _build_searchable_pdf(rotated_pdf_bytes, fake_result)

        with pdfplumber.open(io.BytesIO(candidate_bytes)) as pdf:
            out_page = pdf.pages[0]
            # Normalizing the rotation must resize the visible page to the
            # visual (landscape) dimensions Azure measured.
            assert out_page.width == pytest.approx(200.0, abs=1.0)
            assert out_page.height == pytest.approx(100.0, abs=1.0)

            words = out_page.extract_words()
            assert len(words) == 1
            word = words[0]

        # Expected visual position: x0 ~= 0.1in*72 = 7.2pt, top ~= 0.1in*72
        # = 7.2pt from the page's top edge -- i.e. near the top-left corner
        # of the (now 200x100) visual page. A rotation-handling bug would
        # instead place this well outside this region (e.g. near the
        # opposite corner, or outside the page bounds entirely).
        assert word["x0"] == pytest.approx(7.2, abs=6.0)
        assert word["top"] == pytest.approx(7.2, abs=8.0)
        assert 0 <= word["x0"] <= 100.0
        assert 0 <= word["top"] <= 50.0
