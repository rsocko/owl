# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

OWL primarily serves a single household operator managing sensitive personal
documents in a self-hosted homelab. The operator regularly handles financial,
medical, insurance, tax, account, and administrative records and needs to know:

- whether each document is filed correctly;
- whether it creates real-world work;
- whether it relates to another document or obligation;
- whether an expected document is missing; and
- why OWL reached a conclusion before trusting or correcting it.

The same person may work directly in OWL, edit documents in Paperless-ngx, or
review trusted cross-system work in Mission Control. OWL must treat all three
paths as valid parts of one operating environment rather than assuming that
every decision originates in OWL.

In Action Review, the operator's primary job is fast sequential triage:
understand the source evidence, complete or defer the real-world work, and move
to the next item without losing document context. In Needs Review, the job is
to supply judgment where OWL cannot yet safely decide what a document or
relationship means.

## Product Purpose

OWL turns a Paperless-ngx archive into a reviewable document-intelligence
workspace. It organizes new documents, watches for missing or changing
evidence, learns from repeated patterns and corrections, and helps the operator
decide what to believe and what to do next.

OWL succeeds when the operator can:

1. clear document intake without losing control of Paperless metadata;
2. identify real obligations, deadlines, gaps, and exceptions before they are
   missed;
3. resolve uncertain analysis with the source evidence in view;
4. preserve and understand relationships between documents over time;
5. reverse or correct automated decisions without losing history; and
6. route only trusted, actionable work into Mission Control.

Success is not the number of classifications, alerts, or tasks OWL creates. It
is the reduction of overlooked obligations and repetitive review while keeping
important decisions explainable, correctable, and grounded in preserved
evidence.

## Positioning

OWL is the governed intelligence and review layer between a document archive
and a cross-system action hub.

Unlike a document-management system, OWL does not own the archive or replace
native filing and browsing. Unlike a task dashboard, it does not treat every
document or uncertain inference as work. OWL specializes in evidence-rich
review, domain extraction, confidence-gated decisions, cross-document
relationships, reconciliation, and the promotion of trusted findings into
actionable work.

Its distinctive mechanism is a human-correctable evidence chain:

**source document → extracted facts → confidence and reasons → review or
trusted result → relationship or action → downstream projection**

Each step remains inspectable and can be corrected without rewriting the
original source or pretending that an uncertain result is final.

## Operating Context

OWL runs alongside Paperless-ngx and Mission Control in a private, self-hosted
environment. It is a full-stack application with its own focused review UI and
an API consumed by Mission Control.

Documents may arrive through Paperless-native ingestion or optional upstream
acquisition tools. The operator may browse and edit the archive through
Paperless web, mobile, workflow, API, or third-party clients. OWL observes those
changes, detects when prior analysis is stale, and re-evaluates relevant
results instead of requiring exclusive control.

Typical work includes:

- reviewing Paperless Inbox metadata and suggestions;
- deciding whether a document creates an action;
- completing, deferring, declining, or correcting trusted work in Action
  Review while keeping its source visible;
- accepting, correcting, rejecting, matching, re-evaluating, or deferring
  uncertain beliefs in Needs Review;
- resolving uncertain classifications, matches, duplicates, and groupings;
- matching medical EOBs to bills and reviewing discrepancies;
- tracking recurring statements and investigating gaps;
- connecting reminders, revisions, receipts, payments, and other supporting
  evidence to an obligation;
- reviewing OCR quality or candidate improvements before changing a searchable
  document version; and
- reconciling trusted OWL actions through Mission Control.

The interface supports dense, repeated review work. It should make the next
decision fast without hiding provenance, confidence, exceptions, or source
evidence.

## Core User Jobs

### File documents with confidence

Review current Paperless metadata, native suggestions, and OWL-derived fields;
make corrections; and decide when a document is ready to leave the Paperless
Inbox.

### Determine what requires action

Distinguish informational records from real-world work such as paying,
responding, signing, scheduling, sharing, or reviewing. Confirm, correct,
snooze, complete, or dismiss actions without confusing filing with action
lifecycle.

### Resolve uncertainty

See why OWL is unsure, compare source evidence, and make an explicit decision.
Needs Review determines what OWL should believe; Action Queue tracks what the
operator should do.

### Connect evidence across documents

Relate documents that follow, supersede, support, or belong to the same
sequence. Preserve every source document while building an understandable
timeline of an obligation or event.

### Detect absence and change

Identify missing recurring documents, later notices, altered source content,
stale analysis, OCR quality problems, and other exceptions that are not visible
from a single document in isolation.

### Route trusted work

Promote only confident or human-confirmed actions to Mission Control. Keep
uncertain interpretation, filing decisions, and no-action history in OWL.

## Product Boundaries

### Paperless-ngx owns

- original and archived files;
- OCR content and document versions;
- native metadata, including title, tags, correspondent, document type, date,
  and storage path;
- native browsing, reading, search, ingestion, and filing suggestions; and
- direct user, workflow, mobile, and API edits to the archive.

