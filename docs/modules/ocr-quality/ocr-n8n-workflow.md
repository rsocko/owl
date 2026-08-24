---
title: "OCR Quality Orchestration"
sidebar_label: OCR Orchestration
sidebar_position: 6
---

# OCR Quality Orchestration

**Status:** OWL-owned workflow contract
**Revised:** 2026-08-24

## Decision

OWL owns OCR run state, idempotency, candidate lifecycle, comparisons, and
decisions. n8n is optional notification/integration glue and is not the OCR
state machine.

Scheduled, event-driven, and manual entry points call the same OWL services and
produce the same observable run records.

## Initial entry points

| Entry point | Initial behavior |
|---|---|
| Full-corpus inventory | Explicit operator action; non-mutating |
| Document assessment | User-triggered from OWL |
| Candidate generation | User-triggered from OWL |
| Small batch | Explicitly selected documents with hard caps |
| New-document event | Assessment only, if enabled |
| Schedule | Assessment of unscored/stale documents only, if enabled |

No initial entry point automatically generates or accepts OCR candidates.

## Run contract

Every asynchronous operation has:

- run ID and type;
- requested scope and actor;
- scorer/provider/configuration versions;
- queued, running, completed, failed, and cancelled states;
- item and page progress;
- safe aggregate outcomes;
- bounded retries;
- cancellation; and
- timestamps and correlation IDs.

Document operations are idempotent by document/version checksum and operation
configuration. Concurrent requests for the same effective work coalesce or
return a conflict rather than creating duplicate candidates.

## Manual and batch workflow

```mermaid
flowchart LR
    A[OWL review queue] --> B[Select document or capped batch]
    B --> C[Create run]
    C --> D[Assess or generate candidates]
    D --> E[Persist per-document outcomes]
    E --> F[OWL comparison review]
    F -->|Explicit acceptance| G[Paperless version application]
```

Batch completion produces candidates and comparisons only. It does not provide
an `accept all` path.

## Event-driven assessment

A Paperless event may request assessment after document processing completes.
Repeated delivery is safe. The event identifies the Paperless document/version;
OWL re-reads authoritative state and does not trust OCR content embedded in the
event.

Events that arrive while Paperless is still processing are retried with bounded
backoff and an explicit terminal failure.

An accepted candidate or rollback also creates a durable, privacy-safe
invalidation for affected downstream analysis. Each module reprocesses
idempotently against the accepted version/checksum and exposes stale, running,
complete, or failed state. This re-analysis is tracked by issue
[#114](https://github.com/rsocko/owl/issues/114).

## Scheduled assessment

Scheduling is optional and uses OWL's existing scheduler unless deployment
requirements justify another orchestrator. It assesses documents that are:

- unscored for the current version;
- scored by an obsolete scorer version; or
- changed since the last assessment.

It does not periodically re-OCR the corpus.

## n8n integration

n8n may:

- invoke an authenticated OWL inventory or assessment endpoint;
- route aggregate completion/failure notifications;
- forward reviewed action-required outcomes; or
- coordinate with other homelab systems.

n8n must not:

- independently decide which documents need remediation;
- write OCR scores or statuses without OWL confirmation;
- accept candidates;
- maintain a second queue/state machine; or
- include raw OCR text or document metadata in notifications.

## Observability

OWL exposes:

- run progress and duration;
- aggregate score/status distributions;
- provider latency, failure, and retry counts;
- candidate and review throughput;
- Azure page usage and configured cost estimates;
- stale comparison and Paperless application failures; and
- rollback availability.

Alerts are emitted for failed runs, reviewed action-required outcomes, version
application failures, and rollback failures. A low score by itself is a review
signal, not an urgent alert.

## API shape

```text
POST /api/ocr/inventory
POST /api/ocr/assessments/{document_id}
POST /api/ocr/candidates
POST /api/ocr/batches
GET  /api/ocr/runs/{run_id}
POST /api/ocr/runs/{run_id}/cancel
POST /api/ocr/comparisons/{comparison_id}/accept
POST /api/ocr/comparisons/{comparison_id}/reject
POST /api/ocr/documents/{document_id}/rollback
```

Authorization distinguishes inventory, candidate generation, acceptance, and
rollback permissions.
