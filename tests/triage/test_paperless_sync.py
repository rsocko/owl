"""Tests for registry-backed triage Paperless synchronization."""

from unittest.mock import AsyncMock

import pytest

from doc_intelligence_hub.core.paperless import MetadataFieldKey, resolve_metadata_schema
from doc_intelligence_hub.modules.triage.paperless_sync import (
    _resolve_field_ids,
    _update_doc_fields,
)


@pytest.mark.asyncio
async def test_resolve_field_ids_returns_stable_keys_and_canonical_ids() -> None:
    client = AsyncMock()
    client.list_custom_fields.return_value = [
        {"id": 10, "name": "Series Name", "data_type": "string"},
        {"id": 11, "name": "Account Identifier", "data_type": "string"},
        {"id": 12, "name": "di_account_id", "data_type": "string"},
    ]

    assert await _resolve_field_ids(client) == {
        MetadataFieldKey.SERIES_NAME: 10,
        MetadataFieldKey.ACCOUNT_IDENTIFIER: 11,
    }


@pytest.mark.asyncio
async def test_update_doc_fields_uses_resolved_ids() -> None:
    client = AsyncMock()
    schema = resolve_metadata_schema(
        [
            {"id": 10, "name": "Series Name", "data_type": "string"},
            {"id": 11, "name": "Account Identifier", "data_type": "string"},
        ],
        (MetadataFieldKey.SERIES_NAME, MetadataFieldKey.ACCOUNT_IDENTIFIER),
    )

    await _update_doc_fields(
        client,
        "100",
        schema,
        series_name=" Sample Series ",
        account_identifier=" ending 4321 ",
    )

    client.update_custom_fields.assert_awaited_once_with(
        100,
        [
            {"field": 10, "value": "Sample Series"},
            {"field": 11, "value": "ending 4321"},
        ],
    )


@pytest.mark.asyncio
async def test_update_doc_fields_rejects_unmasked_account_identifier() -> None:
    client = AsyncMock()
    schema = resolve_metadata_schema(
        [{"id": 11, "name": "Account Identifier", "data_type": "string"}],
        (MetadataFieldKey.SERIES_NAME, MetadataFieldKey.ACCOUNT_IDENTIFIER),
    )

    with pytest.raises(ValueError, match="masked"):
        await _update_doc_fields(
            client,
            "100",
            schema,
            account_identifier="SAMPLE123456789",
        )

    client.update_custom_fields.assert_not_awaited()
