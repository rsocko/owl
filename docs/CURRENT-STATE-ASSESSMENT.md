# Document Intelligence Hub — Current State Assessment

*Date: 2026-07-23*

---

## Executive Summary

The Document Intelligence Hub is a **well-designed but partially implemented** unified platform for Paperless-ngx document analysis. The architecture and design documentation are comprehensive, the project skeleton is in place, and two of three core modules have working code. However, the project has **duplicate GitHub issues** that need cleanup, and no module has been tested end-to-end against a live Paperless instance.

---

## GitHub Issues Consolidation

### Canonical Issue Map (after dedup)

| # | Title | Status Label | Category |
|---|-------|--------------|----------|
| **#11** | Statement tracking (original) | idea | Core concept |
| **#160** | Paperless-Enhanced OCR | idea | OCR |
| **#732** | Action Queue - Core Pipeline | in-progress | Module |
| **#733** | Action Queue - UI Dashboard | idea | UI |
| **#734** | EOB Matching - Core Implementation | in-progress | Module |
| **#735** | EOB Matching - UI Dashboard | idea | UI |
| **#736** | OCR - Baseline Inventory Script (Phase 0) | idea | OCR |
| **#737** | OCR - Quality Scoring Service | idea | OCR |
| **#738** | OCR - Ollama Validation Integration | idea | OCR |
| **#739** | OCR - Remediation Engine (Tesseract + Azure DI) | idea | OCR |
| **#740** | OCR - n8n Workflow Orchestration | idea | OCR |
| **#741** | Statement Tracking - Phase 1 | in-progress | Module |
| **#742** | Hub - Unified Dashboard | idea | UI |

---

## What's Built (Current Code State)

### ✅ Infrastructure (Working)

| Component | Status | Notes |
|-----------|--------|-------|
| **Project structure** | ✅ Complete | Clean monorepo under `experiments/document-intelligence/` |
| **pyproject.toml** | ✅ Complete | All deps declared, CLI entry points defined |
| **Dockerfile** | ✅ Complete | Python 3.12-slim, multi-entrypoint |
| **docker-compose.yml** | ✅ Complete | Statement Tracker (always-on), EOB + AQ as job profiles |
| **CI/CD** | ✅ Configured | GitHub Actions builds image → `service-007.example.invalid` |
| **FastAPI app factory** | ✅ Complete | Unified app with router mounting, error handling |
| **Shared Paperless client** | ✅ Complete | Full async CRUD: docs, tags, correspondents, custom fields, pagination |
| **Health endpoint** | ✅ Complete | Reports module status |

### ✅ Statement Tracker Module (Most Mature)

| Component | Status | Notes |
|-----------|--------|-------|
| Config system | ✅ | YAML-based, env vars |
| Database layer | ✅ | SQLite via SQLAlchemy |
| Paperless integration | ✅ | Fetches docs, resolves metadata |
| Detector (recurrence inference) | ✅ | Pattern matching for statement frequency |
| Recommendations engine | ✅ | Gap detection, overdue alerts |
| Hints system | ✅ | Provider overrides |
| CLI (`doc-hub`) | ✅ | Full CLI with serve, scan, status commands |
| API routes | ✅ | `/api/statements/*` — scan, recommendations, overrides |
| Static dashboard | ✅ | HTML/JS dashboard at `/statements/` |
| Tests | ✅ | 8 test files covering API, database, detector, recommendations |

### ⚡ EOB Matching Module (Logic Complete, Not Integrated)

| Component | Status | Notes |
|-----------|--------|-------|
| Document classifier | ✅ | Classifies docs as EOB vs Bill |
| Data extractor | ✅ | Pulls dates, amounts, providers, services, CPT codes |
| Multi-factor matcher | ✅ | 5-factor weighted scoring (date 30%, provider 25%, patient 20%, amount 15%, procedures 10%) |
| Models (Pydantic) | ✅ | ExtractedEOB, ExtractedBill, MatchResult, MatchBreakdown |
| CLI (`eob-match`) | ✅ | Entry point configured |
| API router | ✅ | `/api/eob/*` mounted |
| Tests | ✅ | classifier, extractor, matcher tests |
| **Paperless live integration** | ❌ | No wiring to fetch real docs from Paperless yet |
| **Persistence** | ❌ | No database/storage for match results |
| **UI** | ❌ | Mockups exist (3 HTML files) but not integrated |

