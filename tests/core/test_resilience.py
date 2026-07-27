"""Tests for core.resilience — retry, circuit breaker, and typed exceptions."""

import asyncio

import pytest

from doc_intelligence_hub.core.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    HubError,
    LLMError,
    PaperlessError,
    TransientError,
    VersionConflictError,
    is_transient_error,
    reset_circuit_breakers,
    retry_async,
)


class TestExceptionHierarchy:
    def test_hub_error_is_base(self):
        assert issubclass(TransientError, HubError)
        assert issubclass(LLMError, TransientError)
        assert issubclass(PaperlessError, TransientError)

    def test_version_conflict_fields(self):
        exc = VersionConflictError("Action", 42, expected_version=3, actual_version=5)
        assert exc.entity == "Action"
        assert exc.entity_id == 42
        assert exc.expected_version == 3
        assert exc.actual_version == 5
        assert "version conflict" in str(exc)


class TestIsTransientError:
    def test_transient_error_subclass(self):
        assert is_transient_error(LLMError("timeout"))

    def test_connection_error(self):
        assert is_transient_error(ConnectionError("refused"))

    def test_os_error(self):
        assert is_transient_error(OSError("network unreachable"))

    def test_value_error_not_transient(self):
        assert not is_transient_error(ValueError("bad input"))

    def test_runtime_error_not_transient(self):
        assert not is_transient_error(RuntimeError("unexpected"))


class TestRetryAsync:
    @pytest.mark.asyncio
    async def test_succeeds_first_try(self):
        call_count = 0

        @retry_async(max_attempts=3, base_delay=0.01)
        async def fn():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await fn()
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_transient_error(self):
        call_count = 0

        @retry_async(max_attempts=3, base_delay=0.01)
        async def fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise LLMError("timeout")
            return "recovered"

        result = await fn()
        assert result == "recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_attempts(self):
        @retry_async(max_attempts=2, base_delay=0.01)
        async def fn():
            raise LLMError("always fails")

        with pytest.raises(LLMError, match="always fails"):
            await fn()

    @pytest.mark.asyncio
    async def test_does_not_retry_non_transient(self):
        call_count = 0

        @retry_async(max_attempts=3, base_delay=0.01)
        async def fn():
            nonlocal call_count
            call_count += 1
            raise ValueError("bad input")

        with pytest.raises(ValueError):
            await fn()
        assert call_count == 1


class TestCircuitBreaker:
    def setup_method(self):
        reset_circuit_breakers()

    def test_starts_closed(self):
        cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=60.0)
        assert cb.state == "closed"
        assert cb.allow_request() is True

    def test_opens_after_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=60.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "closed"
        cb.record_failure()
        assert cb.state == "open"
        assert cb.allow_request() is False

    def test_success_resets_count(self):
        cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=60.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        assert cb.state == "closed"

    def test_half_open_after_timeout(self):
        import time

        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0.05)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        time.sleep(0.06)
        assert cb.state == "half_open"
        assert cb.allow_request() is True
