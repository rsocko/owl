---
title: "OWL Scenarios Overview"
sidebar_label: Scenarios
sidebar_position: 4
---

Here's the categorized summary of the **Document Intelligence Hub**:

---

## User Scenarios

### Statement Tracking

- **Detect missing recurring statements** (bank, credit card, utilities, insurance) — never miss a bill
- **Gap alerts** — get notified when an expected statement is overdue
- **Financial reconciliation** — verify all quarterly/annual statements received
- 

### EOB ↔ Bill Matching (Medical)

- **Auto-match** insurance EOBs to medical bills (5-factor weighted scoring)
- **Detect billing errors** — amount/provider mismatches flagged
- **Track payment lifecycle** — pending, paid, overdue
- **Find orphans** — EOBs without bills (coverage pending) or bills without EOBs (gap in insurance)

### Action Queue (Document Triage)

- **Inbox zero workflow** — documents classified as PAY / RESPOND / FILE / REVIEW / SIGN / WAIT
- **Urgency scoring** — high-risk items surface first (deadline proximity + dollar amount)
- **Bulk operations** — multi-select and batch-mark-complete

### Unified Alerts & Insights

- Cross-module alert inbox (overdue bills, mismatches, missing statements, high-risk actions)
- Insurance coverage analysis, payment timeliness trends, cost tracking

---

## Configuration & Management

- **Scheduling** — Built-in APScheduler (daily/2x-daily per module); admin API to change cron at runtime
- **Rule Engine** — YAML or web UI to create custom analysis rules (triggers, thresholds, time windows)
- **Triage & Correction** — Admin workflows for match rejection, series split/merge, duplicate merge, metadata fixes
- **Paperless connection settings** — URL + token, connectivity test
- **OCR Quality system** (Phase 5 design) — tiered remediation: Tesseract → Azure DI with budget controls
- **Tagvico integration** — delegate non-medical docs for general classification/OCR rescue
- **Copilot SDK provider** (proposed) — swap Ollama for GPT via existing Copilot subscription
- **Retention & cleanup** — configurable data cleanup policies, storage usage

---

## Other (Architecture / Platform)

- **Single Docker container** with 4 CLI entry points (`doc-hub`, `eob-match`, `paq`, server)
- **Tech stack**: FastAPI (Python) + React/TS + SQLite + Ollama (local LLM) + pdfplumber + spaCy
- **Mission Control integration** — DI Hub is a headless API; MC is the unified frontend
- **85+ API tests**, 100% endpoint coverage
- **Fully local/private** — no cloud APIs for sensitive data (cloud LLM optional)
- **Phased roadmap** — Phases 0–4 complete; Phase 5 (infrastructure) in progress; future plans include transaction matching, tax export, mobile app


---

## Feature Areas:
- 