OWL must not require users to stop editing through Paperless or a compatible
client. It must detect conflicting or newer Paperless state before writeback
and refresh rather than silently overwrite it.

### OWL owns

- document-focused review workflows;
- domain-extracted facts and governed custom-field writeback;
- action assessments, readiness, confidence, corrections, and lifecycle;
- Needs Review queues for uncertain interpretation;
- cross-document relationships and obligation consolidation;
- reconciliation eligibility and evidence-backed decisions;
- statement expectations, gaps, matching, and exception analysis;
- reviewable OCR quality assessment and candidate decisions; and
- the authoritative record of why an OWL decision was made or changed.

OWL may project searchable summaries and governed fields into Paperless, but
projection does not transfer authority for OWL relationships or decision
history.

### Mission Control owns

- cross-system presentation and prioritization of trusted actionable work;
- aggregation of OWL actions with work from other services; and
- downstream task-oriented views and coordination.

Mission Control must not become the authority for OWL classification,
document readiness, evidence, correction history, or relationship state.

### Optional surrounding tools

Acquisition tools such as PaperPull may feed documents into Paperless and
should preserve provider provenance when available. Browsing clients such as
Papers may provide alternate ways to read the Paperless archive. Reference
systems such as Suchi, Lemmary, paperless-gpt, and OCRAgent provide useful
workflow evidence but are not default OWL runtime dependencies or competing
authorities.

The durable boundary is:

> Paperless owns the archive and native metadata; optional acquisition and
> browsing clients may surround it; OWL owns evidence-rich review, governed
> enrichment, relationships, and action assessment; Mission Control receives
> trusted actionable work.

## Capabilities and Constraints

- OWL is privacy-sensitive by default. Local processing is preferred for
  document content, and any external AI access must be explicit, bounded, and
  appropriate to the sensitivity of the source.
- Paperless remains freely editable. OWL must rebase analysis and writeback on
  current source state rather than assume exclusive ownership.
- Suggestions are proposals, not locks. The operator can accept, edit, ignore,
  replace, or reverse them.
- Uncertain results do not silently progress. They remain reviewable until a
  confidence policy or a human decision makes them trusted.
- Filing readiness, action readiness, relationship eligibility, and no-action
  disposition are separate state dimensions even when one workspace presents
  them together.
- A no-action result is a current assessment, not permanent exclusion from
  later matching, relationship discovery, reconciliation, or re-analysis.
- Original documents and meaningful variants remain preserved. Duplicate,
  supersession, and relationship decisions must not destroy evidence.
- Automatic writeback and unattended progression must be scoped by risk,
  measured accuracy, provenance, and a reversible audit trail.
- Partial success must remain visible. A durable OWL result must not be
  presented as failed merely because a Paperless or Mission Control projection
  needs retry, and projection failure must never be reported as success.
- OWL is optimized for one household operator, but data models and audit
  history must not depend on hidden memory or implementation knowledge to be
  understandable later.
- Inbox Review, Action Review, and Needs Review share a document-workspace
  shell with persistent source evidence, a queue, and focused Metadata,
  Actions, Related, and OCR surfaces. Each workflow must retain its distinct
  default decision surface and queue semantics.
- Action Review optimizes for rapid sequential work and opens actionable
  details by default. Needs Review exposes review type, reason, confidence,
  age, and priority and treats confirmation as establishing trust rather than
  completing a task.
- Action and review lifecycles remain explicit and auditable. Supported
  outcomes include completion, snooze, dismissal, false-positive correction,
  reopening, confirmation, rejection, re-evaluation, matching, and deferral
  where appropriate to the record type.

## Brand Commitments

The product name is **OWL**, expanded as **Organize. Watch. Learn.**

The brand expresses quiet competence: calm, precise, watchful, and trustworthy
with sensitive financial and medical information. The owl metaphor should be
felt through identity and behavior rather than forced puns or novelty copy.
Usability and semantic clarity take priority over decorative branding,
especially in dense review workflows and status communication.

Detailed identity rationale and current visual constraints remain in
[OWL Branding Rationale](docs/design/active/branding-rationale.md).

## Evidence on Hand

The repository contains working modules, tests, user guides, design contracts,
and interactive mockups. Future product and UX work should use real module
behavior and these authorities rather than inventing capabilities or claims.

Key evidence includes:

- current review implementations in
  `frontend/src/pages/IntakeReview.tsx`,
  `frontend/src/pages/ActionWorkspace.tsx`, and
  `frontend/src/pages/TriageQueue.tsx`;
- current visual foundations in `frontend/src/styles/theme.css`,
  `frontend/src/styles/intake-review.css`, and
  `frontend/src/styles/document-workspace.css`;
- interactive workspace proposals under `mockups/intake-review/` and
  `mockups/action-workspace/`;
- [Architecture Overview](docs/architecture/index.md) for current application
  boundaries, modules, and data flow;
- [Intake Triage and Action Routing](docs/design/active/intake-triage-and-action-routing.md)
  for Paperless authority, intake, no-action, re-analysis, and trusted-action
  rules;
