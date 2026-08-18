---
title: "OCR n8n Workflow"
sidebar_label: OCR n8n
sidebar_position: 6
---

# OCR n8n Workflow Specification

## Overview

This document specifies the n8n workflows that orchestrate the OCR quality assessment and remediation pipeline. n8n handles scheduling, Paperless custom field updates, batch document routing, and Home Assistant alerting. The actual scoring and remediation logic lives in the Python services (scorer-service and remediation-worker); n8n is the orchestration layer that drives them.

---

## Workflow Inventory

| Workflow | Trigger | Purpose |
|---------|---------|---------|
| `OCR-Weekly-Scanner` | Schedule (weekly) | Assess all unscored or stale documents |
| `OCR-New-Document-Hook` | Paperless webhook (on consume) | Score new documents immediately |
| `OCR-Custom-Field-Sync` | Called by scanner after scoring | Write OCR score/grade to Paperless custom fields |
| `OCR-Alert-Summary` | Called by scanner after weekly run | Send Home Assistant notification with grade summary |
| `OCR-Manual-Trigger` | Webhook (manual call) | Trigger assessment or remediation for a specific doc |

---

## Workflow 1 — OCR-Weekly-Scanner

### Purpose

Runs once per week. Fetches all documents from Paperless, identifies those that are unscored or whose score is stale (>90 days old), and calls the scorer service for each. Documents graded C or F are placed in the remediation queue.

### Trigger

- **Type:** Cron Schedule
- **Schedule:** `0 2 * * 0` (Sunday 2:00 AM)
- **Timezone:** Your local timezone

### Node Flow

```
[Schedule Trigger]
      │
      ▼
[Set Variables]
  • base_url = http://paperless-ngx:8000
  • scorer_url = http://scorer-service:8001
  • page_size = 100
  • rescore_after_days = 90
      │
      ▼
[HTTP Request — Paperless: Count documents]
  GET {{base_url}}/api/documents/?page_size=1
  → extracts: total_count
      │
      ▼
[Set: page_count]
  page_count = Math.ceil(total_count / page_size)
      │
      ▼
[Loop — Pages]  (Split in Batches, batch size = 1, loop count = page_count)
      │
      ▼
  [HTTP Request — Paperless: Fetch page]
    GET {{base_url}}/api/documents/?page={{$runIndex+1}}&page_size={{page_size}}
    → returns: {results: [{id, modified, custom_fields: [...], ...}]}
      │
      ▼
  [Split In Batches — Documents]  (item = each result)
      │
      ▼
    [Function — Check if scoring needed]
      • Read "OCR Reviewed" custom field from document
      • If "OCR Reviewed" is missing OR older than rescore_after_days → needs_score = true
      • If document.modified > OCR Reviewed date → needs_score = true (doc was updated)
      │
      ▼
    [IF: needs_score == true]
      │
      ├── TRUE ──▶ [HTTP Request — Scorer: Score document]
      │              POST {{scorer_url}}/api/ocr/score/{{document_id}}
      │              → returns: {score, grade, pdf_type, remediation_status}
      │              │
      │              ▼
      │            [IF: grade is C or F AND pdf_type != DIGITAL]
      │              │
      │              ├── TRUE ──▶ [HTTP Request — Scorer: Queue remediation]
      │              │              POST {{scorer_url}}/api/ocr/remediate/{{document_id}}
      │              │              Body: {"tier": "auto"}
      │              │
      │              └── FALSE ──▶ (skip)
      │              │
      │              ▼
      │            [Execute Workflow — OCR-Custom-Field-Sync]
      │              (passes: document_id, score, grade, pdf_type, remediation_status)
      │
      └── FALSE ──▶ (skip document)

[After all documents processed]
      │
      ▼
[HTTP Request — Scorer: Get stats]
  GET {{scorer_url}}/api/ocr/stats
  → returns: {grade_distribution: {A:N, B:N, C:N, F:N, EXEMPT:N}, ...}
      │
      ▼
[Execute Workflow — OCR-Alert-Summary]
  (passes: stats)
```

### Error Handling

- Set **"Continue On Fail"** to `true` on all HTTP Request nodes so one failed document does not abort the entire run
- Add an **Error Trigger** workflow that catches failed executions and sends a HA notification: "OCR scanner run failed: {{error.message}}"

---

## Workflow 2 — OCR-New-Document-Hook

### Purpose

Scores a document immediately when it is consumed by Paperless. Uses Paperless's webhook (post-consume script) or n8n's webhook trigger.

