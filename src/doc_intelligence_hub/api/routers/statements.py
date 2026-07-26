from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from starlette.responses import StreamingResponse

from doc_intelligence_hub.api.routers import (
    get_statement_config_path,
    load_statement_config_from_request,
    make_paperless_client,
    raise_api_error,
)
from doc_intelligence_hub.modules.statements.api import (
    _discovery_event_generator,
    _recommendations_event_generator,
)
from doc_intelligence_hub.modules.statements.database import Database
from doc_intelligence_hub.modules.statements.models import (
    MergeSeriesRequest,
    ReassignDocumentRequest,
    RenameSeriesRequest,
    SplitSeriesRequest,
)
from doc_intelligence_hub.modules.statements.service import run_discovery, run_recommendations

router = APIRouter(tags=["statement-tracker"])


@router.get("/providers")
async def list_discovered_providers(request: Request) -> dict[str, Any]:
    """Return all providers from the latest discovery run."""
    config = load_statement_config_from_request(request)
    db = Database(config.runtime.database_path)
    try:
        discovery = db.load_latest_discovery()
        if discovery is None:
            return {"providers": [], "analyzed_documents": 0, "run_at": None}

        # Also grab the run timestamp
        runs = db.list_discovery_runs(limit=1)
        run_at = runs[0]["run_at"] if runs else None

        providers = [
            {
                "provider_key": p.provider_key,
                "provider_name": p.provider_name,
                "correspondent_id": p.correspondent_id,
                "document_count": p.document_count,
                "normalized_title": p.normalized_title,
                "title_consistency": p.title_consistency,
                "frequency": p.pattern.frequency,
                "pattern_type": p.pattern.pattern_type,
                "confidence": p.pattern.confidence,
                "anchor_day": p.pattern.anchor_day,
                "variance_days": p.pattern.variance_days,
                "grace_period_days": p.pattern.grace_period_days,
                "sample_document_ids": p.sample_document_ids,
                "first_seen": p.first_seen.isoformat(),
                "last_seen": p.last_seen.isoformat(),
            }
            for p in discovery.providers
        ]

        return {
            "providers": providers,
            "analyzed_documents": discovery.analyzed_documents,
            "run_at": run_at,
        }
    finally:
        db.close()


@router.get("/health")
async def statement_health(request: Request) -> dict[str, Any]:
    config = load_statement_config_from_request(request)
    return {
        "status": "ok",
        "module": "statement-tracker",
        "config_path": get_statement_config_path(request),
        "source_mode": config.source.mode,
        "paperless_url": config.source.paperless_url,
    }


@router.post("/discovery/run")
async def discovery_run(request: Request) -> dict[str, Any]:
    result = await run_discovery(get_statement_config_path(request))
    return result.model_dump(mode="json")


@router.post("/recommendations/run")
async def recommendations_run(request: Request, as_of: date = Query(...)) -> dict[str, Any]:
    result = await run_recommendations(get_statement_config_path(request), as_of)
    return result.model_dump(mode="json")


