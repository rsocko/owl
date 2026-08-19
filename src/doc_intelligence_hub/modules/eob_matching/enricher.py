"""Governed durable metadata projection for EOB matching."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doc_intelligence_hub.core.paperless import (
    AccountIdentifierClass,
    MetadataFieldKey,
    MetadataProjectionPolicy,
    MetadataSchemaError,
    PaperlessClient,
    PaperlessMetadataResolver,
    ResolvedMetadataSchema,
    build_account_identifier_update,
    build_metadata_update,
    get_metadata_field_spec,
    mask_account_identifier,
)

_EOB_DURABLE_FIELDS = (
    MetadataFieldKey.ACCOUNT_IDENTIFIER,
    MetadataFieldKey.PATIENT_NAME,
    MetadataFieldKey.PROVIDER_NAME,
    MetadataFieldKey.DATE_OF_SERVICE,
    MetadataFieldKey.PATIENT_RESPONSIBILITY,
    MetadataFieldKey.CLAIM_NUMBER,
    MetadataFieldKey.INVOICE_NUMBER,
)

# Backward-compatible public constant; only durable canonical fields are provisioned.
CUSTOM_FIELD_DEFINITIONS = [
    get_metadata_field_spec(key).create_definition() for key in _EOB_DURABLE_FIELDS
]


@dataclass(frozen=True)
class ProjectionAuditRecord:
    document_id: int
    stable_key: str
    field_id: int
    value_display: str | None
    actor: str
    reason: str


class EOBEnricher:
    """Projects independently useful EOB metadata without exporting match analytics."""

    def __init__(self, client: PaperlessClient, *, audit_session: Any | None = None):
        self.client = client
        self.audit_session = audit_session
        self.audit_records: list[ProjectionAuditRecord] = []
        self._schema: ResolvedMetadataSchema | None = None
        self._field_id_cache: dict[MetadataFieldKey, int] = {}

    async def ensure_custom_fields_exist(self) -> dict[MetadataFieldKey, int]:
        schema = await PaperlessMetadataResolver(self.client).ensure(_EOB_DURABLE_FIELDS)
        self._schema = schema
        self._field_id_cache = {
            key: resolved.canonical_id
            for key, resolved in schema.fields.items()
            if resolved.canonical_id is not None and resolved.is_compatible
        }
        return dict(self._field_id_cache)

    async def get_schema(self) -> ResolvedMetadataSchema:
        if self._schema is None:
            await self.ensure_custom_fields_exist()
        if self._schema is None:
            raise MetadataSchemaError("EOB metadata schema was not initialized")
        return self._schema

    async def get_field_ids(self) -> dict[MetadataFieldKey, int]:
        await self.get_schema()
        return dict(self._field_id_cache)

    async def link_match(
        self,
        eob_document_id: int,
        bill_document_id: int,
        score: float,
        confidence: str,
        patient_responsibility: float | None = None,
        *,
        eob: Any | None = None,
        bill: Any | None = None,
        confirmed: bool = False,
        actor: str = "system",
        reason: str = "eob_match_projection",
    ) -> tuple[ProjectionAuditRecord, ...]:
        """Project durable values; score, confidence, and the relationship stay OWL-local."""
        del score, confidence
        schema = await self.get_schema()
        records: list[ProjectionAuditRecord] = []

        eob_values = {
            MetadataFieldKey.PATIENT_NAME: _value(eob, "patient_name"),
            MetadataFieldKey.PROVIDER_NAME: _value(eob, "provider_name"),
            MetadataFieldKey.DATE_OF_SERVICE: _value(eob, "date_of_service"),
            MetadataFieldKey.PATIENT_RESPONSIBILITY: (
                _value(eob, "total_patient_responsibility")
                if eob is not None
                else patient_responsibility
            ),
            MetadataFieldKey.CLAIM_NUMBER: _value(eob, "claim_number"),
        }
        eob_confidence = _confidence(eob)
        eob_updates = self._durable_updates(
            schema,
            document_type="EOB",
            values=eob_values,
            extraction_confidence=eob_confidence,
            confirmed=confirmed,
        )
        policy_number = _value(eob, "policy_number")
        if policy_number and _projection_allowed(
            MetadataFieldKey.ACCOUNT_IDENTIFIER,
            "EOB",
            eob_confidence,
            confirmed,
        ):
            account_update, projection = build_account_identifier_update(
                policy_number,
                AccountIdentifierClass.POLICY,
                eob_confidence,
                schema,
            )
            if not projection.requires_review and projection.paperless_value is not None:
                eob_updates.append((MetadataFieldKey.ACCOUNT_IDENTIFIER, account_update))

        records.extend(
            await self._write_and_audit(
                eob_document_id,
                eob_updates,
                actor=actor,
                reason=reason,
            )
        )

        bill_values = {
            MetadataFieldKey.PATIENT_NAME: _value(bill, "patient_name"),
            MetadataFieldKey.PROVIDER_NAME: _value(bill, "provider_name"),
            MetadataFieldKey.DATE_OF_SERVICE: _value(bill, "date_of_service"),
            MetadataFieldKey.INVOICE_NUMBER: _value(bill, "invoice_number"),
        }
        records.extend(
            await self._write_and_audit(
                bill_document_id,
                self._durable_updates(
                    schema,
                    document_type="BILL",
                    values=bill_values,
                    extraction_confidence=_confidence(bill),
                    confirmed=confirmed,
                ),
                actor=actor,
                reason=reason,
            )
        )
        return tuple(records)

    def _durable_updates(
        self,
        schema: ResolvedMetadataSchema,
        *,
        document_type: str,
        values: dict[MetadataFieldKey, Any],
        extraction_confidence: float,
        confirmed: bool,
    ) -> list[tuple[MetadataFieldKey, dict[str, Any]]]:
        updates: list[tuple[MetadataFieldKey, dict[str, Any]]] = []
        for key, value in values.items():
            if value is None or not _projection_allowed(
                key,
                document_type,
                extraction_confidence,
                confirmed,
            ):
                continue
            updates.append((key, build_metadata_update(key, value, schema)))
        return updates

    async def _write_and_audit(
        self,
        document_id: int,
        updates: list[tuple[MetadataFieldKey, dict[str, Any]]],
        *,
        actor: str,
        reason: str,
    ) -> tuple[ProjectionAuditRecord, ...]:
        if not updates:
            return ()
        if self.audit_session is None:
            raise MetadataSchemaError("EOB projection requires durable audit storage")
        records = tuple(
            ProjectionAuditRecord(
                document_id=document_id,
                stable_key=key.value,
                field_id=int(update["field"]),
                value_display=_audit_value(key, update.get("value")),
                actor=actor,
                reason=reason,
            )
            for key, update in updates
        )
        self.audit_records.extend(records)
        from doc_intelligence_hub.modules.eob_matching.database import ProjectionAudit

        audit_rows = [
            ProjectionAudit(
                document_id=record.document_id,
                stable_key=record.stable_key,
                field_id=record.field_id,
                value_display=record.value_display,
                actor=record.actor,
                reason=record.reason,
                status="pending",
            )
            for record in records
        ]
        self.audit_session.add_all(audit_rows)
        self.audit_session.commit()
        try:
            await self.client.update_custom_fields(document_id, [update for _, update in updates])
        except Exception:
            for row in audit_rows:
                row.status = "failed"
            self.audit_session.commit()
            raise
        for row in audit_rows:
            row.status = "applied"
        self.audit_session.commit()
        return records

    async def mark_unmatched(self, document_id: int, doc_type: str) -> None:
        """Keep unmatched and triage state OWL-local."""
        del document_id, doc_type


def _value(record: Any | None, name: str) -> Any:
    return getattr(record, name, None) if record is not None else None


def _confidence(record: Any | None) -> float:
    try:
        return float(_value(record, "extraction_confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _projection_allowed(
    key: MetadataFieldKey,
    document_type: str,
    extraction_confidence: float,
    confirmed: bool,
) -> bool:
    spec = get_metadata_field_spec(key)
    if spec.projection_policy is not MetadataProjectionPolicy.DURABLE:
        return False
    if document_type not in spec.eligible_document_types:
        return False
    if spec.confirmation_required:
        if not confirmed:
            return False
    if confirmed and spec.confirmation_bypasses_confidence:
        return True
    threshold = spec.auto_projection_min_confidence
    return threshold is not None and extraction_confidence >= threshold


def _audit_value(key: MetadataFieldKey, value: Any) -> str | None:
    if value is None:
        return None
    if key is MetadataFieldKey.ACCOUNT_IDENTIFIER:
        return mask_account_identifier(str(value), AccountIdentifierClass.POLICY)
    return str(value)
