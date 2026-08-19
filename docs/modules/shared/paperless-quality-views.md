---
title: Paperless Quality Views
sidebar_label: Quality Views
---

# Paperless Quality Views

`paperless-quality` provisions deterministic Paperless saved views for OWL review
workflows. The committed registry contains only stable keys and view semantics. Paperless
object IDs, household mappings, account-field bindings, owner IDs, correspondent
candidates, endpoints, and audit artifacts remain in protected deployment inventory.

The managed keys are:

`inbox`, `missing_correspondent`, `missing_document_type`, `no_tags`, `record`, `other`,
`manual_missing_storage_path`, `eob_missing_household_member`,
`account_identifier_missing_or_conflicting`, `duplicate_correspondent_candidates`, and
`recently_added_awaiting_review`.

The account view is deliberately a safe scan superset: it includes missing canonical
values and documents carrying a legacy value. OWL computes the exact missing/conflicting
count with the typed registry. The duplicate-correspondent view contains documents from
inventory-provided candidate correspondents only. It never merges correspondents.
Recently added uses a rolling `recent_window_days` full-text date predicate and excludes
every inventory-provided review-complete tag.

## Plan

Create a protected manifest from `config/paperless-quality.example.yaml`, then run:

```text
paperless-quality plan \
  --config /app/data/protected/paperless-metadata/quality.yaml \
  --protected-output /app/data/protected/paperless-metadata/quality-plan.json
```

Planning is GET-only. Normal stdout contains stable keys, aggregate expected/observed/exact
counts, action and reason codes, and the plan digest. It does not contain IDs, names,
document metadata, content, titles, paths, household data, account values, or endpoints.
The protected plan contains IDs and optimistic-concurrency fingerprints and must be
owner-only. Re-run planning whenever inventory, the Paperless origin, or view definitions
change.
Any configured `expected_counts` mismatch is a hard apply tripwire: review the protected
inventory, update the private expectation deliberately, and create a new plan.
The plan writer verifies owner-only POSIX permissions. It fails closed on Windows because
portable Python cannot verify ACLs; Windows integrations must use the service API only
after independently protecting the destination and explicitly opting into that path.

## Saved-view apply

```text
paperless-quality apply-views \
  --apply \
  --external-writers-disabled \
  --config /app/data/protected/paperless-metadata/quality.yaml \
  --plan /app/data/protected/paperless-metadata/quality-plan.json \
  --plan-digest DIGEST \
  --approval saved-views:DIGEST \
  --state-db /app/data/protected/paperless-metadata/quality-audit.sqlite
```

Apply creates missing views, updates drifted managed views, and leaves unrelated views
alone. Duplicate managed names fail to review instead of choosing one. Every result is
written to protected migration state. `WRITE_TO_PAPERLESS` must be false and all external
writers must be stopped or proven non-overlapping.

OWL Action Queue lists the views with `paq views` and accepts either the Paperless ID or a
stable key:

```text
paq run --saved-view-key missing_correspondent --dry-run
```

## Manual storage-path correction

Manual correction is a separate operation and never runs as part of view provisioning:

```text
paperless-quality apply-manual-storage-path \
  --apply \
  --external-writers-disabled \
  --manufacturer-reviewed \
  --paperless-export-verified-at 2026-08-18T20:00:00Z \
  --owl-backup-verified-at 2026-08-18T20:00:00Z \
  --batch-size 25 \
  --config /app/data/protected/paperless-metadata/quality.yaml \
  --plan /app/data/protected/paperless-metadata/quality-plan.json \
  --plan-digest DIGEST \
  --approval manual-storage-path:DIGEST \
  --state-db /app/data/protected/paperless-metadata/quality-audit.sqlite
```

Both backup attestations must be timezone-aware and less than 24 hours old. Each document
is re-read and must still have the planned modification fingerprint, Manual document type,
and no storage path. Changed documents remain review items. Successful patches are read
back and verified; each document receives a protected audit outcome. Use deployment
retention of seven days for raw inventory and at least 90 days for mutation audit.
When more unresolved candidates remain than `--batch-size`, the command reports `partial`
with a non-zero status; rerunning the same approved plan advances past reconciled documents.
