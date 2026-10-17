"""Unified Paperless intake review API."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func

from doc_intelligence_hub.api.document_summary import build_document_summary
from doc_intelligence_hub.api.routers import make_paperless_client
from doc_intelligence_hub.api.routers.action_queue import (
    _serialize_action_with_siblings,
    _sync_action_queue_settings,
)
from doc_intelligence_hub.modules.action_queue.config import settings as action_queue_settings
from doc_intelligence_hub.modules.action_queue.database import (
    VALID_ACTION_TYPES,
    Action,
    ActionLifecycleEvent,
    ProcessingHistory,
    QueueConfiguration,
    get_session,
    init_db,
)
from doc_intelligence_hub.modules.action_queue.lifecycle import mark_action_ready
from doc_intelligence_hub.modules.action_queue.pipeline import run_pipeline

router = APIRouter(prefix="/api/intake", tags=["intake-review"])

_TAXONOMY_METHODS = {
    "tag": ("list_tags", "create_tag"),
    "correspondent": ("list_correspondents", "create_correspondent"),
    "document_type": ("list_document_types", "create_document_type"),
    "storage_path": ("list_storage_paths", "create_storage_path"),
}


class IntakeMetadataPatch(BaseModel):
    title: str | None = None
    correspondent: int | None = Field(default=None, ge=1)
    document_type: int | None = Field(default=None, ge=1)
    storage_path: int | None = Field(default=None, ge=1)
    created: date | None = None
    tags: list[int] | None = None
    custom_fields: list[dict[str, Any]] | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("title cannot be blank")
        return normalized

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if any(isinstance(item, bool) or item <= 0 for item in value):
            raise ValueError("tags must contain positive integer IDs")
        if len(value) != len(set(value)):
            raise ValueError("tags cannot contain duplicates")
        return value


class IntakeSaveRequest(BaseModel):
    expected_modified: str
    metadata: IntakeMetadataPatch
    disposition: Literal["save", "defer", "file_only", "confirm_and_file"] = "save"
    action_versions: dict[int, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_confirmation(self) -> "IntakeSaveRequest":
        if self.disposition == "confirm_and_file" and not self.action_versions:
            raise ValueError("action_versions are required when confirming actions")
        return self


class TaxonomyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("name cannot be blank")
        return normalized


class UserActionCreateRequest(BaseModel):
    action_type: str
    title: str = Field(min_length=1, max_length=500)
    payee_or_owner: str | None = None
    amount: float | None = None
    due_date: date | None = None
    reference: str | None = None

    @field_validator("action_type")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in VALID_ACTION_TYPES:
            raise ValueError(f"action_type must be one of: {', '.join(sorted(VALID_ACTION_TYPES))}")
        return normalized

    @field_validator("title")
    @classmethod
    def normalize_action_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("title cannot be blank")
        return normalized

    @field_validator("payee_or_owner", "reference")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


def _raise_paperless_error(exc: Exception, operation: str) -> None:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "paperless_access_denied",
                    "message": f"Paperless denied access while {operation}.",
                },
            ) from exc
        if status == 404:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "paperless_resource_not_found",
                    "message": f"The Paperless resource was not found while {operation}.",
                },
            ) from exc
    raise HTTPException(
        status_code=502,
        detail={
            "code": "paperless_request_failed",
            "message": f"Paperless failed while {operation}: {exc}",
        },
    ) from exc


def _catalog_map(values: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {
        int(value["id"]): value
        for value in values
        if isinstance(value, dict)
        and isinstance(value.get("id"), int)
        and not isinstance(value.get("id"), bool)
    }


def _catalog_value(catalog: dict[int, dict[str, Any]], value: Any) -> dict[str, Any] | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    item = catalog.get(value)
    if item is None:
        return {"id": value, "name": f"Unknown value #{value}", "unresolved": True}
    return item


def _suggestion_values(payload: dict[str, Any], *keys: str) -> list[Any]:
    values: list[Any] = []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif value is not None:
            values.append(value)
    return values


def _normalize_suggestions(
    payload: dict[str, Any],
    source: str,
    catalogs: dict[str, dict[int, dict[str, Any]]],
    current: dict[str, Any],
) -> list[dict[str, Any]]:
    field_specs = {
        "correspondent": (
            ("correspondents", "correspondent", "suggested_correspondent"),
            catalogs["correspondents"],
        ),
        "document_type": (
            ("document_types", "document_type", "suggested_document_type"),
            catalogs["document_types"],
        ),
        "storage_path": (
            ("storage_paths", "storage_path", "suggested_storage_path"),
            catalogs["storage_paths"],
        ),
        "tags": (("tags", "suggested_tags"), catalogs["tags"]),
    }
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for field, (keys, catalog) in field_specs.items():
        applied = set(current.get(field) or []) if field == "tags" else {current.get(field)}
        for raw in _suggestion_values(payload, *keys):
            item = _catalog_value(catalog, raw)
            if item is None and isinstance(raw, str):
                name = raw.strip()
                item = {"id": None, "name": name, "create": True} if name else None
            if item is None or item.get("id") in applied:
                continue
            identity = (field, str(item.get("id") or item.get("name", "")).casefold())
            if identity in seen:
                continue
            seen.add(identity)
            normalized.append({"field": field, "source": source, **item})

    for field, keys in {
        "title": ("title", "suggested_title"),
        "created": ("created", "date", "suggested_date"),
    }.items():
        for raw in _suggestion_values(payload, *keys):
            if not isinstance(raw, str) or not raw.strip() or raw == current.get(field):
                continue
            identity = (field, raw.strip().casefold())
            if identity not in seen:
                seen.add(identity)
                normalized.append(
                    {"field": field, "source": source, "value": raw.strip(), "name": raw.strip()}
                )
    return normalized


def _queue_settings() -> QueueConfiguration | None:
    init_db()
    db = get_session()
    try:
        return db.query(QueueConfiguration).filter_by(id=1).first()
    finally:
        db.close()


def _monitor_tags() -> list[str]:
    settings = _queue_settings()
    return list(settings.monitor_tags if settings else action_queue_settings.monitor_tags)


async def _load_taxonomies(client: Any) -> dict[str, list[dict[str, Any]]]:
    tags, correspondents, document_types, storage_paths, custom_fields = await asyncio.gather(
        client.list_tags(),
        client.list_correspondents(),
        client.list_document_types(),
        client.list_storage_paths(),
        client.list_custom_fields(),
    )
    return {
        "tags": tags,
        "correspondents": correspondents,
        "document_types": document_types,
        "storage_paths": storage_paths,
        "custom_fields": custom_fields,
    }


async def _document_detail(request: Request, document_id: int) -> dict[str, Any]:
    client = make_paperless_client(request, timeout=30.0)
    try:
        try:
            document, taxonomies, content = await asyncio.gather(
                client.get_document(document_id),
                _load_taxonomies(client),
                client.get_document_content(document_id),
            )
        except Exception as exc:
            _raise_paperless_error(exc, "loading the intake document")

        catalogs = {
            key: _catalog_map(taxonomies[key])
            for key in ("tags", "correspondents", "document_types", "storage_paths")
        }
        current = {
            "correspondent": document.get("correspondent"),
            "document_type": document.get("document_type"),
            "storage_path": document.get("storage_path"),
            "tags": document.get("tags") or [],
            "title": document.get("title"),
            "created": document.get("created"),
        }

        suggestion_results = await asyncio.gather(
            client.get_document_suggestions(document_id),
            client.get_document_ai_suggestions(document_id),
            return_exceptions=True,
        )
        suggestions: list[dict[str, Any]] = []
        suggestion_sources: dict[str, dict[str, Any]] = {}
        for source, result in zip(
            ("paperless_ml", "paperless_ai"), suggestion_results, strict=True
        ):
            if isinstance(result, Exception):
                status = (
                    result.response.status_code
                    if isinstance(result, httpx.HTTPStatusError)
                    else None
                )
                suggestion_sources[source] = {
                    "available": False,
                    "status": status,
                    "error": str(result),
                }
                continue
            suggestion_sources[source] = {"available": True, "status": 200, "error": None}
            suggestions.extend(_normalize_suggestions(result, source, catalogs, current))

        init_db()
        db = get_session()
        try:
            actions = (
                db.query(Action)
                .filter(
                    Action.document_id == document_id,
                    Action.superseded_by_action_id.is_(None),
                )
                .order_by(Action.action_index.asc(), Action.id.asc())
                .all()
            )
            action_payloads = [_serialize_action_with_siblings(db, action) for action in actions]
            history = (
                db.query(ProcessingHistory)
                .filter_by(document_id=document_id)
                .order_by(ProcessingHistory.processed_at.desc())
                .first()
            )
        finally:
            db.close()

        quality_score = history.text_quality_score if history else None
        quality_band = (
            "green"
            if quality_score is not None and quality_score >= 90
            else "amber"
            if quality_score is not None and quality_score >= 75
            else "red"
            if quality_score is not None
            else "unknown"
        )
        return {
            "document": {
                **document,
                "document_summary": build_document_summary(document),
                "correspondent_value": _catalog_value(
                    catalogs["correspondents"], document.get("correspondent")
                ),
                "document_type_value": _catalog_value(
                    catalogs["document_types"], document.get("document_type")
                ),
                "storage_path_value": _catalog_value(
                    catalogs["storage_paths"], document.get("storage_path")
                ),
                "tag_values": [
                    item
                    for tag_id in document.get("tags") or []
                    if (item := _catalog_value(catalogs["tags"], tag_id)) is not None
                ],
            },
            "taxonomies": taxonomies,
            "suggestions": suggestions,
            "suggestion_sources": suggestion_sources,
            "actions": action_payloads,
            "ocr": {
                "quality_score": quality_score,
                "quality_band": quality_band,
                "poor_quality": quality_score is not None and quality_score < 75,
                "text": content,
            },
            "analysis": {
                "available": history is not None,
                "processed_at": (
                    history.processed_at.isoformat() if history and history.processed_at else None
                ),
                "disposition": history.disposition if history else None,
                "source_modified": document.get("modified"),
            },
            "preview_url": f"/api/statements/documents/{document_id}/preview",
            "download_url": f"/api/documents/{document_id}/download",
            "write_enabled": bool(request.app.state.hub_settings.write_to_paperless),
            "monitor_tags": _monitor_tags(),
        }
    finally:
        await client.aclose()


@router.get("/documents")
async def list_intake_documents(request: Request, limit: int = 100) -> dict[str, Any]:
    client = make_paperless_client(request, timeout=30.0)
    try:
        try:
            documents = await client.list_documents(tags=_monitor_tags(), limit=min(limit, 200))
        except Exception as exc:
            _raise_paperless_error(exc, "loading the intake queue")
        return {
            "documents": [
                {
                    "id": document.get("id"),
                    "title": document.get("title") or "Untitled document",
                    "created": document.get("created"),
                    "modified": document.get("modified"),
                    "correspondent": document.get("correspondent"),
                    "document_type": document.get("document_type"),
                    "tags": document.get("tags") or [],
                    "document_summary": build_document_summary(document),
                }
                for document in documents
            ],
            "total": len(documents),
            "monitor_tags": _monitor_tags(),
        }
    finally:
        await client.aclose()


@router.get("/documents/{document_id}")
async def get_intake_document(request: Request, document_id: int) -> dict[str, Any]:
    return await _document_detail(request, document_id)


@router.post("/taxonomies/{kind}")
async def create_taxonomy(
    request: Request,
    kind: Literal["tag", "correspondent", "document_type", "storage_path"],
    body: TaxonomyCreateRequest,
) -> dict[str, Any]:
    if not request.app.state.hub_settings.write_to_paperless:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "paperless_writes_disabled",
                "message": "Paperless writes are disabled; the value was not created.",
            },
        )
    client = make_paperless_client(request)
    try:
        list_method_name, create_method_name = _TAXONOMY_METHODS[kind]
        try:
            values = await getattr(client, list_method_name)()
        except Exception as exc:
            _raise_paperless_error(exc, f"checking existing {kind} values")
        duplicate = next(
            (
                value
                for value in values
                if str(value.get("name", "")).strip().casefold() == body.name.casefold()
            ),
            None,
        )
        if duplicate:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "taxonomy_duplicate",
                    "message": f"{body.name!r} already exists.",
                    "existing": duplicate,
                },
            )
        try:
            created = await getattr(client, create_method_name)(body.name)
        except Exception as exc:
            _raise_paperless_error(exc, f"creating the {kind}")
        return {"value": created}
    finally:
        await client.aclose()


@router.put("/documents/{document_id}")
async def save_intake_document(
    request: Request, document_id: int, body: IntakeSaveRequest
) -> dict[str, Any]:
    if not request.app.state.hub_settings.write_to_paperless:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "paperless_writes_disabled",
                "message": "Paperless writes are disabled; no changes were saved.",
            },
        )
    client = make_paperless_client(request, timeout=30.0)
    action_db = None
    actions_to_confirm: list[Action] = []
    try:
        try:
            current = await client.get_document(document_id)
        except Exception as exc:
            _raise_paperless_error(exc, "checking the current document revision")
        if current.get("modified") != body.expected_modified:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "source_revision_conflict",
                    "message": "This document changed in Paperless. Reload it before saving.",
                    "expected_modified": body.expected_modified,
                    "current_modified": current.get("modified"),
                },
            )

        if body.disposition == "confirm_and_file":
            init_db()
            action_db = get_session()
            actions_to_confirm = (
                action_db.query(Action)
                .filter(
                    Action.id.in_(body.action_versions),
                    Action.document_id == document_id,
                    Action.superseded_by_action_id.is_(None),
                )
                .all()
            )
            if len(actions_to_confirm) != len(body.action_versions):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "invalid_action_selection",
                        "message": "One or more selected actions do not belong to this document.",
                    },
                )
            for action in actions_to_confirm:
                expected_version = body.action_versions[action.id]
                if action.version != expected_version:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "action_version_conflict",
                            "message": f"Action {action.id} changed before confirmation.",
                            "action_id": action.id,
                            "expected_version": expected_version,
                            "current_version": action.version,
                        },
                    )

        patch: dict[str, Any] = {}
        for field in body.metadata.model_fields_set:
            value = getattr(body.metadata, field)
            patch[field] = value.isoformat() if isinstance(value, date) else value

        if body.disposition in {"file_only", "confirm_and_file"}:
            intake_tag_ids = set(await client.resolve_tag_ids(_monitor_tags()))
            source_tags = patch.get("tags", current.get("tags") or [])
            patch["tags"] = [tag_id for tag_id in source_tags if tag_id not in intake_tag_ids]

        try:
            updated = await client.update_document(document_id, patch) if patch else current
        except Exception as exc:
            _raise_paperless_error(exc, "saving document metadata")

        if body.disposition == "confirm_and_file" and action_db is not None:
            for action in actions_to_confirm:
                mark_action_ready(action)
                action.human_reviewed = True
                action.version = (action.version or 1) + 1
                action_db.add(
                    ActionLifecycleEvent(
                        action_id=action.id,
                        event_type="confirmed",
                        previous_status=action.status,
                        new_status=action.status,
                        action_version=action.version,
                        details={"document_id": document_id, "disposition": body.disposition},
                    )
                )
            action_db.commit()

        return {
            "document": updated,
            "disposition": body.disposition,
            "filed": body.disposition in {"file_only", "confirm_and_file"},
            "confirmed_action_ids": sorted(body.action_versions)
            if body.disposition == "confirm_and_file"
            else [],
        }
    finally:
        if action_db is not None:
            action_db.close()
        await client.aclose()


@router.post("/documents/{document_id}/refresh")
async def refresh_intake_analysis(
    request: Request, document_id: int, expected_modified: str
) -> dict[str, Any]:
    client = make_paperless_client(request, timeout=30.0)
    try:
        try:
            current = await client.get_document(document_id)
        except Exception as exc:
            _raise_paperless_error(exc, "checking the document before analysis")
        if current.get("modified") != expected_modified:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "source_revision_conflict",
                    "message": "The Paperless source changed. Reload before refreshing analysis.",
                    "current_modified": current.get("modified"),
                },
            )
    finally:
        await client.aclose()

    _sync_action_queue_settings(request)
    result = await run_pipeline(document_id=document_id, force=True, dry_run=False)
    return {"result": result, "review": await _document_detail(request, document_id)}


@router.post("/documents/{document_id}/actions")
async def create_user_action(
    request: Request, document_id: int, body: UserActionCreateRequest
) -> dict[str, Any]:
    client = make_paperless_client(request)
    try:
        try:
            document = await client.get_document(document_id)
        except Exception as exc:
            _raise_paperless_error(exc, "loading the action source document")
    finally:
        await client.aclose()

    init_db()
    db = get_session()
    try:
        next_index = (
            db.query(func.max(Action.action_index)).filter_by(document_id=document_id).scalar()
        )
        action = Action(
            document_id=document_id,
            document_title=document.get("title") or f"Document {document_id}",
            action_type=body.action_type,
            title=body.title,
            payee_or_owner=body.payee_or_owner,
            amount=body.amount,
            due_date=body.due_date,
            reference=body.reference,
            correspondent=None,
            document_date=(
                date.fromisoformat(document["created"][:10]) if document.get("created") else None
            ),
            status="pending",
            confidence=100,
            action_ready=True,
            review_state="ready",
            human_reviewed=True,
            action_index=(next_index if next_index is not None else -1) + 1,
            is_primary=next_index is None,
            origin="user",
        )
        db.add(action)
        db.flush()
        db.add(
            ActionLifecycleEvent(
                action_id=action.id,
                event_type="created",
                previous_status=None,
                new_status="pending",
                action_version=action.version or 1,
                details={"document_id": document_id, "origin": "user"},
            )
        )
        db.commit()
        db.refresh(action)
        return _serialize_action_with_siblings(db, action)
    finally:
        db.close()


@router.delete("/actions/{action_id}", status_code=204)
async def delete_user_action(action_id: int) -> None:
    init_db()
    db = get_session()
    try:
        action = db.query(Action).filter_by(id=action_id).first()
        if not action:
            raise HTTPException(status_code=404, detail=f"Action {action_id} not found")
        if action.origin != "user":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "inferred_action_cannot_be_deleted",
                    "message": "Inferred actions must be marked Not an Action to preserve audit history.",
                },
            )
        db.add(
            ActionLifecycleEvent(
                action_id=action.id,
                event_type="deleted",
                previous_status=action.status,
                new_status=None,
                action_version=action.version or 1,
                details={"document_id": action.document_id, "origin": "user"},
            )
        )
        db.delete(action)
        db.commit()
    finally:
        db.close()
