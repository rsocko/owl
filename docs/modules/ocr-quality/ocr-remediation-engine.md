---
title: "OCR Candidate and Version Engine"
sidebar_label: OCR Candidates
sidebar_position: 5
---

# OCR Candidate Generation and Version Application

**Status:** Review-first specification
**Revised:** 2026-08-24

## Decision

The engine generates alternate OCR candidates. It does not automatically
replace low-scoring documents.

Candidate generation is allowed only for:

- a document explicitly selected by a user; or
- a small, explicitly selected batch with a configured document/page/cost cap.

Every candidate is staged outside the current Paperless version until reviewed.

## Safety invariants

1. The exact source file originally ingested by Paperless remains immutable.
2. One engine and configuration own each candidate PDF and text result.
3. Outputs from different engines are never merged into one text layer.
4. The current Paperless version is not changed while candidates run.
5. Acceptance requires a fresh comparison against the same source checksum.
6. The prior usable Paperless version is preserved before the candidate becomes
   latest.
7. Failure at any application step leaves the current version usable.
8. Automatic replacement is out of scope for the initial release.

## Providers

### OCRmyPDF and Tesseract 5

The local provider produces a searchable PDF, sidecar text, and available
geometry/confidence evidence. Configuration is explicit and versioned,
including language, deskew, cleanup, rotation, and OCR mode.

It is the default private, no-service-cost candidate. It is expected to perform
well on clean printed scans and less well on handwriting, degraded images,
complex tables, or patterned backgrounds.

### Azure Document Intelligence

Use the current `prebuilt-read` API and request searchable PDF output. Record the
API/model version, page count, word confidence, geometry, and billed usage.

Azure Layout may be invoked as a separate structured-extraction experiment. Its
Markdown or layout output must not be spliced into a `prebuilt-read` or
Tesseract PDF.

Where the deployed Paperless version supports Azure remote OCR, the
implementation should reuse that integration when it can preserve staging,
comparison, and version-history guarantees. Otherwise OWL may invoke Azure
directly for candidate generation. Provider choice must not weaken the safety
invariants.

### Optional external rescue providers

Third-party services such as Tagvico may be evaluated as additional candidate
providers. They must return or identify a staged searchable-PDF candidate with
provenance and must not mutate the current Paperless document directly. They
use the same OWL comparison, explicit acceptance, Paperless version, rollback,
and downstream-invalidation contract as Tesseract and Azure.

## Candidate state

```text
REQUESTED
  -> RUNNING
  -> READY
  -> ACCEPTED | REJECTED | EXPIRED | FAILED
```

`ACCEPTED` means the candidate passed blocking validation, the user approved it,
and Paperless confirmed the new latest version and extracted content.

Store:

- candidate ID and source document/version/checksum;
- engine, model/version, and settings;
- candidate PDF and text checksums;
- page count, runtime, cost, and provider operation ID;
- overlay and machine scores with scorer version;
- comparison ID and blocking findings;
- actor and decision; and
- retention/expiration timestamps.

## Review and comparison

OWL shows the current and candidate versions together. Comparison must verify:

- PDF validity and searchable text;
- matching page count unless an explained operation intentionally changes it;
- page identity/order;
- overlay alignment and coverage;
- changed text and low-confidence regions;
- machine-extraction changes; and
- downstream extractor regressions.

A higher text score is evidence, not authorization.

## Applying an accepted candidate

Application is a coordinated operation:

1. acquire a document-scoped lock;
2. re-read current Paperless identity and checksum;
3. fail stale if the document changed since comparison;
4. preserve the current usable artifact as a Paperless document version;
5. add or promote the candidate as the latest version;
6. ensure Paperless extracted content and search index correspond to it;
7. verify normal Paperless preview/download and OWL retrieval;
8. durably persist an analysis-invalidation record for downstream modules;
9. persist the decision and new version identity; and
10. release temporary candidate artifacts according to retention policy.

Do not re-consume the candidate as an unrelated document. Do not PATCH only
`document.content` when the accepted improvement is intended to update the
copyable PDF overlay.

The exact Paperless API sequence depends on the deployed Paperless version and
must be integration-tested. If the available API cannot create/preserve versions
atomically enough, application remains disabled while comparison stays usable.

Downstream re-analysis may finish asynchronously, but coordinated application is
not reported complete until the invalidation record is durable. A failed
downstream module does not undo a valid Paperless version; it remains visibly
stale and retryable under issue
[#114](https://github.com/rsocko/owl/issues/114).

## Rollback

Rollback selects a preserved Paperless version as latest and refreshes content,
search, and OWL assessment. OWL keeps the previous archive/text snapshot for a
configurable rollback window as additional protection, while Paperless versions
provide the primary user-visible history.

Rollback is audited and must be available from the same OWL review surface.

## Batch behavior

Small batches may generate candidates concurrently within resource and provider
limits. Each document still has an independent comparison and decision.

Initial batch controls include:

- explicit document selection;
- maximum documents and pages;
- provider allowlist;
- Azure cost estimate and hard cap;
- cancellation; and
- no `accept all` action.

## Errors and retries

Provider timeouts, rate limits, unsupported/encrypted files, invalid PDFs, stale
comparisons, and Paperless version failures are distinct terminal or retryable
states. Retries are bounded and idempotent by source checksum, engine version,
and settings hash.

Raw provider responses and document text are not placed in notifications or
external issue trackers.
