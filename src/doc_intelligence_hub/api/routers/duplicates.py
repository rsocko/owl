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

import hashlib
import json
import logging
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from doc_intelligence_hub.api.document_summary import build_document_summary
from doc_intelligence_hub.api.routers import make_paperless_client
from doc_intelligence_hub.core.paperless.client import ProgressCallback
from doc_intelligence_hub.modules.action_queue.config import settings as action_queue_settings
from doc_intelligence_hub.modules.action_queue.database import (
    QueueConfiguration,
)
from doc_intelligence_hub.modules.action_queue.database import (
    get_session as get_action_queue_session,
)
from doc_intelligence_hub.modules.action_queue.database import (
    init_db as init_action_queue_db,
)
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
from doc_intelligence_hub.modules.triage.relationship_discovery import (
    discover_paperless_documents,
    validate_date_range,
)
from doc_intelligence_hub.modules.triage.relationship_projection import (
    project_relationships_to_paperless,
)
from doc_intelligence_hub.modules.triage.relationship_scans import (
    ScanAlreadyActiveError,
    create_scan_run,
    get_scan_run,
    recover_abandoned_runs,
    update_scan_run,
)
from doc_intelligence_hub.modules.triage.relationships import (
    RelationshipConflictError,
    calculate_priority_adjustment,
    classify_related_notice,
    create_document_relationship,
    get_document_relationship,
    set_projection_result,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/duplicates", tags=["duplicates"])
_active_scan_run_ids: set[str] = set()


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


class RelationshipScanScope(BaseModel):
    kind: Literal["action_queue", "tag", "saved_view", "date_range", "all"] = "action_queue"
    tags: list[str] = Field(default_factory=list, max_length=20)
    saved_view_id: int | None = Field(default=None, gt=0)
    created_after: date | None = None
    created_before: date | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> RelationshipScanScope:
        self.tags = [tag.strip() for tag in self.tags if tag.strip()]
        if self.kind == "tag" and not self.tags:
            raise ValueError("At least one Paperless tag is required")
        if self.kind == "saved_view" and self.saved_view_id is None:
            raise ValueError("A Paperless saved view is required")
        if self.kind == "date_range":
            if self.created_after is None and self.created_before is None:
                raise ValueError("At least one date boundary is required")
            validate_date_range(self.created_after, self.created_before)
        return self

    def discovery_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "tags": self.tags,
            "saved_view_id": self.saved_view_id,
            "created_after": self.created_after.isoformat() if self.created_after else None,
            "created_before": self.created_before.isoformat() if self.created_before else None,
        }


def _action_queue_source() -> dict[str, Any]:
    init_action_queue_db()
    session = get_action_queue_session()
    try:
        stored = session.get(QueueConfiguration, 1)
        if stored is None:
            return {
                "scan_mode": "tags",
                "monitor_tags": list(action_queue_settings.monitor_tags),
                "saved_view_id": None,
                "document_limit": None,
            }
        return {
            "scan_mode": stored.scan_mode,
            "monitor_tags": list(stored.monitor_tags or []),
            "saved_view_id": stored.saved_view_id,
            "document_limit": stored.document_limit,
        }
    finally:
        session.close()


