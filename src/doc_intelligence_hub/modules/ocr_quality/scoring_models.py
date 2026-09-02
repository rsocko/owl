"""Data contracts for OCR quality assessment (issue #29).

These models define the versioned, explainable output contract from
``docs/modules/ocr-quality/ocr-quality-scoring.md``. They are intentionally
self-contained (no ORM/database coupling) so the scorer can be called
standalone or embedded in the issue #25 batch inventory scanner, which owns
the actual persistence schema.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class PageClassification(str, Enum):
    """Page-aware document profiling classification.

    Mixed image/text PDFs must not be treated as wholly digital or wholly
    scanned — each page gets its own classification.
    """

    DIGITAL_TEXT = "digital_text"
    SCANNED_WITH_OVERLAY = "scanned_with_overlay"
    IMAGE_NO_TEXT = "image_no_text"
    MIXED = "mixed"
    UNSUPPORTED_ERROR = "unsupported_error"


class ContentShape(str, Enum):
    """Broad shape of a document's content, used to contextualize signals."""

    PROSE = "prose"
    TABLE_OR_FORM = "table_or_form"
    CODE_HEAVY = "code_heavy"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class AssessmentStatus(str, Enum):
    """Operational-risk summary. Never a claim of OCR correctness."""

    GOOD = "GOOD"
    UNCERTAIN = "UNCERTAIN"
    REVIEW_RECOMMENDED = "REVIEW_RECOMMENDED"
    FAILED = "FAILED"


class Severity(str, Enum):
    """Severity of an individual explainability reason."""

    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class Reason(BaseModel):
    """A single explainable contribution to a score or status.

    ``code`` is a stable machine-readable identifier (e.g.
    ``"overlay.duplicate_text"``); ``message`` is a short human-readable
    explanation. Reasons with no numeric ``weight``/``value`` are informational
    (e.g. unavailable-signal markers).
    """

    code: str
    message: str
    severity: Severity = Severity.INFO
    component: str = Field(description="e.g. 'overlay', 'machine', 'profile'")
    weight: float | None = Field(
        default=None, description="Configured weight applied to this signal, if any."
    )
    value: float | None = Field(
        default=None, description="Raw signal value (0-1 unless noted), if computed."
    )


class PageProfile(BaseModel):
    """Per-page profiling result."""

    page_number: int = Field(ge=1)
    classification: PageClassification
    text_coverage: float | None = Field(
        default=None, description="Fraction (0-1) of page area covered by extractable text."
    )
    image_coverage: float | None = Field(
        default=None, description="Fraction (0-1) of page area covered by embedded images."
    )
    char_count: int = 0
    word_count: int = 0
    rotation: int = 0
    error: str | None = None


class DocumentProfile(BaseModel):
    """Page-aware document profile.

    Digital-native pages remain assessable even though they are normally
    exempt from image re-OCR. Short documents are not automatically treated
    as failures merely because they contain few characters.
    """

    page_count: int = Field(ge=0)
    pages: list[PageProfile] = Field(default_factory=list)
    dominant_classification: PageClassification | None = None
    content_shape: ContentShape = ContentShape.UNKNOWN
    language_hint: str | None = None
    producer: str | None = None
    is_short_document: bool = False
    has_pdf_geometry: bool = Field(
        default=False, description="Whether page geometry (bytes) was available for profiling."
    )


class DownstreamOutcome(BaseModel):
    """Privacy-safe downstream extraction outcome used as scoring evidence.

    Callers (TYRION, EOB matching, Action Queue, statements, Mission Control,
    OWL Insights) supply these; the scorer never fetches them itself. A
    failure raises review risk. A success does not prove the whole document
    is correct, so it is capped/low-weight corroboration only.
    """

    source: str = Field(description="e.g. 'tyrion', 'eob_matching', 'action_queue', 'statements'")
    success: bool
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    detail: str | None = Field(default=None, description="Short, privacy-safe note. No raw text.")


class ScoreComponent(BaseModel):
    """One scored dimension (overlay or machine) with full explainability."""

    score: float | None = Field(
        default=None, ge=0.0, le=100.0, description="None when no signals were available at all."
    )
    signals: dict[str, float | None] = Field(
        default_factory=dict, description="Raw computed signal values, keyed by signal name."
    )
    reasons: list[Reason] = Field(default_factory=list)
    unavailable_signals: list[str] = Field(
        default_factory=list,
        description="Signal names that could not be computed given the available inputs.",
    )


class OCRQualityAssessment(BaseModel):
    """The versioned, explainable OCR quality assessment result contract.

    Mirrors the contract in ``docs/modules/ocr-quality/ocr-quality-scoring.md``:
    overlay score, machine score, review status, reasons, document profile,
    scorer version, and assessment time. ``overlay_signals``/``machine_signals``
    provide additional per-signal detail for review UIs and calibration.
    """

    overlay_score: float | None = Field(default=None, ge=0.0, le=100.0)
    machine_score: float | None = Field(default=None, ge=0.0, le=100.0)
    review_status: AssessmentStatus
    reasons: list[Reason] = Field(default_factory=list)
    document_profile: DocumentProfile
    scorer_version: str
    assessed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    overlay_signals: ScoreComponent
    machine_signals: ScoreComponent
