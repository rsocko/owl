"""Main OCR quality scoring entrypoint.

``assess_document`` is the pluggable seam described in issue #29: it accepts
document text/PDF inputs and returns a single, self-contained, versioned
:class:`OCRQualityAssessment`. It does not persist anything — the issue #25
inventory scanner (or any other caller) owns storing the result.
"""

from __future__ import annotations

from doc_intelligence_hub.modules.ocr_quality.machine_scoring import score_machine
from doc_intelligence_hub.modules.ocr_quality.overlay_scoring import score_overlay
from doc_intelligence_hub.modules.ocr_quality.pdf_loader import load_pdf_pages
from doc_intelligence_hub.modules.ocr_quality.pdf_types import PdfPageData
from doc_intelligence_hub.modules.ocr_quality.profiling import (
    build_document_profile,
    reconstruct_text_from_pages,
)
from doc_intelligence_hub.modules.ocr_quality.scoring_config import (
    DEFAULT_CONFIG,
    ScoringConfig,
    load_config,
)
from doc_intelligence_hub.modules.ocr_quality.scoring_config import (
    scorer_version as _scorer_version,
)
from doc_intelligence_hub.modules.ocr_quality.scoring_models import (
    AssessmentStatus,
    DocumentProfile,
    DownstreamOutcome,
    OCRQualityAssessment,
    Reason,
    ScoreComponent,
    Severity,
)


def assess_document(
    *,
    pdf_bytes: bytes | None = None,
    pdf_pages: list[PdfPageData] | None = None,
    text_content: str | None = None,
    confidence_data: list[float] | None = None,
    downstream_outcomes: list[DownstreamOutcome] | None = None,
    metadata: dict | None = None,
    expected_page_count: int | None = None,
    config: ScoringConfig | None = None,
) -> OCRQualityAssessment:
    """Assess OCR quality for a single document.

    Args:
        pdf_bytes: Raw PDF bytes (e.g. Paperless archive or original file).
            Parsed into page geometry via ``pdfplumber``. Ignored if
            ``pdf_pages`` is also given.
        pdf_pages: Pre-parsed page geometry, e.g. from a non-pdfplumber
            engine such as Azure Document Intelligence. Takes precedence
            over ``pdf_bytes``.
        text_content: Extracted text (e.g. Paperless ``document.content``).
            If omitted but page geometry is available, text is
            reconstructed from it in reading order.
        confidence_data: Optional per-word/per-page OCR engine confidence
            values (0-1).
        downstream_outcomes: Optional privacy-safe downstream extraction
            outcomes (TYRION, EOB matching, Action Queue, statements,
            Mission Control, OWL Insights) used as evidence, never as
            replacement authority.
        metadata: Optional document metadata (``producer``, ``language_hint``).
        expected_page_count: Optional expected page count (e.g. from
            Paperless metadata) used for the overlay page-integrity signal.
        config: Scoring configuration. Defaults to :func:`load_config`
            (built-in defaults merged with any ``config/ocr-quality-scoring.yaml``
            override). Pass an explicit :class:`ScoringConfig` for
            deterministic, environment-independent results (e.g. in tests).

    Returns:
        A self-contained, versioned :class:`OCRQualityAssessment`. Nothing is
        persisted; the caller owns storage.
    """
    cfg = config or load_config()

    if pdf_pages is None and pdf_bytes:
        pdf_pages = load_pdf_pages(pdf_bytes)

    profile = build_document_profile(
        pdf_pages=pdf_pages, text_content=text_content, metadata=metadata, config=cfg
    )

    effective_text = text_content
    if effective_text is None and pdf_pages:
        effective_text = reconstruct_text_from_pages(pdf_pages)

    overlay_component = score_overlay(
        pdf_pages=pdf_pages,
        profile=profile,
        config=cfg,
        expected_page_count=expected_page_count,
    )
    machine_component = score_machine(
        text_content=effective_text,
        confidence_data=confidence_data,
        downstream_outcomes=downstream_outcomes,
        content_shape=profile.content_shape,
        config=cfg,
    )

    content_score = _compute_content_score(overlay_component, machine_component, cfg)

    review_status, status_reasons = _determine_review_status(
        overlay_component, machine_component, content_score, profile, cfg
    )

    return OCRQualityAssessment(
        overlay_score=overlay_component.score,
        machine_score=machine_component.score,
        content_score=content_score,
        review_status=review_status,
        reasons=[*overlay_component.reasons, *machine_component.reasons, *status_reasons],
        document_profile=profile,
        scorer_version=_scorer_version(cfg),
        overlay_signals=overlay_component,
        machine_signals=machine_component,
    )


