"""Engine-neutral page geometry abstraction.

Scoring logic (``overlay_scoring.py``, ``profiling.py``) depends only on these
plain dataclasses, never on a specific PDF/OCR library. ``pdf_loader.py``
adapts ``pdfplumber`` output into this shape today; the Azure Document
Intelligence ``prebuilt-layout`` adapter (word-level text, geometry, and
confidence, reconstructed from Layout's word/polygon primitives rather than
its structured/markdown extraction) fills the same seam without changing any
scoring code.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WordBox:
    """A single extracted word with its bounding box (page-relative points)."""

    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    confidence: float | None = None
    order_index: int = 0
    """Index in native extraction order, used for reading-order checks."""
    angle_degrees: float = 0.0
    """Rotation of the word's glyphs, in degrees (0 = normal upright text).

    Derived from the underlying PDF text-rendering matrix, so it reflects
    arbitrary skew (not just cardinal 90 deg rotations) -- e.g. a vertical
    sidebar stamp rotated ~90 deg relative to the rest of a page. Purely
    informational: bbox-based signals/heuristics elsewhere are unaffected.
    """


@dataclass(frozen=True)
class ImageBox:
    """A single embedded raster image region on a page."""

    x0: float
    top: float
    x1: float
    bottom: float


@dataclass
class PdfPageData:
    """Parsed geometry for a single PDF page."""

    page_number: int
    width: float
    height: float
    words: list[WordBox] = field(default_factory=list)
    images: list[ImageBox] = field(default_factory=list)
    rotation: int = 0
    char_count: int = 0
    error: str | None = None
    """Set when this page could not be parsed; other fields are best-effort."""

    @property
    def area(self) -> float:
        return max(self.width, 0.0) * max(self.height, 0.0)

    @property
    def has_text(self) -> bool:
        return bool(self.words) or self.char_count > 0

    @property
    def has_images(self) -> bool:
        return bool(self.images)
