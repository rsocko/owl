---
title: OWL Architecture
sidebar_label: Architecture
sidebar_position: 1
---

# Architecture Overview

OWL is a **full-stack document intelligence application** with its own built-in web UI and a rich API layer. It serves two audiences:

- **Direct users** interact with the OWL dashboard (a React/Vite SPA served from the same container) for triage, action queues, statement tracking, and EOB matching.
- **Mission Control** consumes OWL's dedicated connector endpoints to surface alerts and actions within the broader homelab dashboard.

Paperless-ngx remains the document source-of-truth; OWL reads from it but never modifies documents directly.

```mermaid
graph LR
    P[Paperless-ngx] -->|documents & metadata| OWL[OWL]
    LLM[LLM Gateway] -->|analysis| OWL
    OWL -->|built-in UI| User
    OWL -->|connector API| MC[Mission Control]
    User -->|interacts| OWL
    User -->|interacts| MC
```

## Module Architecture

OWL is organized into independent, single-responsibility modules. Each module owns its own database, models, and router — there is no shared mutable state between modules.

| Module | Responsibility |
|--------|---------------|
| **Action Queue** | Inbox-zero workflow — classify documents, recommend next actions |
| **Statement Tracking** | Detect missing recurring statements (bills, pay stubs) |
| **EOB Matching** | Match medical Explanation of Benefits to provider bills |
| **Needs Review** | Human review of uncertain matches, possible duplicates, grouping issues, and unmatched documents |
| **Alerts** | Unified alerting engine consumed by all modules |

### Module Organization

```
src/doc_intelligence_hub/
├── api/
│   ├── app.py              # FastAPI app factory
│   └── routers/            # One router per module
│       ├── action_queue.py
│       ├── statements.py
│       ├── eob.py
│       ├── alerts.py
│       └── admin.py
├── modules/                # Business logic per module
│   ├── action_queue/
│   ├── statements/
│   ├── eob_matching/
│   ├── analysis/
│   └── triage/
└── core/                   # Shared utilities & alerts engine
```

:::tip Cross-references
For usage instructions, see the [User Guide](../guide/). For deep-dive design docs per module, see the [Module Reference](../modules/).
:::

## Data Flow

Documents flow through OWL in a single direction — from ingestion to actionable alerts, surfaced in both the built-in UI and Mission Control:

```mermaid
flowchart TD
    subgraph Paperless-ngx
        docs[Documents & Tags]
    end

    subgraph OWL
        client[Paperless Client]
        aq[Action Queue]
        st[Statement Tracking]
        eob[EOB Matching]
        triage[Needs Review]
        alerts[Alerts Engine]
        ui[Built-in Dashboard]
    end

    subgraph External
        mc[Mission Control]
    end

    docs --> client
    client --> aq
    client --> st
    client --> eob
    client --> triage
    aq --> alerts
    st --> alerts
    eob --> alerts
    triage --> alerts
    alerts --> ui
    alerts --> mc
```

Each module independently queries Paperless via the shared client, processes documents through its own logic (potentially calling the LLM), and emits alerts. The built-in dashboard displays these directly; Mission Control polls the connector endpoints for integration into its own UI.

## Action Queue vs. Needs Review

The two workflows represent different kinds of human work:

| Workflow | Question it answers | Typical items | User outcome |
|----------|---------------------|---------------|--------------|
| **Action Queue** | "What do I need to do because of this document?" | Pay a bill, sign a form, respond to a letter, schedule an appointment | Done, remind me later, won't do, or no action needed |
| **Needs Review** | "Where does OWL need my judgment before it can proceed?" | Uncertain EOB matches, possible duplicates, grouping issues, unmatched documents | Accept, reject, correct, match, or review later |

An item in Needs Review is not necessarily a task and may represent a relationship between several documents rather than one document. Conversely, reviewing a document can itself be a real-world task, so generic labels such as "Documents to Review" would overlap with the Action Queue.

The workflows are currently implemented as independent modules. The target routing model below makes their intended relationship explicit: confident analysis bypasses Needs Review, while uncertain analysis goes there first and may produce an Action Queue item after a person resolves the uncertainty.

```mermaid
flowchart TD
    D[Document received] --> Analyze[OWL analyzes document]

    Analyze -->|Confident and no work required| File[File automatically]
    Analyze -->|Confident and work required| Action[Action Queue]
    Analyze -->|Uncertain interpretation| Review[Needs Review]

    Review -->|User corrects or confirms| Reprocess[Re-evaluate document]
    Review -->|No task required| File
    Reprocess -->|Work required| Action
    Reprocess -->|No work required| File

    Action -->|Done| Finished[Finished]
    Action -->|Remind later| Action
    Action -->|Won't do| Finished
```

This establishes the product rule:

- **Needs Review determines what OWL should believe.**
- **Action Queue tracks what the user should do.**
- Most confidently understood documents never enter Needs Review.
- Resolving an item in Needs Review does not always create an action.