- [Document Relationships](docs/design/active/document-relationships.md) for
  relationship evidence, reversibility, obligation consolidation, and
  projection behavior;
- [Mission Control Integration](docs/guide/mission-control-integration.md) for
  connector trust gates and lifecycle ownership;
- [Feature Roadmap](docs/feature-roadmap.md) for the distinction between built
  and planned capabilities;
- [Shared Scenarios](docs/modules/shared/overview-scenarios.md) for the current
  statement, EOB, action, alert, and configuration scenarios;
- [Module Reference](docs/modules/shared/index.md) and the module-specific
  design documents under `docs/modules/` for detailed behavior; and
- [Paperless Ecosystem Strategy](docs/research/paperless-ecosystem-strategy.md)
  for external reference evidence, subject to the superseding decisions linked
  from that document.

Feature-level technical designs remain authoritative within their scope. This
document supplies product-level priorities and boundaries; it does not replace
their algorithms, API contracts, schemas, or implementation plans.

## Product Principles

### 1. Preserve user authority

The operator can always inspect, correct, defer, or reverse an OWL decision.
Paperless edits made outside OWL are legitimate and must not be silently
overwritten.

### 2. Show the evidence, not just the answer

Important recommendations expose source documents, extracted facts,
provenance, confidence, freshness, reason codes, and meaningful alternatives.
The UI may summarize evidence, but it must not obscure the basis of a decision.

### 3. Gate automation by trust and consequence

High-confidence, low-risk, measured behavior may progress automatically.
Uncertain, novel, conflicting, sensitive, or high-consequence results wait for
review. Automation earns expanded authority through observed accuracy rather
than aspiration.

### 4. Make every material change reviewable and reversible

Preserve originals, use versioned or soft-removal models where appropriate,
record before-and-after state, and keep retryable projections separate from
authoritative decisions. A correction should restore a coherent prior state
without erasing why the earlier decision existed.

### 5. Separate belief, filing, and action

What OWL believes about a document, whether Paperless metadata is ready, and
what the operator should do are related but independent decisions. The product
may streamline them in one workspace without collapsing their lifecycles.
Action Review and Needs Review should remain visually connected through that
workspace while staying semantically distinct.

### 6. Use density in service of decisions

OWL is a repeated-review tool, not a presentation dashboard. Favor compact,
scannable information with a clear primary decision, visible exceptions, and
progressive access to deeper evidence. Do not trade clarity for empty space or
hide critical context to make the interface appear simple.

### 7. Fail explicitly and preserve partial truth

Stale analysis, conflicts, unavailable sources, writeback errors, and
projection failures must have distinct visible states. Never turn uncertainty
or partial failure into a success-shaped fallback.

## Success Measures

Product and UX changes should improve one or more of these observable outcomes:

- fewer actionable documents or deadlines are missed;
- less time is spent repeatedly interpreting the same document evidence;
- a greater share of routine, low-risk results can progress without review
  while sampled quality remains acceptable;
- uncertain and conflicting results are routed to review rather than leaking
  into trusted action views;
- corrections reduce repeated mistakes and retain an understandable audit
  trail;
- Paperless, OWL, and Mission Control remain consistent after edits, retries,
  partial failures, and re-analysis; and
- the operator can explain why an action, relationship, or no-action decision
  exists without reading implementation code.

Exact thresholds are intentionally not fixed here. Each module must establish
its own baseline, consequence-aware target, and sampling or evaluation method
before expanding unattended automation.

## Accessibility & Inclusion

OWL must support WCAG AA contrast for text and controls, keyboard-operable
review workflows, visible focus, and status communication that does not depend
on color alone. Dense views must remain zoomable and responsive without hiding
the evidence or decision controls required to complete the workflow.

Sensitive and domain-specific language should be direct and unambiguous.
Automation must not imply certainty it has not earned, and error states must
tell the operator what is known, what failed, and what can be done next.

## Unresolved Product Decisions

Later UX and product work must settle these decisions with evidence rather than
allowing individual screens to choose independently:

1. **Primary navigation model:** determine the durable grouping and hierarchy
   for Intake, Action Queue, Needs Review, relationships, statements, EOBs,
   OCR quality, insights, and administration.
2. **OWL versus Mission Control interaction split:** define which trusted
   action operations belong in both products and which specialist review
   workflows must deep-link back to OWL.
3. **Automation graduation policy:** define the evaluation sample, accuracy
   measures, risk tiers, and rollback triggers required before each automated
   metadata, relationship, no-action, or action decision can bypass review.
4. **Privacy tiers for AI processing:** define which document classes and data
   fields may use local models, bounded external services, or no AI processing.
5. **Review workload controls:** define sampling, batching, prioritization, and
   escalation rules that keep confidence-gated review useful as corpus size and
   automation coverage grow.
6. **Success baselines:** instrument current intake time, correction rates,
   missed-obligation incidents, review volume, and projection failures so
   future UX changes can be evaluated against real household use.
