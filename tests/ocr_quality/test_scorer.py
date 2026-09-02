"""End-to-end tests for assess_document: boundaries, malformed input,
determinism, and scorer-version behavior.
"""

from __future__ import annotations

import copy

from doc_intelligence_hub.modules.ocr_quality.config import ScoringConfig
from doc_intelligence_hub.modules.ocr_quality.models import AssessmentStatus, DownstreamOutcome
from doc_intelligence_hub.modules.ocr_quality.scorer import assess_document

from .conftest import make_digital_page, make_error_page, make_minimal_pdf_bytes


def test_fully_empty_input_is_failed() -> None:
    assessment = assess_document()
    assert assessment.review_status == AssessmentStatus.FAILED
    assert assessment.overlay_score is None
    assert assessment.machine_score is None
    assert assessment.document_profile.page_count == 0


def test_none_text_and_no_pdf_is_uncertain_or_failed_never_crashes() -> None:
    assessment = assess_document(text_content=None)
    assert assessment.review_status in (AssessmentStatus.FAILED, AssessmentStatus.UNCERTAIN)


def test_empty_string_text_is_low_scoring_and_flagged() -> None:
    assessment = assess_document(text_content="")
    assert assessment.machine_score == 0.0
    assert assessment.review_status in (
        AssessmentStatus.FAILED,
        AssessmentStatus.REVIEW_RECOMMENDED,
    )


def test_binary_garbage_text_does_not_crash_and_scores_low() -> None:
    garbage = bytes(range(0, 40)).decode("latin-1")
    assessment = assess_document(text_content=garbage)
    assert assessment.machine_score is not None
    assert assessment.machine_score < 60.0


def test_whitespace_only_text_treated_as_empty() -> None:
    assessment = assess_document(text_content="   \n\t  ")
    assert assessment.machine_score == 0.0


def test_malformed_pdf_bytes_do_not_crash() -> None:
    assessment = assess_document(pdf_bytes=b"not a real pdf")
    assert assessment.review_status in (
        AssessmentStatus.FAILED,
        AssessmentStatus.UNCERTAIN,
    )


def test_good_digital_document_end_to_end() -> None:
    text = (
        "This is a routine account statement. It contains a normal, coherent "
        "paragraph describing recent activity on the account, including the "
        "total amount due of $120.00 and the due date of 04/15/2024."
    )
    pages = [make_digital_page(1)]
    assessment = assess_document(pdf_pages=pages, text_content=text)
    assert assessment.overlay_score is not None
    assert assessment.machine_score is not None
    assert assessment.review_status in (AssessmentStatus.GOOD, AssessmentStatus.UNCERTAIN)


def test_mixed_pdf_with_missing_pages_is_review_recommended_or_worse() -> None:
    pages = [make_digital_page(1), make_error_page(2)]
    assessment = assess_document(
        pdf_pages=pages, text_content="Some short text.", expected_page_count=5
    )
    assert assessment.review_status in (
        AssessmentStatus.REVIEW_RECOMMENDED,
        AssessmentStatus.FAILED,
    )
    assert any(r.severity.value == "blocking" for r in assessment.reasons)


def test_short_valid_document_is_not_automatically_failed() -> None:
    assessment = assess_document(text_content="Paid in full.")
    assert assessment.document_profile.is_short_document is True
    assert assessment.review_status != AssessmentStatus.FAILED


def test_real_pdf_bytes_round_trip() -> None:
    pdf_bytes = make_minimal_pdf_bytes("Hello World")
    assessment = assess_document(pdf_bytes=pdf_bytes)
    assert assessment.document_profile.has_pdf_geometry is True
    assert assessment.document_profile.page_count == 1
    # Text was reconstructed from geometry since none was passed explicitly.
    assert assessment.machine_score is not None


def test_downstream_evidence_hook_is_recorded_as_reasons_not_authority() -> None:
    text = "This is a normal document with reasonable prose content for testing purposes here."
    assessment = assess_document(
        text_content=text,
        downstream_outcomes=[
            DownstreamOutcome(source="tyrion", success=False, detail="account signal missing"),
        ],
    )
    assert assessment.machine_signals.signals["downstream_evidence"] is not None
    # Downstream failure is evidence, contributing to reasons, not a hard override.
    assert assessment.machine_score is not None


def test_assessment_is_deterministic_for_identical_input() -> None:
    text = "This document repeats identical content for a determinism check."
    pages = [make_digital_page(1)]
    first = assess_document(pdf_pages=pages, text_content=text)
    second = assess_document(pdf_pages=pages, text_content=text)
    assert first.overlay_score == second.overlay_score
    assert first.machine_score == second.machine_score
    assert first.review_status == second.review_status
    assert first.scorer_version == second.scorer_version


def test_scorer_version_recorded_on_every_assessment() -> None:
    assessment = assess_document(text_content="Some text here.")
    assert assessment.scorer_version
    assert "/" in assessment.scorer_version


def test_scorer_version_changes_when_config_version_changes() -> None:
    text = "This document has some reasonable content for scoring purposes."
    config_a = ScoringConfig(config_version="alpha")
    config_b = ScoringConfig(config_version="beta")
    a = assess_document(text_content=text, config=config_a)
    b = assess_document(text_content=text, config=config_b)
    assert a.scorer_version != b.scorer_version
    assert a.scorer_version.split("/")[0] == b.scorer_version.split("/")[0]


def test_scorer_version_changes_when_weights_change_via_config_version() -> None:
    """Tuning weights must be paired with a config_version bump so results
    are traceable to the configuration that produced them."""
    text = "This document has some reasonable content for scoring purposes."
    base_config = ScoringConfig(config_version="v1")
    tuned_weights = copy.deepcopy(base_config.machine_weights)
    tuned_config = ScoringConfig(
        config_version="v2",
        machine_weights=type(tuned_weights)(
            **{**tuned_weights.model_dump(), "structured_entities": 40.0}
        ),
    )

    base_result = assess_document(text_content=text, config=base_config)
    tuned_result = assess_document(text_content=text, config=tuned_config)

    assert base_result.scorer_version != tuned_result.scorer_version
    # Different weighting is very likely to move the score for this input.
    assert base_result.machine_score != tuned_result.machine_score


def test_table_document_end_to_end() -> None:
    text = "\n".join(
        [
            "Patient Name:   Jane Doe",
            "Account Number: 445566",
            "Date of Service: 02/03/2024",
            "Total Billed:      $250.00",
            "Total Allowed:      $200.00",
        ]
    )
    assessment = assess_document(text_content=text)
    assert assessment.document_profile.content_shape.value == "table_or_form"
    assert assessment.machine_score is not None


def test_code_heavy_text_does_not_crash_and_is_profiled() -> None:
    text = "\n".join(
        [
            "def compute(x):",
            "    if x > 0:",
            "        return x + 1;",
            "    return 0;",
            "class Foo(Bar):",
            "    pass",
        ]
    )
    assessment = assess_document(text_content=text)
    assert assessment.document_profile.content_shape.value in ("code_heavy", "mixed")
    assert assessment.machine_score is not None
