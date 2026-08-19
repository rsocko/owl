"""Tests for governed durable EOB Paperless projection."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from doc_intelligence_hub.core.paperless import (
    MetadataFieldKey,
    MetadataSchemaError,
    resolve_metadata_schema,
)
from doc_intelligence_hub.modules.eob_matching.enricher import (
    CUSTOM_FIELD_DEFINITIONS,
    EOBEnricher,
)


def _schema():
    definitions = [
        {"id": 1, "name": "Account Identifier", "data_type": "string"},
        {"id": 2, "name": "Patient Name", "data_type": "string"},
        {"id": 3, "name": "Provider Name", "data_type": "string"},
        {"id": 4, "name": "Date of Service", "data_type": "date"},
        {"id": 5, "name": "Patient Responsibility", "data_type": "monetary"},
        {"id": 6, "name": "Claim Number", "data_type": "string"},
        {"id": 7, "name": "Invoice Number", "data_type": "string"},
    ]
    return resolve_metadata_schema(
        definitions,
        (
            MetadataFieldKey.ACCOUNT_IDENTIFIER,
            MetadataFieldKey.PATIENT_NAME,
            MetadataFieldKey.PROVIDER_NAME,
            MetadataFieldKey.DATE_OF_SERVICE,
            MetadataFieldKey.PATIENT_RESPONSIBILITY,
            MetadataFieldKey.CLAIM_NUMBER,
            MetadataFieldKey.INVOICE_NUMBER,
        ),
    )


def _eob(confidence: float = 0.98):
    return SimpleNamespace(
        patient_name="Ada Patient",
        provider_name="Example Clinic",
        date_of_service=date(2026, 8, 1),
        total_patient_responsibility=125.5,
        claim_number="CLAIM-42",
        policy_number="POLICY-12345678",
        extraction_confidence=confidence,
    )


def _bill(confidence: float = 0.98):
    return SimpleNamespace(
        patient_name="Ada Patient",
        provider_name="Example Clinic",
        date_of_service=date(2026, 8, 1),
        invoice_number="INV-9",
        extraction_confidence=confidence,
    )


def _enricher() -> tuple[AsyncMock, EOBEnricher]:
    client = AsyncMock()
    enricher = EOBEnricher(client, audit_session=MagicMock())
    enricher._schema = _schema()
    return client, enricher


def test_setup_definitions_exclude_analytical_and_triage_fields() -> None:
    names = {definition["name"] for definition in CUSTOM_FIELD_DEFINITIONS}

    assert names == {
        "Account Identifier",
        "Patient Name",
        "Provider Name",
        "Date of Service",
        "Patient Responsibility",
        "Claim Number",
        "Invoice Number",
    }
    assert not any(
        term in name
        for name in names
        for term in ("Match", "Confidence", "Score", "Analyzed", "Triage")
    )


@pytest.mark.asyncio
async def test_unconfirmed_projection_writes_only_high_confidence_durable_fields() -> None:
    client, enricher = _enricher()

    records = await enricher.link_match(
        100,
        200,
        87.5,
        "HIGH",
        eob=_eob(),
        bill=_bill(),
    )

    assert client.update_custom_fields.await_count == 2
    eob_updates = client.update_custom_fields.await_args_list[0].args[1]
    bill_updates = client.update_custom_fields.await_args_list[1].args[1]
    assert {item["field"]: item["value"] for item in eob_updates} == {
        1: "POLICY-12345678",
        4: "2026-08-01",
        5: 125.5,
    }
    assert {item["field"]: item["value"] for item in bill_updates} == {
        4: "2026-08-01",
    }
    assert {record.stable_key for record in records} == {
        "account_identifier",
        "date_of_service",
        "patient_responsibility",
    }


@pytest.mark.asyncio
async def test_confirmed_projection_applies_sensitive_field_policy() -> None:
    client, enricher = _enricher()

    await enricher.link_match(
        100,
        200,
        10.0,
        "LOW",
        eob=_eob(),
        bill=_bill(),
        confirmed=True,
        actor="user",
        reason="match_confirmed",
    )

    eob_fields = {item["field"] for item in client.update_custom_fields.await_args_list[0].args[1]}
    bill_fields = {item["field"] for item in client.update_custom_fields.await_args_list[1].args[1]}
    assert eob_fields == {1, 2, 3, 4, 5, 6}
    assert bill_fields == {2, 3, 4, 7}


@pytest.mark.asyncio
async def test_account_identifier_reuses_policy_and_audit_is_masked() -> None:
    client, enricher = _enricher()

    records = await enricher.link_match(
        100,
        200,
        99.0,
        "HIGH",
        eob=_eob(),
        bill=None,
    )

    account_record = next(record for record in records if record.stable_key == "account_identifier")
    account_update = client.update_custom_fields.await_args.args[1][2]
    assert account_update == {"field": 1, "value": "POLICY-12345678"}
    assert account_record.value_display == "policy ending 5678"
    assert "POLICY-12345678" not in account_record.value_display
    audit_rows = [
        row
        for call in enricher.audit_session.add_all.call_args_list
        for row in call.args[0]
    ]
    stored_account_audit = next(
        row for row in audit_rows if row.stable_key == "account_identifier"
    )
    assert stored_account_audit.value_display == "policy ending 5678"
    assert stored_account_audit.status == "applied"
    assert enricher.audit_session.commit.call_count == 2


@pytest.mark.asyncio
async def test_confirmed_low_confidence_account_does_not_block_other_fields() -> None:
    client, enricher = _enricher()

    records = await enricher.link_match(
        100,
        200,
        99.0,
        "HIGH",
        eob=_eob(0.0),
        bill=None,
        confirmed=True,
    )

    fields = {item["field"] for item in client.update_custom_fields.await_args.args[1]}
    assert fields == {2, 3, 4, 5, 6}
    assert "account_identifier" not in {record.stable_key for record in records}


@pytest.mark.asyncio
async def test_low_confidence_unconfirmed_values_do_not_reach_paperless() -> None:
    client, enricher = _enricher()

    records = await enricher.link_match(
        100,
        200,
        50.0,
        "LOW",
        eob=_eob(0.5),
        bill=_bill(0.5),
    )

    assert records == ()
    client.update_custom_fields.assert_not_awaited()


@pytest.mark.asyncio
async def test_projection_refuses_to_write_without_durable_audit_storage() -> None:
    client = AsyncMock()
    enricher = EOBEnricher(client)
    enricher._schema = _schema()

    with pytest.raises(MetadataSchemaError, match="durable audit"):
        await enricher.link_match(
            100,
            200,
            99.0,
            "HIGH",
            eob=_eob(),
            bill=None,
        )

    client.update_custom_fields.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_paperless_write_records_failed_audit_outcome() -> None:
    client, enricher = _enricher()
    client.update_custom_fields.side_effect = RuntimeError("write failed")

    with pytest.raises(RuntimeError, match="write failed"):
        await enricher.link_match(
            100,
            200,
            99.0,
            "HIGH",
            eob=_eob(),
            bill=None,
        )

    rows = enricher.audit_session.add_all.call_args.args[0]
    assert rows
    assert {row.status for row in rows} == {"failed"}
    assert enricher.audit_session.commit.call_count == 2


@pytest.mark.asyncio
async def test_unmatched_state_remains_local() -> None:
    client, enricher = _enricher()

    await enricher.mark_unmatched(100, "EOB")

    client.update_custom_fields.assert_not_awaited()
