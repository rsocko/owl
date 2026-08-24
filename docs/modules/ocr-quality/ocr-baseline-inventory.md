---
title: "OCR Baseline Inventory"
sidebar_label: OCR Inventory
sidebar_position: 1
---

# OCR Baseline Inventory

**Status:** Phase 0 execution specification
**Revised:** 2026-08-24

## Objective

Assess the existing 8,000+ document corpus before building automated
remediation. The inventory is non-mutating: it reads Paperless metadata,
extracted content, and selected PDFs, then stores OWL-local quality evidence.

It answers:

- how much of the corpus is digital, scanned, mixed, or missing text;
- which document profiles have the highest quality risk;
- whether poor OCR is associated with TYRION, EOB, Action Queue, statement, or
  Insights failures;
- which scoring signals agree with human judgment; and
- whether Tesseract or Azure candidates materially improve representative
  documents.

## Stage 1: Full-corpus text inventory

Fetch all accessible Paperless documents through pagination and compute fast
signals from `document.content` and metadata.

Persist per-document results locally so OWL can provide a review queue. Reports
leaving the trusted boundary contain aggregate counts only.

Collect:

- document/version identity and checksum where available;
- page count, MIME type, producer/creator hints, date, type, correspondent, and
  privacy-safe tag identifiers;
- content length and token-shape statistics;
- character/script, whitespace, repetition, and prose/code/table-shape signals;
- existing Action Queue text score as a separate legacy signal; and
- privacy-safe downstream success/failure outcomes.

If one document request per second is required, 8,000 requests take about 2
hours 15 minutes before processing overhead. If Paperless pagination includes
the required content, the run can be substantially faster. The implementation
must measure actual throughput rather than rely on the older 3,000-document
estimate.

## Stage 2: Page-profile sample

Download PDFs for a deterministic stratified sample first, not an arbitrary
every-Nth sample.

Stratify by:

- preliminary score range;
- document type and broad content shape;
- correspondent/source;
- age and scanner/producer hints;
- downstream extraction success/failure; and
- short, long, table-heavy, code-heavy, and mixed documents.

Use page-aware profiling to distinguish digital text, scanned images with an
overlay, no-text pages, and mixed pages.

## Stage 3: Human calibration

OWL presents a calibration set for two independent labels:

- overlay/readability quality; and
- machine-extraction usefulness.

Record reason codes such as missing text, bad alignment, incorrect reading
order, gibberish, broken table structure, number/code errors, or acceptable
domain terminology.

Start with 200–400 inspected documents and 75–150 explicit labels, adjusting
when strata remain underrepresented.

## Stage 4: Engine bake-off

On a smaller 50–100 document set, generate independent candidates with:

- OCRmyPDF/Tesseract; and
- Azure Document Intelligence `prebuilt-read`.

Include known-good controls, obvious failures, uncertain documents, tables,
receipts, EOBs, faded scans, handwriting, and identifier/code-heavy documents.

Compare:

- searchable-PDF overlay quality;
- machine score and critical token preservation;
- downstream extractor outcomes;
- runtime and operational failure rate;
- Azure pages and current configured cost; and
- human preference.

No candidate is applied during the inventory.

## Outputs

OWL-local outputs:

- reproducible inventory run record;
- per-document assessment and reasons;
- distribution by quality dimension and document profile;
- calibration labels;
- false-positive/false-negative analysis;
- engine bake-off results; and
- a proposed scorer configuration/version.

Safe exported summary:

- aggregate counts and percentages;
- signal and score distributions;
- document-profile distribution;
- downstream failure correlation;
- sample sizes and calibration agreement; and
- engine win/tie/failure rates without document content.

Do not export titles, OCR text, identifiers, patient data, or sensitive custom
field values.

## Reproducibility

Every run records:

- Paperless deployment/source identity;
- query/filter scope;
- scorer and configuration version;
- start/end time and item counts;
- failed/skipped counts with safe reason codes;
- deterministic sampling seed and strata;
- candidate provider/model versions; and
- source checksums sufficient to detect changed documents.

Runs are resumable and idempotent. Re-running does not duplicate assessment
records for the same document version and scorer version.

## Decision gate

The inventory authorizes building candidate generation and review only when it
shows a meaningful population of actionable OCR risk or measurable downstream
benefit. It never authorizes automatic replacement.
