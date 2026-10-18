from __future__ import annotations

from datetime import UTC, datetime

import pytest

from doc_intelligence_hub.core.paperless.client import PaperlessPage
from doc_intelligence_hub.modules.analysis_invalidation import config as ai_config
from doc_intelligence_hub.modules.analysis_invalidation.database import (
    DiscoveryCursor,
    get_session,
    init_db,
)
from doc_intelligence_hub.modules.analysis_invalidation.discovery import (
    IncrementalDiscoveryService,
)
from doc_intelligence_hub.modules.analysis_invalidation.fingerprint import (
    document_metadata_fields,
    document_source_fingerprint,
)


@pytest.fixture()
def discovery_db(tmp_path):
    original = ai_config.settings.database_url
    ai_config.settings.database_url = f"sqlite:///{tmp_path / 'test_discovery.db'}"
    init_db()
    yield
    ai_config.settings.database_url = original


def _document(
    document_id: int,
    *,
    timestamp: str,
    title: str = "Invoice",
    checksum: str = "checksum-1",
    custom_fields: list[dict] | None = None,
) -> dict:
    return {
        "id": document_id,
        "title": title,
        "checksum": checksum,
        "added": timestamp,
        "modified": timestamp,
        "created": "2026-10-18",
        "correspondent": 3,
        "document_type": 4,
        "tags": [1, 2],
        "custom_fields": custom_fields or [],
    }


class FakePaperlessClient:
    def __init__(self, documents: list[dict] | None = None):
        self.documents = {int(document["id"]): document for document in (documents or [])}
        self.pages: dict[tuple[str, str | None], PaperlessPage] = {}
        self.page_calls: list[dict] = []
        self.list_calls: list[dict] = []

    async def get_document(self, document_id: int) -> dict:
        return self.documents[document_id]

    async def list_document_page(
        self,
        *,
        page_size: int,
        cursor: str | None,
        scope_params: dict,
        ordering: str,
    ) -> PaperlessPage:
        self.page_calls.append(
            {
                "page_size": page_size,
                "cursor": cursor,
                "scope_params": scope_params,
                "ordering": ordering,
            }
        )
        return self.pages.get((ordering, cursor), PaperlessPage((), None, 0))

    async def list_documents(self, **kwargs) -> list[dict]:
        self.list_calls.append(kwargs)
        return list(self.documents.values())[: kwargs.get("limit")]


@pytest.mark.asyncio
async def test_first_incremental_run_bootstraps_without_full_corpus_scan(discovery_db):
    client = FakePaperlessClient([_document(1, timestamp="2026-10-18T12:00:00Z")])
    analyzed: list[int] = []

    result = await IncrementalDiscoveryService().reconcile(
        client=client,
        analyze=lambda document_id: _record(analyzed, document_id),
        now=datetime(2026, 10, 18, 13, 0, tzinfo=UTC),
    )

    assert result["bootstrapped"] is True
    assert analyzed == []
    assert client.page_calls == []


@pytest.mark.asyncio
async def test_scan_cursor_survives_restart_and_equal_timestamps(discovery_db):
    timestamp = "2026-10-18T13:00:00Z"
    first = _document(10, timestamp=timestamp)
    second = _document(11, timestamp=timestamp, checksum="checksum-2")
    client = FakePaperlessClient([first, second])
    analyzed: list[int] = []
    service = IncrementalDiscoveryService()
    await service.reconcile(
        client=client,
        analyze=lambda document_id: _record(analyzed, document_id),
        now=datetime(2026, 10, 18, 12, 0, tzinfo=UTC),
    )
    client.pages[("added,id", None)] = PaperlessPage((first,), "2", 2)
    client.pages[("modified,id", None)] = PaperlessPage((), None, 0)

    first_run = await service.reconcile(
        client=client,
        analyze=lambda document_id: _record(analyzed, document_id),
        page_size=1,
    )
    assert first_run["processed"] == 1

    client.pages[("added,id", "2")] = PaperlessPage((second,), None, 2)
    restarted_service = IncrementalDiscoveryService()
    second_run = await restarted_service.reconcile(
        client=client,
        analyze=lambda document_id: _record(analyzed, document_id),
        page_size=1,
    )

    assert second_run["processed"] == 1
    assert analyzed == [10, 11]
    assert any(call["cursor"] == "2" for call in client.page_calls)

    db = get_session()
    try:
        cursor = db.get(DiscoveryCursor, "paperless-analysis")
        assert cursor.state["added"]["high_water_id"] == 11
        assert cursor.state["added"]["scan_cursor"] is None
    finally:
        db.close()


