"""Shared client for OpenAI-compatible LLM endpoints.

All modules should use `get_llm_client()` for LLM calls.

Environment variables:
    LLM_BASE_URL: Endpoint URL (default: local Ollama OpenAI compatibility API)
    LLM_API_KEY: Provider API key (the local default is non-secret)
    LLM_MODEL: Model identifier (default: phi3:mini)
    LLM_TIMEOUT: Request timeout in seconds (default: 120)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, RateLimitError
from pydantic_settings import BaseSettings, SettingsConfigDict

from doc_intelligence_hub.core.resilience import (
    CircuitOpenError,
    LLMError,
    get_circuit_breaker,
    retry_async,
)

logger = logging.getLogger(__name__)


class LLMSettings(BaseSettings):
    """LLM configuration — reads from environment with LLM_ prefix."""

    base_url: str = "http://localhost:11434/v1"
    api_key: str = "local-development"
    model: str = "phi3:mini"
    timeout: float = 120.0
    temperature: float = 0.1

    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


_settings: LLMSettings | None = None
_client: AsyncOpenAI | None = None


def get_llm_settings() -> LLMSettings:
    """Get or create the singleton LLM settings."""
    global _settings
    if _settings is None:
        _settings = LLMSettings()
    return _settings


def get_llm_client(settings: LLMSettings | None = None) -> AsyncOpenAI:
    """Get or create the singleton async OpenAI client."""
    global _client, _settings
    if settings is not None:
        _settings = settings
        _client = None  # Force re-creation with new settings
    if _client is None:
        s = get_llm_settings()
        _client = AsyncOpenAI(
            base_url=s.base_url,
            api_key=s.api_key,
            timeout=s.timeout,
        )
    return _client


def reset_llm_client() -> None:
    """Reset the singleton client (for testing or config reload)."""
    global _client, _settings
    _client = None
    _settings = None


async def chat_completion(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int = 2048,
    response_format: dict[str, str] | None = None,
) -> str | None:
    """High-level helper: send a chat completion and return the text response.

    Args:
        prompt: User message content.
        system: Optional system message.
        model: Override the default model for this call.
        temperature: Override the default temperature.
        max_tokens: Max tokens in response.
        response_format: Optional {"type": "json_object"} for JSON mode.

    Returns:
        The assistant's response text, or None if the call failed.
    """
    client = get_llm_client()
    settings = get_llm_settings()

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs: dict[str, Any] = {
        "model": model or settings.model,
        "messages": messages,
        "temperature": temperature if temperature is not None else settings.temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format

    try:
        breaker = get_circuit_breaker("llm", failure_threshold=5, recovery_timeout=60.0)
        if not breaker.allow_request():
            raise CircuitOpenError(breaker)
        response = await _llm_call_with_retry(client, **kwargs)
        breaker.record_success()
        return response.choices[0].message.content
    except CircuitOpenError:
        logger.warning("LLM circuit breaker is open — skipping call")
        return None
    except (APIConnectionError, APITimeoutError) as e:
        breaker = get_circuit_breaker("llm")
        breaker.record_failure()
        logger.warning("LLM call failed (transient): %s", e)
        return None
    except RateLimitError as e:
        breaker = get_circuit_breaker("llm")
        breaker.record_failure()
        logger.warning("LLM rate limited: %s", e)
        return None
    except Exception as e:
        logger.error("LLM call failed (non-transient): %s", e)
        return None


@retry_async(max_attempts=3, base_delay=2.0, max_delay=30.0)
async def _llm_call_with_retry(client: AsyncOpenAI, **kwargs: Any) -> Any:
    """Execute LLM call with automatic retry on transient failures."""
    try:
        return await client.chat.completions.create(**kwargs)
    except (APIConnectionError, APITimeoutError, RateLimitError) as e:
        raise LLMError(str(e)) from e


async def chat_json(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int = 2048,
) -> dict | None:
    """Chat completion that parses JSON from the response.

    Tries JSON mode first, falls back to extracting JSON from freeform text.
    """
    text = await chat_completion(
        prompt,
        system=system,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    if text is None:
        return None
    return _parse_json_response(text)


def _parse_json_response(text: str) -> dict | None:
    """Parse JSON from LLM response, handling common issues like markdown fences."""
    text = text.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        text = text.removesuffix("```")
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                return None
        return None


async def validate_model_availability() -> dict[str, Any]:
    """Check if the configured model is available via the LLM gateway.

    Queries the gateway's /models endpoint and verifies the configured model
    is listed. This catches misconfigurations (e.g., requesting phi3:mini when
    Bifrost has no key/route for it) at startup rather than at first LLM call.

    Returns:
        Dict with 'available' bool, configured model, available models list,
        and a human-readable message if the model is missing.
    """
    import httpx

    settings = get_llm_settings()
    # Use the configured OpenAI-compatible base URL for the /models endpoint.
    models_url = settings.base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(
                f"{models_url}/models",
                headers={"Authorization": f"Bearer {settings.api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

        # OpenAI-compatible /models response has {"data": [{"id": "model-name"}, ...]}
        available_models = [m.get("id", "") for m in data.get("data", [])]
        model_found = settings.model in available_models

        result: dict[str, Any] = {
            "available": model_found,
            "configured_model": settings.model,
            "available_models": available_models,
            "base_url": settings.base_url,
        }
        if not model_found:
            result["message"] = (
                f"Model '{settings.model}' is not listed by the LLM gateway at "
                f"{settings.base_url}. Available models: {available_models}. "
                f"Check the endpoint configuration or change LLM_MODEL to a supported model."
            )
        return result

    except httpx.TimeoutException as e:
        return {
            "available": None,
            "configured_model": settings.model,
            "base_url": settings.base_url,
            "message": f"Gateway timeout querying models endpoint: {e}",
        }
    except httpx.HTTPStatusError as e:
        return {
            "available": None,
            "configured_model": settings.model,
            "base_url": settings.base_url,
            "message": f"Gateway returned HTTP {e.response.status_code}: {e}",
        }
    except (httpx.ConnectError, OSError) as e:
        return {
            "available": None,
            "configured_model": settings.model,
            "base_url": settings.base_url,
            "message": f"Cannot connect to gateway: {e}",
        }


async def health_check() -> dict[str, Any]:
    """Check if the LLM gateway is reachable and responsive."""
    client = get_llm_client()
    settings = get_llm_settings()
    try:
        response = await client.chat.completions.create(
            model=settings.model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
        )
        return {
            "status": "ok",
            "model": settings.model,
            "base_url": settings.base_url,
            "response": response.choices[0].message.content,
        }
    except (APIConnectionError, APITimeoutError) as e:
        return {
            "status": "error",
            "model": settings.model,
            "base_url": settings.base_url,
            "message": f"Connection failed: {e}",
        }
    except Exception as e:
        return {
            "status": "error",
            "model": settings.model,
            "base_url": settings.base_url,
            "message": str(e),
        }


__all__ = [
    "LLMSettings",
    "chat_completion",
    "chat_json",
    "get_llm_client",
    "get_llm_settings",
    "health_check",
    "reset_llm_client",
    "validate_model_availability",
]
