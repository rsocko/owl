from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError
from sqlalchemy import String as SAString
from starlette.responses import StreamingResponse

from doc_intelligence_hub.api.routers import get_loaded_statement_config, make_paperless_client
from doc_intelligence_hub.modules.action_queue.analyzer import OllamaAnalyzer
from doc_intelligence_hub.modules.action_queue.config import settings as action_queue_settings
from doc_intelligence_hub.modules.action_queue.database import (
    VALID_ACTION_TYPES,
    Action,
    ActionFeedback,
    QueueConfiguration,
    get_session,
    init_db,
)
from doc_intelligence_hub.modules.action_queue.pipeline import get_pipeline_progress, run_pipeline
from doc_intelligence_hub.modules.action_queue.risk_scoring import (
    compute_risk_score,
    recalculate_risk_scores,
)
from doc_intelligence_hub.modules.statements.config import resolve_api_token

router = APIRouter(prefix="/api/queue", tags=["action-queue"])

_INITIAL_QUEUE_SETTINGS = {
    "scan_mode": "tags",
    "monitor_tags": list(action_queue_settings.monitor_tags),
    "saved_view_id": None,
    "confidence_threshold": action_queue_settings.confidence_threshold,
    "document_limit": None,
    "rate_limit_delay": action_queue_settings.rate_limit_delay,
    "remove_source_tag_on_resolve": action_queue_settings.remove_source_tag_on_resolve,
}


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
    status: str | None = Field(
        default=None,
        pattern=r"^(completed|dismissed|pending|acknowledged|snoozed|not_an_action)$",
    )
    dry_run: bool = True
    version: int | None = Field(default=None, description="Expected version for optimistic locking (returns 409 on mismatch)")
    snoozed_until: str | None = Field(default=None, description="ISO timestamp for snooze expiry (required when status=snoozed)")
    action_type: str | None = Field(default=None, description="Corrected action type")
    title: str | None = Field(default=None, description="Editable task title")
    summary: str | None = Field(default=None, description="Editable action summary")
    due_date: date | None = Field(default=None, description="Editable due date")
    amount: float | None = Field(default=None, description="Editable amount")
    urgency: str | None = Field(
        default=None,
        pattern=r"^(CRITICAL|HIGH|MEDIUM|LOW)$",
        description="Editable urgency",
    )
    correspondent: str | None = Field(default=None, description="Editable correspondent")

    @field_validator("action_type")
    @classmethod
    def _normalize_action_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in VALID_ACTION_TYPES:
            raise PydanticCustomError(
                "invalid_action_type",
                "action_type must be one of: {valid_types}",
                {"valid_types": ", ".join(sorted(VALID_ACTION_TYPES))},
            )
        return normalized

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise PydanticCustomError("blank_title", "title cannot be blank")
        return normalized

    @field_validator("summary", "correspondent")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def _require_changes(self) -> "ActionUpdateRequest":
        mutable_fields = {
            "status",
            "snoozed_until",
            "action_type",
            "title",
            "summary",
            "due_date",
            "amount",
            "urgency",
            "correspondent",
        }
        if not (self.model_fields_set & mutable_fields):
            raise PydanticCustomError(
                "missing_editable_fields",
                "At least one editable field must be provided",
            )

        for field_name in ("action_type", "title", "urgency"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise PydanticCustomError(
                    "null_editable_field",
                    "{field_name} cannot be null",
                    {"field_name": field_name},
                )

        return self


class BulkActionRequest(BaseModel):
    action: str = Field(
        ..., pattern=r"^(complete|dismiss|reopen|acknowledge|snooze)$",
        description="Bulk action: 'complete', 'dismiss', 'reopen', 'acknowledge', or 'snooze'",
    )
    action_ids: list[int] = Field(
        ..., min_length=1, max_length=200,
        description="List of action IDs to update (max 200)",
    )
    snoozed_until: str | None = Field(default=None, description="ISO timestamp for snooze expiry (required for 'snooze' action)")


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
    _get_queue_settings(request)


def _build_preview_url(document_id: int | None) -> str | None:
    """Build a Paperless document preview URL, or None if unavailable."""
    paperless_base = action_queue_settings.paperless_url.rstrip("/")
    if not paperless_base or not document_id:
        return None
    return f"{paperless_base}/documents/{document_id}/details"


def _serialize_action(a: Action) -> dict[str, Any]:
    """Serialize an Action row to a JSON-safe dict with preview_url."""
    import json

    # Deserialize recommended_cta from JSON string if stored as such
    cta = a.recommended_cta
    if isinstance(cta, str):
        try:
            cta = json.loads(cta)
        except (json.JSONDecodeError, TypeError):
            pass  # Keep as string if not valid JSON

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
        "severity": _urgency_to_severity(a.urgency),
        "confidence": a.confidence,
        "risk_score": a.risk_score,
        "status": a.status,
        "recommended_cta": cta,
        "correspondent": a.correspondent,
        "document_date": a.document_date.isoformat() if a.document_date else None,
        "document_type": a.document_type,
        "tags": a.tags if isinstance(a.tags, list) else None,
        "ai_reasoning": a.ai_reasoning,
        "version": a.version or 1,
        "preview_url": _build_preview_url(a.document_id),
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "completed_at": a.completed_at.isoformat() if a.completed_at else None,
        "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
        "snoozed_until": a.snoozed_until.isoformat() if a.snoozed_until else None,
    }