@router.get("/discovery/stream")
async def discovery_stream(request: Request) -> StreamingResponse:
    return StreamingResponse(
        _discovery_event_generator(get_statement_config_path(request)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/recommendations/stream")
async def recommendations_stream(request: Request, as_of: date = Query(...)) -> StreamingResponse:
    return StreamingResponse(
        _recommendations_event_generator(get_statement_config_path(request), as_of),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/providers/overrides")
async def get_provider_overrides(request: Request) -> dict[str, Any]:
    config = load_statement_config_from_request(request)
    db = Database(config.runtime.database_path)
    try:
        return db.get_provider_overrides()
    finally:
        db.close()


@router.post("/providers/{provider_key}/override")
async def set_provider_override(request: Request, provider_key: str, body: dict[str, Any]) -> dict[str, Any]:
    config = load_statement_config_from_request(request)
    db = Database(config.runtime.database_path)
    try:
        db.set_provider_override(
            provider_key=provider_key,
            status=body.get("status", "confirmed"),
            display_name=body.get("display_name"),
            frequency_override=body.get("frequency_override"),
            anchor_day_override=body.get("anchor_day_override"),
            notes=body.get("notes"),
        )
        return {"status": "ok", "provider_key": provider_key}
    finally:
        db.close()


@router.delete("/providers/{provider_key}/override")
async def delete_provider_override(request: Request, provider_key: str) -> dict[str, Any]:
    config = load_statement_config_from_request(request)
    db = Database(config.runtime.database_path)
    try:
        db.delete_provider_override(provider_key)
        return {"status": "ok", "provider_key": provider_key}
    finally:
        db.close()


@router.get("/config/paperless-url")
async def get_paperless_url(request: Request) -> dict[str, Any]:
    config = load_statement_config_from_request(request)
    return {"paperless_url": config.source.paperless_url}


@router.get("/documents/{doc_id}/thumb")
async def document_thumbnail(request: Request, doc_id: int) -> Response:
    client = make_paperless_client(request, timeout=20.0)
    content, media_type = await client.get_document_thumbnail(doc_id)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/documents/{doc_id}/preview")
async def document_preview(request: Request, doc_id: int) -> Response:
    client = make_paperless_client(request, timeout=30.0)
    content, media_type = await client.get_document_preview(doc_id)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ---------------------------------------------------------------------------
# Statement series endpoints
# ---------------------------------------------------------------------------


def _get_db(request: Request) -> Database:
    config = load_statement_config_from_request(request)
    return Database(config.runtime.database_path)


def _build_timeline(documents: list[dict]) -> list[dict]:
    """Build timeline entries from documents, calculating gap_before_days."""
    from datetime import date as date_type

    timeline = []
    prev_date: date_type | None = None
    for doc in sorted(documents, key=lambda d: d.get("statement_date") or ""):
        gap_days = None
        stmt_date_str = doc.get("statement_date")
        if stmt_date_str and prev_date:
            try:
                current = date_type.fromisoformat(stmt_date_str)
                gap_days = (current - prev_date).days
            except (ValueError, TypeError):
                pass
        if stmt_date_str:
            try:
                prev_date = date_type.fromisoformat(stmt_date_str)
            except (ValueError, TypeError):
                pass
        timeline.append({
            "document_id": doc["document_id"],
            "title": doc.get("title"),
            "statement_date": stmt_date_str,
            "period_label": doc.get("period_label"),
            "account_hint": doc.get("account_hint"),
            "gap_before_days": gap_days,
        })
    return timeline


def _record_correction_event(
    event_type: str,
    target_type: str,
    target_id: str,
    payload: dict | None = None,
) -> None:
    """Record a correction event in the triage database."""
    try:
        from doc_intelligence_hub.modules.triage.database import CorrectionEvent, get_session as get_triage_session
        import json

        session = get_triage_session()
        try:
            event = CorrectionEvent(
                event_type=event_type,
                target_type=target_type,
                target_id=target_id,
                payload_json=json.dumps(payload or {}),
            )
            session.add(event)
            session.commit()
        finally:
            session.close()
    except Exception:
        pass  # Don't fail the main operation if triage DB is unavailable


def _resolve_triage_item_for_series(series_id: str, action: str, payload: dict | None = None) -> None:
    """Resolve any pending triage queue items targeting this series."""
    try:
        from doc_intelligence_hub.modules.triage.database import get_session as get_triage_session, TriageQueueItem
        from datetime import datetime, UTC
        import json

        session = get_triage_session()
        try:
            items = (
                session.query(TriageQueueItem)
                .filter(
                    TriageQueueItem.target_id == series_id,
                    TriageQueueItem.status == "pending",
                )
                .all()
            )
            for item in items:
                item.status = "resolved"
                item.resolved_at = datetime.now(UTC)
                item.resolved_action = action
            session.commit()
        finally:
            session.close()
    except Exception:
        pass


@router.get("/series")
async def list_series(
    request: Request,
    correspondent: str | None = Query(None),
    flagged: bool = Query(False),
) -> dict[str, Any]:
    """List statement series with optional filters."""
    db = _get_db(request)
    try:
        series = db.list_series(correspondent=correspondent, flagged=flagged)
        return {"series": series, "count": len(series)}
    finally:
        db.close()


@router.get("/series/{series_id}")
async def get_series_detail(request: Request, series_id: str) -> dict[str, Any]:
    """Get full detail view for a statement series."""
    db = _get_db(request)
    try:
        series = db.get_series(series_id)
        if not series:
            raise_api_error(404, "series_not_found", f"Series '{series_id}' not found")

        documents = db.get_series_documents(series_id)
        timeline = _build_timeline(documents)
        similar = db.get_similar_series(series_id)

        # Derive anomaly indicators from the data
        anomaly_indicators: list[str] = []
        account_hints = {d.get("account_hint") for d in documents if d.get("account_hint")}
        if len(account_hints) > 1:
            anomaly_indicators.append(f"Multiple account numbers detected: {', '.join(sorted(account_hints))}")
        for entry in timeline:
            if entry.get("gap_before_days") and entry["gap_before_days"] > 60:
                anomaly_indicators.append(f"Large gap of {entry['gap_before_days']} days before {entry.get('period_label', entry.get('statement_date', 'unknown'))}")

        return {
            "series": series,
            "documents": documents,
            "timeline": timeline,
            "similar_series": similar,
            "anomaly_indicators": anomaly_indicators,
        }
    finally:
        db.close()


@router.get("/series/{series_id}/timeline")
async def get_series_timeline(request: Request, series_id: str) -> dict[str, Any]:
    """Get timeline data for a series with gap indicators."""
    db = _get_db(request)
    try:
        series = db.get_series(series_id)
        if not series:
            raise_api_error(404, "series_not_found", f"Series '{series_id}' not found")

        documents = db.get_series_documents(series_id)
        timeline = _build_timeline(documents)
        return {"series_id": series_id, "timeline": timeline}
    finally:
        db.close()


@router.post("/series/{series_id}/split")
async def split_series(request: Request, series_id: str, body: SplitSeriesRequest) -> dict[str, Any]:
    """Split documents from one series into a new series."""
    db = _get_db(request)
    try:
        series = db.get_series(series_id)
        if not series:
            raise_api_error(404, "series_not_found", f"Series '{series_id}' not found")

        if not body.document_ids:
            raise_api_error(400, "no_documents", "At least one document_id is required for split")

        # Get the documents being moved
        all_docs = db.get_series_documents(series_id)
        moving_docs = [d for d in all_docs if d["document_id"] in body.document_ids]

        if not moving_docs:
            raise_api_error(400, "documents_not_found", "None of the specified documents belong to this series")

        # Create new series
        new_id = uuid.uuid4().hex[:12]
        db.create_series(
            series_id=new_id,
            name=body.new_series_name,
            correspondent_name=series["correspondent_name"],
            correspondent_id=series.get("correspondent_id"),
            frequency=series.get("frequency", "monthly"),
            account_identifier=body.account_identifier,
        )

        # Move documents to new series
        db.remove_documents_from_series(series_id, body.document_ids)
        db.add_documents_to_series(new_id, moving_docs)

        # Record override
        override_id = uuid.uuid4().hex[:12]
        db.save_series_override(override_id, series_id, "split_from", {
            "new_series_id": new_id,
            "new_series_name": body.new_series_name,
            "document_ids": body.document_ids,
            "account_identifier": body.account_identifier,
        })

        # Record correction event and resolve triage item
        _record_correction_event("series_split", "statement_series", series_id, {
            "new_series_id": new_id,
            "document_ids": body.document_ids,
        })
        _resolve_triage_item_for_series(series_id, "split")

        return {
            "status": "ok",
            "original_series": db.get_series(series_id),
            "new_series": db.get_series(new_id),
        }
    finally:
        db.close()


@router.post("/series/merge")
async def merge_series(request: Request, body: MergeSeriesRequest) -> dict[str, Any]:
    """Merge source series into target series."""
    db = _get_db(request)
    try:
        source = db.get_series(body.source_series_id)
        target = db.get_series(body.target_series_id)

        if not source:
            raise_api_error(404, "series_not_found", f"Source series '{body.source_series_id}' not found")
        if not target:
            raise_api_error(404, "series_not_found", f"Target series '{body.target_series_id}' not found")

        # Move all source documents to target
        source_docs = db.get_series_documents(body.source_series_id)
        if source_docs:
            db.add_documents_to_series(body.target_series_id, source_docs)

        # Record override on target
        override_id = uuid.uuid4().hex[:12]
        db.save_series_override(override_id, body.target_series_id, "merge_into", {
            "merged_series_id": body.source_series_id,
            "merged_series_name": source["name"],
            "document_count": len(source_docs),
        })

        # Delete source series
        db.delete_series(body.source_series_id)

        # Mark target as manually curated
        db.update_series(body.target_series_id, manually_curated=True)

        # Record correction event and resolve triage items for both series
        _record_correction_event("series_merge", "statement_series", body.target_series_id, {
            "source_series_id": body.source_series_id,
            "source_series_name": source["name"],
        })
        _resolve_triage_item_for_series(body.source_series_id, "merge")
        _resolve_triage_item_for_series(body.target_series_id, "merge")

        return {
            "status": "ok",
            "merged_series": db.get_series(body.target_series_id),
            "documents_moved": len(source_docs),
        }
    finally:
        db.close()


@router.post("/series/{series_id}/reassign")
async def reassign_document(request: Request, series_id: str, body: ReassignDocumentRequest) -> dict[str, Any]:
    """Move a single document from one series to another."""
    db = _get_db(request)
    try:
        source = db.get_series(series_id)
        target = db.get_series(body.target_series_id)

        if not source:
            raise_api_error(404, "series_not_found", f"Source series '{series_id}' not found")
        if not target:
            raise_api_error(404, "series_not_found", f"Target series '{body.target_series_id}' not found")

        # Get the document being moved
        all_docs = db.get_series_documents(series_id)
        doc = next((d for d in all_docs if d["document_id"] == body.document_id), None)
        if not doc:
            raise_api_error(400, "document_not_found", f"Document '{body.document_id}' not in series '{series_id}'")

        # Move the document
        db.remove_documents_from_series(series_id, [body.document_id])
        db.add_documents_to_series(body.target_series_id, [doc])

        # Record override
        override_id = uuid.uuid4().hex[:12]
        db.save_series_override(override_id, series_id, "remove_doc", {
            "document_id": body.document_id,
            "target_series_id": body.target_series_id,
        })

        _record_correction_event("series_reassign", "statement_series", series_id, {
            "document_id": body.document_id,
            "target_series_id": body.target_series_id,
        })

        return {
            "status": "ok",
            "source_series": db.get_series(series_id),
            "target_series": db.get_series(body.target_series_id),
        }
    finally:
        db.close()


@router.post("/series/{series_id}/rename")
async def rename_series(request: Request, series_id: str, body: RenameSeriesRequest) -> dict[str, Any]:
    """Rename a series and/or set its account identifier."""
    db = _get_db(request)
    try:
        series = db.get_series(series_id)
        if not series:
            raise_api_error(404, "series_not_found", f"Series '{series_id}' not found")

        old_name = series["name"]
        updated = db.update_series(
            series_id,
            name=body.name,
            account_identifier=body.account_identifier,
            manually_curated=True,
        )

        # Record override
        override_id = uuid.uuid4().hex[:12]
        db.save_series_override(override_id, series_id, "rename", {
            "old_name": old_name,
            "new_name": body.name,
            "account_identifier": body.account_identifier,
        })

        _record_correction_event("series_rename", "statement_series", series_id, {
            "old_name": old_name,
            "new_name": body.name,
        })
        # Note: rename does NOT resolve the triage item — the grouping issue
        # may still need a split or merge after renaming.

        return {"status": "ok", "series": updated}
    finally:
        db.close()
