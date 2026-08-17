# Document Intelligence Hub

## Overview

The **Document Intelligence Hub** is a unified platform that consolidates three Paperless-ngx–centric document analysis experiments into a single service with a unified web UI. It runs as a single Docker container on the homelab alongside the existing n8n instance.

This replaces the previous approach of building three separate services (Statement Tracking, EOB Matching, Action Queue) that each had their own FastAPI backend, SQLite database, and dashboard.

## Problem Statement

Three modules in this repository solve related problems around **Paperless-ngx document intelligence**:

| Experiment | Purpose | Issue |
|-----------|---------|-------|
| **Statement Tracking** (#11) | Track recurring statements, detect gaps | [#11](https://github.com/rsocko/ideation/issues/11) |
| **Medical EOB Matching** (#10, #27) | Match EOBs to bills, surface unmatched | [#10](https://github.com/rsocko/ideation/issues/10), [#27](https://github.com/rsocko/ideation/issues/27) |
| **Paperless Action Queue** (#26) | Triage documents needing action | [#26](https://github.com/rsocko/ideation/issues/26) |

All three share:
- The same Paperless-ngx API client
- Similar entity extraction (dates, amounts, providers)
- SQLite storage with overlapping schemas
- FastAPI REST APIs
- n8n workflow triggers
- Dashboard / alerting UIs

Building them separately means **3 deployments, 3 databases, 3 UIs, and 3 copies of the same Paperless integration code**. Instead, we unify them.

## Architecture

```
                    ┌──────────────────────────────────┐
                    │         Paperless-ngx             │
                    └──────────────┬───────────────────┘
                                   │ REST API
                    ┌──────────────▼───────────────────┐
                    │    Document Intelligence Hub       │
                    │       (single Docker container)    │
                    ├──────────────────────────────────┤
                    │  Core Layer                       │
                    │  ├─ Paperless API Client          │
                    │  ├─ Document Fetcher & Cache      │
                    │  ├─ Text Extraction (pdfplumber)  │
                    │  ├─ Entity Extractor (dates, $)   │
                    │  └─ Database (SQLite)             │
                    ├──────────────────────────────────┤
                    │  Feature Modules                  │
                    │  ├─ 📋 Statement Tracker          │
                    │  ├─ 🏥 EOB ↔ Bill Matcher         │
                    │  ├─ 📬 Action Queue / Triage      │
                    │  └─ 💰 (Future: Finance Summary)  │
                    ├──────────────────────────────────┤
                    │  API Layer (FastAPI)               │
                    │  ├─ /api/statements/*              │
                    │  ├─ /api/eob/*                     │
                    │  ├─ /api/actions/*                 │
                    │  ├─ /api/alerts/*  (unified)       │
                    │  └─ /api/dashboard/* (unified)     │
                    ├──────────────────────────────────┤
                    │  Frontend (Single Web App)         │
                    │  ├─ Dashboard (unified home)       │
                    │  ├─ /statements                    │
                    │  ├─ /eob-matching                  │
                    │  ├─ /action-queue                  │
                    │  └─ /settings                      │
                    └──────────────────────────────────┘
                                   │
                    ┌──────────────▼───────────────────┐
                    │       n8n (already running)        │
                    │  ├─ Scheduled document polling     │
                    │  ├─ New document webhook trigger   │
                    │  └─ Alert/notification routing     │
                    └──────────────────────────────────┘
```

## Service Count

| Service | Description | Deploy |
|---------|-------------|--------|
| **Document Intelligence Hub** | All document intelligence + web UI | 1 Docker container |
| **n8n** | Workflow automation (pre-existing) | Already running |

**Separate projects (not part of this hub):**
- **Personal Email Agent** (#34) — not Paperless-centric, will be its own project
- **Shipment Tracking** (#8) — different data sources (carrier APIs)
- **Task Sync / Aggregation** (#59–62) — broader scope than document intelligence

## Unified Web UI

One web app with sidebar navigation, combining all feature modules into a single user experience:

```
┌─────────────────────────────────────────────────────────┐
│  📊 Document Intelligence Hub                      ⚙️   │
├──────┬──────────────────────────────────────────────────┤
│      │                                                   │
│  🏠  │  Dashboard (Home)                                 │
│      │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌────────┐│
│  📋  │  │ Actions │ │ EOB     │ │ Missing │ │ Bills  ││
│      │  │ Pending │ │ Unmatched│ │ Stmts  │ │ Due    ││
│  🏥  │  │   3     │ │   5     │ │   2     │ │ $420   ││
│      │  └─────────┘ └─────────┘ └─────────┘ └────────┘│
│  📬  │                                                   │
│      │  ⚠️ Alerts (unified across all modules)           │
│  ⚙️  │  • Bill overdue: Dr. Smith ($125) — 3 days       │
│      │  • EOB/Bill mismatch: $25 difference             │
│      │  • Missing: Chase CC stmt (May 2026)             │
│      │  • Action needed: Sign insurance form            │
│      │                                                   │
│      │  📋 Recent Activity                               │
│      │  • Matched: UHC EOB → City Hospital bill         │
│      │  • Statement found: Amex (May 2026)              │
│      │  • Action completed: Filed tax form              │
└──────┴──────────────────────────────────────────────────┘
```

### Navigation
| Route | Module | Description |
|-------|--------|-------------|
| `/` | Dashboard | Unified summary, cross-module alerts, recent activity |
| `/action-queue` | Action Queue | Document triage: PAY, RESPOND, FILE, REVIEW, etc. |
| `/eob-matching` | EOB Matcher | Match review, unmatched docs (#27), payment tracking |
| `/statements` | Statement Tracker | Recurring statement catalog, gap detection, reminders |
| `/settings` | Settings | Paperless connection, notification preferences |

### Why unified:
- **Single entry point** — one URL to see everything that needs attention
- **Cross-feature alerts** — overdue bill + missing statement + action needed = one sorted list
- **Shared components** — tables, badges, search, filters reused across modules
- **Consistent design** — existing mockups already share the same CSS design system

## Shared Core

### Paperless API Client
```
core/paperless/
├── client.py          # HTTP client, auth, retry, rate limiting
├── documents.py       # Fetch, search, filter documents
├── tags.py            # Tag management
├── custom_fields.py   # Custom field CRUD
├── links.py           # Document linking (bidirectional)
└── cache.py           # Local metadata cache
```

### Entity Extractors
```
core/extractors/
├── base.py            # Base extractor interface
├── date_extractor.py  # Dates: service dates, due dates, statement periods
├── amount_extractor.py # Dollar amounts, balances, totals
├── provider_extractor.py # Provider/company name matching
├── account_extractor.py  # Account numbers, claim numbers, invoice numbers
└── fuzzy_match.py     # String similarity (fuzzywuzzy)
```

### Database Schema (single SQLite)
```sql
-- ─── Shared ────────────────────────────
documents           -- cached Paperless doc metadata
alerts              -- unified alert queue (all modules write here)
settings            -- user preferences

-- ─── Statement Tracker ─────────────────
statement_series    -- recurring statement definitions
statement_entries   -- individual statement instances + gap tracking

-- ─── EOB Matcher ───────────────────────
eob_records         -- extracted EOB data
bill_records        -- extracted bill data
matches             -- EOB-to-bill links with confidence scores

-- ─── Action Queue ──────────────────────
actions             -- recommended actions from documents
action_feedback     -- user corrections (for future ML learning)
```

## Project Structure

```
owl/
├── README.md                          # This file (architecture overview)
├── .gitignore                         # Protects secrets, DB files, etc.
│
├── docs/                              # All design documentation
│   ├── shared/README.md               # Shared principles + tech stack
│   ├── eob-matching/                  # EOB ↔ Bill Matcher designs
│   │   ├── DESIGN.md                  # Architecture, algorithms, scoring
│   │   ├── UI-DESIGN.md              # Dashboard & UX specifications
│   │   ├── TECHNOLOGY-STACK.md       # Tech decisions + ADRs
│   │   ├── SETUP-PAPERLESS.md        # Paperless integration guide
│   │   ├── QUICK-REFERENCE.md        # Implementation quick-start
│   │   └── SUMMARY.md                # Module status & roadmap
│   ├── action-queue/                  # Action Queue / Triage designs
│   │   ├── DESIGN.md                  # System architecture + data flow
│   │   ├── UI-DESIGN.md              # Dashboard specifications
│   │   ├── TECHNOLOGY-STACK.md       # Tech decisions + ADRs
│   │   ├── QUICK-REFERENCE.md        # Implementation quick-start
│   │   ├── SUMMARY.md                # Module status & roadmap
│   │   ├── OCR-QUALITY-DESIGN.md     # OCR assessment sub-system
│   │   ├── OCR-QUALITY-SCORING.md    # OCR scoring algorithm
│   │   ├── OCR-REMEDIATION-ENGINE.md # OCR remediation pipeline
│   │   ├── OCR-OLLAMA-INTEGRATION.md # Ollama validation for borderline OCR
│   │   ├── OCR-N8N-WORKFLOW.md       # n8n OCR workflow spec
│   │   └── OCR-BASELINE-INVENTORY.md # Baseline analysis script spec
│   └── statement-tracking/            # Statement Tracker designs
│       ├── DESIGN.md                  # Architecture + algorithms
│       ├── TECHNOLOGY-STACK.md       # Tech decisions + ADRs
│       ├── SETUP-PAPERLESS.md        # Paperless integration guide
│       ├── QUICK-REFERENCE.md        # Phase 1 prototype guide
│       ├── PHASE1-IMPLEMENTATION.md  # Phase 1 scope & plan
│       └── SUMMARY.md                # Module status & roadmap
│
├── mockups/                           # Interactive HTML mockups
│   ├── eob-matching/
│   │   ├── dashboard.html            # EOB matching dashboard
│   │   ├── match-review.html         # Side-by-side match comparison
│   │   └── unmatched.html            # Unmatched documents view (#27)
│   └── action-queue/
│       └── dashboard.html            # Action queue dashboard
│
├── examples/                          # Example data structures
│   ├── sample-actions.json           # Action queue recommendations
│   └── sample-documents.json         # Paperless API document structures
│
├── config/                            # Configuration references
│   ├── config.fixture.yaml           # Fixture test config
│   ├── config.paperless.example.yaml # Paperless connection template
│   ├── docker-compose.image.yaml     # Docker Compose reference
│   ├── statement-tracker-pyproject.toml # Python deps reference
│   ├── schedules.yaml                # Central schedule definitions
│   └── crontab.example               # Fallback crontab entries
│
└── src/                               # (Phase 2: code migration)
    ├── core/                          # Shared infrastructure
    │   ├── paperless/                 # Paperless API client
    │   ├── extractors/                # Entity extraction pipeline
    │   ├── scheduler.py               # Built-in APScheduler job runner
    │   ├── database.py                # DB connection + migrations
    │   └── models.py                  # Shared data models
    │
    ├── modules/                       # Feature modules
    │   ├── statements/                # Statement Tracker (from existing code)
    │   ├── eob_matching/              # EOB ↔ Bill Matcher
    │   └── action_queue/              # Action Queue / Triage
    │
    ├── api/                           # FastAPI routes
    │   ├── main.py                    # App factory + router mounting
    │   ├── dashboard.py               # Unified dashboard endpoints
    │   ├── alerts.py                  # Unified alert endpoints
    │   ├── statements.py              # /api/statements/*
    │   ├── eob.py                     # /api/eob/*
    │   └── actions.py                 # /api/actions/*
    │
    └── frontend/                      # Static web UI
        ├── index.html                 # SPA shell
        ├── css/                       # Shared styles
        └── js/                        # Page scripts
```

## Technology Stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| **Language** | Python 3.11+ | Best PDF/ML ecosystem, existing code in Statement Tracker |
| **API Framework** | FastAPI | Already designed for in all 3 experiments |
| **Database** | SQLite | Simple, no separate process, sufficient for single-user |
| **PDF Extraction** | pdfplumber | Best text extraction for structured PDFs |
| **Fuzzy Matching** | fuzzywuzzy/thefuzz | Provider name matching |
| **Date Parsing** | python-dateutil | Flexible date extraction |
| **Frontend** | Vanilla JS → React | Start simple, upgrade if needed |
| **Deployment** | Docker | Single container on homelab |
| **Orchestration** | n8n | Already running; handles scheduling + notifications |

## Scheduling

The hub includes a **built-in scheduler** (APScheduler) that runs all module pipelines automatically — no external orchestrator (n8n, cron) required. Schedules are configurable at runtime via the admin API and UI.

### Schedule Overview

| Module | Job | Default Schedule | Endpoint |
|--------|-----|-----------------|----------|
| **Statement Tracker** | Discovery | Daily 9:00 AM | `POST /api/statements/discovery/run` |
| **Statement Tracker** | Gap Check | Daily 9:30 AM | `POST /api/statements/recommendations/run?as_of=TODAY` |
| **EOB Matching** | Full pipeline | Weekly Sun 10:00 AM | `POST /api/eob/run` |
| **Action Queue** | Triage pipeline | Daily 8:00 AM & 2:00 PM | `POST /api/queue/run` |

### How It Works

The scheduler starts automatically when the hub boots. It calls the hub's own API endpoints via `httpx` on each cron tick, so all middleware, error handling, and state management is exercised identically to external callers.

- **No external dependencies** — everything runs inside the single Docker container
- **Runtime configuration** — change cron expressions, limits, and enable/disable via the admin UI or API
- **Last-run tracking** — the scheduler records status, timestamps, and errors for each job

### Admin API

```bash
# View current schedules (includes next_run, last_run info)
***REMOVED*** http://localhost:8071/api/admin/schedules

# Update a schedule (takes effect immediately)
***REMOVED*** -X PUT http://localhost:8071/api/admin/schedules \
  -H 'Content-Type: application/json' \
  -d '{"statement_discovery": {"cron": "0 8 * * *", "enabled": true}}'

# Disable a schedule
***REMOVED*** -X PUT http://localhost:8071/api/admin/schedules \
  -H 'Content-Type: application/json' \
  -d '{"eob_matching": {"enabled": false}}'
```

### Admin UI

The **Scan Schedules** page in the admin panel (`/admin` → 🕐) shows all four module schedules with:
- Editable cron expressions and document limits
- Enable/disable toggles
- Next scheduled run time
- Last run status and timestamp
- "Run Now" buttons for immediate execution

### Fallback: Crontab

For environments where the built-in scheduler is not suitable, `config/crontab.example` provides equivalent cron entries that call the hub API via `***REMOVED***`.

## What Changes from Existing Designs

### Statement Tracker (has existing code)
- Migrate `src/statement_tracker/` into `src/modules/statements/`
- Replace standalone `paperless.py` with shared `core/paperless/` client
- Keep existing detector, models, recommendations logic

### EOB Matching (design only)
- All design docs, algorithms, and mockups remain valid
- Namespace API routes under `/api/eob/*`
- Use shared extractors instead of building standalone ones
- Mockups (dashboard.html, match-review.html, unmatched.html) inform the EOB section UI

### Action Queue (design only)
- All design docs and action categories remain valid
- Namespace API routes under `/api/actions/*`
- Share document classification with EOB matcher where applicable
- Integrate alerts into unified alert system

## Development Phases

### Phase 1: Foundation + Statement Tracker (Weeks 1–2)
- Create unified project structure
- Build shared Paperless API client
- Build shared entity extractors
- Set up database schema
- Migrate Statement Tracker code into module
- Basic web shell (sidebar nav + dashboard placeholder)
- Docker Compose setup

### Phase 2: EOB Matching Module (Weeks 3–4)
- Document classifier (EOB vs Bill)
- EOB/Bill data extraction using shared extractors
- Matching engine + multi-factor scoring
- Paperless document linking
- EOB matching UI (from existing mockups)
- Unmatched documents view (Issue #27)

### Phase 3: Action Queue Module (Weeks 5–6)
- Inbox/Todo document scanner
- Action type classification (PAY, RESPOND, FILE, etc.)
- Priority/urgency scoring
- Action queue UI
- Unified alert system across all modules

### Phase 4: Unified Dashboard + Polish (Weeks 7–8)
- Cross-feature dashboard (combined stats from all modules)
- Notification integration (n8n → email/push)
- Settings page
- Testing with real documents
- Docker image optimization

## Testing

### Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run only the API integration tests
pytest tests/api/ -v

# Run tests for a specific module
pytest tests/api/test_eob.py -v
```

### Test Suite

| Directory | Coverage | Tests |
|-----------|----------|-------|
| `tests/api/` | All REST endpoints (85 integration tests) | Health, statements, EOB, action queue, alerts, admin, stats, MC connector |
| `tests/statements/` | Statement tracker unit tests | Detector, database, recommendations |
| `tests/eob_matching/` | EOB matching unit tests | Classifier, extractor, matcher |
| `tests/action_queue/` | Action queue unit tests | Pipeline, analyzer |
| `tests/core/` | Core module unit tests | Paperless client, LLM |

The API integration tests (`tests/api/`) use **FastAPI TestClient** with fully mocked external dependencies (Paperless, LLM gateway) and isolated temp SQLite databases. They require no network access.

## Related Issues

- [#10](https://github.com/rsocko/ideation/issues/10) — Medical EOB & Bill matching
- [#11](https://github.com/rsocko/ideation/issues/11) — Statement tracking
- [#26](https://github.com/rsocko/ideation/issues/26) — Queue of TODO documents
- [#27](https://github.com/rsocko/ideation/issues/27) — Show unmatched EOB/Bill items
- [#160](https://github.com/rsocko/ideation/issues/160) — Paperless Enhanced OCR

## Source Experiments (now deprecated — design docs consolidated here)

- `experiments/home-automation/statement-tracking/` — Phase 1 code still lives here (migrate in Phase 2)
- `experiments/home-automation/medical-eob-matching/` — Design complete, docs moved here
- `experiments/home-automation/paperless-action-queue/` — Design complete, docs moved here

---

*Created: 2026-06-20*
*Status: Architecture Decision — Ready for Implementation*
