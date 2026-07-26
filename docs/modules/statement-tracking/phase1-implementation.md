---
title: "Phase 1 Implementation"
sidebar_label: Phase 1
sidebar_position: 4
---

# Phase 1 Implementation Plan

## Goal

Phase 1 should validate three claims from the design before adding UI or automated retrieval:

1. Recurring statement-like documents can be discovered reliably from metadata.
2. A practical recurrence model can be inferred from document dates.
3. Missing or overdue statement windows can be computed from that model.

## Review Findings

The current design docs are strong on product intent and algorithm direction, but they need a narrower execution slice for implementation.

### What is solid

- The privacy and self-hosting constraints are clear.
- The MVP should be rule-based and Python-based.
- Paperless integration is read-only for the first useful version.
- The main algorithms are directionally correct for monthly and quarterly schedules.

### What is under-specified for implementation

- There is no explicit Phase 1 contract defining which fields are mandatory from Paperless.
- The docs jump from architecture to full product scope, without a fixture-first validation layer.
- The initial storage choice is inconsistent with the fastest test loop; a JSON snapshot is enough before introducing SQLite.
- There are no acceptance fixtures or test cases that prove the algorithms on representative data.
- Deployment guidance assumes Docker build availability on the target, but the host only supports image-based deploys.

## Recommended Phase 1 Scope

Keep Phase 1 deliberately thin:

- Read documents from either a fixture file or Paperless API.
- Detect candidate providers from recurring document groups.
- Infer release frequency and a basic monthly pattern.
- Compute missing and overdue recommendations relative to an as-of date.
- Expose the results through a CLI and a small API.
- Persist only a JSON snapshot for inspection.

Do not include these yet:

- User-managed catalog editing UI
- Database migrations
- Notification delivery
- Automated downloads
- ML classification

## Stepped Testing Approach

### Step 1: Fixture-driven unit tests

Use synthetic Paperless-like documents to validate:

- monthly detection
- title normalization
- confidence scoring
- gap detection
- overdue prioritization

Acceptance criteria:

- At least one monthly provider is discovered from the fixture.
- Non-statement noise is ignored.
- Expected missing windows are stable for a fixed as-of date.

### Step 2: Local CLI validation

Install the package into your local virtual environment first:

```bash
python -m pip install -e .[dev]
```

Run the engine against the fixture config:

```bash
statement-tracker discover --config config/config.fixture.yaml
statement-tracker debug-discovery --config config/config.fixture.yaml --limit 20
statement-tracker check-missing --config config/config.fixture.yaml --as-of 2026-05-12
```

Acceptance criteria:

- Output is deterministic.
- Snapshot file is written to `data/catalog.snapshot.json`.
- Results are inspectable without any service dependencies.
- Rejected candidate groups can be inspected without changing code.

### Step 3: Local API validation

Run the lightweight API locally:

```bash
statement-tracker serve --config config/config.fixture.yaml
```

Validate:

- `GET /health`
- `POST /api/discovery/run`
- `POST /api/recommendations/run?as_of=2026-05-12`

### Step 4: Read-only Paperless smoke test

Switch to `config.paperless.yaml` derived from the example file.

Validate the connection first:

```bash
statement-tracker test-connection --config config/config.paperless.yaml
```

Acceptance criteria:

- Connection succeeds.
- Documents and correspondents can be fetched.
- Discovery completes without writing back to Paperless.

### Step 5: Docker packaging

Build the image locally on the workstation that can run Docker:

```bash
docker build -t service-007.example.invalid/statement-tracker:phase1 .
docker push service-007.example.invalid/statement-tracker:phase1
```

Then deploy using the image-only compose file:

```bash
docker compose -f deploy/docker-compose.image.yaml up -d
```

Before the first Paperless-backed run, copy `config/config.paperless.example.yaml` to `config/config.paperless.yaml` and set `PAPERLESS_API_TOKEN` in the deployment environment.

## Artifact Map

- `src/statement_tracker/`: rule engine, API, CLI, config loading
- `tests/fixtures/`: deterministic Paperless-like input data
- `tests/`: unit and API validation
- `config/config.fixture.yaml`: local no-dependency config
- `config/config.paperless.example.yaml`: Paperless-backed config template
- `deploy/docker-compose.image.yaml`: image-only deployment for your host model

## Recommended Next Phase

After this prototype is stable, Phase 1.1 should replace the snapshot-only runtime store with SQLite while preserving the same public CLI and API contract.
