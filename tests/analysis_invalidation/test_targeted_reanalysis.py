from __future__ import annotations

import asyncio

import pytest

from doc_intelligence_hub.modules.analysis_invalidation import config as ai_config
from doc_intelligence_hub.modules.analysis_invalidation.database import init_db
from doc_intelligence_hub.modules.analysis_invalidation.targeted_reanalysis import (
    TargetedReanalysisService,
)


@pytest.fixture()
def service(tmp_path):
    original = ai_config.settings.database_url
    ai_config.settings.database_url = f"sqlite:///{tmp_path / 'targeted.db'}"
    init_db()
    yield TargetedReanalysisService()
    ai_config.settings.database_url = original


def _schedule(
    service: TargetedReanalysisService,
    fingerprint: str,
    *,
    reason: str = "metadata_changed",
):
    return service.schedule(
        document_id=42,
        source_fingerprint=fingerprint,
        source_modified="revision",
        reason=reason,
        analyzer_version="analyzer-v1",
        debounce_seconds=0,
    )


def test_same_pending_source_is_coalesced(service):
    first = _schedule(service, "source-a")
    second = _schedule(service, "source-a")

    assert second["coalesced"] is True
    assert second["generation"] == first["generation"]


@pytest.mark.asyncio
async def test_newer_source_supersedes_pending_generation(service):
    first = _schedule(service, "source-a")
    second = _schedule(service, "source-b")
    analyze_calls = 0

    async def analyze(_document_id: int, _fingerprint: str):
        nonlocal analyze_calls
        analyze_calls += 1
        return {}

    result = await service.run_if_current(
        document_id=42,
        generation=first["generation"],
        analyze=analyze,
    )

    assert result["status"] == "superseded"
    assert analyze_calls == 0
    assert second["generation"] == first["generation"] + 1


@pytest.mark.asyncio
async def test_completion_cannot_overwrite_newer_request(service):
    first = _schedule(service, "source-a")
    started = asyncio.Event()
    release = asyncio.Event()

    async def analyze(_document_id: int, _fingerprint: str):
        started.set()
        await release.wait()
        return {"processed": 1}

    running = asyncio.create_task(
        service.run_if_current(
            document_id=42,
            generation=first["generation"],
            analyze=analyze,
        )
    )
    await started.wait()
    second = _schedule(service, "source-b")
    release.set()

    result = await running
    current = service.get(42)

    assert result["status"] == "superseded"
    assert current is not None
    assert current["generation"] == second["generation"]
    assert current["status"] == "pending"


@pytest.mark.asyncio
async def test_manual_reanalysis_bypasses_completed_coalescing(service):
    first = _schedule(service, "source-a")

    async def analyze(_document_id: int, _fingerprint: str):
        return {"processed": 1}

    completed = await service.run_if_current(
        document_id=42,
        generation=first["generation"],
        analyze=analyze,
    )
    manual = _schedule(service, "source-a", reason="manual_reanalysis")

    assert completed["status"] == "completed"
    assert manual["coalesced"] is False
    assert manual["generation"] == first["generation"] + 1
