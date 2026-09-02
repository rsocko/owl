"""OCR quality baseline inventory (issue #25).

Non-mutating, resumable inventory of OCR/text quality across the Paperless
corpus. Stage 1 computes fast text/metadata signals for every accessible
document. Stage 2 selects a deterministic stratified sample and profiles the
sampled PDFs page-by-page (digital / scanned-with-overlay / no-text / mixed).

This module intentionally does not implement:
- the full multidimensional quality scorer (issue #29), or
- human calibration labeling (issue #29), or
- the Tesseract/Azure candidate engine bake-off (issue #18).

It stores per-document OWL-local assessments and produces privacy-safe
aggregate reports so #29 can later attach real scoring to the same
``document_id`` / ``document_version_key`` / run records.
"""
