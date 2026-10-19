---
title: "Paperless Metadata and Document Summary"
sidebar_label: Metadata and Document Summary
sidebar_position: 4
---

# Paperless Metadata and Document Summary Design

*Status: Active design*

*Date: 2026-08-18*

## Decision Summary

OWL will:

1. present document identity through a shared, context-rich `DocumentSummary` UI instead of repeating title- or ID-only renderings;
2. define Paperless custom fields in one metadata schema registry;
3. use human-readable canonical Paperless field names without a `di_` prefix;
4. read canonical and legacy names, but write only canonical names;
5. preserve confidently classified archive identifiers exactly in Paperless and
   show them exactly in purpose-built OWL review workflows inside the same
   deployment trust boundary;
6. retain match confidence and triage analytics in OWL while projecting durable, user-facing metadata to Paperless only when it improves the archive; and
7. migrate metadata through reviewable queues and reversible, type-safe operations.

The metadata registry, governed Account Identifier projection, durable EOB
projection, and shared document-summary contract are implemented. This document
remains the design authority for extending those capabilities to new surfaces.

## Context

The deployed OWL release is `0.2.0`. Its metadata correction API maps internal
fields directly to legacy Paperless names such as `di_patient_name`,
`di_patient_resp`, and `di_account_id`. Account extraction also writes directly
to `di_account_id`. Those mappings are distributed across modules, which makes
field naming, types, privacy, and migration behavior difficult to change safely.

The frontend has a similar consistency problem. Several workflows identify a
document using only its title or Paperless ID even when provider, date, type,
amount, patient, and display-safe account context are available elsewhere. This
increases the chance of selecting the wrong document during correction, split,
merge, duplicate, and matching operations.

## Goals

- Make the same document recognizable across OWL workflows.
- Establish one typed source of truth for Paperless metadata.
- Replace implementation-oriented `di_*` names with user-facing canonical names.
- Preserve access to metadata produced by OWL `0.2.0`.
- Prevent exact account identifiers and unnecessary medical identity data from
  spreading beyond the OWL/Paperless deployment trust boundary through logs,
  analytics, external integrations, or general-purpose aggregate UI.
- Separate durable archive metadata from OWL's operational and analytical state.
- Make metadata cleanup observable, reviewable, idempotent, and reversible.

## Non-Goals

- Turning Paperless into OWL's analytics database.
- Writing every extracted value or model signal back to Paperless.
- Renaming fields in place or deleting legacy fields during initial rollout.
- Displaying raw OCR snippets or unrestricted patient details in a generic
  summary component.

## 1. Shared `DocumentSummary` UI

### Contract

`DocumentSummary` is a shared presentation contract backed by a normalized
document-summary API model. It is not a component that accepts arbitrary
Paperless or module-specific records.

The model should support:

| Group | Fields | Display rule |
|---|---|---|
| Identity | Paperless document ID, title, native Paperless Document Type | Always available; ID is the stable fallback |
| Source | correspondent or provider | Show when known |
| Date | created date, document date, statement date, or date of service | Use a labeled, context-appropriate date |
| Financial | patient responsibility, invoice amount, or balance | Show one labeled amount when relevant |
| Account | Account Identifier | Show the Paperless value in named account-review contexts |
| Clinical | Patient Name | Only in medical workflows that already require patient identity |
| Navigation | Paperless detail or OWL preview URL | Preserve the current workflow's navigation behavior |

The component should offer two visual densities:

- **compact** for tables, selectors, timelines, and candidate lists;
- **review** for drawers, correction panels, and side-by-side decisions.

Both variants use the same ordering and formatting helpers. Missing values are
omitted rather than rendered as a row of placeholders. The accessible name must
include the title or document ID plus enough non-sensitive context to
differentiate adjacent choices.

### Surface Inventory

The initial adoption scope is every current document surface that renders only
a title, only an ID, or a module-generated title in place of document context:

| Surface | Current identity | Target |
|---|---|---|
| Action Queue table and detail drawer | action title or document title | Keep the action title; add the related document's compact/review summary |
| Statement grouping document list | title with separately assembled ID/date/account | Replace local assembly with compact summary |
| Statement series detail list | title with separately assembled ID/date/account | Replace local assembly with compact summary |
| Statement split flow lists | title plus optional account hint | Compact summary for both destination lists |
| Statement merge flow source and target lists | title plus date/account hint | Compact summary for both series |
| Statement timeline tooltip | title plus date/account hint | Compact summary in tooltip-safe layout |
| Metadata correction header and source card | title or ID | Review summary with privacy-scoped metadata |
| Duplicate review primary selector | `Doc A/B #id` plus title | Compact summaries for both choices |
| Manual match page and modal | `EOB #id` and `Bill #id` | Paired compact summaries for source and candidates |
| EOB match detail, history, and confirmation text | match and document IDs | Paired summaries; keep score outside the document summary |
| Unmatched EOB/bill table and suggested pairs | provider plus ID | Compact summary using durable metadata |
| Orphan/duplicate live tables and side-panel headings | synthesized queue or alert title | Summary when a Paperless document ID can be resolved; otherwise retain queue identity |

`DocumentPreview` remains responsible for preview/download behavior. It may
compose `DocumentSummary`, but it does not become the metadata source of truth.
Alert, insight, and history-event titles that do not identify a Paperless
document are explicitly outside this component's scope.

### Data Boundary

The frontend must not reconstruct summaries independently from action, EOB,
statement, and triage response shapes. APIs should return a shared summary model
or a shared adapter should normalize those responses at the API boundary.
Formatting, masking, field precedence, and privacy flags belong in shared code.
The normalized model retains the compatibility key
`account_identifier_display`, but named account-review contexts may populate it
with the exact Paperless value. It is omitted outside those contexts.

### Implemented Summary Contexts

The server owns field precedence and privacy enforcement through four named
contexts:

- `general` omits Account Identifier and Patient Name;
- `account_review` may include the exact Paperless Account Identifier;
- `medical_review` may include Patient Name but omits Account Identifier; and
- `medical_account_review` may include Patient Name plus the exact stored
  account identifier needed for EOB review.

Only `document_type_name` or an explicitly supplied native
`paperless_document_type` can populate the summary's Document Type. Module
classifications such as EOB, bill, statement, and receipt roles remain workflow
labels and do not override Paperless's native taxonomy.

## 2. Central Paperless Metadata Schema Registry

The registry is the only authority for Paperless custom-field integration. Each
entry defines:

- stable OWL key;
- canonical Paperless display name;
- Paperless field type;
- ordered legacy aliases;
- normalization and validation rules;
- sensitivity classification;
- read/write policy;
- eligible document types and projection policy; and
- migration/version metadata.

Consumers request fields by stable OWL key. They must not embed Paperless field
names or IDs. The Paperless adapter resolves names to IDs once, validates the
deployed schema, and reports missing, duplicate, or incompatible definitions.

### Canonical Field Names

Canonical Paperless names contain **no `di_` prefix**:

| Stable OWL key | Canonical Paperless name | Type | Legacy read aliases |
|---|---|---|---|
| `account_identifier` | `Account Identifier` | text | `di_account_id` |
| `patient_name` | `Patient Name` | text | `di_patient_name` |
| `provider_name` | `Provider Name` | text | `di_provider_name` |
| `date_of_service` | `Date of Service` | date | `di_date_of_service` |
| `patient_responsibility` | `Patient Responsibility` | monetary | `di_patient_resp` |
| `claim_number` | `Claim Number` | text | `di_claim_number` |
| `invoice_number` | `Invoice Number` | text | `di_invoice_number` |
Additional aliases may be added only after a deployed-schema inventory proves
they exist. Alias matching is exact after trimming; fuzzy field-name matching
is unsafe.

Paperless's native Document Type is the canonical authoritative taxonomy. OWL
does not create, migrate, correct, or project a separate custom document-type
field.

### Read Precedence and Conflicts

For each registry entry:

1. read a non-empty canonical value when present;
2. otherwise read the first non-empty legacy alias in registry order;
3. normalize the selected value according to the registry;
4. retain the source field name in diagnostic metadata; and
5. enqueue a metadata-quality review if canonical and legacy values normalize
   to different values.

Canonical wins a conflict, but the conflict is never silently discarded.
Blank canonical values do not shadow populated legacy aliases.

### Single-Write Rule

All new writes target the canonical field ID only. OWL must not dual-write
canonical and legacy fields because partial failures would create two apparent
sources of truth. Legacy fields remain read-only until migration completion and
an explicit, separately reviewed retirement decision.

