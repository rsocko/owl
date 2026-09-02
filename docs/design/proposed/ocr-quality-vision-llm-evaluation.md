---
title: "OCR Quality — Vision-Capable LLM Advisory Review Evaluation"
sidebar_label: OCR Vision Review Evaluation
sidebar_position: 5
status: proposed
created: 2026-09-02
revised: 2026-09-02
---

# OCR Quality — Vision-Capable LLM Advisory Review Evaluation

**Status:** Research/feasibility only. Not authorized for implementation.

This document evaluates issue
[#135](https://github.com/rsocko/owl/issues/135) (vision-capable LLM advisory
review against the source page image) so a future decision can be made with
real numbers instead of assumptions. It does not implement anything, and it
does not change the activation gate in #135: implementation remains blocked
until (1) #17's activation gate is met, (2) there is a specific, measured case
where #17's text-only review is insufficient, and (3) privacy/latency/cost
approval explicitly covers sending page images (not just text) to the chosen
provider.

## Relationship to #17 and #29

- [#29](https://github.com/rsocko/owl/issues/29) (deterministic scoring) and
  [#17](https://github.com/rsocko/owl/issues/17) (text-only advisory review,
  itself still deferred) are prerequisites, not alternatives. Both must exist
  and be calibrated before a vision reviewer's marginal value can even be
  measured.
- This document assumes #17's guardrails (advisory-only, schema-validated
  output, provenance, exposed disagreement, calibration-set evaluation) carry
  over unchanged to a vision reviewer, with privacy treated as strictly
  stricter because the payload is a page image rather than extracted text.

## 1. Provider options

### 1.1 Self-hosted / local

