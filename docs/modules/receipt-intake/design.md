---
sidebar_position: 1
title: Canonical Receipt Intake
---

# Canonical receipt intake and provenance ledger

OWL owns the byte-free idempotency and provenance boundary for receipt intake.
Paperless-ngx remains the canonical artifact store. OWL never becomes a document
archive and never exposes raw Paperless or Monarch identifiers.

## Protected v1 API

Set `OWL_RECEIPT_INTAKE_API_TOKEN` independently from the Mission Control token.
All routes require `Authorization: Bearer <token>`.

### Submit an occurrence

`POST /api/receipt-intake/v1/occurrences` streams the request body to a bounded OS
temporary file. Supported content types are `application/pdf`, `image/jpeg`,
`image/png`, and `image/tiff`; the default limit is 25 MiB. The file is removed
after Paperless acknowledgement, duplicate, failure, or unknown outcome.

Required headers:

| Header | Contract |
| --- | --- |
| `Content-Type` | One allowlisted media type |
| `X-OWL-Source-Channel` | `email_attachment`, `email_body`, `scanner_job`, `phone_upload`, `manual_upload`, `provider_api`, or `monarch_recovery` |
| `X-OWL-Source-Occurrence-Version` | Source identity algorithm version, 1-32 lowercase version characters; currently `1` |
| `X-OWL-Source-Occurrence` | 64 lowercase hex characters, generated from the channel-scoped source identity before crossing the OWL boundary |

Optional bounded headers:

| Header | Contract |
| --- | --- |
| `X-OWL-Connector-Ref` | Opaque, whitespace-free, 8-200 characters |
| `X-OWL-Source-Observed-At` | RFC 3339 source timestamp |
| `X-OWL-Transform-Version` | Deterministic rendering/conversion version, 1-64 characters |
| `X-OWL-Normalized-Fingerprint` | 64-character lowercase hex strong normalized-content fingerprint |
| `X-OWL-Normalized-Version` | Required with normalized fingerprint, 1-64 characters |
| `X-OWL-Semantic-Fingerprint` | 64-character lowercase hex bounded semantic fingerprint |
| `X-OWL-Semantic-Version` | Required with semantic fingerprint, 1-64 characters |

Callers construct source occurrences with
`doc_intelligence_hub.modules.receipt_intake.identities`. The helpers cover email
attachment, rendered email body, scanner job, phone/manual upload, provider object
version, and Monarch recovery identities. Raw mailbox, message, provider, scanner,
upload, and Monarch identifiers are hashed before persistence.

### Result schema

Every submit, status, lookup, and reconciliation route returns:

```json
{
  "schema_version": "1.0",
  "intake_ref": "intake_<opaque>",
  "outcome": "new_canonical",
  "attempt_state": "accepted",
  "canonical_document_ref": "document_<opaque>",
  "review_ref": null,
  "reason_codes": ["paperless_acknowledged"],
  "source_channel": "manual_upload",
  "source_occurrence_version": "1",
  "source_as_of": "2026-10-10T16:00:00Z",
  "retry_safe": true,
  "external_replica_eligible": true
}
```

`outcome` is one of `new_canonical`, `source_occurrence_reused`,
`exact_hash_reused`, `normalized_content_review`,
`semantic_review_candidate`, `paperless_duplicate`,
`upload_outcome_unknown`, `upload_retryable`, or `upload_failed`.
`attempt_state` is one of `pending`, `accepted`, `unknown`, `retryable`, `failed`,
or `review_required`.

`monarch_recovery` results always set `external_replica_eligible` to `false`. This
is the structural loop-prevention signal: recovered artifacts must never be
submitted back to Monarch.

The response excludes bytes, OCR, filenames, email body/subject, signed URLs, raw
Paperless/Monarch/account identifiers, credentials, and upstream exception text.

### Read and reconcile

- `GET /api/receipt-intake/v1/occurrences/{intake_ref}` reads the bounded result.
- `GET /api/receipt-intake/v1/lookup` requires exactly one of
  `X-OWL-Source-Occurrence` or `X-OWL-Blob-SHA256`. Call this before retrying.
- `POST /api/receipt-intake/v1/occurrences/{intake_ref}/reconcile` handles an
  unknown upload by querying Paperless with the exact SHA-256. It never uploads.

An unknown outcome is not retry-safe. A known retryable outcome can be resubmitted
with the same occurrence and exact bytes. OWL reuses the same ledger attempt and
enforces `OWL_RECEIPT_INTAKE_MAX_UPLOAD_ATTEMPTS` (default 3).

## Gate order and outcomes

1. Same source occurrence and exact hash returns its prior canonical result.
2. Exact SHA-256 attaches provenance to the existing canonical document.
3. A same-version strong normalized-content match creates a review proposal and
   pauses upload.
4. A same-version semantic match creates a review proposal **after** Paperless
   accepts a distinct canonical document. Semantic similarity never suppresses
   ingestion.
5. Otherwise OWL submits a new canonical Paperless document.

Paperless duplicate task responses are `paperless_duplicate`; timeouts and
non-terminal tasks are `upload_outcome_unknown`; bounded transient failures are
`upload_retryable`; exhausted or permanent failures are `upload_failed`.

## Ledger and migration

`receipt_intake.db` schema version 1 creates:

- `receipt_canonical_documents`: deployment fingerprint, exact hash, opaque
  Paperless mapping, transform and versioned normalized/semantic fingerprints.
- `receipt_intake_attempts`: versioned source occurrence, channel, timestamps,
  byte count, media type, state, task correlation, and bounded reason code.
- `receipt_match_proposals`: normalized and semantic proposals. Rejections are
  tombstones (`proposal_state=rejected` plus `resolved_at`), never deletes.
- `receipt_external_replicas`: opaque external identity, lifecycle, source
  revision, and optimistic integer revision.
- `receipt_audit_events`: identifiers, event types, reason codes, and timestamps.

No table contains document content, OCR, filenames, email content, signed URLs,
credentials, or temporary paths. Source mappings, canonical mappings, proposal
tombstones, replica deletion receipts, and audit events are retained by default.
Any future purge policy must preserve idempotency mappings and tombstones longer
than transient analysis output.

## External replica service boundary

`ReceiptIntakeService.record_external_replica(...)` records lifecycle values
`not_submitted`, `submitted`, `processing`, `matched`, `awaiting_transaction`,
`ambiguous`, `failed`, `retryable`, `review`, `not_applicable`, `verified`,
`settled`, `deleted`, or `delete_unknown`.

Creation accepts `expected_revision=None` or `0` and returns revision `1`. Updates
must supply the returned revision; stale or omitted revisions fail with
`external_replica_revision_conflict`. The returned contract contains only opaque
replica/canonical refs, lifecycle state, and revision. This is sufficient for
later Tyrion work to avoid blind recreation and for recovery to record a
pre-existing Monarch replica without synchronizing bytes.
