"""Tests for bounded rule-triggered Action Queue coordination."""

from __future__ import annotations

import asyncio

import pytest

from doc_intelligence_hub.modules.action_queue import pipeline as pipeline_module
from doc_intelligence_hub.modules.action_queue.fast_path import FastPathCoordinator


@pytest.mark.asyncio
async def test_concurrent_duplicate_trigger_runs_once():
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[int] = []

    async def runner(**kwargs):
        calls.append(kwargs["document_id"])
        started.set()
        await release.wait()
        return {"processed": 1}

    coordinator = FastPathCoordinator(runner=runner, min_interval_seconds=0)
    first = asyncio.create_task(coordinator.trigger(42))
    await started.wait()

    duplicate = await coordinator.trigger(42)
    release.set()
    completed = await first

    assert duplicate.status == "already_pending"
    assert completed.status == "completed"
    assert calls == [42]


@pytest.mark.asyncio
async def test_trigger_rejects_when_capacity_is_reached():
    started = asyncio.Event()
    release = asyncio.Event()

    async def runner(**kwargs):
        started.set()
        await release.wait()
        return {"processed": 1}

    coordinator = FastPathCoordinator(
        runner=runner,
        max_pending=1,
        min_interval_seconds=0,
    )
    first = asyncio.create_task(coordinator.trigger(1))
    await started.wait()

    rejected = await coordinator.trigger(2)
    release.set()
    await first

    assert rejected.status == "rejected"
    assert rejected.reason == "fast-path queue capacity reached"


@pytest.mark.asyncio
async def test_trigger_passes_cooldown_to_serialized_pipeline_entrypoint():
    received_interval = None

    async def runner(**kwargs):
        nonlocal received_interval
        received_interval = kwargs["min_start_interval_seconds"]
        return {"processed": 1}

    coordinator = FastPathCoordinator(
        runner=runner,
        min_interval_seconds=0.75,
    )

    await coordinator.trigger(42)

    assert received_interval == 0.75


@pytest.mark.asyncio
@pytest.mark.parametrize("document_id", [0, -1, True, "42"])
async def test_trigger_rejects_invalid_document_scope(document_id):
    calls = 0

    async def runner(**kwargs):
        nonlocal calls
        calls += 1
        return {"processed": 1}

    coordinator = FastPathCoordinator(runner=runner, min_interval_seconds=0)
    result = await coordinator.trigger(document_id)

    assert result.status == "rejected"
    assert calls == 0


@pytest.mark.asyncio
async def test_pipeline_entrypoint_serializes_scheduled_and_fast_path_runs(monkeypatch):
    active = 0
    max_active = 0
    release = asyncio.Event()
    both_started = asyncio.Event()
    attempts = 0

    class FakePipeline:
        async def run(self, **kwargs):
            nonlocal active, max_active, attempts
            attempts += 1
            if attempts == 2:
                both_started.set()
            active += 1
            max_active = max(max_active, active)
            if kwargs["document_id"] == 1:
                await release.wait()
            active -= 1
            return {"processed": 1}

    monkeypatch.setattr(pipeline_module, "Pipeline", FakePipeline)

    first = asyncio.create_task(pipeline_module.run_pipeline(document_id=1))
    await asyncio.sleep(0)
    second = asyncio.create_task(pipeline_module.run_pipeline(document_id=None))
    await asyncio.sleep(0.01)
    assert not both_started.is_set()

    release.set()
    await asyncio.gather(first, second)

    assert max_active == 1
