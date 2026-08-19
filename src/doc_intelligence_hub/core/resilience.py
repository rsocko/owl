"""Resilience utilities — retry, circuit breaker, and typed exceptions.

Provides:
- `retry_async`: Decorator for async functions with exponential backoff
- `retry_on_transient`: Pre-configured retry for transient HTTP/network errors
- Typed exception hierarchy for categorized error handling
- Circuit breaker for external service calls
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

import httpx

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Typed Exception Hierarchy
# ---------------------------------------------------------------------------


class HubError(Exception):
    """Base exception for all Document Intelligence Hub errors."""

    pass


class TransientError(HubError):
    """Retriable error — external service temporarily unavailable."""

    pass


class ExternalServiceError(TransientError):
    """External service (Paperless, LLM gateway) returned an error."""

    def __init__(self, service: str, message: str, status_code: int | None = None):
        self.service = service
        self.status_code = status_code
        super().__init__(
            f"[{service}] {message} (HTTP {status_code})"
            if status_code
            else f"[{service}] {message}"
        )


class LLMError(TransientError):
    """LLM gateway call failed."""

    pass


class PaperlessError(TransientError):
    """Paperless-ngx API call failed."""

    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(
            f"Paperless: {message} (HTTP {status_code})" if status_code else f"Paperless: {message}"
        )


class UnsupportedSavedViewError(PaperlessError):
    """A Paperless saved view uses rules OWL cannot safely translate."""

    pass


class ConfigurationError(HubError):
    """Invalid or missing configuration."""

    pass


class DataIntegrityError(HubError):
    """Data constraint violation (e.g., optimistic locking conflict)."""

    pass


class VersionConflictError(DataIntegrityError):
    """Optimistic locking version mismatch — concurrent modification detected."""

    def __init__(self, entity: str, entity_id: Any, expected_version: int, actual_version: int):
        self.entity = entity
        self.entity_id = entity_id
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"{entity} {entity_id}: version conflict "
            f"(expected {expected_version}, found {actual_version})"
        )


# ---------------------------------------------------------------------------
# Retry Decorator
# ---------------------------------------------------------------------------

# HTTP status codes that indicate a transient failure worth retrying
TRANSIENT_HTTP_CODES = frozenset({408, 429, 500, 502, 503, 504})


def is_transient_error(exc: BaseException) -> bool:
    """Determine if an exception represents a transient, retriable failure."""
    if isinstance(exc, TransientError):
        return True
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.ConnectError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in TRANSIENT_HTTP_CODES
    if isinstance(exc, (ConnectionError, OSError)):
        return True
    return False


def retry_async(
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    retryable: Callable[[BaseException], bool] = is_transient_error,
    on_retry: Callable[[BaseException, int, float], None] | None = None,
):
    """Decorator: retry an async function with exponential backoff.

    Args:
        max_attempts: Total attempts including the initial call.
        base_delay: Initial delay in seconds before first retry.
        max_delay: Cap on the computed delay.
        backoff_factor: Multiplier applied to delay after each retry.
        retryable: Predicate that returns True if the exception should trigger a retry.
        on_retry: Optional callback(exception, attempt_number, delay) invoked before each retry sleep.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = base_delay
            last_exc: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt >= max_attempts or not retryable(exc):
                        raise
                    # Compute delay with exponential backoff
                    current_delay = min(delay, max_delay)
                    if on_retry:
                        on_retry(exc, attempt, current_delay)
                    else:
                        logger.warning(
                            "%s attempt %d/%d failed (%s), retrying in %.1fs",
                            func.__qualname__,
                            attempt,
                            max_attempts,
                            exc,
                            current_delay,
                        )
                    await asyncio.sleep(current_delay)
                    delay *= backoff_factor
            # Should not reach here, but satisfy type checker
            raise last_exc  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """Simple circuit breaker for external service calls.

    States:
    - CLOSED: requests flow through normally
    - OPEN: requests are immediately rejected (fail-fast)
    - HALF_OPEN: a single probe request is allowed through

    Transitions:
    - CLOSED → OPEN: after `failure_threshold` consecutive failures
    - OPEN → HALF_OPEN: after `recovery_timeout` seconds
    - HALF_OPEN → CLOSED: on a successful probe
    - HALF_OPEN → OPEN: on a failed probe
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = self.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0

    @property
    def state(self) -> str:
        if self._state == self.OPEN:
            if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                self._state = self.HALF_OPEN
        return self._state

    def record_success(self) -> None:
        """Record a successful call — resets failure count and closes circuit."""
        self._failure_count = 0
        self._state = self.CLOSED

    def record_failure(self) -> None:
        """Record a failed call — may trip the circuit to OPEN."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self.failure_threshold:
            self._state = self.OPEN
            logger.error(
                "Circuit breaker '%s' OPEN after %d consecutive failures",
                self.name,
                self._failure_count,
            )

    def allow_request(self) -> bool:
        """Return True if a request should be allowed through."""
        state = self.state  # triggers OPEN→HALF_OPEN check
        if state == self.CLOSED:
            return True
        if state == self.HALF_OPEN:
            return True  # allow one probe
        return False

    def __repr__(self) -> str:
        return f"CircuitBreaker(name={self.name!r}, state={self.state}, failures={self._failure_count})"


class CircuitOpenError(TransientError):
    """Circuit breaker is open — requests are being rejected to protect the system."""

    def __init__(self, breaker: CircuitBreaker):
        self.breaker = breaker
        super().__init__(
            f"Circuit breaker '{breaker.name}' is OPEN (recovering in {breaker.recovery_timeout}s)"
        )


# ---------------------------------------------------------------------------
# Pre-configured circuit breakers for known external services
# ---------------------------------------------------------------------------

_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(service: str, **kwargs: Any) -> CircuitBreaker:
    """Get or create a named circuit breaker for a service."""
    if service not in _breakers:
        _breakers[service] = CircuitBreaker(name=service, **kwargs)
    return _breakers[service]


def reset_circuit_breakers() -> None:
    """Reset all circuit breakers (for testing)."""
    _breakers.clear()


__all__ = [
    "CircuitBreaker",
    "CircuitOpenError",
    "ConfigurationError",
    "DataIntegrityError",
    "ExternalServiceError",
    "HubError",
    "LLMError",
    "PaperlessError",
    "UnsupportedSavedViewError",
    "TransientError",
    "VersionConflictError",
    "get_circuit_breaker",
    "is_transient_error",
    "reset_circuit_breakers",
    "retry_async",
]