### Trigger

- **Type:** Webhook (POST)
- **URL:** `https://n8n.yourhomelab/webhook/ocr-new-doc`
- **Authentication:** Header Auth (set a shared secret)

### Paperless Configuration

In `paperless.conf` or environment:
```
PAPERLESS_POST_CONSUME_SCRIPT=/usr/local/bin/paperless-post-consume.sh
```

`/usr/local/bin/paperless-post-consume.sh`:
```bash
#!/bin/bash
# Called by Paperless after a document is consumed
# Environment variables available: DOCUMENT_ID, DOCUMENT_FILE_NAME, DOCUMENT_CREATED, etc.
curl -s -X POST \
  -H "X-OCR-Webhook-Secret: ${OCR_WEBHOOK_SECRET}" \
  -H "Content-Type: application/json" \
  -d "{\"document_id\": ${DOCUMENT_ID}, \"filename\": \"${DOCUMENT_FILE_NAME}\"}" \
  "https://n8n.yourhomelab/webhook/ocr-new-doc"
```

### Node Flow

```
[Webhook Trigger]
  Receives: {document_id, filename}
      │
      ▼
[Wait — 30 seconds]
  (Give Paperless time to finish indexing the new document)
      │
      ▼
[HTTP Request — Scorer: Score document]
  POST {{scorer_url}}/api/ocr/score/{{document_id}}
      │
      ▼
[IF: grade is C or F AND pdf_type != DIGITAL]
  TRUE  → [HTTP Request — Queue remediation]
  FALSE → (skip)
      │
      ▼
[Execute Workflow — OCR-Custom-Field-Sync]
```

---

## Workflow 3 — OCR-Custom-Field-Sync

### Purpose

Updates Paperless custom fields for a single document with its OCR score data. Called by other workflows — not triggered independently.

### Input Parameters (passed from calling workflow)

```json
{
  "document_id": 1234,
  "score": 72.5,
  "grade": "B",
  "pdf_type": "SCANNED_OCR",
  "remediation_status": "NONE",
  "ocr_engine": "abbyy",
  "reviewed_date": "2026-06-10"
}
```

### Node Flow

```
[Execute Workflow Trigger]
  (receives input parameters above)
      │
      ▼
[HTTP Request — Paperless: Get document]
  GET {{base_url}}/api/documents/{{document_id}}/
  → retrieve current custom_fields array to find field IDs
      │
      ▼
[Function — Build custom fields PATCH body]
  Maps field names to their IDs from the document response.
  Builds the update payload:
  {
    "custom_fields": [
      {"field": <OCR_SCORE_FIELD_ID>,        "value": "{{score}}"},
      {"field": <OCR_GRADE_FIELD_ID>,        "value": "{{grade}}"},
      {"field": <OCR_REVIEWED_FIELD_ID>,     "value": "{{reviewed_date}}"},
      {"field": <OCR_ENGINE_FIELD_ID>,       "value": "{{ocr_engine}}"},
      {"field": <OCR_REMEDIATION_FIELD_ID>,  "value": "{{remediation_status}}"}
    ]
  }
      │
      ▼
[HTTP Request — Paperless: PATCH document]
  PATCH {{base_url}}/api/documents/{{document_id}}/
  Body: (from Function node above)
  Auth: Token {{paperless_api_token}}
```

### Custom Field ID Discovery

Paperless custom field IDs are numeric. Fetch them once at setup:

```
GET /api/custom_fields/
→ [{id: 1, name: "OCR Score"}, {id: 2, name: "OCR Grade"}, ...]
```

Hardcode these IDs as n8n workflow variables after initial setup. They do not change unless the field is deleted and recreated.

---

## Workflow 4 — OCR-Alert-Summary

### Purpose

Sends a Home Assistant notification summarizing the weekly OCR scan results.

### Input Parameters

```json
{
  "grade_distribution": {"A": 842, "B": 156, "C": 34, "F": 12, "EXEMPT": 203},
  "remediation_queued": 46,
  "remediation_completed_this_week": 18,
  "azure_pages_used_this_month": 127,
  "azure_budget": 500
}
```

### Node Flow

```
[Execute Workflow Trigger]
      │
      ▼
[Function — Build notification message]
  Constructs a human-readable summary string.
      │
      ▼
[IF: any F-grade documents exist]
  TRUE  → Priority = "high"
  FALSE → Priority = "normal"
      │
      ▼
[HTTP Request — Home Assistant: Send notification]
  POST http://homeassistant:8123/api/services/notify/mobile_app_<your_device>
  Headers: Authorization: Bearer {{ha_long_lived_token}}
  Body:
    {
      "title": "📄 Paperless OCR Weekly Report",
      "message": "{{notification_message}}",
      "data": {
        "priority": "{{priority}}",
        "tag": "paperless-ocr-report"
      }
    }
```

