"""Acceptance tests for the typed Paperless metadata registry."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from doc_intelligence_hub.core.paperless import (
    PAPERLESS_METADATA_REGISTRY,
    MetadataCreatePolicy,
    MetadataDiagnosticCode,
    MetadataFieldKey,
    MetadataSchemaError,
    MetadataValueError,
    PaperlessMetadataResolver,
    build_metadata_update,
    get_metadata_field_spec,
    resolve_metadata_schema,
    resolve_metadata_value,
)

CANONICAL_KEYS = (
    MetadataFieldKey.ACCOUNT_IDENTIFIER,
    MetadataFieldKey.PATIENT_NAME,
    MetadataFieldKey.PROVIDER_NAME,
    MetadataFieldKey.DATE_OF_SERVICE,
    MetadataFieldKey.PATIENT_RESPONSIBILITY,
    MetadataFieldKey.CLAIM_NUMBER,
    MetadataFieldKey.INVOICE_NUMBER,
    MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE,
)


def _definition(
    field_id: int,
    key: MetadataFieldKey,
    *,
    alias: bool = False,
    data_type: str | None = None,
    options: tuple[tuple[int, str], ...] = (),
) -> dict:
    spec = get_metadata_field_spec(key)
    result = {
        "id": field_id,
        "name": spec.aliases[0] if alias else spec.canonical_name,
        "data_type": data_type or spec.compatible_types[0].value,
    }
    if options:
        result["extra_data"] = {
            "select_options": [{"id": option_id, "label": label} for option_id, label in options]
        }
    return result


def test_registry_contains_all_design_fields_without_di_prefix() -> None:
    assert set(CANONICAL_KEYS) <= set(PAPERLESS_METADATA_REGISTRY)
    assert all(
        not get_metadata_field_spec(key).canonical_name.startswith("di_") for key in CANONICAL_KEYS
    )
    assert all(get_metadata_field_spec(key).compatibility_read for key in CANONICAL_KEYS)


def test_relationship_projection_fields_are_creatable() -> None:
    related = get_metadata_field_spec(MetadataFieldKey.RELATED_DOCUMENTS)
    summary = get_metadata_field_spec(MetadataFieldKey.RELATIONSHIP_SUMMARY)

    assert related.canonical_name == "Related Document IDs"
    assert related.create_policy is MetadataCreatePolicy.IF_MISSING
    assert related.create_definition()["data_type"] == "string"
    assert summary.create_policy is MetadataCreatePolicy.IF_MISSING


def test_document_classification_is_an_internal_key_alias() -> None:
    spec = get_metadata_field_spec("document_classification")
    assert spec.key is MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE
    assert spec.canonical_name == "Normalized Document Type"


def test_canonical_nonblank_value_wins() -> None:
    definitions = [
        _definition(10, MetadataFieldKey.PROVIDER_NAME),
        _definition(11, MetadataFieldKey.PROVIDER_NAME, alias=True),
    ]
    schema = resolve_metadata_schema(definitions, (MetadataFieldKey.PROVIDER_NAME,))

    result = resolve_metadata_value(
        MetadataFieldKey.PROVIDER_NAME,
        [{"field": 10, "value": " Sample Provider "}, {"field": 11, "value": ""}],
        schema,
    )

    assert result.value == "Sample Provider"
    assert result.source_id == 10
    assert result.conflict is None


def test_alias_only_value_remains_readable() -> None:
    schema = resolve_metadata_schema(
        [_definition(11, MetadataFieldKey.CLAIM_NUMBER, alias=True)],
        (MetadataFieldKey.CLAIM_NUMBER,),
    )

    result = resolve_metadata_value(
        MetadataFieldKey.CLAIM_NUMBER,
        [{"field": "11", "value": " SAMPLE-100 "}],
        schema,
    )

    assert result.value == "SAMPLE-100"
    assert result.source_name == "di_claim_number"


def test_blank_canonical_falls_back_to_ordered_alias() -> None:
    schema = resolve_metadata_schema(
        [
            _definition(20, MetadataFieldKey.ACCOUNT_IDENTIFIER),
            _definition(21, MetadataFieldKey.ACCOUNT_IDENTIFIER, alias=True),
        ],
        (MetadataFieldKey.ACCOUNT_IDENTIFIER,),
    )

    result = resolve_metadata_value(
        MetadataFieldKey.ACCOUNT_IDENTIFIER,
        [{"field": 20, "value": "  "}, {"field": 21, "value": "ending 4321"}],
        schema,
    )

    assert result.value == "ending 4321"
    assert result.source_id == 21


def test_equal_normalized_values_do_not_conflict() -> None:
    schema = resolve_metadata_schema(
        [
            _definition(30, MetadataFieldKey.PATIENT_RESPONSIBILITY),
            _definition(31, MetadataFieldKey.PATIENT_RESPONSIBILITY, alias=True),
        ],
        (MetadataFieldKey.PATIENT_RESPONSIBILITY,),
    )

    result = resolve_metadata_value(
        MetadataFieldKey.PATIENT_RESPONSIBILITY,
        [{"field": 30, "value": "125.00"}, {"field": 31, "value": 125}],
        schema,
    )

    assert result.value == Decimal("125")
    assert result.conflict is None


def test_conflict_is_reported_and_canonical_wins() -> None:
    schema = resolve_metadata_schema(
        [
            _definition(40, MetadataFieldKey.INVOICE_NUMBER),
            _definition(41, MetadataFieldKey.INVOICE_NUMBER, alias=True),
        ],
        (MetadataFieldKey.INVOICE_NUMBER,),
    )

    result = resolve_metadata_value(
        MetadataFieldKey.INVOICE_NUMBER,
        [{"field": 40, "value": "INV-NEW"}, {"field": 41, "value": "INV-OLD"}],
        schema,
    )

    assert result.value == "INV-NEW"
    assert result.conflict is not None
    assert result.conflict.conflicting_sources == (("di_invoice_number", "INV-OLD"),)


def test_select_option_id_and_legacy_label_normalize_equally() -> None:
    schema = resolve_metadata_schema(
        [
            _definition(
                45,
                MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE,
                data_type="select",
                options=((1, "Statement"), (2, "Invoice")),
            ),
            _definition(
                46,
                MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE,
                alias=True,
                data_type="select",
            ),
        ],
        (MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE,),
    )

    result = resolve_metadata_value(
        MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE,
        [{"field": 45, "value": 2}, {"field": 46, "value": "Invoice"}],
        schema,
    )

    assert result.value == "Invoice"
    assert result.conflict is None
    assert result.validation_error is None


def test_missing_and_incompatible_fields_are_diagnostics() -> None:
    schema = resolve_metadata_schema(
        [
            _definition(
                50,
                MetadataFieldKey.DATE_OF_SERVICE,
                data_type="string",
            )
        ],
        (MetadataFieldKey.DATE_OF_SERVICE, MetadataFieldKey.PATIENT_NAME),
    )

    codes = {(item.key, item.code) for item in schema.diagnostics}
    assert (
        MetadataFieldKey.DATE_OF_SERVICE,
        MetadataDiagnosticCode.INCOMPATIBLE_TYPE,
    ) in codes
    assert (
        MetadataFieldKey.PATIENT_NAME,
        MetadataDiagnosticCode.MISSING_FIELD,
    ) in codes
    with pytest.raises(MetadataSchemaError):
        schema.write_field_id(MetadataFieldKey.DATE_OF_SERVICE)


def test_duplicate_canonical_definition_is_not_writable() -> None:
    schema = resolve_metadata_schema(
        [
            _definition(60, MetadataFieldKey.PATIENT_NAME),
            _definition(61, MetadataFieldKey.PATIENT_NAME),
        ],
        (MetadataFieldKey.PATIENT_NAME,),
    )

    assert any(
        diagnostic.code is MetadataDiagnosticCode.DUPLICATE_FIELD
        for diagnostic in schema.diagnostics
    )
    with pytest.raises(MetadataSchemaError):
        schema.write_field_id(MetadataFieldKey.PATIENT_NAME)


def test_select_options_are_validated_and_resolved_to_ids() -> None:
    schema = resolve_metadata_schema(
        [
            _definition(
                70,
                MetadataFieldKey.ACTION_STATUS,
                options=(
                    (1, "pending"),
                    (2, "acknowledged"),
                    (3, "completed"),
                    (4, "snoozed"),
                    (5, "dismissed"),
                    (6, "not_an_action"),
                ),
            )
        ],
        (MetadataFieldKey.ACTION_STATUS,),
    )

    assert build_metadata_update(MetadataFieldKey.ACTION_STATUS, "pending", schema) == {
        "field": 70,
        "value": 1,
    }
    with pytest.raises(MetadataValueError):
        build_metadata_update(MetadataFieldKey.ACTION_STATUS, "unknown", schema)


def test_missing_select_option_blocks_writes() -> None:
    schema = resolve_metadata_schema(
        [
            _definition(
                80,
                MetadataFieldKey.EOB_DOCUMENT_TYPE,
                options=((1, "EOB"),),
            )
        ],
        (MetadataFieldKey.EOB_DOCUMENT_TYPE,),
    )

    assert any(
        diagnostic.code is MetadataDiagnosticCode.MISSING_SELECT_OPTION
        for diagnostic in schema.diagnostics
    )
    with pytest.raises(MetadataSchemaError):
        build_metadata_update(MetadataFieldKey.EOB_DOCUMENT_TYPE, "EOB", schema)


def test_build_update_targets_canonical_id_only() -> None:
    schema = resolve_metadata_schema(
        [
            _definition(90, MetadataFieldKey.ACCOUNT_IDENTIFIER),
            _definition(91, MetadataFieldKey.ACCOUNT_IDENTIFIER, alias=True),
        ],
        (MetadataFieldKey.ACCOUNT_IDENTIFIER,),
    )

    assert build_metadata_update(
        MetadataFieldKey.ACCOUNT_IDENTIFIER,
        " ending 9876 ",
        schema,
    ) == {"field": 90, "value": "ending 9876"}


def test_build_update_rejects_unmasked_account_identifier() -> None:
    schema = resolve_metadata_schema(
        [_definition(92, MetadataFieldKey.ACCOUNT_IDENTIFIER)],
        (MetadataFieldKey.ACCOUNT_IDENTIFIER,),
    )

    with pytest.raises(MetadataValueError, match="masked"):
        build_metadata_update(
            MetadataFieldKey.ACCOUNT_IDENTIFIER,
            "SAMPLE123456789",
            schema,
        )


def test_normalized_document_type_remains_inventory_gated() -> None:
    spec = get_metadata_field_spec(MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE)
    assert {field_type.value for field_type in spec.compatible_types} == {"select", "string"}
    assert spec.create_policy is MetadataCreatePolicy.NEVER
    assert spec.create_type is None


def test_unambiguous_canonical_fields_are_creation_enabled() -> None:
    expected_types = {
        MetadataFieldKey.ACCOUNT_IDENTIFIER: "string",
        MetadataFieldKey.PATIENT_NAME: "string",
        MetadataFieldKey.PROVIDER_NAME: "string",
        MetadataFieldKey.DATE_OF_SERVICE: "date",
        MetadataFieldKey.PATIENT_RESPONSIBILITY: "monetary",
        MetadataFieldKey.CLAIM_NUMBER: "string",
        MetadataFieldKey.INVOICE_NUMBER: "string",
    }

    for key, expected_type in expected_types.items():
        spec = get_metadata_field_spec(key)
        assert spec.create_policy is MetadataCreatePolicy.IF_MISSING
        assert spec.create_type is not None
        assert spec.create_type.value == expected_type


def test_deployed_select_document_type_uses_existing_option_ids() -> None:
    schema = resolve_metadata_schema(
        [
            _definition(
                93,
                MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE,
                data_type="select",
                options=((1, "Statement"), (2, "Invoice")),
            )
        ],
        (MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE,),
    )

    assert build_metadata_update(
        MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE,
        "Invoice",
        schema,
    ) == {"field": 93, "value": 2}


@pytest.mark.asyncio
async def test_resolver_ensure_creates_seven_unambiguous_canonical_fields() -> None:
    class StubClient:
        def __init__(self) -> None:
            self.definitions: list[dict] = []

        async def list_custom_fields(self) -> list[dict]:
            return self.definitions

        async def create_custom_field(self, definition: dict) -> dict:
            created = {"id": len(self.definitions) + 1, **definition}
            if definition["data_type"] == "select":
                created["extra_data"] = {
                    "select_options": [
                        {"id": index, **option}
                        for index, option in enumerate(
                            definition["extra_data"]["select_options"], start=1
                        )
                    ]
                }
            self.definitions.append(created)
            return created

        async def update_custom_field(self, field_id: int, data: dict) -> dict:
            raise AssertionError("No update expected")

    client = StubClient()
    resolver = PaperlessMetadataResolver(client)
    schema = await resolver.ensure(CANONICAL_KEYS)

    for key in CANONICAL_KEYS[:-1]:
        assert schema.field(key).is_compatible
    assert schema.field(MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE).canonical_id is None
    assert [definition["name"] for definition in client.definitions] == [
        "Account Identifier",
        "Patient Name",
        "Provider Name",
        "Date of Service",
        "Patient Responsibility",
        "Claim Number",
        "Invoice Number",
    ]


@pytest.mark.asyncio
async def test_resolver_ensure_does_not_mutate_incompatible_canonical_field() -> None:
    client = AsyncMock()
    client.list_custom_fields.return_value = [
        {"id": 100, "name": "Date of Service", "data_type": "string"}
    ]

    schema = await PaperlessMetadataResolver(client).ensure((MetadataFieldKey.DATE_OF_SERVICE,))

    assert not schema.field(MetadataFieldKey.DATE_OF_SERVICE).is_compatible
    assert any(
        diagnostic.code is MetadataDiagnosticCode.INCOMPATIBLE_TYPE
        for diagnostic in schema.diagnostics
    )
    client.create_custom_field.assert_not_awaited()
    client.update_custom_field.assert_not_awaited()
