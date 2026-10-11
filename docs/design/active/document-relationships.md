---
title: "Document Relationships"
sidebar_label: Document Relationships
sidebar_position: 6
---

# Document Relationships

*Status: Active implementation contract*

*Date: 2026-08-22*

## Outcome

OWL preserves distinct documents that describe the same obligation or sequence and links
them without classifying either document as a duplicate. A second, past-due, or final bill
notice remains available as evidence while increasing the priority of the existing
obligation for an explainable reason.

OWL is authoritative for relationships. Paperless receives a searchable projection, not
an independently editable source of relationship truth.

## Relationship contract

| Type | Direction | Meaning |
|---|---|---|
| `follows` | source → target | Source is a later notice or event for the same obligation |
| `supersedes` | source → target | Source replaces target as the current authoritative document |
| `supports` | source → target | Source supplies evidence for target without replacing it |
| `same_sequence` | symmetric | Documents belong to the same sequence with no stronger direction |

Every active relationship stores source and target Paperless IDs, type, provenance
(`automatic`, `user`, or `imported`), confidence, deterministic reason codes, priority
adjustment and explanation, lifecycle timestamps, and optional source duplicate-pair ID.
Removing a relationship is a soft removal so history remains auditable.

Self-links are rejected. Repeated creation of the same active link is idempotent. The
reverse of a symmetric link is the same link. A document pair cannot have incompatible
active direction (`A follows B` and `B follows A`) or both `follows` and `supersedes`.
Duplicate classification remains in `document_duplicates`; relationships remain in
`document_relationships`.

## Deterministic notice detection

Automatic proposals use structured evidence only. The classifier:

1. requires a provider/correspondent match;
2. requires at least one obligation identity signal: exact invoice/claim number, exact
   masked account identifier plus close amount, or same service date plus close amount;
3. determines chronological direction from document/notice dates;
4. detects notice stage from bounded phrases: original bill, reminder, second notice,
   past due, final notice, collections, or disconnect;
5. emits `follows` when the newer document advances or repeats a notice stage, and
   `supersedes` only when explicit replacement/update language is present;
6. proposes automatic creation at confidence `>= 0.85`; lower-confidence results are
   review proposals and never mutate relationship state.

Reason codes and confidence components are returned and stored. Missing provider or
obligation identity evidence produces no proposal rather than a guessed link.

## Priority rules

Priority adjustment is a pure deterministic function:

| Trigger | Adjustment |
|---|---:|
| Reminder or second notice | +12 |
| Past due | +18 |
| Final notice | +28 |
| Collections or disconnect warning | +35 |
| Explicit supersession | +8 |
| Conflicting relationship evidence | +15 and Needs Review |

Only the highest notice-stage adjustment applies; compatible non-stage adjustments may
be added. The result is capped at 100. The API stores and returns the fired rules and a
human-readable explanation.

## Obligation and queue behavior

Relationship creation does not merge, archive, tag as duplicate, or otherwise alter
either Paperless document. For `follows`, `supersedes`, and `same_sequence`, the active
typed graph is the authoritative Action Queue consolidation input. Provider, invoice or
claim, and account evidence validates that the linked documents can safely share an
obligation; matching provider metadata alone never consolidates unrelated bills.

Each consolidated obligation has a stable hashed identity, one primary action, one
authoritative document, and an evidence timeline containing every preserved notice.
Direction selects the newest source document as authoritative even when documents arrive
out of order. Its balance and due date update the primary action. The highest
relationship priority adjustment deterministically raises risk and urgency, while all
fired rule codes and explanations remain available to the API and UI.

Additional action records are retained as suppressed audit history rather than deleted.
An acknowledged, snoozed, or completed primary action keeps its explicit lifecycle state
when a later notice arrives. Relationship creation and soft removal immediately rebuild
affected connected components, so a corrected or removed link can split obligations and
restore each document action's pre-relationship values.

Conflicting provider, invoice/claim, or account identities never merge. Each affected
action is routed to Needs Review with the conflict reason. Obligation recalculation also
records before/after audit events, including the triggering relationship ID.

## Paperless projection

The metadata registry defines two operational fields:

- **Related Document IDs** (`string`) contains all currently related Paperless IDs. A
  text projection is used because a Paperless document-link custom field stores one
  target, while an OWL relationship graph can contain many.
- **Relationship Summary** (`string`) contains a compact generated summary such as
  `follows #123; supports #456`.

Each successful create or removal rebuilds both fields for both affected documents from
OWL's active graph. Projection failure is explicit in the API response and audit state;
it does not roll back the authoritative OWL relationship. The next sync can safely retry
because projection is rebuilt, not incrementally appended.

When **automatic duplicate detection** is enabled, Paperless post-consumption automation
calls `POST /api/duplicates/document-ingested` with `{"document_id": 123}` after metadata
extraction completes. Delivery retries are idempotent: an existing relationship or
Needs Review item is reused, and an unsynced relationship projection is attempted again.
Automatic detection remains disabled by default and can be controlled through
`PUT /api/duplicates/settings`.

Historical maintenance is a separate, secondary operation:

- Every scan accepts a Paperless scope. The safe default reuses the configured Action
  Queue source (monitored tags or saved view). Explicit alternatives are one or more
  Paperless tags, a saved view, a document date range, or all Paperless documents.
  The all-documents option is intentionally advanced and always runs as a tracked
  background scan when applied.
