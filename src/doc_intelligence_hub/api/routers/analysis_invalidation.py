"""Analysis invalidation / staleness API router (issue #114).

Manual/programmatic entry points for the analysis-invalidation mechanism,
incremental Paperless reconciliation, and explicitly bounded backfill.

No endpoint here ever contains OCR text, document bodies, or raw metadata
values — only document/version identifiers, checksums, hashes, and reason
codes (see ``analysis_invalidation.database`` module docstring).

Endpoints:
    POST /api/analysis-invalidation/simulate-version-change  — simulate a version change
    POST /api/analysis-invalidation/invalidate                — bounded manual invalidation
    GET  /api/analysis-invalidation/documents/{document_id}    — per-module freshness status
    GET  /api/analysis-invalidation/events                     — recent invalidation events (redacted)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, PositiveInt, model_validator

from doc_intelligence_hub.api.routers import make_paperless_client
from doc_intelligence_hub.api.routers.action_queue import _get_queue_settings
from doc_intelligence_hub.modules.action_queue.database import Action
from doc_intelligence_hub.modules.action_queue.database import get_session as get_action_session
from doc_intelligence_hub.modules.analysis_invalidation.config import settings
from doc_intelligence_hub.modules.analysis_invalidation.database import init_db
from doc_intelligence_hub.modules.analysis_invalidation.discovery import (
    IncrementalDiscoveryService,
)
from doc_intelligence_hub.modules.analysis_invalidation.models import InvalidationReason
from doc_intelligence_hub.modules.analysis_invalidation.scopes import (
    resolve_low_confidence_failed_document_ids,
)
from doc_intelligence_hub.modules.analysis_invalidation.service import AnalysisFreshnessService

router = APIRouter(prefix="/api/analysis-invalidation", tags=["analysis-invalidation"])


def _service() -> AnalysisFreshnessService:
    init_db()
    return AnalysisFreshnessService()


class SimulateVersionChangeRequest(BaseModel):
    """Simulate an accepted OCR version change for one document.

    ``metadata_fields`` should only contain fields a downstream module might
    depend on (e.g. title, correspondent, document_type, tags) — values are
    hashed, never stored raw.
    """

    document_id: int
    checksum: str = Field(min_length=1)
    metadata_fields: dict[str, Any] | None = None


class InvalidateRequest(BaseModel):
    """Bounded manual invalidation request.

    Exactly one of ``all``, ``scope``, or ``document_ids`` must be set.
    """

    all: bool = False
    scope: str | None = Field(default=None, pattern="^(low_confidence_failed)$")
    document_ids: list[int] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1)


class ReconcileRequest(BaseModel):
    cursor_name: str = Field(default="paperless-analysis", min_length=1, max_length=100)
    page_size: int | None = Field(default=None, ge=1, le=500)
    overlap_seconds: int | None = Field(default=None, ge=0, le=86400)
    dry_run: bool = False


class BackfillRequest(BaseModel):
    document_ids: list[PositiveInt] = Field(default_factory=list)
    added_after: str | None = None
    added_before: str | None = None
    id_after: int | None = Field(default=None, ge=1)
    id_before: int | None = Field(default=None, ge=1)
    saved_view_id: int | None = Field(default=None, ge=1)
    limit: int | None = Field(default=None, ge=1, le=settings.max_backfill_batch)
    dry_run: bool = False

    @model_validator(mode="after")
    def validate_scope(self) -> BackfillRequest:
        selectors = [
            bool(self.document_ids),
            bool(self.added_after or self.added_before),
            self.id_after is not None or self.id_before is not None,
            self.saved_view_id is not None,
        ]
        if sum(selectors) != 1:
            raise ValueError(
                "Select exactly one scope: document_ids, added date range, ID range, "
                "or saved_view_id"
            )
        if len(self.document_ids) > settings.max_backfill_batch:
            raise ValueError(
                f"document_ids exceeds the configured limit of {settings.max_backfill_batch}"
            )
        if self.id_after is not None and self.id_before is not None:
            if self.id_after > self.id_before:
                raise ValueError("id_after cannot be greater than id_before")
        parsed_after = date.fromisoformat(self.added_after) if self.added_after else None
        parsed_before = date.fromisoformat(self.added_before) if self.added_before else None
        if parsed_after and parsed_before and parsed_after > parsed_before:
            raise ValueError("added_after cannot be later than added_before")
        return self


def _document_has_existing_action(document_id: int) -> bool:
    db = get_action_session()
    try:
        return (
            db.query(Action.id)
            .filter(
                Action.document_id == document_id,
                Action.superseded_by_action_id.is_(None),
            )
            .first()
            is not None
        )
    finally:
        db.close()


async def _matches_action_queue_scope(
    request: Request,
    client: Any,
    document: dict[str, Any],
) -> bool:
    document_id = int(document["id"])
    if _document_has_existing_action(document_id):
        return True

    queue_settings = _get_queue_settings(request)
    lookback_days = int(queue_settings.get("lookback_days") or 548)
    added_at = document.get("added")
    if isinstance(added_at, str) and added_at:
        try:
            parsed_added_at = datetime.fromisoformat(added_at.replace("Z", "+00:00"))
            if parsed_added_at.tzinfo is None:
                parsed_added_at = parsed_added_at.replace(tzinfo=UTC)
            if parsed_added_at < (datetime.now(UTC) - timedelta(days=lookback_days)):
                return False
        except ValueError:
            pass

    if queue_settings.get("scan_mode") == "saved_view":
        saved_view_id = queue_settings.get("saved_view_id")
        if saved_view_id is None:
            return False
        matches = await client.list_documents(
            saved_view=saved_view_id,
            id_after=document_id,
            id_before=document_id,
            limit=1,
        )
        return any(int(item["id"]) == document_id for item in matches)

    tag_ids = document.get("tags") or []
    tag_names = await client.resolve_tag_names(
        [tag_id for tag_id in tag_ids if isinstance(tag_id, int)]
    )
    monitored = {
        str(tag).strip().casefold()
        for tag in queue_settings.get("monitor_tags") or []
        if str(tag).strip()
    }
    return bool(
        monitored
        & {
            str(tag_names.get(tag_id) or "").strip().casefold()
            for tag_id in tag_ids
            if isinstance(tag_id, int)
        }
    )


async def _analyze_document(
    request: Request,
    client: Any,
    document_id: int,
) -> dict[str, Any]:
    from doc_intelligence_hub.modules.analysis.models import TriggerType
    from doc_intelligence_hub.modules.analysis.rule_executor import execute_trigger
    from doc_intelligence_hub.modules.analysis.rule_registry import get_rules_for_trigger

    document = await client.get_document(document_id)
    document_type_id = document.get("document_type")
    document_types = await client.list_document_types()
    document_type = next(
        (
            str(item.get("name") or "").strip().casefold()
            for item in document_types
            if item.get("id") == document_type_id
        ),
        None,
    )
    rules = get_rules_for_trigger(
        TriggerType.DOCUMENT_ADDED,
        document_type=document_type,
    )
    action_scope_matches = await _matches_action_queue_scope(request, client, document)
    rule_ids = [
        rule.id for rule in rules if rule.id != "action-queue-trigger" or action_scope_matches
    ]

    result = await execute_trigger(
        TriggerType.DOCUMENT_ADDED,
        document_id=document_id,
        document_type=document_type,
        rule_ids=rule_ids,
        dry_run=False,
    )
    failed_results = [item for item in result.get("results", []) if item.get("success") is False]
    if result.get("errors") or failed_results:
        raise RuntimeError("targeted analysis failed")
    return result


@router.post("/simulate-version-change", status_code=201)
async def simulate_version_change(body: SimulateVersionChangeRequest) -> dict[str, Any]:
    """Simulate "this document's accepted OCR version changed" (issue #18 stand-in)."""
    service = _service()
    return service.simulate_version_change(
        document_id=body.document_id,
        new_checksum=body.checksum,
        metadata_fields=body.metadata_fields,
        triggered_by="simulated:api",
    )


@router.post("/invalidate", status_code=202)
async def invalidate(body: InvalidateRequest) -> dict[str, Any]:
    """Force invalidation for all known documents, a named scope, or specific IDs.

    Never bypasses the configured batch limit — ``limit`` can only tighten
    it, not loosen it.
    """
    chosen = [body.all, bool(body.scope), bool(body.document_ids)]
    if sum(bool(c) for c in chosen) != 1:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_scope",
                "message": "Specify exactly one of all=true, scope, or document_ids.",
            },
        )

    effective_limit = (
        min(body.limit, settings.max_manual_invalidation_batch)
        if body.limit
        else settings.max_manual_invalidation_batch
    )

    service = _service()
    if body.all:
        document_ids = service.list_known_document_ids(limit=effective_limit)
        reason = InvalidationReason.MANUAL_ALL
    elif body.scope == "low_confidence_failed":
        document_ids = resolve_low_confidence_failed_document_ids(limit=effective_limit)
        reason = InvalidationReason.MANUAL_SCOPE
    else:
        document_ids = body.document_ids[:effective_limit]
        reason = InvalidationReason.MANUAL_DOCUMENT

    if not document_ids:
        return {"invalidated_count": 0, "results": []}

    try:
        return service.manual_invalidate(
            document_ids=document_ids, reason=reason, triggered_by="manual:api"
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "batch_too_large", "message": str(exc)}
        ) from exc


