---
title: Mission Control Integration
sidebar_label: Mission Control Integration
---

# Mission Control Integration

Mission Control (MC) reconciles OWL Action Queue items through the flat-array
connector API. OWL remains the source of truth for document actions and applies
every lifecycle mutation to its database before writing the Action Status back
to Paperless-ngx. Successful Paperless writes update `last_synced_status`;
resolved statuses also remove configured intake tags when that behavior is
enabled.

MC owns cross-system prioritization only. OWL owns classification, readiness,
document/Paperless metadata, lifecycle transitions, and correction history.

## Payee and document review

The versioned payee-review contract requires
`Authorization: Bearer <OWL_MISSION_CONTROL_API_TOKEN>` on every request. It is
additive to the existing Action Queue connector.

Set `OWL_TYRION_PAYEE_CONNECTOR_REF` to the one opaque connector identity that
OWL may synchronize. Callers select only an opaque replay generation; they
cannot use OWL's stored Tyrion credentials to select another connector.

OWL consumes Tyrion's read-only `PayeePatternProjectionV1` replay endpoint:

```http
GET /api/connector/v1/payee-patterns/{sourceGeneration}?connectorRef={connectorRef}
```

The projection supplies opaque `payeeRef` identity and privacy-bounded
recurrence evidence. OWL never ingests raw transactions, never matches identity
by `displayName`, and never converts financial recurrence classifications or
interval evidence into document cadence. OWL derives a stable local review ID
from its Paperless deployment identity, Tyrion connector identity, and opaque
payee identity; the source `payeeRef` is not exposed to Mission Control.

### Reconcile the review queue

```http
GET /api/mc/v1/payee-document-reviews?status=all&limit=100&offset=0
```

`status` accepts `all`, `unreviewed`, `reviewed`, or `inactive`. Each item
contains the bounded Tyrion evidence, OWL `document_decision`, zero or more
reviewed mappings and expectation IDs, an `owl_deep_link`, and these
`source_actions`:

- `set_mapping` — `PUT /api/mc/v1/payee-document-reviews/{id}/mapping`
- `mark_no_documents_expected` —
  `POST /api/mc/v1/payee-document-reviews/{id}/no-documents-expected`

The mapping action accepts:

```json
{
  "mappings": [
    {"account_candidate_id": null, "correspondent_id": 42}
  ],
  "expectation_ids": [],
  "notes": "Reviewed in Mission Control."
}
```

`account_candidate_id` is optional and, when present, references OWL's stable
V1 `accountStatementCandidate` ID. It is not a display name or a raw financial
account identifier. `expectation_ids` supports zero or many OWL document
expectations. The action records `document_decision=documents_expected`.

The durable negative decision accepts the same `mappings` and optional `notes`,
but no expectation IDs:

```json
{
  "mappings": [
    {"account_candidate_id": null, "correspondent_id": 42}
  ],
  "notes": "This payee does not issue documents."
}
```

It records `document_decision=no_documents_expected` and remains in force
across Tyrion generations and display-name changes. Every review replaces the
current mapping atomically and appends an immutable OWL history event.

### Correspondent lookup

```http
GET /api/mc/v1/correspondents?query=utility&limit=100&offset=0
```

The result is `{id, name, lifecycle_status, owl_deep_link}[]`. `name` is for
search and display only; actions persist the numeric Paperless correspondent
`id`. Specialist queue inspection and immutable history remain available in
OWL under `/api/statements/payee-document-reviews` and
`/api/statements/payee-document-reviews/{id}/history`.

## List and reconcile actions

```http
GET /api/action-queue/actions?status=all&limit=100&offset=0&updated_since=2026-08-22T12:00:00Z&include_not_ready=false
```

| Parameter | Default | Behavior |
|-----------|---------|----------|
| `status` | all | One of `pending`, `acknowledged`, `completed`, `snoozed`, `dismissed`, `not_an_action`, or `all` |
| `limit` | `100` | Page size from 1 through 500 |
| `offset` | `0` | Deterministic page offset |
| `updated_since` | none | Inclusive ISO-8601 lower bound on `updated_at` |
| `include_not_ready` | `false` | When false, returns trusted actions plus terminal reconciliation records. Set true only for review/diagnostic experiences. |

The response remains a flat JSON array for backward compatibility. Each item
includes `id`, normalized lowercase `action_type` and `urgency`, normalized OWL
`status`, `document_created_at`, `created_at`, `updated_at`, `completed_at`,
`snoozed_until`, and the existing document/action metadata.
`document_created_at` is the authoritative Paperless `created` calendar date as
an ISO `YYYY-MM-DD` string, or `null` when Paperless did not provide a valid
date. It never falls back to the OWL action's `created_at` lifecycle timestamp.
Results preserve the legacy newest-first order by action `created_at`, then
`id`, so unpaginated clients continue to receive the newest actions.

OWL creates one MC item per action, not one per Paperless document. Additive
grouping fields let MC present related work without collapsing task identity:

