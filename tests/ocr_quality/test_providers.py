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

    def test_polygon_edge_angle_degrees_horizontal_is_zero(self):
        """A normal, horizontal (reading left-to-right) polygon must report
        an angle of ~0 degrees.
        """
        from doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence import (
            _polygon_edge_angle_degrees,
        )

        # p0=(1,1) top-left, p1=(2,1) top-right -- horizontal reading edge.
        polygon = [1.0, 1.0, 2.0, 1.0, 2.0, 1.3, 1.0, 1.3]
        angle = _polygon_edge_angle_degrees(polygon, scale_x=1.0, scale_y=1.0)
        assert angle == pytest.approx(0.0, abs=1e-6)

    def test_polygon_edge_angle_degrees_rotated_word(self):
        """A word polygon rotated ~90 deg (e.g. a vertical sidebar stamp,
        issue #148) must report an angle near 90 degrees, not 0.
        """
        from doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence import (
            _polygon_edge_angle_degrees,
        )

        # p0=(1,3) top-left, p1=(1,1) top-right of a word whose reading
        # direction points "up" in Azure's y-down space -- azure's dy is
        # negative (1 - 3 = -2), so the PDF-space angle (negated) is +90.
        polygon = [1.0, 3.0, 1.0, 1.0, 1.3, 1.0, 1.3, 3.0]
        angle = _polygon_edge_angle_degrees(polygon, scale_x=1.0, scale_y=1.0)
        assert angle == pytest.approx(90.0, abs=1e-6)

    def test_is_near_horizontal_tolerance(self):
        from doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence import (
            _is_near_horizontal,
        )

        assert _is_near_horizontal(0.0) is True
        assert _is_near_horizontal(2.5) is True  # within tolerance
        assert _is_near_horizontal(180.0) is True
        assert _is_near_horizontal(-178.0) is True  # near 180 the other way
        assert _is_near_horizontal(90.0) is False
        assert _is_near_horizontal(45.0) is False

    def test_rotated_word_is_drawn_at_correct_location_not_smeared(self):
        """Regression test for issue #148: a ~90 deg rotated word polygon
        must no longer be collapsed into a giant axis-aligned box drawn
        sideways across the page. After the fix, pdfplumber re-extracting
        the invisible overlay text must find it near its true (small,
        rotated) location, not smeared across a huge horizontal span.
        """
        import io

        import pdfplumber
        import reportlab.pdfgen.canvas as _canvas
        from pypdf import PdfReader, PdfWriter

        from doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence import (
            _build_searchable_pdf,
        )

        raw_buf = io.BytesIO()
        _c = _canvas.Canvas(raw_buf, pagesize=(300, 300))
        _c.showPage()
        _c.save()
        reader = PdfReader(io.BytesIO(raw_buf.getvalue()))
        writer = PdfWriter()
        writer.add_page(reader.pages[0])
        out_buf = io.BytesIO()
        writer.write(out_buf)
        plain_pdf_bytes = out_buf.getvalue()

        # A tall, narrow polygon (width 20pt, height 100pt in azure's own
        # "inch" unit with scale 1.0) rotated ~90 deg -- reading direction
        # points straight up the page, mirroring the real-world vertical
        # sidebar stamp from the Ford lease document.
        fake_word = MagicMock(
            confidence=0.9,
            content="SidebarStamp",
            # p0=(10,250) top-left, p1=(10,150) top-right (reading "up"),
            # p2=(30,150) bottom-right, p3=(30,250) bottom-left.
            polygon=[10.0, 250.0, 10.0, 150.0, 30.0, 150.0, 30.0, 250.0],
        )
        fake_page = MagicMock(
            page_number=1,
            unit="inch",
            width=300.0,
            height=300.0,
            words=[fake_word],
        )
        fake_result = MagicMock(pages=[fake_page])

        candidate_bytes = _build_searchable_pdf(plain_pdf_bytes, fake_result)

        with pdfplumber.open(io.BytesIO(candidate_bytes)) as pdf:
            words = pdf.pages[0].extract_words()

        assert len(words) == 1
        word = words[0]
        # The old collapse-to-axis-aligned-box bug would produce a box
        # whose width equals the polygon's long (100pt) axis, spanning far
        # outside the tall/narrow region the word actually occupies -- e.g.
        # x1 well beyond 30 + a small margin. The fixed rotated placement
        # must stay within (approximately) the polygon's own footprint.
        assert word["x1"] - word["x0"] < 60.0
        assert 0.0 <= word["x0"] <= 300.0
        assert 0.0 <= word["top"] <= 300.0


