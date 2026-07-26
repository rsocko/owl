"""Duplicate Detection API router — manage duplicate document pairs.

Endpoints:
    GET    /api/duplicates                — List duplicate pairs (with status filter)
    GET    /api/duplicates/{id}           — Single pair detail with document metadata
    POST   /api/duplicates/{id}/resolve   — Resolve a duplicate pair
    POST   /api/duplicates/scan           — Trigger a full duplicate scan
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from doc_intelligence_hub.modules.triage.database import (
    get_duplicate_pair,
    list_duplicate_pairs,
)
from doc_intelligence_hub.modules.triage.duplicates import (
    merge_documents,
    resolve_not_duplicate,
    scan_all_duplicates,
    _get_document_metadata,
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


@router.get("/{pair_id}")
async def get_duplicate(pair_id: str) -> dict[str, Any]:
    """Get a single duplicate pair with full detail including document metadata."""
    pair = get_duplicate_pair(pair_id)
    if not pair:
        raise HTTPException(status_code=404, detail=f"Duplicate pair {pair_id} not found")

    # Enrich with document metadata
    doc_a_meta = _get_document_metadata(pair["doc_a_id"])
    doc_b_meta = _get_document_metadata(pair["doc_b_id"])

    return {
        **pair,
        "doc_a_metadata": doc_a_meta,
        "doc_b_metadata": doc_b_meta,
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
        raise HTTPException(status_code=404, detail=str(exc))

    return result


@router.post("/scan")
async def trigger_scan() -> dict[str, Any]:
    """Trigger a full duplicate detection scan."""
    result = scan_all_duplicates()
    return result
