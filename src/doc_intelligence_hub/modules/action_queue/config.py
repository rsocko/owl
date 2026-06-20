"""Paperless Action Queue - Configuration"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Paperless-NGX
    paperless_url: str = Field(default="http://paperless:8000")
    paperless_token: str = Field(default="")

    # Ollama
    ollama_url: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="phi3:mini")

    # Database
    database_url: str = Field(default="sqlite:///./data/actions.db")

    # Processing
    confidence_threshold: int = Field(default=70)
    tags_to_monitor: str = Field(default="Inbox,Todo")

    # Safety
    write_to_paperless: bool = Field(default=True)  # Set False to disable all Paperless writes
    rate_limit_delay: float = Field(default=1.0)  # Seconds between API writes (be nice to Paperless)

    @property
    def monitor_tags(self) -> list[str]:
        return [t.strip() for t in self.tags_to_monitor.split(",")]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
