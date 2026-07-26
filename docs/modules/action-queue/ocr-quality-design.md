---
title: "OCR Quality Design"
sidebar_label: OCR Quality Design
sidebar_position: 8
---

# OCR Quality Assessment & Remediation — Design Document

## Overview

This document describes the design for a recurring process to assess the OCR quality of documents stored in Paperless-ngx and automatically remediate documents where quality is poor. It is a sub-system of the broader `paperless-action-queue` project and shares its infrastructure and Paperless API client.

**Design Date:** 2026-06-10  
**Status:** Design Complete — Ready for Phase 0 implementation  
**Environment:** CPU-only homelab, Ollama available, Azure cloud access approved

---

## Problem Statement

Documents in Paperless-ngx fall into three categories by origin:

| Origin | Text Layer Source | OCR Quality Risk |
|--------|------------------|------------------|
| Digital-native PDF (print-to-PDF, exported) | Embedded at file creation (vector text) | None — text is perfect by definition |
| Physical scan via ScanSnap scanner | ABBYY FineReader (built into ScanSnap) | Low-to-medium — degrades on old/faded/thermal documents |
| Physical scan re-processed by Paperless | Tesseract 4/5 via OCRmyPDF | Medium — depends heavily on scan quality and Tesseract config |

**Key insight:** Not all ScanSnap documents are high quality. ABBYY FineReader degrades on faded documents, colored backgrounds, thermal paper, handwritten annotations, and mixed-orientation pages. The pipeline does not exempt any document from scoring based on assumed source quality.

**Critical risk:** Paperless-ngx's built-in `OCR_MODE=redo` replaces existing text unconditionally. Running it without a quality comparison gate can downgrade documents that already have good ABBYY-sourced OCR text.

---

## Design Goals

1. **Classify** every PDF as digital-native, scanned-OCR'd, or no-text — so downstream work targets only documents that can actually be improved
2. **Score** the OCR quality of every non-exempt document using heuristics (fast, free, no model inference required)
3. **Validate** borderline scores and before/after comparisons using Ollama (secondary signal, not primary gate)
4. **Remediate** poor-quality documents in a tiered engine: Tesseract 5 (free) → Azure Document Intelligence (cloud, cost-controlled)
5. **Gate** all re-OCR commits: never replace existing text unless the new text scores measurably better AND Ollama validates the improvement
6. **Surface** scores and remediation status in Paperless via custom fields, searchable in the UI
7. **Orchestrate** recurring runs via n8n with Home Assistant alerting

---

## Document Lifecycle in This System

```
Paperless-ngx Document
         │
         ▼
   ┌─────────────┐
   │ PDF Type    │
   │ Classifier  │ ──── PyMuPDF (fitz)
   └─────────────┘
         │
    ┌────┴────┬─────────────┐
    │         │             │
    ▼         ▼             ▼
DIGITAL   SCANNED_OCR    NO_TEXT
(exempt)  (has text)     (no text layer at all)
    │         │             │
    │         ▼             │
    │   ┌──────────┐        │
    │   │ Heuristic│        │
    │   │  Scorer  │        │
    │   └──────────┘        │
    │         │             │
    │    ┌────┴────┐        │
    │    │ Grade?  │        │
    │    └────┬────┘        │
    │    A/B  │  C/F        │
    │    ↓    │   ↓         │
    │  log  Ollama      queue ────────────────────┐
    │  only  confirm         │                    │
    │         │              │                    │
    │    ┌────┴────┐         │                    │
    │    │confirmed│         │                    │
    │    │  bad?   │         │                    │
    │    └────┬────┘         │                    │
    │    yes  │  no          │                    │
    │     ↓   │  ↓           │                    │
    │   queue log only       │                    │
    │                        ▼                    ▼
    │              ┌──────────────────────────────────┐
    │              │    REMEDIATION ENGINE            │
    │              │                                  │
    │              │  Tier 1: OCRmyPDF + Tesseract 5  │
    │              │    ↓ comparison gate             │
    │              │  Tier 2: Azure Doc Intelligence  │
    │              │    ↓ comparison gate             │
    │              │  REJECTED: keep original         │
    │              └──────────────────────────────────┘
    │                        │
    ▼                        ▼
score_db             Paperless Consume
(grade=EXEMPT)       (improved PDF dropped in)
                             │
                             ▼
                     Custom fields updated
                     ocr_score, ocr_grade,
                     ocr_engine, ocr_reviewed
```

---

## Component Architecture

### Services

| Service | Technology | Role |
|---------|-----------|------|
| `scorer-service` | FastAPI + PyMuPDF + wordfreq | Classifies PDF type, scores OCR quality, stores results |
| `remediation-worker` | Python worker process | Pulls queue, runs OCR engines, applies comparison gate, commits |
| `ollama` | Ollama (existing homelab) | Secondary scorer for borderline docs, before/after validator |
| `n8n` | n8n (existing homelab) | Schedules scanner runs, writes Paperless custom fields, sends alerts |
| `paperless-ngx` | Paperless (existing homelab) | Document store, source of truth, target for updates |
| `azure-doc-intelligence` | Azure cloud API | Tier 2 OCR engine (cloud, cost-controlled) |

