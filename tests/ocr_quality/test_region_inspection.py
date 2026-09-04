"""Tests for the per-word region-inspection flag heuristics (issue #134)."""

from __future__ import annotations

from doc_intelligence_hub.modules.ocr_quality.pdf_types import PdfPageData
from doc_intelligence_hub.modules.ocr_quality.region_inspection import (
    build_page_regions_from_pages,
)

from .conftest import (
    make_digital_page,
    make_digital_page_with_small_image,
    make_scanned_overlay_page,
    make_word,
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


def test_word_angle_surfaced_in_payload() -> None:
    """A word's rotation angle (issue #148) must be surfaced in the
    region-inspection payload so the frontend can draw a rotated box for
    it, while an ordinary upright word reports exactly 0.0.
    """
    upright = make_word("Normal", 10.0, 50.0, 60.0, 62.0, order=0, angle=0.0)
    rotated = make_word("Vertical", 100.0, 50.0, 112.0, 150.0, order=1, angle=90.0)
    page = PdfPageData(
        page_number=1, width=600.0, height=800.0, words=[upright, rotated], char_count=20
    )
    payload = build_page_regions_from_pages([page], page_number=1)
    assert payload is not None
    by_text = {w["text"]: w["angle"] for w in payload["words"]}
    assert by_text["Normal"] == 0.0
    assert by_text["Vertical"] == 90.0


# --- content_plausibility (docs #2346/#5483: PDF glyph-ID corruption) -------


def test_cid_glyph_artifact_word_is_flagged_content_plausibility() -> None:
    clean = make_word("Normal", 10.0, 50.0, 60.0, 62.0, order=0)
    corrupted = make_word("(cid:68)(cid:97)(cid:98)", 70.0, 50.0, 160.0, 62.0, order=1)
    page = PdfPageData(
        page_number=1, width=600.0, height=800.0, words=[clean, corrupted], char_count=20
    )
    payload = build_page_regions_from_pages([page], page_number=1)
    assert payload is not None
    by_text = _flag_reasons_by_text(payload)
    assert "content_plausibility" not in by_text["Normal"]
    assert "content_plausibility" in by_text["(cid:68)(cid:97)(cid:98)"]


def test_replacement_char_word_is_flagged_content_plausibility() -> None:
    word = make_word("bro\ufffden", 10.0, 50.0, 60.0, 62.0, order=0)
    page = PdfPageData(page_number=1, width=600.0, height=800.0, words=[word], char_count=10)
    payload = build_page_regions_from_pages([page], page_number=1)
    assert payload is not None
    assert payload["words"][0]["flagged"] is True
    assert "content_plausibility" in payload["words"][0]["flag_reasons"]


def test_low_confidence_word_is_flagged_content_plausibility() -> None:
    low_conf = make_word("maybe", 10.0, 50.0, 60.0, 62.0, order=0, confidence=0.2)
    high_conf = make_word("clear", 70.0, 50.0, 120.0, 62.0, order=1, confidence=0.95)
    page = PdfPageData(
        page_number=1, width=600.0, height=800.0, words=[low_conf, high_conf], char_count=10
    )
    payload = build_page_regions_from_pages([page], page_number=1)
    assert payload is not None
    by_text = _flag_reasons_by_text(payload)
    assert "content_plausibility" in by_text["maybe"]
    assert "content_plausibility" not in by_text["clear"]


def test_digital_page_without_confidence_data_is_not_flagged() -> None:
    """``confidence=None`` (ordinary digital-PDF extraction) must never trip
    the low-confidence check — only providers that report a real value do.
    """
    page = make_digital_page()
    payload = build_page_regions_from_pages([page], page_number=1)
    assert payload is not None
    for word in payload["words"]:
        assert "content_plausibility" not in word["flag_reasons"]


def test_content_plausibility_flag_cross_references_machine_reasons() -> None:
    """The document-level ``machine.cid_glyph_artifacts`` reason (issue:
    docs #2346/#5483) must surface next to the specific word that carries
    the corruption, not just the geometry-based ``overlay.*`` reasons.
    """
    corrupted = make_word("(cid:68)(cid:97)", 10.0, 50.0, 90.0, 62.0, order=0)
    page = PdfPageData(page_number=1, width=600.0, height=800.0, words=[corrupted], char_count=10)
    document_reasons = [
        {
            "code": "machine.cid_glyph_artifacts",
            "message": "Extracted text is dominated by (cid:N) placeholders.",
            "severity": "blocking",
        },
        {"code": "overlay.page_coverage", "message": "unrelated", "severity": "warning"},
    ]
    payload = build_page_regions_from_pages(
        [page], page_number=1, document_reasons=document_reasons
    )
    assert payload is not None
    matched_codes = {r["code"] for r in payload["words"][0]["matched_reasons"]}
    assert matched_codes == {"machine.cid_glyph_artifacts"}
