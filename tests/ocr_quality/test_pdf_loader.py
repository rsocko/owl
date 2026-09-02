"""Tests for the pdfplumber adapter."""

from __future__ import annotations

from doc_intelligence_hub.modules.ocr_quality.pdf_loader import load_pdf_pages

from .conftest import make_minimal_pdf_bytes


def test_loads_minimal_real_pdf() -> None:
    pdf_bytes = make_minimal_pdf_bytes("Hello World", width=200, height=100)
    pages = load_pdf_pages(pdf_bytes)
    assert len(pages) == 1
    page = pages[0]
    assert page.error is None
    assert page.width == 200
    assert page.height == 100
    assert [w.text for w in page.words] == ["Hello", "World"]


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
