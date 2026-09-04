"""Tests for neutral Paperless metadata enrichment."""

from datetime import date
from unittest.mock import AsyncMock, Mock

import pytest

from doc_intelligence_hub.core.paperless import (
    MetadataFieldKey,
    resolve_metadata_schema,
)
from doc_intelligence_hub.modules.action_queue import enricher as enricher_module
from doc_intelligence_hub.modules.action_queue.config import settings
from doc_intelligence_hub.modules.action_queue.enricher import (
    CUSTOM_FIELD_DEFINITIONS,
    PaperlessEnricher,
)

_ACTION_KEYS = (
    MetadataFieldKey.ACCOUNT_IDENTIFIER,
    MetadataFieldKey.INVOICE_NUMBER,
    MetadataFieldKey.DOCUMENT_AMOUNT,
    MetadataFieldKey.DOCUMENT_DUE_DATE,
    MetadataFieldKey.ACTION_STATUS,
    MetadataFieldKey.ACTION_ANALYZED,
    MetadataFieldKey.LEGACY_ACTION_TYPE,
    MetadataFieldKey.LEGACY_ACTION_DUE_DATE,
    MetadataFieldKey.LEGACY_ACTION_URGENCY,
    MetadataFieldKey.LEGACY_ACTION_SUMMARY,
    MetadataFieldKey.LEGACY_ACTION_COUNT,
)


@pytest.fixture(autouse=True)
def _no_existing_corrections(monkeypatch):
    """By default, pretend no field has an authoritative correction on file.

    Individual tests override this via monkeypatch to exercise the
    overwrite-guard behavior.
    """
    monkeypatch.setattr(enricher_module, "has_correction_for_field", lambda *a, **k: False)


