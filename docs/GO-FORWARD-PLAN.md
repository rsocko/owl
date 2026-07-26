# Doc Intelligence — Gap Analysis, Duplicate Check & Go-Forward Plan

*Date: 2026-07-25*

This document captures the results of a comprehensive audit comparing all 14 UI mockups, 30+ design docs, the full frontend/backend implementation, and ~55 open GitHub issues for the Document Intelligence Hub. It identifies gaps, duplicates, and provides a phased go-forward plan.

---

## Analysis Scope
- **14 mockups** across 4 areas (Action Queue, EOB Matching, Hub, Triage-Correction)
- **30+ design docs** covering architecture, modules, and integration
- **15 frontend pages** + **9 backend routers** + **3 major modules** implemented
- **~50 open GitHub issues** related to Document Intelligence

---

## 1. Gap Analysis: Mockup → Implementation → Issues

### ✅ COVERED — Mockup features with existing GitHub issues

| Mockup | Key Gap | Issue(s) |
|--------|---------|----------|
| eob-matching/match-review.html | Amount validation cards | #882 |
| eob-matching/match-review.html | Confidence factor narratives | #878 |
| eob-matching/match-review.html | Notes textarea | #871 |
| eob-matching/match-review.html | Alternative matches display | #874 |
| eob-matching/unmatched.html | Filters + suggested matches | #884 |
| triage-correction/triage-queue.html | Bulk actions | #868 |
| triage-correction/eob-match-review.html | Match history timeline | #873 |
| triage-correction/manual-match-search.html | Modal overlay (not page) | #877 |
| triage-correction/rules-config.html | Full rule editor + LLM/n8n | #869, #833 |
| triage-correction/insights-tab.html | Trend charts + sparklines | #870, #828 |
| triage-correction/orphans-dupes-metadata.html | Orphan management | #831 |
| triage-correction/orphans-dupes-metadata.html | Duplicate detection | #830 |
| triage-correction/orphans-dupes-metadata.html | Metadata correction | #827 |
| triage-correction/dashboard-history.html | Dashboard + history | #835 |
| triage-correction/statement-series-detail.html | Grouping correction | #829 |
| eob-matching/dashboard.html | Full dashboard UI | #848 |
| hub/dashboard-unified.html | Loading skeletons | #883 |
| hub/dashboard-unified.html | Document preview links | #872 |
| hub/dashboard-unified.html | Keyboard shortcuts | #876 |

### 🆕 NEW ISSUES CREATED — Gaps not previously covered

| Issue | Gap Description |
|-------|----------------|
| #902 | Statement series detail — document timeline + split/merge UI |
| #903 | Unmatched view bulk actions (Select All, Bulk Mark Orphan/Paid) |
| #904 | Action Queue risk score visualization + AI reasoning display |
| #905 | Sidebar navigation layout evaluation (mockup vs top nav) |
| #906 | EOB Dashboard inline alerts + quick stats section |

---

## 2. Duplicate & Overlap Analysis

### 🔴 LIKELY DUPLICATES (recommend closing one)

| Issue A | Issue B | Overlap | Recommendation |
|---------|---------|---------|----------------|
| **#869** Rules Config (doc-hub-ui) | **#833** Rules Configuration UI (design) | Same scope: full rule editor with LLM/n8n tabs | ✅ #833 closed |
| **#848** Build full dashboard UI | **#847** Historical trending + run history | #847 is a subset of #848's mockup scope | Keep both — #847 is a deliverable within #848; add parent reference |

### 🟡 SIGNIFICANT OVERLAP (recommend consolidation)

| Issue A | Issue B | Overlap | Recommendation |
|---------|---------|---------|----------------|
| **#870** Insights page — trends/charts | **#828** Analysis Engine & Insights | #870 is the UI; #828 is engine + UI. Insights UI is in both. | Keep both — #828 is backend engine, #870 is frontend. Add cross-references. |
| **#834** EOB Match Review UI (design) | **#882, #878, #871, #874, #873** (doc-hub-ui) | #834 is the epic; the 5 issues are specific feature gaps | Keep all — #834 is the parent epic. Add "Part of #834" to each sub-issue. |
| **#832** Unified Triage Queue UI | **#868** Bulk actions in Triage Queue | #868 is a specific feature within #832 | Keep both — #868 is a sub-task of #832. |
| **#831** Orphan Management UI | **#826** Triage view of docs with nothing to do | #826 is a vague idea that #831 fully encompasses | Close #826 — #831 covers the "what to do with docs that have no action" use case |
| **#160** Paperless-Enhanced OCR (umbrella) | **#736-#740** OCR sub-issues | #160 is the original idea; #736-740 are the decomposed tasks | Keep all — #160 is the umbrella. Already cross-referenced. |