def _make_fake_table(rows: list[list[str]], start_offset: int) -> tuple[MagicMock, str]:
    """Build a fake Azure ``DocumentTable`` (MagicMock) plus the per-cell-line
    text block Azure's plain-``text`` content format would emit for it,
    positioned as if it started at ``start_offset`` within some larger
    ``result.content`` string -- mirroring Azure's real one-line-per-cell
    serialization this fix reconstructs away from.
    """
    cells = []
    cell_lines: list[str] = []
    for row_index, row in enumerate(rows):
        for column_index, cell_text in enumerate(row):
            cells.append(
                MagicMock(row_index=row_index, column_index=column_index, content=cell_text)
            )
            cell_lines.append(cell_text)
    block = "\n".join(cell_lines)
    span = MagicMock(offset=start_offset, length=len(block))
    table = MagicMock(cells=cells, spans=[span])
    return table, block


class TestAzureTableTextReconstruction:
    """Regression tests: candidate text must row-join table cells the same
    way the existing Paperless/pdftotext-style comparison text does, instead
    of Azure's default one-line-per-cell serialization (issue: table text
    formatting mismatch distorting the human comparison view/diff stats).
    """

    def test_row_joined_table_text_joins_cells_by_row(self):
        from doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence import (
            _row_joined_table_text,
        )

        header = ["Payment Due Date", "Principal", "Interest", "Escrow", "Late Charge", "Other", "Total"]
        data = ["Jul 12, 2026", "141.97", "0.00", "0.00", "0.00", "0.00", "141.97"]
        table, _ = _make_fake_table([header, data], start_offset=0)

        result = _row_joined_table_text(table)

        assert result == f"{' '.join(header)}\n{' '.join(data)}"
        # 2 rows, not 14 per-cell lines.
        assert result.count("\n") == 1

    def test_extract_text_reconstructs_table_and_preserves_surrounding_text(self):
        from doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence import (
            _extract_text_and_confidence,
        )

        header = ["Payment Due Date", "Principal", "Interest", "Escrow", "Late Charge", "Other", "Total"]
        data = ["Jul 12, 2026", "141.97", "0.00", "0.00", "0.00", "0.00", "141.97"]
        before = "Statement Summary"
        after = "Thank you for your business"

        table, block = _make_fake_table([header, data], start_offset=len(before) + 1)
        content = f"{before}\n{block}\n{after}"

        fake_result = MagicMock(content=content, pages=[], tables=[table])

        text, _confidence = _extract_text_and_confidence(fake_result)

        expected_table_block = f"{' '.join(header)}\n{' '.join(data)}"
        assert text == f"{before}\n{expected_table_block}\n{after}"
        # Row-joined, not one line per cell.
        assert "Payment Due Date Principal Interest Escrow Late Charge Other Total" in text
        assert text.count("Payment Due Date\n") == 0

    def test_extract_text_with_no_tables_is_unchanged(self):
        """Guards the common non-table document case: byte-for-byte identical
        to plain ``result.content`` when there are no tables at all."""
        from doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence import (
            _extract_text_and_confidence,
        )

        content = "Just a plain paragraph.\nAnother line.\n"
        fake_result_no_attr = MagicMock(content=content, pages=[])
        del fake_result_no_attr.tables  # simulate no `tables` attribute at all
        fake_result_empty = MagicMock(content=content, pages=[], tables=[])

        text_no_attr, _ = _extract_text_and_confidence(fake_result_no_attr)
        text_empty, _ = _extract_text_and_confidence(fake_result_empty)

        assert text_no_attr == content
        assert text_empty == content

    def test_reconstruct_multiple_tables_preserves_order(self):
        from doc_intelligence_hub.modules.ocr_quality.providers.azure_document_intelligence import (
            _extract_text_and_confidence,
        )

        table1, block1 = _make_fake_table([["A", "B"], ["1", "2"]], start_offset=len("Intro\n"))
        middle = "Middle paragraph."
        offset2 = len("Intro\n") + len(block1) + len("\n" + middle + "\n")
        table2, block2 = _make_fake_table([["X", "Y"], ["9", "8"]], start_offset=offset2)
        content = f"Intro\n{block1}\n{middle}\n{block2}\nOutro"

        fake_result = MagicMock(content=content, pages=[], tables=[table1, table2])

        text, _ = _extract_text_and_confidence(fake_result)

        assert text == "Intro\nA B\n1 2\nMiddle paragraph.\nX Y\n9 8\nOutro"
