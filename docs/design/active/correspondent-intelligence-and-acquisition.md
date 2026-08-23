---
title: "Correspondent Intelligence and Acquisition"
sidebar_label: Correspondent Intelligence
sidebar_position: 6
---

# Correspondent Intelligence and Acquisition

*Status: Active design*

*Date: 2026-08-22*

## Decision

OWL will add an advisory correspondent-review layer around Paperless:

1. an OWL-local **Correspondent Profile** records reviewed policy and observations;
2. each profile can own multiple **Document Expectations** for distinct accounts,
   document kinds, or series;
3. Paperless history and optional Tyrion signals produce suggestions, not policy;
4. users explicitly confirm expectations and apply proposed Paperless metadata changes;
5. Paperless remains the document archive and normal metadata editor;
6. Tyrion/Monarch remains authoritative for financial accounts and recurring obligations;
7. acquisition uses Paperless mail ingestion and direct email/API connectors first; and
8. credentialed browser automation is deferred, although feasibility and manual portal
   instructions may be recorded.

This design extends statement review without turning OWL into a second document manager,
financial ledger, credential vault, or generic browser-automation platform.

## Why this belongs in OWL

Paperless correspondents provide a name and document-content matching rules. Paperless
custom fields apply to documents, not correspondents. Native workflows can assign titles,
tags, document types, and other metadata, but Paperless does not model:

- whether a correspondent is expected to produce documents;
- several independent series from one correspondent;
- learned or user-confirmed cadence;
- a conditional requirement such as "at least one `DOG:<name>` tag";
- reconciliation with external account or recurring-payment inventories; or
- where an absent document should be obtained.

OWL already analyzes documents across time and supports statement-series review. The new
layer generalizes that evidence into a small, reviewable policy while continuing to launch
ordinary document browsing and editing in Paperless.

## Source boundaries

| System | Authority | Not authoritative for |
|---|---|---|
| Paperless | Documents, correspondent assignment, tags, document type, title, archive history | Expected-but-absent documents, financial accounts, acquisition credentials |
| OWL | Reviewed correspondent policy, document expectations, series mappings, findings, review history | Financial balances or transactions, original document storage |
| Tyrion/Monarch | Active account and recurring-obligation facts | Statement availability, invoices, receipts, Paperless identity |
| Mission Control | Optional authenticated relay for Tyrion-originated projections | Candidate generation or document policy |
| n8n/connectors | Acquisition execution and connector health | Whether a document is required |

An external signal may identify something worth reviewing. Only Paperless evidence or an
explicit user decision can establish a document expectation.

If Mission Control mediates the Tyrion projection, it is transport only. It preserves
Tyrion provenance and must not add independently inferred candidates.

## Domain model

### Correspondent Profile

A profile is keyed by Paperless deployment identity and correspondent ID, never by display
name. Renaming preserves identity. If a correspondent is deleted or merged in Paperless,
OWL flags the profile as orphaned and requires an explicit relink or retirement decision.

| Field | Purpose |
|---|---|
| `correspondent_id` | Stable Paperless identity within a deployment |
| `review_status` | `unreviewed`, `reviewed`, or `ignored` |
| `aliases` | User-confirmed names used for matching external signals |
| `notes` | User guidance that is not document metadata |
| `profile_defaults` | Optional title and metadata defaults inherited by expectations |
| `observed_summary` | Counts, types, title patterns, tag families, and candidate series |
| `last_analyzed_at` / `last_reviewed_at` | Freshness and review state |

Profiles remain OWL-local. OWL must not create synthetic Paperless tags or document custom
fields merely to serialize correspondent configuration.

### Document Expectation

Policy is scoped below the correspondent because a vet may send invoices and medical
records for several animals, while one bank may issue statements for several accounts.

