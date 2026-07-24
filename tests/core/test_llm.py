"""Tests for core.llm settings defaults.

Regression coverage for a bug where LLM_MODEL defaulted to a bare model
name ("gpt-4o-mini") that doesn't match any Bifrost routing rule. Bifrost
routes require a provider-prefixed model id (e.g. "azure/gpt-4o-mini",
"ollama/phi3:mini") — a bare name causes calls to silently return
unparsable/garbage responses instead of a clear routing error.
"""

from doc_intelligence_hub.core.llm import LLMSettings, reset_llm_client


def test_default_model_is_provider_prefixed():
    reset_llm_client()
    settings = LLMSettings(_env_file=None)
    assert settings.model == "azure/gpt-4o-mini"
    assert "/" in settings.model, "model id must be provider-prefixed for Bifrost routing"