The existing `/triage` route, API namespace, database tables, and internal code identifiers retain the `triage` name for compatibility. **Needs Review** is the user-facing product name.

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12 |
| Web Framework | FastAPI (async) |
| ORM / Database | SQLAlchemy + SQLite (one DB per module) |
| LLM | OpenAI-compatible chat completions via Bifrost gateway |
| Containerization | Docker (single image) |
| Reverse Proxy | Traefik (TLS termination, routing) |
| Configuration | YAML config + environment variables |

## Deployment Topology

```mermaid
graph TD
    subgraph Homelab Network
        traefik[Traefik Reverse Proxy]
        subgraph Docker Host
            owl[OWL Container<br/>port 8001]
            paperless[Paperless-ngx]
        end
        bifrost[Bifrost LLM Gateway]
    end

    internet[Internet / Browser] -->|HTTPS| traefik
    traefik -->|:8071 → :8001| owl
    owl -->|HTTP| paperless
    owl -->|OpenAI API| bifrost
```

| Detail | Value |
|--------|-------|
| Image | `service-007.example.invalid/doc-intelligence-hub` |
| Internal port | `8001` |
| External port | `8071` (via Traefik) |
| Data volume | `/app/data` (SQLite databases) |

## API Layer

The FastAPI application is constructed via an app factory in `api/app.py`. On startup it:

1. Loads `hub_settings` from YAML + env vars and attaches it to `app.state`
2. Initializes a shared **Paperless client** (token-based HTTP client)
3. Registers module routers under versioned prefixes
4. Serves the **built-in React/Vite dashboard** at `/` (HashRouter SPA)
5. Mounts the legacy **Statement Tracker dashboard** at `/statements/`

### Frontend

OWL ships a React/Vite single-page application as its primary UI. The compiled assets are served directly from the FastAPI container — no separate frontend deployment is needed. The SPA uses HashRouter, so all client-side routes are handled without server-side catch-all configuration.

### Mission Control Connector

A dedicated `mc_connector` router exposes the endpoints that Mission Control's Document Intelligence connector expects:

- `/api/action-queue/actions` — paginated, optionally incremental action reconciliation
- `/api/action-queue/actions/{id}` — Paperless-aware completion, dismissal, and reopen
- `/api/action-queue/actions/{id}/snooze` — source-side snooze
- `/api/action-queue/actions/{id}/feedback` — classifier feedback and corrections
- `/api/statements/missing` — missing statement alerts
- `/api/eob/unmatched` — unmatched EOB records

The Action Queue and connector routers share lifecycle and feedback helpers so
timestamps, Paperless status write-back, sync tracking, and configured intake-tag
removal stay consistent. See the
[Mission Control integration contract](../guide/mission-control-integration.md).

### Module Routers

All routers share the Paperless client instance but maintain their own database connections and session factories.

:::info Router structure
Each router is self-contained — it defines its own Pydantic models, dependencies, and SQLAlchemy sessions. This keeps modules independently deployable in the future. The built-in UI consumes the same API endpoints that external clients use.
:::

## Storage

Each module maintains its own SQLite database under `/app/data`:

```
/app/data/
├── action_queue.db
├── statements.db
├── eob_matching.db
└── alerts.db
```

:::warning No shared database state
Modules do not read from each other's databases. Cross-module communication happens exclusively through the Alerts engine API. This isolation simplifies testing and prevents cascading failures.
:::

- SQLAlchemy handles migrations and schema management per module
- SQLite is chosen for simplicity — the workload is single-user, low-write
- The `/app/data` directory is a Docker volume for persistence across container restarts

## LLM Integration

OWL uses an OpenAI-compatible chat completions API for document analysis, classification, and extraction tasks.

### Bifrost Gateway Pattern

Rather than calling LLM providers directly, OWL routes all requests through **Bifrost** — a local gateway that handles:

- Model routing and selection
- Rate limiting and retry logic
- Provider failover (local → cloud)
- Usage tracking

### Configuration

| Env Var | Purpose |
|---------|---------|
| `LLM_BASE_URL` | Bifrost endpoint (e.g., `http://bifrost:8000/v1`) |
| `LLM_API_KEY` | API key for the gateway |
| `LLM_MODEL` | Default model identifier |

Modules can override the model per-request when a task requires specific capabilities (e.g., larger context window for full-document analysis).

## Security Model

:::info Internal-only service
OWL runs on an isolated homelab network behind Traefik. It is **not exposed to the public internet**.
:::

- **No authentication layer** — the service trusts all callers on the internal network
- **Paperless access** — token-based authentication to Paperless-ngx API
- **LLM access** — API key for Bifrost gateway
- **Network isolation** — Docker network restricts inter-container communication to declared links
- **TLS** — Traefik terminates HTTPS at the edge; internal traffic is plaintext HTTP

This model is appropriate for a single-user homelab. If OWL were ever exposed publicly, an auth layer (e.g., OAuth2 or API keys) would need to be added at the FastAPI level.
