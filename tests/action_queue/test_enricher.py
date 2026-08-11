"""Tests for neutral Paperless metadata enrichment."""

from unittest.mock import AsyncMock

import pytest

from doc_intelligence_hub.modules.action_queue.enricher import PaperlessEnricher


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