| Field | Values or purpose |
|---|---|
| `expectation_id` | Stable OWL identity |
| `correspondent_id` | Parent profile |
| `kind` | `statement`, `invoice`, `bill`, `receipt`, `record`, `other` |
| `document_type_id` | Optional Paperless type |
| `series_discriminator` | Account, subject, policy, or other non-secret series distinction |
| `expectation_mode` | `recurring`, `periodic`, `one_off`, `irregular`, `not_expected` |
| `cadence` | Frequency and availability window, when applicable |
| `status` | `suggested`, `confirmed`, `dismissed`, `retired` |
| `evidence` | Source, reason codes, confidence, sample size, observed range |
| `title_convention` | Reviewable title template or pattern |
| `metadata_policy` | Required and forbidden metadata |
| `acquisition_source_id` | How and where the document is obtained |

`irregular` means that OWL validates a document when one appears but never reports a missing
period. `suggested`, `dismissed`, `retired`, and `not_expected` expectations also cannot
produce missing-document alerts.

### Metadata Policy

The first version supports only rules that are easy to explain and preview:

- `all_of`: every listed tag is required;
- `any_of`: at least one listed tag is required;
- `none_of`: listed tags are forbidden;
- optional required document type; and
- optional title convention.

For West St. Vet, an invoice expectation could require `any_of` the configured
`DOG:<name>` tags. Paperless nested tags may provide a `DOG` parent for roll-up, but the
parent alone does not satisfy the child requirement.

Rules produce findings and exact proposed patches. They do not continuously enforce or
silently repair metadata.

### Title Convention

Title policy is defined per **correspondent + expectation/series**, not merely per
correspondent. This allows one bank to use different formats for checking and credit-card
statements and one vet to distinguish invoices from animal records.

A convention contains:

- a user-confirmed template;
- a small allowlist of fields;
- the date or period basis;
- an example rendered from a real or synthetic document; and
- evidence showing how closely existing titles follow the suggestion.

The initial field allowlist is:

| Field | Meaning |
|---|---|
| `{correspondent}` | Current Paperless correspondent name |
| `{series}` | Confirmed non-secret series label, such as `Checking 1234` |
| `{kind}` | User-facing document kind, such as `Statement` or `Invoice` |
| `{period}` | Covered period, such as `2026-07` or `2026 Q2` |
| `{document_date}` | Issued/document date in ISO format |
| `{subject}` | Confirmed subject label, such as a pet name |

Examples:

```text
Chase - Checking 1234 - Statement - 2026-07
Chase - Credit Card 9876 - Statement - 2026-07
West St. Vet - Quinn - Invoice - 2026-08-15
West St. Vet - Avery - Vaccination Record - 2026-08-15
```

The user may choose a shorter template if the correspondent is redundant in Paperless's UI,
for example `{series} - {kind} - {period}`. OWL does not impose one global convention.

OWL infers a suggestion by comparing normalized historical titles and identifying which
stable tokens and date/period tokens vary by document. A suggestion must show coverage,
exceptions, and at least three representative before/after examples. It is never accepted
solely because an LLM proposed it.

Rendering is deterministic. Required fields that are unavailable produce a review finding;
OWL does not silently remove a segment and create an ambiguous title. Rendered titles must
fit Paperless's 128-character title limit. The preview shows the exact old and new titles,
and only selected documents are patched.

Paperless workflows may apply a confirmed convention to future documents when their trigger
can identify the expectation unambiguously. Otherwise OWL continues to suggest corrections
after ingestion. `PAPERLESS_FILENAME_FORMAT` remains a separate archive-file concern and
must not be changed merely to enforce document titles.

### Acquisition Source

Acquisition configuration is reusable across expectations:

