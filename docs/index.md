---
title: OWL — Document Intelligence Hub
sidebar_label: Document Intelligence
sidebar_position: 1
---

# OWL 🦉 Documentation

> **Organize. Watch. Learn.** — Document intelligence for your Paperless-ngx archive.

OWL transforms a passive document archive into an actionable intelligence layer with its own built-in dashboard UI, and also feeds data into [Mission Control](https://service-004.example.invalid) for cross-service visibility.

## Documentation Map

| Section | Description |
|---------|-------------|
| [User Guide](./guide/) | How to use each module — start here |
| [Architecture](./architecture/) | System design, data flow, deployment topology |
| [Getting Started](./getting-started/) | Installation, configuration, first run |
| [Module Reference](./modules/) | Deep-dive design docs per module |
| [Design](./design/) | Active and proposed design documents |
| [Research](./research/) | Ecosystem analysis and competitive research |
| [Development](./development/) | Contributing, testing, CI/CD |

## Quick Links

- **API Base:** `http://service-005.example.invalid` (production) / `http://localhost:8071` (dev)
- **Source:** [`experiments/document-intelligence/`](https://github.com/rsocko/ideation/tree/main/experiments/document-intelligence)
- **Issues:** [GitHub Issues](https://github.com/rsocko/ideation/issues?q=label%3Adoc-intelligence)
- **Docker:** `service-007.example.invalid/doc-intelligence-hub:latest`

## Interactive Mockups

| Mockup | Feature Area | Description |
|--------|-------------|-------------|
| [Triage Unified](../mockups/triage-correction/triage-unified.html) | Action Queue / Triage | Combined triage workflow with keyboard shortcuts |
| [EOB Match Review](../mockups/triage-correction/eob-match-review.html) | EOB Matching | Side-by-side EOB/bill comparison with scoring |
| [Manual Match Search](../mockups/triage-correction/manual-match-search.html) | EOB Matching | Search interface for manually pairing documents |
| [Orphans & Dupes](../mockups/triage-correction/orphans-dupes-metadata.html) | EOB / Triage | Managing unmatched and duplicate documents |
| [Statement Series Detail](../mockups/triage-correction/statement-series-detail.html) | Statements | Provider timeline and gap visualization |
| [Insights Tab](../mockups/triage-correction/insights-tab.html) | Alerts & Insights | Alert feed with severity filters and trends |
| [Rules Config](../mockups/triage-correction/rules-config.html) | Configuration | Visual rule editor with triggers and thresholds |
| [Dashboard History](../mockups/triage-correction/dashboard-history.html) | Overview | Processing history and audit trail |

## Status

| Module | Maturity | Notes |
|--------|----------|-------|
| Statement Tracking | ✅ Most mature | Tested, deployed, API + dashboard |
| Action Queue | ⚡ Logic complete | Pipeline works, needs e2e validation |
| EOB Matching | ⚡ Logic complete | Matching works, needs live Paperless data |
| Alerts | 🔧 Scaffolded | API exists, cross-module emission incomplete |
| OCR Quality | 📋 Designed | Not yet implemented |
