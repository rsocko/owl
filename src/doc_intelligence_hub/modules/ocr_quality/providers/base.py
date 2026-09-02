"""Abstract base for OCR candidate-generation providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from doc_intelligence_hub.modules.ocr_quality.candidate_models import CandidateGenResult


class OcrProvider(ABC):
    """One engine + configuration that owns a candidate's PDF/text result.

    Implementations must never raise for expected failure modes (missing
    binary/credentials, provider timeout/error, invalid input) — they return
    a ``CandidateGenResult`` with ``success=False`` and an ``error_message``
    so the caller can transition the candidate to ``FAILED`` without an
    unhandled exception aborting a batch.
    """

    @property
    @abstractmethod
    def engine_name(self) -> str:
        """Stable engine identifier, e.g. ``'ocrmypdf-tesseract-5'``."""

    @abstractmethod
    def model_version(self) -> str:
        """Engine-specific version identifier, resolved at call time."""

    @abstractmethod
    async def is_available(self) -> tuple[bool, str | None]:
        """Whether this provider can run right now.

        Returns ``(True, None)`` if ready, or ``(False, reason)`` if not
        (e.g. binary missing, provider disabled/unconfigured). Checked before
        a candidate is dispatched so an unavailable provider fails fast with
        a clear reason rather than raising deep inside generation.
        """

    @abstractmethod
    async def generate_candidate(
        self,
        pdf_bytes: bytes,
        *,
        settings: dict[str, Any],
        timeout_seconds: float = 300.0,
    ) -> CandidateGenResult:
        """Generate a candidate PDF/text result for the given source PDF.

        Args:
            pdf_bytes: The current Paperless document's PDF bytes. Never
                mutated in place and never written back to Paperless.
            settings: Provider-specific, versioned configuration (e.g.
                language, deskew). Recorded verbatim on the candidate row.
            timeout_seconds: Maximum wall-clock time to allow.
        """
