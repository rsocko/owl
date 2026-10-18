---
title: Safe Paperless Metadata Migration
sidebar_label: Metadata Migration
---

# Safe Paperless Metadata Migration

OWL provides registry-driven tooling to inventory and backfill canonical Paperless custom
fields. The tooling consumes the typed metadata registry; operators do not configure field
names, aliases, types, or IDs independently.

## Safety model

- `inventory` performs only GET requests.
- `prepare` and `backfill` are dry-run operations unless `--apply` is present.
- Apply mode requires protected state and an explicit confirmation that every external
  metadata writer, including Paperless-AI and older OWL releases, is disabled.
- Apply mode refuses to run when `WRITE_TO_PAPERLESS` enables another OWL write path.
- Writes target canonical field IDs only and preserve unrelated custom fields.
- Every write is read back and verified.
- Conflicts, invalid conversions, incompatible definitions, and exhausted retries remain
  actionable outcomes. They are never converted to plausible defaults.
- Backfill tooling never renames, hides, clears, or deletes legacy fields.
- Field retirement is a separate dry-run-first command. It operates only on registry-approved
  retirement candidates with no saved-view, document-type, or workflow references.
- Apply mode clears a populated legacy Action/EOB value only when the corresponding document
  has authoritative OWL data, verifies the clear, and deletes the definition only after no
  values remain.
- Uncovered legacy values require the explicit `--discard-retired-values` option. Aliases are
  never discarded by that option.

Paperless's native Document Type remains the canonical taxonomy and is outside this
custom-field migration. The tool does not create or migrate a separate OWL document-type
field.

## Field ownership and retirement

The typed registry explicitly identifies what remains in Paperless and what can eventually
be retired. Unknown fields are user-owned and are always preserved.

| Outcome | Fields |
|---|---|
| Keep — durable archive metadata | `Account Identifier`, `Patient Name`, `Provider Name`, `Date of Service`, `Patient Responsibility`, `Claim Number`, `Invoice Number`, `Series Name`, `Document Amount`, `Document Due Date` |
| Keep — OWL projection | `Related Document IDs`, `Relationship Summary`, `Related Documents`, `Obligation ID`, `Obligation Role`, `Action Status` |
| Migrate, then retire when empty | `di_account_id`, `di_patient_name`, `di_provider_name`, `di_date_of_service`, `di_patient_resp`, `di_claim_number`, `di_invoice_number`, `Action Amount` |
| Retire when empty | `Action Analyzed`, `Action Type`, `Action Due Date`, `Action Urgency`, `Action Summary`, `Action Count`, `EOB Match Status`, `EOB Match Score`, `EOB Match Confidence`, `EOB Matched Document`, `EOB Document Type`, `EOB Patient Responsibility`, `EOB Analyzed` |
| Preserve | Every unregistered/custom deployment field |

The action fields are retired because OWL stores actions as first-class records and one
Paperless document may have multiple actions. `Action Analyzed` was only a calendar-date
projection written during enrichment; `ProcessingHistory.processed_at` and action
`created_at` remain the authoritative timestamps. The retired EOB fields contain match
analytics or duplicate metadata that now remains OWL-local or uses canonical durable fields.

## OWL obligation projection

OWL provisions three additional Paperless custom fields for obligation navigation:

| Field | Paperless type | Purpose |
|---|---|---|
| `Related Documents` | Document Link | Multi-value links to every other active document in the obligation |
| `Obligation ID` | Text | Stable OWL-owned obligation identifier shared by all projected documents |
| `Obligation Role` | Select | `invoice`, `receipt`, `reminder`, `revision`, or `supporting` |

These are new fields; the existing `Related Document IDs` and `Relationship Summary`
fields describe generic document relationships and cannot provide native Paperless
navigation or obligation identity. OWL remains authoritative for confidence, provenance,
rejected-link tombstones, action suppression, and completion state.

Link and unlink operations rebuild the projection on every affected document. The
projection merges only these three owned values into each document's custom fields, so
unrelated Paperless metadata is preserved. A removed or rejected link clears stale OWL
values without deleting the document. Operators can idempotently backfill or repair all
known obligations with:

