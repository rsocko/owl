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
- Legacy fields are never renamed, hidden, cleared, or deleted by this tooling.

`Normalized Document Type` accepts deployed text or select fields, but field creation is
disabled until a protected inventory resolves that deployment-specific decision. The
tool reports `type_decision_required` instead of choosing a type or adding private options.

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
4. Resolve the `Normalized Document Type` decision and schema incompatibilities.
5. Dry-run canonical preparation and backfill.
6. Apply in bounded batches with protected state.
7. Observe canonical coverage for a normal processing cycle.

Legacy-field retirement is a separate, explicitly reviewed future operation.
