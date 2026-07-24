"""Model comparison benchmark for the EOB extraction pipeline.

Compares LLM models for speed and accuracy on real EOB document extraction.
Tests local Ollama models vs Azure OpenAI models via Bifrost gateway.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from doc_intelligence_hub.core.llm import chat_json, get_llm_client, get_llm_settings
from doc_intelligence_hub.core.paperless import PaperlessClient
from doc_intelligence_hub.modules.eob_matching.llm_extractor import (
    _EOB_PROMPT,
    _EOB_SYSTEM,
    _truncate,
    compute_eob_confidence,
    validate_eob_extraction,
)
from doc_intelligence_hub.modules.eob_matching.models import ExtractedEOB, ServiceLine

logger = logging.getLogger(__name__)

# Known Azure models for cost estimation (per 1M tokens, approximate)
_AZURE_COST_PER_1M_TOKENS: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
}

# Average tokens per EOB extraction (rough estimate for cost calculation)
_AVG_INPUT_TOKENS_PER_DOC = 1500
_AVG_OUTPUT_TOKENS_PER_DOC = 500

DEFAULT_MODELS = [
    "phi3:mini",
    "llama3.1:8b",
    "mistral-nemo:latest",
    "qwen2.5:7b-instruct",
    "gpt-4o-mini",
    "gpt-4o",
]


@dataclass
class ExtractionResult:
    """Result of a single model × document extraction attempt."""

    model: str
    document_id: str
    success: bool
    elapsed_seconds: float
    confidence: float = 0.0
    validation_error: str | None = None
    extracted_fields: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class ModelBenchmarkSummary:
    """Aggregated benchmark results for one model."""

    model: str
    documents_tested: int
    avg_time_seconds: float
    success_rate: float
    avg_confidence: float
    total_time_seconds: float
    estimated_cost_usd: float | None = None
    sample_fields: dict[str, Any] = field(default_factory=dict)
    results: list[ExtractionResult] = field(default_factory=list)


async def fetch_eob_documents(
    paperless_url: str,
    paperless_token: str,
    *,
    limit: int = 5,
    tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch EOB documents from Paperless for benchmarking.

    Returns list of dicts with 'id' and 'content' keys.
    """
    if tags is None:
        tags = ["medical"]

    client = PaperlessClient(
        base_url=paperless_url,
        token=paperless_token,
    )

    documents = await client.list_documents(tags=tags, page_size=min(limit, 100))
    documents = documents[:limit]

    results = []
    for doc in documents:
        doc_id = str(doc.get("id", ""))
        content = doc.get("content", "")
        if not content:
            content = await client.get_document_content(int(doc_id))
        if content:
            results.append({"id": doc_id, "content": content, "title": doc.get("title", "")})

    return results


async def run_single_extraction(
    text: str,
    document_id: str,
    model: str,
) -> ExtractionResult:
    """Run extraction with a specific model and measure performance."""
    start = time.perf_counter()

    try:
        prompt = _EOB_PROMPT.format(text=_truncate(text))
        data = await chat_json(prompt, system=_EOB_SYSTEM, model=model, max_tokens=1536)
        elapsed = time.perf_counter() - start

        if data is None:
            return ExtractionResult(
                model=model,
                document_id=document_id,
                success=False,
                elapsed_seconds=elapsed,
                error="LLM returned no response",
            )

        # Build EOB from response (same logic as llm_extractor)
        services = []
        for svc in data.get("services") or []:
            services.append(ServiceLine(
                description=svc.get("description", "Service"),
                cpt_code=svc.get("cpt_code"),
                billed_amount=_safe_float(svc.get("billed_amount")),
                allowed_amount=_safe_float(svc.get("allowed_amount")),
                plan_pays=_safe_float(svc.get("plan_pays")),
                patient_responsibility=_safe_float(svc.get("patient_responsibility")),
            ))

        eob = ExtractedEOB(
            insurance_company=data.get("insurance_company"),
            policy_number=data.get("policy_number"),
            patient_name=data.get("patient_name"),
            claim_number=data.get("claim_number"),
            date_of_service=_safe_date(data.get("date_of_service")),
            provider_name=_safe_provider_name(data.get("provider_name")),
            services=services,
            total_billed=_safe_float(data.get("total_billed")),
            total_allowed=_safe_float(data.get("total_allowed")),
            total_plan_pays=_safe_float(data.get("total_plan_pays")),
            total_patient_responsibility=_safe_float(data.get("total_patient_responsibility")),
            document_id=document_id,
            extraction_confidence=0.0,
        )

        validation_error = validate_eob_extraction(eob)
        if validation_error:
            return ExtractionResult(
                model=model,
                document_id=document_id,
                success=False,
                elapsed_seconds=elapsed,
                validation_error=validation_error,
                extracted_fields=_summarize_fields(data),
            )

        eob.extraction_confidence = compute_eob_confidence(eob)

        return ExtractionResult(
            model=model,
            document_id=document_id,
            success=True,
            elapsed_seconds=elapsed,
            confidence=eob.extraction_confidence,
            extracted_fields=_summarize_fields(data),
        )

    except Exception as e:
        elapsed = time.perf_counter() - start
        return ExtractionResult(
            model=model,
            document_id=document_id,
            success=False,
            elapsed_seconds=elapsed,
            error=str(e),
        )


