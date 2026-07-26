---
title: "Shared Components"
sidebar_label: Shared
sidebar_position: 1
---

# Shared: Paperless-ngx Integration

This directory contains design documentation that applies across all modules
of the Document Intelligence Hub.

## Paperless-ngx Setup

Both the EOB Matching and Statement Tracking modules have their own SETUP-PAPERLESS.md guides.
These will be consolidated into a single shared setup during the code implementation phase:

- [EOB Matching Paperless Setup](../eob-matching/SETUP-PAPERLESS.md)
- [Statement Tracking Paperless Setup](../statement-tracking/SETUP-PAPERLESS.md)

## Shared Design Principles

All modules follow these architectural decisions:

1. **Self-Hosted First** — No cloud APIs for document processing or PHI
2. **Single Paperless API Client** — Shared across all modules (auth, caching, retry)
3. **Entity Extraction Pipeline** — Shared extractors for dates, amounts, providers
4. **Unified Database** — Single SQLite database with module-specific tables
5. **Unified Alert System** — All modules contribute to a single alert/action queue
6. **n8n for Orchestration** — Scheduling, webhooks, notifications via existing n8n instance

## Technology Stack (Unified)

| Component | Choice | Notes |
|-----------|--------|-------|
| Language | Python 3.11+ | Best PDF/ML ecosystem |
| API Framework | FastAPI | Serves both REST API and static frontend |
| Database | SQLite | Single file, no separate process |
| PDF Extraction | pdfplumber | Structured PDF text extraction |
| Fuzzy Matching | thefuzz (fuzzywuzzy) | Provider/company name matching |
| Date Parsing | python-dateutil | Flexible date extraction |
| Frontend | Vanilla JS → React | Start simple, upgrade as needed |
| Deployment | Docker | Single container on homelab |
| Orchestration | n8n | Already running; scheduling + notifications |

## Module-Specific Docs

Each module retains its own detailed design documentation:

- **EOB Matching**: `docs/eob-matching/` — matching algorithms, scoring, confidence
- **Action Queue**: `docs/action-queue/` — document triage, action categories, OCR quality
- **Statement Tracking**: `docs/statement-tracking/` — recurrence detection, gap analysis
