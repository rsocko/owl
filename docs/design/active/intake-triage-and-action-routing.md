---
title: "Intake Triage and Action Routing"
sidebar_label: Intake and Action Routing
sidebar_position: 3
status: active
created: 2026-10-15
category: design
related:
  - "[Action Queue Design](../../modules/action-queue/design.md)"
  - "[Document Views and Metadata Quality](./document-views-and-metadata-quality.md)"
  - "[Paperless Metadata and Document Summary](./paperless-metadata-and-document-summary.md)"
  - "[OWL Architecture](../../architecture/index.md)"
---

# Intake Triage and Action Routing

## Decision

Paperless-ngx remains the freely editable document system of record. OWL
complements Paperless with a faster review workspace, domain analysis, and
action routing; it does not require users to stop editing through Paperless's
web UI, mobile clients, workflows, or API.

Metadata readiness and action assessment are separate decisions, but they do
not require separate pages or a rigid sequential workflow. OWL should present
them together when useful:

1. **Metadata readiness** asks whether the document is filed well enough to
   leave the Paperless Inbox.
2. **Action assessment** asks whether the document creates real-world work.

The same workspace may show both. Action analysis can run against the current
document while metadata is being reviewed. A relevant metadata or file change
marks the prior assessment stale and schedules a targeted re-analysis rather
than forcing every user through two complete passes.

Intake and Action Queue still have distinct primary jobs:

- **Intake** optimizes fast metadata cleanup and the decision to remove the
  document from the Paperless Inbox.
- **Action Queue** is the canonical place to review proposed action details and
  manage trusted actions through their lifecycle.

Intake may expose enough action detail to confirm an obvious proposal while
filing, but filing never implicitly confirms or dismisses an action.

## Product principles

1. **Paperless stays authoritative.** Direct Paperless edits are valid and
   expected. OWL observes, rebases on, and learns from them.
2. **One suggestion engine may serve multiple interfaces.** OWL should reuse
   Paperless's native ML and AI suggestion APIs instead of rebuilding routine
   title, tag, correspondent, document-type, storage-path, and date
   classification.
3. **Suggestions do not become locks.** Users may accept, edit, ignore, or
   replace any suggestion in OWL or Paperless.
4. **Automatic filing may graduate over time.** Existing low-risk Paperless
   matching may continue to apply metadata automatically. AI-backed fields may
   move from review-only to automatic only after measured accuracy supports it.
5. **No action is a current assessment, not permanent exclusion.** A document
   with no current task remains eligible for later relationship matching,
   re-analysis, and backfills.
6. **Mission Control receives trusted work only.** Confident or
   human-confirmed no-action assessments never become Mission Control tasks.

## Ownership

| Concern | Authority |
|---|---|
| Original and archived files, OCR content, native metadata | Paperless |
| Current title, tags, correspondent, document type, date, storage path | Paperless |
| Native ML and AI filing suggestions | Paperless |
| Domain-extracted custom fields and governed writeback | OWL metadata registry |
| Action assessments, confidence, corrections, and lifecycle | OWL |
| Cross-document relationships and reconciliation eligibility | OWL |
| Cross-system presentation of trusted actions | Mission Control |

Paperless may assign fixed custom-field values through ordinary workflows and
accept custom-field values through its REST API. Its native AI classifier does
not currently suggest dynamic custom-field values. OWL remains the only
automated writer for domain-derived values such as amount, due date, account or
reference identifiers, claim facts, and relationship summaries.

## Paperless suggestion integration

OWL's backend may call the Paperless per-document endpoints:

```http
GET /api/documents/{id}/suggestions/
GET /api/documents/{id}/ai_suggestions/
```

The first returns traditional Paperless suggestions. The second returns native
AI suggestions when Paperless AI is enabled. OWL resolves returned IDs to
display objects, combines both sources with current metadata, and presents the
result without hiding values already applied by Paperless.

Accepted changes use the ordinary Paperless document API. Before writing, OWL
must compare the current Paperless `modified` value or an equivalent source
fingerprint with the version loaded into the workspace. If Paperless changed
underneath the user, OWL refreshes and shows the conflict rather than silently
overwriting a mobile, web, workflow, or API edit.

Paperless's native suggestion UI remains available. OWL does not disable it.
Deployments may also retain existing automatic non-AI matching. Automatic AI
application should initially remain off or limited to explicitly approved
low-risk fields so two unattended writers do not compete.

## Unified review workspace

The target OWL experience is one document-focused workspace, not two mandatory
screens. It contains:

- a document preview and shared `DocumentSummary`;
- current Paperless metadata;
- Paperless ML and AI suggestions with provenance;
- editable proposed values;
- the latest OWL action assessment and its freshness;
- any extracted domain facts or relationship evidence; and
- explicit controls for saving, analyzing, filing, and correcting.

The workspace may use separate filing and action-detail tabs or sections to
keep the primary filing CTA visible. This is presentation hierarchy, not a
required two-stage process. A user may clean up the Inbox quickly and defer a
proposed action to Action Queue, or confirm the action and file the document in
one pass.