### ⚡ Action Queue Module (Pipeline Complete, Not Integrated)

| Component | Status | Notes |
|-----------|--------|-------|
| Pipeline orchestrator | ✅ | Full fetch → analyze → store → enrich flow |
| Ollama analyzer | ✅ | Uses phi3:mini for document classification |
| Rule-based fallback | ✅ | Fallback when Ollama unavailable |
| Paperless enricher | ✅ | Writes tags/custom fields back to Paperless |
| Database (SQLAlchemy) | ✅ | Action and ProcessingHistory models |
| Config (pydantic-settings) | ✅ | Env-var driven settings |
| CLI (`paq`) | ✅ | Entry point configured |
| API router | ✅ | `/api/actions/*` mounted |
| **Live testing** | ❌ | Docker compose has `--dry-run` flag; untested with real data |
| **UI** | ❌ | Mockup exists but not integrated |

### 🟡 OCR Quality Sub-System (Design Only)

| Component | Status | Notes |
|-----------|--------|-------|
| Baseline inventory script | 📋 Designed | Score all existing docs |
| Quality scoring heuristics | 📋 Designed | A–F grading on text quality metrics |
| Remediation engine | 📋 Designed | Tiered: Tesseract 5 → Azure DI |
| Ollama validation | 📋 Designed | Secondary validation for borderline scores |
| n8n workflow orchestration | 📋 Designed | Weekly scans, webhooks, alerts |
| **Code** | ❌ None | Zero implementation |

### 🟡 Hub Unified Dashboard (Design Only)

| Component | Status | Notes |
|-----------|--------|-------|
| Unified dashboard mockup | ✅ | `mockups/hub/dashboard-unified.html` |
| Cross-module alert system | 📋 Designed | In README architecture |
| **Code** | ❌ None | Only the FastAPI index.html shell exists |

---

## Design Documentation Status

All design docs are comprehensive and implementation-ready:

| Module | Docs | Mockups | Quality |
|--------|------|---------|---------|
| **Shared** | 1 doc (principles/stack) | — | Good |
| **Statement Tracking** | 6 docs (DESIGN, PHASE1, TECH, SETUP, QUICK-REF, SUMMARY) | Integrated in code | Excellent |
| **EOB Matching** | 6 docs (DESIGN, UI, TECH, SETUP, QUICK-REF, SUMMARY) | 3 HTML mockups | Excellent |
| **Action Queue** | 11 docs (DESIGN, UI, TECH, QUICK-REF, SUMMARY + 6 OCR docs) | 1 HTML mockup | Excellent |
| **Hub** | README architecture | 1 HTML mockup | Good |

---

## What's NOT Working / Gaps

1. **No end-to-end test against live Paperless** — Everything runs in fixture/dry-run mode
2. **No unified frontend** — Each module has its own static HTML or nothing; no SPA shell
3. **Shared extractors empty** — `core/extractors/__init__.py` is empty; each module has its own extraction
4. **No unified alert system** — Designed but not coded
5. **No settings page** — Designed but not coded
6. **OCR pipeline** — 6 design docs, zero code

---

## Phased Go-Forward Plan

### Phase 1: Stabilize & Validate (Priority: HIGH) 🎯
*Estimated: 1–2 weeks*

**Goal:** Get Statement Tracker running live on homelab and validate the shared infrastructure works.

| Task                                              | Issue | Effort |
| ------------------------------------------------- | ----- | ------ |
| Deploy Statement Tracker to homelab via Dockhand  | #741  | 2h     |
| Validate Paperless connectivity end-to-end        | #741  | 2h     |
| Run statement scan against real documents         | #741  | 4h     |
| Fix any issues found in live testing              | #741  | 4h     |
| Update #741 status to reflect what actually works | —     | 30 min |

**Exit criteria:** Statement Tracker dashboard shows real data from Paperless at `http://server-mini:8001/statements/`

