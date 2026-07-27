# n8n Integration — Webhook Automation

> **Module:** Statement Tracking · **Phase:** 2 (Automation)

The Document Intelligence Hub exposes webhook endpoints that integrate with
[n8n](https://n8n.io/) (or any HTTP-capable automation engine) to automate
the missing-statement retrieval workflow.

## Architecture Overview

```
┌─────────────────────────┐        ┌──────────────────┐
│  DI Hub  (FastAPI)      │        │  n8n  Workflow    │
│                         │        │                   │
│  Scheduled gap check ───┼──POST──▶  Webhook trigger  │
│    (recommendations)    │        │       │           │
│                         │        │  Fetch statement  │
│  POST /statement-found ◀┼──POST──│  from provider    │
│                         │        │       │           │
│  Clear alert state      │        │  Upload to        │
│  Notify subscribers     │        │  Paperless-ngx    │
└─────────────────────────┘        └──────────────────┘
```

## Setup

### 1. Configure the default n8n webhook URL

Set the `N8N_WEBHOOK_URL` environment variable to point at your n8n webhook
trigger node:

```bash
# .env
N8N_WEBHOOK_URL=https://n8n.example.com/webhook/statement-missing
```

The hub will POST events to this URL **in addition to** any registered
webhook subscriptions.

### 2. Register additional subscribers (optional)

Use the subscriptions API to register extra webhook URLs:

```bash
***REMOVED*** -X POST http://localhost:8001/api/webhooks/subscriptions \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://n8n.example.com/webhook/other-workflow",
    "event_type": "statement.missing",
    "description": "Secondary n8n workflow for bank statements"
  }'
```

Use `event_type: "*"` to subscribe to all event types.

### 3. Configure the callback in n8n

At the end of your n8n workflow (after the statement has been retrieved and
uploaded to Paperless), add an HTTP Request node that calls back:

```
POST http://localhost:8001/api/webhooks/statement-found
Content-Type: application/json

{
  "provider_key": "{{ $json.provider_key }}",
  "expected_date": "{{ $json.expected_date }}",
  "document_id": "{{ $json.paperless_document_id }}",
  "source": "n8n"
}
```

## API Endpoints

### Subscriptions

| Method   | Path                                              | Description                      |
|----------|---------------------------------------------------|----------------------------------|
| `GET`    | `/api/webhooks/subscriptions`                     | List active subscriptions        |
| `POST`   | `/api/webhooks/subscriptions`                     | Register a new subscription      |
| `DELETE` | `/api/webhooks/subscriptions/{id}`                | Remove a subscription            |
| `PATCH`  | `/api/webhooks/subscriptions/{id}/toggle?active=` | Enable / disable a subscription  |

### Webhook Triggers

| Method | Path                             | Description                                          |
|--------|----------------------------------|------------------------------------------------------|
| `POST` | `/api/webhooks/statement-missing`| Notify subscribers of a missing / overdue statement   |
| `POST` | `/api/webhooks/statement-found`  | Callback: a statement was found and ingested          |

### Logs

| Method | Path                  | Description                           |
|--------|-----------------------|---------------------------------------|
| `GET`  | `/api/webhooks/logs`  | View recent webhook delivery attempts |

## Payload Schemas

### Outbound: `statement.missing` / `statement.overdue`

```json
{
  "event": "statement.missing",
  "timestamp": "2026-07-26T12:00:00+00:00",
  "data": {
    "provider_key": "electric-co::monthly-bill",
    "provider_name": "Electric Co",
    "expected_date": "2026-07-15",
    "status": "missing",
    "priority": 7,
    "days_late": 11,
    "earliest_date": "2026-07-14",
    "latest_date": "2026-07-25"
  }
}
```

### Outbound: `statement.found`

```json
{
  "event": "statement.found",
  "timestamp": "2026-07-26T14:30:00+00:00",
  "data": {
    "provider_key": "electric-co::monthly-bill",
    "expected_date": "2026-07-15",
    "document_id": "42",
    "source": "n8n"
  }
}
```

### Inbound: `POST /api/webhooks/statement-missing`

```json
{
  "provider_key": "electric-co::monthly-bill",
  "provider_name": "Electric Co",
  "expected_date": "2026-07-15",
  "status": "missing",
  "priority": 7,
  "days_late": 11
}
```

### Inbound: `POST /api/webhooks/statement-found`

```json
{
  "provider_key": "electric-co::monthly-bill",
  "expected_date": "2026-07-15",
  "document_id": "42",
  "source": "n8n",
  "notes": "Downloaded from provider portal"
}
```

## De-duplication

The hub tracks which `(provider_key, expected_date, event_type)` tuples have
already been alerted in the `webhook_alert_state` table. This ensures:

- The daily scheduled gap check won't fire the same webhook twice for the
  same missing statement.
- When `POST /api/webhooks/statement-found` is called, the alert state is
  cleared so future recommendation cycles won't try to alert again.

## Example n8n Workflow

```
┌───────────────┐     ┌──────────────────┐     ┌──────────────────┐
│ Webhook Node  │────▶│ IF status ==     │────▶│ HTTP Request     │
│ (trigger)     │     │ "missing"        │     │ Login to provider│
└───────────────┘     └──────────────────┘     └────────┬─────────┘
                                                        │
                      ┌──────────────────┐     ┌────────▼─────────┐
                      │ HTTP Request     │◀────│ Download PDF     │
                      │ POST to          │     │ statement        │
                      │ /statement-found │     └──────────────────┘
                      └──────────────────┘
                              │
                      ┌───────▼──────────┐
                      │ Upload to        │
                      │ Paperless-ngx    │
                      └──────────────────┘
```

### n8n Workflow JSON (minimal example)

```json
{
  "name": "Statement Retrieval",
  "nodes": [
    {
      "type": "n8n-nodes-base.webhook",
      "name": "Webhook Trigger",
      "parameters": {
        "path": "statement-missing",
        "httpMethod": "POST"
      }
    },
    {
      "type": "n8n-nodes-base.httpRequest",
      "name": "Report Found",
      "parameters": {
        "url": "http://di-hub:8001/api/webhooks/statement-found",
        "method": "POST",
        "sendBody": true,
        "bodyParameters": {
          "parameters": [
            { "name": "provider_key", "value": "={{ $json.data.provider_key }}" },
            { "name": "expected_date", "value": "={{ $json.data.expected_date }}" },
            { "name": "source", "value": "n8n" }
          ]
        }
      }
    }
  ]
}
```

## Environment Variables

| Variable           | Default                | Description                                 |
|--------------------|------------------------|---------------------------------------------|
| `N8N_WEBHOOK_URL`  | *(none)*               | Default outbound webhook URL for n8n        |
| `WEBHOOK_DB_PATH`  | `data/webhook_log.db`  | SQLite database for subscriptions and logs  |
