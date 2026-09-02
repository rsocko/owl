"""OCR Quality module — multidimensional, versioned, explainable OCR quality scoring.

This module implements the scoring engine described in
``docs/modules/ocr-quality/ocr-quality-scoring.md`` (issue #29). It is a pure,
pluggable component: given document text/PDF inputs it produces a single
:class:`~doc_intelligence_hub.modules.ocr_quality.models.OCRQualityAssessment`
result. It does not read from or write to Paperless, OWL storage, or any other
module — callers (e.g. the issue #25 corpus inventory scanner, or a standalone
CLI) own persistence and orchestration.

The scorer never claims character-level OCR accuracy. Without ground truth,
its two dimensions — overlay/readability and machine-extraction — are
quality-risk estimates only, and never authorize automatic replacement of a
document.
"""

from doc_intelligence_hub.modules.ocr_quality.models import (
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
from doc_intelligence_hub.modules.ocr_quality.scorer import assess_document

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
