# OCR Ollama Integration — Prompt Templates & Implementation

## Overview

Ollama serves two distinct roles in the OCR quality pipeline. It is **not** used as an OCR engine. It is used as an intelligent validator that reads text samples and provides a human-like judgment of readability — a signal that complements the purely statistical heuristic scorer.

**Model:** `phi3:mini` (3.8B parameter, ~2.2GB, fast CPU inference, excellent instruction following)  
**Alternative:** `llama3.2:3b` (similar size, slightly better reasoning but slower on CPU)

**Ollama endpoint assumption:** `http://ollama:11434` (standard homelab Docker Compose service name)

---

## Role 1 — Secondary Scorer for Borderline C-Grade Documents

### When Invoked

Only for documents where the heuristic score falls in the **40–70 range** (borderline C-grade). Documents below 40 (clearly F) or above 70 (clearly B/A) are not sent to Ollama — it would add latency with no decision impact.

### What It Resolves

The dictionary ratio signal can produce a mid-range score for two very different reasons:

1. **Legitimate low dict_ratio:** Document contains many proper nouns, account numbers, part codes, medical terms, legal Latin. The OCR is fine; the content is just unusual.
2. **Genuine OCR garbage:** The text contains fragments, misread characters, merged words. The OCR failed.

A human reading 500 characters can distinguish these instantly. `phi3:mini` can too.

### Prompt Template

```
You are an OCR quality evaluator. Your task is to assess whether the text below was 
accurately extracted from a scanned document, or whether it contains OCR errors.

Evaluate the following text sample from a scanned document and respond with ONLY a 
valid JSON object. Do not add any explanation, preamble, or text outside the JSON.

Criteria:
- "coherent": true if a human can read and understand the text, false if it is 
  largely unreadable or fragmented
- "has_artifacts": true if you see clear OCR artifacts such as: letters merged into 
  numbers (e.g. "l" confused with "1" or "I"), words run together without spaces, 
  symbols appearing in place of letters, fragmented partial words, or repeated 
  character noise
- "quality_score": integer from 1 to 10 where 1 = completely unreadable garbage, 
  5 = readable but with noticeable errors, 10 = perfectly clean OCR text

Text sample (up to 500 characters):
"""
{TEXT_SAMPLE}
"""

Respond with exactly this JSON structure:
{"coherent": <true|false>, "has_artifacts": <true|false>, "quality_score": <1-10>}
```

### Python Implementation

```python
import httpx
import json
import logging

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = "http://ollama:11434"
SCORER_MODEL = "phi3:mini"
SAMPLE_LENGTH = 500

SECONDARY_SCORER_PROMPT = '''You are an OCR quality evaluator. Your task is to assess whether the text below was 
accurately extracted from a scanned document, or whether it contains OCR errors.

Evaluate the following text sample from a scanned document and respond with ONLY a 
valid JSON object. Do not add any explanation, preamble, or text outside the JSON.

Criteria:
- "coherent": true if a human can read and understand the text, false if it is 
  largely unreadable or fragmented
- "has_artifacts": true if you see clear OCR artifacts such as: letters merged into 
  numbers (e.g. "l" confused with "1" or "I"), words run together without spaces, 
  symbols appearing in place of letters, fragmented partial words, or repeated 
  character noise
- "quality_score": integer from 1 to 10 where 1 = completely unreadable garbage, 
  5 = readable but with noticeable errors, 10 = perfectly clean OCR text

Text sample (up to 500 characters):
"""
{text_sample}
"""

Respond with exactly this JSON structure:
{{"coherent": true_or_false, "has_artifacts": true_or_false, "quality_score": 1_to_10}}'''


def score_text_with_ollama(text: str, timeout_seconds: int = 30) -> dict | None:
    """
    Use Ollama phi3:mini to assess OCR quality of a text sample.

    Args:
        text: The document content to evaluate (will be truncated to SAMPLE_LENGTH)
        timeout_seconds: HTTP timeout. CPU inference on phi3:mini ~5–15s.

    Returns:
        {"coherent": bool, "has_artifacts": bool, "quality_score": int}
        or None if Ollama is unavailable or response is unparseable.
    """
    sample = _select_representative_sample(text, SAMPLE_LENGTH)
    prompt = SECONDARY_SCORER_PROMPT.format(text_sample=sample)

    try:
        response = httpx.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": SCORER_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.0,     # deterministic output
                    "num_predict": 60,      # JSON response is short
                },
            },
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        raw_response = response.json().get("response", "")
        return _parse_json_response(raw_response, expected_keys=["coherent", "has_artifacts", "quality_score"])

    except httpx.TimeoutException:
        logger.warning("Ollama timeout during secondary scoring")
        return None
    except httpx.HTTPError as e:
        logger.warning("Ollama HTTP error during secondary scoring: %s", e)
        return None
    except Exception as e:
        logger.error("Unexpected Ollama error during secondary scoring: %s", e)
        return None


def _select_representative_sample(text: str, length: int) -> str:
    """
    Select a representative text sample. Prefers the middle of the document
    over the beginning, since page headers/footers can skew the sample.
    Falls back to the start if the text is short.
    """
    if len(text) <= length:
        return text
    # Take from ~20% into the document to skip headers
    start = max(0, int(len(text) * 0.2))
    return text[start:start + length]
```

