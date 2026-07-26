"""Paperless-ngx API client — shared across all modules."""

from doc_intelligence_hub.core.paperless.client import (
    PaperlessClient,
    ProgressCallback,
    load_fixture,
)

__all__ = ["PaperlessClient", "ProgressCallback", "load_fixture"]
