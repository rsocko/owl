"""Base service class with logging, retry, and circuit breaker support."""

from __future__ import annotations

import logging
from typing import Any

from doc_intelligence_hub.core.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    get_circuit_breaker,
    retry_async,
)


class BaseService:
    """Base class for all service layer classes.

    Provides:
    - Named logger per service
    - Circuit breaker access for external calls
    - Retry decorator helper
    """

    service_name: str = "base"

    def __init__(self) -> None:
        self.logger = logging.getLogger(f"doc_intelligence_hub.services.{self.service_name}")

    def get_circuit_breaker(self, name: str | None = None, **kwargs: Any) -> CircuitBreaker:
        """Get or create a circuit breaker for this service's external calls."""
        breaker_name = name or self.service_name
        return get_circuit_breaker(breaker_name, **kwargs)

    async def _call_with_breaker(self, breaker_name: str, coro):
        """Execute an async call with circuit breaker protection."""
        breaker = self.get_circuit_breaker(breaker_name)
        if not breaker.allow_request():
            raise CircuitOpenError(breaker)
        try:
            result = await coro
            breaker.record_success()
            return result
        except Exception as exc:
            breaker.record_failure()
            raise
