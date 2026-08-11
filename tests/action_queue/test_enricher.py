from unittest.mock import AsyncMock

import pytest

from doc_intelligence_hub.modules.action_queue.config import settings
from doc_intelligence_hub.modules.action_queue.enricher import (
    CUSTOM_FIELD_DEFINITIONS,
    PaperlessEnricher,
)


def test_action_status_field_supports_full_lifecycle():
    status_field = next(
        field for field in CUSTOM_FIELD_DEFINITIONS if field["name"] == "Action Status"
    )
    labels = {option["label"] for option in status_field["extra_data"]["select_options"]}

    assert labels == {
        "pending",
        "acknowledged",
        "completed",
        "snoozed",
        "dismissed",
        "not_an_action",
    }


@pytest.mark.asyncio
async def test_existing_action_status_field_gets_missing_lifecycle_options():
    enricher = PaperlessEnricher()
    enricher.client = AsyncMock()
    existing_field = {
        "id": 7,
        "name": "Action Status",
        "data_type": "select",
        "extra_data": {
            "select_options": [
                {"id": 1, "label": "pending"},
                {"id": 2, "label": "completed"},
                {"id": 3, "label": "dismissed"},
            ]
        },
    }
    enricher.client.list_custom_fields.return_value = [existing_field]
    enricher.client.update_custom_field.return_value = {
        **existing_field,
        "extra_data": {
            "select_options": [
                *existing_field["extra_data"]["select_options"],
                {"id": 4, "label": "acknowledged"},
                {"id": 5, "label": "snoozed"},
                {"id": 6, "label": "not_an_action"},
            ]
        },
    }
    enricher.client.create_custom_field.side_effect = lambda definition: {
        "id": 100,
        **definition,
    }

    await enricher.ensure_custom_fields_exist()

    update = enricher.client.update_custom_field.await_args.args
    assert update[0] == 7
    labels = {
        option["label"]
        for option in update[1]["extra_data"]["select_options"]
    }
    assert {"acknowledged", "snoozed", "not_an_action"} <= labels


@pytest.mark.asyncio
async def test_sync_status_removes_monitored_tags_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "write_to_paperless", True)
    monkeypatch.setattr(settings, "remove_source_tag_on_resolve", True)
    monkeypatch.setattr(settings, "tags_to_monitor", "Inbox")

    enricher = PaperlessEnricher()
    enricher.client = AsyncMock()
    enricher._field_id_cache = {"Action Status": 7}

    await enricher.sync_status(42, "not_an_action")

    enricher.client.update_custom_fields.assert_awaited_once()
    enricher.client.remove_tags_from_document.assert_awaited_once_with(42, ["Inbox"])


@pytest.mark.asyncio
async def test_sync_status_keeps_monitored_tags_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "write_to_paperless", True)
    monkeypatch.setattr(settings, "remove_source_tag_on_resolve", False)
    monkeypatch.setattr(settings, "tags_to_monitor", "Inbox")

    enricher = PaperlessEnricher()
    enricher.client = AsyncMock()
    enricher._field_id_cache = {"Action Status": 7}

    await enricher.sync_status(42, "completed")

    enricher.client.update_custom_fields.assert_awaited_once()
    enricher.client.remove_tags_from_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_status_propagates_tag_removal_failure(monkeypatch):
    monkeypatch.setattr(settings, "write_to_paperless", True)
    monkeypatch.setattr(settings, "remove_source_tag_on_resolve", True)
    monkeypatch.setattr(settings, "tags_to_monitor", "Inbox")

    enricher = PaperlessEnricher()
    enricher.client = AsyncMock()
    enricher.client.remove_tags_from_document.side_effect = RuntimeError(
        "Paperless unavailable"
    )
    enricher._field_id_cache = {"Action Status": 7}

    with pytest.raises(RuntimeError, match="Paperless unavailable"):
        await enricher.sync_status(42, "completed")
