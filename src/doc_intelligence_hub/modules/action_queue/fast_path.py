"""Bounded coordination for rule-triggered Action Queue analysis."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from doc_intelligence_hub.modules.action_queue.config import settings
from doc_intelligence_hub.modules.action_queue.pipeline import run_pipeline

PipelineRunner = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class FastPathResult:
    status: str
    document_id: int
    pipeline_result: dict[str, Any] | None = None
    reason: str | None = None


class FastPathCoordinator:
    """Deduplicate bursts and bound pending rule-triggered pipeline runs."""

    def __init__(
        self,
        *,
        runner: PipelineRunner = run_pipeline,
        max_pending: int = 100,
        min_interval_seconds: float = 0.25,
    ) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be at least 1")
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds cannot be negative")

        self._runner = runner
        self._max_pending = max_pending
        self._min_interval_seconds = min_interval_seconds
        self._pending: set[int] = set()
        self._state_lock = asyncio.Lock()

    async def trigger(
        self,
        document_id: int,
        *,
        force: bool = False,
        dry_run: bool = False,
    ) -> FastPathResult:
        if isinstance(document_id, bool) or not isinstance(document_id, int) or document_id <= 0:
            return FastPathResult(
                status="rejected",
                document_id=document_id,
                reason="document_id must be a positive integer",
            )

        async with self._state_lock:
            if document_id in self._pending:
                return FastPathResult(status="already_pending", document_id=document_id)
            if len(self._pending) >= self._max_pending:
                return FastPathResult(
                    status="rejected",
                    document_id=document_id,
                    reason="fast-path queue capacity reached",
                )
            self._pending.add(document_id)

        try:
            result = await self._runner(
                document_id=document_id,
                force=force,
                dry_run=dry_run,
                min_start_interval_seconds=self._min_interval_seconds,
            )
            return FastPathResult(
                status="completed",
                document_id=document_id,
                pipeline_result=result,
            )
        finally:
            async with self._state_lock:
                self._pending.discard(document_id)


_coordinator = FastPathCoordinator(
    max_pending=settings.fast_path_max_pending,
    min_interval_seconds=settings.fast_path_min_interval_seconds,
)


async def trigger_fast_path_analysis(
    document_id: int,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> FastPathResult:
    """Submit one document to the shared rule-triggered coordinator."""
    return await _coordinator.trigger(document_id, force=force, dry_run=dry_run)