---

## Role 2 — Before/After Comparison Validator

### When Invoked

After every successful OCR attempt (Tier 1 or Tier 2), when the heuristic comparison gate shows improvement (`score_new >= score_old + MIN_SCORE_DELTA`). Ollama provides a final human-language validation that the new text is genuinely more readable, catching edge cases where the scoring heuristic is fooled.

**Specific edge cases Ollama catches that heuristics miss:**

1. Tesseract correctly OCR'd a different (wrong) page due to a deskew error — new text scores well but is from the wrong content
2. Azure returned higher-confidence text but changed proper nouns (e.g., changed a person's name) — heuristics can't detect this
3. New OCR improved one section but degraded another — overall score went up, but specific passages got worse

### Prompt Template

```
You are comparing two versions of text extracted from the same scanned document.
Version A is the existing OCR text. Version B is newly generated OCR text.

Your job is to determine which version is more accurate and readable as a document.
Respond with ONLY a valid JSON object. No explanation outside the JSON.

Consider:
- Which version has fewer obvious OCR errors or artifacts?
- Which version reads more naturally as a real document?
- If both are similar quality, choose B (prefer the new version when equal).
- Only choose A if you are confident the original is meaningfully better.

VERSION A (existing OCR):
"""
{OLD_TEXT_SAMPLE}
"""

VERSION B (new OCR):
"""
{NEW_TEXT_SAMPLE}
"""

Respond with exactly this JSON structure:
{"better_version": "A" or "B", "confidence": "low" or "medium" or "high", "reason": "brief one-sentence reason"}
```

### Python Implementation

```python
COMPARATOR_PROMPT = '''You are comparing two versions of text extracted from the same scanned document.
Version A is the existing OCR text. Version B is newly generated OCR text.

Your job is to determine which version is more accurate and readable as a document.
Respond with ONLY a valid JSON object. No explanation outside the JSON.

Consider:
- Which version has fewer obvious OCR errors or artifacts?
- Which version reads more naturally as a real document?
- If both are similar quality, choose B (prefer the new version when equal).
- Only choose A if you are confident the original is meaningfully better.

VERSION A (existing OCR):
"""
{old_text_sample}
"""

VERSION B (new OCR):
"""
{new_text_sample}
"""

Respond with exactly this JSON structure:
{{"better_version": "A or B", "confidence": "low or medium or high", "reason": "brief one-sentence reason"}}'''

COMPARISON_SAMPLE_LENGTH = 750  # slightly longer to give Ollama more context


def compare_ocr_versions(
    old_text: str,
    new_text: str,
    timeout_seconds: int = 45,
) -> dict | None:
    """
    Use Ollama to compare two OCR text versions and determine which is better.

    Args:
        old_text: Existing OCR text from Paperless document.content
        new_text: New OCR text from Tesseract or Azure
        timeout_seconds: CPU inference with two text blocks takes longer

    Returns:
        {"better_version": "A" | "B", "confidence": "low" | "medium" | "high", "reason": str}
        or None if Ollama is unavailable.
    """
    old_sample = _select_representative_sample(old_text, COMPARISON_SAMPLE_LENGTH)
    new_sample = _select_representative_sample(new_text, COMPARISON_SAMPLE_LENGTH)

    prompt = COMPARATOR_PROMPT.format(
        old_text_sample=old_sample,
        new_text_sample=new_sample,
    )

    try:
        response = httpx.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": SCORER_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_predict": 120,   # reason field makes response slightly longer
                },
            },
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        raw_response = response.json().get("response", "")
        return _parse_json_response(raw_response, expected_keys=["better_version", "confidence", "reason"])

    except httpx.TimeoutException:
        logger.warning("Ollama timeout during OCR comparison — defaulting to numeric gate only")
        return None
    except httpx.HTTPError as e:
        logger.warning("Ollama HTTP error during OCR comparison: %s", e)
        return None
    except Exception as e:
        logger.error("Unexpected Ollama error during OCR comparison: %s", e)
        return None
```

---

## Shared Utilities

### JSON Response Parser

LLMs occasionally wrap JSON in markdown code fences or add a preamble despite instructions. This parser handles those cases gracefully:

```python
import re

def _parse_json_response(raw: str, expected_keys: list[str]) -> dict | None:
    """
    Extract and parse a JSON object from an LLM response string.
    Handles markdown code fences and leading/trailing whitespace.
    """
    # Strip markdown code fences if present
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()

    # Try direct parse first
    try:
        result = json.loads(cleaned)
        if all(k in result for k in expected_keys):
            return result
    except json.JSONDecodeError:
        pass

    # Try to extract JSON object substring
    match = re.search(r"\{[^{}]+\}", cleaned, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if all(k in result for k in expected_keys):
                return result
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse Ollama JSON response: %s", raw[:200])
    return None
```

### Ollama Health Check

```python
def is_ollama_available(timeout_seconds: int = 5) -> bool:
    """Check if Ollama is reachable and phi3:mini is loaded."""
    try:
        response = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=timeout_seconds)
        if response.status_code != 200:
            return False
        models = [m["name"] for m in response.json().get("models", [])]
        return any(SCORER_MODEL in m for m in models)
    except Exception:
        return False
```

---

## Decision Flow Integration

### Secondary Scoring Decision

```python
def should_queue_for_remediation(
    heuristic_score: float,
    ollama_available: bool,
) -> tuple[bool, str]:
    """
    Determine if a document should be queued for remediation.

    Returns:
        (should_queue: bool, reason: str)
    """
    if heuristic_score < 45:
        return True, "F-grade: automatic queue"

    if heuristic_score >= 70:
        return False, "B/A-grade: no remediation needed"

    # C-grade borderline: use Ollama if available
    if not ollama_available:
        # Conservative fallback without Ollama
        queue = heuristic_score < 55
        reason = f"Ollama unavailable; conservative threshold: {'queue' if queue else 'skip'}"
        return queue, reason

    ollama_result = score_text_with_ollama(text)

    if ollama_result is None:
        queue = heuristic_score < 55
        return queue, "Ollama returned no result; conservative fallback"

    ollama_bad = (
        not ollama_result.get("coherent", True)
        or ollama_result.get("has_artifacts", False)
        or ollama_result.get("quality_score", 10) <= 5
    )

    if ollama_bad:
        return True, f"Ollama confirmed quality issues (score={ollama_result.get('quality_score')})"
    else:
        return False, f"Ollama confirms text is acceptable (score={ollama_result.get('quality_score')})"
```

### Comparison Gate Integration

```python
def apply_comparison_gate(
    old_text: str,
    new_text: str,
    page_count: int,
    old_score: float,
    ollama_available: bool,
    min_score_delta: float = 5.0,
) -> dict:
    new_score = compute_ocr_quality_score(new_text, page_count)["score"]
    score_delta = new_score - old_score

    if score_delta < min_score_delta:
        return {
            "accept": False,
            "reason": f"Score delta {score_delta:.1f} below minimum {min_score_delta}",
            "ollama_comparison": None,
        }

    # Score improved — validate with Ollama if available
    if ollama_available:
        ollama_result = compare_ocr_versions(old_text, new_text)
        if ollama_result and ollama_result["better_version"] == "A" and ollama_result["confidence"] in ("medium", "high"):
            return {
                "accept": False,
                "reason": f"Ollama ({ollama_result['confidence']} confidence): original is better. {ollama_result['reason']}",
                "ollama_comparison": ollama_result,
            }
        return {
            "accept": True,
            "reason": f"Score +{score_delta:.1f} pts; Ollama: {ollama_result}",
            "ollama_comparison": ollama_result,
        }

    # Ollama unavailable — accept on numeric gate alone
    return {
        "accept": True,
        "reason": f"Score +{score_delta:.1f} pts (Ollama unavailable)",
        "ollama_comparison": None,
    }
```

---

## Performance Characteristics (CPU-Only)

| Operation | Model | Expected Latency (CPU) | Invocation frequency |
|-----------|-------|----------------------|----------------------|
| Secondary scoring | `phi3:mini` | 5–20 seconds | ~20% of all docs (C-grade only) |
| Before/after comparison | `phi3:mini` | 10–30 seconds | ~5% of all docs (post-OCR only) |

For a library of 3,000 documents on the weekly scan:
- ~600 expected C-grade docs → 600 Ollama calls × 15s avg = ~2.5 hours total
- But the weekly run runs overnight, so this is not a UX concern

**Optimization:** If Ollama is busy with other requests (e.g., other homelab LLM workloads), the scorer service retries once after a 60-second delay. If Ollama is still busy, it proceeds with heuristic-only decision (conservative fallback applies).

---

## Pulling the Model

Before first use, pull `phi3:mini` in Ollama:

```bash
docker exec ollama ollama pull phi3:mini
```

Verify:
```bash
docker exec ollama ollama list
# Should show: phi3:mini   latest   ...
```