def _urgency_to_severity(urgency: str | None) -> str:
    """Map 4-tier urgency to 3-tier severity for consistent display."""
    mapping = {"CRITICAL": "critical", "HIGH": "focus", "MEDIUM": "focus", "LOW": "safe"}
    return mapping.get((urgency or "LOW").upper(), "safe")


def _database_counts() -> dict[str, int]:
    init_db()
    db = get_session()
    try:
        pending = db.query(Action).filter_by(status="pending").count()
        acknowledged = db.query(Action).filter_by(status="acknowledged").count()
        completed = db.query(Action).filter_by(status="completed").count()
        dismissed = db.query(Action).filter_by(status="dismissed").count()
        snoozed = db.query(Action).filter_by(status="snoozed").count()
        not_an_action = db.query(Action).filter_by(status="not_an_action").count()
        return {
            "pending": pending,
            "acknowledged": acknowledged,
            "completed": completed,
            "dismissed": dismissed,
            "snoozed": snoozed,
            "not_an_action": not_an_action,
            "total": pending + acknowledged + completed + dismissed + snoozed + not_an_action,
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
    source_settings = _get_queue_settings(request)
    effective_limit = (
        body.limit if body.limit is not None else source_settings.get("document_limit")
    )
    started_at = datetime.utcnow().isoformat()
    request.app.state.last_queue_status = {
        "status": "running",
        "started_at": started_at,
        "dry_run": body.dry_run,
        "limit": effective_limit,
        "read_only": not action_queue_settings.write_to_paperless,
    }

    # Apply persisted source settings when no explicit overrides are provided
    saved_view_id = body.saved_view_id
    if not saved_view_id and not body.tag_override and not body.document_id:
        if source_settings.get("scan_mode") == "saved_view" and source_settings.get("saved_view_id"):
            saved_view_id = source_settings["saved_view_id"]

    result = await run_pipeline(
        limit=effective_limit,
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
        "limit": effective_limit,
        "read_only": not action_queue_settings.write_to_paperless,
        "result": result,
        "database": _database_counts(),
    }
    request.app.state.last_queue_status = status
    return status


@router.post("/run/stream")
async def queue_run_stream(
    request: Request,
    body: QueueRunRequest,
) -> StreamingResponse:
    """SSE endpoint that runs the pipeline and streams progress events."""
    _sync_action_queue_settings(request)
    source_settings = _get_queue_settings(request)
    effective_limit = (
        body.limit if body.limit is not None else source_settings.get("document_limit")
    )

    # Reject if a pipeline is already running
    current_status = getattr(request.app.state, "last_queue_status", None) or {}
    if current_status.get("status") == "running":
        from fastapi.responses import JSONResponse
        return JSONResponse(  # type: ignore[return-value]
            {"detail": {"message": "A pipeline run is already in progress."}},
            status_code=409,
        )

    # Apply persisted source settings when no explicit overrides are provided
    effective_saved_view_id = body.saved_view_id
    if not effective_saved_view_id and not body.tag_override and not body.document_id:
        if source_settings.get("scan_mode") == "saved_view" and source_settings.get("saved_view_id"):
            effective_saved_view_id = source_settings["saved_view_id"]

    async def event_generator():
        started_at = datetime.utcnow().isoformat()
        request.app.state.last_queue_status = {
            "status": "running",
            "started_at": started_at,
            "dry_run": body.dry_run,
            "limit": effective_limit,
            "read_only": not action_queue_settings.write_to_paperless,
        }

        # Emit an initial "starting" event so the client knows the stream is alive
        yield f"data: {json.dumps({'stage': 'starting', 'message': 'Pipeline starting…', 'current': 0, 'total': 0})}\n\n"

        # Start the pipeline in a background task
        pipeline_task = asyncio.create_task(run_pipeline(
            limit=effective_limit,
            dry_run=body.dry_run,
            force=body.force,
            tag_override=body.tag_override,
            saved_view_id=effective_saved_view_id,
            document_id=body.document_id,
            created_after=body.created_after,
            created_before=body.created_before,
            added_after=body.added_after,
            added_before=body.added_before,
            correspondent=body.correspondent,
            document_type=body.document_type,
        ))

        last_progress = None
        try:
            while not pipeline_task.done():
                await asyncio.sleep(0.5)
                progress = get_pipeline_progress()
                current_step = progress.get("current_step", "")

                # Don't forward the pipeline's internal "complete" step as a stream event
                # — our own explicit "complete" event below is the real terminal signal.
                if current_step == "complete":
                    continue

                # Only emit when progress changes
                if progress != last_progress:
                    last_progress = dict(progress)
                    progress_str = progress.get("progress", "")
                    current_doc = progress.get("current_document", "")

                    # Parse "3/10 documents processed" into current/total
                    current = 0
                    total = 0
                    if progress_str and "/" in str(progress_str):
                        try:
                            parts = str(progress_str).split("/")
                            current = int(parts[0])
                            total = int(parts[1].split()[0])
                        except (ValueError, IndexError):
                            pass

                    event = {
                        "stage": current_step,
                        "message": current_doc or progress_str or current_step,
                        "current": current,
                        "total": total,
                    }
                    yield f"data: {json.dumps(event)}\n\n"

            # Pipeline finished — get the result
            result = await pipeline_task
            finished_at = datetime.utcnow().isoformat()

            # Emit alerts (best-effort)
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
                except Exception:
                    pass

            # Update app state
            final_status = {
                "status": "ok",
                "started_at": started_at,
                "finished_at": finished_at,
                "dry_run": body.dry_run,
                "limit": body.limit,
                "read_only": not action_queue_settings.write_to_paperless,
                "result": result,
                "database": _database_counts(),
            }
            request.app.state.last_queue_status = final_status

            yield f"data: {json.dumps({'stage': 'complete', 'result': result})}\n\n"
        except Exception as exc:
            request.app.state.last_queue_status = {"status": "error", "error": str(exc)}
            yield f"data: {json.dumps({'stage': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    document_type: str | None = None,
    tag: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List action items from the database with optional status/document_type/tag filters."""
    _sync_action_queue_settings(request)
    init_db()
    db = get_session()
    try:
        query = db.query(Action)
        if status:
            query = query.filter_by(status=status)
        if document_type:
            query = query.filter(Action.document_type == document_type)
        if tag:
            # SQLite JSON: check if tag appears in the JSON array
            query = query.filter(Action.tags.isnot(None))
            query = query.filter(Action.tags.cast(SAString).contains(f'"{tag}"'))
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


@router.get("/actions/expired-snoozes")
async def expired_snoozes(request: Request) -> dict[str, Any]:
    """Find snoozed actions whose snooze has expired (ready to resurface)."""
    _sync_action_queue_settings(request)
    init_db()
    db = get_session()
    try:
        now = datetime.utcnow()
        expired = (
            db.query(Action)
            .filter(Action.status == "snoozed", Action.snoozed_until <= now)
            .order_by(Action.snoozed_until.asc())
            .all()
        )
        return {
            "count": len(expired),
            "actions": [_serialize_action(a) for a in expired],
        }
    finally:
        db.close()


@router.patch("/actions/{action_id}")
async def update_action(
    request: Request, action_id: int, body: ActionUpdateRequest
) -> dict[str, Any]:
    """Update an action's editable fields and/or status.

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

        supplied_fields = body.model_fields_set
        effective_status = body.status or action.status
        risk_inputs_changed = False

        if "action_type" in supplied_fields:
            action.action_type = body.action_type
            risk_inputs_changed = True
        if "title" in supplied_fields:
            action.title = body.title
        if "summary" in supplied_fields:
            action.summary = body.summary
        if "due_date" in supplied_fields:
            action.due_date = body.due_date
            risk_inputs_changed = True
        if "amount" in supplied_fields:
            action.amount = body.amount
            risk_inputs_changed = True
        if "urgency" in supplied_fields:
            action.urgency = body.urgency
            risk_inputs_changed = True
        if "correspondent" in supplied_fields:
            action.correspondent = body.correspondent

        if "status" in supplied_fields:
            action.status = body.status
        if effective_status == "completed" and "status" in supplied_fields:
            action.completed_at = datetime.utcnow()
        elif effective_status == "acknowledged" and "status" in supplied_fields:
            action.acknowledged_at = datetime.utcnow()
        elif effective_status == "snoozed":
            if "status" in supplied_fields or "snoozed_until" in supplied_fields:
                if not body.snoozed_until:
                    from fastapi import HTTPException
                    raise HTTPException(
                        status_code=422,
                        detail="snoozed_until is required when status is 'snoozed'",
                    )
                action.snoozed_until = datetime.fromisoformat(body.snoozed_until)
        elif effective_status == "pending" and "status" in supplied_fields:
            action.completed_at = None
            action.acknowledged_at = None
            action.snoozed_until = None
            risk_inputs_changed = True

        if risk_inputs_changed:
            action.risk_score = compute_risk_score(
                urgency=action.urgency or "LOW",
                due_date=action.due_date,
                amount=action.amount,
                confidence=action.confidence or 0,
                action_type=action.action_type or "REVIEW",
            )

        action.version = (action.version or 1) + 1
        db.commit()

        # Sync status to Paperless (best-effort — don't fail the user action)
        if action_queue_settings.write_to_paperless and action.document_id and "status" in supplied_fields:
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
    "acknowledge": "acknowledged",
    "snooze": "snoozed",
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


class RefreshMetadataRequest(BaseModel):
    status_filter: str | None = Field(
        default=None,
        pattern=r"^(pending|acknowledged|completed|dismissed|snoozed|not_an_action)$",
        description="Only refresh actions with this status (default: all)",
    )
    limit: int | None = Field(default=None, ge=1, le=1000, description="Max actions to refresh")
    force: bool = Field(
        default=False,
        description="Re-fetch even if metadata fields are already populated",
    )


@router.post("/actions/refresh-metadata")
async def refresh_metadata_from_paperless(
    request: Request, body: RefreshMetadataRequest
) -> dict[str, Any]:
    """Refresh document_date, document_type, tags, and correspondent from Paperless.

    Fetches current metadata from the Paperless API and updates the local action
    database. No AI/LLM call is made — this only reads Paperless document metadata.
    Use this to backfill metadata columns for actions created before these fields
    were added, or to pick up changes made in Paperless after initial ingestion.
    """
    import logging

    from doc_intelligence_hub.core.paperless import PaperlessClient

    _sync_action_queue_settings(request)

    init_db()
    db = get_session()
    log = logging.getLogger(__name__)

    try:
        # Build query for actions to refresh
        query = db.query(Action)

        if body.status_filter:
            query = query.filter_by(status=body.status_filter)

        if not body.force:
            # Only actions missing at least one metadata field
            query = query.filter(
                (Action.document_date == None)  # noqa: E711
                | (Action.document_type == None)  # noqa: E711
                | (Action.tags == None)  # noqa: E711
            )

        query = query.order_by(Action.created_at.asc())

        if body.limit:
            query = query.limit(body.limit)

        actions_to_refresh = query.all()

        if not actions_to_refresh:
            return {"updated": 0, "failed": 0, "message": "No actions need metadata refresh."}

        # Collect unique document IDs to fetch
        doc_ids = list({a.document_id for a in actions_to_refresh})

        # Build metadata lookup caches from Paperless
        client = PaperlessClient(
            base_url=action_queue_settings.paperless_url,
            token=action_queue_settings.paperless_api_token,
        )
        correspondents, tags_map, doc_types = await client.fetch_all_metadata()

        # Fetch each document's metadata from Paperless (batched with rate limiting)
        doc_metadata: dict[int, dict] = {}
        fetch_errors: list[dict[str, Any]] = []
        for doc_id in doc_ids:
            try:
                doc = await client.get_document(doc_id)
                doc_metadata[doc_id] = doc
            except Exception as exc:
                fetch_errors.append({"document_id": doc_id, "error": str(exc)})
                log.warning("refresh-metadata: failed to fetch doc_id=%s: %s", doc_id, exc)
            await asyncio.sleep(0.05)  # Light rate limiting

        # Update actions with fresh metadata
        updated = 0
        skipped = 0
        for action in actions_to_refresh:
            doc = doc_metadata.get(action.document_id)
            if not doc:
                skipped += 1
                continue

            changed = False

            # Document date (Paperless "created" field)
            new_date = _parse_date_safe(doc.get("created"))
            if new_date and (body.force or action.document_date is None):
                action.document_date = new_date
                changed = True

            # Document type (resolve ID to name)
            doc_type_raw = doc.get("document_type")
            if doc_type_raw is not None:
                if isinstance(doc_type_raw, int):
                    doc_type_name = doc_types.get(doc_type_raw, str(doc_type_raw))
                else:
                    doc_type_name = str(doc_type_raw) if doc_type_raw else None
                if doc_type_name and (body.force or action.document_type is None):
                    action.document_type = doc_type_name
                    changed = True

            # Tags (resolve IDs to names)
            tag_ids = doc.get("tags", [])
            if tag_ids:
                tag_names = [tags_map.get(tid, str(tid)) for tid in tag_ids if isinstance(tid, int)]
                if not tag_names:
                    # tag_names might already be strings
                    tag_names = [str(t) for t in doc.get("tag_names", tag_ids)]
            else:
                tag_names = [str(t) for t in doc.get("tag_names", [])]
            if tag_names and (body.force or action.tags is None):
                action.tags = tag_names
                changed = True

            # Correspondent (resolve ID to name)
            corr_raw = doc.get("correspondent")
            if corr_raw is not None:
                if isinstance(corr_raw, int):
                    corr_name = correspondents.get(corr_raw, str(corr_raw))
                else:
                    corr_name = str(corr_raw) if corr_raw else None
                if corr_name and (body.force or action.correspondent is None):
                    action.correspondent = corr_name
                    changed = True

            if changed:
                action.updated_at = datetime.utcnow()
                updated += 1
            else:
                skipped += 1

        db.commit()

        return {
            "updated": updated,
            "skipped": skipped,
            "failed": len(fetch_errors),
            "total_candidates": len(actions_to_refresh),
            "unique_documents_fetched": len(doc_metadata),
            "errors": fetch_errors[:20],
        }
    finally:
        db.close()


def _parse_date_safe(date_str: str | None):
    """Parse a date string, returning None on failure."""
    if not date_str:
        return None
    try:
        from dateutil.parser import parse
        return parse(date_str).date()
    except (ValueError, TypeError):
        return None


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
            # Retry actions that were never synced or whose status has changed.
            query = query.filter(
                (Action.last_synced_status == None)  # noqa: E711
                | (Action.last_synced_status == "")
                | (Action.last_synced_status != Action.status)
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


# ------------------------------------------------------------------
# Feedback endpoint — false positive / misclassification signals
# ------------------------------------------------------------------


class FeedbackRequest(BaseModel):
    feedback_type: str = Field(
        ..., pattern=r"^(not_an_action|misclassified|wrong_urgency|wrong_amount)$",
        description="Type of feedback signal",
    )
    corrected_action_type: str | None = Field(
        default=None,
        description="What the action type should be (for misclassified feedback)",
    )
    reason: str | None = Field(default=None, description="Optional user explanation")


@router.post("/actions/{action_id}/feedback")
async def submit_feedback(
    request: Request, action_id: int, body: FeedbackRequest
) -> dict[str, Any]:
    """Submit feedback on an action item — trains the classifier over time.

    Feedback types:
    - not_an_action: This document doesn't require any action (false positive)
    - misclassified: The action type is wrong (e.g., classified as PAY but should be FILE)
    - wrong_urgency: Urgency level is incorrect
    - wrong_amount: Extracted amount is wrong

    When feedback_type is 'not_an_action', the action status is also set to 'not_an_action'.
    """
    _sync_action_queue_settings(request)
    init_db()
    db = get_session()
    try:
        action = db.query(Action).filter_by(id=action_id).first()
        if not action:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"Action {action_id} not found")

        # Record the feedback
        feedback = ActionFeedback(
            action_id=action_id,
            feedback_type=body.feedback_type,
            original_action_type=action.action_type,
            corrected_action_type=body.corrected_action_type,
            reason=body.reason,
        )
        db.add(feedback)

        # If "not_an_action", also update the action status
        if body.feedback_type == "not_an_action":
            action.status = "not_an_action"
            action.version = (action.version or 1) + 1

        # If misclassified and a correction is provided, update the action type
        if body.feedback_type == "misclassified" and body.corrected_action_type:
            from doc_intelligence_hub.modules.action_queue.database import VALID_ACTION_TYPES
            if body.corrected_action_type.upper() in VALID_ACTION_TYPES:
                action.action_type = body.corrected_action_type.upper()
                action.version = (action.version or 1) + 1

        db.commit()

        if (
            body.feedback_type == "not_an_action"
            and action_queue_settings.write_to_paperless
            and action.document_id
        ):
            try:
                from doc_intelligence_hub.modules.action_queue.enricher import PaperlessEnricher

                enricher = PaperlessEnricher()
                await enricher.sync_status(action.document_id, "not_an_action")
                action.last_synced_status = "not_an_action"
                db.commit()
            except Exception as exc:
                import logging

                logging.getLogger(__name__).warning(
                    "Failed to sync not-an-action feedback to Paperless for action %d "
                    "(doc %d): %s",
                    action_id,
                    action.document_id,
                    exc,
                )

        return {
            "feedback_id": feedback.id,
            "action_id": action_id,
            "feedback_type": body.feedback_type,
            "action_status": action.status,
            "action_type": action.action_type,
        }
    finally:
        db.close()


@router.get("/actions/{action_id}/feedback")
async def get_feedback(request: Request, action_id: int) -> dict[str, Any]:
    """Get all feedback submitted for an action item."""
    _sync_action_queue_settings(request)
    init_db()
    db = get_session()
    try:
        feedbacks = db.query(ActionFeedback).filter_by(action_id=action_id).order_by(
            ActionFeedback.created_at.desc()
        ).all()
        return {
            "action_id": action_id,
            "feedback": [
                {
                    "id": f.id,
                    "feedback_type": f.feedback_type,
                    "original_action_type": f.original_action_type,
                    "corrected_action_type": f.corrected_action_type,
                    "reason": f.reason,
                    "created_at": f.created_at.isoformat() if f.created_at else None,
                }
                for f in feedbacks
            ],
        }
    finally:
        db.close()


# ------------------------------------------------------------------
# Convenience snooze endpoint
# ------------------------------------------------------------------


class SnoozeRequest(BaseModel):
    until: str = Field(..., description="ISO timestamp when the action should resurface")


@router.post("/actions/{action_id}/snooze")
async def snooze_action(
    request: Request, action_id: int, body: SnoozeRequest
) -> dict[str, Any]:
    """Snooze an action — defer it until a specified time.

    The action moves to 'snoozed' status and will resurface when the snooze expires.
    Use the queue/unsnoozed endpoint to find actions whose snooze has expired.
    """
    _sync_action_queue_settings(request)
    init_db()
    db = get_session()
    try:
        action = db.query(Action).filter_by(id=action_id).first()
        if not action:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"Action {action_id} not found")

        try:
            snooze_dt = datetime.fromisoformat(body.until)
        except (ValueError, TypeError) as err:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=422,
                detail=f"Invalid ISO timestamp: {body.until}",
            ) from err

        action.status = "snoozed"
        action.snoozed_until = snooze_dt
        action.version = (action.version or 1) + 1
        db.commit()

        return _serialize_action(action)
    finally:
        db.close()