### Example Notification Message Template

```javascript
// In the Function node:
const g = $input.first().json.grade_distribution;
const total = g.A + g.B + g.C + g.F;
const good_pct = Math.round(((g.A + g.B) / total) * 100);

const lines = [
  `Scored ${total} documents (${g.EXEMPT} exempt digital)`,
  `Quality: ${g.A} A · ${g.B} B · ${g.C} C · ${g.F} F  (${good_pct}% good)`,
  `Remediation queued: ${$input.first().json.remediation_queued}`,
  `Completed this week: ${$input.first().json.remediation_completed_this_week}`,
  `Azure usage: ${$input.first().json.azure_pages_used_this_month}/${$input.first().json.azure_budget} pages this month`,
];

if (g.F > 0) {
  lines.push(`⚠️ ${g.F} documents need attention`);
}

return [{ json: { message: lines.join("\n") } }];
```

---

## Workflow 5 — OCR-Manual-Trigger

### Purpose

Allows ad-hoc triggering of scoring or remediation for a specific document from Home Assistant, a dashboard, or a direct curl call.

### Trigger

- **Type:** Webhook (POST)
- **URL:** `https://n8n.yourhomelab/webhook/ocr-manual`

### Request Body

```json
{
  "document_id": 1234,
  "action": "score" | "remediate" | "remediate_azure",
  "secret": "your-webhook-secret"
}
```

### Node Flow

```
[Webhook Trigger]
      │
      ▼
[Function — Validate secret]
  if (body.secret !== process.env.OCR_WEBHOOK_SECRET) throw "Unauthorized";
      │
      ▼
[Switch — action]
  "score"           → POST /api/ocr/score/{{document_id}}?force=true
  "remediate"       → POST /api/ocr/remediate/{{document_id}} body={"tier":"auto"}
  "remediate_azure" → POST /api/ocr/remediate/{{document_id}} body={"tier":"azure"}
      │
      ▼
[Execute Workflow — OCR-Custom-Field-Sync]
      │
      ▼
[Respond to Webhook]
  Returns: {status: "ok", result: <scorer response>}
```

---

## Environment Variables for n8n Workflows

Store these as n8n **Credentials** or **Environment Variables**:

| Variable | Value |
|---------|-------|
| `PAPERLESS_BASE_URL` | `http://paperless-ngx:8000` (or your LAN URL) |
| `PAPERLESS_API_TOKEN` | Your Paperless API token |
| `SCORER_SERVICE_URL` | `http://scorer-service:8001` |
| `HA_BASE_URL` | `http://homeassistant:8123` |
| `HA_TOKEN` | Home Assistant long-lived access token |
| `OCR_WEBHOOK_SECRET` | Random secret string for webhook auth |
| `OCR_SCORE_FIELD_ID` | Paperless custom field ID for "OCR Score" |
| `OCR_GRADE_FIELD_ID` | Paperless custom field ID for "OCR Grade" |
| `OCR_REVIEWED_FIELD_ID` | Paperless custom field ID for "OCR Reviewed" |
| `OCR_ENGINE_FIELD_ID` | Paperless custom field ID for "OCR Engine" |
| `OCR_REMEDIATION_FIELD_ID` | Paperless custom field ID for "OCR Remediation" |

---

## n8n Docker Compose Integration

If n8n is running via Docker Compose alongside Paperless and the scorer service, ensure all services share a network:

```yaml
# Relevant excerpt for existing docker-compose.yml
networks:
  paperless_net:
    external: true   # or define inline if you own the compose file

services:
  n8n:
    networks:
      - paperless_net

  scorer-service:
    image: your-registry/ocr-scorer:latest
    networks:
      - paperless_net
    environment:
      - PAPERLESS_BASE_URL=http://paperless-ngx:8000
      - PAPERLESS_API_TOKEN=${PAPERLESS_API_TOKEN}
      - OLLAMA_BASE_URL=http://ollama:11434
      - AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=${AZURE_DOC_INTEL_ENDPOINT}
      - AZURE_DOCUMENT_INTELLIGENCE_KEY=${AZURE_DOC_INTEL_KEY}
    volumes:
      - ocr_data:/data
```
