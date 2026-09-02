"""Tests for the per-word region-inspection flag heuristics (issue #134)."""

from __future__ import annotations

from doc_intelligence_hub.modules.ocr_quality.region_inspection import (
    build_page_regions_from_pages,
)

from .conftest import (
    make_digital_page,
    make_digital_page_with_small_image,
    make_scanned_overlay_page,
)


def _flag_reasons_by_text(payload: dict) -> dict[str, list[str]]:
    return {w["text"]: w["flag_reasons"] for w in payload["words"]}


def test_digital_page_with_small_incidental_image_has_no_alignment_flags() -> None:
    """A digital lease with a small logo (e.g. doc 8061) should not be flagged.

    Regression test for the false-positive bug: previously any embedded
    image at all — however small — caused every word far from it to be
    flagged "alignment", producing an all-red heatmap on an otherwise clean
    digital document.
    """
    page = make_digital_page_with_small_image()
    payload = build_page_regions_from_pages([page], page_number=1)
    assert payload is not None
    for word in payload["words"]:
        assert "alignment" not in word["flag_reasons"]
        assert word["flagged"] is False


def test_clean_digital_page_without_images_has_no_alignment_flags() -> None:
    page = make_digital_page()
    payload = build_page_regions_from_pages([page], page_number=1)
    assert payload is not None
    for word in payload["words"]:
        assert "alignment" not in word["flag_reasons"]


def test_misaligned_scanned_overlay_page_still_flags_alignment() -> None:
    """True-positive case from #134 must still work: a real scanned page

    (image covering the whole page) with OCR text that doesn't line up with
    it should still have every word flagged "alignment".
    """
    page = make_scanned_overlay_page(misaligned=True)
    payload = build_page_regions_from_pages([page], page_number=1)
    assert payload is not None
    assert payload["words"], "expected words on the synthetic misaligned page"
    for word in payload["words"]:
        assert "alignment" in word["flag_reasons"]
        assert word["flagged"] is True


def test_well_aligned_scanned_overlay_page_has_no_alignment_flags() -> None:
    page = make_scanned_overlay_page(misaligned=False)
    payload = build_page_regions_from_pages([page], page_number=1)
    assert payload is not None
    assert payload["words"], "expected words on the synthetic aligned page"
    for word in payload["words"]:
        assert "alignment" not in word["flag_reasons"]
