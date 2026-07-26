---
title: OWL — Document Intelligence Hub
sidebar_label: OWL Docs
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

## Status

| Module | Maturity | Notes |
|--------|----------|-------|
| Statement Tracking | ✅ Most mature | Tested, deployed, API + dashboard |
| Action Queue | ⚡ Logic complete | Pipeline works, needs e2e validation |
| EOB Matching | ⚡ Logic complete | Matching works, needs live Paperless data |
| Alerts | 🔧 Scaffolded | API exists, cross-module emission incomplete |
| OCR Quality | 📋 Designed | Not yet implemented |
