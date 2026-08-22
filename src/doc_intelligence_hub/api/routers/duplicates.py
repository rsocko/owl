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
from doc_intelligence_hub.modules.triage.relationship_projection import (
    project_relationships_to_paperless,
)
from doc_intelligence_hub.modules.triage.relationships import (
    RelationshipConflictError,
    calculate_priority_adjustment,
    classify_related_notice,
    create_document_relationship,
    get_document_relationship,
    set_projection_result,
)

router = APIRouter(prefix="/api/duplicates", tags=["duplicates"])


# ------------------------------------------------------------------
# Request / response models
# ------------------------------------------------------------------


class ResolveRequest(BaseModel):
    resolution: str = Field(
        ...,
        description="Resolution: 'true_duplicate', 'superseded', 'related', or 'not_duplicate'",
    )
    primary_doc_id: int | None = Field(
        default=None,
        description="ID of the document to keep as primary (required for true_duplicate/superseded)",
    )
    relationship_type: str | None = Field(
        default=None,
        description="Typed relationship used when resolution is 'related'",
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
    await _project_automatic_relationships(result)
    return result


@router.post("/check-single")
async def check_single_document(body: CheckSingleRequest) -> dict[str, Any]:
    """Run duplicate detection for a single document (as if it were just ingested)."""
    result = on_document_ingested(body.document_id)
    await _project_automatic_relationships(result)
    return result


async def _project_automatic_relationships(result: dict[str, Any]) -> None:
    relationship_ids = result.get("relationship_ids", [])
    if not relationship_ids:
        return
    document_ids: set[int] = set()
    relationships = []
    for relationship_id in relationship_ids:
        relationship = get_document_relationship(relationship_id)
        if relationship:
            relationships.append(relationship)
            document_ids.update(
                {
                    relationship["source_document_id"],
                    relationship["target_document_id"],
                }
            )
    if not relationships:
        return
    try:
        projection = await project_relationships_to_paperless(document_ids)
    except Exception as exc:
        projection = {"synced": False, "error": str(exc)}
    for relationship in relationships:
        set_projection_result(
            relationship["id"],
            synced=bool(projection["synced"]),
            error=projection["error"],
        )
    result["projection"] = projection


@router.get("/{pair_id}")
async def get_duplicate(pair_id: str) -> dict[str, Any]:
    """Get a single duplicate pair with full detail including document metadata."""
    pair = get_duplicate_pair(pair_id)
    if not pair:
        raise HTTPException(status_code=404, detail=f"Duplicate pair {pair_id} not found")

    # Enrich with document metadata
    doc_a_meta = get_document_metadata(pair["doc_a_id"])
    doc_b_meta = get_document_metadata(pair["doc_b_id"])
    proposal = (
        classify_related_notice(pair["doc_a_id"], doc_a_meta, pair["doc_b_id"], doc_b_meta)
        if doc_a_meta and doc_b_meta
        else None
    )

    return {
        **pair,
        "doc_a_metadata": doc_a_meta,
        "doc_b_metadata": doc_b_meta,
        "relationship_proposal": proposal.to_dict() if proposal else None,
    }


@router.post("/{pair_id}/resolve")
async def resolve_duplicate(pair_id: str, body: ResolveRequest) -> dict[str, Any]:
    """Resolve a duplicate pair."""
    valid_resolutions = {"true_duplicate", "superseded", "related", "not_duplicate"}
    if body.resolution not in valid_resolutions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid resolution '{body.resolution}'. Must be one of: {', '.join(valid_resolutions)}",
        )

    if body.resolution in ("true_duplicate", "superseded", "related") and body.primary_doc_id is None:
        raise HTTPException(
            status_code=400,
            detail="primary_doc_id is required for true_duplicate, superseded, and related resolutions",
        )

    try:
        if body.resolution == "related":
            pair = get_duplicate_pair(pair_id)
            if not pair:
                raise ValueError(f"Duplicate pair {pair_id} not found")
            if body.primary_doc_id not in {pair["doc_a_id"], pair["doc_b_id"]}:
                raise ValueError("primary_doc_id must identify a document in the duplicate pair")
            relationship_type = body.relationship_type or "follows"
            target_document_id = (
                pair["doc_b_id"]
                if body.primary_doc_id == pair["doc_a_id"]
                else pair["doc_a_id"]
            )
            source_metadata = get_document_metadata(body.primary_doc_id) or {}
            source_text = " ".join(
                str(source_metadata.get(key) or "")
                for key in ("title", "summary", "content", "text")
            )
            priority_adjustment, priority_reasons, priority_explanation = (
                calculate_priority_adjustment(source_text, relationship_type)
            )
            relationship, created = create_document_relationship(
                source_document_id=body.primary_doc_id,
                target_document_id=target_document_id,
                relationship_type=relationship_type,
                provenance="user",
                confidence=pair["similarity_score"],
                reason_codes=["duplicate_review", *priority_reasons],
                priority_adjustment=priority_adjustment,
                priority_explanation=priority_explanation,
                source_duplicate_pair_id=pair_id,
            )
            duplicate = resolve_not_duplicate(pair_id)
            try:
                projection = await project_relationships_to_paperless(
                    {body.primary_doc_id, target_document_id}
                )
            except Exception as exc:
                projection = {
                    "synced": False,
                    "documents": [body.primary_doc_id, target_document_id],
                    "error": str(exc),
                }
            relationship = (
                set_projection_result(
                    relationship["id"],
                    synced=bool(projection["synced"]),
                    error=projection["error"],
                )
                or relationship
            )
            return {
                "duplicate": duplicate,
                "relationship": relationship,
                "relationship_created": created,
                "projection": projection,
            }
        if body.resolution == "not_duplicate":
            result = resolve_not_duplicate(pair_id)
        else:
            result = merge_documents(pair_id, body.primary_doc_id, body.resolution)
    except RelationshipConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return result
