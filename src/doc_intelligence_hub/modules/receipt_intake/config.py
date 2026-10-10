"""Receipt intake configuration."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = Field(default="sqlite:///./data/receipt_intake.db")
    max_bytes: int = Field(default=25 * 1024 * 1024, ge=1)
    task_poll_attempts: int = Field(default=8, ge=1, le=100)
    task_poll_interval_seconds: float = Field(default=0.25, ge=0, le=30)
    max_upload_attempts: int = Field(default=3, ge=1, le=10)
    paperless_deployment_id: str = Field(default="default")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "env_prefix": "OWL_RECEIPT_INTAKE_",
    }


settings = Settings()
