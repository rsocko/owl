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
    assert "document_classification" not in metadata.VALID_FIELD_NAMES


@pytest.mark.asyncio
async def test_writeback_targets_canonical_field_only(monkeypatch) -> None:
    client = AsyncMock()
    client.get_document.return_value = {
        "id": 100,
        "modified": "2026-10-18T10:00:00Z",
        "custom_fields": [{"field": 10, "value": "OLD-100"}],
    }
    client.list_custom_fields.return_value = [
        {"id": 10, "name": "Claim Number", "data_type": "string"},
        {"id": 11, "name": "di_claim_number", "data_type": "string"},
    ]
    client.update_custom_fields.return_value = {
        **client.get_document.return_value,
        "modified": "2026-10-18T10:01:00Z",
    }
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

    result = await metadata.writeback_to_paperless(
        100,
        metadata.WritebackRequest(
            expected_modified="2026-10-18T10:00:00Z",
            base_values={"claim_number": "OLD-100"},
        ),
        object(),
    )

    client.update_custom_fields.assert_awaited_once_with(
        100,
        [{"field": 10, "value": "SAMPLE-200"}],
        expected_modified="2026-10-18T10:00:00Z",
    )
    assert result["written_fields"] == ["claim_number"]


@pytest.mark.asyncio
async def test_document_amount_and_due_date_are_correctable(monkeypatch) -> None:
    """DOCUMENT_AMOUNT/DOCUMENT_DUE_DATE are in the canonical registry but were
    previously missing from the API allowlist — verify /correct accepts them
    and /writeback pushes them to their canonical Paperless custom fields."""
    assert "document_amount" in metadata.VALID_FIELD_NAMES
    assert "document_due_date" in metadata.VALID_FIELD_NAMES

    monkeypatch.setattr(
        metadata,
        "create_extraction_correction",
        lambda **kwargs: {"id": "c1", **kwargs},
    )
    client = AsyncMock()
    client.get_document.return_value = {"id": 100, "correspondent": None, "content": None}
    client.list_correspondents.return_value = []
    monkeypatch.setattr(metadata, "make_paperless_client", lambda request: client)
    correct_result = await metadata.correct_field(
        100,
        metadata.CorrectFieldRequest(field_name="document_amount", corrected_value="125.00"),
        object(),
    )
    assert correct_result["correction"]["field_name"] == "document_amount"

    client.list_custom_fields.return_value = [
        {"id": 30, "name": "Document Amount", "data_type": "float"},
        {"id": 31, "name": "Document Due Date", "data_type": "date"},
    ]
    client.get_document.return_value = {
        "id": 100,
        "modified": "2026-10-18T11:00:00Z",
        "custom_fields": [
            {"field": 30, "value": 100.0},
            {"field": 31, "value": "2026-08-01"},
        ],
    }
    client.update_custom_fields.return_value = {
        **client.get_document.return_value,
        "modified": "2026-10-18T11:01:00Z",
    }
    monkeypatch.setattr(
        metadata,
        "get_corrections_for_document",
        lambda document_id: [
            {"field_name": "document_amount", "corrected_value": "125.00"},
            {"field_name": "document_due_date", "corrected_value": "2026-08-15"},
        ],
    )

    result = await metadata.writeback_to_paperless(
        100,
        metadata.WritebackRequest(
            expected_modified="2026-10-18T11:00:00Z",
            base_values={
                "document_amount": 100.0,
                "document_due_date": "2026-08-01",
            },
        ),
        object(),
    )

    client.update_custom_fields.assert_awaited_once()
    written = client.update_custom_fields.await_args.args[1]
    values = {update["field"]: update["value"] for update in written}
    assert values[30] == 125.0
    assert values[31] == "2026-08-15"
    assert set(result["written_fields"]) == {"document_amount", "document_due_date"}