- `POST /api/duplicates/scan/preview` evaluates the selected Paperless scope without
  creating relationships, duplicate pairs, Needs Review items, obligation changes, or
  Paperless projections. It reports the human-readable scope, discovered and
  metadata-eligible documents, bounded exclusion reason counts, candidate groups and
  pairs, reusable links, and review candidates.
- `POST /api/duplicates/scan/runs` starts one background application run and immediately
  returns its durable run ID. Repeating the same active scope attaches to that run
  without launching another worker. A different scope receives a bounded conflict that
  identifies only the active scope kind.
- `GET /api/duplicates/scan/runs/{run_id}` reports `queued`, `running`, `completed`, or
  `failed` state with aggregate progress, safe failure details, result counts, and a
  separate Paperless projection result. Paperless projection problems are partial
  success: core scan `failures` remain reserved for pair-processing failures because
  relationships are already durable in OWL.
- Concurrency is protected by one durable active slot. Repeated completed scans reuse
  existing relationship identities and review records rather than duplicating them.
- The synchronous `POST /api/duplicates/scan` endpoint remains available for compatible
  callers.

Discovery uses the shared Paperless client and bounded pagination. The scanner creates
deterministic indexes from the classifier's required structured evidence: normalized
provider/correspondent plus exact invoice or claim identity, account identity with a
close amount, or service date with a close amount. Only documents in compatible buckets
are compared, and document pairs produced by multiple keys are deduplicated. OCR content
for notice-stage phrases is fetched only for documents already present in candidate
pairs; it is never persisted in scan status or errors.

`GET /api/relationships/projection-health` reports pending, failed, and explicitly
not-intended projection counts plus the latest safe error. Projection intent is durable:
relationships created with `project_to_paperless=false` are excluded from reconciliation.
`POST /api/relationships/reconcile-projections` retries intended projections
idempotently and returns attempted, succeeded, failed, remaining, and per-relationship
errors. Projection work uses the API's canonical request-scoped Paperless client rather
than loading a separate statements configuration. Each document update is isolated, so
a failure marks only relationships in the affected document or obligation scope while
successful relationship projections remain synced. Stable categories distinguish
missing runtime configuration, incompatible custom-field schema, document update, and
obligation projection failures; detailed exceptions remain in server logs. OWL remains
authoritative when any Paperless retry fails.

Action Queue Operations validates the full projection schema: **Related Document IDs**
(`string`), **Relationship Summary** (`string`), **Related Documents**
(`documentlink`), **Obligation ID** (`string`), and **Obligation Role** (`select`) with
the required invoice, receipt, reminder, revision, and supporting options. Missing
fields/options can be provisioned when Paperless writes are enabled. Incompatible
existing fields, disabled writes, permission failures, and custom-field endpoint
failures are reported without exposing connector details. Legacy `RelatedDoc` is not a
projection field and is neither required nor recreated.

## UX

Duplicate review offers **Keep both and link** separately from **Not duplicate**. The
reviewer sees the proposed type, direction, evidence, confidence, and priority effect
before confirming. Document context shows incoming and outgoing relationships and
supports removal. Uncertain or conflicting proposals appear in Needs Review.

The Orphans & Duplicates surface reveals a compact scope selector and non-mutating
preview inline before an applied run can start. Scope-specific controls are shown only
when relevant. It explains that documents are preserved and reports discovery,
eligibility, exclusions, candidate groups and pairs, relationships created or reused,
Needs Review items, conflicts, and failures. Empty, loading, validation, warning,
progress, completion, and failure states remain in the inline panel. The all-documents
warning is restrained and explains that the tracked scan may take longer.
Action Queue Operations exposes Paperless relationship retry as a repair action under
Backfill and repair, disabled when projection health has no pending or failed work. A
completed scan with projection trouble explicitly confirms that relationships are safe
in OWL and links directly to that repair surface.

### Visual projection contract

`GET /api/relationships/graph?action_id={id}` is the single read-only projection used
by the Action Queue Timeline and Graph views. Node IDs are namespaced durable IDs:
`obligation:{owl_id}`, `action:{owl_id}`, `document:{paperless_id}`, and
`payment-evidence:{owl_id}`. OWL owns obligation, action, payment-evidence, and every
edge identity. Paperless owns document identity and content.

The projection includes `represents`, obligation evidence, typed document-relationship,
and obligation-payment edges. Timeline order uses stored document/evidence dates with
durable creation time as a fallback; it never infers ordering from display labels.

Tyrion source records are represented only after OWL has persisted normalized,
privacy-bounded `PaymentEvidence` and `ObligationPaymentEdge` identities. Raw Tyrion
source references are not graph identities. Mission Control task nodes are omitted:
OWL exports Action Queue items but does not receive or persist a durable MC task ID.
Action nodes declare their MC projection, and the response returns an explicit
`mission_control_task_identity_unavailable` warning until that connector contract
exists. Display-name, amount, date, or text heuristics must not create cross-system
nodes or edges.

## Delivery order

1. Relationship model, conflict rules, lifecycle, and audit events.
2. Deterministic classifier and priority explanation.
3. Typed create/propose/query/remove API with Paperless projection result.
4. Duplicate-review link action.
5. Tests for second notices, direction, idempotency, conflicts, removal, audit, and
   preservation of both Paperless documents.
