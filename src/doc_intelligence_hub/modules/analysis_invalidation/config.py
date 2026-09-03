"""Analysis invalidation — configuration."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database — dedicated sqlite db for invalidation events / fingerprints
    database_url: str = Field(default="sqlite:///./data/analysis_invalidation.db")

    # Optional cross-reference: OCR quality's own database, read-only, used
    # only to resolve the "low_confidence_failed" manual-invalidation scope
    # to a set of document IDs (mirrors ocr_quality's own read-only
    # cross-reference into the Action Queue database).
    ocr_quality_database_url: str = Field(default="sqlite:///./data/ocr_quality.db")

    # Manual invalidation is bounded — never affects more documents than this
    # in a single call, even for the "all documents" scope.
    max_manual_invalidation_batch: int = Field(default=2000, ge=1)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