### Data Store

Single SQLite database (upgradeable to Postgres) — `ocr_quality.db`.  
See [OCR-QUALITY-SCORING.md](OCR-QUALITY-SCORING.md) for schema.

### Shared Infrastructure with Action Queue Agent

This sub-system reuses the Paperless API client already designed for the action queue agent. Both services can share a single FastAPI app with separate routers:

```
/api/documents/{id}/score       ← OCR quality scorer
/api/documents/{id}/remediate   ← trigger remediation
/api/queue/actions              ← action queue agent (existing)
```

---

## Paperless Custom Fields

Add these custom fields in the Paperless-ngx admin UI before deploying the pipeline. These surface scores directly in Paperless search and list views.

| Field Name | Type | Values |
|-----------|------|--------|
| `OCR Score` | Integer | 0–100 |
| `OCR Grade` | Select / Text | A, B, C, F, EXEMPT |
| `OCR Reviewed` | Date | Date last scored |
| `OCR Engine` | Text | tesseract, azure, abbyy, digital, none |
| `OCR Remediation` | Select / Text | NONE, QUEUED, IMPROVED, FAILED, REJECTED |

---

## Technology Decisions

### Why no Surya/docTR (local neural OCR)?

Both require GPU for practical throughput (~1–3 seconds/page on GPU, 10–30 seconds/page on CPU). Without a dedicated GPU in the homelab, these are not viable for batch processing. Azure Document Intelligence provides better quality than either at ~$0.0015/page and is the right Tier 2 choice.

### Why Ollama for validation but not OCR?

Ollama vision models (LLaVA, MiniCPM-V) are inconsistent on document OCR tasks, produce no word-level confidence scores, and are too slow for bulk use on CPU. However, text-only models like `phi3:mini` are fast and capable at reading a 500-character text sample and assessing whether it is coherent English. That is a well-scoped task that adds genuine value at low cost.

### Why the comparison gate?

Tesseract 5 (LSTM mode) performs well on clean black-and-white printed text but underperforms ABBYY FineReader on degraded originals. Without a gate, running Tesseract on a good ScanSnap document can degrade it. The gate ensures the pipeline is net-positive: new OCR is only committed when it demonstrably improves quality.

### Why Azure Document Intelligence over AWS Textract?

- Azure credits may already exist from other homelab use
- Azure's Read API has the most consistent published accuracy benchmarks for mixed document types (invoices, letters, statements, forms) which is exactly the mix in a home document library
- AWS Textract Forms/Tables charges $15/1000 pages (vs $1.50 for Azure Read API basic text)

---

## Operational Considerations

### Budget Control for Azure

The remediation worker enforces a monthly page budget cap:

```python
AZURE_MONTHLY_PAGE_BUDGET = 500  # configurable, track in score_db

def can_use_azure(pages_requested: int) -> bool:
    used_this_month = db.count_azure_pages_this_month()
    return (used_this_month + pages_requested) <= AZURE_MONTHLY_PAGE_BUDGET
```

When the budget is exhausted, documents are queued with `remediation_status=DEFERRED_BUDGET` and retried next month.

### Idempotency

Every document assessment is idempotent. Re-running the scanner on an already-assessed document:
- Skips if `assessed_at` is within the re-assess window (default: 90 days)
- Re-runs if document `modified` timestamp is newer than `assessed_at`
- Re-runs unconditionally if triggered with `force=true`

### Rate Limiting

Paperless API calls and Azure API calls are both rate-limited:
- Paperless: 1 request/second (conservative, no published limit)
- Azure Document Intelligence: Analyze calls respect 429 responses with exponential backoff

---

## Related Documents

| Document | Content |
|---------|---------|
| [OCR-QUALITY-SCORING.md](OCR-QUALITY-SCORING.md) | Scoring algorithm, code samples, data schema |
| [OCR-REMEDIATION-ENGINE.md](OCR-REMEDIATION-ENGINE.md) | Tier engine design, comparison gate, OCRmyPDF and Azure integration |
| [OCR-OLLAMA-INTEGRATION.md](OCR-OLLAMA-INTEGRATION.md) | Prompt templates, Ollama roles, integration patterns |
| [OCR-N8N-WORKFLOW.md](OCR-N8N-WORKFLOW.md) | n8n workflow specification, node-by-node design |
| [OCR-BASELINE-INVENTORY.md](OCR-BASELINE-INVENTORY.md) | Phase 0 one-shot inventory script specification |
| [DESIGN.md](experiments/personal-automation/mission-control/DESIGN.md) | Action Queue Agent overall system design |
| [TECHNOLOGY-STACK.md](TECHNOLOGY-STACK.md) | Full technology stack decisions |