@router.post("/actions/{action_id}/acknowledge")
async def acknowledge_action(request: Request, action_id: int) -> dict[str, Any]:
    """Acknowledge an action — mark as seen/owned without claiming completion.

    Use when you've seen the action and intend to handle it, but haven't completed
    the actual task yet. Removes it from the active queue.
    """
    _sync_action_queue_settings(request)
    init_db()
    db = get_session()
    try:
        action = db.query(Action).filter_by(id=action_id).first()
        if not action:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"Action {action_id} not found")

        action.status = "acknowledged"
        action.acknowledged_at = datetime.utcnow()
        action.version = (action.version or 1) + 1
        db.commit()

        return _serialize_action(action)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Action Queue Settings (persisted via app.state for this server session)
# ---------------------------------------------------------------------------


class QueueSettingsResponse(BaseModel):
    scan_mode: str = Field(default="tags", description="Default scan source: 'tags' or 'saved_view'")
    monitor_tags: list[str] = Field(default_factory=lambda: ["Inbox"])
    saved_view_id: int | None = None
    confidence_threshold: int = 70
    document_limit: int | None = None
    rate_limit_delay: float = 0.25
    remove_source_tag_on_resolve: bool = True


class QueueSettingsUpdate(BaseModel):
    scan_mode: str | None = Field(default=None, pattern=r"^(tags|saved_view)$")
    monitor_tags: list[str] | None = None
    saved_view_id: int | None = Field(default=None, description="Set to 0 to clear")
    confidence_threshold: int | None = Field(default=None, ge=1, le=100)
    document_limit: int | None = Field(default=None, description="Set to 0 to clear (unlimited)")
    rate_limit_delay: float | None = Field(default=None, ge=0, le=10)
    remove_source_tag_on_resolve: bool | None = None


