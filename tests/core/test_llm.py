"""Tests for public-safe core.llm settings defaults."""

from doc_intelligence_hub.core.llm import LLMSettings, reset_llm_client


def test_defaults_use_local_openai_compatible_endpoint():
    reset_llm_client()
    settings = LLMSettings(_env_file=None)
    assert settings.base_url == "http://localhost:11434/v1"
    assert settings.api_key == "local-development"
    assert settings.model == "phi3:mini"