The workspace supports these states:

| State | Behavior |
|---|---|
| OCR or source still changing | Show progress; do not present analysis as final |
| Metadata review pending, analysis absent | Allow metadata edits and **Analyze now** |
| Metadata review pending, analysis current | Show filing and action decisions together |
| Metadata changed after analysis | Mark assessment stale and queue a debounced rerun |
| Action assessment uncertain | Route to Needs Review without blocking metadata edits |
| Metadata ready, action trusted | Remove Inbox independently of action lifecycle |

Analysis may start automatically after OCR stabilizes. It should not wait for
Inbox removal. To control cost and churn, OWL coalesces rapid metadata changes
and re-runs only after the saved source fingerprint changes.

### OCR quality review ownership

Review-worthy OCR assessments are first-class, persisted Needs Review items.
The OCR queue is selected with the `ocr_quality_review` review type and uses the
same OCR summary and focus inspector as Inbox and Action Review. Resolving the
review records trust for that OCR review item only; filing, action completion,
and candidate acceptance remain separate explicit operations. OCR items are
excluded from generic triage bulk acceptance.

The OCR Quality page is not an operational queue. It remains the corpus
analytics and administration surface for distributions, calibration,
inventory/scoring runs, scorer versions, and assessment freshness.

## Source fingerprint and re-analysis

OWL tracks a processing cursor for discovery and a separate source fingerprint
for analysis validity.

The discovery cursor uses Paperless document ID and date added, with a bounded
overlap window. The action-analysis fingerprint includes only inputs that can
materially change the result:

- effective file version or checksum;
- effective OCR/content version;
- title;
- correspondent;
- document type;
- created/document date;
- relevant tags;
- governed custom fields used by the analyzer; and
- analyzer and prompt/rules version.

Re-analysis occurs when:

- a new document becomes readable;
- the fingerprint changes;
- a user selects **Analyze now** or **Re-evaluate**;
- an analyzer version changes; or
- a backfill explicitly includes the document.

Intake Review marks a material metadata save stale immediately, writes one
durable per-document re-analysis request, and debounces rapid edits into the
latest source fingerprint. Explicit **Analyze** and **Re-evaluate** requests
bypass the delay. A generation check prevents an older completion from
resolving a newer request, while Action Queue's existing protected-action
matching preserves human-confirmed lifecycle state. The workspace exposes
fresh, stale, reanalyzing, failed, and not-yet-analyzed states together with
the analyzer version, evaluated source fingerprint, and evaluation time;
metadata editing remains available in every state.

External Paperless edits are therefore complementary rather than conflicting.
OWL sees the changed fingerprint and re-evaluates without requiring the edit to
originate in OWL.

Incremental reconciliation persists separate added and modified high-water
tuples plus an in-progress page cursor. Each scan replays a bounded overlap
window, hashes only material analyzer inputs, and retains failed targeted runs
as privacy-safe pending records so restarts do not lose them. The first scan
starts at the current time; historical processing requires an explicit bounded
date range, ID range, saved view, or document selection. Paperless webhook
delivery remains a complementary path tracked by #81; until that path exists,
the reconciliation status reports the dependency rather than claiming missed
webhook recovery.

## Discovery and analysis scope

OWL separates cheap document discovery from the scope of each analyzer:

- **Global discovery** observes every document added or modified after the
  reconciliation cursor starts. It does not scan prior years on first use.
- **Action and payment tracking** evaluates documents in the configured tags
  or saved view, with an 18-month (`548` day) default lookback. Existing active
  actions remain eligible outside that window so source changes cannot strand
  ongoing work.
- **EOB and bill matching** uses a 24-month (`730` day) initial lookback, then
  continues incrementally from the last successful run.
- **Statement recurrence and anomaly analysis** uses document dates and an
  18-month (`548` day) default lookback.

The Paperless `Inbox` tag is an intake state, not the sole analysis boundary.
Removing it during filing is excluded from the global fingerprint so that the
workflow transition alone does not trigger broad re-analysis. For Action Queue,
the recommended source is a dedicated Paperless saved view such as
`OWL - Action & Payment Candidates`, combining the correspondents, document
types, tags, and other Paperless filters appropriate for the deployment.

Operators can still run explicit bounded backfills by document selection, date
range, ID range, or saved view. Those backfills are deliberate exceptions to
the forward-only discovery bootstrap and should be used when older history is
needed for a specific analysis.

## Filing and no-action decisions

Leaving the Paperless Inbox means the metadata is sufficiently filed. It does
not mean OWL will never inspect the document again.

OWL should offer a combined convenience action in the intake workspace:

**File — no action needed**

This operation:

1. saves any pending metadata edits;
2. removes only configured intake tags such as `Inbox`;
3. records a human-confirmed no-action assessment for the current analysis
   fingerprint; and
4. advances to the next intake document.

It does not:

- delete or archive the Paperless document;
- prevent future relationship scans;
- suppress re-analysis after a source change;
- prevent a later document from linking to it; or
- create a Mission Control task.

