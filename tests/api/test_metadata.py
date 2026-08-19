"""Tests for registry-backed metadata correction API behavior."""

from unittest.mock import AsyncMock

import pytest

from doc_intelligence_hub.api.routers import metadata


@pytest.mark.asyncio
async def test_get_document_metadata_reads_alias_and_reports_conflict(monkeypatch) -> None:
    client = AsyncMock()
    client.get_document.return_value = {
        "id": 100,
        "title": "Synthetic Document",
        "custom_fields": [
            {"field": 1, "value": "Sample Provider"},
            {"field": 2, "value": "Legacy Provider"},
            {"field": 4, "value": "SAMPLE-100"},
        ],
    }
    client.list_custom_fields.return_value = [
        {"id": 1, "name": "Provider Name", "data_type": "string"},
        {"id": 2, "name": "di_provider_name", "data_type": "string"},
        {"id": 4, "name": "di_claim_number", "data_type": "string"},
    ]
    monkeypatch.setattr(metadata, "make_paperless_client", lambda request: client)
    monkeypatch.setattr(metadata, "get_corrections_for_document", lambda document_id: [])

    result = await metadata.get_document_metadata(100, object())

    fields = {item["field_name"]: item for item in result["extracted_fields"]}
    assert fields["provider_name"]["value"] == "Sample Provider"
    assert fields["provider_name"]["conflict"] is True
    assert fields["claim_number"]["value"] == "SAMPLE-100"
    assert fields["claim_number"]["source_field"] == "di_claim_number"
    assert result["metadata_conflicts"][0]["field_name"] == "provider_name"


@pytest.mark.asyncio
async def test_writeback_targets_canonical_field_only(monkeypatch) -> None:
    client = AsyncMock()
    client.list_custom_fields.return_value = [
        {"id": 10, "name": "Claim Number", "data_type": "string"},
        {"id": 11, "name": "di_claim_number", "data_type": "string"},
    ]
    monkeypatch.setattr(metadata, "make_paperless_client", lambda request: client)
    monkeypatch.setattr(
        metadata,
        "get_corrections_for_document",
        lambda document_id: [
            {
                "field_name": "claim_number",
                "corrected_value": "SAMPLE-200",
            }
        ],
    )

    result = await metadata.writeback_to_paperless(100, object())

    client.update_custom_fields.assert_awaited_once_with(
        100,
        [{"field": 10, "value": "SAMPLE-200"}],
    )
    assert result["written_fields"] == ["claim_number"]


@pytest.mark.asyncio
async def test_get_document_metadata_retains_invalid_legacy_value(monkeypatch) -> None:
    client = AsyncMock()
    client.get_document.return_value = {
        "id": 100,
        "title": "Synthetic Document",
        "custom_fields": [{"field": 5, "value": "$125.00"}],
    }
    client.list_custom_fields.return_value = [
        {"id": 5, "name": "di_patient_resp", "data_type": "monetary"}
    ]
    monkeypatch.setattr(metadata, "make_paperless_client", lambda request: client)
    monkeypatch.setattr(metadata, "get_corrections_for_document", lambda document_id: [])

    result = await metadata.get_document_metadata(100, object())

    fields = {item["field_name"]: item for item in result["extracted_fields"]}
    assert fields["patient_responsibility"]["value"] == "$125.00"
    assert fields["patient_responsibility"]["validation_error"]
    assert result["metadata_value_diagnostics"][0]["field_name"] == "patient_responsibility"


@pytest.mark.asyncio
async def test_account_identifier_is_only_masked_in_named_review_context(monkeypatch) -> None:
    client = AsyncMock()
    client.get_document.return_value = {
        "id": 100,
        "title": "Synthetic Document",
        "custom_fields": [{"field": 6, "value": "SENSITIVE123456"}],
    }
    client.list_custom_fields.return_value = [
        {"id": 6, "name": "Account Identifier", "data_type": "string"}
    ]
    monkeypatch.setattr(metadata, "make_paperless_client", lambda request: client)
    monkeypatch.setattr(metadata, "get_corrections_for_document", lambda document_id: [])

    general = await metadata.get_document_metadata(100, object())
    review = await metadata.get_document_metadata(100, object(), context="account_review")

    assert all(field["field_name"] != "account_identifier" for field in general["extracted_fields"])
    account = next(
        field for field in review["extracted_fields"] if field["field_name"] == "account_identifier"
    )
    assert account["account_identifier_display"] == "ending 3456"
    assert "value" not in account
    assert "SENSITIVE123456" not in str(review)


@pytest.mark.asyncio
async def test_account_correction_writes_exact_only_to_paperless(monkeypatch) -> None:
    client = AsyncMock()
    client.list_custom_fields.return_value = [
        {"id": 10, "name": "Account Identifier", "data_type": "string"}
    ]
    persisted: dict = {}

    def capture_correction(**kwargs):
        persisted.update(kwargs)
        return kwargs

    monkeypatch.setattr(metadata, "make_paperless_client", lambda request: client)
    monkeypatch.setattr(metadata, "create_extraction_correction", capture_correction)
    body = metadata.CorrectFieldRequest(
        field_name="account_identifier",
        corrected_value="MEMBER123456",
        original_value="OLDMEMBER9876",
        identifier_class="member",
        notes="Do not retain MEMBER123456",
    )

    result = await metadata.correct_field(100, body, object())

    client.update_custom_fields.assert_awaited_once_with(
        100,
        [{"field": 10, "value": "MEMBER123456"}],
    )
    assert persisted["corrected_value"] == "member ending 3456"
    assert persisted["original_value"] == "member ending 9876"
    assert persisted["notes"] is None
    assert "MEMBER123456" not in str(result)
