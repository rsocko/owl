"""Paperless Action Queue - Configuration"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Paperless-NGX
    paperless_url: str = Field(default="http://paperless:8000")
    paperless_api_token: str = Field(default="")

    # LLM (optional override — defaults come from core.llm LLM_* env vars)
    llm_model: str = Field(default="")  # Empty = use default from LLM_MODEL

    # Legacy Ollama settings (mapped to LLM_* for backwards compat)
    ollama_url: str = Field(default="")
    ollama_model: str = Field(default="")

    # Database
    database_url: str = Field(default="sqlite:///./data/actions.db")

    # Processing
    confidence_threshold: int = Field(default=70)
    tags_to_monitor: str = Field(default="Inbox,Todo")

    # Safety
    write_to_paperless: bool = Field(default=True)
    rate_limit_delay: float = Field(default=1.0)

    @property
    def monitor_tags(self) -> list[str]:
        return [t.strip() for t in self.tags_to_monitor.split(",")]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