The workspace also supports these independent outcomes:

- **Save & file** saves pending metadata, removes configured intake tags, and
  advances to the next document. Any proposed action remains available for
  review in Action Queue; filing does not confirm or dismiss it.
- **Confirm action & file** confirms the selected action assessment, saves
  metadata, removes configured intake tags, and advances. Each confirmed action
  retains its own lifecycle.
- **Defer & next** discards pending metadata edits, retains the Inbox tag, and
  advances to the next document. It creates no trusted action or Mission
  Control work by itself.

When OWL is confident that no action exists, it may record the same disposition
automatically. Lower-confidence no-action predictions go to Needs Review, where
the user can confirm **No action needed**, choose an action type, or request
re-evaluation.

## FILE and ARCHIVE semantics

`FILE` and `ARCHIVE` are legacy Action Queue action types:

- **FILE** is deprecated as a newly inferred real-world action. Filing is an
  intake operation owned by metadata readiness. Existing FILE records remain
  supported for compatibility and explicit legacy cleanup.
- **ARCHIVE** is interpreted as an informational no-action outcome for new
  analysis. Existing ARCHIVE records remain available through the explicit
  legacy File / Archive filter, not the default Action Queue or Mission Control.
- If a real physical or procedural archival task exists, such as retaining an
  original or sending records to another system, OWL creates a specific `TASK`
  or `SHARE` action rather than an ambiguous ARCHIVE action.

The analyzer does not emit either legacy type. If an older or non-conforming
model response returns FILE or ARCHIVE, OWL removes that classification before
persistence and records no action unless another explicit real-world action is
present.

## No-action review and learning

No-action predictions need a readily available audit surface while thresholds
are being tuned. OWL provides a **No action** view containing:

- document summary and preview link;
- predicted disposition (`no_action` or `archive`);
- confidence and concise reasoning;
- whether the result was automatic or human-confirmed;
- analysis and source-fingerprint timestamps;
- related-document or reconciliation status; and
- **Create action**, **Send to Needs Review**, and **Re-evaluate** controls.

High-confidence results may resolve automatically. Medium or low confidence
never disappears silently; it routes to Needs Review. Periodic sampling of
recent automatic no-action results provides quality evidence without making
every informational document a user task.

Human decisions become training and evaluation data. A human-confirmed
no-action result protects the current assessment from being replaced by an
unchanged automated rerun, but a changed source fingerprint or new explicit
evidence may reopen evaluation.

## Relationships continue after no action

Action disposition and relationship eligibility are independent.

A receipt may have no current action and still participate in future matching.
When a later invoice, credit-card statement, reimbursement record, or other
candidate arrives, OWL runs the relevant relationship or reconciliation pass
against eligible historical documents.

Examples:

- A receipt can later become payment evidence for an obligation.
- A reminder can later attach to an earlier invoice.
- A credit can later reconcile against a purchase or statement.
- A new EOB can later match an existing provider bill.

These events do not automatically turn every related document into a task.
They update the relationship graph, may suggest completion, or create Needs
Review when the match is ambiguous.

Transaction-level receipt-to-credit-card-statement reconciliation is a
separate capability from the Action Queue. The intake design must not preclude
it, but ordinary no-action routing does not claim that it is already
implemented.

## Action routing

Each analysis produces zero or more independent action assessments:

```text
trusted actionable
    -> Action Queue
    -> Mission Control

plausible action needing detail confirmation
    -> Action Queue proposed state
    -> not Mission Control until trusted

uncertain whether the document is actionable or interpretable
    -> Needs Review

confident or human-confirmed no action
    -> No action history
    -> never Mission Control
```

One Paperless document may produce multiple OWL actions. Each action retains
its own lifecycle while the document keeps one aggregate projected status.
Metadata readiness is not an additional action and does not wait for all
real-world actions to complete. Filing a document may therefore leave one or
more proposed actions awaiting review in Action Queue.

## Tagvico position

Tagvico is not a required service in this flow. Its current product direction
centers on household Action Cases, assignments, approval roles, chat, and
family bots, which overlap with OWL and Mission Control without matching OWL's
multi-action and domain-analysis model.

OWL may study and adapt appropriately licensed implementation patterns such as
durable approvals, deterministic execution, restore snapshots, retries,
processing history, and one-writer safeguards. OWL does not depend on
Tagvico's processed tag, review queue, action store, or OCR pipeline.

## Delivery sequence

1. Replace Inbox-only Action Queue discovery with the incremental
   added/modified cursor and source fingerprint.
2. Add a Paperless suggestion adapter for native and AI suggestion endpoints.
3. Build the unified intake workspace using the existing preview and
   `DocumentSummary` contracts.
4. Add optimistic Paperless writeback and conflict refresh.
5. Add no-action history and confidence sampling.
6. Deprecate new FILE inference and route ordinary ARCHIVE results to
   no-action history.
7. Add relationship-triggered re-evaluation without reopening unrelated
   actions.
