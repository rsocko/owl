"""Statement Tracking service — encapsulates discovery, recommendations, and series management."""

from __future__ import annotations

from datetime import date
from typing import Any

from doc_intelligence_hub.core.resilience import retry_async
from doc_intelligence_hub.core.services.base import BaseService
from doc_intelligence_hub.modules.statements.config import AppConfig, load_config
from doc_intelligence_hub.modules.statements.database import Database
from doc_intelligence_hub.modules.statements.models import (
    DiscoveryDiagnosticResult,
    DiscoveryResult,
    RecommendationResult,
)
from doc_intelligence_hub.modules.statements.service import (
    run_connection_test,
    run_discovery,
    run_discovery_debug,
    run_recommendations,
)


class StatementService(BaseService):
    """Service layer for the Statement Tracking module.

    Wraps statement discovery, recommendations, and series management
    with logging, retry for external calls, and circuit breaker integration.
    """

    service_name = "statements"

    @retry_async(max_attempts=2, base_delay=2.0)
    async def discover_providers(self, config_path: str) -> DiscoveryResult:
        """Run provider discovery with retry for Paperless calls."""
        self.logger.info("Running provider discovery (config=%s)", config_path)
        result = await self._call_with_breaker(
            "paperless",
            run_discovery(config_path),
        )
        self.logger.info(
            "Discovery complete: %d providers from %d documents",
            len(result.providers),
            result.analyzed_documents,
        )
        return result

    async def discover_providers_debug(
        self, config_path: str, limit: int
    ) -> DiscoveryDiagnosticResult:
        """Run diagnostic discovery (no retry — diagnostic only)."""
        self.logger.info("Running diagnostic discovery (limit=%d)", limit)
        return await run_discovery_debug(config_path, limit)

    @retry_async(max_attempts=2, base_delay=2.0)
    async def get_recommendations(
        self, config_path: str, as_of: date
    ) -> RecommendationResult:
        """Run recommendations with retry for Paperless calls."""
        self.logger.info("Running recommendations (as_of=%s)", as_of)
        result = await self._call_with_breaker(
            "paperless",
            run_recommendations(config_path, as_of),
        )
        self.logger.info(
            "Recommendations complete: %d items",
            len(result.recommendations),
        )
        return result

    @retry_async(max_attempts=2, base_delay=1.0)
    async def test_connection(self, config_path: str) -> dict[str, Any]:
        """Test Paperless connection with retry."""
        return await self._call_with_breaker(
            "paperless",
            run_connection_test(config_path),
        )

    def load_latest_discovery(self, config_path: str) -> DiscoveryResult | None:
        """Load the most recent discovery result from the database."""
        config = load_config(config_path)
        db = Database(config.runtime.database_path)
        try:
            return db.load_latest_discovery()
        finally:
            db.close()

    def get_series_list(self, config_path: str) -> list[dict[str, Any]]:
        """Load all statement series from the database."""
        config = load_config(config_path)
        db = Database(config.runtime.database_path)
        try:
            return db.list_series()
        finally:
            db.close()
