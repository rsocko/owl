from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from doc_intelligence_hub.api.routers import get_loaded_statement_config, make_paperless_client
from doc_intelligence_hub.modules.action_queue.analyzer import OllamaAnalyzer
from doc_intelligence_hub.modules.action_queue.config import settings as action_queue_settings
from doc_intelligence_hub.modules.action_queue.database import Action, get_session, init_db
from doc_intelligence_hub.modules.action_queue.pipeline import get_pipeline_progress, run_pipeline
from doc_intelligence_hub.modules.action_queue.risk_scoring import compute_risk_score, recalculate_risk_scores
from doc_intelligence_hub.modules.statements.config import resolve_api_token

router = APIRouter(prefix="/api/queue", tags=["action-queue"])


class QueueRunRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=500)
    dry_run: bool = True
    force: bool = False
    # Source overrides (for custom/ad-hoc runs)
    tag_override: str | None = Field(default=None, description="Comma-separated tag names to override configured defaults")
    saved_view_id: int | None = Field(default=None, description="Paperless saved view ID to use as document source")
    document_id: int | None = Field(default=None, description="Analyze a specific document by ID")
    created_after: str | None = Field(default=None, description="Filter: document created after (YYYY-MM-DD)")
    created_before: str | None = Field(default=None, description="Filter: document created before (YYYY-MM-DD)")
    added_after: str | None = Field(default=None, description="Filter: added to Paperless after (YYYY-MM-DD)")
    added_before: str | None = Field(default=None, description="Filter: added to Paperless before (YYYY-MM-DD)")
    correspondent: str | None = Field(default=None, description="Filter by correspondent name")
    document_type: str | None = Field(default=None, description="Filter by document type name")


class ActionUpdateRequest(BaseModel):
    status: str = Field(..., pattern=r"^(completed|dismissed|pending)$")
    dry_run: bool = True
    version: int | None = Field(default=None, description="Expected version for optimistic locking (returns 409 on mismatch)")


class BulkActionRequest(BaseModel):
    action: str = Field(
        ..., pattern=r"^(complete|dismiss|reopen)$",
        description="Bulk action: 'complete', 'dismiss', or 'reopen'",
    )
    action_ids: list[int] = Field(
        ..., min_length=1, max_length=200,
        description="List of action IDs to update (max 200)",
    )


def _sync_action_queue_settings(request: Request) -> None:
    hub_settings = request.app.state.hub_settings
    statement_config = get_loaded_statement_config(request)

    if hub_settings.paperless_url:
        action_queue_settings.paperless_url = hub_settings.paperless_url
    elif statement_config and statement_config.source.paperless_url:
        action_queue_settings.paperless_url = statement_config.source.paperless_url

    token = hub_settings.resolved_paperless_token or (
        resolve_api_token(statement_config) if statement_config else None
    )
    if token:
        action_queue_settings.paperless_api_token = token

    action_queue_settings.write_to_paperless = hub_settings.write_to_paperless
    action_queue_settings.ollama_url = hub_settings.ollama_url
    action_queue_settings.ollama_model = hub_settings.ollama_model


def _build_preview_url(document_id: int | None) -> str | None:
    """Build a Paperless document preview URL, or None if unavailable."""
    paperless_base = action_queue_settings.paperless_url.rstrip("/")
    if not paperless_base or not document_id:
        return None
    return f"{paperless_base}/documents/{document_id}/details"


def _serialize_action(a: Action) -> dict[str, Any]:
    """Serialize an Action row to a JSON-safe dict with preview_url."""
    return {
        "id": a.id,
        "document_id": a.document_id,
        "document_title": a.document_title,
        "action_type": a.action_type,
        "title": a.title,
        "summary": a.summary,
        "due_date": a.due_date.isoformat() if a.due_date else None,
        "amount": a.amount,
        "urgency": a.urgency,
        "confidence": a.confidence,
        "risk_score": a.risk_score,
        "status": a.status,
        "correspondent": a.correspondent,
        "ai_reasoning": a.ai_reasoning,
        "version": a.version or 1,
        "preview_url": _build_preview_url(a.document_id),
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "completed_at": a.completed_at.isoformat() if a.completed_at else None,
    }