Every correction write carries the Paperless `modified` revision captured when
the values were loaded. OWL compares that revision immediately before PATCH and
returns field-level loaded, current Paperless, and proposed OWL values when both
systems changed the same field. The reviewer explicitly keeps Paperless,
applies OWL, or returns to editing. Matching newer values are idempotent
replays. Write audits retain the initiating OWL surface and field names, never
document text, values, or exact protected identifiers.

## 3. Account Identifier Extraction and Privacy

### Paperless Value

Paperless is the document archive and the trust boundary for document-derived
metadata. A user who can read `Account Identifier` must already be authorized to
read the source document. Within that boundary, preserving an exact, clearly
labeled provider account, member, or policy identifier improves filtering and
avoids collisions caused by suffix-only values.

Bank-account and payment-card identifiers follow the same archive rule as other
confidently classified document identifiers. If the value is already present in
the source document, Paperless may preserve it exactly as searchable metadata
and a named OWL review workflow may display and correct it. Claim Number and
Invoice Number have dedicated canonical fields and must not be selected as
account fallbacks.

The registry assigns each identifier class a storage policy:

| Identifier class | Paperless value | Outside Paperless |
|---|---|---|
| Provider or service account | Exact when confidently labeled | Exact in named OWL review; excluded from external projections |
| Member or policy identifier | Exact when confidently labeled | Exact in named OWL review; excluded from external projections |
| Bank account or payment card | Exact when confidently labeled | Exact in named OWL review; excluded from external projections |
| Claim or invoice | Dedicated canonical field | Governed by that field's policy |
| Ambiguous generic sequence | Do not write | Do not expose |

### Extraction

The extraction pipeline should:

1. inspect OCR text inside the trusted OWL processing boundary;
2. identify the label and identifier class rather than relying on digit shape;
3. reject ambiguous generic digit sequences;
4. apply the registry's Paperless storage policy in memory;
5. record extraction method, class, and confidence in OWL-local state without
   retaining the exact candidate; and
6. write the policy-approved value directly to Paperless after confidence checks.

Low-confidence, conflicting, or multiple plausible account identifiers enter
the metadata-quality queue instead of being written automatically.

### Privacy and API Boundaries

- Exact identifiers and surrounding OCR text must not enter OWL persistence,
  application logs, telemetry, analytics events, queue titles, URLs, browser
  storage, Mission Control, Tyrion, GitHub, or other external projections.
- General API responses never expose the canonical Paperless
  `Account Identifier` value. Named OWL account-review responses may expose the
  exact Paperless value for display and correction within the deployment trust
  boundary.
- A narrowly scoped correction endpoint may accept an exact archive identifier
  for immediate write-through to Paperless. It must not echo the value or place
  request bodies in logs, audit records, retries, or durable OWL state.
- General-purpose summaries omit Patient Name and Account Identifier by
  default. Callers opt into those fields through a named medical or account
  review context, never an arbitrary boolean.
- OWL search indexes may include the masked display value but not exact values
  or raw candidates. Paperless-native indexing remains inside the archive
  boundary and follows Paperless access controls.
- Correction and migration audit records store masked old/new display values,
  field IDs, actor, and reason; they do not store exact values or raw OCR evidence.

## 4. EOB Data Placement

Paperless is the durable archive. OWL is the analysis and workflow system.

### Keep OWL-Local

- match score and confidence label;
- factor breakdowns and model/classifier confidence;
- alternative candidate rankings;
- triage reason, queue state, assignment, and aging;
- extraction confidence and source regions;
- benchmark, trend, and coverage analytics;
- correction history used for model improvement; and
- transient payment-reconciliation hypotheses.

These values are run-specific, model-specific, operational, or too volatile to
be durable document metadata.

### Eligible for Paperless Projection

Project a value only when it is useful outside OWL, stable across reprocessing,
understandable to a Paperless user, and safe to expose:

- Patient Name;
- Provider Name;
- Date of Service;
- Patient Responsibility;
- Claim Number;
- Invoice Number;
- policy-approved Account Identifier.

Document classification uses the native Paperless Document Type relation and
is not part of custom-field projection.

Projection is not automatic merely because a value was extracted. Registry
policy determines whether confidence is sufficient, confirmation is required,
and which document types are eligible. A confirmed match may justify projecting
durable fields to both documents, but the fact that the match scored 87% does
not.

## 5. Quality and Migration Workflows

### Metadata-Quality Queue

Create review items for:

