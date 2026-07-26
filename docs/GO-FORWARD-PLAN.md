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
| **#869** Rules Config (doc-hub-ui) | **#833** Rules Configuration UI (design) | Same scope: full rule editor with LLM/n8n tabs | Keep #869 (more detailed from mockup comparison), close #833 with reference |
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
1. **Close #833** → reference #869 (Rules Config duplicate)
2. **Close #826** → reference #831 (Orphan management covers this)
3. **Add parent/child references** to #834 for issues #882, #878, #871, #874, #873
4. **Cross-reference #870 ↔ #828** (frontend vs backend)
5. **Cross-reference #832 ↔ #868** (parent ↔ sub-task)

---

## 3. Complete Issue Inventory by Category

### Core Modules (Backend)
| # | Title | Status |
|---|-------|--------|
| 839 | Run EOB Matching LIVE — validate integration | **CRITICAL — Phase 1 gate** |
| 887 | Fix Statement Discovery | Bug fix |
| 844 | EOB: Add --use-llm flag to CLI | Enhancement |
| 845 | EOB: Cross-run document deduplication | Enhancement |
| 846 | EOB: Set up scheduled execution (cron/Dockhand) | Infrastructure |
| 849 | EOB: Automated benchmark scheduling | Enhancement |
| 850 | EOB: Payment tracking integration | Feature |
| 851 | EOB: Notification/alerting for new matches | Feature |
| 852 | EOB: Include extraction details in API results | API |
| 843 | EOB: Insurance coverage analysis | Feature |

### Doc Hub UI — Quick Wins
| # | Title | Effort |
|---|-------|--------|
| 876 | Keyboard shortcut hints panel | Quick Win |
| 878 | Confidence factor narratives | Quick Win |
| 871 | Match decision notes textarea | Quick Win |
| 873 | Match history timeline | Quick Win |
| 904 | Action Queue risk score + AI reasoning | Quick Win |

### Doc Hub UI — Medium Effort
| # | Title | Effort |
|---|-------|--------|
| 882 | Amount validation pass/fail cards | Medium |
| 884 | Unmatched view filters + suggested matches | Medium |
| 883 | Loading skeletons matching card shapes | Medium |
| 877 | Manual Match Search as modal overlay | Medium |
| 874 | Alternative matches display | Medium |
| 872 | Document preview links + Paperless integration | Medium |
| 870 | Insights page — trend charts + sparklines | Medium |
| 869 | Rules Config — full rule editor | Medium |
| 868 | Bulk actions in Triage Queue | Medium |
| 903 | Unmatched view bulk actions | Medium |
| 902 | Statement series timeline + split/merge | Medium |
| 906 | EOB Dashboard inline alerts + quick stats | Medium |

### Design / Triage System
| # | Title | Scope |
|---|-------|-------|
| 832 | Unified Triage Queue UI | Design epic |
| 834 | EOB ↔ Bill Match Review UI | Design epic |
| 835 | Dashboard, Correction History + Account # | Design epic |
| 831 | Orphan Document Management UI | Design epic |
| 830 | Duplicate Detection & Merge UI | Design epic |
| 829 | Statement Grouping Correction UI | Design epic |
| 827 | Metadata Correction & Writeback UI | Design epic |
| 828 | Analysis Engine & Insights System | Design epic |
| 833 | Rules Configuration UI | **DUPLICATE — close** |

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

### Phase 1: Quick Win UI Polish (Weeks 2-3) 🎨
**Goal:** Tighten the existing UI with low-effort, high-impact improvements.

| Priority | Issue | Effort                                 |       |
| -------- | ----- | -------------------------------------- | ----- |
| P1       | #876  | Keyboard shortcut hints                | defer |
| P1       | #878  | Confidence factor narratives           | done  |
| P1       | #871  | Match decision notes textarea          | done  |
| P1       | #873  | Match history timeline                 |       |
| P1       | #904  | Action Queue risk score + AI reasoning |       |
| P1       | #883  | Loading skeletons                      |       |

**Exit Criteria:** Match review workflow feels polished; users can add notes, see narratives, use keyboard shortcuts.

---

### Phase 2: EOB Matching Completion (Weeks 3-5) 🏥
**Goal:** Complete the EOB matching loop end-to-end.

| Priority | Issue | Description |
|----------|-------|-------------|
| P2 | #848 | Full EOB dashboard UI (replaces basic tab) |
| P2 | #906 | EOB Dashboard inline alerts + quick stats |
| P2 | #882 | Amount validation pass/fail cards |
| P2 | #874 | Alternative matches display |
| P2 | #884 | Unmatched filters + suggested matches |
| P2 | #903 | Unmatched view bulk actions |
| P2 | #852 | Include extraction details in API results |
| P2 | #844 | Add --use-llm flag to CLI |
| P2 | #845 | Cross-run document deduplication |

**Exit Criteria:** EOB matching dashboard is fully functional with rich match review, unmatched management, and deduplication.

---

### Phase 3: Triage & Correction System (Weeks 5-8) 🔧
**Goal:** Build the human-in-the-loop correction workflows.

| Priority | Issue | Description |
|----------|-------|-------------|
| P3 | #832 | Unified Triage Queue UI |
| P3 | #868 | Triage Queue bulk actions |
| P3 | #834 | EOB ↔ Bill Match Review UI |
| P3 | #877 | Manual Match Search as modal |
| P3 | #872 | Document preview links + Paperless |
| P3 | #831 | Orphan Document Management |
| P3 | #830 | Duplicate Detection & Merge |
| P3 | #829 | Statement Grouping Correction |
| P3 | #902 | Statement series timeline + split/merge |
| P3 | #827 | Metadata Correction & Writeback |

**Exit Criteria:** Users can triage all flagged items, correct groupings, manage orphans/duplicates, and write corrections back to Paperless.

---

### Phase 4: Insights, Rules & Scheduling (Weeks 8-10) 📊
**Goal:** Automated pipeline execution + analytics + configurable rules.

| Priority | Issue | Description |
|----------|-------|-------------|
| P4 | #870 | Insights page — trend charts + sparklines |
| P4 | #828 | Analysis Engine & Insights System |
| P4 | #869 | Rules Config — full rule editor |
| P4 | #835 | Dashboard + Correction History |
| P4 | #846 | Scheduled execution (cron/Dockhand) |
| P4 | #849 | Automated benchmark scheduling |
| P4 | #851 | Notification/alerting |
| P4 | #850 | Payment tracking integration |

**Exit Criteria:** System runs automatically on schedule, produces insights, and rules can be configured without code changes.

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
| Total DI-related open issues | ~55 (including 5 new) |
| Likely duplicates to close | 2 (#833, #826) |
| Issues needing cross-references | ~8 pairs |
| Phases of work | 7 (0-6 + future) |
| Estimated timeline (Phases 0-4) | 10 weeks |
| Estimated timeline (all phases) | 16+ weeks |
| Quick Wins (can ship this week) | 5 issues |
| Critical path blockers | #887 (bug), #839 (live validation) |