def _compute_content_score(
    overlay: ScoreComponent, machine: ScoreComponent, config: ScoringConfig
) -> float | None:
    """Blend machine/content quality with reading-order correctness.

    Reading order (extracted word sequence vs. visual layout) directly
    affects whether captured label/value pairs are correct — e.g. an
    account number split across two out-of-sequence lines is a content
    error, not a cosmetic layout one. This is deliberately narrower than
    ``overlay_score``, which also folds in presentation-only signals (page
    coverage, bounds sanity, duplicate text, page integrity) that don't
    bear on whether the captured content itself is right. Each half is
    renormalized when only one is available (e.g. no PDF geometry at all,
    so reading_order can't be computed) rather than penalizing the document
    for a signal that was never computable.
    """
    reading_order = overlay.signals.get("reading_order")
    weights = config.content_weights.model_dump()

    weighted_total = 0.0
    weight_sum = 0.0
    if machine.score is not None:
        weighted_total += machine.score * weights["machine"]
        weight_sum += weights["machine"]
    if reading_order is not None:
        weighted_total += reading_order * 100.0 * weights["reading_order"]
        weight_sum += weights["reading_order"]

    if weight_sum <= 0:
        return None
    return round(weighted_total / weight_sum, 2)


def _determine_review_status(
    overlay: ScoreComponent,
    machine: ScoreComponent,
    content_score: float | None,
    profile: DocumentProfile,
    config: ScoringConfig,
) -> tuple[AssessmentStatus, list[Reason]]:
    """Derive the operational-risk status primarily from ``content_score``.

    ``content_score`` (machine/content quality blended with reading-order
    correctness — see ``_compute_content_score``) drives the status because
    reading-order errors are content errors: they change which value a
    captured field actually holds. Overlay signals unrelated to content
    (page coverage, bounds sanity, duplicate text, page integrity) no
    longer silently drag the status down via a bare ``min()`` with the full
    overlay score — but any BLOCKING-severity reason from either component
    (e.g. missing/reordered pages, completely empty extracted text) still
    prevents a GOOD or UNCERTAIN status regardless of the numeric score.
    """
    reasons: list[Reason] = []
    thresholds = config.status_thresholds

    has_blocking = any(
        r.severity == Severity.BLOCKING for r in (*overlay.reasons, *machine.reasons)
    )

    if content_score is None:
        if profile.page_count == 0:
            reasons.append(
                Reason(
                    code="status.empty_document",
                    message="No page geometry or extracted text was provided; nothing could be "
                    "assessed.",
                    severity=Severity.BLOCKING,
                    component="profile",
                )
            )
            return AssessmentStatus.FAILED, reasons

        reasons.append(
            Reason(
                code="status.no_scorable_signals",
                message="Neither machine content quality nor reading order could be scored for "
                "this document.",
                severity=Severity.WARNING,
                component="profile",
            )
        )
        return AssessmentStatus.UNCERTAIN, reasons

    if content_score >= thresholds.good_min:
        status = AssessmentStatus.GOOD
    elif content_score >= thresholds.uncertain_min:
        status = AssessmentStatus.UNCERTAIN
    elif content_score >= thresholds.review_recommended_min:
        status = AssessmentStatus.REVIEW_RECOMMENDED
    else:
        status = AssessmentStatus.FAILED

    if has_blocking and status in (AssessmentStatus.GOOD, AssessmentStatus.UNCERTAIN):
        status = AssessmentStatus.REVIEW_RECOMMENDED
        reasons.append(
            Reason(
                code="status.blocking_signal_present",
                message="A blocking-severity signal downgraded an otherwise acceptable score.",
                severity=Severity.WARNING,
                component="profile",
            )
        )

    reasons.append(
        Reason(
            code="status.content_score",
            message=f"Derived status from content score {content_score:.1f} "
            f"(machine={machine.score}, "
            f"reading_order={overlay.signals.get('reading_order')}, "
            f"overlay={overlay.score}).",
            severity=Severity.INFO,
            component="profile",
            value=round(content_score, 2),
        )
    )
    return status, reasons


__all__ = ["assess_document", "DEFAULT_CONFIG"]
