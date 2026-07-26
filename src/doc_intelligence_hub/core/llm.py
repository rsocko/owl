"""Shared LLM client — OpenAI-compatible interface via Bifrost gateway.

All modules should use `get_llm_client()` for LLM calls. This routes through
Bifrost, which handles model routing, failover, and provider abstraction.

Environment variables:
    LLM_BASE_URL: Bifrost gateway URL (default: https://service-001.example.invalid/openai/v1)
    LLM_API_KEY: API key for Bifrost (default: "bifrost" — local gateway)
    LLM_MODEL: Default model to use (default: azure/gpt-4o-mini — routed through Bifrost to Azure).
        Bifrost matches routes by provider-prefixed model id (e.g. "azure/gpt-4o-mini",
        "ollama/phi3:mini"). A bare model name like "gpt-4o-mini" or "phi3:mini" won't match
        any Bifrost route and silently returns unparsable/garbage responses instead of erroring.
    LLM_TIMEOUT: Request timeout in seconds (default: 120)
"""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    """LLM configuration — reads from environment with LLM_ prefix."""

    base_url: str = "https://service-001.example.invalid/openai/v1"
    api_key: str = "bifrost"
    model: str = "azure/gpt-4o-mini"
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
    """Get or create the singleton async OpenAI client pointed at Bifrost."""
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
        response = await client.chat.completions.create(**kwargs)
        return response.choices[0].message.content
    except Exception as e:
        # Log but don't crash — callers handle None gracefully
        import logging

        logging.getLogger(__name__).warning("LLM call failed: %s", e)
        return None


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
        if text.endswith("```"):
            text = text[:-3]
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
    # Strip /chat/completions or trailing path to get the base for /models
    models_url = settings.base_url.rstrip("/")
    if models_url.endswith("/v1"):
        models_url = models_url  # already at /v1 level
    elif models_url.endswith("/openai/v1"):
        models_url = models_url  # already at /openai/v1 level

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
                f"Check Bifrost routing rules or change LLM_MODEL to a supported model."
            )
        return result

    except Exception as e:
        return {
            "available": None,
            "configured_model": settings.model,
            "base_url": settings.base_url,
            "message": f"Could not query gateway models endpoint: {e}",
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