- canonical/legacy value conflicts;
- missing required metadata for an eligible document type;
- invalid date, monetary, or select values;
- multiple candidate account identifiers;
- values below auto-projection confidence;
- duplicate canonical field definitions; and
- incompatible deployed Paperless field types.

Items must show a privacy-scoped `DocumentSummary`, proposed value, source,
validation failure, and available actions. Bulk acceptance is allowed only for
homogeneous, reversible corrections.

### Correspondent Merge Review

Correspondent cleanup is a separate entity-resolution queue, not an automatic
side effect of metadata migration. A proposal includes source and target
correspondents, document counts, normalized-name evidence, affected document
types, and collision warnings. Execution must be idempotent, auditable, and
support a dry run. Similar names alone are insufficient for an automatic merge.

### Safe Type Migrations and Storage Corrections

Paperless field-type changes are data migrations:

1. inventory deployed names, IDs, types, values, and document assignments;
2. validate conversion against every populated value;
3. create a new correctly typed canonical field rather than mutate an
   incompatible field in place;
4. backfill normalized values in bounded, restartable batches;
5. compare counts and sampled values;
6. switch registry writes only after validation;
7. retain the source field through an observation period; and
8. require explicit approval before archival or deletion.

Every operation supports dry-run output, checkpoints, per-document errors, and
retry without duplicating writes. A failed conversion remains queued; it is not
coerced to a plausible default.

## 6. OWL 0.2.0 Compatibility and Rollout

The migration uses **dual-read/single-write**:

### Phase A: Prepare

- Inventory existing canonical and `di_*` fields, IDs, types, and values.
- Create missing canonical fields with validated types.
- Make no runtime or destructive data changes.

### Phase B: Compatibility Release

- Deploy registry-based readers that understand canonical and legacy aliases.
- Route all writes to canonical fields.
- Keep legacy fields intact.
- Prevent OWL `0.2.0` instances from performing metadata writeback during the
  rolling cutover; they may continue read-only workloads.

Existing `0.2.0` data remains readable because aliases are supported. The
single-writer restriction avoids a stale legacy write competing with a newer
canonical value. Rollback after canonical writes requires either the
compatibility reader or a deliberate reverse projection; an unmodified `0.2.0`
writer is not a safe rollback target.

### Phase C: Backfill and Review

- Backfill canonical fields from non-conflicting legacy values.
- Send conflicts and invalid conversions to the metadata-quality queue.
- Record source field, target field, normalized values, and migration version.

### Phase D: Observe

- Compare canonical coverage, conflict rate, write failures, and unresolved
  queue depth.
- Continue dual-read/single-write for at least one normal processing cycle.

### Phase E: Retire Legacy Fields

Legacy retirement is a later, explicit decision. Hiding or deleting `di_*`
fields is not required for this design to succeed and must not occur while any
supported deployment still depends on them.

## 7. Implementation Phases

| Phase | Outcome |
|---|---|
| 1. Registry and compatibility | Typed registry, schema diagnostics, dual-read/single-write behavior |
| 2. Safe canonical migration | Canonical fields, dry-run/backfill tooling, conflict queue |
| 3. Privacy-safe enrichment | Classified Account Identifier extraction, Paperless storage policy, trusted review display, and external egress controls |
| 4. Shared UI | Normalized summary API model and `DocumentSummary` adoption |
| 5. Quality operations | Metadata-quality and correspondent merge review queues |
| 6. EOB projection hardening | Registry-driven durable EOB projection; analytics remain OWL-local |

Phases 1 and 2 are prerequisites for all canonical writes. Phase 4 may start in
parallel using read-only summary adapters, but it must consume registry-resolved
metadata before completion.

## Acceptance Criteria for the Design

- All seven canonical durable metadata field names are defined without a `di_` prefix.
- Native Paperless Document Type remains the sole authoritative taxonomy.
- Legacy aliases and deterministic conflict behavior are explicit.
- No initial migration step deletes or renames a deployed field in place.
- Exact account identifiers are retained only in Paperless and may be read
  transiently by named OWL review workflows; external projections remain
  display-safe.
- OWL-local and Paperless-projected EOB data are clearly separated.
- Every title- or ID-only document surface has an adoption disposition.
- Migration and merge operations are dry-runnable, auditable, idempotent, and
  reviewable.
- The rollout describes how deployed OWL `0.2.0` data remains readable.
