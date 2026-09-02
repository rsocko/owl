"""Tests for overlay/readability scoring."""

from __future__ import annotations

from doc_intelligence_hub.modules.ocr_quality.overlay_scoring import score_overlay
from doc_intelligence_hub.modules.ocr_quality.pdf_types import PdfPageData, WordBox
from doc_intelligence_hub.modules.ocr_quality.profiling import build_document_profile
from doc_intelligence_hub.modules.ocr_quality.scoring_config import DEFAULT_CONFIG
from doc_intelligence_hub.modules.ocr_quality.scoring_models import Severity

from .conftest import make_digital_page, make_error_page, make_scanned_overlay_page


def test_no_geometry_means_unavailable_overlay_score() -> None:
    profile = build_document_profile()
    result = score_overlay(pdf_pages=None, profile=profile, config=DEFAULT_CONFIG)
    assert result.score is None
    assert set(result.unavailable_signals) == {
        "searchable_text",
        "page_coverage",
        "bounds_sanity",
        "duplicate_overlap",
        "alignment",
        "reading_order",
        "page_integrity",
    }


def test_clean_digital_pages_score_well() -> None:
    pages = [make_digital_page(1), make_digital_page(2)]
    profile = build_document_profile(pdf_pages=pages)
    result = score_overlay(pdf_pages=pages, profile=profile, config=DEFAULT_CONFIG)
    assert result.score is not None
    assert result.score > 60.0
    assert result.signals["searchable_text"] == 1.0


def test_well_aligned_scanned_overlay_scores_higher_than_misaligned() -> None:
    good_page = make_scanned_overlay_page(1, misaligned=False)
    bad_page = make_scanned_overlay_page(1, misaligned=True)
    profile = build_document_profile(pdf_pages=[good_page])

    good_result = score_overlay(pdf_pages=[good_page], profile=profile, config=DEFAULT_CONFIG)
    bad_result = score_overlay(pdf_pages=[bad_page], profile=profile, config=DEFAULT_CONFIG)

    assert good_result.signals["alignment"] == 1.0
    assert bad_result.signals["alignment"] == 0.0
    assert good_result.score > bad_result.score


def test_duplicate_overlapping_text_lowers_signal() -> None:
    page = make_digital_page(1)
    duplicated_words = list(page.words) + list(page.words)  # exact duplicate overlay
    dup_page = PdfPageData(
        page_number=1, width=page.width, height=page.height, words=duplicated_words
    )
    profile = build_document_profile(pdf_pages=[dup_page])
    result = score_overlay(pdf_pages=[dup_page], profile=profile, config=DEFAULT_CONFIG)
    assert result.signals["duplicate_overlap"] == 0.5
    assert any(r.code == "overlay.duplicate_overlap" for r in result.reasons)


def test_out_of_bounds_words_lower_bounds_sanity() -> None:
    page = make_digital_page(1, width=100.0, height=100.0)
    out_of_bounds_word = WordBox(
        text="Overflow", x0=90.0, top=90.0, x1=250.0, bottom=250.0, order_index=999
    )
    page.words.append(out_of_bounds_word)
    profile = build_document_profile(pdf_pages=[page])
    result = score_overlay(pdf_pages=[page], profile=profile, config=DEFAULT_CONFIG)
    assert result.signals["bounds_sanity"] is not None
    assert result.signals["bounds_sanity"] < 1.0


def test_shuffled_reading_order_lowers_signal() -> None:
    ordered_page = make_digital_page(1)
    shuffled_words = list(ordered_page.words)
    # Reverse the native order index while keeping the same visual layout.
    n = len(shuffled_words)
    shuffled = [
        WordBox(
            text=w.text,
            x0=w.x0,
            top=w.top,
            x1=w.x1,
            bottom=w.bottom,
            order_index=n - 1 - i,
        )
        for i, w in enumerate(shuffled_words)
    ]
    shuffled_page = PdfPageData(
        page_number=1, width=ordered_page.width, height=ordered_page.height, words=shuffled
    )
    profile = build_document_profile(pdf_pages=[shuffled_page])

    ordered_result = score_overlay(pdf_pages=[ordered_page], profile=profile, config=DEFAULT_CONFIG)
    shuffled_result = score_overlay(
        pdf_pages=[shuffled_page], profile=profile, config=DEFAULT_CONFIG
    )

    assert ordered_result.signals["reading_order"] == 1.0
    assert shuffled_result.signals["reading_order"] < ordered_result.signals["reading_order"]


def test_missing_pages_relative_to_expected_count_is_blocking() -> None:
    pages = [make_digital_page(1)]
    profile = build_document_profile(pdf_pages=pages)
    result = score_overlay(
        pdf_pages=pages, profile=profile, config=DEFAULT_CONFIG, expected_page_count=5
    )
    assert result.signals["page_integrity"] < 1.0
    assert any(
        r.code == "overlay.page_integrity" and r.severity == Severity.BLOCKING
        for r in result.reasons
    )


def test_matching_expected_page_count_has_full_integrity() -> None:
    pages = [make_digital_page(1), make_digital_page(2)]
    profile = build_document_profile(pdf_pages=pages)
    result = score_overlay(
        pdf_pages=pages, profile=profile, config=DEFAULT_CONFIG, expected_page_count=2
    )
    assert result.signals["page_integrity"] == 1.0


def test_error_pages_reduce_page_integrity_but_do_not_crash() -> None:
    pages = [make_digital_page(1), make_error_page(2)]
    profile = build_document_profile(pdf_pages=pages)
    result = score_overlay(pdf_pages=pages, profile=profile, config=DEFAULT_CONFIG)
    assert result.signals["page_integrity"] == 0.5
    assert result.score is not None


def test_malformed_zero_area_page_does_not_crash() -> None:
    page = PdfPageData(page_number=1, width=0.0, height=0.0, words=[])
    profile = build_document_profile(pdf_pages=[page])
    result = score_overlay(pdf_pages=[page], profile=profile, config=DEFAULT_CONFIG)
    # No usable geometry signals, but scoring must not raise.
    assert result.signals["bounds_sanity"] is None
    assert result.signals["page_coverage"] is None
