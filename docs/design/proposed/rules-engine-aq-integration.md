---
title: "Rules Engine → Action Queue Integration"
sidebar_label: Rules Engine AQ Trigger
sidebar_position: 10
status: proposed
---

# Design: Rules Engine → Action Queue Integration

## Overview

This document proposes a future enhancement where the Analysis Rules Engine can **trigger** Action Queue pipeline runs on specific documents or document sets. Currently, the two systems are separate:

- **Action Queue**: Batch pipeline that fetches documents (by tags, saved views, or filters) → runs LLM analysis → extracts actionable items (pay bills, respond to letters, etc.)
- **Analysis Rules Engine**: Event-driven system that fires rules on `document_added`, `schedule`, or `manual` triggers, evaluating conditions and routing insights to dashboards/alerts.

## Motivation

The rules engine already detects interesting events (new document added, anomaly detected, etc.). It would be natural to allow a rule to say "when a new document is added with tag X, immediately analyze it through the Action Queue pipeline" rather than waiting for the next scheduled AQ run.

## Proposed Design

### New Rule Analyzer Type: `builtin:action_queue_trigger`

Add a new built-in rule that, when triggered, invokes the Action Queue pipeline for the triggering document:

```yaml
rules:
  - id: immediate-inbox-analysis
    name: "Immediate Action Queue Analysis"
    description: "Analyze new Inbox documents immediately rather than waiting for scheduled run"
    tier: basic
    trigger:
      type: document_added
      filter:
        tags: ["Inbox"]
    analyzer: "builtin:action_queue_trigger"
    params:
      force: false        # Don't re-analyze if already processed
      dry_run: false      # Actually create actions
    routing:
      default: informational
    display:
      card_type: summary
```

### Implementation Approach

1. **New rule class** `ActionQueueTriggerRule` (registered as `action-queue-trigger`):
   - `execute(context)` calls `run_pipeline(document_id=context.document_id, force=params.force, dry_run=params.dry_run)`
   - Returns a `RuleExecutionResult` with the pipeline summary

2. **Context builder extension**: Ensure the `current_document` context includes the Paperless document ID for pass-through.

3. **Rate limiting**: Since rules fire per-document, add a debounce/batch mechanism so bulk imports don't trigger hundreds of individual pipeline runs. Options:
   - Queue document IDs and batch-run every N seconds
   - Skip if a pipeline run is already in progress
   - Configurable `batch_delay_seconds` param

### Scope & Boundaries

- The rules engine triggers the AQ pipeline but does **not** replace it. Scheduled batch runs remain the primary mechanism.
- The rule merely provides a "fast path" for high-priority documents.
- The AQ pipeline's deduplication (`ProcessingHistory`) prevents double-processing even if both the rule and the scheduled run pick up the same document.

## Alternatives Considered

1. **Paperless consumption hooks → direct AQ call**: Skip the rules engine entirely and use Paperless post-consumption webhooks. Rejected because: less flexible, no UI for configuration, harder to add conditions.

2. **Move AQ source config into rules engine**: Make the "what to scan" question a rule concern. Rejected because: the AQ pipeline is fundamentally batch-oriented and its source config is simpler than a full rule. Overloading the rules engine adds complexity without benefit.

## Status

**Proposed** — not yet scheduled for implementation. This document serves as the reference for future work.
