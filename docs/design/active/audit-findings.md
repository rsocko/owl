---
title: "OWL Audit Findings"
sidebar_label: Audit Findings
sidebar_position: 4
status: active
created: 2026-07-26
---

# OWL — Audit Findings & Triage

## Executive Summary

A combined UX and architecture audit of the OWL Document Intelligence Hub reveals **12 critical UX issues** and **5 critical architecture gaps** that undermine the platform's core value proposition of unified document intelligence. The most impactful problems center on fragmented EOB workflows (3 pages for one task), unimplemented bulk operations in the Action Queue, and a completely missing OCR quality pipeline despite extensive design documentation. Addressing Priority 1–2 items first will resolve the most user-facing friction and close the gap between documented capabilities and actual implementation.

## Priority 1: Critical UX Issues

| ID | Area | Issue | Impact | Suggested Fix | Effort |
|----|------|-------|--------|---------------|--------|
| [UX-01](https://github.com/rsocko/ideation/issues/999) | EOB Matching | 3 separate pages for one workflow; unclear entry points across EobDashboard, EobUnmatched, EobMatchReview, ManualMatchSearch | Users abandon matching flow; ManualMatchSearch is a dead-end with no back nav | Consolidate into single EOB Matching workspace with tabbed sub-views and persistent breadcrumb nav | L |
| [UX-02](https://github.com/rsocko/ideation/issues/1001) | EOB Matching | Overlapping filter controls — tabs AND type dropdown both filter by document type (192 state combos) | Contradictory filter states confuse users; redundant combinations produce empty results | Remove type dropdown; promote tabs as sole type filter; add clear "active filters" pill bar | M |
| [UX-03](https://github.com/rsocko/ideation/issues/1000) | EOB Matching | Inconsistent confirm/reject flows — some auto-confirm, others open modals | Users unsure when action is final; accidental confirms on high-stakes matches | Standardize: all destructive/irreversible actions require modal confirmation; all reversible actions auto-confirm with undo toast | M |
| [UX-04](https://github.com/rsocko/ideation/issues/1002) | Navigation | 12-link horizontal nav overflows on <1400px screens | Nav items wrap or become inaccessible on smaller displays; broken layout | Group into 4–5 top-level categories with dropdown sub-menus; add responsive hamburger at breakpoint | M |
| [UX-05](https://github.com/rsocko/ideation/issues/1003) | Navigation | No consistent 'back' affordance — ManualMatchSearch has none | Users use browser back button; lose unsaved state; disorientation in deep pages | Add global breadcrumb component; ensure every sub-page has contextual back link | S |
| [UX-06](https://github.com/rsocko/ideation/issues/1004) | Action Queue | No 'inbox zero' visual goal — stats show counts but no progress indicator | No sense of completion; reduces motivation for queue processing | Add progress bar showing "X of Y resolved today"; empty-state celebration when queue hits zero | S |
| [UX-07](https://github.com/rsocko/ideation/issues/1006) | Action Queue | Detail panel loses state when filters change | Selected action deselects unexpectedly; users lose context mid-review | Preserve selection if item still exists in filtered set; show "item no longer in view" banner otherwise | S |
| [UX-08](https://github.com/rsocko/ideation/issues/1005) | Statements | Two action buttons ('Run discovery', 'Run recommendations') with no guidance | Users don't know which to run first or what each does; leads to errors or inaction | Add sequential step indicator (1→2); include tooltip/description for each button; disable step 2 until step 1 completes | S |
| [UX-09](https://github.com/rsocko/ideation/issues/1007) | Triage | Detail panel renders 5 content types with no context breadcrumb | Users can't identify which type they're viewing; cognitive overload switching between types | Add type badge/icon header to detail panel; include breadcrumb showing Queue → Type → Item | S |
| [UX-10](https://github.com/rsocko/ideation/issues/1008) | Cross-cutting | No loading states for bulk actions — buttons disable but items show no feedback | Users re-click or navigate away thinking action failed; potential duplicate submissions | Add inline spinner per affected item; show progress count "Processing 3 of 12…" | M |
| [UX-11](https://github.com/rsocko/ideation/issues/1011) | Cross-cutting | Keyboard shortcuts completely undiscoverable (Y/N/S/D/X/F/arrows in Triage) | Power users never learn shortcuts; new users accidentally trigger actions | Add `?` help overlay; show shortcut hints on hover; add "Keyboard shortcuts" link in footer | S |
| [UX-12](https://github.com/rsocko/ideation/issues/1009) | Cross-cutting | Toast timing inconsistent — 3000ms, 3200ms, 4000ms, 30000ms across pages | Users miss quick toasts or are annoyed by lingering ones; no predictable feedback timing | Standardize: success=3000ms, warning=5000ms, error=persistent until dismissed; extract to shared toast config | S |

## Priority 2: Critical Architecture Gaps

| ID | Area | Issue | Impact | Suggested Fix | Effort |
|----|------|-------|--------|---------------|--------|
| [ARCH-01](https://github.com/rsocko/ideation/issues/1010) | Action Queue | Bulk operations documented but NOT implemented — no `/api/queue/actions/bulk` endpoint | Users must process actions one-by-one; major throughput bottleneck for high-volume orgs | Implement bulk endpoint with batch processing; mirror Triage module's existing bulk pattern | M |
| [ARCH-02](https://github.com/rsocko/ideation/issues/1012) | Alerts | Statement/EOB modules don't emit alerts — infrastructure exists but is unused | 'Unified alerts' goal is unrealized; users must check each module separately for issues | Wire statement recommendations and EOB orphan/mismatch events into alert emission pipeline | M |
| [ARCH-03](https://github.com/rsocko/ideation/issues/1013) | OCR | Entire quality pipeline unimplemented — 6 design docs, 5 issues, zero code | No document quality validation; garbage-in-garbage-out for all downstream processing | Implement Phase 5 OCR quality scoring: confidence thresholds, re-OCR triggers, quality dashboard | XL |
| [ARCH-04](https://github.com/rsocko/ideation/issues/1017) | Risk Scoring | `risk_score` field exists in DB model but never populated by pipeline | Priority sorting is non-functional; Action Queue can't surface highest-risk items first | Implement composite risk scoring function; integrate into action creation pipeline; add recalc on state changes | L |
| [ARCH-05](https://github.com/rsocko/ideation/issues/1014) | Error Handling | Best-effort patterns — `except Exception: pass`, no retry logic, no circuit breaker, in-memory state lost on restart | Silent data loss; unrecoverable failures during LLM/Paperless calls; system instability | Replace bare excepts with typed handlers; add retry with exponential backoff for external calls; persist critical state to DB | L |

## Priority 3: UX Improvements

| ID | Area | Issue | Impact | Suggested Fix | Effort |
|----|------|-------|--------|---------------|--------|
| [UX-13](https://github.com/rsocko/ideation/issues/1016) | Cross-cutting | Filter pills and tabs use similar styling but different semantics | Visual confusion about what is a filter vs. a view switch | Differentiate visually: pills = filters (removable), tabs = views (mutually exclusive) | S |
| [UX-14](https://github.com/rsocko/ideation/issues/1018) | Cross-cutting | Empty states vary in messaging across pages | Inconsistent brand voice; some empty states provide no guidance | Create shared EmptyState component with consistent illustration, message, and primary action CTA | S |
| [UX-15](https://github.com/rsocko/ideation/issues/1020) | Action Queue | PDF expansion state not reset when switching actions | Previous action's expanded PDF bleeds into next action's view | Reset PDF viewer state on action selection change | S |
| [UX-16](https://github.com/rsocko/ideation/issues/1015) | Triage | Confidence badges lack legend/tooltip | Users don't understand what confidence percentages mean in context | Add tooltip explaining score derivation; add legend to first-time-use onboarding | S |
| [UX-17](https://github.com/rsocko/ideation/issues/1023) | Statements | Missing statement table shows redundant date formats | Visual noise; wasted horizontal space | Standardize to single date format (relative for <7d, absolute otherwise) | S |
| [UX-18](https://github.com/rsocko/ideation/issues/1019) | Statements | Coverage breakdown fails silently — no 'unavailable' message | Users see empty space with no explanation; assume bug | Show "Coverage data unavailable" placeholder with retry action | S |

## Priority 4: Architecture Concerns

| ID | Area | Issue | Impact | Suggested Fix | Effort |
|----|------|-------|--------|---------------|--------|
| [ARCH-06](https://github.com/rsocko/ideation/issues/1022) | Cross-cutting | Tight coupling — routers directly call pipeline functions (no abstraction/retry) | Hard to test, swap implementations, or add cross-cutting concerns (logging, metrics) | Introduce service layer between routers and pipelines; add dependency injection | L |
| [ARCH-07](https://github.com/rsocko/ideation/issues/1021) | Configuration | Fragmented across env vars, YAML, admin API, app.state | Hard to audit current config; drift between environments; no single source of truth | Consolidate into hierarchical config system: defaults → YAML → env vars → admin overrides | M |
| [ARCH-08](https://github.com/rsocko/ideation/issues/1024) | Statements | Models missing balance/amount fields for true financial reconciliation | Cannot do actual dollar-amount reconciliation; limits to metadata matching only | Add financial fields to statement models; implement balance-based matching logic | L |
| [ARCH-09](https://github.com/rsocko/ideation/issues/1026) | EOB | No explicit 'error_type' enum for billing errors (only scoring) | Cannot categorize or report on billing error types; limits analytics and routing | Define BillingErrorType enum; add to EOB match model; populate during analysis | M |
| [ARCH-10](https://github.com/rsocko/ideation/issues/1025) | Cross-cutting | No locking for concurrent requests on shared state | Race conditions on simultaneous edits; potential data corruption in multi-user scenarios | Add optimistic locking (version field) for mutable entities; return 409 on conflicts | M |

## Priority 5: Observations & Future Considerations

- The Triage module's keyboard shortcut pattern (Y/N/S/D/X/F) is well-designed and should be the template for other queue-based views once discoverability is fixed
- The alert infrastructure (`/api/insights/alerts`) is architecturally sound — the gap is purely in wiring, not design
- OCR quality pipeline has extensive design documentation that can accelerate implementation once prioritized — see [OCR Quality Implementation Plan](../proposed/ocr-quality-implementation-plan.md)
- The 12-link navigation problem will worsen as new modules are added; solving it now prevents compounding tech debt
- Consider a plugin/module architecture for future document types rather than hardcoding each into the monolith — see [Plugin Module Architecture design](../proposed/plugin-module-architecture.md)
- WebSocket support for real-time queue updates would significantly improve multi-user coordination
- The existing Triage bulk operations pattern is a good reference implementation for [ARCH-01](https://github.com/rsocko/ideation/issues/1010)

## Recommended Fix Order

1. **Sprint 1 — Navigation & Orientation** ([UX-04](https://github.com/rsocko/ideation/issues/1002), [UX-05](https://github.com/rsocko/ideation/issues/1003), [UX-09](https://github.com/rsocko/ideation/issues/1007), [UX-12](https://github.com/rsocko/ideation/issues/1009))
   - Fix nav overflow, add breadcrumbs, standardize toasts
   - These are cross-cutting and unblock better UX everywhere
   - Estimated effort: ~1 week

2. **Sprint 2 — EOB Workflow Consolidation** ([UX-01](https://github.com/rsocko/ideation/issues/999), [UX-02](https://github.com/rsocko/ideation/issues/1001), [UX-03](https://github.com/rsocko/ideation/issues/1000))
   - Redesign EOB matching as single workspace
   - Resolve filter conflicts and confirm/reject consistency
   - Estimated effort: ~2 weeks

3. **Sprint 3 — Action Queue & Bulk Operations** ([ARCH-01](https://github.com/rsocko/ideation/issues/1010), [UX-06](https://github.com/rsocko/ideation/issues/1004), [UX-07](https://github.com/rsocko/ideation/issues/1006), [UX-10](https://github.com/rsocko/ideation/issues/1008))
   - Implement bulk endpoint (mirror Triage pattern)
   - Add progress indicators and loading states
   - Estimated effort: ~1.5 weeks

4. **Sprint 4 — Alert Wiring & Risk Scoring** ([ARCH-02](https://github.com/rsocko/ideation/issues/1012), [ARCH-04](https://github.com/rsocko/ideation/issues/1017))
   - Connect statement/EOB modules to alert pipeline
   - Implement risk_score calculation and integrate into queue sorting
   - Estimated effort: ~1.5 weeks

5. **Sprint 5 — Error Resilience** ([ARCH-05](https://github.com/rsocko/ideation/issues/1014), [ARCH-10](https://github.com/rsocko/ideation/issues/1025))
   - Replace bare excepts, add retry logic, add optimistic locking
   - Critical for production stability before scaling
   - Estimated effort: ~1 week

6. **Sprint 6 — Polish & Discoverability** ([UX-08](https://github.com/rsocko/ideation/issues/1005), [UX-11](https://github.com/rsocko/ideation/issues/1011), [UX-13](https://github.com/rsocko/ideation/issues/1016) through [UX-18](https://github.com/rsocko/ideation/issues/1019))
   - Keyboard shortcut help, empty states, button guidance, confidence tooltips
   - Lower risk; can be parallelized across team members
   - Estimated effort: ~1 week

7. **Sprint 7 — Architecture Hardening** ([ARCH-06](https://github.com/rsocko/ideation/issues/1022), [ARCH-07](https://github.com/rsocko/ideation/issues/1021), [ARCH-08](https://github.com/rsocko/ideation/issues/1024), [ARCH-09](https://github.com/rsocko/ideation/issues/1026))
   - Service layer extraction, config consolidation, model enrichment
   - Longer-term investment; schedule after user-facing fixes stabilize
   - Estimated effort: ~2–3 weeks

8. **Future — OCR Quality Pipeline** ([ARCH-03](https://github.com/rsocko/ideation/issues/1013))
   - Largest single item; benefits from design docs already in place
   - Schedule as dedicated initiative with its own milestone
   - Estimated effort: ~4–6 weeks