---

### Phase 2: EOB Matching Live Integration (Priority: HIGH) 🎯
*Estimated: 2–3 weeks*

**Goal:** Wire EOB matching to real Paperless docs, persist results, confirm matching accuracy.

| Task | Issue | Effort |
|------|-------|--------|
| Wire classifier + extractor to shared PaperlessClient | #734 | 4h |
| Add SQLite persistence for matches (eob_records, bill_records, matches tables) | #734 | 4h |
| Build CLI workflow: scan → classify → extract → match → store | #734 | 8h |
| Test with real EOB/bill documents | #734 | 4h |
| Tune scoring weights based on real data | #734 | 4h |
| Add Paperless document linking for confirmed matches | #734 | 4h |

**Exit criteria:** `eob-match run` produces accurate matches against real Paperless documents.

---

### Phase 3: Action Queue Live Integration (Priority: MEDIUM)
*Estimated: 2 weeks*

**Goal:** Run action queue against real inbox docs, validate Ollama classification, confirm enrichment.

| Task | Issue | Effort |
|------|-------|--------|
| Test pipeline against Paperless inbox/tag | #732 | 4h |
| Remove `--dry-run` default; validate enricher writes | #732 | 4h |
| Tune rule-based fallback categories | #732 | 4h |
| Add scheduling via n8n or cron | #732 | 2h |
| Verify Ollama connectivity on homelab | #732 | 2h |

**Exit criteria:** `paq run` correctly classifies and enriches inbox documents.

---

### Phase 4: Unified Hub UI (Priority: MEDIUM)
*Estimated: 2–3 weeks*

**Goal:** Single web dashboard showing all modules' data.

| Task | Issue | Effort |
|------|-------|--------|
| Build SPA shell (sidebar nav, routing) | #742 | 8h |
| Unified dashboard home (stats from all modules) | #742 | 8h |
| Integrate Statement Tracker dashboard | #742 | 4h |
| Build EOB matching UI (from mockups) | #735 | 12h |
| Build Action Queue UI (from mockup) | #733 | 8h |
| Cross-module alert feed | #742 | 4h |

**Exit criteria:** One URL at `:8001` shows a unified dashboard with live data from all three modules.

---

### Phase 5: OCR Quality Pipeline (Priority: LOW)
*Estimated: 3–4 weeks*

**Goal:** Assess and remediate OCR quality across document corpus.

| Task | Issue | Effort |
|------|-------|--------|
| Build baseline inventory script | #736 | 8h |
| Implement quality scoring service | #737 | 12h |
| Build remediation engine (Tesseract tier) | #739 | 16h |
| Add Ollama validation for borderline scores | #738 | 8h |
| Build n8n orchestration workflows | #740 | 8h |
| (Optional) Azure DI tier for remediation | #739 | 8h |

**Exit criteria:** OCR quality grades visible in Paperless custom fields; low-quality docs auto-remediated.

---

### Phase 6: Polish & Notifications (Priority: LOW)
*Estimated: 1–2 weeks*

| Task | Issue | Effort |
|------|-------|--------|
| Settings page (Paperless connection, notification prefs) | #742 | 4h |
| n8n notification routing (email/push for alerts) | #740 | 4h |
| Docker image optimization (multi-stage build) | — | 2h |
| Consolidate extractors into shared `core/extractors/` | — | 8h |

---

## Recommended Immediate Actions

1. **Close 11 duplicate issues** (#721–#731) — reduces noise immediately
2. **Deploy Statement Tracker to homelab** — proves infrastructure works
3. **Pick Phase 2 or Phase 3** based on which documents you have more of (EOBs or inbox items)

---

## Architecture Confidence: HIGH ✅

The overall architecture is sound:
- Single Docker container with multiple entry points
- Shared Paperless client avoids code duplication
- Feature modules are cleanly separated
- Design docs are thorough enough to code from directly
- Tech stack choices are appropriate for single-user homelab

The main risk is **scope creep from OCR** (5 design docs, 5 issues, ~60h of work) — consider deprioritizing unless OCR quality is actively blocking the other modules.
