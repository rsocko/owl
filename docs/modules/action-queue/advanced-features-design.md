---
title: "Advanced Features Design"
sidebar_label: Advanced Features
sidebar_position: 6
---

# Advanced Features Design: Action Queue Module

> **Version:** 1.0  
> **Date:** July 2026  
> **Status:** Implementation-Ready  
> **Module:** `doc_intelligence_hub.modules.action_queue`

---

## Table of Contents

1. [Scheduling & Orchestration](#1-scheduling--orchestration)
   - [n8n Workflow Template](#11-n8n-workflow-template)
   - [Cron/Systemd Fallback](#12-cronsystemd-fallback)
   - [Retry Logic & Exponential Backoff](#13-retry-logic--exponential-backoff)
   - [Dead-Letter Queue](#14-dead-letter-queue)
   - [Monitoring & Alerting](#15-monitoring--alerting)
   - [Webhook Authentication](#16-webhook-authentication)
2. [Intent Detection](#2-intent-detection)
   - [Decision Tree](#21-decision-tree)
   - [Confidence Scoring](#22-confidence-scoring)
   - [Rule-Based MVP Algorithm](#23-rule-based-mvp-algorithm)
   - [ML Upgrade Path](#24-ml-upgrade-path)
   - [Edge Cases](#25-edge-cases)
3. [Risk & Urgency Scoring](#3-risk--urgency-scoring)
   - [Priority Score Formula](#31-priority-score-formula)
   - [Risk Score Calculation](#32-risk-score-calculation)
   - [Due Date Extraction & Weighting](#33-due-date-extraction--weighting)
   - [Late Fee / Penalty Detection](#34-late-fee--penalty-detection)
   - [Urgency Levels](#35-urgency-levels)
   - [Recency Decay](#36-recency-decay)
4. [Feedback Loop](#4-feedback-loop)
   - [Corrections as Training Data](#41-corrections-as-training-data)
   - [Confidence Threshold Adjustment](#42-confidence-threshold-adjustment)
   - [Retraining Triggers](#43-retraining-triggers)
   - [Model Versioning](#44-model-versioning)
   - [A/B Testing Framework](#45-ab-testing-framework)
   - [Active Learning](#46-active-learning)
5. [Deduplication Refinement](#5-deduplication-refinement)
   - [Hash Algorithm Spec](#51-hash-algorithm-spec)
   - [Re-processing Triggers](#52-re-processing-triggers)
   - [Analysis Version Format](#53-analysis-version-format)
   - [Cache Invalidation Strategy](#54-cache-invalidation-strategy)
   - [Document Update Handling](#55-document-update-handling)
6. [LLM/AI Model Strategy](#6-llmai-model-strategy)
   - [Local Models (Ollama)](#61-local-models-ollama)
   - [Azure AI Options](#62-azure-ai-options)
   - [Hybrid Approach](#63-hybrid-approach)
   - [Cost Estimates](#64-cost-estimates)
7. [Configuration Schema](#7-configuration-schema)
8. [Database Migrations](#8-database-migrations)
9. [API Endpoints](#9-api-endpoints)
10. [Phased Implementation Plan](#10-phased-implementation-plan)

---

## 1. Scheduling & Orchestration

### 1.1 n8n Workflow Template

The primary scheduling mechanism. n8n runs on the homelab alongside DI and provides visual workflow management, retry handling, and error notifications.

**Workflow: `action-queue-daily-scan.json`**

```json
{
  "name": "Action Queue - Daily Document Scan",
  "nodes": [
    {
      "id": "trigger-cron",
      "name": "Daily Trigger",
      "type": "n8n-nodes-base.scheduleTrigger",
      "position": [250, 300],
      "parameters": {
        "rule": {
          "interval": [{ "field": "cronExpression", "expression": "0 6 * * *" }]
        }
      }
    },
    {
      "id": "webhook-manual",
      "name": "Manual Trigger",
      "type": "n8n-nodes-base.webhook",
      "position": [250, 500],
      "parameters": {
        "path": "action-queue-trigger",
        "httpMethod": "POST",
        "authentication": "headerAuth",
        "headerAuth": {
          "name": "X-Webhook-Secret",
          "value": "={{ $env.AQ_WEBHOOK_SECRET }}"
        }
      }
    },
    {
      "id": "health-check",
      "name": "DI Health Check",
      "type": "n8n-nodes-base.httpRequest",
      "position": [500, 400],
      "parameters": {
        "url": "={{ $env.DI_API_URL }}/health",
        "method": "GET",
        "timeout": 10000,
        "options": {
          "allowUnauthorizedCerts": false
        }
      }
    },
    {
      "id": "check-healthy",
      "name": "Is Healthy?",
      "type": "n8n-nodes-base.if",
      "position": [700, 400],
      "parameters": {
        "conditions": {
          "boolean": [
            {
              "value1": "={{ $json.status }}",
              "operation": "equal",
              "value2": "ok"
            }
          ]
        }
      }
    },
    {
      "id": "run-pipeline",
      "name": "Run Action Queue Pipeline",
      "type": "n8n-nodes-base.httpRequest",
      "position": [950, 300],
      "parameters": {
        "url": "={{ $env.DI_API_URL }}/api/action-queue/run",
        "method": "POST",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendHeaders": true,
        "headerParameters": {
          "parameters": [
            { "name": "Authorization", "value": "Bearer {{ $env.DI_API_TOKEN }}" }
          ]
        },
        "sendBody": true,
        "bodyParameters": {
          "parameters": [
            { "name": "force", "value": "false" },
            { "name": "limit", "value": "50" }
          ]
        },
        "timeout": 300000,
        "options": {
          "response": { "response": { "responseFormat": "json" } }
        }
      }
    },
    {
      "id": "check-results",
      "name": "Process Results",
      "type": "n8n-nodes-base.if",
      "position": [1200, 300],
      "parameters": {
        "conditions": {
          "number": [
            {
              "value1": "={{ $json.failed }}",
              "operation": "larger",
              "value2": 0
            }
          ]
        }
      }
    },
    {
      "id": "notify-success",
      "name": "Log Success",
      "type": "n8n-nodes-base.set",
      "position": [1450, 200],
      "parameters": {
        "values": {
          "string": [
            {
              "name": "message",
              "value": "Action Queue: Processed {{ $json.processed }}, skipped {{ $json.skipped }}, no-action {{ $json.no_action }}"
            }
          ]
        }
      }
    },
    {
      "id": "notify-failures",
      "name": "Alert: Failures Detected",
      "type": "n8n-nodes-base.httpRequest",
      "position": [1450, 400],
      "parameters": {
        "url": "={{ $env.NTFY_URL }}/action-queue",
        "method": "POST",
        "sendHeaders": true,
        "headerParameters": {
          "parameters": [
            { "name": "Title", "value": "Action Queue: {{ $json.failed }} failures" },
            { "name": "Priority", "value": "high" },
            { "name": "Tags", "value": "warning" }
          ]
        },
        "sendBody": true,
        "body": "Pipeline run had {{ $json.failed }} failures. Processed: {{ $json.processed }}. Check DI admin for details."
      }
    },
    {
      "id": "notify-unhealthy",
      "name": "Alert: DI Unavailable",
      "type": "n8n-nodes-base.httpRequest",
      "position": [950, 550],
      "parameters": {
        "url": "={{ $env.NTFY_URL }}/action-queue",
        "method": "POST",
        "sendHeaders": true,
        "headerParameters": {
          "parameters": [
            { "name": "Title", "value": "Action Queue: DI API unreachable" },
            { "name": "Priority", "value": "urgent" },
            { "name": "Tags", "value": "rotating_light" }
          ]
        },
        "body": "Document Intelligence API health check failed. Pipeline skipped."
      }
    },
    {
      "id": "dead-letter",
      "name": "Record to Dead-Letter",
      "type": "n8n-nodes-base.httpRequest",
      "position": [1450, 550],
      "parameters": {
        "url": "={{ $env.DI_API_URL }}/api/action-queue/dead-letter",
        "method": "POST",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendHeaders": true,
        "headerParameters": {
          "parameters": [
            { "name": "Authorization", "value": "Bearer {{ $env.DI_API_TOKEN }}" }
          ]
        },
        "sendBody": true,
        "bodyParameters": {
          "parameters": [
            { "name": "run_id", "value": "={{ $execution.id }}" },
            { "name": "error", "value": "={{ $json.error_message || 'Unknown error' }}" },
            { "name": "timestamp", "value": "={{ new Date().toISOString() }}" }
          ]
        }
      }
    }
  ],
  "connections": {
    "trigger-cron": { "main": [[{ "node": "health-check" }]] },
    "webhook-manual": { "main": [[{ "node": "health-check" }]] },
    "health-check": { "main": [[{ "node": "check-healthy" }]] },
    "check-healthy": {
      "main": [
        [{ "node": "run-pipeline" }],
        [{ "node": "notify-unhealthy" }]
      ]
    },
    "run-pipeline": { "main": [[{ "node": "check-results" }]] },
    "check-results": {
      "main": [
        [{ "node": "notify-failures" }, { "node": "dead-letter" }],
        [{ "node": "notify-success" }]
      ]
    }
  },
  "settings": {
    "executionOrder": "v1",
    "saveManualExecutions": true,
    "callerPolicy": "workflowsFromSameOwner",
    "errorWorkflow": "action-queue-error-handler"
  }
}
```

**Environment variables required in n8n:**
| Variable | Example | Purpose |
|----------|---------|---------|
| `DI_API_URL` | `http://di-hub:8000` | DI API base URL |
| `DI_API_TOKEN` | `di_aq_...` | Bearer token for DI API |
| `AQ_WEBHOOK_SECRET` | `whsec_...` | Webhook auth header value |
| `NTFY_URL` | `http://ntfy:80` | ntfy notification server |

### 1.2 Cron/Systemd Fallback

For environments without n8n or when n8n is down:

**`/etc/systemd/system/action-queue.timer`**
```ini
[Unit]
Description=Action Queue daily scan timer

[Timer]
OnCalendar=*-*-* 06:00:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

**`/etc/systemd/system/action-queue.service`**
```ini
[Unit]
Description=Action Queue pipeline run
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
User=di
WorkingDirectory=/opt/document-intelligence
ExecStart=/opt/document-intelligence/.venv/bin/python -m doc_intelligence_hub.modules.action_queue.cli run --limit 50
TimeoutStartSec=600
Environment=DI_ENV=production
EnvironmentFile=/opt/document-intelligence/.env

[Install]
WantedBy=multi-user.target
```

### 1.3 Retry Logic & Exponential Backoff

Implemented in the pipeline runner, triggered by both n8n and the CLI:

```python
# In: src/doc_intelligence_hub/modules/action_queue/scheduler.py

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .config import settings


@dataclass
class RetryPolicy:
    """Configurable retry policy for pipeline execution."""
    max_retries: int = 3
    base_delay_seconds: float = 30.0
    max_delay_seconds: float = 600.0  # 10 min cap
    jitter_factor: float = 0.25  # ±25% random jitter
    retry_on_status: set[int] = None  # HTTP codes to retry

    def __post_init__(self):
        if self.retry_on_status is None:
            self.retry_on_status = {429, 500, 502, 503, 504}

    def delay_for_attempt(self, attempt: int) -> float:
        """Calculate delay with exponential backoff + jitter."""
        delay = min(
            self.base_delay_seconds * (2 ** attempt),
            self.max_delay_seconds,
        )
        jitter = delay * self.jitter_factor * (2 * random.random() - 1)
        return max(0, delay + jitter)


@dataclass
class RunAttempt:
    """Record of a single pipeline execution attempt."""
    attempt_number: int
    started_at: datetime
    completed_at: Optional[datetime] = None
    success: bool = False
    error: Optional[str] = None
    documents_processed: int = 0


async def run_with_retry(
    pipeline_fn,
    policy: RetryPolicy = None,
    **pipeline_kwargs,
) -> tuple[dict, list[RunAttempt]]:
    """Execute pipeline with retry logic.

    Args:
        pipeline_fn: Async callable (Pipeline.run)
        policy: Retry policy config
        **pipeline_kwargs: Forwarded to pipeline_fn

    Returns:
        (result_dict, list_of_attempts)
    """
    policy = policy or RetryPolicy()
    attempts: list[RunAttempt] = []

    for attempt_num in range(policy.max_retries + 1):
        attempt = RunAttempt(
            attempt_number=attempt_num,
            started_at=datetime.utcnow(),
        )

        try:
            result = await pipeline_fn(**pipeline_kwargs)
            attempt.success = True
            attempt.documents_processed = result.get("processed", 0)
            attempt.completed_at = datetime.utcnow()
            attempts.append(attempt)
            return result, attempts

        except Exception as e:
            attempt.error = str(e)
            attempt.completed_at = datetime.utcnow()
            attempts.append(attempt)

            if attempt_num < policy.max_retries:
                delay = policy.delay_for_attempt(attempt_num)
                # Log retry intent
                await asyncio.sleep(delay)
            else:
                # Final attempt failed — propagate
                raise

    # Unreachable, but satisfies type checker
    return {"processed": 0, "failed": 0}, attempts
```

### 1.4 Dead-Letter Queue

Failed documents that exhaust retries land here for manual inspection:

```python
# In: src/doc_intelligence_hub/modules/action_queue/dead_letter.py

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, JSON
from .database import Base


class DeadLetterEntry(Base):
    """Documents that failed processing after all retries."""
    __tablename__ = "dead_letter_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, nullable=True, index=True)
    run_id = Column(String, nullable=True)  # n8n execution ID or CLI run ID
    error_type = Column(String, nullable=False)  # timeout, parse_error, api_error, ollama_error
    error_message = Column(Text, nullable=False)
    retry_count = Column(Integer, default=0)
    first_failed_at = Column(DateTime, default=datetime.utcnow)
    last_failed_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    resolution = Column(String, nullable=True)  # manual_retry, skipped, fixed
    context = Column(JSON, nullable=True)  # Document metadata snapshot at failure time
```

**Resolution workflow:**
1. DLQ items appear in DI admin UI
2. Operator can: retry (re-queue), skip (mark as won't-process), or fix (edit and retry)
3. Items older than 30 days with no resolution get auto-archived with a notification

### 1.5 Monitoring & Alerting

**Thresholds (configurable via `SchedulingSettings`):**

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| Pipeline duration | > 5 min | > 15 min | ntfy alert |
| Failure rate (per run) | > 10% | > 50% | ntfy + DLQ |
| Ollama response time | > 30s/doc | > 60s/doc | Log, switch to fallback |
| DLQ depth | > 5 items | > 20 items | ntfy urgent |
| No successful run | > 36 hours | > 72 hours | ntfy urgent |
| Paperless API errors | > 3 per run | > 10 per run | Abort run, alert |

**Health endpoint response (existing `/health` extended):**
```python
@router.get("/health")
async def health():
    return {
        "status": "ok",
        "action_queue": {
            "last_run": "2026-07-23T06:00:12Z",
            "last_run_status": "success",
            "documents_pending": 3,
            "dlq_depth": 0,
            "ollama_available": True,
        }
    }
```

### 1.6 Webhook Authentication

n8n → DI API calls use a shared secret Bearer token:

```python
# In: src/doc_intelligence_hub/modules/action_queue/auth.py

import secrets
import hmac
from fastapi import Header, HTTPException, Depends


async def verify_aq_token(authorization: str = Header(...)):
    """Verify Bearer token for Action Queue API endpoints."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    
    token = authorization[7:]
    expected = settings.api_token
    
    if not expected:
        raise HTTPException(status_code=500, detail="API token not configured")
    
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid token")


def generate_api_token() -> str:
    """Generate a secure API token for initial setup."""
    return f"di_aq_{secrets.token_urlsafe(32)}"
```

For n8n webhook → DI (reverse direction, used for status callbacks):
```python
async def verify_webhook_signature(
    x_webhook_secret: str = Header(..., alias="X-Webhook-Secret"),
):
    """Verify n8n webhook callback authenticity."""
    if not hmac.compare_digest(x_webhook_secret, settings.webhook_secret):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
```

---

## 2. Intent Detection

### 2.1 Decision Tree

The intent detection system maps `(document_type, extracted_entities, text_signals)` → `action_type`. This is the complete decision tree:

```
ROOT
├── Has monetary amount AND due_date?
│   ├── Amount > $0 AND keywords ∈ {invoice, bill, payment due, balance due}
│   │   └── → PAY (confidence: 85-95)
│   ├── Amount > $0 AND keywords ∈ {premium, deductible, copay}
│   │   └── → PAY (confidence: 80-90)
│   └── Amount > $0 AND keywords ∈ {refund, credit, overpayment}
│       └── → FILE (confidence: 70) — money coming TO you
│
├── Has signature_required signals?
│   ├── Keywords ∈ {sign here, signature required, notarize, consent form}
│   │   └── → SIGN (confidence: 85-90)
│   └── Keywords ∈ {power of attorney, affidavit}
│       └── → SIGN (confidence: 90)
│
├── Has response_required signals?
│   ├── Keywords ∈ {jury duty, summons, subpoena}
│   │   └── → RESPOND (confidence: 95) — legal obligation
│   ├── Keywords ∈ {action required, respond by, reply needed}
│   │   └── → RESPOND (confidence: 80-85)
│   └── Keywords ∈ {RSVP, confirm attendance}
│       └── → SCHEDULE (confidence: 75)
│
├── Has scheduling signals?
│   ├── Keywords ∈ {appointment, renewal, expires, expiration}
│   │   └── → SCHEDULE (confidence: 80-85)
│   ├── Keywords ∈ {open enrollment, re-enroll, registration deadline}
│   │   └── → SCHEDULE (confidence: 85)
│   └── Keywords ∈ {annual review, inspection due}
│       └── → SCHEDULE (confidence: 75)
│
├── Has review signals WITHOUT action requirement?
│   ├── Keywords ∈ {policy change, terms update, rate change}
│   │   └── → REVIEW (confidence: 75-80)
│   ├── Keywords ∈ {explanation of benefits, claim summary, EOB}
│   │   └── → REVIEW (confidence: 80)
│   └── Keywords ∈ {contract, agreement, disclosure}
│       └── → REVIEW (confidence: 70-80)
│
├── Has sharing signals?
│   ├── Keywords ∈ {W-2, 1099, K-1, tax form} AND month ∈ {Jan, Feb, Mar, Apr}
│   │   └── → SHARE (confidence: 85) — forward to accountant
│   ├── Keywords ∈ {forward to, provide to your, give to}
│   │   └── → SHARE (confidence: 70)
│   └── Correspondent ∈ known_accountant_list OR known_lawyer_list
│       └── → SHARE (confidence: 60-70)
│
├── Has archival signals?
│   ├── Keywords ∈ {for your records, no action needed, informational only}
│   │   └── → ARCHIVE (confidence: 85)
│   ├── Document age > 90 days AND no due_date AND no amount
│   │   └── → ARCHIVE (confidence: 70)
│   └── Keywords ∈ {confirmation, receipt} AND no future date
│       └── → FILE (confidence: 75)
│
└── DEFAULT (no strong signals)
    ├── Has any content at all?
    │   └── → REVIEW (confidence: 40) — surface for human decision
    └── Empty/unreadable
        └── → skip (no action created)
```

### 2.2 Confidence Scoring

Three-tier system for confidence-based routing:

| Range | Label | Behavior |
|-------|-------|----------|
| 80-100 | **Auto-accept** | Action created, Paperless enriched, appears in MC |
| 50-79 | **Human-review** | Action created but flagged `needs_review=true`, surfaced in MC review queue |
| 0-49 | **Reject** | No action created, logged to `processing_history` with `disposition=low_confidence` |

**Confidence calculation for rule-based:**

```python
def calculate_confidence(
    keyword_hits: int,
    signal_strength: float,  # 0-1, based on keyword specificity
    tag_boost: float,  # 0-30, from Paperless tags
    has_due_date: bool,
    has_amount: bool,
    text_quality_score: int,  # 0-100
) -> int:
    """Calculate composite confidence score.
    
    Base score from keyword matches, boosted by corroborating signals.
    """
    # Base: keyword signal (0-50)
    base = min(50, keyword_hits * signal_strength * 15)
    
    # Corroboration bonus (0-30): multiple independent signals agree
    corroboration = 0
    if has_due_date:
        corroboration += 12
    if has_amount:
        corroboration += 10
    corroboration += tag_boost * 0.3  # Tags contribute up to 9 points
    
    # Text quality factor (0.5-1.0): poor OCR reduces confidence
    quality_factor = max(0.5, text_quality_score / 100)
    
    # Composite
    raw = (base + corroboration) * quality_factor
    return int(min(95, max(5, raw)))  # Cap at 95 for rule-based (never 100% without AI)
```

### 2.3 Rule-Based MVP Algorithm

Extends the existing `RuleBasedAnalyzer` in `fallback_analyzer.py`:

```python
# Enhancement to: src/doc_intelligence_hub/modules/action_queue/intent_detector.py

from dataclasses import dataclass
from typing import Optional
import re


@dataclass
class IntentSignal:
    """A detected signal contributing to intent classification."""
    category: str  # PAY, RESPOND, etc.
    keyword: str  # The matched keyword
    weight: float  # 0-1 specificity
    position: int  # Character offset in text (earlier = more important)


@dataclass
class IntentResult:
    """Result of intent detection."""
    primary_intent: str
    confidence: int
    signals: list[IntentSignal]
    secondary_intents: list[tuple[str, int]]  # [(type, confidence), ...]
    is_multi_intent: bool


class IntentDetector:
    """Rule-based intent detection with weighted signal aggregation."""

    # Signal weights: higher = more specific/reliable indicator
    SIGNAL_WEIGHTS = {
        "PAY": {
            "invoice": 0.9, "bill": 0.85, "payment due": 0.95,
            "balance due": 0.9, "amount due": 0.9, "past due": 0.95,
            "minimum payment": 0.85, "autopay": 0.7,
            "total due": 0.9, "remit": 0.8,
        },
        "RESPOND": {
            "action required": 0.85, "respond by": 0.9,
            "jury duty": 0.99, "summons": 0.95, "subpoena": 0.99,
            "please call": 0.6, "contact us": 0.5,
            "verification needed": 0.8, "confirm": 0.6,
        },
        "SIGN": {
            "sign here": 0.95, "signature required": 0.95,
            "notarize": 0.9, "consent form": 0.85,
            "power of attorney": 0.95, "affidavit": 0.9,
        },
        "SCHEDULE": {
            "appointment": 0.85, "renewal": 0.75, "expires": 0.8,
            "expiration": 0.8, "open enrollment": 0.9,
            "registration deadline": 0.85, "inspection due": 0.8,
        },
        "REVIEW": {
            "policy change": 0.7, "terms update": 0.7,
            "explanation of benefits": 0.8, "eob": 0.75,
            "notice": 0.5, "disclosure": 0.65,
        },
        "SHARE": {
            "w-2": 0.9, "1099": 0.9, "k-1": 0.9, "tax form": 0.85,
            "forward to": 0.6, "provide to your": 0.6,
        },
        "FILE": {
            "receipt": 0.7, "confirmation": 0.65,
            "statement of": 0.6, "proof of": 0.6,
            "for your records": 0.8,
        },
        "ARCHIVE": {
            "no action needed": 0.9, "informational only": 0.85,
            "fyi": 0.7, "keep for": 0.6,
        },
    }

    # Mutually exclusive signal pairs (if both present, pick higher confidence)
    EXCLUSIONS = [
        ("PAY", "FILE"),      # Statement vs. bill
        ("RESPOND", "REVIEW"),  # Action-required notice vs. FYI notice
        ("FILE", "ARCHIVE"),  # Keep vs. archive
    ]

    def detect(
        self,
        text: str,
        tags: list[str],
        correspondent: str,
        has_due_date: bool,
        has_amount: bool,
        document_age_days: int = 0,
        text_quality_score: int = 70,
    ) -> IntentResult:
        """Detect intent from document signals."""
        text_lower = text.lower()
        signals: list[IntentSignal] = []

        # Scan for all keyword matches
        for category, keywords in self.SIGNAL_WEIGHTS.items():
            for keyword, weight in keywords.items():
                pattern = re.compile(r'\b' + re.escape(keyword) + r'\b', re.IGNORECASE)
                match = pattern.search(text)
                if match:
                    signals.append(IntentSignal(
                        category=category,
                        keyword=keyword,
                        weight=weight,
                        position=match.start(),
                    ))

        # Aggregate scores per category
        category_scores: dict[str, float] = {}
        for signal in signals:
            # Position decay: signals in first 500 chars get 1.2x boost
            position_boost = 1.2 if signal.position < 500 else 1.0
            score = signal.weight * position_boost
            category_scores[signal.category] = category_scores.get(signal.category, 0) + score

        # Apply tag boosts
        tag_str = " ".join(tags).lower()
        if "bill" in tag_str or "invoice" in tag_str:
            category_scores["PAY"] = category_scores.get("PAY", 0) + 0.8
        if "tax" in tag_str:
            category_scores["SHARE"] = category_scores.get("SHARE", 0) + 0.6
            category_scores["FILE"] = category_scores.get("FILE", 0) + 0.4
        if "medical" in tag_str or "health" in tag_str:
            category_scores["REVIEW"] = category_scores.get("REVIEW", 0) + 0.5
        if "legal" in tag_str:
            category_scores["SIGN"] = category_scores.get("SIGN", 0) + 0.4
            category_scores["RESPOND"] = category_scores.get("RESPOND", 0) + 0.3

        # Corroboration: amount + due_date strongly suggests PAY
        if has_amount and has_due_date:
            category_scores["PAY"] = category_scores.get("PAY", 0) + 1.0

        # Age decay for ARCHIVE hint
        if document_age_days > 90 and not has_due_date:
            category_scores["ARCHIVE"] = category_scores.get("ARCHIVE", 0) + 0.5

        # Sort and determine primary
        if not category_scores:
            return IntentResult(
                primary_intent="REVIEW",
                confidence=35,
                signals=[],
                secondary_intents=[],
                is_multi_intent=False,
            )

        sorted_intents = sorted(category_scores.items(), key=lambda x: x[1], reverse=True)
        primary_type, primary_score = sorted_intents[0]

        # Check for multi-intent (two strong independent signals)
        is_multi_intent = (
            len(sorted_intents) >= 2
            and sorted_intents[1][1] > 1.5
            and sorted_intents[1][1] / primary_score > 0.6
            and (sorted_intents[0][0], sorted_intents[1][0]) not in self.EXCLUSIONS
            and (sorted_intents[1][0], sorted_intents[0][0]) not in self.EXCLUSIONS
        )

        # Convert raw score to confidence
        confidence = self._score_to_confidence(
            primary_score, has_due_date, has_amount, text_quality_score
        )

        secondary = [
            (t, self._score_to_confidence(s, has_due_date, has_amount, text_quality_score))
            for t, s in sorted_intents[1:3]
            if s > 0.5
        ]

        return IntentResult(
            primary_intent=primary_type,
            confidence=confidence,
            signals=signals,
            secondary_intents=secondary,
            is_multi_intent=is_multi_intent,
        )

    def _score_to_confidence(
        self, raw_score: float, has_due_date: bool, has_amount: bool, text_quality: int
    ) -> int:
        """Convert raw signal score to 0-100 confidence."""
        # Normalize: typical strong match is 2-4 score, very strong is 5+
        base = min(60, raw_score * 20)

        # Corroboration
        bonus = 0
        if has_due_date:
            bonus += 12
        if has_amount:
            bonus += 10

        # Quality factor
        quality_factor = max(0.5, text_quality / 100)

        return int(min(95, max(10, (base + bonus) * quality_factor)))
```

### 2.4 ML Upgrade Path

**Phase B: Local Ollama-powered intent detection**

The existing `OllamaAnalyzer` already handles intent via the prompt. Enhancement is to use structured output mode:

```python
# Future: structured intent classification via Ollama with llama3
INTENT_CLASSIFICATION_PROMPT = """Classify this document into exactly one primary action category.

Categories:
- PAY: Document requires payment (bills, invoices)
- RESPOND: Document requires a reply or action response
- FILE: Document should be filed/kept for records
- REVIEW: Document needs careful reading before deciding
- SHARE: Document should be forwarded to someone (accountant, lawyer)
- SCHEDULE: Document relates to an upcoming date/deadline to calendar
- SIGN: Document requires signature
- ARCHIVE: Document is fully processed, ready for long-term storage

Document text (first 2000 chars):
{text}

Respond with JSON only:
{{"intent": "PAY", "confidence": 87, "reasoning": "..."}}
"""
```

**Phase C: Azure AI escalation for ambiguous cases**

When local confidence is 50-79 (human-review zone), escalate to Azure OpenAI:

| Model | Use Case | Cost/1K docs | Latency |
|-------|----------|--------------|---------|
| GPT-4o-mini | Intent classification for ambiguous docs | ~$0.30 | 1-2s |
| Azure AI Language | Entity extraction, key phrase extraction | ~$0.50 | 0.5-1s |
| Azure Document Intelligence | Structured form parsing (invoices, receipts) | ~$10 | 2-4s |

**Recommendation:** Use GPT-4o-mini only for the ~10-20% of documents that fall in the 50-79 confidence zone. At 100-500 docs/month, that's 10-100 API calls/month = **$0.03-$0.30/month**. Trivial cost, significant accuracy gain.

**Training data format for future fine-tuning:**
```jsonl
{"text": "Duke Energy - Statement for account #4421...", "label": "PAY", "metadata": {"has_amount": true, "has_due_date": true}}
{"text": "Your auto insurance policy has been updated...", "label": "REVIEW", "metadata": {"has_amount": false, "has_due_date": false}}
{"text": "Please sign and return the enclosed form...", "label": "SIGN", "metadata": {"has_amount": false, "has_due_date": true}}
```

Minimum viable training set: **200 labeled examples** (25 per category).

### 2.5 Edge Cases

| Case | Detection | Handling |
|------|-----------|----------|
| **Multi-intent document** | Two categories score > 60% of top | Create separate actions for each intent |
| **Ambiguous bill vs. statement** | Amount present but "statement" keyword, no "due" keyword | Default to FILE with 60% confidence; let human promote to PAY |
| **Foreign language** | text_quality heuristic detects non-ASCII dominance | Flag for review; don't classify |
| **Scanned image (no OCR)** | content_length < 20 chars | Skip with `disposition=unreadable` |
| **Duplicate content** | Same content hash, different document IDs | Process once, link actions to all doc IDs |
| **Contradictory signals** | "No action needed" + "payment due" | Higher-weight signal wins; flag as needs_review |
| **Promotional mail** | Keywords like "special offer", "limited time" | Score as ARCHIVE/FILE with low confidence |

---

## 3. Risk & Urgency Scoring

### 3.1 Priority Score Formula

The `priority_score` (0-100) determines sort order in Mission Control. It combines urgency, risk, and recency:

```python
def calculate_priority_score(
    days_until_due: Optional[int],  # None if no due date
    risk_score: int,  # 0-100
    amount: Optional[float],
    action_type: str,
    confidence: int,
    created_days_ago: int,
) -> int:
    """Calculate composite priority score (0-100).
    
    Formula:
        priority = (urgency_component * 0.45) + (risk_component * 0.30) 
                 + (financial_component * 0.15) + (type_boost * 0.10)
        
    Then apply recency decay and confidence scaling.
    """
    # 1. Urgency component (0-100): days until due
    if days_until_due is None:
        urgency_component = 30  # No date = moderate baseline
    elif days_until_due < 0:
        urgency_component = 100  # Overdue
    elif days_until_due == 0:
        urgency_component = 98  # Due today
    elif days_until_due <= 2:
        urgency_component = 92
    elif days_until_due <= 7:
        urgency_component = 75
    elif days_until_due <= 14:
        urgency_component = 55
    elif days_until_due <= 30:
        urgency_component = 35
    else:
        urgency_component = 15

    # 2. Risk component (0-100): direct from risk_score
    risk_component = risk_score

    # 3. Financial component (0-100): scaled by amount
    if amount is None or amount <= 0:
        financial_component = 0
    elif amount < 50:
        financial_component = 20
    elif amount < 200:
        financial_component = 40
    elif amount < 1000:
        financial_component = 65
    elif amount < 5000:
        financial_component = 80
    else:
        financial_component = 100

    # 4. Type boost (0-100): inherent priority by action type
    type_boosts = {
        "PAY": 70, "RESPOND": 65, "SIGN": 60, "SCHEDULE": 50,
        "REVIEW": 40, "SHARE": 35, "FILE": 15, "ARCHIVE": 5,
    }
    type_boost = type_boosts.get(action_type, 30)

    # Weighted combination
    raw_priority = (
        urgency_component * 0.45
        + risk_component * 0.30
        + financial_component * 0.15
        + type_boost * 0.10
    )

    # Confidence scaling: low-confidence items get deprioritized
    confidence_factor = max(0.6, confidence / 100)
    
    # Recency decay: items older than 7 days with no action get slight boost
    # (they've been sitting too long), but items older than 60 days decay
    if created_days_ago > 60:
        recency_factor = 0.85  # Stale, probably less relevant
    elif created_days_ago > 7:
        recency_factor = 1.05  # Nudge: been waiting too long
    else:
        recency_factor = 1.0

    final = raw_priority * confidence_factor * recency_factor
    return int(min(100, max(0, final)))
```

### 3.2 Risk Score Calculation

Risk score (0-100) measures potential negative consequences of inaction:

```python
import re
from typing import Optional

# Risk signal patterns with weights
RISK_SIGNALS = {
    # Legal threats (highest risk)
    "legal_action": (re.compile(r"\b(legal action|attorney|lawsuit|court|litigation)\b", re.I), 35),
    "collection": (re.compile(r"\b(collection agency|collections?|debt collector|sent to collections)\b", re.I), 30),
    "final_notice": (re.compile(r"\b(final notice|last chance|final warning|final opportunity)\b", re.I), 28),
    
    # Financial penalties
    "late_fee": (re.compile(r"\b(late fee|penalty|interest charge|finance charge|service charge)\b", re.I), 20),
    "service_disconnect": (re.compile(r"\b(disconnect|shut[\s-]?off|termination of service|suspend)\b", re.I), 25),
    "credit_impact": (re.compile(r"\b(credit report|credit score|credit bureau|reported to)\b", re.I), 22),
    
    # Moderate risk
    "overdue": (re.compile(r"\b(overdue|past due|delinquent|in arrears)\b", re.I), 18),
    "second_notice": (re.compile(r"\b(second notice|2nd notice|reminder|follow[\s-]?up)\b", re.I), 12),
    "deadline_explicit": (re.compile(r"\b(must be received by|deadline|no later than)\b", re.I), 10),
    
    # Low risk signals
    "time_sensitive": (re.compile(r"\b(time[\s-]?sensitive|urgent|immediate attention)\b", re.I), 8),
    "expiration": (re.compile(r"\b(expir(es?|ation|ing)|will lapse|coverage ends)\b", re.I), 8),
}


def calculate_risk_score(
    text: str,
    amount: Optional[float] = None,
    days_until_due: Optional[int] = None,
) -> tuple[int, list[str]]:
    """Calculate risk score from text signals.
    
    Returns:
        (risk_score 0-100, list of triggered signal names)
    """
    triggered: list[str] = []
    raw_score = 0.0

    for signal_name, (pattern, weight) in RISK_SIGNALS.items():
        if pattern.search(text):
            triggered.append(signal_name)
            raw_score += weight

    # Amount amplifier: higher amounts increase risk
    if amount and amount > 0:
        if amount > 5000:
            raw_score *= 1.3
        elif amount > 1000:
            raw_score *= 1.2
        elif amount > 500:
            raw_score *= 1.1

    # Overdue amplifier
    if days_until_due is not None and days_until_due < 0:
        overdue_days = abs(days_until_due)
        if overdue_days > 30:
            raw_score *= 1.4
        elif overdue_days > 14:
            raw_score *= 1.2
        elif overdue_days > 0:
            raw_score *= 1.1

    # Cap at 100
    return int(min(100, max(0, raw_score))), triggered
```

### 3.3 Due Date Extraction & Weighting

Enhanced date extraction beyond simple regex:

```python
import re
from datetime import date, timedelta
from typing import Optional

# Patterns ordered by specificity (most specific first)
DATE_PATTERNS = [
    # "Due by March 15, 2026" or "Payment due: 03/15/2026"
    (re.compile(r"(?:due|by|before|no later than)[:\s]*(\w+ \d{1,2},?\s*\d{4})", re.I), "named_month_full"),
    (re.compile(r"(?:due|by|before)[:\s]*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})", re.I), "due_explicit"),
    # "Expires: 2026-03-15"
    (re.compile(r"(?:expir\w+|expires?)[:\s]*(\d{4}-\d{2}-\d{2})", re.I), "iso_date"),
    # Generic dates (lower priority)
    (re.compile(r"\b(\d{1,2}[/\-]\d{1,2}[/\-]20\d{2})\b"), "generic_date"),
]

# Context keywords that confirm a date is a DUE date (not just any date)
DUE_DATE_CONTEXT = re.compile(
    r"\b(due|payment|deadline|must|required|expir|by|before|no later than)\b", re.I
)


def extract_due_date(text: str) -> tuple[Optional[date], float]:
    """Extract the most likely due date from text.
    
    Returns:
        (date_or_None, relevance_weight 0-1)
        
    Weight indicates how confident we are this IS a due date
    (vs. a random date in the document).
    """
    from dateutil import parser as dateutil_parser

    candidates: list[tuple[date, float, int]] = []  # (date, weight, position)

    for pattern, pattern_type in DATE_PATTERNS:
        for match in pattern.finditer(text):
            try:
                parsed = dateutil_parser.parse(match.group(1), fuzzy=True).date()
            except (ValueError, OverflowError):
                continue

            # Only consider dates within reasonable range
            today = date.today()
            if parsed < today - timedelta(days=90) or parsed > today + timedelta(days=730):
                continue

            # Weight based on pattern type and context
            weight = {
                "due_explicit": 0.95,
                "named_month_full": 0.90,
                "iso_date": 0.80,
                "generic_date": 0.50,
            }.get(pattern_type, 0.5)

            # Boost if date appears near due-date context keywords
            context_window = text[max(0, match.start() - 80):match.end() + 20]
            if DUE_DATE_CONTEXT.search(context_window):
                weight = min(1.0, weight + 0.2)

            candidates.append((parsed, weight, match.start()))

    if not candidates:
        return None, 0.0

    # Pick the candidate with highest weight; break ties by earliest position
    candidates.sort(key=lambda c: (-c[1], c[2]))
    best_date, best_weight, _ = candidates[0]
    return best_date, best_weight
```

**Days-remaining weighting table:**

| Days Remaining | Urgency Level | Priority Multiplier |
|----------------|---------------|---------------------|
| < 0 (overdue) | CRITICAL | 1.5x |
| 0-2 | CRITICAL | 1.4x |
| 3-7 | HIGH | 1.2x |
| 8-14 | MEDIUM | 1.0x |
| 15-30 | LOW | 0.8x |
| 31+ | NONE | 0.6x |

### 3.4 Late Fee / Penalty Detection

```python
import re
from typing import Optional
from dataclasses import dataclass


@dataclass
class PenaltyInfo:
    """Detected late fee or penalty information."""
    penalty_type: str  # late_fee, interest, disconnect, legal
    amount: Optional[float]  # Dollar amount if parseable
    description: str  # Raw matched text
    severity: str  # low, medium, high, critical


# Patterns that capture penalty amounts
PENALTY_PATTERNS = [
    (re.compile(r"late fee[:\s]*\$?([\d,]+\.?\d{0,2})", re.I), "late_fee"),
    (re.compile(r"penalty[:\s]*\$?([\d,]+\.?\d{0,2})", re.I), "late_fee"),
    (re.compile(r"interest(?:\s*charge)?[:\s]*\$?([\d,]+\.?\d{0,2})", re.I), "interest"),
    (re.compile(r"finance charge[:\s]*\$?([\d,]+\.?\d{0,2})", re.I), "interest"),
    (re.compile(r"(?:will be|subject to)\s+(?:a\s+)?\$?([\d,]+\.?\d{0,2})\s*(?:late|penalty|fee)", re.I), "late_fee"),
    (re.compile(r"(\d+\.?\d{0,2})%\s*(?:per|monthly|annual)\s*interest", re.I), "interest_rate"),
]

# Non-amount penalty threats
PENALTY_THREATS = [
    (re.compile(r"\b(service will be disconnected|shut[\s-]?off notice)\b", re.I), "disconnect", "critical"),
    (re.compile(r"\b(sent to collections?|collection agency)\b", re.I), "legal", "critical"),
    (re.compile(r"\b(report(?:ed)? to credit|credit bureau)\b", re.I), "legal", "high"),
    (re.compile(r"\b(legal action|attorney fees|court costs)\b", re.I), "legal", "critical"),
    (re.compile(r"\b(lien|garnish|levy)\b", re.I), "legal", "critical"),
    (re.compile(r"\b(cancel|terminate|revoke).{0,20}(?:coverage|policy|service)\b", re.I), "disconnect", "high"),
]


def detect_penalties(text: str) -> list[PenaltyInfo]:
    """Detect all late fee/penalty signals in document text."""
    penalties: list[PenaltyInfo] = []

    # Amount-based penalties
    for pattern, penalty_type in PENALTY_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                amount = float(match.group(1).replace(",", ""))
            except (ValueError, IndexError):
                amount = None
            
            severity = "medium"
            if amount and amount > 100:
                severity = "high"
            elif amount and amount > 25:
                severity = "medium"
            else:
                severity = "low"

            penalties.append(PenaltyInfo(
                penalty_type=penalty_type,
                amount=amount,
                description=match.group(0).strip(),
                severity=severity,
            ))

    # Threat-based penalties (no dollar amount, but serious)
    for pattern, penalty_type, severity in PENALTY_THREATS:
        match = pattern.search(text)
        if match:
            penalties.append(PenaltyInfo(
                penalty_type=penalty_type,
                amount=None,
                description=match.group(0).strip(),
                severity=severity,
            ))

    return penalties
```

### 3.5 Urgency Levels

Defined mapping from days-remaining to urgency enum:

```python
from enum import Enum
from typing import Optional
from datetime import date


class UrgencyLevel(str, Enum):
    CRITICAL = "CRITICAL"  # < 2 days or overdue or has critical penalties
    HIGH = "HIGH"          # < 7 days
    MEDIUM = "MEDIUM"      # < 14 days
    LOW = "LOW"            # < 30 days
    NONE = "NONE"          # > 30 days or no deadline


def determine_urgency(
    due_date: Optional[date],
    penalties: list,  # list[PenaltyInfo]
    risk_score: int,
) -> UrgencyLevel:
    """Determine urgency level from all available signals.
    
    Priority order:
    1. Critical penalties override everything
    2. Due date proximity
    3. Risk score as tiebreaker
    """
    # Critical penalties = instant CRITICAL
    if any(p.severity == "critical" for p in penalties):
        return UrgencyLevel.CRITICAL

    # Due date calculation
    if due_date:
        days_remaining = (due_date - date.today()).days
        if days_remaining < 0:
            return UrgencyLevel.CRITICAL  # Overdue
        elif days_remaining <= 2:
            return UrgencyLevel.CRITICAL
        elif days_remaining <= 7:
            return UrgencyLevel.HIGH
        elif days_remaining <= 14:
            return UrgencyLevel.MEDIUM
        elif days_remaining <= 30:
            return UrgencyLevel.LOW
        else:
            return UrgencyLevel.NONE

    # No due date: use risk score
    if risk_score >= 70:
        return UrgencyLevel.HIGH
    elif risk_score >= 40:
        return UrgencyLevel.MEDIUM
    elif risk_score >= 15:
        return UrgencyLevel.LOW
    else:
        return UrgencyLevel.NONE
```

### 3.6 Recency Decay

Actions that sit unresolved for too long should either escalate or decay:

```python
def apply_recency_decay(
    priority_score: int,
    action_created_at: date,
    action_type: str,
    has_due_date: bool,
) -> int:
    """Adjust priority based on action age.
    
    - Items WITH a due date: never decay (they escalate as due date approaches)
    - Items WITHOUT due date:
      - Days 0-7: no change
      - Days 8-30: slight boost (+5%) — "you've been ignoring this"
      - Days 31-60: start decaying (-2% per day over 30)
      - Days 60+: cap at 60% of original — stale, probably not urgent
    """
    if has_due_date:
        return priority_score  # Due-date items self-escalate

    age_days = (date.today() - action_created_at).days

    if age_days <= 7:
        return priority_score
    elif age_days <= 30:
        # Gentle nudge: "you've been ignoring this"
        return int(priority_score * 1.05)
    elif age_days <= 60:
        # Start decay
        decay_days = age_days - 30
        decay_factor = max(0.6, 1.0 - (decay_days * 0.013))  # ~2%/day loss
        return int(priority_score * decay_factor)
    else:
        # Stale: cap at 60% of original
        return int(priority_score * 0.6)
```

---

## 4. Feedback Loop

### 4.1 Corrections as Training Data

When a user changes an action's type, urgency, or dismisses it, that's training data:

```python
# Database model for feedback
# In: src/doc_intelligence_hub/modules/action_queue/feedback.py

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, JSON, Float
from .database import Base


class FeedbackEntry(Base):
    """Records user corrections for model improvement."""
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    action_id = Column(Integer, nullable=False, index=True)
    document_id = Column(Integer, nullable=False, index=True)
    
    # What the model predicted
    predicted_action_type = Column(String, nullable=False)
    predicted_confidence = Column(Integer, nullable=False)
    predicted_urgency = Column(String, nullable=True)
    
    # What the user corrected to (null if user confirmed prediction)
    corrected_action_type = Column(String, nullable=True)
    corrected_urgency = Column(String, nullable=True)
    
    # Feedback type
    feedback_type = Column(String, nullable=False)
    # Types: confirmed, type_changed, dismissed_wrong, dismissed_duplicate,
    #        urgency_changed, amount_corrected
    
    # Context snapshot (for training data export)
    document_text_snippet = Column(Text, nullable=True)  # First 2000 chars
    document_tags = Column(JSON, nullable=True)
    document_correspondent = Column(String, nullable=True)
    
    # Metadata
    model_version = Column(String, nullable=True)  # Which model made the prediction
    created_at = Column(DateTime, default=datetime.utcnow)


class ModelAccuracyMetric(Base):
    """Rolling accuracy metrics per model version."""
    __tablename__ = "model_accuracy"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_version = Column(String, nullable=False, index=True)
    window_start = Column(DateTime, nullable=False)
    window_end = Column(DateTime, nullable=False)
    total_predictions = Column(Integer, default=0)
    correct_predictions = Column(Integer, default=0)
    accuracy = Column(Float, default=0.0)
    category_accuracy = Column(JSON, nullable=True)  # {"PAY": 0.92, "REVIEW": 0.75, ...}
    created_at = Column(DateTime, default=datetime.utcnow)
```

**Feedback capture hook (integrates with existing pipeline):**

```python
# In the API layer when user updates an action:
async def record_feedback(
    action_id: int,
    old_action_type: str,
    new_action_type: Optional[str],
    old_urgency: str,
    new_urgency: Optional[str],
    feedback_type: str,
    db_session,
):
    """Record user correction as training data.
    
    Called by: PUT /api/action-queue/actions/{id} endpoint
    """
    action = db_session.query(Action).get(action_id)
    if not action:
        return

    # Fetch document text snippet for training context
    text_snippet = None
    history = db_session.query(ProcessingHistory).filter_by(
        document_id=action.document_id
    ).first()

    entry = FeedbackEntry(
        action_id=action_id,
        document_id=action.document_id,
        predicted_action_type=old_action_type,
        predicted_confidence=action.confidence,
        predicted_urgency=old_urgency,
        corrected_action_type=new_action_type if new_action_type != old_action_type else None,
        corrected_urgency=new_urgency if new_urgency != old_urgency else None,
        feedback_type=feedback_type,
        document_text_snippet=text_snippet,
        document_tags=action.extracted_data.get("tags") if action.extracted_data else None,
        document_correspondent=action.correspondent,
        model_version=settings.analysis_version,
    )
    db_session.add(entry)
    db_session.commit()
```

### 4.2 Confidence Threshold Adjustment

The auto-accept threshold (default 80) adapts based on observed accuracy:

```python
def calculate_optimal_threshold(
    feedback_entries: list,  # Recent FeedbackEntry records
    current_threshold: int = 80,
    target_accuracy: float = 0.90,
    min_sample_size: int = 30,
) -> int:
    """Adjust confidence threshold based on observed accuracy at each level.
    
    Strategy:
    - If accuracy above threshold is > target: LOWER threshold (accept more)
    - If accuracy above threshold is < target: RAISE threshold (be more strict)
    - Never go below 60 or above 95
    
    Uses binary search over confidence levels to find the sweet spot.
    """
    if len(feedback_entries) < min_sample_size:
        return current_threshold  # Not enough data to adjust

    # Group feedback by confidence bucket
    buckets: dict[int, dict] = {}  # confidence_floor -> {correct, total}
    for entry in feedback_entries:
        bucket = (entry.predicted_confidence // 5) * 5  # 5-point buckets
        if bucket not in buckets:
            buckets[bucket] = {"correct": 0, "total": 0}
        buckets[bucket]["total"] += 1
        if entry.corrected_action_type is None:  # User confirmed or didn't change
            buckets[bucket]["correct"] += 1

    # Find the threshold where accuracy >= target
    for threshold in range(60, 96, 5):
        # Calculate accuracy for predictions AT or ABOVE this threshold
        correct = sum(b["correct"] for t, b in buckets.items() if t >= threshold)
        total = sum(b["total"] for t, b in buckets.items() if t >= threshold)
        
        if total >= 10:  # Need minimum sample in this range
            accuracy = correct / total
            if accuracy >= target_accuracy:
                # This threshold gives us acceptable accuracy
                # Bias toward lowering slowly (conservative)
                new_threshold = max(60, min(95, threshold))
                # Don't move more than 5 points per adjustment
                if new_threshold < current_threshold:
                    return max(current_threshold - 5, new_threshold)
                elif new_threshold > current_threshold:
                    return min(current_threshold + 5, new_threshold)
                return new_threshold

    # Couldn't find good threshold — raise it
    return min(95, current_threshold + 5)
```

### 4.3 Retraining Triggers

Retraining the model (or adjusting rules) happens when:

```python
@dataclass
class RetrainTrigger:
    """Conditions that trigger a model retrain/rule adjustment."""
    
    # Minimum samples before any retraining is considered
    min_total_feedback: int = 50
    
    # Accuracy dropped below this in a rolling 30-day window
    accuracy_floor: float = 0.80
    
    # New feedback since last retrain exceeds this count
    new_feedback_threshold: int = 30
    
    # Accuracy dropped more than this from the model's baseline
    accuracy_drift_threshold: float = 0.10  # 10% drop
    
    # Maximum days between retrains (forces periodic refresh)
    max_days_between_retrains: int = 90


def should_retrain(
    current_accuracy: float,
    baseline_accuracy: float,
    feedback_since_last_train: int,
    days_since_last_train: int,
    total_feedback: int,
    trigger: RetrainTrigger = None,
) -> tuple[bool, str]:
    """Determine if retraining should be triggered.
    
    Returns:
        (should_retrain, reason)
    """
    trigger = trigger or RetrainTrigger()

    if total_feedback < trigger.min_total_feedback:
        return False, "insufficient_data"

    if current_accuracy < trigger.accuracy_floor:
        return True, f"accuracy_below_floor ({current_accuracy:.2%} < {trigger.accuracy_floor:.2%})"

    drift = baseline_accuracy - current_accuracy
    if drift > trigger.accuracy_drift_threshold:
        return True, f"accuracy_drift ({drift:.2%} drop from baseline)"

    if feedback_since_last_train >= trigger.new_feedback_threshold:
        return True, f"new_feedback_available ({feedback_since_last_train} entries)"

    if days_since_last_train >= trigger.max_days_between_retrains:
        return True, f"periodic_refresh ({days_since_last_train} days)"

    return False, "no_trigger"
```

### 4.4 Model Versioning

```python
# Version format: {method}.{major}.{minor}.{patch}
# method: "rules" | "ollama" | "azure" | "hybrid"
# major: breaking changes to classification logic
# minor: new signals/keywords added
# patch: threshold adjustments, bugfixes

# Examples:
# "rules.1.0.0"  — initial rule-based system
# "rules.1.1.0"  — added new keywords
# "rules.1.1.1"  — adjusted confidence threshold
# "ollama.2.0.0" — switched to Ollama-powered classification
# "hybrid.3.0.0" — rules + Ollama + Azure escalation

CURRENT_ANALYSIS_VERSION = "rules.1.0.0"


@dataclass
class ModelVersion:
    method: str
    major: int
    minor: int
    patch: int
    trained_at: Optional[datetime] = None
    training_samples: int = 0
    baseline_accuracy: float = 0.0
    
    @classmethod
    def parse(cls, version_str: str) -> "ModelVersion":
        method, semver = version_str.split(".", 1)
        major, minor, patch = semver.split(".")
        return cls(method=method, major=int(major), minor=int(minor), patch=int(patch))
    
    def __str__(self) -> str:
        return f"{self.method}.{self.major}.{self.minor}.{self.patch}"
    
    def is_compatible_with(self, other: "ModelVersion") -> bool:
        """Check if results are comparable (same method + major)."""
        return self.method == other.method and self.major == other.major
```

### 4.5 A/B Testing Framework

Simple percentage-split A/B testing for comparing model versions:

```python
import hashlib
from dataclasses import dataclass
from typing import Optional


@dataclass
class ABTestConfig:
    """Configuration for an active A/B test."""
    test_id: str
    variant_a: str  # Model version A (typically current/control)
    variant_b: str  # Model version B (challenger)
    traffic_pct_b: int  # Percentage of docs routed to variant B (0-100)
    started_at: datetime
    min_samples_per_variant: int = 50
    max_duration_days: int = 30


def assign_variant(document_id: int, test_config: ABTestConfig) -> str:
    """Deterministically assign a document to a test variant.
    
    Uses document_id hash for consistent assignment (same doc always
    gets same variant, even on reprocess).
    """
    hash_input = f"{test_config.test_id}:{document_id}"
    hash_value = int(hashlib.sha256(hash_input.encode()).hexdigest()[:8], 16)
    bucket = hash_value % 100
    
    if bucket < test_config.traffic_pct_b:
        return test_config.variant_b
    return test_config.variant_a


def evaluate_ab_test(
    variant_a_results: list,  # FeedbackEntry for variant A
    variant_b_results: list,  # FeedbackEntry for variant B
    min_samples: int = 50,
) -> Optional[dict]:
    """Evaluate A/B test results.
    
    Returns None if insufficient data, otherwise returns winner analysis.
    """
    if len(variant_a_results) < min_samples or len(variant_b_results) < min_samples:
        return None  # Not enough data

    def accuracy(entries):
        if not entries:
            return 0.0
        correct = sum(1 for e in entries if e.corrected_action_type is None)
        return correct / len(entries)

    acc_a = accuracy(variant_a_results)
    acc_b = accuracy(variant_b_results)

    return {
        "variant_a_accuracy": acc_a,
        "variant_b_accuracy": acc_b,
        "variant_a_samples": len(variant_a_results),
        "variant_b_samples": len(variant_b_results),
        "winner": "b" if acc_b > acc_a + 0.03 else ("a" if acc_a > acc_b + 0.03 else "tie"),
        "improvement": acc_b - acc_a,
    }
```

### 4.6 Active Learning

Surface the most uncertain predictions for human feedback to maximize learning efficiency:

```python
def select_for_active_learning(
    pending_actions: list,  # Actions with status="pending"
    daily_budget: int = 5,  # Max items to surface per day
) -> list:
    """Select the most valuable predictions to get human feedback on.
    
    Strategy (uncertainty sampling):
    1. Predictions closest to the auto-accept threshold (most uncertain)
    2. Predictions where primary and secondary intents are close
    3. One from each underrepresented category (diversity)
    
    These get a "needs_review" flag in Mission Control, prompting the user
    to confirm or correct before the action is considered final.
    """
    # Sort by "uncertainty" — distance from decision boundaries
    def uncertainty_score(action) -> float:
        # Closer to threshold = more uncertain
        threshold = settings.confidence_threshold
        distance_from_threshold = abs(action.confidence - threshold)
        
        # Normalize: 0 = at threshold (max uncertainty), 1 = far from threshold
        normalized_distance = min(distance_from_threshold / 30, 1.0)
        
        # Invert: higher = more uncertain
        return 1.0 - normalized_distance

    scored = [(action, uncertainty_score(action)) for action in pending_actions]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Take top N by uncertainty, but ensure category diversity
    selected = []
    seen_categories = set()
    
    for action, score in scored:
        if len(selected) >= daily_budget:
            break
        # Prefer diversity: one from each category first
        if action.action_type not in seen_categories or len(selected) < daily_budget // 2:
            selected.append(action)
            seen_categories.add(action.action_type)

    return selected
```

---

## 5. Deduplication Refinement

### 5.1 Hash Algorithm Spec

Content hash determines if a document has materially changed:

```python
import hashlib
from typing import Optional


def compute_document_hash(
    content: str,
    title: str,
    correspondent: Optional[str] = None,
) -> str:
    """Compute a content hash for deduplication.
    
    What we hash:
    - Normalized document text (stripped, lowercased, whitespace-collapsed)
    - Document title (normalized)
    - Correspondent name (normalized)
    
    What we DON'T hash (intentionally):
    - Tags (change frequently, don't mean content changed)
    - Created/added dates (metadata, not content)
    - Custom fields (downstream data, not source)
    - Document ID (varies across systems)
    
    Normalization:
    1. Strip leading/trailing whitespace
    2. Collapse multiple whitespace to single space
    3. Lowercase
    4. Remove common OCR artifacts (isolated single chars, repeated punctuation)
    """
    import re

    def normalize(text: str) -> str:
        if not text:
            return ""
        text = text.strip().lower()
        text = re.sub(r'\s+', ' ', text)  # Collapse whitespace
        text = re.sub(r'[^\w\s$@.,-]', '', text)  # Remove noise punctuation
        text = re.sub(r'\b\w\b', '', text)  # Remove isolated single chars (OCR noise)
        text = re.sub(r'\s+', ' ', text).strip()  # Re-collapse after removals
        return text

    # Hash components
    parts = [
        normalize(content),
        normalize(title),
        normalize(correspondent or ""),
    ]
    combined = "\n---\n".join(parts)
    
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
```

### 5.2 Re-processing Triggers

Documents should be re-analyzed when any of these change:

```python
from dataclasses import dataclass
from typing import Optional


@dataclass
class ReprocessTrigger:
    """Conditions that force re-analysis of a previously processed document."""
    
    # Content changed (new hash differs from stored hash)
    content_hash_changed: bool = False
    
    # Model version changed (new rules/model available)
    analysis_version_changed: bool = False
    
    # OCR quality improved (re-OCR'd document)
    ocr_quality_improved: bool = False
    
    # Configuration changed (thresholds, keywords)
    config_version_changed: bool = False
    
    # Explicit user request (force flag)
    force_reprocess: bool = False
    
    # Tag change that affects classification (e.g., added "bill" tag)
    relevant_tag_changed: bool = False
    
    @property
    def should_reprocess(self) -> bool:
        return any([
            self.content_hash_changed,
            self.analysis_version_changed,
            self.ocr_quality_improved,
            self.config_version_changed,
            self.force_reprocess,
            self.relevant_tag_changed,
        ])
    
    @property
    def reason(self) -> str:
        reasons = []
        if self.content_hash_changed:
            reasons.append("content_changed")
        if self.analysis_version_changed:
            reasons.append("model_updated")
        if self.ocr_quality_improved:
            reasons.append("ocr_improved")
        if self.config_version_changed:
            reasons.append("config_changed")
        if self.force_reprocess:
            reasons.append("force")
        if self.relevant_tag_changed:
            reasons.append("tag_changed")
        return ",".join(reasons) or "none"


# Tags that affect classification (changes to these trigger reprocess)
CLASSIFICATION_RELEVANT_TAGS = {
    "bill", "invoice", "medical", "legal", "tax", "insurance",
    "contract", "receipt", "statement",
}


def check_reprocess_needed(
    document: dict,
    history: "ProcessingHistory",
    current_analysis_version: str,
    current_config_version: str,
) -> ReprocessTrigger:
    """Check if a document needs re-analysis."""
    trigger = ReprocessTrigger()

    # Content hash comparison
    new_hash = compute_document_hash(
        content=document.get("content", ""),
        title=document.get("title", ""),
        correspondent=document.get("correspondent_name"),
    )
    if history.document_checksum and history.document_checksum != new_hash:
        trigger.content_hash_changed = True

    # Analysis version comparison
    stored_version = history.ollama_model or "unknown"
    if stored_version != current_analysis_version:
        trigger.analysis_version_changed = True

    # OCR quality: compare stored vs current text metrics
    current_length = len(document.get("content", ""))
    if history.content_length and current_length > history.content_length * 1.5:
        trigger.ocr_quality_improved = True  # Significantly more text = better OCR

    return trigger
```

### 5.3 Analysis Version Format

```python
# Format: "{method}.{major}.{minor}.{patch}+{config_hash}"
# The config_hash suffix captures threshold/keyword changes that affect output
# without changing the core algorithm.

import hashlib
import json


def compute_config_hash() -> str:
    """Hash configuration values that affect analysis output."""
    config_values = {
        "confidence_threshold": settings.confidence_threshold,
        "ollama_model": settings.ollama_model,
        # Add any other settings that change classification behavior
    }
    config_str = json.dumps(config_values, sort_keys=True)
    return hashlib.md5(config_str.encode()).hexdigest()[:8]


def get_analysis_version() -> str:
    """Get the full analysis version string including config hash."""
    base_version = CURRENT_ANALYSIS_VERSION  # e.g., "rules.1.0.0"
    config_hash = compute_config_hash()
    return f"{base_version}+{config_hash}"


def versions_are_equivalent(v1: str, v2: str) -> bool:
    """Check if two versions would produce the same results.
    
    Versions are equivalent if base version AND config hash match.
    """
    return v1 == v2
```

**Comparison logic for dedup:**

```python
def should_skip_document(history: ProcessingHistory, current_version: str) -> bool:
    """Determine if we can skip this document based on processing history.
    
    Skip if:
    1. Previously processed successfully
    2. Same analysis version (base + config)
    3. Content hash hasn't changed
    
    Don't skip if:
    1. Previous processing failed
    2. Analysis version changed
    3. Content hash differs (re-OCR'd)
    """
    if not history.success:
        return False  # Retry failures
    
    stored_version = history.ollama_model  # We store version in this field
    if not versions_are_equivalent(stored_version or "", current_version):
        return False  # Model/config changed
    
    return True  # Safe to skip
```

### 5.4 Cache Invalidation Strategy

```
Cache Layers:
1. ProcessingHistory table (document_id → was-processed flag)
2. Content hash (document_id → sha256 of normalized content)
3. Analysis version (stored per-record)

Invalidation events:
┌─────────────────────────┬──────────────────────────────────────┐
│ Event                   │ Invalidation Scope                    │
├─────────────────────────┼──────────────────────────────────────┤
│ Model version bump      │ ALL documents (batch reprocess)       │
│ Config threshold change │ ALL documents (batch reprocess)       │
│ Document re-OCR'd       │ Single document (content hash diff)   │
│ Tag added/removed       │ Single document (if relevant tag)     │
│ Force reprocess flag    │ Single document or batch              │
│ Feedback loop retrain   │ Documents in low-confidence band only │
│ Keyword list update     │ Minor version bump → ALL              │
└─────────────────────────┴──────────────────────────────────────┘
```

**Batch invalidation command:**

```python
# CLI: python -m doc_intelligence_hub.modules.action_queue.cli invalidate --reason model_update
# This marks all ProcessingHistory records as needing reprocessing by clearing success flag

async def invalidate_cache(
    reason: str,
    scope: str = "all",  # "all", "low_confidence", "failed", "document_ids"
    document_ids: Optional[list[int]] = None,
    confidence_below: Optional[int] = None,
):
    """Invalidate processing cache to trigger re-analysis.
    
    Does NOT delete history — marks records for reprocessing.
    """
    db = get_session()
    query = db.query(ProcessingHistory)
    
    if scope == "low_confidence" and confidence_below:
        # Only invalidate documents that scored below a threshold
        # (These are the ones most likely to benefit from a better model)
        doc_ids_to_invalidate = [
            a.document_id for a in
            db.query(Action.document_id)
            .filter(Action.confidence < confidence_below)
            .distinct()
            .all()
        ]
        query = query.filter(ProcessingHistory.document_id.in_(doc_ids_to_invalidate))
    elif scope == "failed":
        query = query.filter(ProcessingHistory.success == 0)
    elif scope == "document_ids" and document_ids:
        query = query.filter(ProcessingHistory.document_id.in_(document_ids))
    
    # Mark for reprocessing (don't delete — preserve history)
    count = query.update({"success": 0, "error_message": f"invalidated:{reason}"})
    db.commit()
    db.close()
    return count
```

### 5.5 Document Update Handling

When Paperless documents get edited metadata or re-OCR'd text:

```python
async def handle_document_update(
    document_id: int,
    change_type: str,  # "metadata", "content", "tags"
    paperless_client: "PaperlessClient",
):
    """Handle a document that was updated in Paperless after processing.
    
    Called by: webhook from Paperless (if configured) or detected on next scan.
    
    Strategy:
    - metadata change (title, correspondent): Update action records, don't re-analyze
    - content change (re-OCR): Full re-analysis (content hash will differ)
    - tag change: Re-analyze only if relevant tag added/removed
    """
    db = get_session()
    history = db.query(ProcessingHistory).filter_by(document_id=document_id).first()
    
    if not history:
        return  # Never processed — will be picked up on next scan
    
    if change_type == "metadata":
        # Just update action records with new metadata
        doc = await paperless_client.get_document(document_id)
        actions = db.query(Action).filter_by(document_id=document_id).all()
        for action in actions:
            action.document_title = doc.get("title", action.document_title)
            new_corr = doc.get("correspondent_name")
            if new_corr:
                action.correspondent = new_corr
        db.commit()
        
    elif change_type == "content":
        # Full re-analysis needed
        history.success = 0
        history.error_message = "invalidated:content_updated"
        db.commit()
        
    elif change_type == "tags":
        doc = await paperless_client.get_document(document_id)
        current_tags = set(t.lower() for t in doc.get("tag_names", []))
        if current_tags & CLASSIFICATION_RELEVANT_TAGS:
            # A relevant tag was changed — re-analyze
            history.success = 0
            history.error_message = "invalidated:relevant_tag_changed"
            db.commit()
    
    db.close()
```

---

## 6. LLM/AI Model Strategy

### 6.1 Local Models (Ollama)

| Model | Size | Speed (homelab) | Quality | Best For |
|-------|------|-----------------|---------|----------|
| **phi3:mini (3.8B)** ✅ current | 2.3 GB | ~8-15s/doc | Good for structured tasks | Current production. Great at JSON extraction. |
| **llama3 (8B)** | 4.7 GB | ~15-25s/doc | Better reasoning | Intent detection on ambiguous docs |
| **mistral (7B)** | 4.1 GB | ~12-20s/doc | Good general | Alternative to llama3, faster |
| **phi3:medium (14B)** | 8 GB | ~30-45s/doc | Excellent | Too slow for batch, good for escalation |

**Recommendation:** Stay on `phi3:mini` for the main pipeline. It's fast enough for batch processing and produces reliable structured JSON. Use `llama3` only for the escalation path (when confidence is 50-79 and you want a second opinion before sending to Azure).

### 6.2 Azure AI Options

| Service | Use Case | Cost/1K docs | When to Use |
|---------|----------|--------------|-------------|
| **Azure OpenAI (GPT-4o-mini)** | Intent classification, risk assessment | ~$0.30 | Low-confidence escalation (10-20% of docs) |
| **Azure Document Intelligence** | Invoice/receipt structured extraction | ~$10 | Only for financial docs that need exact amounts |
| **Azure AI Language** | Entity extraction, key phrase | ~$0.50 | Not needed — local models handle this fine |

**Cost projection at homelab scale:**

| Monthly Docs | % Escalated to Azure | Azure Calls | Monthly Cost |
|--------------|---------------------|-------------|--------------|
| 100 | 15% | 15 | ~$0.005 |
| 300 | 15% | 45 | ~$0.014 |
| 500 | 20% | 100 | ~$0.03 |

**Verdict:** Azure costs are negligible at homelab scale. The question is latency and complexity, not money.

### 6.3 Hybrid Approach

**The recommended architecture (opinionated):**

```
Document arrives
    │
    ▼
┌─────────────────────────┐
│ Rule-based classifier   │ ← Always runs first (instant, no GPU)
│ (fallback_analyzer.py)  │
└────────────┬────────────┘
             │
    confidence >= 80? ─── YES ──→ Accept. Done.
             │
             NO (< 80)
             │
             ▼
┌─────────────────────────┐
│ Ollama (phi3:mini)      │ ← Runs for uncertain cases
│ (analyzer.py)           │
└────────────┬────────────┘
             │
    confidence >= 80? ─── YES ──→ Accept. Done.
             │
             NO (50-79)
             │
             ▼
┌─────────────────────────┐
│ Azure GPT-4o-mini       │ ← Only for the hardest 5-15%
│ (cloud escalation)      │
└────────────┬────────────┘
             │
    confidence >= 60? ─── YES ──→ Accept with needs_review flag.
             │
             NO (< 60)
             │
             ▼
    Surface for human review (active learning candidate)
```

**What should stay local:**
- ✅ Rule-based detection (always runs, zero cost)
- ✅ phi3:mini for ~80% of documents  
- ✅ Date/amount extraction (regex, no AI needed)
- ✅ Risk scoring (rule-based, deterministic)
- ✅ Deduplication (pure hashing)

**What justifies cloud AI:**
- ☁️ Ambiguous multi-page contracts
- ☁️ Documents where phi3:mini returns confidence 50-65
- ☁️ Foreign language documents
- ☁️ Complex intent: "pay this if it's a bill, but it might be a refund notice"

### 6.4 Cost Estimates

**Monthly operating costs at 300 docs/month (typical homelab):**

| Component | Resource | Cost |
|-----------|----------|------|
| Ollama (phi3:mini) | Homelab GPU/CPU | $0 (already running) |
| SQLite/PostgreSQL | Homelab storage | $0 |
| n8n | Homelab Docker | $0 |
| Azure OpenAI (escalation) | ~45 API calls | ~$0.02 |
| Azure Document Intelligence | 0 (not recommended for MVP) | $0 |
| **Total** | | **~$0.02/month** |

**Conclusion:** The hybrid approach costs essentially nothing at homelab scale. The Azure escalation path is free-tier eligible for the first 12 months regardless.

---

## 7. Configuration Schema

New settings to add to `config.py`:

```python
# In: src/doc_intelligence_hub/modules/action_queue/config.py
# (extends existing Settings class)

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    # === Existing settings (unchanged) ===
    paperless_url: str = Field(default="http://paperless:8000")
    paperless_token: str = Field(default="")
    ollama_url: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="phi3:mini")
    database_url: str = Field(default="sqlite:///./data/actions.db")
    confidence_threshold: int = Field(default=70)
    tags_to_monitor: str = Field(default="Inbox,Todo")
    write_to_paperless: bool = Field(default=True)
    rate_limit_delay: float = Field(default=1.0)

    # === Scheduling ===
    api_token: str = Field(default="", description="Bearer token for API authentication")
    webhook_secret: str = Field(default="", description="Shared secret for webhook verification")
    max_retries: int = Field(default=3, description="Max retry attempts per pipeline run")
    retry_base_delay: float = Field(default=30.0, description="Base delay in seconds for exponential backoff")
    retry_max_delay: float = Field(default=600.0, description="Maximum retry delay cap")
    pipeline_timeout: int = Field(default=300, description="Pipeline run timeout in seconds")
    max_documents_per_run: int = Field(default=50, description="Safety limit on docs per run")

    # === Intent Detection ===
    auto_accept_threshold: int = Field(default=80, description="Confidence >= this: auto-accept")
    human_review_threshold: int = Field(default=50, description="Confidence >= this but < auto_accept: needs review")
    enable_multi_intent: bool = Field(default=True, description="Detect multiple actions per document")
    
    # === Risk & Urgency ===
    risk_score_enabled: bool = Field(default=True, description="Enable risk scoring")
    urgency_overdue_boost: float = Field(default=1.5, description="Priority multiplier for overdue items")
    critical_days_threshold: int = Field(default=2, description="Days until due for CRITICAL urgency")
    high_days_threshold: int = Field(default=7)
    medium_days_threshold: int = Field(default=14)
    low_days_threshold: int = Field(default=30)

    # === AI Escalation ===
    enable_azure_escalation: bool = Field(default=False, description="Enable Azure AI for low-confidence docs")
    azure_openai_endpoint: str = Field(default="", description="Azure OpenAI endpoint URL")
    azure_openai_key: str = Field(default="", description="Azure OpenAI API key")
    azure_openai_model: str = Field(default="gpt-4o-mini", description="Azure OpenAI deployment name")
    azure_escalation_threshold: int = Field(default=50, description="Confidence below auto_accept but above this: escalate to Azure")
    ollama_escalation_model: str = Field(default="llama3", description="Larger Ollama model for local escalation")
    enable_local_escalation: bool = Field(default=True, description="Try larger local model before Azure")

    # === Feedback Loop ===
    feedback_enabled: bool = Field(default=True, description="Track user corrections")
    threshold_auto_adjust: bool = Field(default=False, description="Auto-adjust confidence thresholds from feedback")
    min_feedback_for_adjustment: int = Field(default=30, description="Minimum feedback entries before auto-adjustment")
    target_accuracy: float = Field(default=0.90, description="Target accuracy for threshold adjustment")
    active_learning_budget: int = Field(default=5, description="Max items to surface for review per day")

    # === A/B Testing ===
    ab_test_enabled: bool = Field(default=False, description="Enable A/B testing")
    ab_test_variant_b_pct: int = Field(default=20, description="Percentage of docs for variant B")
    ab_test_variant_b_model: str = Field(default="", description="Model version for variant B")

    # === Deduplication ===
    analysis_version: str = Field(default="rules.1.0.0", description="Current analysis version")
    reprocess_on_version_change: bool = Field(default=True, description="Reprocess all docs when version changes")
    content_hash_algorithm: str = Field(default="sha256", description="Hash algorithm for content dedup")
    
    # === Dead Letter Queue ===
    dlq_max_age_days: int = Field(default=30, description="Auto-archive DLQ items older than this")
    dlq_alert_threshold: int = Field(default=5, description="Alert when DLQ depth exceeds this")

    # === Monitoring ===
    ntfy_url: str = Field(default="", description="ntfy notification server URL")
    ntfy_topic: str = Field(default="action-queue", description="ntfy topic for alerts")
    alert_on_failure_rate: float = Field(default=0.10, description="Alert if failure rate exceeds this (0-1)")
    alert_on_no_run_hours: int = Field(default=36, description="Alert if no successful run in N hours")

    @property
    def monitor_tags(self) -> list[str]:
        return [t.strip() for t in self.tags_to_monitor.split(",")]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}
```

---

## 8. Database Migrations

SQL for new tables (SQLite-compatible, can run via Alembic or `init_db()`):

```sql
-- Migration: 002_advanced_features.sql
-- Adds tables for feedback loop, dead-letter queue, model metrics, and A/B testing

-- Dead Letter Queue
CREATE TABLE IF NOT EXISTS dead_letter_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER,
    run_id TEXT,
    error_type TEXT NOT NULL,  -- timeout, parse_error, api_error, ollama_error
    error_message TEXT NOT NULL,
    retry_count INTEGER DEFAULT 0,
    first_failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    resolution TEXT,  -- manual_retry, skipped, fixed
    context JSON
);
CREATE INDEX IF NOT EXISTS idx_dlq_document_id ON dead_letter_queue(document_id);
CREATE INDEX IF NOT EXISTS idx_dlq_resolved ON dead_letter_queue(resolved_at);

-- User Feedback / Corrections
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    predicted_action_type TEXT NOT NULL,
    predicted_confidence INTEGER NOT NULL,
    predicted_urgency TEXT,
    corrected_action_type TEXT,
    corrected_urgency TEXT,
    feedback_type TEXT NOT NULL,  -- confirmed, type_changed, dismissed_wrong, dismissed_duplicate, urgency_changed
    document_text_snippet TEXT,
    document_tags JSON,
    document_correspondent TEXT,
    model_version TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_feedback_action ON feedback(action_id);
CREATE INDEX IF NOT EXISTS idx_feedback_document ON feedback(document_id);
CREATE INDEX IF NOT EXISTS idx_feedback_model ON feedback(model_version);

-- Model Accuracy Tracking
CREATE TABLE IF NOT EXISTS model_accuracy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version TEXT NOT NULL,
    window_start TIMESTAMP NOT NULL,
    window_end TIMESTAMP NOT NULL,
    total_predictions INTEGER DEFAULT 0,
    correct_predictions INTEGER DEFAULT 0,
    accuracy REAL DEFAULT 0.0,
    category_accuracy JSON,  -- {"PAY": 0.92, "REVIEW": 0.75, ...}
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_accuracy_version ON model_accuracy(model_version);

-- A/B Test Results
CREATE TABLE IF NOT EXISTS ab_test_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id TEXT NOT NULL,
    document_id INTEGER NOT NULL,
    variant TEXT NOT NULL,  -- variant_a, variant_b
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(test_id, document_id)
);
CREATE INDEX IF NOT EXISTS idx_ab_test ON ab_test_assignments(test_id);

-- Scheduling / Run History
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    trigger_type TEXT NOT NULL,  -- cron, webhook, manual, n8n
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    status TEXT DEFAULT 'running',  -- running, success, failed, timeout
    documents_found INTEGER DEFAULT 0,
    documents_processed INTEGER DEFAULT 0,
    documents_skipped INTEGER DEFAULT 0,
    documents_failed INTEGER DEFAULT 0,
    duration_seconds REAL,
    error_message TEXT,
    analysis_version TEXT,
    retry_attempt INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON pipeline_runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_started ON pipeline_runs(started_at);

-- Add new columns to existing processing_history table
ALTER TABLE processing_history ADD COLUMN analysis_version TEXT;
ALTER TABLE processing_history ADD COLUMN config_hash TEXT;
ALTER TABLE processing_history ADD COLUMN reprocess_reason TEXT;

-- Add new columns to existing actions table
ALTER TABLE actions ADD COLUMN priority_score INTEGER DEFAULT 50;
ALTER TABLE actions ADD COLUMN risk_score INTEGER DEFAULT 0;
ALTER TABLE actions ADD COLUMN risk_signals JSON;
ALTER TABLE actions ADD COLUMN needs_review INTEGER DEFAULT 0;
ALTER TABLE actions ADD COLUMN ab_test_variant TEXT;
ALTER TABLE actions ADD COLUMN penalties JSON;
```

---

## 9. API Endpoints

New FastAPI routes (integrate with existing DI API):

```python
# In: src/doc_intelligence_hub/modules/action_queue/api.py

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

router = APIRouter(prefix="/api/action-queue", tags=["action-queue"])


# --- Scheduling Endpoints ---

class PipelineRunRequest(BaseModel):
    force: bool = False
    limit: int = 50
    tag_override: Optional[str] = None
    document_id: Optional[int] = None

class PipelineRunResponse(BaseModel):
    run_id: str
    status: str
    processed: int
    skipped: int
    failed: int
    no_action: int
    duration_seconds: float

@router.post("/run", response_model=PipelineRunResponse)
async def trigger_pipeline_run(request: PipelineRunRequest, token=Depends(verify_aq_token)):
    """Trigger a pipeline run. Called by n8n or manual."""
    ...

@router.get("/runs")
async def list_pipeline_runs(
    limit: int = Query(20, le=100),
    status: Optional[str] = None,
    token=Depends(verify_aq_token),
):
    """List recent pipeline runs."""
    ...

@router.get("/runs/{run_id}")
async def get_pipeline_run(run_id: str, token=Depends(verify_aq_token)):
    """Get details of a specific run."""
    ...


# --- Dead Letter Queue ---

class DLQEntry(BaseModel):
    id: int
    document_id: Optional[int]
    error_type: str
    error_message: str
    retry_count: int
    first_failed_at: datetime
    resolution: Optional[str]

@router.get("/dead-letter", response_model=list[DLQEntry])
async def list_dead_letter(
    resolved: bool = False,
    token=Depends(verify_aq_token),
):
    """List dead-letter queue entries."""
    ...

@router.post("/dead-letter")
async def add_to_dead_letter(entry: dict, token=Depends(verify_aq_token)):
    """Add a failed item to the DLQ (called by n8n error handler)."""
    ...

@router.post("/dead-letter/{entry_id}/resolve")
async def resolve_dlq_entry(
    entry_id: int,
    resolution: str = Query(..., regex="^(manual_retry|skipped|fixed)$"),
    token=Depends(verify_aq_token),
):
    """Resolve a DLQ entry."""
    ...


# --- Feedback ---

class FeedbackRequest(BaseModel):
    action_id: int
    feedback_type: str  # confirmed, type_changed, dismissed_wrong, urgency_changed
    corrected_action_type: Optional[str] = None
    corrected_urgency: Optional[str] = None

@router.post("/feedback")
async def submit_feedback(feedback: FeedbackRequest, token=Depends(verify_aq_token)):
    """Record user feedback/correction on an action."""
    ...

@router.get("/feedback/stats")
async def get_feedback_stats(
    days: int = Query(30, le=365),
    token=Depends(verify_aq_token),
):
    """Get feedback statistics (accuracy, category breakdown)."""
    ...


# --- Active Learning ---

@router.get("/review-queue")
async def get_review_queue(
    limit: int = Query(5, le=20),
    token=Depends(verify_aq_token),
):
    """Get actions needing human review (active learning candidates)."""
    ...


# --- Model Management ---

@router.get("/model/status")
async def get_model_status(token=Depends(verify_aq_token)):
    """Get current model version, accuracy metrics, and threshold settings."""
    ...

@router.post("/model/invalidate")
async def invalidate_cache(
    scope: str = Query("all", regex="^(all|low_confidence|failed)$"),
    reason: str = Query("manual"),
    token=Depends(verify_aq_token),
):
    """Invalidate processing cache to trigger re-analysis."""
    ...


# --- Health & Monitoring ---

@router.get("/health")
async def action_queue_health():
    """Detailed health check for the action queue subsystem."""
    ...

@router.get("/metrics")
async def get_metrics(token=Depends(verify_aq_token)):
    """Prometheus-compatible metrics endpoint."""
    ...
```

---

## 10. Phased Implementation Plan

### Phase A: MVP (Pure Rules, Cron) — 1-2 days

**Goal:** Scheduling, basic risk scoring, and dedup refinement running on systemd timer.

| Task | File(s) | Effort | Closes/Advances |
|------|---------|--------|-----------------|
| Add systemd timer + service files | `deploy/action-queue.{timer,service}` | 1h | Scheduling MVP |
| Add `priority_score` + `risk_score` columns to Action | `database.py`, migration SQL | 30m | Schema prep |
| Implement `calculate_risk_score()` | `risk_scorer.py` | 2h | Risk scoring |
| Implement `calculate_priority_score()` | `risk_scorer.py` | 1h | Priority sort |
| Add content hash to `ProcessingHistory` | `pipeline.py`, `database.py` | 1h | Dedup |
| Add `analysis_version` to processing flow | `pipeline.py`, `config.py` | 30m | Version tracking |
| Basic penalty detection keywords | `risk_scorer.py` | 1h | Late fee detection |
| Wire risk/priority into `_store_action()` | `pipeline.py` | 30m | Integration |
| Add `/api/action-queue/run` endpoint with bearer auth | `api.py`, `auth.py` | 1.5h | API for n8n |
| Add `/api/action-queue/health` endpoint | `api.py` | 30m | Monitoring |

**Dependencies:** None — builds on existing pipeline.  
**Estimated total:** 8-10 hours.  
**What ships:** Pipeline runs on schedule, actions have priority/risk scores, dedup uses content hashing.

### Phase B: Local AI Enhancement — 1 week

**Goal:** Ollama-powered intent detection and risk scoring for uncertain documents. Enhanced date extraction.

| Task | File(s) | Effort | Closes/Advances |
|------|---------|--------|-----------------|
| Implement `IntentDetector` class | `intent_detector.py` | 4h | Intent detection |
| Enhanced date extraction with `dateutil` | `date_extractor.py` | 2h | Due date accuracy |
| Integrate IntentDetector into pipeline (rules → Ollama escalation) | `pipeline.py` | 2h | Hybrid flow |
| Implement `PenaltyDetector` with amount parsing | `risk_scorer.py` | 2h | Penalty detection |
| Add urgency level enum and determination | `risk_scorer.py` | 1h | Urgency levels |
| Add recency decay to priority calculation | `risk_scorer.py` | 1h | Stale actions |
| Create n8n workflow JSON template | `deploy/n8n/action-queue-scan.json` | 2h | n8n integration |
| Implement retry logic with backoff | `scheduler.py` | 2h | Reliability |
| Add dead-letter queue table + API | `dead_letter.py`, `api.py` | 3h | Error recovery |
| Add `pipeline_runs` table for history | `database.py`, `api.py` | 2h | Observability |
| ntfy alerting on failure/DLQ thresholds | `alerting.py` | 2h | Monitoring |
| Wire webhook auth for n8n | `auth.py` | 1h | Security |

**Dependencies:** Phase A complete. Ollama running with phi3:mini.  
**Estimated total:** ~24 hours (1 focused week).  
**What ships:** Intelligent intent detection, risk scoring with penalties, n8n orchestration, error handling.

### Phase C: Cloud AI & Feedback Loop — 2-3 weeks

**Goal:** Azure escalation for hard cases, feedback loop capturing corrections, threshold auto-tuning.

| Task | File(s) | Effort | Closes/Advances |
|------|---------|--------|-----------------|
| Azure OpenAI client for escalation | `azure_escalator.py` | 4h | Cloud AI path |
| Confidence-based routing (rules → Ollama → Azure) | `pipeline.py` | 3h | Hybrid architecture |
| `feedback` table + FeedbackEntry model | `feedback.py`, `database.py` | 2h | Feedback storage |
| Feedback API endpoints (submit, stats) | `api.py` | 3h | MC integration |
| Hook feedback capture into action update flow | `api.py` | 2h | Automatic capture |
| `ModelAccuracyMetric` table + rolling accuracy calculation | `feedback.py` | 3h | Drift detection |
| Confidence threshold auto-adjustment algorithm | `feedback.py` | 3h | Self-tuning |
| Retrain trigger detection | `feedback.py` | 2h | When to adjust |
| Model versioning scheme implementation | `versioning.py` | 2h | Version tracking |
| Active learning: `select_for_active_learning()` | `active_learning.py` | 2h | Efficient feedback |
| Review queue API endpoint | `api.py` | 1h | MC review UI |
| Dedup: `check_reprocess_needed()` with all triggers | `dedup.py` | 3h | Smart reprocessing |
| Cache invalidation CLI command | `cli.py` | 1h | Operator tooling |
| Document update handler (metadata/content/tag changes) | `dedup.py` | 3h | Document lifecycle |
| Integration tests for full pipeline with feedback | `tests/` | 4h | Validation |

**Dependencies:** Phase B complete. Azure OpenAI resource provisioned.  
**Estimated total:** ~38 hours (2-3 weeks at homelab pace).  
**What ships:** Full feedback loop, Azure escalation, smart dedup, self-tuning thresholds.

### Phase D: Learning & Experimentation — Future

**Goal:** A/B testing, active learning at scale, drift detection, training data export.

| Task | File(s) | Effort | Closes/Advances |
|------|---------|--------|-----------------|
| A/B testing framework (assignment + evaluation) | `ab_testing.py` | 4h | Experimentation |
| A/B test management API | `api.py` | 2h | Test lifecycle |
| Training data export (JSONL format) | `training_export.py` | 3h | Model training |
| Fine-tune phi3:mini on accumulated feedback | — | 8h | Custom model |
| Accuracy drift detection + alerting | `drift_detector.py` | 3h | Quality monitoring |
| Prometheus metrics endpoint | `metrics.py` | 2h | Grafana dashboards |
| Per-category threshold adjustment | `feedback.py` | 3h | Granular tuning |
| Batch reprocessing with new model (controlled rollout) | `batch_reprocess.py` | 3h | Safe deployment |

**Dependencies:** Phase C complete. 200+ feedback entries accumulated.  
**Estimated total:** ~28 hours.  
**What ships:** Systematic experimentation, custom-trained models, drift monitoring.

---

## Integration Points with Existing Code

| Feature | Hooks Into | How |
|---------|-----------|-----|
| Risk/Priority scoring | `pipeline.py → _store_action()` | Calculate scores before DB write |
| Intent detection | `pipeline.py` (between fetch and store) | Replace/augment current Ollama prompt response parsing |
| Feedback capture | API layer (action update endpoint) | Record old vs. new values on any action mutation |
| Dedup refinement | `pipeline.py` (dedup check block, lines 122-138) | Replace simple `processed_ids` check with `check_reprocess_needed()` |
| Scheduling | New `scheduler.py` + `api.py` | Pipeline.run() called by scheduler with retry wrapper |
| Dead-letter | `pipeline.py` (failure path, line 214-219) | After max retries, write to DLQ instead of just logging |

---

*End of document. Each section is implementation-ready — pick up any section and code it directly.*
