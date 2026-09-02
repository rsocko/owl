"""OCR quality — baseline inventory (issue #25) and quality scorer (issue #29).

This package holds two coordinated but independent pieces of OCR quality
work:

- The **baseline inventory** (issue #25): a non-mutating, resumable
  inventory of OCR/text quality across the Paperless corpus. Stage 1
  computes fast text/metadata signals for every accessible document. Stage 2
  selects a deterministic stratified sample and profiles the sampled PDFs
  page-by-page (digital / scanned-with-overlay / no-text / mixed). See
  ``config.py``, ``models.py``, ``cli.py``, ``service.py``, ``database.py``,
  ``sampling.py``, ``signals.py``, and ``pdf_profiling.py``.

- The **quality scorer** (issue #29): a pure, pluggable multidimensional
  scoring engine described in
  ``docs/modules/ocr-quality/ocr-quality-scoring.md``. Given document
  text/PDF inputs it produces a single
  :class:`~doc_intelligence_hub.modules.ocr_quality.scoring_models.OCRQualityAssessment`
  result. It does not read from or write to Paperless, OWL storage, or any
  other module — callers (e.g. the issue #25 corpus inventory scanner, or
  the standalone ``ocr-quality-score`` CLI) own persistence and
  orchestration. See ``scoring_models.py``, ``scoring_config.py``,
  ``scoring_cli.py``, ``profiling.py``, ``overlay_scoring.py``,
  ``machine_scoring.py``, ``scorer.py``, ``pdf_types.py``, and
  ``pdf_loader.py``.

  The scorer never claims character-level OCR accuracy. Without ground
  truth, its two dimensions — overlay/readability and machine-extraction —
  are quality-risk estimates only, and never authorize automatic
  replacement of a document.

These two pieces intentionally do not import from each other yet: #25's
inventory computes its own preliminary/stratification signals (versioned
separately via ``INVENTORY_SIGNAL_VERSION``) and does not implement the full
#29 scorer. #29's scorer is designed to be callable by #25's batch scanner
(or standalone) once that wiring is added; note that ``models.py`` (#25) and
``scoring_models.py`` (#29) each define their own, unrelated
``DocumentProfile`` type — one an enum, one a full profile model — so import
from the specific submodule you need rather than assuming a single shared
``DocumentProfile``.
"""

from doc_intelligence_hub.modules.ocr_quality.scorer import assess_document
from doc_intelligence_hub.modules.ocr_quality.scoring_models import (
    AssessmentStatus,
    ContentShape,
    DocumentProfile,
    DownstreamOutcome,
    OCRQualityAssessment,
    PageClassification,
    PageProfile,
    Reason,
    ScoreComponent,
    Severity,
)

__all__ = [
    "AssessmentStatus",
    "ContentShape",
    "DocumentProfile",
    "DownstreamOutcome",
    "OCRQualityAssessment",
    "PageClassification",
    "PageProfile",
    "Reason",
    "ScoreComponent",
    "Severity",
    "assess_document",
]