def _scan_scope_key(scope: RelationshipScanScope) -> str:
    payload = json.dumps(scope.discovery_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_projection_summary(projection: dict[str, Any] | None) -> dict[str, Any] | None:
    if projection is None:
        return {"status": "not_required", "error_code": None, "message": None}
    return {
        "status": projection.get("status", "synced" if projection.get("synced") else "failed"),
        "error_code": projection.get("error_code"),
        "message": projection.get("error"),
    }


async def _discover_scan_documents(
    request: Request,
    scope: RelationshipScanScope,
    *,
    on_progress: ProgressCallback | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    client = make_paperless_client(request, timeout=60.0)
    try:
        discovery_scope = scope.discovery_dict()
        discovery_scope["on_progress"] = on_progress
        return await discover_paperless_documents(
            client,
            scope=discovery_scope,
            action_queue_source=_action_queue_source(),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "relationship_scan_scope_invalid",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:
        logger.exception("Paperless relationship scan discovery failed")
        raise HTTPException(
            status_code=502,
            detail={
                "code": "relationship_scan_discovery_failed",
                "message": "Paperless documents could not be discovered for this scope.",
            },
        ) from exc
    finally:
        await client.aclose()


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
async def trigger_scan(
    request: Request, body: RelationshipScanScope | None = None
) -> dict[str, Any]:
    """Run a compatible synchronous relationship scan for one Paperless scope."""
    scope = body or RelationshipScanScope()
    documents, discovery = await _discover_scan_documents(request, scope)
    result = scan_all_duplicates(documents=documents, discovery=discovery)
    await _project_automatic_relationships(result, request)
    return result


@router.post("/scan/preview")
async def preview_historical_relationship_scan(
    request: Request, body: RelationshipScanScope | None = None
) -> dict[str, Any]:
    """Estimate the historical scan without changing OWL or Paperless state."""
    scope = body or RelationshipScanScope()
    documents, discovery = await _discover_scan_documents(request, scope)
    return scan_all_duplicates(
        dry_run=True,
        documents=documents,
        discovery=discovery,
    )


async def _run_tracked_scan(
    run_id: str,
    request: Request,
    scope: RelationshipScanScope,
) -> None:
    counts: dict[str, Any] = {}

    def record_progress(progress: dict[str, int]) -> None:
        counts.update(progress)
        update_scan_run(run_id, counts=counts)

    async def record_discovery_progress(
        _stage: str,
        _message: str,
        current: int,
        total: int,
    ) -> None:
        counts.update(
            {
                "documents_discovered": current,
                "documents_total": total,
                "scan_stage": "discovering",
            }
        )
        update_scan_run(run_id, counts=counts)

    try:
        update_scan_run(run_id, status="running", counts=counts)
        documents, discovery = await _discover_scan_documents(
            request,
            scope,
            on_progress=record_discovery_progress,
        )
        counts.update(
            {
                "documents_discovered": discovery["documents_discovered"],
                "documents_total": discovery["documents_discovered"],
                "scope": discovery["scope"],
            }
        )
        update_scan_run(run_id, counts=counts)
        result = scan_all_duplicates(
            on_progress=record_progress,
            documents=documents,
            discovery=discovery,
        )
        projection = await _project_automatic_relationships(result, request)
        counts.update(
            {
                key: value
                for key, value in result.items()
                if key not in {"relationship_ids", "projection"}
            }
        )
        update_scan_run(
            run_id,
            status="completed",
            counts=counts,
            errors=result.get("errors", []),
            projection=_safe_projection_summary(projection),
        )
    except Exception as exc:
        logger.exception("Tracked historical relationship scan failed")
        update_scan_run(
            run_id,
            status="failed",
            counts=counts,
            errors=[
                {
                    "code": type(exc).__name__,
                    "message": "The relationship scan could not be completed.",
                }
            ],
        )
    finally:
        _active_scan_run_ids.discard(run_id)


@router.post("/scan/runs", status_code=202)
async def start_historical_relationship_scan(
    background_tasks: BackgroundTasks,
    request: Request,
    body: RelationshipScanScope | None = None,
) -> dict[str, Any]:
    """Start one durable, tracked historical relationship scan."""
    recover_abandoned_runs(_active_scan_run_ids)
    scope = body or RelationshipScanScope()
    scope_key = _scan_scope_key(scope)
    try:
        run = create_scan_run(scope_key=scope_key, scope_kind=scope.kind)
    except ScanAlreadyActiveError as exc:
        active = exc.run
        if exc.scope_key == scope_key:
            stored = get_scan_run(active["run_id"])
            if stored is not None:
                return {**stored, "attached": True}
        raise HTTPException(
            status_code=409,
            detail={
                "code": "relationship_scan_scope_conflict",
                "message": (
                    "A historical relationship scan is already in progress for another scope."
                ),
                "run_id": active["run_id"],
                "active_scope": active.get("scope", {"kind": "unknown"}),
            },
        ) from exc
    run_id = run["run_id"]
    _active_scan_run_ids.add(run_id)
    run["counts"] = {"scope": {"kind": scope.kind}}
    run["attached"] = False
    update_scan_run(run_id, counts=run["counts"])
    background_tasks.add_task(_run_tracked_scan, run_id, request, scope)
    return run


@router.get("/scan/runs/{run_id}")
async def historical_relationship_scan_status(run_id: str) -> dict[str, Any]:
    run = get_scan_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Relationship scan run not found")
    return run


@router.post("/check-single")
async def check_single_document(request: Request, body: CheckSingleRequest) -> dict[str, Any]:
    """Run duplicate detection for a single document (as if it were just ingested)."""
    result = on_document_ingested(body.document_id)
    await _project_automatic_relationships(result, request)
    return result


@router.post("/document-ingested")
async def document_ingested(request: Request, body: CheckSingleRequest) -> dict[str, Any]:
    """Process a Paperless post-consumption event after document metadata is available."""
    result = on_document_ingested(body.document_id)
    await _project_automatic_relationships(result, request)
    return result


async def _project_automatic_relationships(
    result: dict[str, Any],
    request: Request,
) -> dict[str, Any] | None:
    relationship_ids = result.get("relationship_ids", [])
    if not relationship_ids:
        return None
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
        return None
    client = None
    try:
        client = make_paperless_client(request, timeout=30.0)
        projection = await project_relationships_to_paperless(client, document_ids)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        projection = {
            "synced": False,
            "status": "failed",
            "failed_documents": sorted(document_ids),
            "error_code": detail.get("code", "paperless_not_configured"),
            "error": detail.get("message", "Paperless connection settings are incomplete."),
        }
    except Exception:
        logger.exception("Failed to project automatic relationships to Paperless")
        projection = {
            "synced": False,
            "status": "failed",
            "failed_documents": sorted(document_ids),
            "error_code": "paperless_document_update_failed",
            "error": "Paperless relationship fields could not be updated.",
        }
    finally:
        if client is not None:
            await client.aclose()
    failed_documents = set(projection.get("failed_documents", []))
    for relationship in relationships:
        relationship_documents = {
            relationship["source_document_id"],
            relationship["target_document_id"],
        }
        synced = bool(projection["synced"]) or not (relationship_documents & failed_documents)
        set_projection_result(
            relationship["id"],
            synced=synced,
            error=None if synced else projection["error"],
            error_code=None if synced else projection.get("error_code"),
        )
    result["projection"] = projection
    logger.info(
        "Relationship projection completed: relationships=%d documents=%d synced=%s",
        len(relationships),
        len(document_ids),
        bool(projection["synced"]),
    )
    return projection


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

    public_doc_a_meta = {
        key: value
        for key, value in (doc_a_meta or {}).items()
        if key not in {"patient_name", "account_identifier", "account_hint", "doc_type"}
    }
    public_doc_b_meta = {
        key: value
        for key, value in (doc_b_meta or {}).items()
        if key not in {"patient_name", "account_identifier", "account_hint", "doc_type"}
    }

    return {
        **pair,
        "doc_a_metadata": public_doc_a_meta,
        "doc_b_metadata": public_doc_b_meta,
        "doc_a_summary": build_document_summary(doc_a_meta or {"document_id": pair["doc_a_id"]}),
        "doc_b_summary": build_document_summary(doc_b_meta or {"document_id": pair["doc_b_id"]}),
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

    if (
        body.resolution in ("true_duplicate", "superseded", "related")
        and body.primary_doc_id is None
    ):
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
                pair["doc_b_id"] if body.primary_doc_id == pair["doc_a_id"] else pair["doc_a_id"]
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
            except Exception:
                logger.exception("Failed to project related-document relationship to Paperless")
                projection = {
                    "synced": False,
                    "documents": [body.primary_doc_id, target_document_id],
                    "error": "Failed to sync relationship projection to Paperless",
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
