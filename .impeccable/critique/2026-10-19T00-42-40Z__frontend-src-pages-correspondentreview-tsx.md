---
target: Correspondent Review
total_score: 21
max_score: 40
na_heuristics:
p0_count: 0
p1_count: 3
timestamp: 2026-10-19T00-42-40Z
slug: frontend-src-pages-correspondentreview-tsx
---
Method: dual-agent (A: 1b20f51c-76fe-40a7-9546-b67336bb1a58 · B: 61daa5b8-85b7-4712-bb95-4f474bb6894e)

# Correspondent Review critique

## Design Health Score

| # | Heuristic | Score | Key issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | Loading, freshness, badges, and toasts exist, but review completion is not summarized. |
| 2 | Match System / Real World | 2 | Expectation, monitoring, calendar-rule, and policy terminology require model knowledge. |
| 3 | User Control and Freedom | 2 | Some corrections support undo, but lifecycle actions and draft preservation are uneven. |
| 4 | Consistency and Standards | 2 | Correspondent Review, Statements, series detail, and Document Workspace use different shells and lifecycle language. |
| 5 | Error Prevention | 2 | Validation is substantial, but review can complete with unresolved suggestions. |
| 6 | Recognition Rather Than Recall | 2 | Users must cross-reference series detail, Paperless, and reconciliation state. |
| 7 | Flexibility and Efficiency | 2 | No strong keyboard, next-item, batch, preset, or reusable-template path is evident. |
| 8 | Aesthetic and Minimalist Design | 1 | Several workflows and too many policy dimensions are visible simultaneously. |
| 9 | Error Recovery | 3 | Errors, retries, previews, and some undo paths are strong. |
| 10 | Help and Documentation | 2 | Microcopy exists, but the profile/series/expectation/source model is not explained succinctly. |
| **Total** | | **21/40** | **Needs significant refinement** |

## Design Specificity Verdict

The domain model is strongly specific to OWL, but the interaction architecture feels like a dense administration page. External-signal reconciliation, correspondent lifecycle, document-series review, expectation configuration, historical metadata correction, and acquisition setup compete inside one route.

The deterministic detector returned zero rule findings for `frontend/src/pages/CorrespondentReview.tsx`. That does not contradict the design assessment: the primary problems are information architecture, progressive disclosure, terminology, and workflow boundaries rather than detectable anti-patterns. Browser rendering and overlays were unavailable, so visual conclusions are source-derived.

## Overall Impression

The page has trustworthy evidence and correction mechanics, but it asks the user to understand OWL's internal model before they can complete a clear job. The biggest opportunity is to reorganize around the user's sequence—identify a source, inspect its document series, decide what should be expected, preview the policy, and complete review—using the newer Document Workspace composition.

## What's Working

1. Authority boundaries between Paperless evidence, OWL policy, and Tyrion signals are explicit.
2. Exact previews, selected metadata application, confidence evidence, and undo support make sensitive changes trustworthy.
3. Search, prioritized inventory, status grouping, sticky regions, and responsive collapse provide a credible operational foundation.

## Priority Issues

### P1 — Multiple workspaces are stacked into one page

Deployment-wide Tyrion reconciliation appears above correspondent-specific review, while profile lifecycle and expectation policy share the same detail flow.

**Fix:** Split the top-level experience into queue modes: Needs setup, External signals, Monitored, Exceptions, and Audit. External reconciliation should not dominate every correspondent-review visit.

### P1 — The source evidence needed for decisions is not central

Users see counts and configuration forms but not enough representative documents or series context.

**Fix:** Reuse the Document Workspace composition: left queue, center representative document/timeline evidence, right focused decision panel.

### P1 — Expectation creation is an expert configuration form

Cadence, monitoring, calendar rules, importance, availability, escalation, metadata, title, and acquisition are presented too close together.

**Fix:** Guide the decision through: What document is this? Should it be expected again? When is it missing? How should it be filed? Where does it come from? Put offsets, escalation intervals, forbidden tags, and title-template internals under Advanced policy.

### P2 — Statement grouping and expectation policy appear to overlap

Statements discovers series and missing periods; Statement Series Detail curates membership; Correspondent Review establishes durable expectations. The boundaries are valid but not visible.

**Fix:** Name the responsibilities explicitly: Series editor = which documents belong together; Expectation editor = whether and how the series is monitored; Coverage = what is upcoming or missing. Link these surfaces rather than duplicating controls.

### P2 — Completion and lifecycle actions lack a confident ending

Mark reviewed, Ignore, and Retire sit near each other, and unresolved suggestions can weaken the meaning of completion.

**Fix:** Use Complete review with a summary, require resolution or acknowledgement of remaining suggestions, move Ignore/Retire to overflow, preserve drafts, and add Save and next.

## Persona Red Flags

**First-time operator:** Profile, candidate series, expectation, dismissed, not expected, ignored, and retired are too easy to conflate.

**Power user:** Repeated review lacks keyboard navigation, policy templates, batch resolution, and a strong next-item path.

**Accessibility-dependent user:** Very small text, nested sticky regions, independent scrolling, and long responsive forms require browser validation at zoom and keyboard-only operation.

## Recommended Information Architecture

- **Document Workspace:** one-document Inbox, Action, Needs Review, metadata, OCR, and relationship work.
- **Sources & Expectations:** cross-document source identity, series, expectation policy, acquisition, external reconciliation, and history.
- **Statement Series Detail:** authoritative statement membership, split, merge, and rename.
- **Document Coverage:** upcoming, missing, fulfilled, and disabled monitoring across statements, invoices, tax documents, renewals, records, and one-off expected documents.

Recommended source-review flow:

1. Select a prioritized source.
2. Inspect representative documents and detected series.
3. Correct series membership if needed.
4. Accept, modify, dismiss, or create an expectation.
5. Review the resulting monitoring and filing policy in plain language.
6. Preview and optionally apply historical corrections.
7. Complete review and advance.
8. Monitor the expectation in Document Coverage.

## Naming

- Rename **Correspondent Review** to **Sources & Expectations**. `Document Sources` is a shorter alternative.
- Rename top-level **Statements** to **Document Coverage** and retain Statements as a filter/category.
- Keep **Statement Series** for the statement-specific grouping editor.
- Avoid **Recurring Documents** as the umbrella because the model also includes periodic, one-off, irregular, and explicitly not-expected policy.

## Mockups

The repository contains `mockups/triage-correction/statement-series-detail.html`, but no dedicated Correspondent Review mockup was found. The existing statement mockup predates the shared Document Workspace visual language.

Create a connected three-screen prototype rather than lightly revising the legacy mockup:

1. Sources & Expectations queue with central evidence preview.
2. Guided expectation setup with plain-language policy summary and advanced disclosure.
3. Document Coverage with a Statements filter.

Then revise Statement Series Detail to look like a linked or embedded grouping sub-workflow.

## Questions to Consider

- If the durable object is the expectation, should Paperless correspondents remain the top-level navigation object?
- Can the calendar UI derive recurring versus periodic instead of asking users to understand both terms?
- Is external reconciliation frequent enough to occupy the top of every visit?
- What is the smallest policy summary needed before safely choosing Monitor this document series?
