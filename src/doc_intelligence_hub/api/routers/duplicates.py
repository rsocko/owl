"""Duplicate Detection API router — manage duplicate document pairs.

Endpoints:
    GET    /api/duplicates                — List duplicate pairs (with status filter)
    GET    /api/duplicates/settings       — Get duplicate detection settings
    PUT    /api/duplicates/settings       — Update duplicate detection settings
    GET    /api/duplicates/{id}           — Single pair detail with document metadata
    POST   /api/duplicates/{id}/resolve   — Resolve a duplicate pair
    POST   /api/duplicates/scan           — Trigger a full duplicate scan
    POST   /api/duplicates/check-single   — Run detection for a single document
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from doc_intelligence_hub.api.document_summary import build_document_summary
from doc_intelligence_hub.modules.triage.database import (
    get_duplicate_pair,
    get_triage_setting,
    list_duplicate_pairs,
    set_triage_setting,
)
from doc_intelligence_hub.modules.triage.duplicates import (
    get_document_metadata,
    merge_documents,
    on_document_ingested,
    resolve_not_duplicate,
    scan_all_duplicates,
)

router = APIRouter(prefix="/api/duplicates", tags=["duplicates"])


# ------------------------------------------------------------------
# Request / response models
# ------------------------------------------------------------------


class ResolveRequest(BaseModel):
    resolution: str = Field(
        ...,
        description="Resolution: 'true_duplicate', 'superseded', or 'not_duplicate'",
    )
    primary_doc_id: int | None = Field(
        default=None,
        description="ID of the document to keep as primary (required for true_duplicate/superseded)",
    )


class DuplicateSettingsRequest(BaseModel):
    auto_detect_enabled: bool = Field(
        ...,
        description="Whether to automatically run duplicate detection when a document is ingested",
    )


class CheckSingleRequest(BaseModel):
    document_id: int = Field(
        ...,
        description="ID of the document to check for duplicates",
    )


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("")
async def list_duplicates(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List duplicate pairs with optional status filter."""
    pairs = list_duplicate_pairs(status=status, limit=min(limit, 200), offset=offset)
    return {"pairs": pairs, "count": len(pairs), "offset": offset, "limit": limit}


@router.get("/settings")
async def get_duplicate_settings() -> dict[str, Any]:
    """Get duplicate detection settings."""
    auto_detect = get_triage_setting("duplicate_auto_detect", "false")
    return {"auto_detect_enabled": auto_detect == "true"}


@router.put("/settings")
async def update_duplicate_settings(body: DuplicateSettingsRequest) -> dict[str, Any]:
    """Update duplicate detection settings."""
    set_triage_setting("duplicate_auto_detect", "true" if body.auto_detect_enabled else "false")
    return {"auto_detect_enabled": body.auto_detect_enabled}


@router.post("/scan")
async def trigger_scan() -> dict[str, Any]:
    """Trigger a full duplicate detection scan."""
    result = scan_all_duplicates()
    return result


@router.post("/check-single")
async def check_single_document(body: CheckSingleRequest) -> dict[str, Any]:
    """Run duplicate detection for a single document (as if it were just ingested)."""
    result = on_document_ingested(body.document_id)
    return result


@router.get("/{pair_id}")
async def get_duplicate(pair_id: str) -> dict[str, Any]:
    """Get a single duplicate pair with full detail including document metadata."""
    pair = get_duplicate_pair(pair_id)
    if not pair:
        raise HTTPException(status_code=404, detail=f"Duplicate pair {pair_id} not found")

    # Enrich with document metadata
    doc_a_meta = get_document_metadata(pair["doc_a_id"])
    doc_b_meta = get_document_metadata(pair["doc_b_id"])

    return {
        **pair,
        "doc_a_metadata": doc_a_meta,
        "doc_b_metadata": doc_b_meta,
        "doc_a_summary": build_document_summary(
            doc_a_meta or {"document_id": pair["doc_a_id"]}
        ),
        "doc_b_summary": build_document_summary(
            doc_b_meta or {"document_id": pair["doc_b_id"]}
        ),
    }


@router.post("/{pair_id}/resolve")
async def resolve_duplicate(pair_id: str, body: ResolveRequest) -> dict[str, Any]:
    """Resolve a duplicate pair."""
    valid_resolutions = {"true_duplicate", "superseded", "not_duplicate"}
    if body.resolution not in valid_resolutions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid resolution '{body.resolution}'. Must be one of: {', '.join(valid_resolutions)}",
        )

    if body.resolution in ("true_duplicate", "superseded") and body.primary_doc_id is None:
        raise HTTPException(
            status_code=400,
            detail="primary_doc_id is required for true_duplicate and superseded resolutions",
        )

    try:
        if body.resolution == "not_duplicate":
            result = resolve_not_duplicate(pair_id)
        else:
            result = merge_documents(pair_id, body.primary_doc_id, body.resolution)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return result
