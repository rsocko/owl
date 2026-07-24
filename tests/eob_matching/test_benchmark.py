"""Tests for the model benchmark module."""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doc_intelligence_hub.modules.eob_matching.benchmark import (
    ExtractionResult,
    ModelBenchmarkSummary,
    _estimate_cost,
    _summarize_fields,
    benchmark_to_json,
    format_benchmark_table,
    run_benchmark,
    run_single_extraction,
)


# ---------------------------------------------------------------------------
# Cost estimation tests
# ---------------------------------------------------------------------------


class TestEstimateCost:
    def test_azure_model_has_cost(self):
        cost = _estimate_cost("gpt-4o-mini", 10)
        assert cost is not None
        assert cost > 0

    def test_gpt4o_more_expensive_than_mini(self):
        cost_mini = _estimate_cost("gpt-4o-mini", 10)
        cost_full = _estimate_cost("gpt-4o", 10)
        assert cost_full > cost_mini

    def test_ollama_model_returns_none(self):
        assert _estimate_cost("phi3:mini", 10) is None
        assert _estimate_cost("llama3.1:8b", 10) is None
        assert _estimate_cost("mistral-nemo:latest", 10) is None

    def test_zero_docs_zero_cost(self):
        cost = _estimate_cost("gpt-4o-mini", 0)
        assert cost == 0.0


# ---------------------------------------------------------------------------
# Field summarization tests
# ---------------------------------------------------------------------------


class TestSummarizeFields:
    def test_extracts_key_fields(self):
        data = {
            "provider_name": "City Medical",
            "patient_name": "John Doe",
            "date_of_service": "2024-01-15",
            "total_billed": 500.00,
            "total_patient_responsibility": 36.00,
            "services": [{"description": "Office Visit"}],
            "extra_field": "ignored",
        }
        result = _summarize_fields(data)
        assert result["provider_name"] == "City Medical"
        assert result["patient_name"] == "John Doe"
        assert result["services_count"] == 1
        assert "extra_field" not in result

    def test_handles_missing_fields(self):
        result = _summarize_fields({})
        assert result["provider_name"] is None
        assert result["services_count"] == 0


# ---------------------------------------------------------------------------
# Single extraction tests
# ---------------------------------------------------------------------------


class TestRunSingleExtraction:
    @pytest.mark.asyncio
    async def test_successful_extraction(self):
        mock_response = {
            "provider_name": "City Medical Center",
            "patient_name": "John Doe",
            "date_of_service": "2024-01-15",
            "total_billed": 500.00,
            "total_patient_responsibility": 36.00,
            "services": [{"description": "Office Visit", "billed_amount": 500.00}],
            "insurance_company": "Blue Cross",
            "policy_number": "ABC123",
            "claim_number": "CLM456",
            "total_allowed": 450.00,
            "total_plan_pays": 414.00,
        }

        with patch(
            "doc_intelligence_hub.modules.eob_matching.benchmark.chat_json",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await run_single_extraction(
                text="Sample EOB text",
                document_id="doc-1",
                model="test-model",
            )

        assert result.success is True
        assert result.model == "test-model"
        assert result.document_id == "doc-1"
        assert result.elapsed_seconds > 0
        assert result.confidence > 0
        assert result.extracted_fields["provider_name"] == "City Medical Center"

    @pytest.mark.asyncio
    async def test_llm_returns_none(self):
        with patch(
            "doc_intelligence_hub.modules.eob_matching.benchmark.chat_json",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await run_single_extraction(
                text="Sample text",
                document_id="doc-2",
                model="bad-model",
            )

        assert result.success is False
        assert "no response" in result.error.lower()

    @pytest.mark.asyncio
    async def test_validation_failure(self):
        # Missing provider_name triggers validation failure
        mock_response = {
            "provider_name": None,
            "patient_name": "John Doe",
            "date_of_service": "2024-01-15",
            "total_billed": 500.00,
            "services": [],
        }

        with patch(
            "doc_intelligence_hub.modules.eob_matching.benchmark.chat_json",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await run_single_extraction(
                text="Sample text",
                document_id="doc-3",
                model="test-model",
            )

        assert result.success is False
        assert result.validation_error is not None
        assert "provider_name" in result.validation_error

    @pytest.mark.asyncio
    async def test_exception_handling(self):
        with patch(
            "doc_intelligence_hub.modules.eob_matching.benchmark.chat_json",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Connection timeout"),
        ):
            result = await run_single_extraction(
                text="Sample text",
                document_id="doc-4",
                model="test-model",
            )

        assert result.success is False
        assert "Connection timeout" in result.error


# ---------------------------------------------------------------------------
# Run benchmark tests
# ---------------------------------------------------------------------------


class TestRunBenchmark:
    @pytest.mark.asyncio
    async def test_runs_all_models_against_all_docs(self):
        docs = [
            {"id": "1", "content": "EOB doc 1", "title": "Doc 1"},
            {"id": "2", "content": "EOB doc 2", "title": "Doc 2"},
        ]
        models = ["model-a", "model-b"]

        mock_response = {
            "provider_name": "City Medical",
            "patient_name": "John Doe",
            "date_of_service": "2024-03-01",
            "total_billed": 200.00,
            "total_patient_responsibility": 50.00,
            "services": [],
            "insurance_company": None,
            "policy_number": None,
            "claim_number": None,
            "total_allowed": None,
            "total_plan_pays": None,
        }

        with patch(
            "doc_intelligence_hub.modules.eob_matching.benchmark.chat_json",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            summaries = await run_benchmark(docs, models)

        assert len(summaries) == 2
        assert summaries[0].model == "model-a"
        assert summaries[1].model == "model-b"
        assert summaries[0].documents_tested == 2
        assert len(summaries[0].results) == 2

    @pytest.mark.asyncio
    async def test_empty_documents(self):
        summaries = await run_benchmark([], ["model-a"])
        assert len(summaries) == 1
        assert summaries[0].documents_tested == 0
        assert summaries[0].avg_time_seconds == 0


# ---------------------------------------------------------------------------
# Output format tests
# ---------------------------------------------------------------------------


class TestBenchmarkToJson:
    def test_serializes_correctly(self):
        summary = ModelBenchmarkSummary(
            model="phi3:mini",
            documents_tested=5,
            avg_time_seconds=2.5,
            success_rate=0.8,
            avg_confidence=0.75,
            total_time_seconds=12.5,
            estimated_cost_usd=None,
            sample_fields={"provider_name": "Test"},
            results=[
                ExtractionResult(
                    model="phi3:mini",
                    document_id="doc-1",
                    success=True,
                    elapsed_seconds=2.1,
                    confidence=0.85,
                ),
            ],
        )

        output = benchmark_to_json([summary])
        assert len(output) == 1
        assert output[0]["model"] == "phi3:mini"
        assert output[0]["avg_time_seconds"] == 2.5
        assert output[0]["success_rate"] == 0.8
        assert output[0]["estimated_cost_usd"] is None
        assert len(output[0]["results"]) == 1
        assert output[0]["results"][0]["success"] is True


class TestFormatBenchmarkTable:
    def test_produces_readable_output(self):
        summary = ModelBenchmarkSummary(
            model="gpt-4o-mini",
            documents_tested=5,
            avg_time_seconds=1.5,
            success_rate=0.9,
            avg_confidence=0.82,
            total_time_seconds=7.5,
            estimated_cost_usd=0.000525,
        )

        table = format_benchmark_table([summary])
        assert "gpt-4o-mini" in table
        assert "1.50" in table
        assert "$0.000525" in table