| Field | Semantics |
|-------|-----------|
| `document_group_id` | Stable `paperless:{document_id}` group key |
| `sibling_action_ids` / `sibling_count` | Current non-superseded actions from the document |
| `action_position` / `is_primary` | OWL display order and analyzer-selected primary action |
| `parent_action_id` | Source action when a user manually split out another task |
| `superseded_by_action_id` | Survivor ID when this action was absorbed by a merge |

A split creates a new OWL ID and therefore a new MC task. A merge preserves the
survivor ID and returns the absorbed ID as a non-ready, dismissed reconciliation
record so MC can cancel the old task instead of leaving it orphaned.

### Readiness and review fields

The following fields are additive. Older consumers may ignore them, but MC task
ingestion must use `action_ready` as its sole trust gate.

| Field | Shape | Semantics |
|-------|-------|-----------|
| `action_ready` | boolean | `true` only when OWL trusts the action and critical details are present |
| `review_state` | `ready \| needs_review \| resolved_no_action` | Human-readable readiness lifecycle |
| `needs_review_url` | string or null | OWL deep link `#/triage?type=action_classification&item={id}` for the current review item |
| `recommended_cta` | object or null | `{id, label, url?: string|null, phone?: string|null, metadata?: object}`; fields are additive and must not be narrowed |
| `source_actions` | array | OWL-owned mutations available for this item |

The default response does **not** include uncertain not-ready items. It does
include dismissed, `not_an_action`, and superseded terminal records needed to
reconcile tasks that MC may already hold. With
`include_not_ready=true`, uncertain items are returned with
`action_ready=false`, `review_state=needs_review`, and a `needs_review_url`.
They must not be converted into MC tasks. `resolved_no_action` items are
non-actionable even if fetched through an explicit historical status query.

Ready items expose this source action:

```json
{
  "id": "send_to_review",
  "label": "Send to Needs Review",
  "method": "POST",
  "url": "/api/action-queue/actions/42/review"
}
```

Ready FILE and ARCHIVE items additionally expose:

```json
{
  "id": "file_document",
  "label": "File in Paperless",
  "method": "POST",
  "url": "/api/action-queue/actions/42/file"
}
```

OWL omits `file_document` while Paperless writes are disabled.
`file_document` removes only configured intake/monitor tags, writes the source
status, and completes the OWL action. It is filing, not deletion or a separate
Paperless archival operation. Failure is returned rather than converted into a
successful local completion.

## Change lifecycle status

```http
PATCH /api/action-queue/actions/{id}
Content-Type: application/json

{"status": "completed"}
```

Accepted status values are:

- `pending` or `reopen` to reopen the action and clear completion,
  acknowledgement, and snooze timestamps
- `completed` or legacy `done` to complete the action
- `dismissed` to resolve without completion

Unsupported values return `422` and are never persisted. A successful response
is:

```json
{"status": "ok", "id": "42", "new_status": "completed"}
```

## Snooze at the source

```http
POST /api/action-queue/actions/{id}/snooze
Content-Type: application/json

{"until": "2026-09-01T09:00:00Z"}
```

OWL stores the `snoozed` lifecycle status and `snoozed_until`, writes the status
to Paperless when write-back is enabled, and returns both values. Paperless
stores one aggregate document status: any pending sibling keeps the document
pending, and the document is completed only after no required sibling remains
open.

## Submit classifier feedback

```http
POST /api/action-queue/actions/{id}/feedback
Content-Type: application/json

{"feedback_type": "wrong_urgency", "corrected_urgency": "low"}
```

Supported payloads are:

| `feedback_type` | Optional correction |
|-----------------|---------------------|
| `not_an_action` | `reason` |
| `misclassified` | `corrected_action_type` |
| `wrong_urgency` | `corrected_urgency` |
| `wrong_amount` | Required `corrected_amount`; JSON `null` explicitly clears it |

Corrections are recorded in Action Queue feedback history and update the source
action when a corrected value is supplied. `not_an_action` always records
feedback, changes the OWL lifecycle status, and writes that status to Paperless.
It is reserved for false positives. Wrong action types use `misclassified`,
which immediately returns the corrected type and recomputed contextual CTA.
`wrong_amount` updates the shared Paperless `Document Amount` and every OWL
sibling. A Paperless write failure leaves the OWL correction uncommitted.

## Needs Review resolution

The deep link opens OWL's `action_classification` Needs Review category. OWL
supports:

- `confirm` — publish a complete classification as action-ready
- `correct` — record type feedback and/or corrected details, then publish only
  if critical details are present
- `no_action` — record a false positive and resolve as `resolved_no_action`
- `re_evaluate` — rerun analysis and keep OWL's resulting readiness decision

The resolution endpoint is:

```http
POST /api/triage/queue/{review_item_id}/resolve
Content-Type: application/json

{"action": "correct", "payload": {"action_type": "FILE", "title": "File statement"}}
```

## Migration and backward compatibility

The response remains a flat array and existing status, snooze, and feedback
endpoints retain their shapes. Readiness fields and source actions are additive.
Existing Action Queue rows migrate as ready so previously trusted tasks remain
visible. New analysis results are readiness-gated. Consumers that have not yet
adopted readiness remain safe because the default list excludes uncertain
items; consumers using `include_not_ready=true` must implement the trust gate.
