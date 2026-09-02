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
                make_minimal_pdf_bytes("original"), settings={"language": "eng"}, timeout_seconds=5.0
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
            provider = AzureDocumentIntelligenceProvider(endpoint="https://x.invalid", api_key="key")
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
            provider = AzureDocumentIntelligenceProvider(endpoint="https://x.invalid", api_key="key")
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
            provider = AzureDocumentIntelligenceProvider(endpoint="https://x.invalid", api_key="key")
            result = await provider.generate_candidate(
                make_minimal_pdf_bytes(), settings={}, timeout_seconds=5.0
            )

        assert result.success is False
        assert "network unreachable" in result.error_message
