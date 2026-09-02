"""Azure Document Intelligence (``prebuilt-read``) candidate-generation provider.

Calls Azure's current ``prebuilt-read`` model directly. No reusable
Paperless-side remote-OCR integration exists in this codebase to delegate to
(searched ``core/paperless/client.py`` and the rest of the repo — nothing
found), so per the design doc's fallback ("Otherwise OWL may invoke Azure
directly for candidate generation"), this provider calls Azure itself.

Azure DI's ``prebuilt-read`` API returns extracted text/words/geometry, not a
rendered PDF. To honor the design doc's "request searchable PDF output"
requirement, this provider builds a searchable candidate PDF by overlaying an
invisible text layer (from Azure's word geometry) onto the *original* page
images via ``pypdf``/``reportlab`` — the same technique OCRmyPDF/Tesseract use
internally. The visual page content is unchanged; only a hidden, selectable
text layer is added.

Disabled unless ``azure_document_intelligence_enabled`` is true and an
endpoint/key are configured — never invoked otherwise, and never invoked
without first checking the batch's cost hard cap.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import Any

from doc_intelligence_hub.modules.ocr_quality import config as ocr_quality_config
from doc_intelligence_hub.modules.ocr_quality.candidate_models import CandidateGenResult
from doc_intelligence_hub.modules.ocr_quality.pdf_loader import load_pdf_pages
from doc_intelligence_hub.modules.ocr_quality.providers.base import OcrProvider

logger = logging.getLogger(__name__)

# Azure "inch" coordinates -> PDF points.
_POINTS_PER_INCH = 72.0


class AzureDocumentIntelligenceProvider(OcrProvider):
    """Direct Azure Document Intelligence ``prebuilt-read`` provider."""

    def __init__(self, endpoint: str | None = None, api_key: str | None = None) -> None:
        self._endpoint = (
            endpoint or ocr_quality_config.settings.azure_document_intelligence_endpoint
        )
        self._api_key = api_key or ocr_quality_config.settings.azure_document_intelligence_api_key

    @property
    def engine_name(self) -> str:
        return "azure-prebuilt-read"

    def model_version(self) -> str:
        return "prebuilt-read"

    async def is_available(self) -> tuple[bool, str | None]:
        if not ocr_quality_config.settings.azure_document_intelligence_enabled:
            return False, "Azure Document Intelligence provider is not enabled"
        if not self._endpoint or not self._api_key:
            return False, "Azure Document Intelligence endpoint/api_key not configured"
        try:
            import azure.ai.documentintelligence  # noqa: F401
        except ImportError:
            return False, "azure-ai-documentintelligence package is not installed"
        return True, None

    def estimate_cost(self, page_count: int) -> float:
        return round(page_count * ocr_quality_config.settings.azure_cost_per_page_usd, 4)

    async def generate_candidate(
        self,
        pdf_bytes: bytes,
        *,
        settings: dict[str, Any],
        timeout_seconds: float = 300.0,
    ) -> CandidateGenResult:
        start = time.monotonic()

        available, reason = await self.is_available()
        if not available:
            return CandidateGenResult(
                success=False, runtime_seconds=time.monotonic() - start, error_message=reason
            )

        # Estimate cost from the source PDF's own page count *before* calling
        # Azure, so a batch never incurs surprise billing above its cap.
        source_pages = load_pdf_pages(pdf_bytes)
        estimated_page_count = len(source_pages) or 1
        estimated_cost = self.estimate_cost(estimated_page_count)
        hard_cap = ocr_quality_config.settings.azure_cost_hard_cap_usd
        if estimated_cost > hard_cap:
            return CandidateGenResult(
                success=False,
                runtime_seconds=time.monotonic() - start,
                cost_estimate=estimated_cost,
                error_message=(
                    f"Estimated cost ${estimated_cost:.4f} exceeds hard cap ${hard_cap:.2f}"
                ),
            )

        try:
            result, operation_id = await asyncio.wait_for(
                asyncio.to_thread(self._analyze, pdf_bytes), timeout=timeout_seconds
            )
        except asyncio.TimeoutError:
            return CandidateGenResult(
                success=False,
                runtime_seconds=time.monotonic() - start,
                cost_estimate=estimated_cost,
                error_message=f"Azure Document Intelligence timed out after {timeout_seconds}s",
            )
        except Exception as exc:  # noqa: BLE001 - never let a provider crash a batch
            logger.exception("Azure Document Intelligence candidate generation failed")
            return CandidateGenResult(
                success=False,
                runtime_seconds=time.monotonic() - start,
                cost_estimate=estimated_cost,
                error_message=str(exc),
            )

        try:
            candidate_text, word_confidence = _extract_text_and_confidence(result)
            candidate_pdf_bytes = _build_searchable_pdf(pdf_bytes, result)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to assemble Azure candidate PDF")
            return CandidateGenResult(
                success=False,
                runtime_seconds=time.monotonic() - start,
                cost_estimate=estimated_cost,
                provider_operation_id=operation_id,
                error_message=f"Failed to assemble candidate PDF: {exc}",
            )

        page_count = len(getattr(result, "pages", None) or [])
        return CandidateGenResult(
            success=True,
            candidate_pdf_bytes=candidate_pdf_bytes,
            candidate_text=candidate_text,
            page_count=page_count or estimated_page_count,
            runtime_seconds=time.monotonic() - start,
            cost_estimate=self.estimate_cost(page_count or estimated_page_count),
            provider_operation_id=operation_id,
            word_confidence=word_confidence,
        )

    def _analyze(self, pdf_bytes: bytes) -> tuple[Any, str | None]:
        """Synchronous Azure SDK call, run off the event loop via ``asyncio.to_thread``."""
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
        from azure.core.credentials import AzureKeyCredential

        client = DocumentIntelligenceClient(
            endpoint=self._endpoint, credential=AzureKeyCredential(self._api_key)
        )
        poller = client.begin_analyze_document(
            "prebuilt-read",
            AnalyzeDocumentRequest(bytes_source=pdf_bytes),
        )
        result = poller.result()
        details = getattr(poller, "details", None) or {}
        operation_id = details.get("operation_id") if isinstance(details, dict) else None
        return result, operation_id


def _extract_text_and_confidence(result: Any) -> tuple[str, dict[str, Any]]:
    """Reconstruct full text and a compact per-page confidence summary."""
    content = getattr(result, "content", None)
    pages = getattr(result, "pages", None) or []

    if content:
        text = content
    else:
        lines: list[str] = []
        for page in pages:
            for line in getattr(page, "lines", None) or []:
                lines.append(getattr(line, "content", ""))
        text = "\n".join(lines)

    per_page: list[dict[str, Any]] = []
    for page in pages:
        words = getattr(page, "words", None) or []
        confidences = [w.confidence for w in words if getattr(w, "confidence", None) is not None]
        per_page.append(
            {
                "page_number": getattr(page, "page_number", None),
                "word_count": len(words),
                "avg_confidence": (sum(confidences) / len(confidences)) if confidences else None,
                "min_confidence": min(confidences) if confidences else None,
            }
        )
    return text, {"pages": per_page}


def _build_searchable_pdf(original_pdf_bytes: bytes, result: Any) -> bytes:
    """Overlay an invisible text layer (from Azure word geometry) onto the
    original page images and return the merged, searchable candidate PDF.

    The visual content of every page is preserved byte-for-byte in appearance
    — only a hidden, selectable text layer is added, mirroring how
    OCRmyPDF/Tesseract embed OCR text over a scanned raster.
    """
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(original_pdf_bytes))
    writer = PdfWriter()

    azure_pages = list(getattr(result, "pages", None) or [])
    azure_pages_by_number = {getattr(p, "page_number", i + 1): p for i, p in enumerate(azure_pages)}

    for index, page in enumerate(reader.pages):
        azure_page = azure_pages_by_number.get(index + 1)

        # Merging/transforming a page not yet assigned to a writer is
        # deprecated in pypdf, so attach it first.
        writer.add_page(page)
        added_page = writer.pages[-1]

        # Azure reports word polygons and page width/height in the page's
        # *visual* (post-rotation) orientation, but ``page.mediabox`` is the
        # PDF's raw, unrotated box. For any page with a non-zero ``/Rotate``
        # (common on scanned docs -- e.g. a 90/270 rotation swaps width and
        # height), leaving that mismatch unresolved badly misplaces/distorts
        # every overlay word box. Baking the rotation into the content
        # stream first (pypdf's documented, recommended step "before page
        # merging") normalizes the page to rotation=0 with a mediabox that
        # matches the visual orientation Azure measured, so the rest of this
        # function can work purely in that visual coordinate space.
        if added_page.rotation % 360 != 0:
            added_page.transfer_rotation_to_content()

        page_width = float(added_page.mediabox.width)
        page_height = float(added_page.mediabox.height)

        if azure_page is not None:
            overlay_bytes = _render_text_overlay(azure_page, page_width, page_height)
            overlay_reader = PdfReader(io.BytesIO(overlay_bytes))
            added_page.merge_page(overlay_reader.pages[0])

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _compute_overlay_scale(
    azure_page: Any, page_width_pts: float, page_height_pts: float
) -> tuple[float, float]:
    """Derive the Azure-units -> PDF-points scale factors for one page.

    Always derived as ``page_dimension_pts / azure_reported_dimension``,
    regardless of Azure's declared ``unit`` ("inch" or "pixel"): dividing the
    PDF's actual point size by Azure's reported size in *its own* unit
    yields "points per Azure-unit" directly -- exactly the multiplier needed
    to scale word polygons, which are expressed in that same unit. This
    self-corrects for any mismatch between Azure's reported page size and
    the PDF's own mediabox (rounding, a source PDF rasterized at a
    different DPI before being sent to Azure, etc.), which a fixed
    points-per-unit constant would otherwise silently get wrong.
    """
    az_width = float(getattr(azure_page, "width", 0.0) or 0.0)
    az_height = float(getattr(azure_page, "height", 0.0) or 0.0)

    if az_width > 0 and az_height > 0:
        return page_width_pts / az_width, page_height_pts / az_height

    # Azure didn't report page dimensions; fall back to the nominal
    # inches->points conversion (Azure's default unit for PDFs).
    return _POINTS_PER_INCH, _POINTS_PER_INCH


def _polygon_to_overlay_box(
    polygon: list[float], scale_x: float, scale_y: float, page_height_pts: float
) -> tuple[float, float, float]:
    """Convert one Azure word polygon into an axis-aligned PDF overlay box.

    Returns ``(x0, pdf_y, box_height)`` in PDF points, bottom-left origin,
    where ``(x0, pdf_y)`` is where the word's baseline should be drawn.
    """
    xs = [polygon[i] * scale_x for i in range(0, len(polygon), 2)]
    ys = [polygon[i] * scale_y for i in range(1, len(polygon), 2)]
    x0 = min(xs)
    y0, y1 = min(ys), max(ys)
    box_height = max(y1 - y0, 1.0)
    # Azure's origin is top-left; PDF's is bottom-left.
    pdf_y = page_height_pts - y1
    return x0, pdf_y, box_height


def _render_text_overlay(azure_page: Any, page_width_pts: float, page_height_pts: float) -> bytes:
    """Render one page's invisible text layer as a standalone single-page PDF."""
    from reportlab.pdfgen import canvas

    scale_x, scale_y = _compute_overlay_scale(azure_page, page_width_pts, page_height_pts)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width_pts, page_height_pts))
    c.setFillAlpha(0)

    for word in getattr(azure_page, "words", None) or []:
        polygon = getattr(word, "polygon", None) or []
        text = getattr(word, "content", "") or ""
        if not text or len(polygon) < 8:
            continue
        x0, pdf_y, box_height = _polygon_to_overlay_box(polygon, scale_x, scale_y, page_height_pts)

        c.saveState()
        c.setFont("Helvetica", box_height * 0.9)
        c.translate(x0, pdf_y)
        c.drawString(0, 0, text)
        c.restoreState()

    c.showPage()
    c.save()
    return buf.getvalue()
