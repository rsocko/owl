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

    # Incremental Paperless reconciliation is intentionally bounded. Historical
    # documents must be selected through an explicit backfill scope.
    discovery_page_size: int = Field(default=100, ge=1, le=500)
    discovery_overlap_seconds: int = Field(default=300, ge=0, le=86400)
    max_backfill_batch: int = Field(default=500, ge=1, le=5000)
    targeted_reanalysis_debounce_seconds: float = Field(default=3.0, ge=0, le=60)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
