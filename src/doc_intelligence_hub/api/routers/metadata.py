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

from doc_intelligence_hub.api.routers import make_paperless_client
from doc_intelligence_hub.core.paperless import (
    MetadataFieldKey,
    MetadataSchemaError,
    MetadataValueError,
    PaperlessMetadataResolver,
    build_metadata_update,
    get_metadata_field_spec,
    resolve_metadata_schema,
    resolve_metadata_value,
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
    "document_classification": MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE,
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


class ConfirmFieldRequest(BaseModel):
    field_name: str = Field(..., description="Name of the extracted field to confirm")
    current_value: str | None = Field(default=None, description="Current value being confirmed")
    confidence: float | None = Field(
        default=None, description="Original extraction confidence 0-100"
    )
    source_region: dict | None = Field(
        default=None, description="Bounding box / OCR region coordinates"
    )


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
async def get_document_metadata(doc_id: int, request: Request) -> dict[str, Any]:
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
    for api_name, key in _API_FIELD_KEYS.items():
        resolved_value = resolve_metadata_value(key, doc.get("custom_fields", []), schema)
        conflict = resolved_value.conflict
        if conflict is not None:
            conflicts.append(
                {
                    "field_name": api_name,
                    "selected_source": conflict.selected_source_name,
                    "conflicting_sources": [
                        {"source": source, "value": value}
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
        extracted_fields.append(
            {
                "field_name": api_name,
                "paperless_field": get_metadata_field_spec(key).canonical_name,
                "value": resolved_value.value,
                "has_value": resolved_value.value is not None,
                "source_field": resolved_value.source_name,
                "conflict": conflict is not None,
                "validation_error": resolved_value.validation_error,
            }
        )

    # Get corrections
    corrections = get_corrections_for_document(doc_id)

    # Build per-field correction map (latest correction per field)
    latest_corrections: dict[str, dict] = {}
    for c in corrections:
        fn = c["field_name"]
        if fn not in latest_corrections:
            latest_corrections[fn] = c

    return {
        "document_id": doc_id,
        "title": doc.get("title", ""),
        "paperless_url": f"/documents/{doc_id}/details",
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


@router.post("/{doc_id}/correct")
async def correct_field(doc_id: int, body: CorrectFieldRequest) -> dict[str, Any]:
    """Submit a field correction."""
    _validate_field_name(body.field_name)
    correction = create_extraction_correction(
        document_id=doc_id,
        field_name=body.field_name,
        original_value=body.original_value,
        corrected_value=body.corrected_value,
        confidence=body.confidence,
        correction_type="corrected",
        source_region=body.source_region,
        notes=body.notes,
    )
    return {"correction": correction}


@router.post("/{doc_id}/confirm")
async def confirm_field(doc_id: int, body: ConfirmFieldRequest) -> dict[str, Any]:
    """Confirm a field extraction is correct (positive training example)."""
    _validate_field_name(body.field_name)
    correction = create_extraction_correction(
        document_id=doc_id,
        field_name=body.field_name,
        original_value=body.current_value,
        corrected_value=body.current_value,
        confidence=body.confidence,
        correction_type="confirmed",
        source_region=body.source_region,
    )
    return {"correction": correction}


@router.post("/{doc_id}/writeback")
async def writeback_to_paperless(doc_id: int, request: Request) -> dict[str, Any]:
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

    if not latest_per_field:
        raise HTTPException(status_code=400, detail="No corrected values to write back.")

    client = make_paperless_client(request)

    try:
        schema = await PaperlessMetadataResolver(client).resolve(_API_FIELD_KEYS.values())
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to list Paperless custom fields: {exc}",
        ) from exc

    updates: list[dict] = []
    written_fields: list[str] = []
    missing_fields: list[str] = []

    for di_field, value in latest_per_field.items():
        key = _API_FIELD_KEYS.get(di_field)
        if key is None:
            missing_fields.append(di_field)
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
        await client.update_custom_fields(doc_id, updates)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to write to Paperless: {exc}",
        ) from exc

    logger.info("Wrote %d fields to Paperless doc %d: %s", len(updates), doc_id, written_fields)

    return {
        "document_id": doc_id,
        "written_fields": written_fields,
        "missing_fields": missing_fields,
        "update_count": len(updates),
    }
