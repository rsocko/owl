"""Paperless custom-field enrichment for EOB matching."""

from __future__ import annotations

from datetime import date

from doc_intelligence_hub.core.paperless import (
    MetadataFieldKey,
    MetadataSchemaError,
    PaperlessClient,
    PaperlessMetadataResolver,
    ResolvedMetadataSchema,
    build_metadata_update,
    get_metadata_field_spec,
)

_EOB_FIELDS = (
    MetadataFieldKey.EOB_MATCH_STATUS,
    MetadataFieldKey.EOB_MATCH_SCORE,
    MetadataFieldKey.EOB_MATCH_CONFIDENCE,
    MetadataFieldKey.EOB_MATCHED_DOCUMENT,
    MetadataFieldKey.EOB_DOCUMENT_TYPE,
    MetadataFieldKey.EOB_PATIENT_RESPONSIBILITY,
    MetadataFieldKey.EOB_ANALYZED,
)

# Backward-compatible public constant; definitions are owned by the shared registry.
CUSTOM_FIELD_DEFINITIONS = [
    get_metadata_field_spec(key).create_definition() for key in _EOB_FIELDS
]


class EOBEnricher:
    """Writes EOB match results back to Paperless custom fields."""

    def __init__(self, client: PaperlessClient):
        self.client = client
        self._schema: ResolvedMetadataSchema | None = None
        self._field_id_cache: dict[MetadataFieldKey, int] = {}

    async def ensure_custom_fields_exist(self) -> dict[MetadataFieldKey, int]:
        schema = await PaperlessMetadataResolver(self.client).ensure(_EOB_FIELDS)
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
    ) -> None:
        """Write match relationship to both EOB and bill documents."""
        schema = await self.get_schema()
        today = date.today().isoformat()

        eob_fields = self._match_fields(
            schema,
            matched_document_id=bill_document_id,
            score=score,
            confidence=confidence,
            document_type="EOB",
            analyzed=today,
        )
        if patient_responsibility is not None:
            eob_fields.append(
                build_metadata_update(
                    MetadataFieldKey.EOB_PATIENT_RESPONSIBILITY,
                    patient_responsibility,
                    schema,
                )
            )
        await self.client.update_custom_fields(eob_document_id, eob_fields)

        bill_fields = self._match_fields(
            schema,
            matched_document_id=eob_document_id,
            score=score,
            confidence=confidence,
            document_type="BILL",
            analyzed=today,
        )
        await self.client.update_custom_fields(bill_document_id, bill_fields)

    @staticmethod
    def _match_fields(
        schema: ResolvedMetadataSchema,
        *,
        matched_document_id: int,
        score: float,
        confidence: str,
        document_type: str,
        analyzed: str,
    ) -> list[dict]:
        return [
            build_metadata_update(MetadataFieldKey.EOB_MATCH_STATUS, "matched", schema),
            build_metadata_update(MetadataFieldKey.EOB_MATCH_SCORE, round(score, 1), schema),
            build_metadata_update(
                MetadataFieldKey.EOB_MATCH_CONFIDENCE, confidence, schema
            ),
            build_metadata_update(
                MetadataFieldKey.EOB_MATCHED_DOCUMENT,
                matched_document_id,
                schema,
            ),
            build_metadata_update(
                MetadataFieldKey.EOB_DOCUMENT_TYPE,
                document_type,
                schema,
            ),
            build_metadata_update(MetadataFieldKey.EOB_ANALYZED, analyzed, schema),
        ]

    async def mark_unmatched(self, document_id: int, doc_type: str) -> None:
        """Mark a document as unmatched after analysis."""
        schema = await self.get_schema()
        await self.client.update_custom_fields(
            document_id,
            [
                build_metadata_update(
                    MetadataFieldKey.EOB_MATCH_STATUS,
                    "unmatched",
                    schema,
                ),
                build_metadata_update(
                    MetadataFieldKey.EOB_DOCUMENT_TYPE,
                    doc_type.upper(),
                    schema,
                ),
                build_metadata_update(
                    MetadataFieldKey.EOB_ANALYZED,
                    date.today().isoformat(),
                    schema,
                ),
            ],
        )
