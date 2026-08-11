"""Tests for neutral Paperless metadata enrichment."""

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


@pytest.fixture()
def enricher(monkeypatch):
    monkeypatch.setattr(
        "doc_intelligence_hub.modules.action_queue.enricher.settings.write_to_paperless",
        True,
    )
    monkeypatch.setattr(
        "doc_intelligence_hub.modules.action_queue.enricher.settings.rate_limit_delay",
        0,
    )
    instance = PaperlessEnricher()
    instance.client = AsyncMock()
    return instance


@pytest.mark.asyncio
async def test_enrich_document_writes_only_neutral_metadata(enricher):
    enricher._field_id_cache = {
        "Document Amount": 1,
        "Action Status": 2,
        "Action Analyzed": 3,
        "Action Type": 5,
        "Action Due Date": 6,
        "Action Urgency": 7,
        "Action Summary": 8,
        "Action Count": 9,
    }

    await enricher.enrich_document(
        42,
        {
            "action_type": "PAY",
            "due_date": "2026-08-15",
            "amount": 123.45,
            "urgency": "HIGH",
            "summary": "Pay immediately",
        },
    )

    fields = enricher.client.update_custom_fields.await_args.args[1]
    values = {field["field"]: field["value"] for field in fields}
    assert values[1] == 123.45
    assert values[2] == "pending"
    assert not {5, 6, 7, 8, 9}.intersection(values)


@pytest.mark.asyncio
async def test_not_an_action_clears_legacy_inferred_fields_but_keeps_amount(enricher):
    enricher._field_id_cache = {
        "Document Amount": 1,
        "Action Status": 2,
        "Action Type": 5,
        "Action Due Date": 6,
        "Action Urgency": 7,
        "Action Summary": 8,
        "Action Count": 9,
    }

    await enricher.sync_status(42, "not_an_action")

    fields = enricher.client.update_custom_fields.await_args.args[1]
    values = {field["field"]: field["value"] for field in fields}
    assert values == {
        2: "not_an_action",
        5: None,
        6: None,
        7: None,
        8: None,
        9: None,
    }
    assert 1 not in values


@pytest.mark.asyncio
async def test_existing_action_amount_field_is_renamed(enricher):
    enricher.client.list_custom_fields.return_value = [
        {"id": 1, "name": "Action Amount", "data_type": "float"},
        {
            "id": 2,
            "name": "Action Status",
            "data_type": "select",
            "extra_data": {
                "select_options": [
                    {"id": 20, "label": "pending"},
                    {"id": 21, "label": "completed"},
                    {"id": 22, "label": "dismissed"},
                ]
            },
        },
        {"id": 3, "name": "Action Analyzed", "data_type": "date"},
    ]
    enricher.client.update_custom_field.side_effect = [
        {"id": 1, "name": "Document Amount", "data_type": "float"},
        {
            "id": 2,
            "name": "Action Status",
            "data_type": "select",
            "extra_data": {
                "select_options": [
                    {"id": 20, "label": "pending"},
                    {"id": 21, "label": "completed"},
                    {"id": 22, "label": "dismissed"},
                    {"label": "acknowledged"},
                    {"label": "snoozed"},
                    {"label": "not_an_action"},
                ]
            },
        },
    ]

    field_ids = await enricher.ensure_custom_fields_exist()

    assert field_ids["Document Amount"] == 1
    assert enricher.client.update_custom_field.await_args_list[0].args == (
        1,
        {"name": "Document Amount"},
    )
