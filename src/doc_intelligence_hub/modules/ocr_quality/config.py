"""OCR quality inventory — configuration."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Paperless-NGX (same env vars as other modules — never logged)
    paperless_url: str = Field(default="http://paperless:8000")
    paperless_api_token: str = Field(default="")

    # Database — dedicated sqlite db for inventory run/assessment state
    database_url: str = Field(default="sqlite:///./data/ocr_quality.db")

    # Optional cross-reference: Action Queue's own database, read-only, used
    # only to attach the legacy `text_quality_score` signal per document.
    action_queue_database_url: str = Field(default="sqlite:///./data/actions.db")

    # Stage 1 — full-corpus scan
    batch_size: int = Field(default=100, ge=1, le=1000)

    # Stage 2 — deterministic stratified sample
    sample_target_size: int = Field(default=300, ge=1)
    sample_seed: str = Field(default="ocr-quality-inventory-v1")
    sample_min_per_stratum: int = Field(default=2, ge=0)
    pdf_profile_max_pages: int = Field(default=50, ge=1)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
