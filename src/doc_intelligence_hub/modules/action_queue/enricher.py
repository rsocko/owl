"""Paperless custom-field enrichment for neutral document-level metadata."""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from doc_intelligence_hub.core.extractors.account_numbers import (
    normalize_masked_account_identifier,
)
from doc_intelligence_hub.core.paperless import (
    MetadataDiagnosticCode,
    MetadataFieldKey,
    MetadataSchemaError,
    PaperlessClient,
    PaperlessMetadataResolver,
    ResolvedMetadataSchema,
    build_metadata_update,
    get_metadata_field_spec,
)
from doc_intelligence_hub.modules.triage.database import (
    create_extraction_correction,
    has_correction_for_field,
)

from .config import settings

logger = logging.getLogger(__name__)


def _make_paperless_client() -> PaperlessClient:
    return PaperlessClient(base_url=settings.paperless_url, token=settings.paperless_api_token)


_ACTION_FIELDS = (
    MetadataFieldKey.ACCOUNT_IDENTIFIER,
    MetadataFieldKey.INVOICE_NUMBER,
    MetadataFieldKey.DOCUMENT_AMOUNT,
    MetadataFieldKey.DOCUMENT_DUE_DATE,
    MetadataFieldKey.ACTION_STATUS,
    MetadataFieldKey.ACTION_ANALYZED,
)
_LEGACY_CLEANUP_FIELDS = (
    MetadataFieldKey.LEGACY_ACTION_TYPE,
    MetadataFieldKey.LEGACY_ACTION_DUE_DATE,
    MetadataFieldKey.LEGACY_ACTION_URGENCY,
    MetadataFieldKey.LEGACY_ACTION_SUMMARY,
    MetadataFieldKey.LEGACY_ACTION_COUNT,
)
_ALL_ACTION_FIELDS = (*_ACTION_FIELDS, *_LEGACY_CLEANUP_FIELDS)

# Backward-compatible public constant; definitions are owned by the shared registry.
CUSTOM_FIELD_DEFINITIONS = [
    get_metadata_field_spec(key).create_definition() for key in _ACTION_FIELDS
]


