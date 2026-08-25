"""Typed registry and deployed-schema resolver for Paperless custom fields."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence


class MetadataFieldKey(str, Enum):
    ACCOUNT_IDENTIFIER = "account_identifier"
    PATIENT_NAME = "patient_name"
    PROVIDER_NAME = "provider_name"
    DATE_OF_SERVICE = "date_of_service"
    PATIENT_RESPONSIBILITY = "patient_responsibility"
    CLAIM_NUMBER = "claim_number"
    INVOICE_NUMBER = "invoice_number"
    NORMALIZED_DOCUMENT_TYPE = "normalized_document_type"
    SERIES_NAME = "series_name"
    DOCUMENT_AMOUNT = "document_amount"
    DOCUMENT_DUE_DATE = "document_due_date"
    ACTION_STATUS = "action_status"
    ACTION_ANALYZED = "action_analyzed"
    LEGACY_ACTION_TYPE = "legacy_action_type"
    LEGACY_ACTION_DUE_DATE = "legacy_action_due_date"
    LEGACY_ACTION_URGENCY = "legacy_action_urgency"
    LEGACY_ACTION_SUMMARY = "legacy_action_summary"
    LEGACY_ACTION_COUNT = "legacy_action_count"
    EOB_MATCH_STATUS = "eob_match_status"
    EOB_MATCH_SCORE = "eob_match_score"
    EOB_MATCH_CONFIDENCE = "eob_match_confidence"
    EOB_MATCHED_DOCUMENT = "eob_matched_document"
    EOB_DOCUMENT_TYPE = "eob_document_type"
    EOB_PATIENT_RESPONSIBILITY = "eob_patient_responsibility"
    EOB_ANALYZED = "eob_analyzed"
    RELATED_DOCUMENTS = "related_documents"
    RELATIONSHIP_SUMMARY = "relationship_summary"


class PaperlessFieldType(str, Enum):
    TEXT = "string"
    DATE = "date"
    SELECT = "select"
    INTEGER = "integer"
    FLOAT = "float"
    DECIMAL = "decimal"
    MONETARY = "monetary"
    DOCUMENT_LINK = "document_link"


class MetadataSensitivity(str, Enum):
    GENERAL = "general"
    FINANCIAL = "financial"
    PERSONAL = "personal"
    MEDICAL = "medical"


class MetadataWritePolicy(str, Enum):
    READ_WRITE = "read_write"
    READ_ONLY = "read_only"
    CLEAR_ONLY = "clear_only"
    DISABLED = "disabled"


class MetadataProjectionPolicy(str, Enum):
    DURABLE = "durable"
    OPERATIONAL = "operational"
    LEGACY_CLEANUP = "legacy_cleanup"


class MetadataNormalization(str, Enum):
    TEXT = "text"
    DATE = "date"
    NUMBER = "number"
    SELECT = "select"
    DOCUMENT_LINK = "document_link"


class MetadataCreatePolicy(str, Enum):
    NEVER = "never"
    IF_MISSING = "if_missing"
    RENAME_FIRST_ALIAS = "rename_first_alias"


@dataclass(frozen=True)
class MetadataFieldSpec:
    key: MetadataFieldKey
    canonical_name: str
    compatible_types: tuple[PaperlessFieldType, ...]
    normalization: MetadataNormalization
    aliases: tuple[str, ...] = ()
    select_options: tuple[str, ...] = ()
    sensitivity: MetadataSensitivity = MetadataSensitivity.GENERAL
    write_policy: MetadataWritePolicy = MetadataWritePolicy.READ_WRITE
    projection_policy: MetadataProjectionPolicy = MetadataProjectionPolicy.OPERATIONAL
    create_policy: MetadataCreatePolicy = MetadataCreatePolicy.NEVER
    create_type: PaperlessFieldType | None = None
    compatibility_read: bool = False
    eligible_document_types: frozenset[str] = frozenset()
    schema_version: str = "1.0"

    def create_definition(self) -> dict[str, Any]:
        if self.create_type is None:
            raise MetadataSchemaError(f"{self.key.value} does not have a creation type")
        definition: dict[str, Any] = {
            "name": self.canonical_name,
            "data_type": self.create_type.value,
        }
        if self.select_options:
            definition["extra_data"] = {
                "select_options": [{"label": label} for label in self.select_options]
            }
        return definition


def _spec(
    key: MetadataFieldKey,
    name: str,
    field_type: PaperlessFieldType | tuple[PaperlessFieldType, ...],
    normalization: MetadataNormalization,
    **kwargs: Any,
) -> MetadataFieldSpec:
    compatible_types = field_type if isinstance(field_type, tuple) else (field_type,)
    return MetadataFieldSpec(
        key=key,
        canonical_name=name,
        compatible_types=compatible_types,
        normalization=normalization,
        **kwargs,
    )


_DURABLE = MetadataProjectionPolicy.DURABLE
_OPERATIONAL = MetadataProjectionPolicy.OPERATIONAL
_CREATE = MetadataCreatePolicy.IF_MISSING

_REGISTRY_ENTRIES = (
    _spec(
        MetadataFieldKey.ACCOUNT_IDENTIFIER,
        "Account Identifier",
        PaperlessFieldType.TEXT,
        MetadataNormalization.TEXT,
        aliases=("di_account_id",),
        sensitivity=MetadataSensitivity.FINANCIAL,
        projection_policy=_DURABLE,
        create_policy=_CREATE,
        create_type=PaperlessFieldType.TEXT,
        compatibility_read=True,
    ),
    _spec(
        MetadataFieldKey.PATIENT_NAME,
        "Patient Name",
        PaperlessFieldType.TEXT,
        MetadataNormalization.TEXT,
        aliases=("di_patient_name",),
        sensitivity=MetadataSensitivity.MEDICAL,
        projection_policy=_DURABLE,
        create_policy=_CREATE,
        create_type=PaperlessFieldType.TEXT,
        compatibility_read=True,
    ),
    _spec(
        MetadataFieldKey.PROVIDER_NAME,
        "Provider Name",
        PaperlessFieldType.TEXT,
        MetadataNormalization.TEXT,
        aliases=("di_provider_name",),
        sensitivity=MetadataSensitivity.MEDICAL,
        projection_policy=_DURABLE,
        create_policy=_CREATE,
        create_type=PaperlessFieldType.TEXT,
        compatibility_read=True,
    ),
    _spec(
        MetadataFieldKey.DATE_OF_SERVICE,
        "Date of Service",
        PaperlessFieldType.DATE,
        MetadataNormalization.DATE,
        aliases=("di_date_of_service",),
        sensitivity=MetadataSensitivity.MEDICAL,
        projection_policy=_DURABLE,
        create_policy=_CREATE,
        create_type=PaperlessFieldType.DATE,
        compatibility_read=True,
    ),
    _spec(
        MetadataFieldKey.PATIENT_RESPONSIBILITY,
        "Patient Responsibility",
        (PaperlessFieldType.MONETARY, PaperlessFieldType.DECIMAL),
        MetadataNormalization.NUMBER,
        aliases=("di_patient_resp",),
        sensitivity=MetadataSensitivity.MEDICAL,
        projection_policy=_DURABLE,
        create_policy=_CREATE,
        create_type=PaperlessFieldType.MONETARY,
        compatibility_read=True,
    ),
    _spec(
        MetadataFieldKey.CLAIM_NUMBER,
        "Claim Number",
        PaperlessFieldType.TEXT,
        MetadataNormalization.TEXT,
        aliases=("di_claim_number",),
        sensitivity=MetadataSensitivity.MEDICAL,
        projection_policy=_DURABLE,
        create_policy=_CREATE,
        create_type=PaperlessFieldType.TEXT,
        compatibility_read=True,
    ),
    _spec(
        MetadataFieldKey.INVOICE_NUMBER,
        "Invoice Number",
        PaperlessFieldType.TEXT,
        MetadataNormalization.TEXT,
        aliases=("di_invoice_number",),
        sensitivity=MetadataSensitivity.FINANCIAL,
        projection_policy=_DURABLE,
        create_policy=_CREATE,
        create_type=PaperlessFieldType.TEXT,
        compatibility_read=True,
    ),
    _spec(
        MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE,
        "Normalized Document Type",
        (PaperlessFieldType.SELECT, PaperlessFieldType.TEXT),
        MetadataNormalization.SELECT,
        aliases=("di_doc_type",),
        projection_policy=_DURABLE,
        compatibility_read=True,
    ),
    _spec(
        MetadataFieldKey.SERIES_NAME,
        "Series Name",
        PaperlessFieldType.TEXT,
        MetadataNormalization.TEXT,
        projection_policy=_DURABLE,
    ),
    _spec(
        MetadataFieldKey.RELATED_DOCUMENTS,
        "Related Document IDs",
        PaperlessFieldType.TEXT,
        MetadataNormalization.TEXT,
        projection_policy=_OPERATIONAL,
        create_policy=_CREATE,
        create_type=PaperlessFieldType.TEXT,
    ),
    _spec(
        MetadataFieldKey.RELATIONSHIP_SUMMARY,
        "Relationship Summary",
        PaperlessFieldType.TEXT,
        MetadataNormalization.TEXT,
        projection_policy=_OPERATIONAL,
        create_policy=_CREATE,
        create_type=PaperlessFieldType.TEXT,
    ),
    _spec(
        MetadataFieldKey.DOCUMENT_AMOUNT,
        "Document Amount",
        PaperlessFieldType.FLOAT,
        MetadataNormalization.NUMBER,
        aliases=("Action Amount",),
        sensitivity=MetadataSensitivity.FINANCIAL,
        create_policy=MetadataCreatePolicy.RENAME_FIRST_ALIAS,
        create_type=PaperlessFieldType.FLOAT,
    ),
    _spec(
        MetadataFieldKey.DOCUMENT_DUE_DATE,
        "Document Due Date",
        PaperlessFieldType.DATE,
        MetadataNormalization.DATE,
        projection_policy=_DURABLE,
        create_policy=_CREATE,
        create_type=PaperlessFieldType.DATE,
    ),
    _spec(
        MetadataFieldKey.ACTION_STATUS,
        "Action Status",
        PaperlessFieldType.SELECT,
        MetadataNormalization.SELECT,
        select_options=(
            "pending",
            "acknowledged",
            "completed",
            "snoozed",
            "dismissed",
            "not_an_action",
        ),
        create_policy=_CREATE,
        create_type=PaperlessFieldType.SELECT,
    ),
    _spec(
        MetadataFieldKey.ACTION_ANALYZED,
        "Action Analyzed",
        PaperlessFieldType.DATE,
        MetadataNormalization.DATE,
        create_policy=_CREATE,
        create_type=PaperlessFieldType.DATE,
    ),
    _spec(
        MetadataFieldKey.LEGACY_ACTION_TYPE,
        "Action Type",
        PaperlessFieldType.SELECT,
        MetadataNormalization.SELECT,
        write_policy=MetadataWritePolicy.CLEAR_ONLY,
        projection_policy=MetadataProjectionPolicy.LEGACY_CLEANUP,
    ),
    _spec(
        MetadataFieldKey.LEGACY_ACTION_DUE_DATE,
        "Action Due Date",
        PaperlessFieldType.DATE,
        MetadataNormalization.DATE,
        write_policy=MetadataWritePolicy.CLEAR_ONLY,
        projection_policy=MetadataProjectionPolicy.LEGACY_CLEANUP,
    ),
    _spec(
        MetadataFieldKey.LEGACY_ACTION_URGENCY,
        "Action Urgency",
        PaperlessFieldType.SELECT,
        MetadataNormalization.SELECT,
        write_policy=MetadataWritePolicy.CLEAR_ONLY,
        projection_policy=MetadataProjectionPolicy.LEGACY_CLEANUP,
    ),
    _spec(
        MetadataFieldKey.LEGACY_ACTION_SUMMARY,
        "Action Summary",
        PaperlessFieldType.TEXT,
        MetadataNormalization.TEXT,
        write_policy=MetadataWritePolicy.CLEAR_ONLY,
        projection_policy=MetadataProjectionPolicy.LEGACY_CLEANUP,
    ),
    _spec(
        MetadataFieldKey.LEGACY_ACTION_COUNT,
        "Action Count",
        PaperlessFieldType.INTEGER,
        MetadataNormalization.NUMBER,
        write_policy=MetadataWritePolicy.CLEAR_ONLY,
        projection_policy=MetadataProjectionPolicy.LEGACY_CLEANUP,
    ),
    _spec(
        MetadataFieldKey.EOB_MATCH_STATUS,
        "EOB Match Status",
        PaperlessFieldType.SELECT,
        MetadataNormalization.SELECT,
        select_options=("matched", "unmatched", "review_needed"),
        create_policy=_CREATE,
        create_type=PaperlessFieldType.SELECT,
    ),
    _spec(
        MetadataFieldKey.EOB_MATCH_SCORE,
        "EOB Match Score",
        PaperlessFieldType.DECIMAL,
        MetadataNormalization.NUMBER,
        create_policy=_CREATE,
        create_type=PaperlessFieldType.DECIMAL,
    ),
    _spec(
        MetadataFieldKey.EOB_MATCH_CONFIDENCE,
        "EOB Match Confidence",
        PaperlessFieldType.SELECT,
        MetadataNormalization.SELECT,
        select_options=("HIGH", "MEDIUM", "LOW"),
        create_policy=_CREATE,
        create_type=PaperlessFieldType.SELECT,
    ),
    _spec(
        MetadataFieldKey.EOB_MATCHED_DOCUMENT,
        "EOB Matched Document",
        PaperlessFieldType.DOCUMENT_LINK,
        MetadataNormalization.DOCUMENT_LINK,
        create_policy=_CREATE,
        create_type=PaperlessFieldType.DOCUMENT_LINK,
    ),
    _spec(
        MetadataFieldKey.EOB_DOCUMENT_TYPE,
        "EOB Document Type",
        PaperlessFieldType.SELECT,
        MetadataNormalization.SELECT,
        select_options=("EOB", "BILL"),
        create_policy=_CREATE,
        create_type=PaperlessFieldType.SELECT,
    ),
    _spec(
        MetadataFieldKey.EOB_PATIENT_RESPONSIBILITY,
        "EOB Patient Responsibility",
        PaperlessFieldType.DECIMAL,
        MetadataNormalization.NUMBER,
        sensitivity=MetadataSensitivity.MEDICAL,
        create_policy=_CREATE,
        create_type=PaperlessFieldType.DECIMAL,
    ),
    _spec(
        MetadataFieldKey.EOB_ANALYZED,
        "EOB Analyzed",
        PaperlessFieldType.DATE,
        MetadataNormalization.DATE,
        create_policy=_CREATE,
        create_type=PaperlessFieldType.DATE,
    ),
)

PAPERLESS_METADATA_REGISTRY: Mapping[MetadataFieldKey, MetadataFieldSpec] = MappingProxyType(
    {entry.key: entry for entry in _REGISTRY_ENTRIES}
)

_KEY_ALIASES = MappingProxyType(
    {"document_classification": MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE}
)


def get_metadata_field_spec(key: MetadataFieldKey | str) -> MetadataFieldSpec:
    if isinstance(key, str) and key in _KEY_ALIASES:
        key = _KEY_ALIASES[key]
    try:
        resolved_key = key if isinstance(key, MetadataFieldKey) else MetadataFieldKey(key)
    except ValueError as exc:
        raise KeyError(f"Unknown metadata field key: {key}") from exc
    return PAPERLESS_METADATA_REGISTRY[resolved_key]


class MetadataDiagnosticCode(str, Enum):
    MISSING_FIELD = "missing_field"
    DUPLICATE_FIELD = "duplicate_field"
    INCOMPATIBLE_TYPE = "incompatible_type"
    MISSING_SELECT_OPTION = "missing_select_option"
    INVALID_SELECT_OPTION = "invalid_select_option"


@dataclass(frozen=True)
class MetadataDiagnostic:
    key: MetadataFieldKey
    code: MetadataDiagnosticCode
    message: str
    field_name: str | None = None
    field_id: int | None = None
    option_label: str | None = None


@dataclass(frozen=True)
class ResolvedMetadataField:
    spec: MetadataFieldSpec
    canonical_id: int | None
    data_type: PaperlessFieldType | None = None
    alias_ids: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    select_option_ids: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    diagnostics: tuple[MetadataDiagnostic, ...] = ()

    @property
    def is_compatible(self) -> bool:
        incompatible_codes = {
            MetadataDiagnosticCode.DUPLICATE_FIELD,
            MetadataDiagnosticCode.INCOMPATIBLE_TYPE,
            MetadataDiagnosticCode.MISSING_SELECT_OPTION,
            MetadataDiagnosticCode.INVALID_SELECT_OPTION,
        }
        return self.canonical_id is not None and not any(
            diagnostic.code in incompatible_codes for diagnostic in self.diagnostics
        )


class MetadataSchemaError(RuntimeError):
    """Raised when deployed Paperless metadata cannot be used safely."""


class MetadataValueError(ValueError):
    """Raised when a custom-field value fails registry validation."""


@dataclass(frozen=True)
class ResolvedMetadataSchema:
    fields: Mapping[MetadataFieldKey, ResolvedMetadataField]
    diagnostics: tuple[MetadataDiagnostic, ...]

    def field(self, key: MetadataFieldKey | str) -> ResolvedMetadataField:
        return self.fields[get_metadata_field_spec(key).key]

    def write_field_id(self, key: MetadataFieldKey | str) -> int:
        resolved = self.field(key)
        if resolved.spec.write_policy is not MetadataWritePolicy.READ_WRITE:
            raise MetadataSchemaError(f"{resolved.spec.key.value} is not writable")
        if not resolved.is_compatible or resolved.canonical_id is None:
            raise MetadataSchemaError(
                f"Canonical Paperless field {resolved.spec.canonical_name!r} is missing or incompatible"
            )
        return resolved.canonical_id

    def select_value(self, key: MetadataFieldKey | str, label: str) -> int:
        resolved = self.field(key)
        normalized_label = str(label).strip()
        try:
            return resolved.select_option_ids[normalized_label]
        except KeyError as exc:
            raise MetadataValueError(
                f"Unknown deployed option {normalized_label!r} for {resolved.spec.canonical_name!r}"
            ) from exc


def _field_id(field_definition: Mapping[str, Any]) -> int | None:
    value = field_definition.get("id")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _field_type(field_definition: Mapping[str, Any]) -> PaperlessFieldType | None:
    value = field_definition.get("data_type")
    try:
        return PaperlessFieldType(value)
    except (TypeError, ValueError):
        return None


def _select_options(field_definition: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    extra_data = field_definition.get("extra_data")
    if not isinstance(extra_data, Mapping):
        return ()
    options = extra_data.get("select_options")
    if not isinstance(options, list):
        return ()
    return tuple(option for option in options if isinstance(option, dict))


def resolve_metadata_schema(
    field_definitions: Sequence[Mapping[str, Any]],
    keys: Iterable[MetadataFieldKey | str] | None = None,
) -> ResolvedMetadataSchema:
    specs = (
        tuple(get_metadata_field_spec(key) for key in keys)
        if keys is not None
        else tuple(PAPERLESS_METADATA_REGISTRY.values())
    )
    by_name: dict[str, list[Mapping[str, Any]]] = {}
    for definition in field_definitions:
        name = definition.get("name")
        if isinstance(name, str):
            by_name.setdefault(name.strip(), []).append(definition)

    fields: dict[MetadataFieldKey, ResolvedMetadataField] = {}
    all_diagnostics: list[MetadataDiagnostic] = []
    for spec in specs:
        diagnostics: list[MetadataDiagnostic] = []
        canonical_matches = by_name.get(spec.canonical_name, [])
        canonical = canonical_matches[0] if len(canonical_matches) == 1 else None
        if not canonical_matches:
            diagnostics.append(
                MetadataDiagnostic(
                    key=spec.key,
                    code=MetadataDiagnosticCode.MISSING_FIELD,
                    message=f"Missing canonical Paperless field {spec.canonical_name!r}",
                    field_name=spec.canonical_name,
                )
            )
        elif len(canonical_matches) > 1:
            diagnostics.append(
                MetadataDiagnostic(
                    key=spec.key,
                    code=MetadataDiagnosticCode.DUPLICATE_FIELD,
                    message=f"Duplicate canonical Paperless field {spec.canonical_name!r}",
                    field_name=spec.canonical_name,
                )
            )

        canonical_id = _field_id(canonical) if canonical else None
        actual_type = _field_type(canonical) if canonical else None
        select_option_ids: dict[str, int] = {}
        if canonical is not None:
            if actual_type not in spec.compatible_types:
                expected = ", ".join(field_type.value for field_type in spec.compatible_types)
                diagnostics.append(
                    MetadataDiagnostic(
                        key=spec.key,
                        code=MetadataDiagnosticCode.INCOMPATIBLE_TYPE,
                        message=(
                            f"Paperless field {spec.canonical_name!r} has type "
                            f"{canonical.get('data_type')!r}; expected {expected}"
                        ),
                        field_name=spec.canonical_name,
                        field_id=canonical_id,
                    )
                )
            if actual_type is PaperlessFieldType.SELECT:
                labels_seen: set[str] = set()
                for option in _select_options(canonical):
                    label = option.get("label")
                    if not isinstance(label, str) or not label.strip():
                        continue
                    normalized_label = label.strip()
                    labels_seen.add(normalized_label)
                    option_id = _field_id(option)
                    if option_id is None:
                        diagnostics.append(
                            MetadataDiagnostic(
                                key=spec.key,
                                code=MetadataDiagnosticCode.INVALID_SELECT_OPTION,
                                message=(
                                    f"Select option {normalized_label!r} on "
                                    f"{spec.canonical_name!r} has no numeric ID"
                                ),
                                field_name=spec.canonical_name,
                                field_id=canonical_id,
                                option_label=normalized_label,
                            )
                        )
                    else:
                        select_option_ids[normalized_label] = option_id
                for required_label in spec.select_options:
                    if required_label not in labels_seen:
                        diagnostics.append(
                            MetadataDiagnostic(
                                key=spec.key,
                                code=MetadataDiagnosticCode.MISSING_SELECT_OPTION,
                                message=(
                                    f"Paperless field {spec.canonical_name!r} is missing "
                                    f"select option {required_label!r}"
                                ),
                                field_name=spec.canonical_name,
                                field_id=canonical_id,
                                option_label=required_label,
                            )
                        )

        alias_ids: dict[str, int] = {}
        for alias in spec.aliases:
            alias_matches = by_name.get(alias, [])
            if len(alias_matches) > 1:
                diagnostics.append(
                    MetadataDiagnostic(
                        key=spec.key,
                        code=MetadataDiagnosticCode.DUPLICATE_FIELD,
                        message=f"Duplicate Paperless alias field {alias!r}",
                        field_name=alias,
                    )
                )
                continue
            if alias_matches:
                alias_id = _field_id(alias_matches[0])
                if alias_id is not None:
                    alias_ids[alias] = alias_id

        resolved = ResolvedMetadataField(
            spec=spec,
            canonical_id=canonical_id,
            data_type=actual_type,
            alias_ids=MappingProxyType(alias_ids),
            select_option_ids=MappingProxyType(select_option_ids),
            diagnostics=tuple(diagnostics),
        )
        fields[spec.key] = resolved
        all_diagnostics.extend(diagnostics)

    return ResolvedMetadataSchema(
        fields=MappingProxyType(fields),
        diagnostics=tuple(all_diagnostics),
    )


class PaperlessMetadataResolver:
    def __init__(self, client: Any):
        self.client = client

    async def resolve(
        self, keys: Iterable[MetadataFieldKey | str] | None = None
    ) -> ResolvedMetadataSchema:
        definitions = await self.client.list_custom_fields()
        return resolve_metadata_schema(definitions, keys)

    async def ensure(self, keys: Iterable[MetadataFieldKey | str]) -> ResolvedMetadataSchema:
        specs = tuple(get_metadata_field_spec(key) for key in keys)
        schema = await self.resolve(spec.key for spec in specs)
        changed = False

        for spec in specs:
            resolved = schema.field(spec.key)
            if resolved.canonical_id is None:
                if (
                    spec.create_policy is MetadataCreatePolicy.RENAME_FIRST_ALIAS
                    and spec.aliases
                    and spec.aliases[0] in resolved.alias_ids
                ):
                    await self.client.update_custom_field(
                        resolved.alias_ids[spec.aliases[0]], {"name": spec.canonical_name}
                    )
                    changed = True
                elif spec.create_policy in {
                    MetadataCreatePolicy.IF_MISSING,
                    MetadataCreatePolicy.RENAME_FIRST_ALIAS,
                }:
                    await self.client.create_custom_field(spec.create_definition())
                    changed = True
                continue

            if spec.select_options and not any(
                diagnostic.code is MetadataDiagnosticCode.INCOMPATIBLE_TYPE
                for diagnostic in resolved.diagnostics
            ):
                missing = [
                    diagnostic.option_label
                    for diagnostic in resolved.diagnostics
                    if diagnostic.code is MetadataDiagnosticCode.MISSING_SELECT_OPTION
                    and diagnostic.option_label is not None
                ]
                if missing:
                    definitions = await self.client.list_custom_fields()
                    existing = next(
                        (
                            definition
                            for definition in definitions
                            if str(definition.get("name", "")).strip() == spec.canonical_name
                        ),
                        None,
                    )
                    if existing is not None:
                        extra_data = existing.get("extra_data")
                        if not isinstance(extra_data, dict):
                            extra_data = {}
                        options = extra_data.get("select_options")
                        if not isinstance(options, list):
                            options = []
                        await self.client.update_custom_field(
                            resolved.canonical_id,
                            {
                                "extra_data": {
                                    **extra_data,
                                    "select_options": [
                                        *options,
                                        *({"label": label} for label in missing),
                                    ],
                                }
                            },
                        )
                        changed = True

        if changed:
            return await self.resolve(spec.key for spec in specs)
        return schema


@dataclass(frozen=True)
class MetadataConflict:
    key: MetadataFieldKey
    selected_source_name: str
    selected_value: Any
    conflicting_sources: tuple[tuple[str, Any], ...]


@dataclass(frozen=True)
class ResolvedMetadataValue:
    key: MetadataFieldKey
    value: Any
    source_name: str | None
    source_id: int | None
    conflict: MetadataConflict | None = None
    validation_error: str | None = None


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _normalize_value(spec: MetadataFieldSpec, value: Any) -> Any:
    if _is_blank(value):
        return None
    if spec.normalization in {
        MetadataNormalization.TEXT,
        MetadataNormalization.SELECT,
    }:
        return str(value).strip()
    if spec.normalization is MetadataNormalization.DATE:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        candidate = str(value).strip()
        try:
            return date.fromisoformat(candidate).isoformat()
        except ValueError as exc:
            raise MetadataValueError(
                f"Invalid date value for {spec.key.value}: {candidate!r}"
            ) from exc
    if spec.normalization is MetadataNormalization.NUMBER:
        try:
            return Decimal(str(value).strip()).normalize()
        except (InvalidOperation, ValueError) as exc:
            raise MetadataValueError(f"Invalid numeric value for {spec.key.value}") from exc
    if spec.normalization is MetadataNormalization.DOCUMENT_LINK:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise MetadataValueError(f"Invalid document link for {spec.key.value}") from exc
    raise MetadataValueError(f"Unsupported normalization for {spec.key.value}")


def resolve_metadata_value(
    key: MetadataFieldKey | str,
    document_custom_fields: Sequence[Mapping[str, Any]],
    schema: ResolvedMetadataSchema,
) -> ResolvedMetadataValue:
    resolved = schema.field(key)
    spec = resolved.spec
    values_by_id: dict[int, Any] = {}
    for item in document_custom_fields:
        field_id = item.get("field")
        try:
            values_by_id[int(field_id)] = item.get("value")
        except (TypeError, ValueError):
            continue

    candidates: list[tuple[str, int, Any, str | None]] = []
    if resolved.canonical_id is not None:
        canonical_value = values_by_id.get(resolved.canonical_id)
        if not _is_blank(canonical_value):
            normalized, validation_error = _normalize_read_value(resolved, canonical_value)
            candidates.append(
                (
                    spec.canonical_name,
                    resolved.canonical_id,
                    normalized,
                    validation_error,
                )
            )
    if spec.compatibility_read:
        for alias in spec.aliases:
            alias_id = resolved.alias_ids.get(alias)
            if alias_id is None:
                continue
            alias_value = values_by_id.get(alias_id)
            if not _is_blank(alias_value):
                normalized, validation_error = _normalize_read_value(resolved, alias_value)
                candidates.append((alias, alias_id, normalized, validation_error))

    if not candidates:
        return ResolvedMetadataValue(spec.key, None, None, None)

    selected_name, selected_id, selected_value, selected_error = candidates[0]
    conflicts = tuple(
        (source_name, source_value)
        for source_name, _, source_value, _ in candidates[1:]
        if source_value != selected_value
    )
    conflict = (
        MetadataConflict(
            key=spec.key,
            selected_source_name=selected_name,
            selected_value=selected_value,
            conflicting_sources=conflicts,
        )
        if conflicts
        else None
    )
    return ResolvedMetadataValue(
        key=spec.key,
        value=selected_value,
        source_name=selected_name,
        source_id=selected_id,
        conflict=conflict,
        validation_error=selected_error,
    )


def _normalize_read_value(resolved: ResolvedMetadataField, value: Any) -> tuple[Any, str | None]:
    spec = resolved.spec
    if resolved.data_type is PaperlessFieldType.SELECT and resolved.select_option_ids:
        id_to_label = {option_id: label for label, option_id in resolved.select_option_ids.items()}
        try:
            numeric_value = int(value)
        except (TypeError, ValueError):
            numeric_value = None
        if numeric_value is not None:
            label = id_to_label.get(numeric_value)
            if label is None:
                return value, (f"Unknown deployed select option ID for {spec.canonical_name!r}")
            return label, None
    try:
        normalized = _normalize_value(spec, value)
    except MetadataValueError as exc:
        fallback = value.strip() if isinstance(value, str) else value
        return fallback, str(exc)
    if (
        resolved.data_type is PaperlessFieldType.SELECT
        and resolved.select_option_ids
        and normalized not in resolved.select_option_ids
    ):
        return normalized, f"Unknown deployed select option for {spec.canonical_name!r}"
    return normalized, None


def build_metadata_update(
    key: MetadataFieldKey | str,
    value: Any,
    schema: ResolvedMetadataSchema,
) -> dict[str, Any]:
    resolved = schema.field(key)
    normalized = _normalize_value(resolved.spec, value)
    _validate_write_value(resolved.spec, normalized)
    field_id = schema.write_field_id(resolved.spec.key)
    if (
        resolved.spec.normalization is MetadataNormalization.SELECT
        and resolved.data_type is PaperlessFieldType.SELECT
        and normalized is not None
    ):
        normalized = schema.select_value(resolved.spec.key, str(normalized))
    elif isinstance(normalized, Decimal):
        normalized = float(normalized)
    return {"field": field_id, "value": normalized}


def _validate_write_value(spec: MetadataFieldSpec, value: Any) -> None:
    if spec.key is not MetadataFieldKey.ACCOUNT_IDENTIFIER or value is None:
        return
    masked_value = str(value)
    if re.fullmatch(r"(?:member\s+)?ending\s+[A-Za-z0-9]{2,8}", masked_value):
        return
    if re.fullmatch(r"[*Xx.\s-]+[A-Za-z0-9]{2,8}", masked_value):
        return
    raise MetadataValueError("Account Identifier must contain only an approved masked value")


__all__ = [
    "MetadataConflict",
    "MetadataCreatePolicy",
    "MetadataDiagnostic",
    "MetadataDiagnosticCode",
    "MetadataFieldKey",
    "MetadataFieldSpec",
    "MetadataNormalization",
    "MetadataProjectionPolicy",
    "MetadataSchemaError",
    "MetadataSensitivity",
    "MetadataValueError",
    "MetadataWritePolicy",
    "PAPERLESS_METADATA_REGISTRY",
    "PaperlessFieldType",
    "PaperlessMetadataResolver",
    "ResolvedMetadataField",
    "ResolvedMetadataSchema",
    "ResolvedMetadataValue",
    "build_metadata_update",
    "get_metadata_field_spec",
    "resolve_metadata_schema",
    "resolve_metadata_value",
]
