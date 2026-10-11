"""Typed document relationship API."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from doc_intelligence_hub.modules.action_queue.relationship_graph import (
    RelationshipGraphNotFoundError,
    build_relationship_graph,
)
from doc_intelligence_hub.modules.triage.duplicates import get_document_metadata
from doc_intelligence_hub.modules.triage.relationship_projection import (
    project_relationships_to_paperless,
)
from doc_intelligence_hub.modules.triage.relationships import (
    PROVENANCE_TYPES,
    RELATIONSHIP_TYPES,
    RelationshipConflictError,
    classify_related_notice,
    create_document_relationship,
    get_projection_health,
    list_document_relationships,
    list_pending_projection_relationships,
    remove_document_relationship,
    set_projection_result,
)

router = APIRouter(prefix="/api/relationships", tags=["document-relationships"])
logger = logging.getLogger(__name__)


class RelationshipCreateRequest(BaseModel):
    source_document_id: int = Field(gt=0)
    target_document_id: int = Field(gt=0)
    relationship_type: str
    provenance: str = "user"
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)
    priority_adjustment: int = Field(default=0, ge=0, le=100)
    priority_explanation: str = ""
    source_duplicate_pair_id: str | None = None
    project_to_paperless: bool = True


class RelationshipProposalRequest(BaseModel):
    left_document_id: int = Field(gt=0)
    right_document_id: int = Field(gt=0)
    left_metadata: dict[str, Any] | None = None
    right_metadata: dict[str, Any] | None = None


@router.get("/graph")
async def get_relationship_graph(
    action_id: int = Query(gt=0),
) -> dict[str, Any]:
    """Return the read-only OWL relationship projection for one action."""
    try:
        return build_relationship_graph(action_id)
    except RelationshipGraphNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _project_and_record(relationship: dict[str, Any]) -> dict[str, Any]:
    try:
        projection = await project_relationships_to_paperless(
            {
                relationship["source_document_id"],
                relationship["target_document_id"],
            }
        )
    except Exception:
        logger.exception("Failed to project document relationship to Paperless")
        projection = {
            "synced": False,
            "documents": [
                relationship["source_document_id"],
                relationship["target_document_id"],
            ],
            "error": "Failed to sync relationship projection to Paperless",
        }
    updated = set_projection_result(
        relationship["id"],
        synced=bool(projection["synced"]),
        error=projection["error"],
    )
    return {"relationship": updated or relationship, "projection": projection}


@router.post("/propose")
async def propose_relationship(body: RelationshipProposalRequest) -> dict[str, Any]:
    left = body.left_metadata or get_document_metadata(body.left_document_id)
    right = body.right_metadata or get_document_metadata(body.right_document_id)
    if left is None or right is None:
        raise HTTPException(status_code=404, detail="Document metadata is unavailable")
    proposal = classify_related_notice(
        body.left_document_id,
        left,
        body.right_document_id,
        right,
    )
    return {"proposal": proposal.to_dict() if proposal else None}


@router.post("")
async def create_relationship(body: RelationshipCreateRequest) -> dict[str, Any]:
    if body.relationship_type not in RELATIONSHIP_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported relationship type")
    if body.provenance not in PROVENANCE_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported provenance")
    try:
        relationship, created = create_document_relationship(
            source_document_id=body.source_document_id,
            target_document_id=body.target_document_id,
            relationship_type=body.relationship_type,
            provenance=body.provenance,
            confidence=body.confidence,
            reason_codes=body.reason_codes,
            priority_adjustment=body.priority_adjustment,
            priority_explanation=body.priority_explanation,
            source_duplicate_pair_id=body.source_duplicate_pair_id,
            project_to_paperless=body.project_to_paperless,
        )
    except RelationshipConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not body.project_to_paperless:
        return {"relationship": relationship, "created": created, "projection": None}
    result = await _project_and_record(relationship)
    return {**result, "created": created}


@router.get("/documents/{document_id}")
async def get_document_relationships(
    document_id: int,
    direction: str = Query(default="all", pattern="^(all|incoming|outgoing)$"),
    include_removed: bool = False,
) -> dict[str, Any]:
    relationships = list_document_relationships(
        document_id,
        direction=direction,
        include_removed=include_removed,
    )
    return {"relationships": relationships, "count": len(relationships)}


@router.post("/reconcile-projections")
async def reconcile_relationship_projections(
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """Retry durable Paperless projections that did not previously sync."""
    relationships = list_pending_projection_relationships(limit)
    if not relationships:
        return {
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "remaining": 0,
            "errors": [],
        }

    succeeded = 0
    errors: list[dict[str, str]] = []
    for relationship in relationships:
        try:
            projection = await project_relationships_to_paperless(
                {
                    relationship["source_document_id"],
                    relationship["target_document_id"],
                }
            )
        except Exception:
            logger.exception(
                "Failed to reconcile relationship projection %s",
                relationship["id"],
            )
            projection = {
                "synced": False,
                "error": "Failed to sync relationship projection to Paperless",
            }
        set_projection_result(
            relationship["id"],
            synced=bool(projection["synced"]),
            error=projection["error"],
        )
        if projection["synced"]:
            succeeded += 1
        else:
            errors.append(
                {
                    "relationship_id": relationship["id"],
                    "message": projection["error"]
                    or "Failed to sync relationship projection to Paperless",
                }
            )
    health = get_projection_health()
    failed = len(relationships) - succeeded
    logger.info(
        "Relationship projection reconciliation completed: attempted=%d succeeded=%d failed=%d",
        len(relationships),
        succeeded,
        failed,
    )
    return {
        "attempted": len(relationships),
        "succeeded": succeeded,
        "failed": failed,
        "remaining": health["pending"] + health["failed"],
        "errors": errors,
    }


@router.get("/projection-health")
async def relationship_projection_health() -> dict[str, Any]:
    """Return aggregate projection health without document content or connector details."""
    return get_projection_health()


@router.delete("/{relationship_id}")
async def delete_relationship(
    relationship_id: str,
    removed_by: str = "user",
    project_to_paperless: bool = True,
) -> dict[str, Any]:
    relationship = remove_document_relationship(relationship_id, removed_by=removed_by)
    if not relationship:
        raise HTTPException(status_code=404, detail="Document relationship not found")
    if not project_to_paperless or not relationship["paperless_projection_enabled"]:
        return {"relationship": relationship, "projection": None}
    return await _project_and_record(relationship)