### 🟢 NOT DUPLICATES (appear similar but distinct)

| Issue A | Issue B | Why Distinct |
|---------|---------|-------------|
| #11 (Statement tracking — original idea) | #887 (Fix Statement Discovery) | #11 is project vision; #887 is a specific bug |
| #848 (Full dashboard UI) | #906 (Inline alerts + quick stats) | #906 is a specific sub-feature within #848's scope |
| #829 (Statement Grouping Correction) | #902 (Timeline + split/merge) | #902 is the UI implementation detail; #829 is the design spec |

### Recommended Cleanup Actions
1. ~~**Close #833** → reference #869 (Rules Config duplicate)~~ ✅ Done
2. **Close #826** → reference #831 (Orphan management covers this)
3. **Add parent/child references** to #834 for issues #882, #878, #871, #874, #873
4. **Cross-reference #870 ↔ #828** (frontend vs backend)
5. **Cross-reference #832 ↔ #868** (parent ↔ sub-task)
6. **Close remaining implemented issues** — #877, #872, #831, #830, #829, #834, #902 have merged PRs but are still open on GitHub

---

## 3. Complete Issue Inventory by Category

### Core Modules (Backend)
| # | Title | Status |
|---|-------|--------|
| 839 | Run EOB Matching LIVE — validate integration | ✅ Done |
| 887 | Fix Statement Discovery | ✅ Done |
| 844 | EOB: Add --use-llm flag to CLI | ✅ Done |
| 845 | EOB: Cross-run document deduplication | ✅ Done |
| 846 | EOB: Set up scheduled execution (cron/Dockhand) | Open |
| 849 | EOB: Automated benchmark scheduling | Open |
| 850 | EOB: Payment tracking integration | Open |
| 851 | EOB: Notification/alerting for new matches | Open |
| 852 | EOB: Include extraction details in API results | ✅ Done |
| 843 | EOB: Insurance coverage analysis | Open |

### Doc Hub UI — Quick Wins
| # | Title | Status |
|---|-------|--------|
| 876 | Keyboard shortcut hints panel | Deferred |
| 878 | Confidence factor narratives | ✅ Done |
| 871 | Match decision notes textarea | ✅ Done |
| 873 | Match history timeline | ✅ Done |
| 904 | Action Queue risk score + AI reasoning | ✅ Done |