def _database_counts() -> dict[str, int]:
    init_db()
    db = get_session()
    try:
        pending = db.query(Action).filter_by(status="pending").count()
        completed = db.query(Action).filter_by(status="completed").count()
        dismissed = db.query(Action).filter_by(status="dismissed").count()
        return {
            "pending": pending,
            "completed": completed,
            "dismissed": dismissed,
            "total": pending + completed + dismissed,
        }
    finally:
        db.close()


@router.get("/check")
async def queue_check(request: Request) -> dict[str, Any]:
    _sync_action_queue_settings(request)
    client = make_paperless_client(request, timeout=10.0)
    paperless = await client.health_check()
    analyzer = OllamaAnalyzer()
    ollama_ok = await analyzer.health_check()
    return {
        "status": "ok" if ollama_ok else "degraded",
        "module": "action-queue",
        "read_only": not action_queue_settings.write_to_paperless,
        "paperless": paperless,
        "ollama": {
            "status": "ok" if ollama_ok else "error",
            "base_url": analyzer.base_url,
            "model": analyzer.model,
        },
    }


@router.get("/check/custom-fields")
async def queue_check_custom_fields(request: Request) -> dict[str, Any]:
    """Diagnostic: test Paperless custom_fields endpoint directly."""
    _sync_action_queue_settings(request)
    client = make_paperless_client(request, timeout=15.0)
    return await client.check_custom_fields()


@router.post("/run")
async def queue_run(request: Request, body: QueueRunRequest) -> dict[str, Any]:
    _sync_action_queue_settings(request)
    started_at = datetime.utcnow().isoformat()
    request.app.state.last_queue_status = {
        "status": "running",
        "started_at": started_at,
        "dry_run": body.dry_run,
        "limit": body.limit,
        "read_only": not action_queue_settings.write_to_paperless,
    }

    # Apply persisted source settings when no explicit overrides are provided
    saved_view_id = body.saved_view_id
    if not saved_view_id and not body.tag_override and not body.document_id:
        source_settings = _get_queue_settings(request)
        if source_settings.get("scan_mode") == "saved_view" and source_settings.get("saved_view_id"):
            saved_view_id = source_settings["saved_view_id"]

    result = await run_pipeline(
        limit=body.limit,
        dry_run=body.dry_run,
        force=body.force,
        tag_override=body.tag_override,
        saved_view_id=saved_view_id,
        document_id=body.document_id,
        created_after=body.created_after,
        created_before=body.created_before,
        added_after=body.added_after,
        added_before=body.added_before,
        correspondent=body.correspondent,
        document_type=body.document_type,
    )
    finished_at = datetime.utcnow().isoformat()

    # Emit unified alerts for pending actions (best-effort)
    if not body.dry_run:
        try:
            from doc_intelligence_hub.core.alerts import emit_action_queue_alerts

            init_db()
            db = get_session()
            try:
                pending_actions = db.query(Action).filter_by(status="pending").all()
                action_dicts = [
                    {
                        "id": a.id,
                        "title": a.title,
                        "document_title": a.document_title,
                        "urgency": a.urgency,
                        "status": a.status,
                        "due_date": a.due_date.isoformat() if a.due_date else None,
                        "action_type": a.action_type,
                    }
                    for a in pending_actions
                ]
                emit_action_queue_alerts(action_dicts)
            finally:
                db.close()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).debug("Alert emission failed (best-effort): %s", exc)

    status = {
        "status": "ok",
        "started_at": started_at,
        "finished_at": finished_at,
        "dry_run": body.dry_run,
        "limit": body.limit,
        "read_only": not action_queue_settings.write_to_paperless,
        "result": result,
        "database": _database_counts(),
    }
    request.app.state.last_queue_status = status
    return status


