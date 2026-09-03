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

    # Issue #30 — shared run contract. Bounded per-document retry budget
    # applied within a single run before a document is recorded as a
    # terminal failure (``RunFailure``); the run itself is never retried
    # automatically.
    run_max_retries: int = Field(default=2, ge=0, le=10)

    # Issue #18 slice 1 — candidate generation/comparison/staging.
    # Candidate PDF/text bytes are never stored in the DB (mirrors the
    # "no raw OCR text" precedent for DocumentAssessment) — only checksums
    # and paths under this directory.
    candidate_storage_dir: str = Field(default="./data/ocr_quality_candidates")
    candidate_retention_window_days: int = Field(default=30, ge=1)

    # Batch caps (design doc "Batch behavior") — deliberately conservative
    # defaults; there is no "accept all" action anywhere in this module.
    candidate_max_documents_per_batch: int = Field(default=5, ge=1, le=50)
    candidate_max_total_pages_per_batch: int = Field(default=200, ge=1)
    candidate_provider_allowlist: list[str] = Field(
        default_factory=lambda: ["ocrmypdf-tesseract-5"]
    )
    candidate_generation_timeout_seconds: float = Field(default=300.0, ge=1.0)

    # OCRmyPDF/Tesseract provider (local, no external cost).
    ocrmypdf_binary: str = Field(default="ocrmypdf")

    # Azure Document Intelligence provider — disabled unless explicitly
    # enabled *and* endpoint/key are configured. Never invoked otherwise.
    azure_document_intelligence_enabled: bool = Field(default=False)
    azure_document_intelligence_endpoint: str = Field(default="")
    azure_document_intelligence_api_key: str = Field(default="")
    azure_cost_per_page_usd: float = Field(default=0.0015, ge=0.0)
    azure_cost_hard_cap_usd: float = Field(default=5.00, ge=0.0)

    # Issue #18 slice 2 — applying an accepted candidate to Paperless
    # (document-version upload) and rollback. Bounded retries: an apply
    # attempt that fails never leaves Paperless mid-write, and a candidate
    # that fails repeatedly moves to a terminal FAILED state rather than
    # retrying forever.
    candidate_max_apply_attempts: int = Field(default=3, ge=1, le=10)
    candidate_apply_task_poll_seconds: float = Field(default=2.0, ge=0.1)
    candidate_apply_task_poll_timeout_seconds: float = Field(default=120.0, ge=1.0)
    # Bounded retry for confirming Paperless's *extracted content* (and by
    # extension search/index state) reflects the newly-applied version —
    # not just that the preview bytes match (issue #18 audit gap: a
    # successful-looking apply could leave stale search results).
    candidate_apply_content_verify_attempts: int = Field(default=5, ge=1, le=20)
    candidate_apply_content_verify_delay_seconds: float = Field(default=1.0, ge=0.0)
    # How long a document-scoped apply/rollback lock is honored before it is
    # considered stale (e.g. the process holding it crashed) and reclaimable
    # by a new request.
    candidate_apply_lock_ttl_seconds: int = Field(default=300, ge=1)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