### Doc Hub UI — Medium Effort
| # | Title | Status |
|---|-------|--------|
| 882 | Amount validation pass/fail cards | Open |
| 884 | Unmatched view filters + suggested matches | ✅ Done |
| 883 | Loading skeletons matching card shapes | ✅ Done |
| 877 | Manual Match Search as modal overlay | ✅ Done (PR #932) |
| 874 | Alternative matches display | ✅ Done |
| 872 | Document preview links + Paperless integration | ✅ Done (PR #930) |
| 870 | Insights page — trend charts + sparklines | Open |
| 869 | Rules Config — full rule editor | Open |
| 868 | Bulk actions in Triage Queue | ✅ Done |
| 903 | Unmatched view bulk actions | ✅ Done |
| 902 | Statement series timeline + split/merge | ✅ Done (PR #929) |
| 906 | EOB Dashboard inline alerts + quick stats | ✅ Done |

### Design / Triage System
| # | Title | Status |
|---|-------|--------|
| 832 | Unified Triage Queue UI | ✅ Done |
| 834 | EOB ↔ Bill Match Review UI | ✅ Done (PR #927) |
| 835 | Dashboard, Correction History + Account # | Open |
| 831 | Orphan Document Management UI | ✅ Done (PR #933) |
| 830 | Duplicate Detection & Merge UI | ✅ Done (PR #925) |
| 829 | Statement Grouping Correction UI | ✅ Done (PR #928) |
| 827 | Metadata Correction & Writeback UI | ✅ Done |
| 828 | Analysis Engine & Insights System | Open |
| 833 | Rules Configuration UI | **DUPLICATE — closed** |

### Design / UX
| # | Title |
|---|-------|
| 810 | First-run / onboarding experience | 
| 805 | Mobile/responsive experience |
| 905 | Sidebar navigation layout evaluation |

### Infrastructure / Integration
| # | Title |
|---|-------|
| 836 | Migrate DocIntel to its own repo |
| 815 | Coordinate pipeline with Tagvico |
| 795 | Use Bifrost virtual key for LLM auth |
| 816 | Evaluate Copilot SDK as AI provider |
| 865 | DocType/Tag review for DocIntel |

### OCR Pipeline (Phase 5 — consider deferring)
| # | Title |
|---|-------|
| 160 | Paperless-Enhanced OCR (umbrella) |
| 736 | OCR Baseline Inventory Script |
| 737 | OCR Quality Scoring Service |
| 738 | OCR Ollama Validation |
| 739 | OCR Remediation Engine |
| 740 | OCR n8n Workflow Orchestration |

### Mission Control Integration (Phase 5-6)
| # | Title |
|---|-------|
| 768 | [Phase 5] Bill-to-transaction matching via Monarch Bridge |
| 765 | [Phase 6] Notification dedup + extractor consolidation |
| 780 | Action Queue Feedback Loop & Active Learning |

---

## 4. Go-Forward Plan — Phased Roadmap

### Phase 0: Stabilize & Validate (Week 1) ⚡
**Goal:** Prove the system works end-to-end with real data.

| Priority | Issue | Description                                        |      |
| -------- | ----- | -------------------------------------------------- | ---- |
| P0       | #887  | Fix Statement Discovery bug                        | done |
| P0       | #839  | Run EOB Matching LIVE — validate integration       | done |
| —        | —     | Deploy to homelab, validate Paperless connectivity | done |

**Exit Criteria:** Statement Tracker + EOB Matching both running against live Paperless, producing real results.

---

### Phase 1: Quick Win UI Polish (Weeks 2-3) 🎨 ✅ COMPLETE
**Goal:** Tighten the existing UI with low-effort, high-impact improvements.

| Priority | Issue | Effort                                 |       |
| -------- | ----- | -------------------------------------- | ----- |
| P1       | #876  | Keyboard shortcut hints                | defer |
| P1       | #878  | Confidence factor narratives           | done  |
| P1       | #871  | Match decision notes textarea          | done  |
| P1       | #873  | Match history timeline                 | done  |
| P1       | #904  | Action Queue risk score + AI reasoning | done  |
| P1       | #883  | Loading skeletons                      | done  |

**Exit Criteria:** Match review workflow feels polished; users can add notes, see narratives, use keyboard shortcuts.

---

### Phase 2: EOB Matching Completion (Weeks 3-5) 🏥 ✅ NEARLY COMPLETE
**Goal:** Complete the EOB matching loop end-to-end.

| Priority | Issue | Description                                |      |
| -------- | ----- | ------------------------------------------ | ---- |
| P2       | #848  | Full EOB dashboard UI (replaces basic tab) | done |
| P2       | #906  | EOB Dashboard inline alerts + quick stats  | done |
| P2       | #882  | Amount validation pass/fail cards          |      |
| P2       | #874  | Alternative matches display                | done |
| P2       | #884  | Unmatched filters + suggested matches      | done |
| P2       | #903  | Unmatched view bulk actions                | done |
| P2       | #852  | Include extraction details in API results  | done |
| P2       | #844  | Add --use-llm flag to CLI                  | done |
| P2       | #845  | Cross-run document deduplication           | done |

**Exit Criteria:** EOB matching dashboard is fully functional with rich match review, unmatched management, and deduplication.

---

### Phase 3: Triage & Correction System (Weeks 5-8) 🔧 ✅ COMPLETE
**Goal:** Build the human-in-the-loop correction workflows.

| Priority | Issue | Description | |
|----------|-------|-------------|---|
| P3 | #832 | Unified Triage Queue UI | done |
| P3 | #868 | Triage Queue bulk actions | done |
| P3 | #834 | EOB ↔ Bill Match Review UI | done |
| P3 | #877 | Manual Match Search as modal | done |
| P3 | #872 | Document preview links + Paperless | done |
| P3 | #831 | Orphan Document Management | done |
| P3 | #830 | Duplicate Detection & Merge | done |
| P3 | #829 | Statement Grouping Correction | done |
| P3 | #902 | Statement series timeline + split/merge | done |
| P3 | #827 | Metadata Correction & Writeback | done |

**Exit Criteria:** Users can triage all flagged items, correct groupings, manage orphans/duplicates, and write corrections back to Paperless.

---

### Phase 4: Insights, Rules & Scheduling (Weeks 8-10) 📊 ✅ COMPLETE
**Goal:** Automated pipeline execution + analytics + configurable rules.

| Priority | Issue | Description | Status |
|----------|-------|-------------|--------|
| P4 | #869 | Rules Config — full rule editor | ✅ done (PR #936) |
| P4 | #846 | Scheduled execution (cron/Dockhand) | ✅ done (PR #937) |
| P4 | #850 | Payment tracking integration | ✅ done (PR #938) |
| P4 | #849 | Automated benchmark scheduling | ✅ done (PR #943) |
| P4 | #851 | Notification/alerting | ✅ done (PR #945) |
| P4 | #828 | Analysis Engine & Insights System | ✅ done (PR #947) |
| P4 | #835 | Dashboard + Correction History | ✅ done |
| P4 | #870 | Insights page — trend charts + sparklines | ✅ done (PR #951) |

**Exit Criteria:** ✅ All met — System runs automatically on schedule (cron/Dockhand + APScheduler for EOB matching, benchmarks, due-date checks, analysis rules), produces insights (3-tier analysis engine → Insights page with charts/sparklines/compliance), and rules can be configured without code changes (full rule editor UI + YAML config + API).

---

### Phase 5: Infrastructure & Integration (Weeks 10-12) 🔗
**Goal:** Harden infrastructure and integrate with ecosystem.

| Priority | Issue | Description |
|----------|-------|-------------|
| P5 | #836 | Migrate DocIntel to its own repo |
| P5 | #815 | Coordinate pipeline with Tagvico |
| P5 | #795 | Use Bifrost virtual key (hardening — LLM auth already working) |
| P5 | #816 | Evaluate Copilot SDK as AI provider |
| P5 | #865 | DocType/Tag review |
| P5 | #843 | Insurance coverage analysis |
| P5 | #905 | Sidebar navigation layout |
| P5 | #810 | First-run / onboarding experience |
| P5 | #805 | Mobile/responsive experience |

**Exit Criteria:** DI Hub is standalone, integrated with Tagvico, and has a polished UX.

---

### Phase 6: OCR Pipeline (Weeks 12-16+) — OPTIONAL 🔬
**Goal:** Improve OCR quality across the document corpus.
**Decision Gate:** Only proceed if OCR quality is actively blocking Phases 1-4.

| Priority | Issue | Description |
|----------|-------|-------------|
| P6 | #736 | OCR Baseline Inventory Script |
| P6 | #737 | OCR Quality Scoring Service |
| P6 | #738 | OCR Ollama Validation |
| P6 | #739 | OCR Remediation Engine |
| P6 | #740 | OCR n8n Workflow Orchestration |
| P6 | #160 | Paperless-Enhanced OCR (umbrella) |

**Estimated effort: 60+ hours.** Consider whether Tagvico's OCR rescue feature (#815) eliminates the need for this.

---

### Phase 7: Cross-Platform Integration (Future) 🌐
| Issue | Description |
|-------|-------------|
| #768 | Bill-to-transaction matching via Monarch Bridge |
| #765 | Notification dedup + extractor consolidation |
| #780 | Feedback Loop & Active Learning |

---

## 5. Summary Statistics

| Category | Count |
|----------|-------|
| Total DI-related issues tracked | ~55 (including 5 new) |
| Issues completed (closed or PR merged) | ~30 |
| Likely duplicates closed | 1 of 2 (#833 closed; #826 still open) |
| Issues needing cross-references | ~8 pairs |
| Phases of work | 7 (0-6 + future) |
| **Phase 0** | ✅ Complete |
| **Phase 1** | ✅ Complete (except #876 deferred) |
| **Phase 2** | ✅ Nearly complete (8/9 done; #882 remaining) |
| **Phase 3** | ✅ Complete (all 10 done) |
| **Phase 4** | Not started |
| **Phase 5-7** | Not started |
| Critical path blockers | ~~#887 (bug), #839 (live validation)~~ Both resolved |
