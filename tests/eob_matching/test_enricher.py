"""Tests for registry-backed EOB Paperless enrichment."""

from unittest.mock import AsyncMock

import pytest

from doc_intelligence_hub.core.paperless import (
    MetadataFieldKey,
    resolve_metadata_schema,
)
from doc_intelligence_hub.modules.eob_matching.enricher import EOBEnricher


def _schema():
    return resolve_metadata_schema(
        [
            {
                "id": 1,
                "name": "EOB Match Status",
                "data_type": "select",
                "extra_data": {
                    "select_options": [
                        {"id": 10, "label": "matched"},
                        {"id": 11, "label": "unmatched"},
                        {"id": 12, "label": "review_needed"},
                    ]
                },
            },
            {"id": 2, "name": "EOB Match Score", "data_type": "decimal"},
            {
                "id": 3,
                "name": "EOB Match Confidence",
                "data_type": "select",
                "extra_data": {
                    "select_options": [
                        {"id": 20, "label": "HIGH"},
                        {"id": 21, "label": "MEDIUM"},
                        {"id": 22, "label": "LOW"},
                    ]
                },
            },
            {"id": 4, "name": "EOB Matched Document", "data_type": "document_link"},
            {
                "id": 5,
                "name": "EOB Document Type",
                "data_type": "select",
                "extra_data": {
                    "select_options": [
                        {"id": 30, "label": "EOB"},
                        {"id": 31, "label": "BILL"},
                    ]
                },
            },
            {"id": 6, "name": "EOB Patient Responsibility", "data_type": "decimal"},
            {"id": 7, "name": "EOB Analyzed", "data_type": "date"},
        ],
        (
            MetadataFieldKey.EOB_MATCH_STATUS,
            MetadataFieldKey.EOB_MATCH_SCORE,
            MetadataFieldKey.EOB_MATCH_CONFIDENCE,
            MetadataFieldKey.EOB_MATCHED_DOCUMENT,
            MetadataFieldKey.EOB_DOCUMENT_TYPE,
            MetadataFieldKey.EOB_PATIENT_RESPONSIBILITY,
            MetadataFieldKey.EOB_ANALYZED,
        ),
    )


@pytest.mark.asyncio
async def test_link_match_uses_registry_ids_and_deployed_options() -> None:
    client = AsyncMock()
    enricher = EOBEnricher(client)
    enricher._schema = _schema()

    await enricher.link_match(100, 200, 87.5, "HIGH", patient_responsibility=125.5)

    assert client.update_custom_fields.await_count == 2
    eob_updates = client.update_custom_fields.await_args_list[0].args[1]
    bill_updates = client.update_custom_fields.await_args_list[1].args[1]
    assert {item["field"]: item["value"] for item in eob_updates} == {
        1: 10,
        2: 87.5,
        3: 20,
        4: 200,
        5: 30,
        6: 125.5,
        7: eob_updates[5]["value"],
    }
    assert {item["field"]: item["value"] for item in bill_updates}[5] == 31


@pytest.mark.asyncio
async def test_mark_unmatched_uses_registered_fields() -> None:
    client = AsyncMock()
    enricher = EOBEnricher(client)
    enricher._schema = _schema()

    await enricher.mark_unmatched(100, "EOB")

    updates = client.update_custom_fields.await_args.args[1]
    values = {item["field"]: item["value"] for item in updates}
    assert values[1] == 11
    assert values[5] == 30