def test_writeback_rejects_external_field_change_with_diff(
    client, mock_paperless, monkeypatch
) -> None:
    mock_paperless.get_document.return_value = {
        "id": 100,
        "modified": "revision-2",
        "custom_fields": [{"field": 10, "value": "PAPERLESS-200"}],
    }
    mock_paperless.list_custom_fields.return_value = [
        {"id": 10, "name": "Claim Number", "data_type": "string"},
    ]
    monkeypatch.setattr(
        metadata,
        "get_corrections_for_document",
        lambda document_id: [
            {"field_name": "claim_number", "corrected_value": "OWL-200"},
        ],
    )

    response = client.post(
        "/api/metadata/100/writeback",
        json={
            "expected_modified": "revision-1",
            "base_values": {"claim_number": "LOADED-100"},
            "initiated_by": "owl-web:test",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["conflicts"] == [
        {
            "field": "claim_number",
            "label": "Claim Number",
            "loaded_value": "LOADED-100",
            "paperless_value": "PAPERLESS-200",
            "owl_value": "OWL-200",
        }
    ]
    mock_paperless.update_custom_fields.assert_not_awaited()


def test_writeback_matching_newer_value_is_idempotent(client, mock_paperless, monkeypatch) -> None:
    mock_paperless.get_document.return_value = {
        "id": 100,
        "modified": "revision-2",
        "custom_fields": [{"field": 10, "value": "OWL-200"}],
    }
    mock_paperless.list_custom_fields.return_value = [
        {"id": 10, "name": "Claim Number", "data_type": "string"},
    ]
    monkeypatch.setattr(
        metadata,
        "get_corrections_for_document",
        lambda document_id: [
            {"field_name": "claim_number", "corrected_value": "OWL-200"},
        ],
    )

    response = client.post(
        "/api/metadata/100/writeback",
        json={
            "expected_modified": "revision-1",
            "base_values": {"claim_number": "LOADED-100"},
        },
    )

    assert response.status_code == 200
    assert response.json()["idempotent_replay"] is True
    mock_paperless.update_custom_fields.assert_not_awaited()


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
async def test_correct_field_resolves_correspondent_and_label_anchor(monkeypatch) -> None:
    client = AsyncMock()
    client.get_document.return_value = {
        "id": 100,
        "correspondent": 7,
        "content": "Statement Summary\nTotal Due: $142.50\nThank you.",
    }
    client.list_correspondents.return_value = [
        {"id": 7, "name": "City Utilities"},
        {"id": 9, "name": "Other Co"},
    ]
    monkeypatch.setattr(metadata, "make_paperless_client", lambda request: client)

    captured: dict = {}

    def fake_create_extraction_correction(**kwargs):
        captured.update(kwargs)
        return {"id": "abc123", **kwargs}

    monkeypatch.setattr(metadata, "create_extraction_correction", fake_create_extraction_correction)

    body = metadata.CorrectFieldRequest(
        field_name="document_amount",
        corrected_value="$142.50",
        original_value="$50.00",
    )
    await metadata.correct_field(100, body, object())

    assert captured["correspondent"] == "City Utilities"
    assert captured["label_anchor"] == "Total Due:"
    assert captured["correction_type"] == "corrected"


@pytest.mark.asyncio
async def test_confirm_field_resolves_correspondent_and_label_anchor(monkeypatch) -> None:
    client = AsyncMock()
    client.get_document.return_value = {
        "id": 200,
        "correspondent": "Chase Visa",  # Paperless sometimes already resolves to a name
        "content": "Claim Number: CLM-4321",
    }
    monkeypatch.setattr(metadata, "make_paperless_client", lambda request: client)

    captured: dict = {}

    def fake_create_extraction_correction(**kwargs):
        captured.update(kwargs)
        return {"id": "def456", **kwargs}

    monkeypatch.setattr(metadata, "create_extraction_correction", fake_create_extraction_correction)

    body = metadata.ConfirmFieldRequest(
        field_name="claim_number",
        current_value="CLM-4321",
    )
    await metadata.confirm_field(200, body, object())

    assert captured["correspondent"] == "Chase Visa"
    assert captured["label_anchor"] == "Claim Number:"
    client.list_correspondents.assert_not_awaited()


@pytest.mark.asyncio
async def test_correct_field_tolerates_document_fetch_failure(monkeypatch) -> None:
    client = AsyncMock()
    client.get_document.side_effect = RuntimeError("Paperless unavailable")
    monkeypatch.setattr(metadata, "make_paperless_client", lambda request: client)

    captured: dict = {}

    def fake_create_extraction_correction(**kwargs):
        captured.update(kwargs)
        return {"id": "ghi789", **kwargs}

    monkeypatch.setattr(metadata, "create_extraction_correction", fake_create_extraction_correction)

    body = metadata.CorrectFieldRequest(
        field_name="provider_name", corrected_value="Sample Provider"
    )
    result = await metadata.correct_field(300, body, object())

    assert captured["correspondent"] is None
    assert captured["label_anchor"] is None
    assert result["correction"]["id"] == "ghi789"


@pytest.mark.asyncio
async def test_account_identifier_is_exact_only_in_named_review_context(monkeypatch) -> None:
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
    assert account["account_identifier_display"] == "SENSITIVE123456"
    assert account["value"] == "SENSITIVE123456"
    assert review["document_summary"]["account_identifier_display"] == "SENSITIVE123456"


@pytest.mark.asyncio
async def test_account_correction_writes_exact_only_to_paperless(monkeypatch) -> None:
    client = AsyncMock()
    client.get_document.return_value = {
        "id": 100,
        "correspondent": "Sample Provider",
        "content": "Member ID: MEMBER123456",
        "modified": "2026-10-18T12:00:00Z",
        "custom_fields": [{"field": 10, "value": "OLDMEMBER9876"}],
    }
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
        notes="must not persist exact values",
        expected_modified="2026-10-18T12:00:00Z",
    )

    result = await metadata.correct_field(100, body, object())

    client.update_custom_fields.assert_awaited_once_with(
        100,
        [{"field": 10, "value": "MEMBER123456"}],
        expected_modified="2026-10-18T12:00:00Z",
    )
    assert persisted["corrected_value"] == "member ending 3456"
    assert persisted["original_value"] == "member ending 9876"
    assert persisted["notes"] is None
    assert result["written_to_paperless"] is True


@pytest.mark.asyncio
async def test_account_correction_matching_newer_value_is_idempotent(monkeypatch) -> None:
    client = AsyncMock()
    client.get_document.return_value = {
        "id": 100,
        "correspondent": "Sample Provider",
        "content": "Member ID: MEMBER123456",
        "modified": "revision-2",
        "custom_fields": [{"field": 10, "value": "MEMBER123456"}],
    }
    client.list_custom_fields.return_value = [
        {"id": 10, "name": "Account Identifier", "data_type": "string"}
    ]
    existing = {
        "id": "existing-correction",
        "field_name": "account_identifier",
        "corrected_value": "member ending 3456",
    }
    create_correction = AsyncMock()
    monkeypatch.setattr(metadata, "make_paperless_client", lambda request: client)
    monkeypatch.setattr(metadata, "get_corrections_for_document", lambda document_id: [existing])
    monkeypatch.setattr(metadata, "create_extraction_correction", create_correction)

    result = await metadata.correct_field(
        100,
        metadata.CorrectFieldRequest(
            field_name="account_identifier",
            corrected_value="MEMBER123456",
            original_value="OLDMEMBER9876",
            identifier_class="member",
            expected_modified="revision-1",
        ),
        object(),
    )

    assert result["idempotent_replay"] is True
    assert result["correction"] == existing
    client.update_custom_fields.assert_not_awaited()
    create_correction.assert_not_awaited()
