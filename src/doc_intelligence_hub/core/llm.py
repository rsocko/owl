"""Shared LLM client — OpenAI-compatible interface via Bifrost gateway.

All modules should use `get_llm_client()` for LLM calls. This routes through
Bifrost, which handles model routing, failover, and provider abstraction.

Environment variables:
    LLM_BASE_URL: Bifrost gateway URL (default: http://bifrost:8080/openai/v1)
    LLM_API_KEY: API key for Bifrost (default: "bifrost" — local gateway)
    LLM_MODEL: Default model to use (default: phi3:mini — routed through Bifrost to Ollama)
    LLM_TIMEOUT: Request timeout in seconds (default: 120)
"""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    """LLM configuration — reads from environment with LLM_ prefix."""

    base_url: str = "http://bifrost:8080/openai/v1"
    api_key: str = "bifrost"
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
]