async def run_benchmark(
    documents: list[dict[str, Any]],
    models: list[str],
) -> list[ModelBenchmarkSummary]:
    """Run benchmark across all models and documents.

    Runs models sequentially (to avoid interference), documents sequentially per model.
    """
    summaries: list[ModelBenchmarkSummary] = []

    for model in models:
        results: list[ExtractionResult] = []
        for doc in documents:
            result = await run_single_extraction(
                text=doc["content"],
                document_id=doc["id"],
                model=model,
            )
            results.append(result)
            logger.info(
                "Benchmark %s × doc %s: %.1fs, success=%s, confidence=%.2f",
                model, doc["id"], result.elapsed_seconds, result.success, result.confidence,
            )

        # Aggregate
        total_time = sum(r.elapsed_seconds for r in results)
        successes = [r for r in results if r.success]
        avg_time = total_time / len(results) if results else 0
        success_rate = len(successes) / len(results) if results else 0
        avg_confidence = (
            sum(r.confidence for r in successes) / len(successes) if successes else 0
        )

        # Cost estimate for Azure models
        cost = _estimate_cost(model, len(documents))

        # Sample fields from first successful extraction
        sample_fields = successes[0].extracted_fields if successes else {}

        summaries.append(ModelBenchmarkSummary(
            model=model,
            documents_tested=len(documents),
            avg_time_seconds=round(avg_time, 2),
            success_rate=round(success_rate, 4),
            avg_confidence=round(avg_confidence, 3),
            total_time_seconds=round(total_time, 2),
            estimated_cost_usd=cost,
            sample_fields=sample_fields,
            results=results,
        ))

    return summaries


def _estimate_cost(model: str, num_docs: int) -> float | None:
    """Estimate cost for Azure models based on token counts."""
    pricing = _AZURE_COST_PER_1M_TOKENS.get(model)
    if pricing is None:
        return None  # Local models are free

    input_cost = (num_docs * _AVG_INPUT_TOKENS_PER_DOC / 1_000_000) * pricing["input"]
    output_cost = (num_docs * _AVG_OUTPUT_TOKENS_PER_DOC / 1_000_000) * pricing["output"]
    return round(input_cost + output_cost, 6)


def _summarize_fields(data: dict) -> dict[str, Any]:
    """Extract key fields for display in results."""
    return {
        "provider_name": data.get("provider_name"),
        "patient_name": data.get("patient_name"),
        "date_of_service": data.get("date_of_service"),
        "total_billed": data.get("total_billed"),
        "total_patient_responsibility": data.get("total_patient_responsibility"),
        "services_count": len(data.get("services") or []),
    }


def format_benchmark_table(summaries: list[ModelBenchmarkSummary]) -> str:
    """Format benchmark results as a text table for CLI output."""
    lines = []
    header = (
        f"{'Model':<25} {'Avg Time (s)':<14} {'Success %':<12} "
        f"{'Avg Confidence':<16} {'Cost (USD)':<12}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    for s in summaries:
        cost_str = f"${s.estimated_cost_usd:.6f}" if s.estimated_cost_usd is not None else "free"
        lines.append(
            f"{s.model:<25} {s.avg_time_seconds:<14.2f} {s.success_rate * 100:<12.1f} "
            f"{s.avg_confidence:<16.3f} {cost_str:<12}"
        )

    return "\n".join(lines)


def benchmark_to_json(summaries: list[ModelBenchmarkSummary]) -> list[dict[str, Any]]:
    """Convert benchmark summaries to JSON-serializable dicts."""
    output = []
    for s in summaries:
        output.append({
            "model": s.model,
            "documents_tested": s.documents_tested,
            "avg_time_seconds": s.avg_time_seconds,
            "success_rate": s.success_rate,
            "avg_confidence": s.avg_confidence,
            "total_time_seconds": s.total_time_seconds,
            "estimated_cost_usd": s.estimated_cost_usd,
            "sample_fields": s.sample_fields,
            "results": [
                {
                    "document_id": r.document_id,
                    "success": r.success,
                    "elapsed_seconds": round(r.elapsed_seconds, 2),
                    "confidence": r.confidence,
                    "validation_error": r.validation_error,
                    "error": r.error,
                    "extracted_fields": r.extracted_fields,
                }
                for r in s.results
            ],
        })
    return output


# ---------------------------------------------------------------------------
# Internal helpers (duplicated from llm_extractor to avoid circular deps)
# ---------------------------------------------------------------------------


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    if isinstance(value, str):
        from doc_intelligence_hub.modules.eob_matching.extractor import parse_amount
        return parse_amount(value)
    return None


def _safe_date(value: Any):
    if value is None:
        return None
    if isinstance(value, str):
        from doc_intelligence_hub.modules.eob_matching.extractor import parse_date
        return parse_date(value)
    return None


def _safe_provider_name(value: Any) -> str | None:
    if not value or not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned.split()) > 8:
        return None
    return cleaned
