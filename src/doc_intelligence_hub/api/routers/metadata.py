"""Metadata Correction & Writeback API router.

Endpoints:
    GET    /api/metadata/{doc_id}              — Extracted fields + corrections for a document
    POST   /api/metadata/{doc_id}/correct      — Submit a field correction
    POST   /api/metadata/{doc_id}/confirm      — Confirm a field extraction is correct
    POST   /api/metadata/{doc_id}/writeback    — Push corrections to Paperless custom fields
    GET    /api/metadata/corrections            — List recent corrections (training data export)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from doc_intelligence_hub.api.document_summary import (
    DocumentSummaryContext,
    build_document_summary,
)
from doc_intelligence_hub.api.routers import make_paperless_client
from doc_intelligence_hub.core.extractors.correction_hints import derive_label_anchor
from doc_intelligence_hub.core.paperless import (
    AccountIdentifierClass,
    MetadataFieldKey,
    MetadataSchemaError,
    MetadataValueError,
    PaperlessMetadataResolver,
    PaperlessRevisionConflict,
    build_account_identifier_update,
    build_metadata_update,
    get_metadata_field_spec,
    mask_account_identifier,
    resolve_metadata_schema,
    resolve_metadata_value,
)
from doc_intelligence_hub.core.paperless.writeback import (
    audit_paperless_write,
    build_write_conflicts,
    is_idempotent_replay,
    sanitize_initiator,
    values_equal,
)
from doc_intelligence_hub.modules.triage.database import (
    create_extraction_correction,
    get_corrections_for_document,
    list_recent_corrections,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/metadata", tags=["metadata"])

_API_FIELD_KEYS: dict[str, MetadataFieldKey] = {
    MetadataFieldKey.PATIENT_NAME.value: MetadataFieldKey.PATIENT_NAME,
    MetadataFieldKey.PROVIDER_NAME.value: MetadataFieldKey.PROVIDER_NAME,
    MetadataFieldKey.DATE_OF_SERVICE.value: MetadataFieldKey.DATE_OF_SERVICE,
    MetadataFieldKey.PATIENT_RESPONSIBILITY.value: MetadataFieldKey.PATIENT_RESPONSIBILITY,
    MetadataFieldKey.CLAIM_NUMBER.value: MetadataFieldKey.CLAIM_NUMBER,
    MetadataFieldKey.INVOICE_NUMBER.value: MetadataFieldKey.INVOICE_NUMBER,
    MetadataFieldKey.ACCOUNT_IDENTIFIER.value: MetadataFieldKey.ACCOUNT_IDENTIFIER,
    MetadataFieldKey.DOCUMENT_AMOUNT.value: MetadataFieldKey.DOCUMENT_AMOUNT,
    MetadataFieldKey.DOCUMENT_DUE_DATE.value: MetadataFieldKey.DOCUMENT_DUE_DATE,
}

FIELD_TO_PAPERLESS: dict[str, str] = {
    api_name: get_metadata_field_spec(key).canonical_name
    for api_name, key in _API_FIELD_KEYS.items()
}
VALID_FIELD_NAMES = set(_API_FIELD_KEYS)


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------


class CorrectFieldRequest(BaseModel):
    field_name: str = Field(..., description="Name of the extracted field")
    corrected_value: str = Field(..., description="The corrected value")
    original_value: str | None = Field(default=None, description="The original extracted value")
    confidence: float | None = Field(
        default=None, description="Original extraction confidence 0-100"
    )
    source_region: dict | None = Field(
        default=None, description="Bounding box / OCR region coordinates"
    )
    notes: str | None = Field(default=None, description="Optional correction notes")
    identifier_class: AccountIdentifierClass | None = Field(
        default=None,
        description="Required classification for an Account Identifier correction",
    )
    expected_modified: str | None = Field(
        default=None,
        description="Paperless modified revision captured when the field was loaded",
    )
    initiated_by: str = Field(default="owl-metadata-correction", min_length=1, max_length=64)


class ConfirmFieldRequest(BaseModel):
    field_name: str = Field(..., description="Name of the extracted field to confirm")
    current_value: str | None = Field(default=None, description="Current value being confirmed")
    confidence: float | None = Field(
        default=None, description="Original extraction confidence 0-100"
    )
    source_region: dict | None = Field(
        default=None, description="Bounding box / OCR region coordinates"
    )


class WritebackRequest(BaseModel):
    expected_modified: str
    base_values: dict[str, Any] = Field(default_factory=dict)
    field_names: list[str] | None = None
    initiated_by: str = Field(default="owl-metadata-correction", min_length=1, max_length=64)


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/corrections")
async def list_corrections(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    correction_type: str | None = None,
    field_name: str | None = None,
) -> dict[str, Any]:
    """List recent corrections for training data export."""
    corrections = list_recent_corrections(
        limit=limit,
        offset=offset,
        correction_type=correction_type,
        field_name=field_name,
    )
    return {"corrections": corrections, "count": len(corrections), "offset": offset, "limit": limit}


@router.get("/{doc_id}")
async def get_document_metadata(
    doc_id: int,
    request: Request,
    context: str | None = Query(default=None),
) -> dict[str, Any]:
    """Get extracted fields and corrections for a document.

    Fetches the document from Paperless and overlays any stored corrections.
    """
    client = make_paperless_client(request)
    try:
        doc = await client.get_document(doc_id)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to fetch document {doc_id} from Paperless: {exc}"
        ) from exc

    try:
        field_defs = await client.list_custom_fields()
    except Exception as exc:
        logger.warning("Failed to resolve Paperless metadata schema: %s", exc)
        field_defs = []
    schema = resolve_metadata_schema(field_defs, _API_FIELD_KEYS.values())

    extracted_fields: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    value_diagnostics: list[dict[str, str]] = []
    account_identifier: Any = None
    for api_name, key in _API_FIELD_KEYS.items():
        if key is MetadataFieldKey.ACCOUNT_IDENTIFIER and context != "account_review":
            continue
        resolved_value = resolve_metadata_value(key, doc.get("custom_fields", []), schema)
        conflict = resolved_value.conflict
        if conflict is not None:
            conflicts.append(
                {
                    "field_name": api_name,
                    "selected_source": conflict.selected_source_name,
                    "conflicting_sources": [
                        {
                            "source": source,
                            "value": (
                                value
                                if (
                                    key is MetadataFieldKey.ACCOUNT_IDENTIFIER
                                    and context == "account_review"
                                )
                                else mask_account_identifier(value)
                                if key is MetadataFieldKey.ACCOUNT_IDENTIFIER
                                else value
                            ),
                        }
                        for source, value in conflict.conflicting_sources
                    ],
                }
            )
        if resolved_value.validation_error is not None:
            value_diagnostics.append(
                {
                    "field_name": api_name,
                    "source_field": resolved_value.source_name or "",
                    "message": resolved_value.validation_error,
                }
            )
        field_payload = {
            "field_name": api_name,
            "paperless_field": get_metadata_field_spec(key).canonical_name,
            "has_value": resolved_value.value is not None,
            "source_field": resolved_value.source_name,
            "conflict": conflict is not None,
            "validation_error": resolved_value.validation_error,
        }
        if key is MetadataFieldKey.ACCOUNT_IDENTIFIER:
            account_identifier = resolved_value.value
            field_payload["value"] = resolved_value.value
            field_payload["account_identifier_display"] = resolved_value.value
        else:
            field_payload["value"] = resolved_value.value
        extracted_fields.append(field_payload)

    # Get corrections
    corrections = [
        _sanitize_correction(correction) for correction in get_corrections_for_document(doc_id)
    ]

    # Build per-field correction map (latest correction per field)
    latest_corrections: dict[str, dict] = {}
    for c in corrections:
        fn = c["field_name"]
        if fn not in latest_corrections:
            latest_corrections[fn] = c

    return {
        "document_id": doc_id,
        "title": doc.get("title", ""),
        "document_summary": build_document_summary(
            {
                **doc,
                "account_identifier": account_identifier,
            },
            context=(
                DocumentSummaryContext.ACCOUNT_REVIEW
                if context == "account_review"
                else DocumentSummaryContext.GENERAL
            ),
        ),
        "paperless_url": f"/documents/{doc_id}/details",
        "source_modified": doc.get("modified"),
        "extracted_fields": extracted_fields,
        "corrections": corrections,
        "latest_corrections": latest_corrections,
        "field_mapping": FIELD_TO_PAPERLESS,
        "metadata_conflicts": conflicts,
        "metadata_value_diagnostics": value_diagnostics,
        "schema_diagnostics": [
            {
                "field_name": diagnostic.key.value,
                "code": diagnostic.code.value,
                "message": diagnostic.message,
            }
            for diagnostic in schema.diagnostics
        ],
    }


def _validate_field_name(field_name: str) -> None:
    """Ensure field_name is a known extraction field."""
    if field_name not in VALID_FIELD_NAMES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown field name '{field_name}'. Valid fields: {sorted(VALID_FIELD_NAMES)}",
        )


def _sanitize_correction(correction: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(correction)
    if sanitized.get("field_name") == MetadataFieldKey.ACCOUNT_IDENTIFIER.value:
        sanitized["original_value"] = mask_account_identifier(sanitized.get("original_value"))
        sanitized["corrected_value"] = mask_account_identifier(sanitized.get("corrected_value"))
        sanitized["source_region"] = None
    return sanitized


async def _resolve_correction_context(
    client: object, doc_id: int, corrected_value: str | None
) -> tuple[str | None, str | None]:
    """Best-effort resolution of correspondent name + label anchor for a correction.

    Fetches the document once to resolve its correspondent (id -> name, via
    ``list_correspondents``) and to derive a short text anchor from the document's
    content immediately preceding the corrected value (see issue #171). Failures here
    should never block saving the correction itself, so any error just yields
    ``(None, None)``.
    """
    try:
        doc = await client.get_document(doc_id)
    except Exception as exc:
        logger.warning("Could not fetch Paperless correction context: %s", exc)
        return None, None

    correspondent_raw = doc.get("correspondent")
    correspondent_name: str | None = None
    if isinstance(correspondent_raw, str):
        correspondent_name = correspondent_raw or None
    elif correspondent_raw is not None:
        try:
            correspondents = await client.list_correspondents()
        except Exception as exc:
            logger.warning("Could not resolve correction correspondent: %s", exc)
            correspondents = []
        for correspondent in correspondents:
            if correspondent.get("id") == correspondent_raw:
                name = correspondent.get("name")
                correspondent_name = str(name) if name else None
                break

    label_anchor = derive_label_anchor(doc.get("content"), corrected_value)
    return correspondent_name, label_anchor


@router.post("/{doc_id}/correct")
async def correct_field(doc_id: int, body: CorrectFieldRequest, request: Request) -> dict[str, Any]:
    """Submit a field correction."""
    _validate_field_name(body.field_name)
    client = make_paperless_client(request)
    correspondent, label_anchor = await _resolve_correction_context(
        client, doc_id, body.corrected_value
    )
    if body.field_name == MetadataFieldKey.ACCOUNT_IDENTIFIER.value:
        if body.identifier_class is None:
            raise HTTPException(
                status_code=422,
                detail="identifier_class is required for Account Identifier corrections",
            )
        if not body.expected_modified:
            raise HTTPException(
                status_code=422,
                detail="expected_modified is required for Account Identifier corrections",
            )
        initiator = sanitize_initiator(body.initiated_by, fallback="owl-metadata-correction")
        try:
            current = await client.get_document(doc_id)
            schema = await PaperlessMetadataResolver(client).resolve(
                (MetadataFieldKey.ACCOUNT_IDENTIFIER,)
            )
            current_value = resolve_metadata_value(
                MetadataFieldKey.ACCOUNT_IDENTIFIER,
                current.get("custom_fields", []),
                schema,
            ).value
            loaded_current_value = current_value
            update, projection = build_account_identifier_update(
                body.corrected_value,
                body.identifier_class,
                1.0,
                schema,
            )
            replayed = current.get("modified") != body.expected_modified and values_equal(
                current_value, body.corrected_value
            )
            if current.get("modified") != body.expected_modified and not replayed:
                audit_paperless_write(
                    logger,
                    initiator=initiator,
                    surface="metadata-correction-account",
                    fields=[body.field_name],
                    outcome="conflict",
                )
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "source_revision_conflict",
                        "message": "This field changed in Paperless. Resolve it before saving.",
                        "expected_modified": body.expected_modified,
                        "current_modified": current.get("modified"),
                        "conflicts": build_write_conflicts(
                            loaded={body.field_name: body.original_value},
                            current={body.field_name: current_value},
                            proposed={body.field_name: body.corrected_value},
                            labels={body.field_name: "Account Identifier"},
                        ),
                    },
                )
            if not replayed:
                await client.update_custom_fields(
                    doc_id,
                    [update],
                    expected_modified=current.get("modified"),
                )
            audit_paperless_write(
                logger,
                initiator=initiator,
                surface="metadata-correction-account",
                fields=[body.field_name],
                outcome="replayed" if replayed else "succeeded",
            )
        except (MetadataSchemaError, MetadataValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except PaperlessRevisionConflict as exc:
            race_value = resolve_metadata_value(
                MetadataFieldKey.ACCOUNT_IDENTIFIER,
                exc.current_document.get("custom_fields", []),
                schema,
            ).value
            audit_paperless_write(
                logger,
                initiator=initiator,
                surface="metadata-correction-account",
                fields=[body.field_name],
                outcome="conflict",
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "source_revision_conflict",
                    "message": "This field changed in Paperless. Resolve it before saving.",
                    "expected_modified": current.get("modified"),
                    "current_modified": exc.current_document.get("modified"),
                    "conflicts": build_write_conflicts(
                        loaded={body.field_name: loaded_current_value},
                        current={body.field_name: race_value},
                        proposed={body.field_name: body.corrected_value},
                        labels={body.field_name: "Account Identifier"},
                    ),
                },
            ) from exc
        except HTTPException:
            raise
        except Exception as exc:
            audit_paperless_write(
                logger,
                initiator=initiator,
                surface="metadata-correction-account",
                fields=[body.field_name],
                outcome="failed",
            )
            raise HTTPException(
                status_code=502,
                detail="Failed to write Account Identifier to Paperless",
            ) from exc
        if replayed:
            existing = next(
                (
                    correction
                    for correction in get_corrections_for_document(doc_id)
                    if correction.get("field_name") == body.field_name
                    and correction.get("corrected_value") == projection.display_value
                ),
                None,
            )
            if existing is not None:
                return {
                    "correction": existing,
                    "written_to_paperless": True,
                    "idempotent_replay": True,
                }
        correction = create_extraction_correction(
            document_id=doc_id,
            field_name=body.field_name,
            original_value=mask_account_identifier(body.original_value, body.identifier_class),
            corrected_value=projection.display_value,
            confidence=100,
            correction_type="corrected",
            source_region=None,
            correspondent=correspondent,
            label_anchor=label_anchor,
            notes=None,
        )
        return {
            "correction": correction,
            "written_to_paperless": True,
            "idempotent_replay": replayed,
        }

    correction = create_extraction_correction(
        document_id=doc_id,
        field_name=body.field_name,
        original_value=body.original_value,
        corrected_value=body.corrected_value,
        confidence=body.confidence,
        correction_type="corrected",
        source_region=body.source_region,
        correspondent=correspondent,
        label_anchor=label_anchor,
        notes=body.notes,
    )
    return {"correction": correction}


@router.post("/{doc_id}/confirm")
async def confirm_field(doc_id: int, body: ConfirmFieldRequest, request: Request) -> dict[str, Any]:
    """Confirm a field extraction is correct (positive training example)."""
    _validate_field_name(body.field_name)
    if body.field_name == MetadataFieldKey.ACCOUNT_IDENTIFIER.value:
        raise HTTPException(
            status_code=422,
            detail="Use the classified Account Identifier correction endpoint",
        )
    client = make_paperless_client(request)
    correspondent, label_anchor = await _resolve_correction_context(
        client, doc_id, body.current_value
    )
    correction = create_extraction_correction(
        document_id=doc_id,
        field_name=body.field_name,
        original_value=body.current_value,
        corrected_value=body.current_value,
        confidence=body.confidence,
        correction_type="confirmed",
        source_region=body.source_region,
        correspondent=correspondent,
        label_anchor=label_anchor,
    )
    return {"correction": correction}


@router.post("/{doc_id}/writeback")
async def writeback_to_paperless(
    doc_id: int, body: WritebackRequest, request: Request
) -> dict[str, Any]:
    """Push all corrections to Paperless custom fields.

    Reads the latest correction for each field, resolves Paperless custom field IDs,
    and writes them via the Paperless API.
    """
    corrections = get_corrections_for_document(doc_id)
    if not corrections:
        raise HTTPException(status_code=400, detail="No corrections found for this document.")

    # Get latest value per field (allow empty string as a valid correction)
    latest_per_field: dict[str, str] = {}
    for c in corrections:
        fn = c["field_name"]
        if fn not in latest_per_field and c.get("corrected_value") is not None:
            latest_per_field[fn] = c["corrected_value"]

    if body.field_names is not None:
        unknown_fields = sorted(set(body.field_names) - VALID_FIELD_NAMES)
        if unknown_fields:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown writeback fields: {unknown_fields}",
            )
        latest_per_field = {
            field: value
            for field, value in latest_per_field.items()
            if field in set(body.field_names)
        }

    if not latest_per_field:
        raise HTTPException(status_code=400, detail="No corrected values to write back.")

    client = make_paperless_client(request)
    initiator = sanitize_initiator(body.initiated_by, fallback="owl-metadata-correction")

    try:
        current = await client.get_document(doc_id)
        definitions = await client.list_custom_fields()
        schema = resolve_metadata_schema(definitions, _API_FIELD_KEYS.values())
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to load current Paperless metadata: {exc}",
        ) from exc

    current_values = {
        field: resolve_metadata_value(
            _API_FIELD_KEYS[field],
            current.get("custom_fields", []),
            schema,
        ).value
        for field in latest_per_field
    }
    replayed = current.get("modified") != body.expected_modified and is_idempotent_replay(
        current_values, latest_per_field
    )
    if current.get("modified") != body.expected_modified and not replayed:
        audit_paperless_write(
            logger,
            initiator=initiator,
            surface="metadata-correction-writeback",
            fields=list(latest_per_field),
            outcome="conflict",
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "source_revision_conflict",
                "message": "Paperless metadata changed after this page was loaded.",
                "expected_modified": body.expected_modified,
                "current_modified": current.get("modified"),
                "conflicts": build_write_conflicts(
                    loaded=body.base_values,
                    current=current_values,
                    proposed=latest_per_field,
                    labels=FIELD_TO_PAPERLESS,
                ),
            },
        )

    updates: list[dict] = []
    written_fields: list[str] = []
    missing_fields: list[str] = []

    for di_field, value in latest_per_field.items():
        key = _API_FIELD_KEYS.get(di_field)
        if key is None:
            missing_fields.append(di_field)
            continue
        if key is MetadataFieldKey.ACCOUNT_IDENTIFIER:
            missing_fields.append(
                "account_identifier (use the classified direct-write correction endpoint)"
            )
            continue
        try:
            updates.append(build_metadata_update(key, value, schema))
        except (MetadataSchemaError, MetadataValueError) as exc:
            missing_fields.append(f"{di_field} ({exc})")
            continue
        written_fields.append(di_field)

    if not updates:
        raise HTTPException(
            status_code=400,
            detail=f"No matching Paperless custom fields found. Missing: {missing_fields}",
        )

    try:
        updated = (
            await client.update_custom_fields(
                doc_id,
                updates,
                expected_modified=current.get("modified"),
            )
            if not replayed
            else current
        )
    except PaperlessRevisionConflict as exc:
        race_values = {
            field: resolve_metadata_value(
                _API_FIELD_KEYS[field],
                exc.current_document.get("custom_fields", []),
                schema,
            ).value
            for field in latest_per_field
        }
        audit_paperless_write(
            logger,
            initiator=initiator,
            surface="metadata-correction-writeback",
            fields=list(latest_per_field),
            outcome="conflict",
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "source_revision_conflict",
                "message": "Paperless metadata changed while the write was being prepared.",
                "expected_modified": current.get("modified"),
                "current_modified": exc.current_document.get("modified"),
                "conflicts": build_write_conflicts(
                    loaded=current_values,
                    current=race_values,
                    proposed=latest_per_field,
                    labels=FIELD_TO_PAPERLESS,
                ),
            },
        ) from exc
    except Exception as exc:
        audit_paperless_write(
            logger,
            initiator=initiator,
            surface="metadata-correction-writeback",
            fields=list(latest_per_field),
            outcome="failed",
        )
        raise HTTPException(
            status_code=502,
            detail=f"Failed to write to Paperless: {exc}",
        ) from exc

    audit_paperless_write(
        logger,
        initiator=initiator,
        surface="metadata-correction-writeback",
        fields=written_fields,
        outcome="replayed" if replayed else "succeeded",
    )

    return {
        "document_id": doc_id,
        "written_fields": written_fields,
        "missing_fields": missing_fields,
        "update_count": len(updates),
        "source_modified": updated.get("modified"),
        "idempotent_replay": replayed,
    }