class PaperlessEnricher:
    """Writes document metadata and OWL disposition back to Paperless."""

    def __init__(self):
        self.client = _make_paperless_client()
        self._schema: ResolvedMetadataSchema | None = None
        self._field_id_cache: dict[MetadataFieldKey, int] = {}

    @staticmethod
    def _resolve_field_write(
        document_id: int, field_name: str, *, source: str
    ) -> tuple[bool, bool]:
        """Decide whether to skip a write to ``field_name`` and whether a
        successful write should be recorded as the new authoritative
        correction.

        Returns ``(skip, record_after_success)``.

        Automated passes (``source="automated"``) skip a field once a
        user-submitted correction exists for it — corrections are
        authoritative and must not be silently clobbered. They never record
        a correction themselves.

        A human-driven Action Queue edit (``source="action_queue"``) is
        itself an authoritative decision, so it is never skipped, and it
        always establishes (or supersedes) the authoritative correction for
        the field — including on a first-time edit with no prior correction
        on file. Without recording a first edit too, a later automated pass
        would see no correction on record and silently clobber the human
        edit, defeating the guard entirely. The caller must only persist
        this *after* the Paperless write is confirmed to succeed, so a
        failed write never leaves a correction on file for a value that was
        never actually persisted.
        """
        if source == "automated":
            if has_correction_for_field(document_id, field_name):
                logger.info(
                    "Skipping automated %s write for document_id=%s: "
                    "an authoritative correction already exists",
                    field_name,
                    document_id,
                )
                return True, False
            return False, False
        return False, True

    @staticmethod
    def _record_action_queue_correction(
        document_id: int, field_name: str, new_value: str | None
    ) -> None:
        """Record a human-driven Action Queue edit as the new authoritative
        correction. Must only be called after the corresponding Paperless
        write has already succeeded."""
        create_extraction_correction(
            document_id=document_id,
            field_name=field_name,
            corrected_value=new_value,
            correction_type="action_queue_edit",
            notes="Recorded from an Action Queue edit, establishing (or superseding) "
            "the authoritative value for this field",
        )

    async def ensure_custom_fields_exist(self) -> dict[MetadataFieldKey, int]:
        """Ensure owned Action Queue fields exist and validate the deployed schema."""
        schema = await PaperlessMetadataResolver(self.client).ensure(_ALL_ACTION_FIELDS)
        self._set_schema(schema)
        for diagnostic in schema.diagnostics:
            if diagnostic.code is not MetadataDiagnosticCode.MISSING_FIELD:
                logger.warning("Paperless metadata schema diagnostic: %s", diagnostic.message)
        return dict(self._field_id_cache)

    def _set_schema(self, schema: ResolvedMetadataSchema) -> None:
        self._schema = schema
        self._field_id_cache = {
            key: resolved.canonical_id
            for key, resolved in schema.fields.items()
            if resolved.canonical_id is not None and resolved.is_compatible
        }

    async def get_schema(self) -> ResolvedMetadataSchema:
        if self._schema is None:
            await self.ensure_custom_fields_exist()
        if self._schema is None:
            raise MetadataSchemaError("Action Queue metadata schema was not initialized")
        return self._schema

    async def get_field_ids(self) -> dict[MetadataFieldKey, int]:
        await self.get_schema()
        return dict(self._field_id_cache)

    async def _write_custom_fields(self, document_id: int, updates: list[dict]) -> None:
        amount_field_id = self._field_id_cache.get(MetadataFieldKey.DOCUMENT_AMOUNT)
        numeric_field_ids = {amount_field_id} if amount_field_id is not None else set()
        await self.client.update_custom_fields_verified(
            document_id,
            updates,
            numeric_field_ids=numeric_field_ids,
        )

    async def enrich_document(
        self,
        document_id: int,
        extraction: dict,
        *,
        action_status: str | None = "pending",
        clear_action_inference: bool = False,
        source: str = "automated",
    ) -> None:
        """Write neutral document extraction results to Paperless.

        ``source`` distinguishes an automated pipeline pass (default) from a
        human-driven Action Queue edit. Automated passes skip any field that
        already has an authoritative user correction; an Action Queue edit is
        itself a human decision, so it is always written through and recorded
        as the new authoritative correction (superseding any earlier one) so
        later automated passes and the Metadata Correction UI stay in sync
        with this single canonical value.
        """
        if not settings.write_to_paperless:
            return

        schema = await self.get_schema()
        updates: list[dict] = []
        # Fields to record as new authoritative corrections, but only once the
        # batched Paperless write below actually succeeds.
        pending_corrections: list[tuple[str, str | None]] = []
        extracted_data = extraction.get("extracted_data")
        if not isinstance(extracted_data, dict):
            extracted_data = {}
        account_identifier = normalize_masked_account_identifier(
            extracted_data.get("account_identifier")
        )
        if account_identifier:
            skip, record_after = self._resolve_field_write(
                document_id, "account_identifier", source=source
            )
            if not skip:
                updates.append(
                    build_metadata_update(
                        MetadataFieldKey.ACCOUNT_IDENTIFIER,
                        account_identifier,
                        schema,
                    )
                )
                if record_after:
                    pending_corrections.append(("account_identifier", account_identifier))
        reference_number = extracted_data.get("reference_number")
        if isinstance(reference_number, str) and reference_number.strip():
            reference_number = reference_number.strip()
            skip, record_after = self._resolve_field_write(
                document_id, "invoice_number", source=source
            )
            if not skip:
                updates.append(
                    build_metadata_update(
                        MetadataFieldKey.INVOICE_NUMBER,
                        reference_number,
                        schema,
                    )
                )
                if record_after:
                    pending_corrections.append(("invoice_number", reference_number))
        if extraction.get("amount") is not None:
            skip, record_after = self._resolve_field_write(
                document_id, "document_amount", source=source
            )
            if not skip:
                updates.append(
                    build_metadata_update(
                        MetadataFieldKey.DOCUMENT_AMOUNT,
                        extraction["amount"],
                        schema,
                    )
                )
                if record_after:
                    pending_corrections.append(("document_amount", str(extraction["amount"])))
        if extraction.get("document_due_date") is not None:
            skip, record_after = self._resolve_field_write(
                document_id, "document_due_date", source=source
            )
            if not skip:
                updates.append(
                    build_metadata_update(
                        MetadataFieldKey.DOCUMENT_DUE_DATE,
                        extraction["document_due_date"],
                        schema,
                    )
                )
                if record_after:
                    pending_corrections.append(
                        ("document_due_date", str(extraction["document_due_date"]))
                    )
        if action_status is not None:
            updates.append(
                build_metadata_update(MetadataFieldKey.ACTION_STATUS, action_status, schema)
            )
        elif clear_action_inference:
            action_status_id = self._field_id_cache.get(MetadataFieldKey.ACTION_STATUS)
            if action_status_id is not None:
                updates.append({"field": action_status_id, "value": None})
        if clear_action_inference:
            for key in _LEGACY_CLEANUP_FIELDS:
                field_id = self._field_id_cache.get(key)
                if field_id is not None:
                    updates.append({"field": field_id, "value": None})
        updates.append(
            build_metadata_update(
                MetadataFieldKey.ACTION_ANALYZED,
                date.today().isoformat(),
                schema,
            )
        )

        await asyncio.sleep(settings.rate_limit_delay)
        logger.info(
            "Writing %d custom field(s) to Paperless document_id=%s: %s",
            len(updates),
            document_id,
            [update["field"] for update in updates],
        )
        await self._write_custom_fields(document_id, updates)
        # Only persist superseding corrections once the write above is
        # confirmed to have succeeded (no exception raised).
        for field_name, new_value in pending_corrections:
            self._record_action_queue_correction(document_id, field_name, new_value)

    async def sync_document_amount(
        self, document_id: int, amount: float | None, *, source: str = "automated"
    ) -> None:
        """Write or explicitly clear the canonical document amount.

        Skipped if a user-submitted correction already exists for this field
        and ``source="automated"`` — corrections are authoritative and must
        not be clobbered by an automated sync. A human-driven Action Queue
        edit (``source="action_queue"``) is always written through and
        recorded as the new authoritative correction.
        """
        if not settings.write_to_paperless:
            raise PermissionError("Paperless writes are disabled")
        skip, record_after = self._resolve_field_write(
            document_id, "document_amount", source=source
        )
        if skip:
            return
        schema = await self.get_schema()
        await self._write_custom_fields(
            document_id,
            [build_metadata_update(MetadataFieldKey.DOCUMENT_AMOUNT, amount, schema)],
        )
        if record_after:
            self._record_action_queue_correction(
                document_id, "document_amount", None if amount is None else str(amount)
            )

    async def sync_document_due_date(
        self, document_id: int, due_date: date | None, *, source: str = "automated"
    ) -> None:
        """Write or explicitly clear the canonical document due date.

        Skipped if a user-submitted correction already exists for this field
        and ``source="automated"`` — corrections are authoritative and must
        not be clobbered by an automated sync. A human-driven Action Queue
        edit (``source="action_queue"``) is always written through and
        recorded as the new authoritative correction.
        """
        if not settings.write_to_paperless:
            raise PermissionError("Paperless writes are disabled")
        skip, record_after = self._resolve_field_write(
            document_id, "document_due_date", source=source
        )
        if skip:
            return
        schema = await self.get_schema()
        await self._write_custom_fields(
            document_id,
            [build_metadata_update(MetadataFieldKey.DOCUMENT_DUE_DATE, due_date, schema)],
        )
        if record_after:
            self._record_action_queue_correction(
                document_id,
                "document_due_date",
                None if due_date is None else due_date.isoformat(),
            )

    async def sync_status(self, document_id: int, status: str) -> None:
        """Mirror a status change from the internal database to Paperless."""
        if not settings.write_to_paperless:
            return

        schema = await self.get_schema()
        updates = [build_metadata_update(MetadataFieldKey.ACTION_STATUS, status, schema)]
        if status == "not_an_action":
            for key in _LEGACY_CLEANUP_FIELDS:
                field_id = self._field_id_cache.get(key)
                if field_id is not None:
                    updates.append({"field": field_id, "value": None})

        await self._write_custom_fields(document_id, updates)

        resolved_statuses = {"completed", "dismissed", "not_an_action"}
        if settings.remove_source_tag_on_resolve and status in resolved_statuses:
            tags_to_remove = settings.monitor_tags
            if tags_to_remove:
                await self.client.remove_tags_from_document(document_id, tags_to_remove)
                logger.info(
                    "Removed source tags %s from document %d (status=%s)",
                    tags_to_remove,
                    document_id,
                    status,
                )

    async def read_paperless_status(self, document_id: int) -> str | None:
        """Read and normalize the deployed Action Status value."""
        schema = await self.get_schema()
        resolved = schema.field(MetadataFieldKey.ACTION_STATUS)
        if resolved.canonical_id is None:
            return None

        document = await self.client.get_document(document_id)
        for custom_field in document.get("custom_fields", []):
            try:
                field_id = int(custom_field.get("field"))
            except (TypeError, ValueError):
                continue
            if field_id != resolved.canonical_id:
                continue
            value = custom_field.get("value")
            label = resolved.select_label(value)
            if label is not None:
                return label
            return str(value).strip() if value is not None else None
        return None
