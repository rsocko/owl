"""Tests for page-aware document profiling."""

from __future__ import annotations

from doc_intelligence_hub.modules.ocr_quality.profiling import build_document_profile
from doc_intelligence_hub.modules.ocr_quality.scoring_config import DEFAULT_CONFIG
from doc_intelligence_hub.modules.ocr_quality.scoring_models import ContentShape, PageClassification

from .conftest import (
    make_digital_page,
    make_error_page,
    make_image_only_page,
    make_scanned_overlay_page,
)


def test_digital_only_document_is_classified_digital_text() -> None:
    profile = build_document_profile(pdf_pages=[make_digital_page(1), make_digital_page(2)])
    assert profile.page_count == 2
    assert all(p.classification == PageClassification.DIGITAL_TEXT for p in profile.pages)
    assert profile.dominant_classification == PageClassification.DIGITAL_TEXT
    assert profile.has_pdf_geometry is True


def test_scanned_with_overlay_document() -> None:
    profile = build_document_profile(pdf_pages=[make_scanned_overlay_page(1)])
    assert profile.pages[0].classification == PageClassification.SCANNED_WITH_OVERLAY
    assert profile.dominant_classification == PageClassification.SCANNED_WITH_OVERLAY


def test_image_without_text_document() -> None:
    profile = build_document_profile(pdf_pages=[make_image_only_page(1)])
    assert profile.pages[0].classification == PageClassification.IMAGE_NO_TEXT


def test_mixed_document_is_not_treated_as_wholly_digital_or_scanned() -> None:
    """A PDF with both digital and scanned pages must be profiled per-page."""
    pages = [make_digital_page(1), make_scanned_overlay_page(2), make_image_only_page(3)]
    profile = build_document_profile(pdf_pages=pages)

    assert profile.pages[0].classification == PageClassification.DIGITAL_TEXT
    assert profile.pages[1].classification == PageClassification.SCANNED_WITH_OVERLAY
    assert profile.pages[2].classification == PageClassification.IMAGE_NO_TEXT
    # Document-level summary must reflect the mix, not collapse to one type.
    assert profile.dominant_classification == PageClassification.MIXED


def test_unsupported_error_page_is_isolated_not_fatal() -> None:
    pages = [make_digital_page(1), make_error_page(2)]
    profile = build_document_profile(pdf_pages=pages)
    assert profile.pages[1].classification == PageClassification.UNSUPPORTED_ERROR
    assert profile.pages[1].error is not None
    # The good page is still profiled normally.
    assert profile.pages[0].classification == PageClassification.DIGITAL_TEXT


def test_all_error_pages_dominant_classification_is_error() -> None:
    profile = build_document_profile(pdf_pages=[make_error_page(1), make_error_page(2)])
    assert profile.dominant_classification == PageClassification.UNSUPPORTED_ERROR


def test_short_document_is_flagged_but_not_penalized_by_profile_alone() -> None:
    short_text = "Paid."
    profile = build_document_profile(text_content=short_text, config=DEFAULT_CONFIG)
    assert profile.is_short_document is True
    # Profiling only flags shortness; it does not fail/score the document.
    assert profile.page_count == 1


def test_longer_document_is_not_flagged_short() -> None:
    long_text = "This is a normal length paragraph. " * 20
    profile = build_document_profile(text_content=long_text)
    assert profile.is_short_document is False


def test_content_shape_prose() -> None:
    text = (
        "This document describes the history of the account in plain prose. "
        "It contains several complete sentences that read naturally.\n"
        "Another sentence follows here, continuing the narrative structure.\n"
        "A third sentence wraps up this short paragraph nicely."
    )
    profile = build_document_profile(text_content=text)
    assert profile.content_shape == ContentShape.PROSE


def test_content_shape_table_or_form() -> None:
    text = "\n".join(
        [
            "Patient Name: John Smith",
            "Account Number: 123456",
            "Date of Service: 01/02/2024",
            "Total Billed:      $100.00",
            "Total Allowed:      $80.00",
        ]
    )
    profile = build_document_profile(text_content=text)
    assert profile.content_shape == ContentShape.TABLE_OR_FORM


def test_content_shape_unknown_for_empty_text() -> None:
    profile = build_document_profile(text_content="   ")
    assert profile.content_shape == ContentShape.UNKNOWN


def test_no_input_at_all_yields_empty_profile() -> None:
    profile = build_document_profile()
    assert profile.page_count == 0
    assert profile.pages == []
    assert profile.has_pdf_geometry is False