`docs/modules/ocr-quality/ocr-ollama-integration.md` and the technology-stack
docs for EOB matching and statement tracking establish the existing pattern
for this deployment: when an LLM is used at all, prefer a self-hosted model
(Ollama) over a cloud API, and OpenAI-style cloud APIs have been explicitly
**rejected** elsewhere in this codebase for data-sharing/policy reasons (see
`docs/modules/eob-matching/technology-stack.md`: "OpenAI API for document
analysis — Rejected due to data sharing policies"). The OCR secondary-review
work previously tried `phi3:mini` (Ollama) — a **text-only** small model — and
found it insufficient even for text comparison.

For a *vision* check specifically, self-hosted options exist but are weaker
than the cloud alternative in this weight class:

| Model | Hosting | Vision? | Notes |
|---|---|---|---|
| LLaVA / LLaVA-NeXT (7B–34B) | Ollama, local GPU | Yes | Open-weight; document/table/digit reading materially weaker than frontier cloud vision models, especially on dense financial tables and small digits |
| Qwen2-VL / Qwen2.5-VL (7B–72B) | Ollama/vLLM, local GPU | Yes | Best open-weight document/OCR-style vision performance available today; 72B needs a substantial GPU (~48GB+ VRAM) to run at usable latency, 7B is more realistic for existing hardware but noticeably worse on cluttered scans |
| Phi-3.5-vision / Phi-4-multimodal | Ollama, local GPU | Yes | Small enough to run on modest hardware; general-purpose, not tuned for financial-document table/digit reading |

None of these have been benchmarked in this repo against real EOB/bank
statement tables. The honest starting position is "plausible, unverified,"
not "known to work" — the same caveat #17's design already applies to
`phi3:mini` for text.

### 1.2 Cloud

Azure Document Intelligence is already an approved cloud vendor for this
deployment (`docs/modules/ocr-quality/ocr-quality-design.md`), which is the
piece of context that made #135 worth writing down rather than dismissing
outright. But approval of Azure DI for OCR is not the same approval as
sending page images to a *different* Azure service (Azure OpenAI). The two
have separate data-handling terms, separate abuse-monitoring/content-review
defaults, and (depending on tenant configuration) separate retention
behavior. This is exactly the distinction #135 calls out, and it must be
verified for the specific model deployment, not assumed:

| Model | Vision quality on documents | Notes |
|---|---|---|
| Azure OpenAI GPT-4o / GPT-4o-mini | Strong; good at tables, handwriting-adjacent digits, layout | Requires an Azure OpenAI resource with vision-enabled deployment in this tenant; not automatically covered by the existing Azure DI approval |
| Azure OpenAI GPT-4.1 / GPT-4.1-mini | Strong, generally better instruction-following for schema-constrained output than 4o | Same tenant/approval caveat |
| Azure AI Content Understanding / Document Intelligence "prebuilt-layout" + custom | Structure-aware but not a general vision-reasoning model | Doesn't answer "is that an 8 or a 3" style questions the way a multimodal chat model can |

**Realistic recommendation for a future pilot, if one is ever authorized:**
Azure OpenAI GPT-4o-mini (or whatever the then-current small vision-capable
Azure OpenAI deployment is) as the cloud option, evaluated *only* on a small
number of synthetic or explicitly-consented documents, compared against a
local Qwen2.5-VL 7B run for a same-tenant/no-image-egress alternative.
General-purpose "AI provider survey" beyond this is not useful here — the
deployment already has one approved cloud vendor family (Azure) and one
existing local-inference pattern (Ollama); the question is which specific
models within those two families are usable, not whether to add a third
ecosystem.

## 2. Privacy/policy analysis

Be concrete about what "send the image" means for this corpus. This is not a
document-metadata or OCR-text payload — it is a rendering of the actual page,
including everything visible on it:

- Bank statement page images: account numbers, routing numbers, balances,
  full transaction history, sometimes a legal name and mailing address, all
  in the same raster image as the "8 vs 3" digit the review is nominally
  checking.
- EOB (explanation of benefits) page images: patient name, member ID,
  provider name, procedure/diagnosis codes, and dollar amounts — all
  simultaneously present on the same page as any table-shape question being
  asked.
- There is no way to send "just the ambiguous digit" without also sending
  enough surrounding context (the row, or realistically the whole page) for
  the model to answer correctly, which means most of the practical prompt
  shapes below re-expose the entire page, not a redacted crop.

This is a materially different exposure than #17's text-only design, which at
least allows redaction/field-limiting before anything leaves the boundary.
Cropping to "just the region in question" (per #135's proposed scope) reduces
but does not eliminate this — a cropped table still contains real amounts and
often account/member identifiers in the same crop.

**Self-hosted vs. cloud tradeoff, stated plainly:**

| | Self-hosted (Ollama + Qwen2.5-VL/LLaVA) | Cloud (Azure OpenAI GPT-4o-family) |
|---|---|---|
| Data leaves trusted boundary | No | Yes — image bytes transit to Azure OpenAI |
| Vision/table/digit accuracy | Unverified, likely weaker on dense financial tables | Materially stronger, still not verified for this corpus specifically |
| Cost | Hardware/GPU time only | Per-call API cost (see §3) |
| Latency | Depends on local GPU; likely slower per call than a well-provisioned cloud endpoint unless GPU is dedicated | Network + inference latency, generally consistent |
| Existing approval | Consistent with prior "self-hosted only" policy stance for LLMs (EOB/statement-tracking docs) | Requires new, explicit approval scoped to *this* service and *this* payload type — not inherited from Azure DI approval |

**Recommendation:** if this is ever piloted, self-hosted vision models should
be the default assumption for anything touching real financial/medical
document images, with a cloud vision model treated as an explicitly-approved
exception for a narrow, opt-in, small-sample evaluation — not the default
path. This mirrors the existing "self-hosted only" stance already recorded
for LLM use elsewhere in this codebase, and treats the Azure DI precedent as
non-transferable to a different Azure product with a different data payload.

## 3. Cost/latency estimate (order of magnitude only)

These numbers are illustrative, not measured. They exist to demonstrate scale,
not to be relied on for a real budget. Any real pilot must measure actual
cost/latency on a handful of real calls before extrapolating.

Assumptions for the cloud case (Azure OpenAI GPT-4o-mini class pricing, rough
figures as of writing, subject to change and not verified against a live
tenant): approximately $0.15 / 1M input tokens, $0.60 / 1M output tokens; a
single page image at "high detail" costs roughly 1,000–1,200 input tokens;
add ~300–500 tokens for prompt scaffolding and the already-extracted
text/structure context; a schema-validated JSON response is roughly
150–300 output tokens.

Per-call estimate: ~1,500 input tokens + ~250 output tokens ≈
$0.00023 (input) + $0.00015 (output) ≈ **~$0.0004–0.001 per page-level call**
depending on prompt size and detail level. Latency per call is typically in
the low single-digit seconds for a vision-capable chat completion.

| Scope | Calls (1 region-of-interest check per flagged doc) | Rough cost | Rough wall-clock time (sequential, 1 call at a time) |
|---|---|---|---|
| Currently-flagged population (~112 REVIEW_RECOMMENDED/FAILED docs from the first full-corpus scan) | ~112–300 (allowing for multiple regions/pages per document) | roughly $0.05–$0.50 total | roughly 5–25 minutes sequential; low single-digit minutes with modest (5–10x) concurrency |
| Full corpus (~8,900 documents), if run unconditionally on every document | ~8,900–25,000+ calls (1+ region per doc) | roughly $4–$25+ total direct API cost — **cost is not the binding constraint here** | roughly 8–35+ hours sequential; still hours even with realistic concurrency limits, and re-run on every re-score would repeat this cost every time |

The headline conclusion is **not** "this is cheap so run it on everything." A
few-dollar API bill for 8,900 documents is easy to justify in isolation, but
running a vision model unconditionally on the full corpus reintroduces
exactly the problem #17 and #29 were designed to avoid: it treats a
probabilistic, fluency-prone model output as if it were free evidence, and it
multiplies both latency (a many-hour serialized run, or a large concurrent
burst against a shared Azure OpenAI quota) and privacy exposure (every page
image in the corpus transiting to a third party) for a check that is only
useful on the small subset of documents where deterministic signals already
disagree or are uncertain. This is precisely why #135 scopes it to "genuinely
uncertain or high-value cases," not universal application — the cost model
here reinforces that constraint rather than removing the need for it.

Self-hosted latency is harder to estimate without a specific GPU
allocation, but a 7B-class vision model on a single consumer/workstation GPU
is realistically 2–10x slower per call than the cloud estimate above,
without the offsetting benefit of infinite provider-side concurrency — so a
self-hosted full-corpus run would take at least as long, likely longer,
purely serialized on one GPU.

## 4. What a vision check would answer that #29 and #17 cannot

Restating #135's proposed scope with concrete input/output shapes:

1. **Table/structure-vs-image mismatch** — extracted table has N columns but
   the image shows N+1 (a column was merged or split during extraction).
2. **Digit ambiguity** — a specific low-confidence character position (e.g.
   Azure DI or Tesseract confidence below threshold on one character) that
   text-only comparison cannot resolve because both readings are lexically
   "plausible" (`8` vs `3`, `S` vs `5`, `1` vs `l`).
3. **Computed-total vs. visible-line-items mismatch** — a statement's stated
   total does not match the sum of the line items *as visually laid out*,
   which can differ from a naive text-order sum if columns were
   misattributed during extraction.
4. **Silent regions** — areas of the page with visible content (a stamp, a
   handwritten annotation, a small table in a margin) that produced no
   extracted text at all, which a text-only comparison cannot detect because
   there's no text to compare in the first place.

None of these are answerable from OCR text alone (#17's ceiling) or from
confidence/geometry heuristics alone (#29's ceiling) — they require actually
looking at the rendered page and reasoning about what's visually present.

### Example prompt shape (illustrative, not implementation)

```text
System: You are a document-quality checker. You will be shown one page image
and the text/structure that an OCR pipeline extracted from it. Answer only
the specific question asked. Do not summarize the document. Do not include
any information from the page in your answer beyond what is needed to answer
the question. If you cannot tell from the image, say so explicitly.

User:
[page image, cropped to the region under review when a crop is sufficient]
Extracted table (from Azure DI): { "rows": 6, "columns": 4, "cells": [...] }
Question: Does the number of columns and rows in the extracted table match
what is visible in the image? If not, describe the specific discrepancy
(e.g. "image has 5 columns, extraction has 4 — the 'Date' and 'Description'
columns appear merged").
```

### Example schema-validated response shape (illustrative, not implementation)

```json
{
  "review_type": "table_structure_mismatch",
  "model": "azure-openai/gpt-4o-mini-vision",
  "model_version": "2026-08-01",
  "match": false,
  "confidence": "medium",
  "discrepancy": "Image shows 5 columns; extraction shows 4. The 'Date' and 'Description' columns appear merged in the extracted table.",
  "region_reference": "page_3:table_1",
  "unable_to_determine": false,
  "raw_model_disagreement_with_deterministic_score": true
}
```

The response contract mirrors #17's: schema-validated, model/version
provenance recorded, explicit `unable_to_determine` rather than a forced
guess, and disagreement with the deterministic score surfaced rather than
silently resolved.

## 5. Recommendation

**Do not implement now.** None of the three gate conditions in #135 are met:

1. **#17's activation gate is not met.** #17 has not been built, so there is
   no baseline, no human calibration set, and no measured deterministic
   false-positive/false-negative rate to compare a vision check against.
2. **There is no specific, measured case yet where text-only review (#17) is
   insufficient**, because #17 does not exist yet to be found insufficient.
   This document's §4 describes *plausible* categories of vision-only value
   (table mismatch, digit ambiguity, computed-total mismatch, silent
   regions), but "plausible" is not "measured" — that requires #17 running
   against real uncertain documents and a log of cases where its text-only
   view provably could not resolve them.
3. **No privacy/latency/cost approval exists for sending page images
   specifically.** This document's §2–§3 give a plausible shape for that
   approval (self-hosted default, cloud as narrow opt-in exception, real
   cost/latency small compared to the exposure/complexity cost), but the
   approval itself has not been sought or granted.

**When to revisit:** after #17 ships and is calibrated (Phase 8 in
`docs/design/proposed/ocr-quality-implementation-plan.md`), re-open #135 and
check its log of disagreements/uncertain cases specifically for the four
categories in §4. If a meaningful population of documents exists where #17's
text-only view is provably insufficient (not just theoretically insufficient),
bring a scoped privacy/cost approval request back to the table using the
numbers in this document as a starting point — re-measured against whatever
Azure OpenAI/self-hosted pricing and model lineup exists at that time, since
both change frequently.

## References

- Issue [#135](https://github.com/rsocko/owl/issues/135) — this evaluation
- Issue [#17](https://github.com/rsocko/owl/issues/17) — text-only advisory
  review (prerequisite, still deferred)
- [Secondary review design](../../modules/ocr-quality/ocr-ollama-integration.md)
- [OCR Quality Design](../../modules/ocr-quality/ocr-quality-design.md)
- [Baseline Inventory](../../modules/ocr-quality/ocr-baseline-inventory.md)
- [Implementation Plan](./ocr-quality-implementation-plan.md)
- `docs/modules/eob-matching/technology-stack.md` — prior "self-hosted only"
  LLM policy stance in this codebase
