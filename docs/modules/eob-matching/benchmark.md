---
title: "EOB Matching Benchmark"
sidebar_label: Benchmark
sidebar_position: 4
---

# EOB Extraction — LLM Model Benchmark

## Benchmark Results (July 2026)

### Summary

We benchmarked 6 LLM models for EOB (Explanation of Benefits) document extraction — 4 local Ollama models and 2 Azure OpenAI models routed through Bifrost.

**Winner: `gpt-4o-mini`** — best balance of speed, accuracy, and cost.

### Results Table

| Model | Type | Avg Time/Doc | Success Rate | Confidence | Est. Cost (5 docs) | Viable? |
|-------|------|-------------|-------------|------------|-------------------|---------|
| **gpt-4o-mini** | Azure | **3.06s** | **100%** | **1.0** | **$0.003** | ✅ **Production pick** |
| gpt-4o | Azure | 1.90s | 100% | 1.0 | $0.044 | ✅ Faster but 17× more expensive |
| phi3:mini | Ollama (CPU) | >300s | — | — | free | ❌ Unusably slow on CPU |
| llama3.1:8b | Ollama (CPU) | >300s | — | — | free | ❌ Unusably slow on CPU |
| mistral-nemo:latest | Ollama (CPU) | >300s | — | — | free | ❌ Timed out |
| qwen2.5:7b-instruct | Ollama (CPU) | >300s | — | — | free | ❌ Timed out |

### Cost Projection

| Scenario | gpt-4o-mini | gpt-4o |
|----------|-------------|--------|
| 5 documents | $0.003 | $0.044 |
| 100 documents | $0.053 | $0.875 |
| 467 documents (all EOBs) | $0.245 | $4.09 |
| Monthly new EOBs (~20) | $0.011 | $0.175 |

**Token pricing (per 1M tokens, approximate):**
- `gpt-4o-mini`: $0.15 input / $0.60 output
- `gpt-4o`: $2.50 input / $10.00 output

### Key Findings

1. **gpt-4o-mini is the clear production choice.** 100% success, 1.0 confidence, ~3s/doc, negligible cost (~$0.01/month for typical volume).

2. **gpt-4o is faster (1.9s vs 3.1s) but 17× more expensive.** Not worth it unless extraction quality degrades on harder documents — both scored identically on the test set.

3. **Ollama models on CPU are not viable for EOB extraction.** >300s per document makes them impractical for any workload. The extraction prompt requires structured JSON output with multiple fields, which is computationally intensive for small models on CPU.

4. **Ollama could be viable with GPU.** The models themselves are capable — they're just starved for compute. If a local GPU server is added (e.g., RTX 3090/4090), re-run the benchmark to see if response times drop to <10s/doc.

### Decision

- **Default model changed from `phi3:mini` → `gpt-4o-mini`** across the entire pipeline (LLM client, docker-compose, deployment env).
- All modules using `chat_json()` or `chat_completion()` automatically use the new default.
- Override per-request with the `model` parameter if needed.

---

## Running Benchmarks

### When to Re-Run

- New local model server with GPU hardware
- New model available via Bifrost (e.g., `gpt-4.1-nano`)
- After prompt changes to the extraction templates
- Quarterly review of Azure pricing changes

### Via API

```bash
# Quick test — 2 models, 5 recent docs
curl -X POST https://service-002.example.invalid/api/eob/benchmark \
  -H "Content-Type: application/json" \
  -d '{
    "models": ["gpt-4o-mini", "gpt-4o"],
    "limit": 5
  }'

# Full benchmark with date range (avoid processing years of history)
curl -X POST https://service-002.example.invalid/api/eob/benchmark \
  -H "Content-Type: application/json" \
  -d '{
    "models": ["gpt-4o-mini", "gpt-4o", "llama3.1:8b"],
    "limit": 10,
    "created_after": "2026-01-01",
    "created_before": "2026-07-31"
  }'

# Test a new local model after adding GPU
curl -X POST https://service-002.example.invalid/api/eob/benchmark \
  -H "Content-Type: application/json" \
  -d '{
    "models": ["gpt-4o-mini", "llama3.1:8b", "mistral-nemo:latest"],
    "limit": 5,
    "created_after": "2026-06-01"
  }'
```

### Via CLI

```bash
# Basic benchmark
eob-match benchmark \
  --models gpt-4o-mini,gpt-4o \
  --limit 5

# With date range filter
eob-match benchmark \
  --models gpt-4o-mini,llama3.1:8b \
  --created-after 2026-01-01 \
  --created-before 2026-07-31 \
  --limit 10

# Save results to file
eob-match benchmark \
  --models gpt-4o-mini,gpt-4o,llama3.1:8b \
  --limit 5 \
  --output benchmark-results.json

# Custom Bifrost URL (e.g., testing against local gateway)
eob-match benchmark \
  --models gpt-4o-mini \
  --bifrost-url http://bifrost:8080/openai/v1 \
  --limit 3
```

### Document Selection Controls

The benchmark pulls real documents from Paperless with these filters:

| Parameter | CLI Flag | API Field | Description |
|-----------|----------|-----------|-------------|
| Document type | `--document-type` | `document_type` | Paperless document type (default: `EOB - Explanation of Benefits`) |
| Tags | `--tag` (repeatable) | `tags` | Paperless tag names |
| Date from | `--created-after` | `created_after` | Only docs created on/after (YYYY-MM-DD) |
| Date to | `--created-before` | `created_before` | Only docs created on/before (YYYY-MM-DD) |
| Count | `--limit` | `limit` | Max documents per model (default: 5, max: 50) |

**Tip:** Use `--created-after` to test against recent documents only. There are 467 EOBs spanning years — you don't need to process all of them for a benchmark.

### Output Fields

Each model result includes:

| Field | Description |
|-------|-------------|
| `avg_time_seconds` | Average wall-clock time per document extraction |
| `success_rate` | Fraction passing validation (provider, date, amounts) |
| `avg_confidence` | Mean confidence score (0.0–1.0) based on field completeness |
| `estimated_cost_usd` | Estimated Azure API cost (null for local models) |
| `sample_fields` | Example extracted fields from first successful doc |
| `results[]` | Per-document detail: timing, success, errors, fields |

### Architecture

```
CLI / API
  │
  ├─ fetch_eob_documents()     ← Pulls from Paperless with filters
  │     └─ PaperlessClient.list_documents(document_type=..., created_after=...)
  │
  ├─ run_benchmark()           ← Iterates models × documents
  │     └─ run_single_extraction()
  │           └─ chat_json(model=<model>)   ← Routes through Bifrost
  │                 └─ Bifrost → Ollama / Azure OpenAI
  │
  └─ Results: timing, validation, confidence, cost
```

All models are accessed through a single Bifrost gateway (`https://service-001.example.invalid/openai/v1`). Bifrost routes to Ollama or Azure based on model name via CEL rules — no code changes needed to add new models.