| Field | Values or purpose |
|---|---|
| `channel` | `paperless_mail`, `email_manual`, `direct_api`, `portal_manual`, `snail_mail`, `linked_storage`, `unknown` |
| `delivery_mode` | `push`, `pull`, or `physical` |
| `instructions` | Safe user-facing retrieval guidance |
| `portal_url` | Optional provider landing page without sensitive query data |
| `automation_state` | `not_applicable`, `candidate`, `available`, `configured`, `blocked` |
| `connector_type` / `connector_ref` | Non-secret external connector reference |
| `availability_delay` | Typical delay after the covered period |
| `last_success_at` | Acquisition health evidence |
| `browser_feasibility` | `not_assessed`, `likely`, `mfa_or_captcha`, `unsupported` |

OWL never stores passwords, tokens, cookies, MFA seeds, or credential-bearing URLs.
Connectors refer to an external secret store or n8n credential ID.

## Inference rules

### From Paperless

OWL may suggest:

- recurring, periodic, or irregular behavior from document dates;
- separate series from normalized titles, account hints, subject tags, and user corrections;
- dominant title conventions;
- common and missing tag families;
- document-type consistency; and
- probable acquisition channel from ingestion source or configured mail rule.

Each suggestion retains its basis and confidence. Too little or contradictory evidence
remains unknown rather than being forced into a category.

### From Tyrion

Tyrion can seed two candidate kinds:

- one `account_statement_candidate` per active, non-cash account; and
- one `recurring_document_candidate` per active recurring obligation.

Accounts use an opaque, consumer-scoped source reference. Two accounts at the same
institution remain two candidates even if their display names are similar. A recurring
obligation uses its stable recurring reference rather than merchant text.

These candidates do **not** prove:

- that a statement, invoice, bill, or receipt exists;
- its cadence or availability date;
- electronic delivery enrollment;
- a Paperless correspondent mapping; or
- that an absent document is missing.

A recurring expense may be documentless, and recurring income is not a bill. A transaction
may help reconcile an existing invoice or receipt but must not create an expected receipt.

## Tyrion integration contract

Introduce a private, pull-only, versioned `DocumentExpectationSignalsV1` projection
mediated by Tyrion or Mission Control:

```json
{
  "contractVersion": "1",
  "connectorRef": "opaque-connector",
  "sourceGeneration": "opaque-generation",
  "sourceAsOf": "2026-08-22T20:00:00Z",
  "completeness": "complete",
  "signals": [
    {
      "seriesRef": "consumer-scoped-opaque-ref",
      "kind": "accountStatementCandidate",
      "active": true,
      "displayHint": "Credit account ending 1234",
      "cadence": null,
      "nextExpectedDate": null,
      "confidence": 0.6,
      "basis": ["active_non_cash_account"]
    }
  ]
}
```

The projection excludes balances, transaction lists, notes, credentials, ownership, URLs,
document content, and raw account identifiers. OWL polls by generation and replaces each
bounded snapshot idempotently. Deactivating a source candidate does not delete a confirmed
OWL expectation; it creates a review finding.

## Relationship to existing statement models

This is a policy layer over the current statement implementation, not a parallel replacement:

- `ProviderCandidate` remains discovery evidence and is not durable policy.
- `StatementSeries` and its split/merge/reassign history remain the document grouping model.
- A statement `DocumentExpectation` binds to one `StatementSeries` and adds confirmed
  expectation, title, metadata, acquisition, and external-signal policy.
- `series_discriminator` reuses the confirmed series name/account hint rather than introducing
  a second grouping mechanism.
- Existing provider overrides migrate into the bound expectation where an unambiguous series
  exists; ambiguous rows enter review rather than being copied automatically.

The current display-name-derived `provider_key` remains a compatibility identifier only.
New webhook and dedup state should use `expectation_id` plus expected period. During migration,
`statement-found` may accept either identity, resolve a legacy `provider_key` to exactly one
expectation, and reject ambiguous resolution. Existing statement-series history is preserved.

## Review workflow

1. **Inventory:** rank Paperless correspondents by unreviewed state, metadata inconsistency,
   statement-like history, stale analysis, and unmatched external candidates.