@router.post("/reconcile", status_code=202)
async def reconcile(request: Request, body: ReconcileRequest) -> dict[str, Any]:
    """Scan one bounded added/modified page and analyze only material changes."""
    init_db()
    client = make_paperless_client(request, timeout=60.0)
    try:
        tags, custom_fields = await asyncio.gather(
            client.list_tags(),
            client.list_custom_fields(),
        )
        ignored_tag_ids = {
            int(tag["id"])
            for tag in tags
            if tag.get("id") and str(tag.get("name") or "").strip().casefold() == "inbox"
        }
        governed_custom_field_ids = {
            int(field["id"])
            for field in custom_fields
            if field.get("id")
            and str(field.get("name") or "").strip().casefold()
            in {"account identifier", "document amount", "document due date"}
        }
        return await IncrementalDiscoveryService().reconcile(
            client=client,
            analyze=lambda document_id: _analyze_document(
                request,
                client,
                document_id,
            ),
            cursor_name=body.cursor_name,
            page_size=body.page_size,
            overlap_seconds=body.overlap_seconds,
            governed_custom_field_ids=governed_custom_field_ids,
            ignored_tag_ids=ignored_tag_ids,
            dry_run=body.dry_run,
        )
    finally:
        await client.aclose()


@router.post("/backfill", status_code=202)
async def backfill(request: Request, body: BackfillRequest) -> dict[str, Any]:
    """Run a bounded, explicitly scoped historical analysis backfill."""
    init_db()
    client = make_paperless_client(request, timeout=60.0)
    try:
        tags, custom_fields = await asyncio.gather(
            client.list_tags(),
            client.list_custom_fields(),
        )
        return await IncrementalDiscoveryService().backfill(
            client=client,
            analyze=lambda document_id: _analyze_document(
                request,
                client,
                document_id,
            ),
            document_ids=body.document_ids or None,
            added_after=body.added_after,
            added_before=body.added_before,
            id_after=body.id_after,
            id_before=body.id_before,
            saved_view_id=body.saved_view_id,
            limit=body.limit,
            governed_custom_field_ids={
                int(field["id"])
                for field in custom_fields
                if field.get("id")
                and str(field.get("name") or "").strip().casefold()
                in {"account identifier", "document amount", "document due date"}
            },
            ignored_tag_ids={
                int(tag["id"])
                for tag in tags
                if tag.get("id") and str(tag.get("name") or "").strip().casefold() == "inbox"
            },
            dry_run=body.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_backfill_scope", "message": str(exc)},
        ) from exc
    finally:
        await client.aclose()


@router.get("/documents/{document_id}")
async def get_document_status(document_id: int) -> dict[str, Any]:
    """Per-module freshness/status for one document (redacted, aggregate-only)."""
    return _service().get_document_status(document_id)


@router.get("/events")
async def list_events(
    document_id: int | None = None, limit: int = Query(50, ge=1, le=500)
) -> dict[str, Any]:
    """Recent invalidation events (redacted — ids/reasons/timestamps only)."""
    return {
        "events": _service().list_events(document_id=document_id, limit=limit),
        "redacted": True,
    }
