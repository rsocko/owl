"""Tests for neutral Paperless metadata enrichment."""

from unittest.mock import AsyncMock

import pytest

from doc_intelligence_hub.core.paperless import (
    MetadataFieldKey,
    resolve_metadata_schema,
)
from doc_intelligence_hub.modules.action_queue.config import settings
from doc_intelligence_hub.modules.action_queue.enricher import (
    CUSTOM_FIELD_DEFINITIONS,
    PaperlessEnricher,
)

_ACTION_KEYS = (
    MetadataFieldKey.ACCOUNT_IDENTIFIER,
    MetadataFieldKey.INVOICE_NUMBER,
    MetadataFieldKey.DOCUMENT_AMOUNT,
    MetadataFieldKey.ACTION_STATUS,
    MetadataFieldKey.ACTION_ANALYZED,
    MetadataFieldKey.LEGACY_ACTION_TYPE,
    MetadataFieldKey.LEGACY_ACTION_DUE_DATE,
    MetadataFieldKey.LEGACY_ACTION_URGENCY,
    MetadataFieldKey.LEGACY_ACTION_SUMMARY,
    MetadataFieldKey.LEGACY_ACTION_COUNT,
)


def _prime_schema(enricher: PaperlessEnricher, *, include_legacy: bool = False) -> None:
    definitions = [
        {"id": 10, "name": "Account Identifier", "data_type": "string"},
        {"id": 11, "name": "Invoice Number", "data_type": "string"},
        {"id": 1, "name": "Document Amount", "data_type": "float"},
        {
            "id": 2,
            "name": "Action Status",
            "data_type": "select",
            "extra_data": {
                "select_options": [
                    {"id": 20, "label": "pending"},
                    {"id": 21, "label": "acknowledged"},
                    {"id": 22, "label": "completed"},
                    {"id": 23, "label": "snoozed"},
                    {"id": 24, "label": "dismissed"},
                    {"id": 25, "label": "not_an_action"},
                ]
            },
        },
        {"id": 3, "name": "Action Analyzed", "data_type": "date"},
    ]
    if include_legacy:
        definitions.extend(
            [
                {"id": 5, "name": "Action Type", "data_type": "select"},
                {"id": 6, "name": "Action Due Date", "data_type": "date"},
                {"id": 7, "name": "Action Urgency", "data_type": "select"},
                {"id": 8, "name": "Action Summary", "data_type": "string"},
                {"id": 9, "name": "Action Count", "data_type": "integer"},
            ]
        )
    enricher._set_schema(resolve_metadata_schema(definitions, _ACTION_KEYS))


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
    labels = {option["label"] for option in update[1]["extra_data"]["select_options"]}
    assert {"acknowledged", "snoozed", "not_an_action"} <= labels


@pytest.mark.asyncio
async def test_sync_status_removes_monitored_tags_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "write_to_paperless", True)
    monkeypatch.setattr(settings, "remove_source_tag_on_resolve", True)
    monkeypatch.setattr(settings, "tags_to_monitor", "Inbox")

    enricher = PaperlessEnricher()
    enricher.client = AsyncMock()
    _prime_schema(enricher)

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
    _prime_schema(enricher)

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
    enricher.client.remove_tags_from_document.side_effect = RuntimeError("Paperless unavailable")
    _prime_schema(enricher)

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
    _prime_schema(enricher, include_legacy=True)

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
    assert values[2] == 20
    assert not {5, 6, 7, 8, 9}.intersection(values)


@pytest.mark.asyncio
async def test_enrich_document_projects_only_masked_identifier_and_canonical_reference(enricher):
    _prime_schema(enricher)

    await enricher.enrich_document(
        42,
        {
            "amount": 50.0,
            "extracted_data": {
                "account_identifier": "ending 4321",
                "reference_number": "INV-42",
                "payment_url": "https://example.test/pay",
                "email": "billing@example.test",
            },
        },
    )

    fields = enricher.client.update_custom_fields.await_args.args[1]
    values = {field["field"]: field["value"] for field in fields}
    assert values[10] == "ending 4321"
    assert values[11] == "INV-42"
    assert all("example.test" not in str(value) for value in values.values())


@pytest.mark.asyncio
async def test_not_an_action_clears_legacy_inferred_fields_but_keeps_amount(enricher):
    _prime_schema(enricher, include_legacy=True)

    await enricher.sync_status(42, "not_an_action")

    fields = enricher.client.update_custom_fields.await_args.args[1]
    values = {field["field"]: field["value"] for field in fields}
    assert values == {
        2: 25,
        5: None,
        6: None,
        7: None,
        8: None,
        9: None,
    }
    assert 1 not in values


@pytest.mark.asyncio
async def test_uncertain_action_clears_current_and_legacy_inference_but_keeps_durable_facts(
    enricher,
):
    _prime_schema(enricher, include_legacy=True)

    await enricher.enrich_document(
        42,
        {
            "amount": 50.0,
            "extracted_data": {
                "account_identifier": "ending 4321",
                "reference_number": "INV-42",
            },
        },
        action_status=None,
        clear_action_inference=True,
    )

    fields = enricher.client.update_custom_fields.await_args.args[1]
    values = {field["field"]: field["value"] for field in fields}
    assert values[1] == 50.0
    assert values[10] == "ending 4321"
    assert values[11] == "INV-42"
    assert values[2] is None
    assert all(values[field_id] is None for field_id in (5, 6, 7, 8, 9))


@pytest.mark.asyncio
async def test_existing_action_amount_field_is_renamed(enricher):
    initial_definitions = [
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
    final_definitions = [
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
                    {"id": 23, "label": "acknowledged"},
                    {"id": 24, "label": "snoozed"},
                    {"id": 25, "label": "not_an_action"},
                ]
            },
        },
        {"id": 3, "name": "Action Analyzed", "data_type": "date"},
    ]
    enricher.client.list_custom_fields.side_effect = [
        initial_definitions,
        initial_definitions,
        final_definitions,
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

    assert field_ids[MetadataFieldKey.DOCUMENT_AMOUNT] == 1
    assert enricher.client.update_custom_field.await_args_list[0].args == (
        1,
        {"name": "Document Amount"},
    )