@pytest.mark.asyncio
async def test_failed_analysis_is_retried_from_durable_pending_state(discovery_db):
    document = _document(20, timestamp="2026-10-18T13:00:00Z")
    client = FakePaperlessClient([document])
    service = IncrementalDiscoveryService()
    await service.reconcile(
        client=client,
        analyze=lambda document_id: _record([], document_id),
        now=datetime(2026, 10, 18, 12, 0, tzinfo=UTC),
    )
    client.pages[("added,id", None)] = PaperlessPage((document,), None, 1)
    client.pages[("modified,id", None)] = PaperlessPage((), None, 0)

    async def fail(_document_id: int):
        raise RuntimeError("temporary failure")

    failed = await service.reconcile(client=client, analyze=fail)
    assert failed["failed"] == 1
    assert failed["pending"] == 1

    analyzed: list[int] = []
    client.pages[("added,id", None)] = PaperlessPage((), None, 0)
    recovered = await IncrementalDiscoveryService().reconcile(
        client=client,
        analyze=lambda document_id: _record(analyzed, document_id),
    )

    assert recovered["processed"] == 1
    assert recovered["pending"] == 0
    assert analyzed == [20]


def test_fingerprint_ignores_unselected_projected_fields_but_tracks_analyzer_inputs():
    base = _document(
        30,
        timestamp="2026-10-18T13:00:00Z",
        custom_fields=[{"field": 100, "value": "pending"}, {"field": 200, "value": "100.00"}],
    )
    projected_change = {
        **base,
        "modified": "2026-10-18T13:05:00Z",
        "custom_fields": [
            {"field": 100, "value": "completed"},
            {"field": 200, "value": "100.00"},
        ],
    }
    material_change = {
        **projected_change,
        "custom_fields": [
            {"field": 100, "value": "completed"},
            {"field": 200, "value": "125.00"},
        ],
    }

    base_fingerprint, _, _ = document_source_fingerprint(
        base,
        governed_custom_field_ids={200},
    )
    projected_fingerprint, _, _ = document_source_fingerprint(
        projected_change,
        governed_custom_field_ids={200},
    )
    material_fingerprint, _, _ = document_source_fingerprint(
        material_change,
        governed_custom_field_ids={200},
    )

    assert projected_fingerprint == base_fingerprint
    assert material_fingerprint != base_fingerprint
    assert document_metadata_fields(base)["governed_custom_fields"] == {}


def test_fingerprint_can_ignore_inbox_workflow_tag():
    base = _document(31, timestamp="2026-10-18T13:00:00Z")
    filed = {**base, "tags": [2]}

    base_fingerprint, _, _ = document_source_fingerprint(base, ignored_tag_ids={1})
    filed_fingerprint, _, _ = document_source_fingerprint(filed, ignored_tag_ids={1})

    assert filed_fingerprint == base_fingerprint


@pytest.mark.asyncio
async def test_backfill_requires_and_preserves_explicit_bounds(discovery_db):
    client = FakePaperlessClient(
        [
            _document(40, timestamp="2026-10-18T13:00:00Z"),
            _document(41, timestamp="2026-10-18T13:01:00Z"),
        ]
    )
    service = IncrementalDiscoveryService()

    with pytest.raises(ValueError, match="exactly one"):
        await service.backfill(client=client, analyze=lambda document_id: _record([], document_id))

    analyzed: list[int] = []
    result = await service.backfill(
        client=client,
        analyze=lambda document_id: _record(analyzed, document_id),
        id_after=40,
        id_before=41,
        limit=2,
    )

    assert result["scope"] == "id_range"
    assert analyzed == [40, 41]
    assert client.list_calls == [
        {
            "added_after": None,
            "added_before": None,
            "id_after": 40,
            "id_before": 41,
            "ordering": "id",
            "limit": 2,
        }
    ]


async def _record(target: list[int], document_id: int) -> dict:
    target.append(document_id)
    return {"document_id": document_id}