2. **Inspect:** show representative Paperless documents, observed title/tag/type patterns,
   candidate series, cadence evidence, and known acquisition sources.
3. **Reconcile:** map a Tyrion candidate to an expectation, create a suggestion, leave it
   ambiguous, or mark it documentless/not applicable.
4. **Configure:** confirm expectation mode, cadence, title convention, metadata policy, and
   acquisition source.
5. **Preview:** identify existing violations and display exact title/tag/type changes,
   including representative title renders and missing template fields.
6. **Apply:** explicitly selected changes are patched through the shared Paperless client.
7. **Monitor:** only confirmed recurring/periodic expectations feed missing-document analysis.

The general document list opens in Paperless. OWL owns the profile and policy review because
the workflow depends on cross-document and cross-system evidence.

## Acquisition strategy

Use the least brittle channel available:

1. **Paperless mail rules:** preferred for emailed attachments and supported push delivery.
2. **Direct email/API connector:** a narrow provider connector orchestrated by n8n, with
   credentials held outside OWL.
3. **Linked storage or consume folder:** useful when another trusted system already downloads
   the file.
4. **Manual portal:** provide a safe portal link and retrieval instructions.
5. **Snail mail:** track expected physical delivery and manual scan/import.

A successful connector uploads through the Paperless API, lets Paperless perform normal OCR
and workflow assignment, and then calls OWL's existing `statement-found` endpoint with the
Paperless document ID. Acquisition must be idempotent and duplicate-safe before reporting
success.

Browser-local tools demonstrate that user-assisted MFA-compatible collection is possible,
but provider-specific browser flows are brittle. This phase records feasibility only.
Do not build a generic Playwright/Puppeteer framework until direct email/API work proves a
shared connector contract and at least two real providers require browser automation.

## Rollout

### Phase 1: Read-only review

- Add correspondent inventory and detail analysis.
- Generate title-template, tag, document-type, cadence, and acquisition suggestions.
- Show title-template coverage, exceptions, and representative rendered examples.
- Confirm profiles and expectations.
- Show findings and Paperless deep links; perform no writes.

### Phase 2: Explicit correction

- Preview title, tag, and document-type patches.
- Apply only user-selected changes.
- Reuse existing metadata-correction audit and undo conventions.

### Phase 3: Tyrion reconciliation

- Add the versioned candidate-signal projection.
- Reconcile account and recurring candidates.
- Detect likely multiple statement series per correspondent.
- Keep ambiguous mappings in review.

### Phase 4: Acquisition

- Configure Paperless mail rules and direct email/API connectors.
- Orchestrate with n8n and reuse `statement-found`.
- Track acquisition health and last success.
- Retain manual portal retrieval as the fallback.

## Non-goals

- Extending Paperless correspondent records with unsupported metadata.
- Automatic enforcement or unreviewed metadata writes.
- Treating every account or recurring transaction as a required document.
- Expecting a receipt for every transaction.
- Copying balances or raw transactions into OWL.
- Storing acquisition credentials in OWL.
- Building a generic browser scraper, graph database, or new workflow engine.
- Replacing Paperless document lists, metadata editing, mail ingestion, workflows, or matching.

## Implementation validation

- One correspondent can own multiple independent expectations and acquisition sources.
- An `any_of` animal-tag rule distinguishes a valid child tag from a parent-only assignment.
- Suggested and irregular expectations cannot emit missing-document alerts.
- Two accounts at one institution remain separate candidate series.
- A recurring transaction alone cannot create an invoice or receipt requirement.
- Source deactivation does not silently delete confirmed policy.
- A Paperless patch preview exactly matches the applied operation and audit record.
- Each confirmed title template renders deterministically for its expectation and rejects
  documents missing required fields.
- Legacy provider overrides and webhook keys resolve to at most one expectation.
- Acquisition retries cannot create duplicate Paperless documents or false success callbacks.
- APIs, logs, and profiles contain no connector credentials or sensitive URLs.
