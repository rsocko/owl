"""Tests for candidate comparison logic (issue #18, slice 1).

Comparison is pure and side-effect free — no DB, no Paperless, no disk I/O —
so these tests exercise :func:`compare_candidate` directly with hand-built
PDF fixtures.
"""

from __future__ import annotations

import io

from doc_intelligence_hub.modules.ocr_quality.candidate_models import ComparisonBlockingFinding
from doc_intelligence_hub.modules.ocr_quality.comparison import compare_candidate

from .conftest import make_minimal_pdf_bytes


def _make_multipage_pdf(page_texts: list[str], width: int = 300, height: int = 200) -> bytes:
    """Build a real multi-page PDF with reportlab, one distinct text-showing line per page."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    for text in page_texts:
        c.drawString(10, height / 2, text)
        c.showPage()
    c.save()
    return buf.getvalue()


class TestCompareCandidate:
    def test_improvement_no_blocking_findings(self):
        current = make_minimal_pdf_bytes("Hello Wrold", width=300, height=200)
        candidate = make_minimal_pdf_bytes("Hello World", width=300, height=200)

        result = compare_candidate(
            current_pdf_bytes=current,
            current_text="Hello Wrold",
            current_overlay_score=60.0,
            current_machine_score=65.0,
            candidate_pdf_bytes=candidate,
            candidate_text="Hello World",
            candidate_overlay_score=90.0,
            candidate_machine_score=92.0,
        )

        assert result.blocking_findings == []
        assert result.overlay_score_delta == 30.0
        assert result.machine_score_delta == 27.0
        assert result.page_count_current == 1
        assert result.page_count_candidate == 1
        # A higher score is evidence, never authorization: comparison never
        # sets any "safe to accept" flag anywhere in the result.
        assert not hasattr(result, "safe_to_accept")

    def test_not_searchable_pdf_when_candidate_missing(self):
        current = make_minimal_pdf_bytes("Hello World")

        result = compare_candidate(
            current_pdf_bytes=current,
            current_text="Hello World",
            current_overlay_score=60.0,
            current_machine_score=65.0,
            candidate_pdf_bytes=None,
            candidate_text=None,
            candidate_overlay_score=None,
            candidate_machine_score=None,
        )

        assert ComparisonBlockingFinding.NOT_SEARCHABLE_PDF in result.blocking_findings

    def test_not_searchable_pdf_when_candidate_has_no_text_layer(self):
        current = make_minimal_pdf_bytes("Hello World")
        # A valid PDF with no BT/Tj text-showing operator at all.
        candidate = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF"

        result = compare_candidate(
            current_pdf_bytes=current,
            current_text="Hello World",
            current_overlay_score=60.0,
            current_machine_score=65.0,
            candidate_pdf_bytes=candidate,
            candidate_text="",
            candidate_overlay_score=None,
            candidate_machine_score=None,
        )

        assert ComparisonBlockingFinding.NOT_SEARCHABLE_PDF in result.blocking_findings

    def test_machine_regression_flagged(self):
        current = make_minimal_pdf_bytes("Hello World")
        candidate = make_minimal_pdf_bytes("Hxllx Wxrld")

        result = compare_candidate(
            current_pdf_bytes=current,
            current_text="Hello World",
            current_overlay_score=80.0,
            current_machine_score=90.0,
            candidate_pdf_bytes=candidate,
            candidate_text="Hxllx Wxrld",
            candidate_overlay_score=40.0,
            candidate_machine_score=50.0,
        )

        assert ComparisonBlockingFinding.MACHINE_REGRESSION in result.blocking_findings
        assert result.machine_score_delta == -40.0

    def test_small_score_drop_is_not_a_regression(self):
        current = make_minimal_pdf_bytes("Hello World")
        candidate = make_minimal_pdf_bytes("Hello World")

        result = compare_candidate(
            current_pdf_bytes=current,
            current_text="Hello World",
            current_overlay_score=80.0,
            current_machine_score=90.0,
            candidate_pdf_bytes=candidate,
            candidate_text="Hello World",
            candidate_overlay_score=78.0,
            candidate_machine_score=87.0,
        )

        assert ComparisonBlockingFinding.MACHINE_REGRESSION not in result.blocking_findings

    def test_missing_pages_flagged_via_expected_page_count(self):
        current = make_minimal_pdf_bytes("Hello World")
        candidate = make_minimal_pdf_bytes("Hello World")

        result = compare_candidate(
            current_pdf_bytes=current,
            current_text="Hello World",
            current_overlay_score=None,
            current_machine_score=None,
            candidate_pdf_bytes=candidate,
            candidate_text="Hello World",
            candidate_overlay_score=None,
            candidate_machine_score=None,
            expected_page_count=3,
        )

        assert ComparisonBlockingFinding.PAGES_MISSING in result.blocking_findings

    def test_never_raises_on_garbage_input(self):
        result = compare_candidate(
            current_pdf_bytes=b"not a pdf at all",
            current_text=None,
            current_overlay_score=None,
            current_machine_score=None,
            candidate_pdf_bytes=b"also not a pdf",
            candidate_text=None,
            candidate_overlay_score=None,
            candidate_machine_score=None,
        )

        # Comparison never raises; either it degrades gracefully or reports
        # UNKNOWN_ERROR, but a candidate object is always returned.
        assert result is not None
        assert isinstance(result.blocking_findings, list)

    def test_stale_source_detection_via_checksum(self):
        """Comparison itself records the checksums it was given; staleness is
        actually enforced by candidate_service.decide_candidate re-checking
        the live document, but the checksums recorded here are what that
        staleness check compares against.
        """
        current = make_minimal_pdf_bytes("Hello World")
        candidate = make_minimal_pdf_bytes("Hxllx Wxrld")

        result = compare_candidate(
            current_pdf_bytes=current,
            current_text="Hello World",
            current_overlay_score=None,
            current_machine_score=None,
            candidate_pdf_bytes=candidate,
            candidate_text="Hxllx Wxrld",
            candidate_overlay_score=None,
            candidate_machine_score=None,
        )

        assert result.source_checksum
        assert result.candidate_checksum
        assert result.source_checksum != result.candidate_checksum

    def test_reordered_pages_flagged(self):
        current = _make_multipage_pdf(["Xyzzy quft wobble zark plonk vex", "Corn dunk pib rill snarq humt"])
        # Candidate has the same two pages' content, but swapped order.
        candidate = _make_multipage_pdf(
            ["Corn dunk pib rill snarq humt", "Xyzzy quft wobble zark plonk vex"]
        )

        result = compare_candidate(
            current_pdf_bytes=current,
            current_text=None,
            current_overlay_score=None,
            current_machine_score=None,
            candidate_pdf_bytes=candidate,
            candidate_text=None,
            candidate_overlay_score=None,
            candidate_machine_score=None,
        )

        assert ComparisonBlockingFinding.PAGES_REORDERED in result.blocking_findings
        assert result.page_count_current == 2
        assert result.page_count_candidate == 2

    def test_matching_page_order_not_flagged(self):
        current = _make_multipage_pdf(["Xyzzy quft wobble zark plonk vex", "Corn dunk pib rill snarq humt"])
        candidate = _make_multipage_pdf(
            ["Xyzzy quft wobble zark plonk vex", "Corn dunk pib rill snarq humt"]
        )

        result = compare_candidate(
            current_pdf_bytes=current,
            current_text=None,
            current_overlay_score=None,
            current_machine_score=None,
            candidate_pdf_bytes=candidate,
            candidate_text=None,
            candidate_overlay_score=None,
            candidate_machine_score=None,
        )

        assert ComparisonBlockingFinding.PAGES_REORDERED not in result.blocking_findings

    def test_missing_page_count_mismatch_flagged_without_expected_count(self):
        current = _make_multipage_pdf(["Xyzzy quft wobble zark plonk vex", "Corn dunk pib rill snarq humt"])
        candidate = _make_multipage_pdf(["Xyzzy quft wobble zark plonk vex"])

        result = compare_candidate(
            current_pdf_bytes=current,
            current_text=None,
            current_overlay_score=None,
            current_machine_score=None,
            candidate_pdf_bytes=candidate,
            candidate_text=None,
            candidate_overlay_score=None,
            candidate_machine_score=None,
        )

        assert ComparisonBlockingFinding.PAGES_MISSING in result.blocking_findings
        assert result.page_count_current == 2
        assert result.page_count_candidate == 1
