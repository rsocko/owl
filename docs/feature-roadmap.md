---
title: "OWL Feature Roadmap"
sidebar_label: Roadmap
sidebar_position: 8
---

# OWL Feature Roadmap

*OWL = Organize. Watch. Learn. — Document Intelligence Hub*
*Last updated: 2026-07-26*

This document delineates what's **built and working today** versus what's **planned or envisioned** for each feature area.

---

## Action Queue

### ✅ Today (Built)

- Full pipeline: fetch → Ollama classify → store → enrich Paperless
- 8 action types: PAY, RESPOND, FILE, REVIEW, SHARE, SCHEDULE, SIGN, ARCHIVE
- Rule-based fallback when Ollama is unavailable
- SQLAlchemy database persistence (Action + ProcessingHistory models)
- API endpoints: `/api/queue/check`, `run`, `status`, `actions`, `PATCH actions/{id}`
- Risk score visualization + AI reasoning display in admin UI (#904)
- CLI entry point (`paq`) with run/status commands
- Pydantic-settings config (env-var driven)
- Integration tests covering all queue API endpoints

### 🔮 Future (Planned/Envisioned)

| Feature | Priority | Effort | Issue |
|---------|----------|--------|-------|
| Feedback loop & active learning (user confirms/rejects → model improves) | MEDIUM | XL | #780 |
| Bulk operations endpoint (multi-select batch actions) | MEDIUM | S | — |
| Populate risk_score field (currently exists but never written) | LOW | S | — |
| Keyboard shortcut hints panel | LOW | S | #876 |

---

## Statement Tracking

### ✅ Today (Built)

- Provider discovery with pattern analysis (detect recurring statements)
- Recurrence inference: monthly, quarterly, annual
- Gap detection with configurable grace periods
- Recommendations engine: missing/overdue statement alerts
- Provider override hints system (user corrections)
- CLI: `doc-hub serve`, `scan`, `status`
- Full API routes: `/api/statements/*` (scan, recommendations, overrides, config, documents)
- Static HTML dashboard at `/statements/`
- SQLite via SQLAlchemy persistence
- YAML-based config + env vars
- Paperless integration (fetches docs, resolves metadata)
- 10 integration tests covering all statement API endpoints

### 🔮 Future (Planned/Envisioned)

| Feature | Priority | Effort | Issue |
|---------|----------|--------|-------|
| Statement series timeline + split/merge UI (built in admin, exposed to MC) | LOW | M | #902 (done in admin) |
| Statement grouping correction UX improvements | LOW | S | — |
| Correspondent profile + expectation/title-format review | MEDIUM | L | — |
| Tyrion account/recurring candidate reconciliation | LOW | L | — |
| Paperless mail + direct email/API acquisition | LOW | L | — |

---

## EOB/Medical Matching

### ✅ Today (Built)

- Document classifier (EOB vs Bill)
- Data extractor (dates, amounts, providers, CPT codes, patient names)
- 5-factor weighted matcher (date 30%, provider 25%, patient 20%, amount 15%, procedures 10%)
- Configurable weights via admin API (`/api/admin/weights`)
- Payment lifecycle tracking (unpaid, partial, paid, overpaid)
- Insurance coverage analysis (#843)
- Full EOB dashboard UI with rich match review (#848)
- Amount validation pass/fail cards (#882)
- Confidence factor narratives (#878)
- Alternative matches display (#874)
- Match decision notes (#871)
- Match history timeline (#873)
- Unmatched view with filters + suggested matches (#884)
- Unmatched view bulk actions (#903)
- Manual match search modal (#877)
- Document preview links to Paperless (#872)
- Cross-run document deduplication (#845)
- `--use-llm` flag for enhanced extraction (#844)
- Notification/alerting for new matches (#851)
- Payment tracking integration (#850)
- Scheduled execution via cron/Dockhand (#846)
- Automated benchmark scheduling (#849)
- EOB Dashboard inline alerts + quick stats (#906)
- CLI entry point (`eob-match`)
- 16 integration tests covering all EOB API endpoints
- Wired to live Paperless (validated end-to-end)

### 🔮 Future (Planned/Envisioned)

| Feature | Priority | Effort | Issue |
|---------|----------|--------|-------|
| Historical trending + run history dashboard | HIGH | M | #847 |
| EOB coverage endpoint: migrate to SQL aggregation for scale | MEDIUM | M | #935 |
| Bill-to-transaction matching via Monarch Bridge (cross-platform) | LOW | XL | #768 |

---

## Unified Alerts & Insights

### ✅ Today (Built)

- Full alert table with CRUD operations
- `/api/insights/alerts` with filters, summary, acknowledge, resolve
- 3-tier analysis engine (YAML rules + DB config + runtime API)
- Insights page with trend charts + sparklines (#870)
- Analysis Engine & Insights System (#828)
- 16 integration tests covering all alert/insight endpoints
- APScheduler-driven rule evaluation on configurable schedules
- Auto-emit alerts from EOB matching (new matches, mismatches, notifications)

### 🔮 Future (Planned/Envisioned)

| Feature | Priority | Effort | Issue |
|---------|----------|--------|-------|
| Notification dedup + extractor consolidation (cross-platform) | LOW | L | #765 |
| n8n notification routing (email/push for alerts) | LOW | M | — |
| Statement module auto-emit alerts (overdue → alert) | MEDIUM | S | — |

---

## OCR Quality System

### ✅ Today (Built)

- 6 comprehensive design documents (baseline inventory, quality scoring, Ollama validation, remediation engine, n8n orchestration, umbrella spec)
- Architecture fully specified with tiered remediation: Tesseract 5 → Azure DI
- Budget controls and A–F grading system designed
- **Zero implementation** — design only

### 🔮 Future (Planned/Envisioned)

| Feature | Priority | Effort | Issue |
|---------|----------|--------|-------|
| Baseline inventory script (score all existing docs) | LOW | M | #736 |
| Quality scoring service (A–F grading on text metrics) | LOW | L | #737 |
| Ollama validation for borderline scores | LOW | M | #738 |
| Remediation engine (Tesseract 5 tier) | LOW | XL | #739 |
| n8n workflow orchestration (weekly scans, webhooks) | LOW | M | #740 |
| Azure DI tier for premium remediation | LOW | L | #739 |

**Decision gate:** Only proceed if OCR quality is actively blocking other modules. Tagvico's OCR rescue feature (#815) may eliminate the need for this entirely. See [OCR Quality Implementation Plan](design/proposed/ocr-quality-implementation-plan.md) for phased build approach.

**Estimated total effort: 60+ hours.**

---

## Triage & Correction System

### ✅ Today (Built)

- Unified Triage Queue UI (#832)
- Bulk actions in triage queue (#868)
- EOB ↔ Bill Match Review UI (#834)
- Orphan Document Management UI (#831)
- Duplicate Detection & Merge UI (#830)
- Statement Grouping Correction UI (#829)
- Metadata Correction & Writeback UI (#827)
- Dashboard + Correction History (#835)
- Rules Config — full rule editor with YAML + web UI (#869)
- Document preview links + Paperless integration (#872)

### 🔮 Future (Planned/Envisioned)

| Feature | Priority | Effort | Issue |
|---------|----------|--------|-------|
| Sidebar navigation layout evaluation (top nav vs sidebar) | LOW | S | #905 |
| First-run / onboarding experience | MEDIUM | M | #810 |
| Mobile/responsive experience | LOW | L | #805 |

---

## Infrastructure & Platform

### ✅ Today (Built)

- Single Docker container (Python 3.12-slim), multi-entrypoint
- FastAPI app factory with router mounting + error handling
- Shared Paperless client (full async CRUD: docs, tags, correspondents, custom fields, pagination)
- APScheduler with runtime reconfiguration (daily/2x-daily per module)
- Analysis rule engine (YAML + DB + API)
- React/TypeScript admin frontend at `:8001/admin`
- 85+ API integration tests (100% endpoint coverage)
- Health endpoint reporting module status
- Docker Compose with profiles (Statement Tracker always-on; EOB + AQ as job profiles)
- CI/CD: GitHub Actions → `service-007.example.invalid`
- Deployed to homelab via Dockhand
- Mission Control connector endpoints (flat-array API for MC consumption)
- `/api/settings` for Paperless connection, scan schedules, scoring weights
- LLM integration (Ollama local, with `--use-llm` optional flag)

### 🔮 Future (Planned/Envisioned)

| Feature | Priority | Effort | Issue |
|---------|----------|--------|-------|
| Migrate DocIntel to its own repository | HIGH | M | #836 |
| Coordinate pipeline with Tagvico (delegate general filing) | HIGH | L | #815 |
| Use Bifrost virtual key for LLM auth (unified LLM routing) | MEDIUM | M | #795 |
| Evaluate Copilot SDK as AI provider (free GPT-5.4-mini via Copilot subscription) | MEDIUM | M | #816 |
| DocType/Tag review for DocIntel | MEDIUM | S | #865 |
| Consolidate extractors into shared `core/extractors/` | LOW | M | — |
| Plugin module architecture (auto-discovery, protocol) | LOW | M | — |
| Docker image optimization (multi-stage build) | LOW | S | — |

---

## Cross-Platform Integration (Long-term Vision)

These features span multiple services (DI Hub + Mission Control + Monarch + external):

| Feature | Priority | Effort | Issue |
|---------|----------|--------|-------|
| Bill-to-transaction matching via Monarch Bridge | LOW | XL | #768 |
| Notification dedup + extractor consolidation | LOW | L | #765 |
| Feedback loop & active learning | MEDIUM | XL | #780 |

---

## Reconciliation Engine (Generalized Document Matching)

### ✅ Today (Built)

- EOB ↔ Bill matching (5-factor weighted scoring, configurable weights, payment lifecycle)
- Full classifier → extractor → scorer → lifecycle pipeline (hardcoded to medical)
- Rich review UI with confidence breakdowns, amount validation, match history

### 🔮 Future: Generalized Reconciliation Engine

The Reconciliation Engine generalizes the proven EOB matching pattern into a recipe-driven system supporting arbitrary document matching scenarios.

#### Phase 1: Foundation + Receipt ↔ Bill (Priority: HIGH)

| Feature | Priority | Effort | Dependencies |
|---------|----------|--------|--------------|
| Extract generic interfaces from `eob_matching/` (RecipeBase, ScoringEngine, LifecycleFSM) | HIGH | M | — |
| Migrate EOB matcher to `EobBillRecipe` plugin (backward compatible) | HIGH | M | Generic interfaces |
| Receipt ↔ Bill recipe: classifier, extractor, scorer | HIGH | M | Generic interfaces |
| Double-payment detection (two receipts → same bill) | HIGH | S | Receipt↔Bill recipe |
| Payment due date alerting (unmatched bills past due) | HIGH | S | Receipt↔Bill recipe |
| `/api/reconciliation/*` endpoints (recipes, matches, review, stats) | HIGH | M | Engine core |
| Reconciliation Dashboard UI (all recipes, review queue, activity feed) | HIGH | M | API endpoints |
| Receipt ↔ Bill matching UI view | HIGH | M | API endpoints |

**Estimated Phase 1 effort: ~6 weeks**

#### Phase 2: Order Lifecycle (Priority: MEDIUM)

| Feature | Priority | Effort | Dependencies |
|---------|----------|--------|--------------|
| Order ↔ Invoice ↔ Shipping recipe (3-way matching) | MEDIUM | L | Phase 1 complete |
| Extend scoring engine for N-way match groups | MEDIUM | M | Phase 1 complete |
| Mission Control package tracking integration | MEDIUM | S | MC connector |
| Item-level reconciliation (line item comparison) | MEDIUM | M | Order recipe |
| Order lifecycle timeline UI | MEDIUM | M | API endpoints |

**Estimated Phase 2 effort: ~4 weeks**

#### Phase 3: Insurance & Contracts (Priority: MEDIUM)

| Feature | Priority | Effort | Dependencies |
|---------|----------|--------|--------------|
| Insurance Policy ↔ Premium ↔ Payment recipe | MEDIUM | L | Phase 1 complete |
| Rate change detection (premium ≠ policy terms) | MEDIUM | M | Insurance recipe |
| Contract ↔ Recurring Bills recipe | LOW | M | Phase 1 complete |
| Variance trending (bills creeping above contract) | LOW | M | Contract recipe |
| Integration with Statement Tracking (recurring patterns) | MEDIUM | S | Both modules |

**Estimated Phase 3 effort: ~4 weeks**

#### Phase 4: Bank Statement Vision (Priority: FUTURE)

| Feature | Priority | Effort | Dependencies |
|---------|----------|--------|--------------|
| Bank statement table extraction (line items) | FUTURE | XL | Azure DI or specialized model |
| Merchant name normalization dictionary | FUTURE | L | — |
| Line-item ↔ document matching (high volume) | FUTURE | XL | Table extraction |
| Monarch Bridge integration (transaction import) | FUTURE | L | Monarch connector (#768) |
| Full reconciliation dashboard (unmatched drill-down) | FUTURE | L | All above |

**Estimated Phase 4 effort: 60+ hours (gated on table extraction capability)**

#### Phased Rollout Summary

| Phase | Scope | Priority | Estimated Effort |
|-------|-------|----------|-----------------|
| **Phase 1** | Engine foundation + Receipt↔Bill | HIGH | ~6 weeks |
| **Phase 2** | Order lifecycle (3-way matching) | MEDIUM | ~4 weeks |
| **Phase 3** | Insurance + Contracts | MEDIUM | ~4 weeks |
| **Phase 4** | Bank statement reconciliation | FUTURE | 60+ hrs |

**Decision gate:** Phase 2+ proceeds only after Phase 1 proves the generalized engine works reliably in production with the Receipt↔Bill recipe.

---

## Summary

| Feature Area | Today Status | Open Future Items |
|---|---|---|
| Action Queue | ✅ Full pipeline, live | 4 items |
| Statement Tracking | ✅ Full pipeline, live | 2 items |
| EOB/Medical Matching | ✅ Full pipeline, live + rich UI | 3 items |
| Reconciliation Engine | 📋 Design complete | 4 phases (20+ items) |
| Unified Alerts & Insights | ✅ Full system, charts, rules | 3 items |
| OCR Quality System | 📋 Design only (6 docs) | 6 items (60+ hrs) |
| Triage & Correction | ✅ Complete workflow | 3 items |
| Infrastructure & Platform | ✅ Production-deployed | 7 items |
| Cross-Platform | 🔮 Not started | 3 items |

**Phases 0–4 complete.** Phase 5 (Infrastructure & Integration) is next. Phase 5.5 (Reconciliation Engine) generalizes matching. Phase 6 (OCR) is gated on need. Phase 7 (Cross-Platform) is long-term vision.