@router.get("/status")
async def queue_status(request: Request) -> dict[str, Any]:
    _sync_action_queue_settings(request)
    base_status = request.app.state.last_queue_status or {"status": "idle"}
    return {
        **base_status,
        "read_only": not action_queue_settings.write_to_paperless,
        "database": _database_counts(),
        "progress": get_pipeline_progress(),
    }


@router.get("/actions")
async def list_actions(
    request: Request,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List action items from the database with optional status filter."""
    _sync_action_queue_settings(request)
    init_db()
    db = get_session()
    try:
        query = db.query(Action)
        if status:
            query = query.filter_by(status=status)
        # Sort pending actions by risk_score (highest risk first), then by created_at
        if status == "pending":
            query = query.order_by(Action.risk_score.desc(), Action.created_at.desc())
        else:
            query = query.order_by(Action.created_at.desc())
        total = query.count()
        actions = query.offset(offset).limit(limit).all()

        return {
            "actions": [_serialize_action(a) for a in actions],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        db.close()


@router.patch("/actions/{action_id}")
async def update_action(
    request: Request, action_id: int, body: ActionUpdateRequest
) -> dict[str, Any]:
    """Update an action's status (complete, dismiss, or re-open).

    Supports optimistic locking: if `version` is provided in the request body,
    the update will only succeed if the action's current version matches.
    Returns 409 Conflict if another request modified the action first.

    Also syncs the status change to Paperless custom fields (best-effort).
    """
    _sync_action_queue_settings(request)
    init_db()
    db = get_session()
    try:
        action = db.query(Action).filter_by(id=action_id).first()
        if not action:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail=f"Action {action_id} not found")

        # Optimistic locking: reject if version doesn't match
        if body.version is not None and action.version != body.version:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=409,
                detail={
                    "error": "version_conflict",
                    "message": f"Action {action_id} was modified by another request",
                    "expected_version": body.version,
                    "current_version": action.version,
                },
            )

        action.status = body.status
        action.version = (action.version or 1) + 1
        if body.status == "completed":
            action.completed_at = datetime.utcnow()
        elif body.status == "pending":
            action.completed_at = None
            # Recalculate risk score when reopening (due dates may have changed)
            action.risk_score = compute_risk_score(
                urgency=action.urgency or "LOW",
                due_date=action.due_date,
                amount=action.amount,
                confidence=action.confidence or 0,
                action_type=action.action_type or "REVIEW",
            )
        db.commit()

        # Sync status to Paperless (best-effort — don't fail the user action)
        if action_queue_settings.write_to_paperless and action.document_id:
            try:
                from doc_intelligence_hub.modules.action_queue.enricher import PaperlessEnricher

                enricher = PaperlessEnricher()
                await enricher.sync_status(action.document_id, body.status)
                action.last_synced_status = body.status
                db.commit()
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "Failed to sync status to Paperless for action %d (doc %d): %s",
                    action_id, action.document_id, exc,
                )

        return _serialize_action(action)
    finally:
        db.close()


# Bulk action mapping: request action → DB status
_BULK_ACTION_STATUS: dict[str, str] = {
    "complete": "completed",
    "dismiss": "dismissed",
    "reopen": "pending",
}


@router.post("/actions/bulk")
async def bulk_action(
    request: Request, body: BulkActionRequest
) -> dict[str, Any]:
    """Apply an action to multiple action queue items at once.

    Also syncs status changes to Paperless custom fields (best-effort).
    """
    from fastapi import HTTPException

    _sync_action_queue_settings(request)
    target_status = _BULK_ACTION_STATUS.get(body.action)
    if not target_status:
        raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")

    init_db()
    db = get_session()
    try:
        actions = db.query(Action).filter(Action.id.in_(body.action_ids)).all()
        if not actions:
            raise HTTPException(status_code=404, detail="No matching actions found")

        affected = 0
        affected_actions: list[Action] = []
        for action in actions:
            if action.status == target_status:
                continue
            action.status = target_status
            action.version = (action.version or 1) + 1
            if target_status == "completed":
                action.completed_at = datetime.utcnow()
            elif target_status == "pending":
                action.completed_at = None
                # Recalculate risk score on reopen
                action.risk_score = compute_risk_score(
                    urgency=action.urgency or "LOW",
                    due_date=action.due_date,
                    amount=action.amount,
                    confidence=action.confidence or 0,
                    action_type=action.action_type or "REVIEW",
                )
            affected += 1
            affected_actions.append(action)

        db.commit()

        # Sync status to Paperless for affected actions (best-effort)
        if action_queue_settings.write_to_paperless and affected_actions:
            import logging
            log = logging.getLogger(__name__)
            try:
                from doc_intelligence_hub.modules.action_queue.enricher import PaperlessEnricher

                enricher = PaperlessEnricher()
                for action in affected_actions:
                    if not action.document_id:
                        continue
                    try:
                        await enricher.sync_status(action.document_id, target_status)
                        action.last_synced_status = target_status
                    except Exception as exc:
                        log.warning(
                            "Bulk sync: failed for action %d (doc %d): %s",
                            action.id, action.document_id, exc,
                        )
                db.commit()
            except Exception as exc:
                log.warning("Bulk sync to Paperless failed: %s", exc)

        return {"affected": affected, "action": body.action}
    finally:
        db.close()


@router.post("/actions/recalculate-risk")
async def recalculate_risk(request: Request) -> dict[str, Any]:
    """Recalculate risk_score for all pending actions.

    Use this to backfill scores for actions created before risk scoring
    was implemented, or to refresh scores when due dates have shifted.
    """
    _sync_action_queue_settings(request)
    init_db()
    db = get_session()
    try:
        pending_actions = db.query(Action).filter_by(status="pending").all()
        changed = recalculate_risk_scores(pending_actions)
        db.commit()
        return {
            "total_pending": len(pending_actions),
            "scores_updated": changed,
        }
    finally:
        db.close()


class BackfillRequest(BaseModel):
    status_filter: str | None = Field(
        default=None,
        pattern=r"^(pending|completed|dismissed)$",
        description="Only backfill actions with this status (default: all unsynced)",
    )
    limit: int | None = Field(default=None, ge=1, le=500, description="Max actions to backfill")
    dry_run: bool = Field(default=True, description="Preview what would be written without modifying Paperless")
    force: bool = Field(default=False, description="Re-sync even if last_synced_status is already set")


@router.post("/actions/backfill")
async def backfill_paperless(request: Request, body: BackfillRequest) -> dict[str, Any]:
    """Re-write action metadata to Paperless custom fields without re-running AI analysis.

    Use this to fix Paperless after a bug in the enrichment step, or to sync
    actions that were created while write_to_paperless was disabled.

    This uses the action data already stored in DocIntel's database — no Ollama
    call is made.
    """
    import asyncio
    import logging

    from doc_intelligence_hub.modules.action_queue.enricher import PaperlessEnricher

    _sync_action_queue_settings(request)

    if not action_queue_settings.write_to_paperless and not body.dry_run:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail="write_to_paperless is disabled in settings. Enable it or use dry_run mode.",
        )

    init_db()
    db = get_session()
    log = logging.getLogger(__name__)

    try:
        query = db.query(Action)

        if body.status_filter:
            query = query.filter_by(status=body.status_filter)

        if not body.force:
            # Only actions that were never successfully synced to Paperless
            query = query.filter(
                (Action.last_synced_status == None) | (Action.last_synced_status == "")  # noqa: E711
            )

        query = query.order_by(Action.created_at.asc())

        if body.limit:
            query = query.limit(body.limit)

        actions_to_sync = query.all()

        if body.dry_run:
            return {
                "dry_run": True,
                "would_sync": len(actions_to_sync),
                "actions": [
                    {
                        "id": a.id,
                        "document_id": a.document_id,
                        "document_title": a.document_title,
                        "action_type": a.action_type,
                        "status": a.status,
                        "last_synced_status": a.last_synced_status,
                    }
                    for a in actions_to_sync
                ],
            }

        # Live run — write to Paperless
        enricher = PaperlessEnricher()
        await enricher.ensure_custom_fields_exist()

        synced = 0
        failed = 0
        errors: list[dict[str, Any]] = []

        for action in actions_to_sync:
            try:
                # Reconstruct enrichment payload from stored action fields
                enrichment_data = {
                    "action_type": action.action_type,
                    "urgency": action.urgency or "LOW",
                    "due_date": action.due_date.isoformat() if action.due_date else None,
                    "amount": action.amount,
                    "summary": action.summary or "",
                    "overall_confidence": action.confidence or 0,
                }

                # Count sibling actions for the same document
                action_count = db.query(Action).filter_by(document_id=action.document_id).count()

                await enricher.enrich_document(
                    action.document_id, enrichment_data, action_count=action_count
                )

                # Also sync the current status (not just "pending")
                if action.status != "pending":
                    await enricher.sync_status(action.document_id, action.status)

                action.last_synced_status = action.status
                synced += 1
                log.info(
                    "Backfill: doc_id=%s action_id=%s synced to Paperless (status=%s)",
                    action.document_id, action.id, action.status,
                )
            except Exception as exc:
                failed += 1
                errors.append({
                    "action_id": action.id,
                    "document_id": action.document_id,
                    "error": str(exc),
                })
                log.warning(
                    "Backfill: doc_id=%s action_id=%s failed: %s",
                    action.document_id, action.id, exc,
                )

            # Brief pause to avoid hammering Paperless
            await asyncio.sleep(0.1)

        db.commit()

        return {
            "dry_run": False,
            "total_candidates": len(actions_to_sync),
            "synced": synced,
            "failed": failed,
            "errors": errors[:20],  # Cap error list to avoid huge responses
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Action Queue Settings (persisted via app.state for this server session)
# ---------------------------------------------------------------------------


class QueueSettingsResponse(BaseModel):
    scan_mode: str = Field(default="tags", description="Default scan source: 'tags' or 'saved_view'")
    monitor_tags: list[str] = Field(default_factory=lambda: ["Inbox", "Todo"])
    saved_view_id: int | None = None
    confidence_threshold: int = 70
    document_limit: int | None = None
    rate_limit_delay: float = 0.25


class QueueSettingsUpdate(BaseModel):
    scan_mode: str | None = Field(default=None, pattern=r"^(tags|saved_view)$")
    monitor_tags: list[str] | None = None
    saved_view_id: int | None = Field(default=None, description="Set to 0 to clear")
    confidence_threshold: int | None = Field(default=None, ge=1, le=100)
    document_limit: int | None = Field(default=None, description="Set to 0 to clear (unlimited)")
    rate_limit_delay: float | None = Field(default=None, ge=0, le=10)


def _get_queue_settings(request: Request) -> dict:
    """Get persisted queue settings from app state, with defaults."""
    stored = getattr(request.app.state, "action_queue_source_settings", None)
    if stored is None:
        # Initialize from the action_queue module-level config
        stored = {
            "scan_mode": "tags",
            "monitor_tags": action_queue_settings.monitor_tags,
            "saved_view_id": None,
            "confidence_threshold": action_queue_settings.confidence_threshold,
            "document_limit": None,
            "rate_limit_delay": action_queue_settings.rate_limit_delay,
        }
        request.app.state.action_queue_source_settings = stored
    return stored


@router.get("/settings")
async def get_queue_settings(request: Request) -> dict[str, Any]:
    """Get current Action Queue source configuration."""
    _sync_action_queue_settings(request)
    return _get_queue_settings(request)


@router.put("/settings")
async def update_queue_settings(request: Request, body: QueueSettingsUpdate) -> dict[str, Any]:
    """Update Action Queue source configuration."""
    _sync_action_queue_settings(request)
    current = _get_queue_settings(request)
    changed = []

    if body.scan_mode is not None:
        current["scan_mode"] = body.scan_mode
        changed.append("scan_mode")
    if body.monitor_tags is not None:
        current["monitor_tags"] = [t.strip() for t in body.monitor_tags if t.strip()]
        changed.append("monitor_tags")
        # Also update the action_queue module settings so next run uses them
        action_queue_settings.tags_to_monitor = ",".join(current["monitor_tags"])
    if body.saved_view_id is not None:
        # saved_view_id=0 means "clear"; any positive value means "set"
        current["saved_view_id"] = body.saved_view_id if body.saved_view_id > 0 else None
        changed.append("saved_view_id")
    if body.confidence_threshold is not None:
        current["confidence_threshold"] = body.confidence_threshold
        changed.append("confidence_threshold")
        action_queue_settings.confidence_threshold = body.confidence_threshold
    if body.document_limit is not None:
        # document_limit=0 means "clear" (unlimited); positive value means "set"
        current["document_limit"] = body.document_limit if body.document_limit > 0 else None
        changed.append("document_limit")
    if body.rate_limit_delay is not None:
        current["rate_limit_delay"] = body.rate_limit_delay
        changed.append("rate_limit_delay")
        action_queue_settings.rate_limit_delay = body.rate_limit_delay

    request.app.state.action_queue_source_settings = current
    return {"status": "ok", "changed": changed, "settings": current}


# ---------------------------------------------------------------------------
# Paperless metadata endpoints (for UI dropdowns)
# ---------------------------------------------------------------------------


@router.get("/metadata/tags")
async def list_paperless_tags(request: Request) -> dict[str, Any]:
    """List all tags from Paperless (for tag picker UI)."""
    _sync_action_queue_settings(request)
    try:
        client = make_paperless_client(request, timeout=15.0)
        tags = await client.list_tags()
        return {
            "tags": [{"id": item["id"], "name": item["name"]} for item in tags],
        }
    except Exception as exc:
        return {"tags": [], "error": str(exc)}


@router.get("/metadata/saved-views")
async def list_paperless_saved_views(request: Request) -> dict[str, Any]:
    """List all saved views from Paperless."""
    _sync_action_queue_settings(request)
    try:
        client = make_paperless_client(request, timeout=15.0)
        views = await client.list_saved_views()
        return {
            "saved_views": [
                {"id": v["id"], "name": v["name"]}
                for v in views
            ],
        }
    except Exception as exc:
        return {"saved_views": [], "error": str(exc)}


@router.get("/metadata/correspondents")
async def list_paperless_correspondents(request: Request) -> dict[str, Any]:
    """List all correspondents from Paperless."""
    _sync_action_queue_settings(request)
    try:
        client = make_paperless_client(request, timeout=15.0)
        correspondents = await client.list_correspondents()
        return {
            "correspondents": [
                {"id": c["id"], "name": c["name"]}
                for c in correspondents
            ],
        }
    except Exception as exc:
        return {"correspondents": [], "error": str(exc)}


@router.get("/metadata/document-types")
async def list_paperless_document_types(request: Request) -> dict[str, Any]:
    """List all document types from Paperless."""
    _sync_action_queue_settings(request)
    try:
        client = make_paperless_client(request, timeout=15.0)
        _, _, doc_types = await client.fetch_all_metadata()
        return {
            "document_types": [
                {"id": type_id, "name": name}
                for type_id, name in sorted(doc_types.items(), key=lambda x: x[1])
            ],
        }
    except Exception as exc:
        return {"document_types": [], "error": str(exc)}