def _get_queue_settings(request: Request) -> dict:
    """Load durable queue settings, creating them from environment defaults once."""
    init_db()
    db = get_session()
    try:
        stored = db.get(QueueConfiguration, 1)
        if stored is None:
            stored = QueueConfiguration(id=1, **_INITIAL_QUEUE_SETTINGS)
            db.add(stored)
            db.commit()
            db.refresh(stored)

        values = {
            "scan_mode": stored.scan_mode,
            "monitor_tags": list(stored.monitor_tags or []),
            "saved_view_id": stored.saved_view_id,
            "confidence_threshold": stored.confidence_threshold,
            "document_limit": stored.document_limit,
            "rate_limit_delay": stored.rate_limit_delay,
            "remove_source_tag_on_resolve": stored.remove_source_tag_on_resolve,
        }
    finally:
        db.close()

    action_queue_settings.tags_to_monitor = ",".join(values["monitor_tags"])
    action_queue_settings.confidence_threshold = values["confidence_threshold"]
    action_queue_settings.rate_limit_delay = values["rate_limit_delay"]
    action_queue_settings.remove_source_tag_on_resolve = values[
        "remove_source_tag_on_resolve"
    ]
    return values


def _persist_queue_settings(values: dict[str, Any]) -> None:
    init_db()
    db = get_session()
    try:
        stored = db.get(QueueConfiguration, 1)
        if stored is None:
            stored = QueueConfiguration(id=1)
            db.add(stored)
        for field_name, value in values.items():
            setattr(stored, field_name, value)
        db.commit()
    finally:
        db.close()


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
    if "saved_view_id" in body.model_fields_set:
        # saved_view_id=0 means "clear"; any positive value means "set"
        current["saved_view_id"] = (
            body.saved_view_id
            if body.saved_view_id is not None and body.saved_view_id > 0
            else None
        )
        changed.append("saved_view_id")
    if body.confidence_threshold is not None:
        current["confidence_threshold"] = body.confidence_threshold
        changed.append("confidence_threshold")
        action_queue_settings.confidence_threshold = body.confidence_threshold
    if "document_limit" in body.model_fields_set:
        # document_limit=0 means "clear" (unlimited); positive value means "set"
        current["document_limit"] = (
            body.document_limit
            if body.document_limit is not None and body.document_limit > 0
            else None
        )
        changed.append("document_limit")
    if body.rate_limit_delay is not None:
        current["rate_limit_delay"] = body.rate_limit_delay
        changed.append("rate_limit_delay")
    if body.remove_source_tag_on_resolve is not None:
        current["remove_source_tag_on_resolve"] = body.remove_source_tag_on_resolve
        changed.append("remove_source_tag_on_resolve")

    if current["scan_mode"] == "tags" and not current["monitor_tags"]:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422,
            detail="At least one monitored tag is required when scan mode is 'tags'",
        )
    if current["scan_mode"] == "saved_view" and current["saved_view_id"] is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422,
            detail="A saved view is required when scan mode is 'saved_view'",
        )

    _persist_queue_settings(current)
    action_queue_settings.tags_to_monitor = ",".join(current["monitor_tags"])
    action_queue_settings.confidence_threshold = current["confidence_threshold"]
    action_queue_settings.rate_limit_delay = current["rate_limit_delay"]
    action_queue_settings.remove_source_tag_on_resolve = current[
        "remove_source_tag_on_resolve"
    ]
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
