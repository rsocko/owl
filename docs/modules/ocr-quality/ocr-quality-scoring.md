---
title: "OCR Quality Scoring"
sidebar_label: OCR Scoring
sidebar_position: 3
---

# OCR Quality Scoring

**Status:** Calibration-first specification
**Revised:** 2026-08-24

## Purpose

The scorer identifies documents likely to benefit from review. It does not
declare OCR correct without ground truth and it does not authorize replacement.

The existing Action Queue `text_quality_score` remains useful seed data, but its
content-length, alphabetic-ratio, and average-word-length heuristic is not the
OCR quality contract.

## Outputs

Each assessment returns:

```json
{
  "overlay_score": 0,
  "machine_score": 0,
  "review_status": "GOOD | UNCERTAIN | REVIEW_RECOMMENDED | FAILED",
  "reasons": [],
  "document_profile": {},
  "scorer_version": "string",
  "assessed_at": "ISO-8601 timestamp"
}
```

The two numeric scores are 0–100 quality indicators. Thresholds and any display
labels are configuration associated with a scorer version. They must be
calibrated against the actual corpus before being treated as stable.

## Document profiling

Classification is page-aware:

- digital text page;
- scanned image with text overlay;
- image page without text;
- mixed page; or
- unsupported/error.

The document profile also records page count, text and image coverage, language
or script hints, producer metadata, and broad content shape such as prose,
table/form, code-heavy, or mixed.

Digital pages are normally ineligible for image re-OCR, but their extracted text
is still assessed. Short documents are not automatically failures merely
because they contain fewer than a fixed number of characters.

## Overlay/readability score

Overlay quality requires PDF bytes and page geometry. Candidate and current
archive assessment considers:

- searchable/selectable text presence by page;
- text coverage relative to visible content;
- word and line boxes within page bounds;
- duplicate or overlapping invisible text;
- text/image alignment;
- reading-order consistency;
- missing, extra, or reordered pages; and
- copy/paste sanity on representative regions.

Engines without geometry can still produce a candidate, but the overlay score
must identify unavailable signals rather than inventing a complete score.

## Machine-extraction score

Machine quality combines explainable signals:

- plausible character and script distribution;
- whitespace, line-break, hyphenation, and token-boundary quality;
- repeated noise and implausible token shapes;
- language coherence for prose regions;
- preservation of acronyms, identifiers, numbers, dates, currency, and codes;
- table and label/value association where detectable;
- engine confidence summaries for candidates; and
- success, completeness, and internal consistency of relevant OWL extractors.

Dictionary frequency may be one low-weight prose signal. It must not penalize
valid medical terminology, names, acronyms, account identifiers, or document
codes as if they were OCR garbage.

## Downstream evidence

The scorer may consume privacy-safe outcomes from:

- TYRION account-signal extraction;
- EOB and bill field extraction;
- Action Queue unreadable or low-confidence dispositions;
- statement and correspondent extraction; and
- OWL Insights rules that depend on document text.

A downstream failure raises review risk. A downstream success does not prove
the whole document is correct.

## Current-document assessment

Assessment of the current Paperless version is non-mutating and may use:

1. Paperless `document.content` for fast corpus-wide text signals;
2. current archive PDF bytes for overlay and page-profile signals; and
3. existing downstream outcomes.

The baseline may assess text for every document and download PDFs only for a
stratified calibration sample before deciding whether full-corpus PDF analysis
is worthwhile.

## Candidate comparison

Candidate comparison is paired, page-aware, and engine-neutral. It reports:

- old and new component scores;
- page and text coverage changes;
- changed text regions;
- confidence and geometry evidence;
- downstream extraction changes;
- blocking regressions; and
- a review recommendation with machine-readable reasons.

No fixed score delta automatically accepts a candidate. During the initial
release, the user makes the decision after all blocking checks pass.

## Calibration

The initial 8,000+ document inventory must:

1. compute fast text signals for the full corpus;
2. stratify documents by source, age, type, correspondent, text shape, and
   preliminary score;
3. manually label a representative sample for readability and machine utility;
4. run engine candidates on a smaller bake-off set;
5. evaluate false positives and false negatives by document profile; and
6. freeze thresholds with a scorer version.

Recommended starting sizes are 200–400 stratified documents for inspection,
75–150 explicit human labels, and 50–100 documents in the engine bake-off.
Actual sizes may change based on distribution.

## Persistence

Store assessments and component reasons in OWL-local state. Records are keyed by
Paperless document/version identity and source checksum so stale results cannot
be applied to a changed document.

At minimum persist:

- document and version identifiers;
- source checksum and document profile;
- overlay and machine scores;
- component signals and unavailable-signal markers;
- review status and reasons;
- scorer/configuration version; and
- assessment time.

Detailed OCR text, snippets, and coordinates must not be copied into logs,
GitHub issues, notifications, or external integrations.

## API shape

```text
GET  /api/ocr/assessments
GET  /api/ocr/assessments/{document_id}
POST /api/ocr/assessments/{document_id}
POST /api/ocr/inventory
GET  /api/ocr/inventory/{run_id}
POST /api/ocr/comparisons
GET  /api/ocr/comparisons/{comparison_id}
```

Inventory and assessment operations expose asynchronous run state, progress,
safe aggregate metrics, and per-document results only to authorized OWL users.
