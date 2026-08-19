---
title: Configuration Reference
sidebar_label: Configuration
sidebar_position: 6
mockups:
  - "[rules-config.html](../../mockups/triage-correction/rules-config.html)"
---

# Configuration Reference

OWL is configured through environment variables, YAML config files, and runtime admin API endpoints.

:::info Interactive Mockup
Preview the rules configuration UI: [Rules Config](../../mockups/triage-correction/rules-config.html) — YAML/visual rule editor with trigger conditions and thresholds.
:::

## Environment Variables

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `PAPERLESS_URL` | Paperless-ngx base URL | `http://paperless:8000` |
| `PAPERLESS_BROWSER_URL` | Browser-reachable Paperless origin for Document Views links | `https://paperless.example.com` |
| `PAPERLESS_API_TOKEN` | Paperless API authentication token | `abc123...` |

### LLM Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_BASE_URL` | OpenAI-compatible API base URL | `https://service-001.example.invalid/openai/v1` |
| `LLM_API_KEY` | API key for LLM endpoint | `bifrost` |
| `LLM_MODEL` | Model identifier | `azure/gpt-4o-mini` |

:::info
OWL uses an OpenAI-compatible API. Any endpoint that supports the chat completions format works — OpenAI direct, Azure OpenAI via Bifrost, local Ollama, or any compatible gateway.
:::

### Behavioral

| Variable | Description | Default |
|----------|-------------|---------|
| `WRITE_TO_PAPERLESS` | Allow OWL to write tags/custom fields back to Paperless | `true` |
| `LOG_FORMAT` | Logging format: `json` (structured) or `text` (human-readable) | `json` |
| `STATEMENT_TRACKER_CONFIG` | Path to statement tracker YAML config | `/app/config/config.docker.yaml` |
| `DOCUMENT_VIEWS_CONFIG` | Optional path to the grouped Document Views allowlist | unset |

### Networking

| Variable | Description | Default |
|----------|-------------|---------|
| `STATEMENT_TRACKER_PORT` | Host port mapping for the hub | `8071` |
| `DOC_HUB_IMAGE_TAG` | Docker image tag | `latest` |

## YAML Configuration

### Document Views Catalog

Set `DOCUMENT_VIEWS_CONFIG` to a deployment-managed YAML file. Start from
`config/document-views.example.yaml`, replace its synthetic Paperless IDs, and
mount the resulting untracked file into the OWL container. Set
`PAPERLESS_BROWSER_URL` to the origin users open in a browser; do not use an
internal Docker service URL such as `http://paperless:8000`.

```yaml
groups:
  - id: daily-review
    label: Daily Review
    default_expanded: true
    views:
      - id: inbox
        label: Inbox
        provider: paperless
        source_id: 17

      - id: needs-review
        label: Needs Review
        provider: owl
        source_id: triage.pending
```

Paperless view names are presentation labels, not identifiers. Paperless-backed
views open Paperless unless `launch: owl` and an internal `owl_route` are
configured for a purpose-built workflow. See
[Document Views and Metadata Quality](../design/active/document-views-and-metadata-quality.md)
for provider semantics, permissions, freshness, and extension rules.

### Statement Tracker Config

Located at the path specified by `STATEMENT_TRACKER_CONFIG`:

```yaml
source:
  paperless_url: http://paperless:8000
  api_token_env: PAPERLESS_API_TOKEN   # reads from this env var
  # Or specify token directly (not recommended):
  # api_token: abc123

runtime:
  database_path: /app/data/statements.db
  log_level: INFO

detection:
  min_documents: 3          # Minimum docs to recognize a provider
  confidence_threshold: 0.7 # Minimum confidence to include in results
  lookback_months: 24       # How far back to scan for patterns

grace_periods:
  monthly: 10      # Days past expected date before alerting
  quarterly: 15
  semi_annual: 20
  annual: 30

provider_hints:
  - correspondent: "State Farm"
    frequency: semi_annual
    expected_months: [1, 7]
    grace_period_days: 14
  - correspondent: "County Tax Office"
    frequency: annual
    expected_month: 11
```

### Analysis Rules Config

Used by the Action Queue's rule-based fallback classifier:

```yaml
# config/analysis_rules.yaml
rules:
  - match:
      correspondent_contains: "Electric"
      tags_include: ["bill"]
    action: PAY
    urgency: 6

  - match:
      document_type: "Invoice"
    action: PAY
    urgency: 7

  - match:
      tags_include: ["insurance", "policy"]
    action: FILE
    urgency: 2
```

## Admin API Endpoints

Runtime configuration can be adjusted without restarting the container.

### Schedules

```bash
# Get all schedules
curl http://service-005.example.invalid/api/admin/schedules

# Update action queue schedule
curl -X PUT http://service-005.example.invalid/api/admin/schedules/action_queue \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "cron": "0 6 * * *"}'

# Update statement discovery schedule
curl -X PUT http://service-005.example.invalid/api/admin/schedules/statement_discovery \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "cron": "0 3 * * 0"}'
```

### EOB Matching Weights

```bash
curl -X PUT http://service-005.example.invalid/api/admin/weights/eob \
  -H "Content-Type: application/json" \
  -d '{
    "date": 0.30,
    "provider": 0.25,
    "patient": 0.20,
    "amount": 0.15,
    "procedures": 0.10
  }'
```

:::warning
Weights must sum to 1.0. The API validates this and returns a 422 error if they don't.
:::

### Hub Settings

```bash
curl http://service-005.example.invalid/api/admin/settings
```

Returns current runtime settings including write mode, connected services, and module status.

## Docker Compose Profiles

The default `docker compose up` starts the hub with all modules enabled. Profiles allow selective deployment:

```yaml
# Full stack (default)
docker compose up -d

# Hub only (no separate workers)
docker compose up -d hub
```

### Volumes

| Volume | Purpose |
|--------|---------|
| `hub-data` | SQLite databases, extraction cache, processing history |

### Networks

| Network | Purpose |
|---------|---------|
| `doc-intelligence` | Internal communication between hub services |

:::tip
For production, the hub runs behind Traefik with the hostname `service-005.example.invalid`. In development, access directly on port 8071.
:::

## Health Check

```bash
curl http://service-005.example.invalid/health
```

Returns overall system health and per-module status. Use this for Docker health checks and monitoring.

```json
{
  "status": "healthy",
  "version": "0.2.0",
  "modules": {
    "action_queue": "ok",
    "statements": "ok",
    "eob_matching": "ok",
    "alerts": "ok"
  }
}
```
