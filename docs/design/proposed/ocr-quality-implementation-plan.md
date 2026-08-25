---
title: "OCR Quality — Implementation Plan"
sidebar_label: OCR Implementation Plan
sidebar_position: 4
status: proposed
created: 2026-07-27
revised: 2026-08-24
---

# OCR Quality Pipeline — Implementation Plan

## Purpose

Implement the revised OCR quality design as an OWL-first, calibration-first
workflow. The plan separates safe corpus assessment from candidate generation
and separates candidate generation from changing Paperless.

## Current state

- OWL has six revised OCR design documents.
- Action Queue records a basic legacy `text_quality_score`.
- There is no dedicated OCR quality module or review UI.
- Paperless remains the source of document metadata, original files, current
  searchable versions, and extracted content.
- Current Paperless capabilities include document file versions and Azure remote
  OCR support, but the deployed version and exact version-application API must
  be verified before OWL enables acceptance.

## Product decisions

1. OWL is the primary OCR Quality UI.
2. No custom URL field is added to Paperless.
3. The exact file originally ingested by Paperless remains immutable.
4. Accepted candidates become the latest Paperless document version while a
   prior usable version is preserved.
5. Quality has separate overlay/readability and machine-extraction dimensions.
6. Full-corpus scoring is non-mutating.
7. Candidate generation is user-triggered or limited to a small explicit batch.
8. Initial acceptance is always user-confirmed.
9. Tesseract and Azure produce independent candidates; outputs are never merged.
10. LLM secondary review is deferred and advisory only.
11. n8n is optional integration glue; OWL owns run and candidate state.

Third-party OCR-rescue or classification tools do not replace the need for a
corpus baseline. They may be evaluated later as candidate providers only if
they satisfy the same searchable-PDF, staging, provenance, version, and rollback
contract.

## Phase 0: Deployment and data check

Before implementation:

- identify the deployed Paperless version and enabled archive/version behavior;
- verify API access to document content, current archive, originals, and version
  operations;
- measure how many documents already have Action Queue quality data;
- measure downstream unreadable/low-confidence outcomes; and
- confirm the trusted processing boundary and Azure policy.

This phase blocks candidate application, not read-only inventory work.

## Phase 1: Full-corpus inventory — issue #25

Build a resumable CLI/service operation that:

- scans a configured Paperless scope without mutation;
- stores per-document OWL-local assessments;
- produces privacy-safe aggregate reports;
- records reproducible inputs and scorer version; and
- supports the 8,000+ document corpus with progress and failure reporting.

Start with text and metadata. Download PDFs for a deterministic stratified
sample before deciding whether full-corpus overlay analysis is justified.

**Exit gate:** Corpus distribution and representative calibration sample exist.

## Phase 2: Multidimensional scoring — issue #29

Implement:

- page-aware document profiling;
- overlay/readability score;
- machine-extraction score;
- explainable reasons and unavailable-signal markers;
- downstream outcome evidence;
- scorer/configuration versioning; and
- boundary and malformed-input tests.

Calibrate thresholds against explicit human labels. Do not preserve the old A–F
formula as the quality contract.

**Exit gate:** False-positive and false-negative behavior is understood by
document profile.

## Phase 3: OWL Quality Review UI — issue #115

Add:

- corpus distribution and review queue;
- metadata filters and Paperless deep links;
- document assessment details;
- synchronized current/candidate PDF views;
- text and changed-region comparison;
- score explanations and downstream extraction differences; and
- accept, reject, retry, and rollback controls gated by permissions.

At first, the UI can review current assessments before candidate generation is
available.

**Exit gate:** Users can inspect and calibrate current-document quality entirely
inside OWL.

## Phase 4: Candidate generation — issue #18

Implement provider-neutral candidate storage and two providers:

1. OCRmyPDF/Tesseract searchable PDF.
2. Azure Document Intelligence `prebuilt-read` searchable PDF.

Add document-level generation first, followed by capped explicit batches.
Record cost, runtime, engine versions, settings, geometry/confidence evidence,
and candidate checksums.

Run the 50–100 document engine bake-off before enabling general use.

**Exit gate:** Candidates can be generated and compared without changing
Paperless.

## Phase 5: Paperless version application and rollback — issues #18 and #23

Verify and implement the deployed Paperless version workflow:

- preserve the current usable version;
- make an approved candidate the latest version;
- align Paperless extracted content and search with it;
- detect stale comparisons;
- verify normal Paperless preview, copy, download, and API behavior; and
- roll back to the prior version.

Application remains disabled if these guarantees cannot be integration-tested.

**Exit gate:** An accepted candidate and rollback both work without duplicate
documents or metadata drift.

## Phase 6: Downstream invalidation — issue #114

Carry forward the prior ideation analysis-versioning work:

- persist exact Paperless version and accepted content/archive checksum;
- fingerprint module/extractor and validated configuration versions;
- mark prior TYRION, EOB, Action Queue, statement, correspondent, and Insights
  outputs stale without deleting their audit history;
- reprocess affected modules idempotently; and
- repeat invalidation on rollback.

Version application is not reported coordinately complete until the invalidation
record is durable. Downstream modules may complete asynchronously and expose
partial failure for retry.

**Exit gate:** Accepted version changes and rollback cannot leave apparently
current downstream results based on older OCR.

## Phase 7: Shared orchestration — issue #30

Add idempotent scheduled, event-driven, and manual entry points around the same
OWL run model.

- Manual inventory, assessment, and candidate runs are first.
- New-document and scheduled operations perform assessment only.
- Small batches generate candidates but never bulk-accept them.
- n8n may invoke endpoints or route safe notifications.

**Exit gate:** Retries, concurrent triggers, cancellation, state synchronization,
and alerts are exercised.

## Phase 8: Optional secondary review — issue #17

Only after calibration, evaluate whether a provider-neutral advisory reviewer
improves handling of uncertain documents. It must be measured against held-out
human labels and cannot accept candidates.

## Release integration — issue #23

Release evidence includes:

- scorer and provider versions;
- calibration and engine bake-off results;
- privacy review;
- Paperless version/rollback verification;
- end-to-end failure isolation;
- UI and aggregate dashboard verification;
- known limitations; and
- rollback readiness.

## Issue map

| Scope | Issue |
|---|---|
| Baseline inventory | [#25](https://github.com/rsocko/owl/issues/25) |
| Quality scoring | [#29](https://github.com/rsocko/owl/issues/29) |
| Optional secondary review | [#17](https://github.com/rsocko/owl/issues/17) |
| Candidate generation, application, rollback | [#18](https://github.com/rsocko/owl/issues/18) |
| Downstream analysis invalidation | [#114](https://github.com/rsocko/owl/issues/114) |
| Orchestration | [#30](https://github.com/rsocko/owl/issues/30) |
| OWL review and comparison UI | [#115](https://github.com/rsocko/owl/issues/115) |
| Integration and release | [#23](https://github.com/rsocko/owl/issues/23) |

## References

- [OCR Quality Design](../../modules/ocr-quality/ocr-quality-design.md)
- [Baseline Inventory](../../modules/ocr-quality/ocr-baseline-inventory.md)
- [Quality Scoring](../../modules/ocr-quality/ocr-quality-scoring.md)
- [Candidate and Version Engine](../../modules/ocr-quality/ocr-remediation-engine.md)
- [Secondary Review](../../modules/ocr-quality/ocr-ollama-integration.md)
- [Orchestration](../../modules/ocr-quality/ocr-n8n-workflow.md)