```text
POST /api/queue/obligations/reconcile-projections
```

The response reports per-document successes and failures. When Paperless writes are
disabled, OWL still records the authoritative relationship and returns an explicit
`Paperless writes are disabled` projection result for later reconciliation.

## Output classes

Default stdout is a versioned, redacted JSON summary. It contains the registry digest, run
mode and timestamps, completion state, batch size, aggregate counts, stable registry keys,
stable reason/result codes, and expected/observed type shapes. It excludes endpoints,
tokens, document and field IDs, option values, custom-field values, and document content.

Detailed inventory and migration state are protected runtime artifacts. They may contain
pagination cursors, document and field IDs, normalized before/after values, idempotency
keys, retry details, and timestamps. They never contain OCR text or document content.
Store these artifacts outside the source checkout with access controls appropriate for
the deployment. Their location, retention, backup, and cleanup policy belong to the
private deployment configuration.

The default SQLite store enforces owner-only POSIX permissions. Because Python cannot
verify Windows ACLs portably, it fails closed on Windows; deployments there must provide a
protected state implementation or explicitly pre-verify the path before constructing the
store through the Python API.

## Commands

Set `PAPERLESS_URL` and `PAPERLESS_API_TOKEN` through protected runtime configuration.

```text
paperless-metadata inventory --batch-size 100
paperless-metadata inventory \
  --protected-output /protected/run.metadata-migration.json
paperless-metadata prepare
paperless-metadata backfill --batch-size 100
paperless-metadata field-audit \
  --protected-output /protected/paperless-field-audit.json
paperless-metadata retire-fields
```

The first three examples above do not mutate Paperless. A write-enabled run requires all
preflight gates:

```text
paperless-metadata prepare --apply --external-writers-disabled
paperless-metadata backfill \
  --apply \
  --external-writers-disabled \
  --state-db /protected/metadata-migration.sqlite
```

`retire-fields` is also dry-run by default. Apply mode uses the same single-writer preflight:

```text
paperless-metadata retire-fields \
  --apply \
  --external-writers-disabled \
  --protected-output /protected/paperless-field-retirement.json
```

Apply mode records affected document IDs in the protected artifact. Populated legacy
Action/EOB values backed by OWL records are cleared and verified automatically. Populated
aliases, fields referenced by saved views/document types/workflows, duplicate definitions,
and incompatible definitions remain blocked.

If the protected audit confirms that uncovered operational values are stale and should be
discarded, use the explicit destructive option:

```text
paperless-metadata retire-fields \
  --apply \
  --discard-retired-values \
  --external-writers-disabled \
  --protected-output /protected/paperless-field-retirement.json
```

This option applies only to canonical fields whose registry retirement policy is
`retire_when_empty`; it cannot clear aliases or unmanaged fields.

Resume uses the original run ID and exact batch/retry configuration:

```text
paperless-metadata backfill \
  --apply \
  --external-writers-disabled \
  --state-db /protected/metadata-migration.sqlite \
  --run-id synthetic-run-id \
  --resume
```

Resume fails closed if the registry, configuration, mode, or target instance fingerprint
changed. SQLite transactions persist each audit outcome with its checkpoint. If a process
stops after Paperless accepted a write but before the checkpoint commits, the next attempt
re-reads the document and records the already-matching canonical value as reconciled.

Render aggregate counts from protected state without exposing detailed rows:

```text
paperless-metadata report \
  --state-db /protected/metadata-migration.sqlite \
  --run-id synthetic-run-id
```

Exit status `0` means clean completion, `2` means protected review items remain, and `1`
means an operational failure remains. A reconciliation or verification failure never
returns a success-shaped result.

## Rollout order

1. Deploy a compatibility release containing the typed registry.
2. Disable all external metadata writers.
3. Run read-only inventory and review the protected report.
4. Resolve schema incompatibilities.
5. Dry-run canonical preparation and backfill.
6. Apply in bounded batches with protected state.
7. Observe canonical coverage for a normal processing cycle.

Legacy-field retirement remains explicit and dry-run-first; it is never part of setup or
backfill.