def _prime_schema(enricher: PaperlessEnricher, *, include_legacy: bool = False) -> None:
    definitions = [
        {"id": 10, "name": "Account Identifier", "data_type": "string"},
        {"id": 11, "name": "Invoice Number", "data_type": "string"},
        {"id": 1, "name": "Document Amount", "data_type": "float"},
        {"id": 4, "name": "Document Due Date", "data_type": "date"},
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
async def test_sync_document_amount_can_explicitly_clear_value(monkeypatch):
    monkeypatch.setattr(settings, "write_to_paperless", True)
    enricher = PaperlessEnricher()
    enricher.client = AsyncMock()
    _prime_schema(enricher)

    await enricher.sync_document_amount(42, None)

    enricher.client.update_custom_fields.assert_awaited_once_with(
        42,
        [{"field": 1, "value": None}],
    )


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


# ------------------------------------------------------------------
# Overwrite guard — authoritative corrections must not be clobbered
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_document_amount_skips_write_when_correction_exists(monkeypatch, enricher):
    monkeypatch.setattr(enricher_module, "has_correction_for_field", lambda *a, **k: True)
    _prime_schema(enricher)

    await enricher.sync_document_amount(42, 99.0)

    enricher.client.update_custom_fields.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_document_amount_writes_when_no_correction_exists(monkeypatch, enricher):
    monkeypatch.setattr(enricher_module, "has_correction_for_field", lambda *a, **k: False)
    _prime_schema(enricher)

    await enricher.sync_document_amount(42, 99.0)

    enricher.client.update_custom_fields.assert_awaited_once_with(
        42,
        [{"field": 1, "value": 99.0}],
    )


@pytest.mark.asyncio
async def test_sync_document_due_date_skips_write_when_correction_exists(monkeypatch, enricher):
    monkeypatch.setattr(enricher_module, "has_correction_for_field", lambda *a, **k: True)
    _prime_schema(enricher)

    await enricher.sync_document_due_date(42, date(2026, 8, 15))

    enricher.client.update_custom_fields.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_document_amount_action_queue_source_writes_through_existing_correction(
    monkeypatch, enricher
):
    """A human-driven Action Queue edit (source='action_queue') must never be
    silently skipped, even if a Metadata Correction API correction already
    exists for document_amount — unlike an automated pipeline pass."""
    monkeypatch.setattr(enricher_module, "has_correction_for_field", lambda *a, **k: True)
    recorded = Mock()
    monkeypatch.setattr(enricher_module, "create_extraction_correction", recorded)
    _prime_schema(enricher)

    await enricher.sync_document_amount(42, 150.0, source="action_queue")

    enricher.client.update_custom_fields.assert_awaited_once_with(
        42,
        [{"field": 1, "value": 150.0}],
    )
    # The Action Queue edit becomes the new authoritative correction, so
    # later automated passes and the Metadata Correction UI see this single
    # canonical value rather than the older, superseded one.
    recorded.assert_called_once()
    assert recorded.call_args.kwargs["document_id"] == 42
    assert recorded.call_args.kwargs["field_name"] == "document_amount"
    assert recorded.call_args.kwargs["corrected_value"] == "150.0"
    assert recorded.call_args.kwargs["correction_type"] == "action_queue_edit"


@pytest.mark.asyncio
async def test_sync_document_amount_action_queue_source_no_op_when_no_correction_exists(
    monkeypatch, enricher
):
    """When there's no prior correction, an Action Queue edit writes through
    without creating a redundant correction record."""
    monkeypatch.setattr(enricher_module, "has_correction_for_field", lambda *a, **k: False)
    recorded = Mock()
    monkeypatch.setattr(enricher_module, "create_extraction_correction", recorded)
    _prime_schema(enricher)

    await enricher.sync_document_amount(42, 150.0, source="action_queue")

    enricher.client.update_custom_fields.assert_awaited_once()
    recorded.assert_not_called()


@pytest.mark.asyncio
async def test_sync_document_due_date_action_queue_source_writes_through_existing_correction(
    monkeypatch, enricher
):
    monkeypatch.setattr(enricher_module, "has_correction_for_field", lambda *a, **k: True)
    recorded = Mock()
    monkeypatch.setattr(enricher_module, "create_extraction_correction", recorded)
    _prime_schema(enricher)

    await enricher.sync_document_due_date(42, date(2026, 9, 1), source="action_queue")

    enricher.client.update_custom_fields.assert_awaited_once()
    recorded.assert_called_once()
    assert recorded.call_args.kwargs["field_name"] == "document_due_date"
    assert recorded.call_args.kwargs["corrected_value"] == "2026-09-01"
    assert recorded.call_args.kwargs["correction_type"] == "action_queue_edit"


@pytest.mark.asyncio
async def test_sync_document_amount_no_correction_recorded_when_paperless_write_fails(
    monkeypatch, enricher
):
    """If the Paperless write itself fails, no correction record must be
    persisted — otherwise has_correction_for_field would return True forever
    for a value that was never actually written to Paperless, silently
    blocking every future automated write for that field."""
    monkeypatch.setattr(enricher_module, "has_correction_for_field", lambda *a, **k: True)
    recorded = Mock()
    monkeypatch.setattr(enricher_module, "create_extraction_correction", recorded)
    _prime_schema(enricher)
    enricher.client.update_custom_fields.side_effect = RuntimeError("Paperless unavailable")

    with pytest.raises(RuntimeError):
        await enricher.sync_document_amount(42, 150.0, source="action_queue")

    recorded.assert_not_called()


@pytest.mark.asyncio
async def test_sync_document_due_date_no_correction_recorded_when_paperless_write_fails(
    monkeypatch, enricher
):
    monkeypatch.setattr(enricher_module, "has_correction_for_field", lambda *a, **k: True)
    recorded = Mock()
    monkeypatch.setattr(enricher_module, "create_extraction_correction", recorded)
    _prime_schema(enricher)
    enricher.client.update_custom_fields.side_effect = RuntimeError("Paperless unavailable")

    with pytest.raises(RuntimeError):
        await enricher.sync_document_due_date(42, date(2026, 9, 1), source="action_queue")

    recorded.assert_not_called()


@pytest.mark.asyncio
async def test_enrich_document_no_corrections_recorded_when_paperless_write_fails(
    monkeypatch, enricher
):
    """The same guarantee applies to enrich_document's batched write: a
    failed Paperless call must not leave behind any superseding correction
    records for the fields that would have been written."""
    _prime_schema(enricher)
    monkeypatch.setattr(enricher_module, "has_correction_for_field", lambda *a, **k: True)
    recorded = Mock()
    monkeypatch.setattr(enricher_module, "create_extraction_correction", recorded)
    enricher.client.update_custom_fields.side_effect = RuntimeError("Paperless unavailable")

    with pytest.raises(RuntimeError):
        await enricher.enrich_document(
            42,
            {
                "amount": 50.0,
                "document_due_date": "2026-08-15",
                "extracted_data": {
                    "account_identifier": "ending 4321",
                    "reference_number": "INV-42",
                },
            },
            source="action_queue",
        )

    recorded.assert_not_called()


@pytest.mark.asyncio
async def test_sync_document_amount_clear_records_real_none_not_empty_string(monkeypatch, enricher):
    """Clearing amount=None via an Action Queue edit must record the
    correction's corrected_value as a real None, not an empty string, so it
    round-trips consistently through the canonical-value overlay."""
    monkeypatch.setattr(enricher_module, "has_correction_for_field", lambda *a, **k: True)
    recorded = Mock()
    monkeypatch.setattr(enricher_module, "create_extraction_correction", recorded)
    _prime_schema(enricher)

    await enricher.sync_document_amount(42, None, source="action_queue")

    recorded.assert_called_once()
    assert recorded.call_args.kwargs["corrected_value"] is None


@pytest.mark.asyncio
async def test_sync_document_due_date_clear_records_real_none_not_empty_string(
    monkeypatch, enricher
):
    monkeypatch.setattr(enricher_module, "has_correction_for_field", lambda *a, **k: True)
    recorded = Mock()
    monkeypatch.setattr(enricher_module, "create_extraction_correction", recorded)
    _prime_schema(enricher)

    await enricher.sync_document_due_date(42, None, source="action_queue")

    recorded.assert_called_once()
    assert recorded.call_args.kwargs["corrected_value"] is None


@pytest.mark.asyncio
async def test_enrich_document_skips_fields_with_existing_corrections(monkeypatch, enricher):
    """Only account_identifier has a correction on file — every other field
    (invoice_number, document_amount, document_due_date, action_status,
    action_analyzed) should still be written."""
    _prime_schema(enricher)
    monkeypatch.setattr(
        enricher_module,
        "has_correction_for_field",
        lambda document_id, field_name: field_name == "account_identifier",
    )

    await enricher.enrich_document(
        42,
        {
            "amount": 50.0,
            "document_due_date": "2026-08-15",
            "extracted_data": {
                "account_identifier": "ending 4321",
                "reference_number": "INV-42",
            },
        },
    )

    fields = enricher.client.update_custom_fields.await_args.args[1]
    values = {field["field"]: field["value"] for field in fields}
    assert 10 not in values  # account_identifier withheld
    assert values[11] == "INV-42"  # invoice_number still written
    assert values[1] == 50.0  # document_amount still written
    assert values[4] == "2026-08-15"  # document_due_date still written


@pytest.mark.asyncio
async def test_enrich_document_skips_all_correctable_fields_when_corrected(monkeypatch, enricher):
    _prime_schema(enricher)
    monkeypatch.setattr(enricher_module, "has_correction_for_field", lambda *a, **k: True)

    await enricher.enrich_document(
        42,
        {
            "amount": 50.0,
            "document_due_date": "2026-08-15",
            "extracted_data": {
                "account_identifier": "ending 4321",
                "reference_number": "INV-42",
            },
        },
    )

    fields = enricher.client.update_custom_fields.await_args.args[1]
    values = {field["field"]: field["value"] for field in fields}
    assert not {1, 4, 10, 11}.intersection(values)
    # Action-analyzed date is not correctable via the metadata API and is
    # always written.
    assert 3 in values


@pytest.mark.asyncio
async def test_enrich_document_action_queue_source_writes_through_all_corrections(
    monkeypatch, enricher
):
    """enrich_document(source='action_queue') represents a human-driven
    Action Queue re-sync, so it must write through every field even when
    corrections already exist -- and re-record each as the new authoritative
    correction."""
    _prime_schema(enricher)
    monkeypatch.setattr(enricher_module, "has_correction_for_field", lambda *a, **k: True)
    recorded = Mock()
    monkeypatch.setattr(enricher_module, "create_extraction_correction", recorded)

    await enricher.enrich_document(
        42,
        {
            "amount": 50.0,
            "document_due_date": "2026-08-15",
            "extracted_data": {
                "account_identifier": "ending 4321",
                "reference_number": "INV-42",
            },
        },
        source="action_queue",
    )

    fields = enricher.client.update_custom_fields.await_args.args[1]
    values = {field["field"]: field["value"] for field in fields}
    assert values[10] == "ending 4321"
    assert values[11] == "INV-42"
    assert values[1] == 50.0
    assert values[4] == "2026-08-15"
    assert {call.kwargs["field_name"] for call in recorded.call_args_list} == {
        "account_identifier",
        "invoice_number",
        "document_amount",
        "document_due_date",
    }
