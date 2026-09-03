"""Local OCRmyPDF + Tesseract 5 candidate-generation provider.

The default, private, no-external-cost provider. Configuration (language,
deskew, cleanup, rotation, OCR mode) is explicit and versioned per the design
doc — settings are recorded verbatim on the candidate row.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from doc_intelligence_hub.modules.ocr_quality.candidate_models import CandidateGenResult
from doc_intelligence_hub.modules.ocr_quality.config import settings
from doc_intelligence_hub.modules.ocr_quality.pdf_loader import load_pdf_pages
from doc_intelligence_hub.modules.ocr_quality.profiling import reconstruct_text_from_pages
from doc_intelligence_hub.modules.ocr_quality.providers.base import OcrProvider

logger = logging.getLogger(__name__)


class OcrMyPdfProvider(OcrProvider):
    """Shells out to the ``ocrmypdf`` CLI to produce a searchable PDF + text."""

    def __init__(self, binary: str | None = None) -> None:
        self._binary = binary or settings.ocrmypdf_binary

    @property
    def engine_name(self) -> str:
        return "ocrmypdf-tesseract-5"

    def model_version(self) -> str:
        resolved = shutil.which(self._binary)
        if not resolved:
            return "ocrmypdf-unavailable"
        return f"ocrmypdf-binary:{Path(resolved).name}"

    async def is_available(self) -> tuple[bool, str | None]:
        if shutil.which(self._binary) is None:
            return False, f"ocrmypdf binary '{self._binary}' not found on PATH"
        return True, None

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
                success=False,
                runtime_seconds=time.monotonic() - start,
                error_message=reason,
            )

        language = str(settings.get("language", "eng"))
        deskew = bool(settings.get("deskew", True))
        cleanup = bool(settings.get("cleanup", True))
        rotate_pages = bool(settings.get("rotate_pages", True))
        force_ocr = bool(settings.get("force_ocr", True))

        with tempfile.TemporaryDirectory(prefix="ocr-candidate-") as tmpdir:
            tmp_path = Path(tmpdir)
            input_path = tmp_path / "input.pdf"
            output_path = tmp_path / "output.pdf"
            sidecar_path = tmp_path / "sidecar.txt"
            input_path.write_bytes(pdf_bytes)

            cmd = [
                self._binary,
                "--language",
                language,
                "--sidecar",
                str(sidecar_path),
                "--deskew" if deskew else "--no-deskew",
                "--clean" if cleanup else "",
                "--rotate-pages" if rotate_pages else "",
                "--force-ocr" if force_ocr else "--skip-text",
                str(input_path),
                str(output_path),
            ]
            cmd = [arg for arg in cmd if arg]

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(), timeout=timeout_seconds
                    )
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    return CandidateGenResult(
                        success=False,
                        runtime_seconds=time.monotonic() - start,
                        error_message=f"ocrmypdf timed out after {timeout_seconds}s",
                    )

                if proc.returncode != 0:
                    return CandidateGenResult(
                        success=False,
                        runtime_seconds=time.monotonic() - start,
                        error_message=(
                            f"ocrmypdf exited {proc.returncode}: "
                            f"{stderr.decode(errors='replace')[-2000:]}"
                        ),
                    )

                if not output_path.exists():
                    return CandidateGenResult(
                        success=False,
                        runtime_seconds=time.monotonic() - start,
                        error_message="ocrmypdf reported success but produced no output file",
                    )

                candidate_pdf_bytes = output_path.read_bytes()
                candidate_text = (
                    sidecar_path.read_text(encoding="utf-8", errors="replace")
                    if sidecar_path.exists()
                    else None
                )
                pages = load_pdf_pages(candidate_pdf_bytes)
                if candidate_text is None and pages:
                    candidate_text = reconstruct_text_from_pages(pages)

                return CandidateGenResult(
                    success=True,
                    candidate_pdf_bytes=candidate_pdf_bytes,
                    candidate_text=candidate_text or "",
                    page_count=len(pages),
                    runtime_seconds=time.monotonic() - start,
                    cost_estimate=0.0,
                    provider_operation_id=None,
                )
            except FileNotFoundError as exc:
                return CandidateGenResult(
                    success=False,
                    runtime_seconds=time.monotonic() - start,
                    error_message=f"ocrmypdf binary not executable: {exc}",
                )
            except Exception as exc:  # noqa: BLE001 - never let a provider crash a batch
                logger.exception("OCRmyPDF candidate generation failed")
                return CandidateGenResult(
                    success=False,
                    runtime_seconds=time.monotonic() - start,
                    error_message=str(exc),
                )
