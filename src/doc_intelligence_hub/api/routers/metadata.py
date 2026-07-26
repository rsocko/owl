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
from doc_intelligence_hub.modules.triage.database import (
    create_extraction_correction,
    get_corrections_for_document,
    list_recent_corrections,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/metadata", tags=["metadata"])

# DI field name → Paperless custom field name
FIELD_TO_PAPERLESS: dict[str, str] = {
    "patient_name": "di_patient_name",
    "provider_name": "di_provider_name",
    "date_of_service": "di_date_of_service",
    "patient_responsibility": "di_patient_resp",
    "claim_number": "di_claim_number",
    "invoice_number": "di_invoice_number",
    "account_identifier": "di_account_id",
    "document_classification": "di_doc_type",
}


VALID_FIELD_NAMES = set(FIELD_TO_PAPERLESS.keys())


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

    # Build extracted fields from Paperless custom fields
    custom_field_values = {cf["field"]: cf["value"] for cf in doc.get("custom_fields", [])}

    # Resolve custom field names
    try:
        field_defs = await client.list_custom_fields()
    except Exception:
        field_defs = []
    field_id_to_name = {f["id"]: f["name"] for f in field_defs}

    # Build a map of Paperless field name → value
    paperless_values: dict[str, Any] = {}
    for fid, val in custom_field_values.items():
        fname = field_id_to_name.get(fid, str(fid))
        paperless_values[fname] = val

    # Map to DI fields
    extracted_fields: list[dict[str, Any]] = []
    for di_field, paperless_field in FIELD_TO_PAPERLESS.items():
        value = paperless_values.get(paperless_field)
        extracted_fields.append(
            {
                "field_name": di_field,
                "paperless_field": paperless_field,
                "value": value,
                "has_value": value is not None and value != "",
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

    # Resolve Paperless custom field IDs
    try:
        field_defs = await client.list_custom_fields()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to list Paperless custom fields: {exc}",
        ) from exc

    field_name_to_id = {f["name"]: f["id"] for f in field_defs}

    # Build custom field updates
    updates: list[dict] = []
    written_fields: list[str] = []
    missing_fields: list[str] = []

    for di_field, value in latest_per_field.items():
        paperless_name = FIELD_TO_PAPERLESS.get(di_field)
        if not paperless_name:
            missing_fields.append(di_field)
            continue
        field_id = field_name_to_id.get(paperless_name)
        if field_id is None:
            missing_fields.append(f"{di_field} (Paperless field '{paperless_name}' not found)")
            continue
        updates.append({"field": field_id, "value": value})
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
