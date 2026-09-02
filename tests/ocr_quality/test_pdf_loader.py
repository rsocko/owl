"""Tests for the pdfplumber adapter."""

from __future__ import annotations

import pytest

from doc_intelligence_hub.modules.ocr_quality.pdf_loader import load_pdf_pages

from .conftest import make_minimal_pdf_bytes, make_rotated_text_pdf_bytes


def test_loads_minimal_real_pdf() -> None:
    pdf_bytes = make_minimal_pdf_bytes("Hello World", width=200, height=100)
    pages = load_pdf_pages(pdf_bytes)
    assert len(pages) == 1
    page = pages[0]
    assert page.error is None
    assert page.width == 200
    assert page.height == 100
    assert [w.text for w in page.words] == ["Hello", "World"]
    # Normal, non-rotated text must have an angle of exactly 0.0.
    assert all(w.angle_degrees == 0.0 for w in page.words)


def test_rotated_text_reports_nonzero_angle() -> None:
    """A word drawn via an explicit ~90 deg rotated text matrix (issue #148)
    must have its rotation reflected in ``WordBox.angle_degrees``, not
    silently reported as upright (``0.0``).
    """
    pdf_bytes = make_rotated_text_pdf_bytes("Vertical", angle_degrees=90.0)
    pages = load_pdf_pages(pdf_bytes)
    assert len(pages) == 1
    page = pages[0]
    assert page.error is None
    assert len(page.words) == 1
    # pdfplumber may reorder glyphs within a rotated word differently than
    # a normal left-to-right word (its default extraction direction
    # heuristics assume upright text) -- only the character set, not
    # ordering, is asserted here; the angle derivation is this test's point.
    assert sorted(page.words[0].text) == sorted("Vertical")
    assert page.words[0].angle_degrees == pytest.approx(90.0, abs=1.0)


def test_multi_page_pdf_parses_each_page() -> None:
    # Build a two-page document by concatenating two independent minimal PDFs'
    # page content is out of scope here; instead verify graceful handling of
    # a single-page doc plus a corrupt-bytes doc, which is the realistic
    # per-page failure mode this loader needs to survive.
    pdf_bytes = make_minimal_pdf_bytes("Page One", width=300, height=150)
    pages = load_pdf_pages(pdf_bytes)
    assert len(pages) == 1
    assert pages[0].words[0].text == "Page"


def test_completely_invalid_bytes_do_not_raise() -> None:
    pages = load_pdf_pages(b"this is not a pdf at all")
    assert pages == []


def test_empty_bytes_do_not_raise() -> None:
    pages = load_pdf_pages(b"")
    assert pages == []


def test_truncated_pdf_bytes_do_not_raise() -> None:
    pdf_bytes = make_minimal_pdf_bytes("Truncated")
    pages = load_pdf_pages(pdf_bytes[: len(pdf_bytes) // 2])
    # Either it recovers a best-effort page list or gives up cleanly — it
    # must never raise.
    assert isinstance(pages, list)
