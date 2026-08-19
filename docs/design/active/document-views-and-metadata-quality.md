---
title: "Document Views and Metadata Quality"
sidebar_label: Document Views
sidebar_position: 5
---

# Document Views and Metadata Quality

*Status: Active implementation contract*

*Date: 2026-08-19*

## Decision

OWL provides a grouped launcher for allowlisted document-review views. A view has
two independent characteristics:

1. its **provider** supplies the authoritative membership and count; and
2. its **launch target** determines where the user performs the work.

Paperless is the preferred provider whenever a Paperless saved view can express
the criterion. Paperless-backed views open Paperless by default so document
browsing and metadata editing continue to use its native UI. An OWL launch target
is appropriate only when a purpose-built flow adds review evidence, cross-document
context, safe correction controls, or another meaningful workflow advantage.

OWL-native providers are reserved for derived, cross-document, local-analysis,
or workflow state that Paperless cannot express. Examples include match
candidates, statement-group conflicts, protected migration conflicts, and
correction queues. A display label is never used to identify a source.

## Provider and launch matrix

| Provider | Default launch | Use when |
|---|---|---|
| `paperless` | `paperless` | Paperless can express the filter and its normal document list/editor is the best workflow |
| `paperless` | `owl` | Paperless remains authoritative for membership, but a named OWL review surface is materially better |
| `owl` | `owl` | Membership depends on OWL-local or cross-document state |

The initial release does not iframe Paperless. A normal deep link avoids
duplicated navigation/edit controls and does not depend on the Paperless
deployment permitting cross-origin framing.

## Saved-view execution and performance

A Paperless saved view stores filter and presentation definitions; it is not a
materialized result set. Paperless's own frontend loads `filter_rules` from the
saved view and sends them through the ordinary filtered document-list service.
OWL follows the same model:

1. fetch the saved view by numeric ID;
2. validate the returned ID and supported filter-rule shape;
3. translate the rules to ordinary `/api/documents/` query parameters;
4. request one result with `page_size=1`; and
5. use Paperless's response `count`.

Filtering and counting therefore remain server-side in Paperless. OWL does not
download, mirror, or synchronize the result set. This design does not claim that
every criterion uses the same index: full-text search, relational metadata
filters, and custom-field expressions have different Paperless execution paths.
The practical guarantee is that Paperless remains authoritative and OWL does not
perform a second client-side scan.

Configured Paperless counts run with bounded concurrency. The catalog endpoint
does not persist counts. `checked_at` is the observation time for each view and
`generated_at` is the response time. The frontend keeps only the current
response and refreshes on page load or an explicit **Refresh counts** action.
Any future cache must be short-lived, keyed by deployment/source identity, and
must preserve per-view error and observation timestamps.

## Stable identifiers and allowlisting

`DOCUMENT_VIEWS_CONFIG` points to a YAML catalog. The catalog contains only views
that should be exposed in OWL:

```yaml
groups:
  - id: daily-review
    label: Daily Review
    default_expanded: true
    views:
      - id: inbox
        label: Inbox
        provider: paperless
        source_id: 17

      - id: needs-review
        label: Needs Review
        provider: owl
        source_id: triage.pending
```

- Group and view `id` values are stable OWL slugs used for UI identity.
- A Paperless `source_id` is the positive numeric saved-view ID.
- An OWL `source_id` is a registered resolver key.
- `label` and `description` are presentation only and may change without
  breaking the source contract.
- Omitting `launch` on a Paperless view means `paperless`.
- A Paperless view may set `launch: owl` plus an internal `owl_route`.
- OWL providers always use their registered OWL route.

`PAPERLESS_URL` is the service-to-service API origin used for counts.
`PAPERLESS_BROWSER_URL` is the separately configured, browser-reachable origin
used for Paperless launch links. This separation is required for Docker
deployments where the API hostname is not resolvable from a user's browser.

The loader rejects duplicate IDs, unknown OWL sources, external OWL routes,
invalid Paperless IDs, unknown keys, and catalogs over the configured safety
bounds. An unset `DOCUMENT_VIEWS_CONFIG` intentionally returns an unconfigured
empty catalog. Once a path is configured, a missing or invalid file is an
explicit startup failure rather than a plausible empty result.

`config/document-views.example.yaml` uses synthetic IDs. Replace them with
deployed Paperless IDs in an untracked deployment file; do not couple
configuration to saved-view names.

## Availability and failure behavior

The catalog endpoint is `GET /api/document-views`. Healthy views are not hidden
when another view fails.

| Status | Meaning |
|---|---|
| `ready` | The provider returned a current count |
| `unsupported` | The saved view exists but uses a rule OWL cannot safely translate for counting |
| `unavailable` | Configuration, permission, existence, connectivity, or Paperless response prevented a count |

An unsupported or temporarily unavailable Paperless count retains its launch
link when the destination is known. OWL launch routes remain available even when
the Paperless count client cannot be created. A Paperless launch is disabled
when `PAPERLESS_BROWSER_URL` is unset.
Errors use safe codes and operator-oriented messages; upstream response bodies,
tokens, filter values, and document metadata are not returned.

## Permissions and privacy

The Paperless count reflects the Paperless API token used by OWL. The opened
Paperless page reflects the browser user's Paperless session. If those principals
have different object permissions, the displayed count and visible documents
may legitimately differ.

Deploy OWL inside the same intended trust boundary as the aggregate counts it
exposes. The catalog endpoint returns configured labels, provider/source IDs,
aggregate counts, routes, and timestamps only. It does not return titles, OCR
content, custom-field values, patient data, account identifiers, or saved-view
filter values. Operators must not place sensitive document facts in labels,
descriptions, IDs, routes, logs, issues, or example configuration.

Document Views is read-only. Launching a view never changes Paperless metadata.
Any OWL correction or writeback flow remains separately previewable, auditable,
and confirmed under its own workflow contract.

## Extending views

### Add a Paperless-backed view

1. Create and validate the saved view in Paperless.
2. Record its numeric ID; do not use the display name as a lookup key.
3. Add it to the deployment allowlist with `provider: paperless`.
4. Keep the default Paperless launch unless an existing OWL route provides a
   purpose-built review flow.
5. Refresh the launcher and verify count visibility with the OWL service token.

### Add an OWL-native view

1. Confirm the criterion cannot be represented as a Paperless saved view.
2. Add a stable resolver key to `OWL_VIEW_DEFINITIONS`.
3. Implement an exact aggregate count against the authoritative OWL store.
4. Register an internal route to an existing purpose-built review surface.
5. Add backend contract tests and frontend launcher coverage.
6. Add the resolver key to the deployment allowlist.

Do not register metadata-quality or account-conflict views until their protected
queue and privacy-scoped review UI exist. Migration report files are not a
substitute for a supported queue.
