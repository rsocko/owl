"""Unified Paperless intake review API."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func

from doc_intelligence_hub.api.document_summary import build_document_summary
from doc_intelligence_hub.api.routers import make_paperless_client
from doc_intelligence_hub.api.routers.action_queue import (
    _serialize_action_with_siblings,
    _sync_action_queue_settings,
)
from doc_intelligence_hub.core.paperless import (
    MetadataFieldKey,
    MetadataProjectionPolicy,
    get_metadata_field_spec,
)
from doc_intelligence_hub.modules.action_queue.config import settings as action_queue_settings
from doc_intelligence_hub.modules.action_queue.database import (
    VALID_ACTION_TYPES,
    Action,
    ActionLifecycleEvent,
    IntakeSuggestionSnapshot,
    ProcessingHistory,
    QueueConfiguration,
    get_session,
    init_db,
)
from doc_intelligence_hub.modules.action_queue.lifecycle import mark_action_ready
from doc_intelligence_hub.modules.action_queue.pipeline import run_pipeline
from doc_intelligence_hub.modules.triage.relationship_discovery import (
    normalize_paperless_document,
)
from doc_intelligence_hub.modules.triage.relationships import (
    classify_related_notice,
    list_document_relationships,
)

router = APIRouter(prefix="/api/intake", tags=["intake-review"])

_TAXONOMY_METHODS = {
    "tag": ("list_tags", "create_tag"),
    "correspondent": ("list_correspondents", "create_correspondent"),
    "document_type": ("list_document_types", "create_document_type"),
    "storage_path": ("list_storage_paths", "create_storage_path"),
}
_TAXONOMY_CACHE_TTL = timedelta(minutes=5)
_SUGGESTION_CACHE_TTL = timedelta(hours=6)
_taxonomy_cache: tuple[datetime, dict[str, list[dict[str, Any]]]] | None = None
_taxonomy_cache_lock = asyncio.Lock()
_EDITABLE_CUSTOM_FIELD_KEYS = {
    MetadataFieldKey.DOCUMENT_AMOUNT,
    MetadataFieldKey.DOCUMENT_DUE_DATE,
}
_HIDDEN_CUSTOM_FIELD_NAMES = {
    "Action Status",
    "Action Analyzed",
    "Action Type",
    "Action Due Date",
    "Action Urgency",
    "Action Summary",
    "Action Count",
    "Related Document IDs",
    "Relationship Summary",
    "Related Documents",
    "Obligation ID",
    "Obligation Role",
}


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


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

    @field_validator("custom_fields")
    @classmethod
    def valid_custom_fields(cls, value: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if value is None:
            return None
        field_ids: list[int] = []
        for item in value:
            field_id = item.get("field")
            if isinstance(field_id, bool) or not isinstance(field_id, int) or field_id <= 0:
                raise ValueError("custom fields require a positive integer field ID")
            if "value" not in item:
                raise ValueError("custom fields require a value")
            field_ids.append(field_id)
        if len(field_ids) != len(set(field_ids)):
            raise ValueError("custom fields cannot contain duplicate field IDs")
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
        catalog_by_name = {
            str(item.get("name") or "").strip().casefold(): item
            for item in catalog.values()
            if item.get("name")
        }
        for raw in _suggestion_values(payload, *keys):
            item = _catalog_value(catalog, raw)
            if item is None and isinstance(raw, str):
                name = raw.strip()
                item = catalog_by_name.get(name.casefold()) if name else None
                if item is None and name and field != "tags":
                    item = {"id": None, "name": name, "create": True}
            if item is None or item.get("id") in applied:
                continue
            identity = (field, str(item.get("id") or item.get("name", "")).casefold())
            if identity in seen:
                continue
            seen.add(identity)
            normalized.append({"field": field, "source": source, **item})

    for field, keys in {
        "title": ("title", "suggested_title"),
        "created": (
            "created",
            "date",
            "dates",
            "created_dates",
            "suggested_date",
            "suggested_dates",
        ),
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
    global _taxonomy_cache
    now = datetime.now(UTC)
    if _taxonomy_cache and now - _taxonomy_cache[0] < _TAXONOMY_CACHE_TTL:
        return _taxonomy_cache[1]
    async with _taxonomy_cache_lock:
        now = datetime.now(UTC)
        if _taxonomy_cache and now - _taxonomy_cache[0] < _TAXONOMY_CACHE_TTL:
            return _taxonomy_cache[1]
        taxonomies = await _fetch_taxonomies(client)
        _taxonomy_cache = (now, taxonomies)
        return taxonomies


async def _fetch_taxonomies(client: Any) -> dict[str, list[dict[str, Any]]]:
    tags, correspondents, document_types, storage_paths, custom_fields = await asyncio.gather(
        client.list_tags(),
        client.list_correspondents(),
        client.list_document_types(),
        client.list_storage_paths(),
        client.list_custom_fields(),
    )
    normalized_tags = [
        {
            **tag,
            "colour": tag.get("colour") or tag.get("color"),
        }
        for tag in tags
    ]
    return {
        "tags": normalized_tags,
        "correspondents": correspondents,
        "document_types": document_types,
        "storage_paths": storage_paths,
        "custom_fields": custom_fields,
    }


def _paperless_document_url(request: Request, document_id: int) -> str | None:
    settings = request.app.state.hub_settings
    base_url = settings.paperless_browser_url or settings.paperless_url
    if not base_url:
        return None
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{base_url.rstrip('/')}/documents/{document_id}/details"


def _metadata_spec_by_name(name: str) -> tuple[MetadataFieldKey, Any] | None:
    normalized = name.strip().casefold()
    for key in MetadataFieldKey:
        spec = get_metadata_field_spec(key)
        if normalized in {
            spec.canonical_name.casefold(),
            *(alias.casefold() for alias in spec.aliases),
        }:
            return key, spec
    return None


def _review_custom_fields(
    document: dict[str, Any], definitions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    definitions_by_id = {
        definition.get("id"): definition
        for definition in definitions
        if isinstance(definition, dict)
    }
    fields: list[dict[str, Any]] = []
    for item in document.get("custom_fields") or []:
        if not isinstance(item, dict):
            continue
        definition = definitions_by_id.get(item.get("field"))
        if not definition:
            continue
        name = str(definition.get("name") or f"Field {item.get('field')}")
        if name in _HIDDEN_CUSTOM_FIELD_NAMES:
            continue
        matched = _metadata_spec_by_name(name)
        if (
            matched
            and matched[1].projection_policy is MetadataProjectionPolicy.OPERATIONAL
            and matched[0] not in _EDITABLE_CUSTOM_FIELD_KEYS
        ):
            continue
        key = matched[0] if matched else None
        fields.append(
            {
                "field": item.get("field"),
                "name": name,
                "value": item.get("value"),
                "data_type": definition.get("data_type"),
                "editable": key in _EDITABLE_CUSTOM_FIELD_KEYS,
                "audit": key is MetadataFieldKey.ACCOUNT_IDENTIFIER,
            }
        )
    return fields


def _native_document_payload(
    document: dict[str, Any],
    taxonomies: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    catalogs = {
        key: _catalog_map(taxonomies[key])
        for key in ("tags", "correspondents", "document_types", "storage_paths")
    }
    return {
        **document,
        "document_summary": build_document_summary(
            {
                **document,
                "correspondent_name": (
                    _catalog_value(catalogs["correspondents"], document.get("correspondent")) or {}
                ).get("name"),
                "document_type_name": (
                    _catalog_value(catalogs["document_types"], document.get("document_type")) or {}
                ).get("name"),
            }
        ),
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
        "review_fields": _review_custom_fields(document, taxonomies["custom_fields"]),
    }


def _relationship_document_payload(
    document: dict[str, Any],
    taxonomies: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    catalogs = {key: _catalog_map(taxonomies[key]) for key in ("correspondents", "document_types")}
    document_id = int(document["id"])
    return {
        "id": document_id,
        "title": document.get("title") or f"Document #{document_id}",
        "created": document.get("created"),
        "correspondent_name": (
            _catalog_value(catalogs["correspondents"], document.get("correspondent")) or {}
        ).get("name"),
        "document_type_name": (
            _catalog_value(catalogs["document_types"], document.get("document_type")) or {}
        ).get("name"),
        "thumbnail_url": f"/api/statements/documents/{document_id}/thumb",
        "preview_url": f"/api/statements/documents/{document_id}/preview",
    }


def _relationship_metadata(
    document: dict[str, Any],
    taxonomies: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    correspondents = {
        int(item["id"]): str(item["name"])
        for item in taxonomies["correspondents"]
        if isinstance(item.get("id"), int) and item.get("name")
    }
    field_names = {
        int(item["id"]): str(item["name"])
        for item in taxonomies["custom_fields"]
        if isinstance(item.get("id"), int) and item.get("name")
    }
    return normalize_paperless_document(
        document,
        correspondents=correspondents,
        field_names=field_names,
    ) or {"document_id": document.get("id"), "title": document.get("title")}


def _sanitize_tag_suggestions(
    suggestions: list[dict[str, Any]],
    taxonomies: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    tags = _catalog_map(taxonomies["tags"])
    sanitized: list[dict[str, Any]] = []
    for suggestion in suggestions:
        if suggestion.get("field") != "tags":
            sanitized.append(suggestion)
            continue
        tag = _catalog_value(tags, suggestion.get("id"))
        if tag is not None and not tag.get("unresolved"):
            sanitized.append(
                {
                    **suggestion,
                    **tag,
                    "field": "tags",
                    "source": suggestion.get("source"),
                }
            )
    return sanitized


async def _suggestions_for_document(
    client: Any,
    document: dict[str, Any],
    taxonomies: dict[str, list[dict[str, Any]]],
    *,
    force: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], bool]:
    init_db()
    db = get_session()
    source_modified = str(document.get("modified") or "")
    try:
        snapshot = db.get(IntakeSuggestionSnapshot, int(document["id"]))
        if (
            not force
            and snapshot
            and snapshot.source_modified == source_modified
            and _utcnow_naive() - snapshot.fetched_at < _SUGGESTION_CACHE_TTL
            and all(state.get("available") for state in (snapshot.source_states or {}).values())
        ):
            return (
                _sanitize_tag_suggestions(list(snapshot.suggestions or []), taxonomies),
                dict(snapshot.source_states or {}),
                True,
            )

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
            client.get_document_suggestions(int(document["id"])),
            client.get_document_ai_suggestions(int(document["id"])),
            return_exceptions=True,
        )
        suggestions: list[dict[str, Any]] = []
        source_states: dict[str, dict[str, Any]] = {}
        for source, result in zip(
            ("paperless_ml", "paperless_ai"), suggestion_results, strict=True
        ):
            if isinstance(result, Exception):
                status = (
                    result.response.status_code
                    if isinstance(result, httpx.HTTPStatusError)
                    else None
                )
                source_states[source] = {
                    "available": False,
                    "status": status,
                    "error": str(result),
                }
                continue
            source_states[source] = {"available": True, "status": 200, "error": None}
            suggestions.extend(_normalize_suggestions(result, source, catalogs, current))
        suggestions = _sanitize_tag_suggestions(suggestions, taxonomies)

        if snapshot is None:
            snapshot = IntakeSuggestionSnapshot(document_id=int(document["id"]))
            db.add(snapshot)
        snapshot.source_modified = source_modified
        snapshot.suggestions = suggestions
        snapshot.source_states = source_states
        snapshot.fetched_at = _utcnow_naive()
        db.commit()
        return suggestions, source_states, False
    finally:
        db.close()


def _action_and_quality_payload(document_id: int) -> dict[str, Any]:
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
        history = (
            db.query(ProcessingHistory)
            .filter_by(document_id=document_id)
            .order_by(ProcessingHistory.processed_at.desc())
            .first()
        )
        score = history.text_quality_score if history else None
        return {
            "actions": [_serialize_action_with_siblings(db, action) for action in actions],
            "analysis": {
                "available": history is not None,
                "processed_at": (
                    history.processed_at.isoformat() if history and history.processed_at else None
                ),
                "disposition": history.disposition if history else None,
            },
            "ocr_quality": {
                "quality_score": score,
                "quality_band": (
                    "green"
                    if score is not None and score >= 90
                    else "amber"
                    if score is not None and score >= 75
                    else "red"
                    if score is not None
                    else "unknown"
                ),
                "poor_quality": score is not None and score < 75,
            },
        }
    finally:
        db.close()


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
        suggestions, suggestion_sources, suggestion_cached = await _suggestions_for_document(
            client, document, taxonomies
        )
        action_payload = _action_and_quality_payload(document_id)
        return {
            "document": _native_document_payload(document, taxonomies),
            "taxonomies": taxonomies,
            "suggestions": suggestions,
            "suggestion_sources": suggestion_sources,
            "suggestions_cached": suggestion_cached,
            "actions": action_payload["actions"],
            "ocr": {
                **action_payload["ocr_quality"],
                "text": content,
            },
            "analysis": {
                **action_payload["analysis"],
                "source_modified": document.get("modified"),
            },
            "preview_url": f"/api/statements/documents/{document_id}/preview",
            "download_url": f"/api/documents/{document_id}/download",
            "paperless_url": _paperless_document_url(request, document_id),
            "write_enabled": bool(request.app.state.hub_settings.write_to_paperless),
            "monitor_tags": _monitor_tags(),
        }
    finally:
        await client.aclose()


@router.get("/documents")
async def list_intake_documents(
    request: Request,
    page_size: int = Query(default=100, ge=1, le=200),
    cursor: str | None = None,
    search: str = Query(default="", max_length=200),
    queue_filter: Literal["all", "actions", "missing_type"] = "all",
) -> dict[str, Any]:
    client = make_paperless_client(request, timeout=30.0)
    try:
        try:
            tag_ids = await client.resolve_tag_ids(_monitor_tags())
            scope_params = {
                "tags__id__in": ",".join(str(tag_id) for tag_id in tag_ids) if tag_ids else "0",
                "ordering": "-id",
            }
            if search.strip():
                scope_params["query"] = search.strip()
            if queue_filter == "missing_type":
                scope_params["document_type__isnull"] = "true"
            elif queue_filter == "actions":
                init_db()
                db = get_session()
                try:
                    active_document_ids = [
                        document_id
                        for (document_id,) in (
                            db.query(Action.document_id)
                            .filter(
                                Action.superseded_by_action_id.is_(None),
                                Action.status.notin_({"completed", "dismissed", "not_an_action"}),
                            )
                            .distinct()
                            .all()
                        )
                    ]
                finally:
                    db.close()
                scope_params["id__in"] = (
                    ",".join(str(document_id) for document_id in active_document_ids)
                    if active_document_ids
                    else "0"
                )
            page, taxonomies = await asyncio.gather(
                client.list_document_page(
                    page_size=page_size,
                    cursor=cursor,
                    scope_params=scope_params,
                ),
                _load_taxonomies(client),
            )
            documents = list(page.results)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            _raise_paperless_error(exc, "loading the intake queue")
        document_ids = [document.get("id") for document in documents if document.get("id")]
        catalogs = {
            key: _catalog_map(taxonomies[key])
            for key in ("tags", "correspondents", "document_types")
        }
        init_db()
        db = get_session()
        try:
            action_rows = (
                db.query(Action.document_id, Action.status, func.count(Action.id))
                .filter(
                    Action.document_id.in_(document_ids),
                    Action.superseded_by_action_id.is_(None),
                )
                .group_by(Action.document_id, Action.status)
                .all()
                if document_ids
                else []
            )
        finally:
            db.close()
        counts: dict[int, dict[str, int]] = {}
        for document_id, status, count in action_rows:
            counts.setdefault(document_id, {})[status] = count
        return {
            "documents": [
                {
                    "id": document.get("id"),
                    "title": document.get("title") or "Untitled document",
                    "created": document.get("created"),
                    "modified": document.get("modified"),
                    "correspondent": document.get("correspondent"),
                    "correspondent_name": (
                        _catalog_value(catalogs["correspondents"], document.get("correspondent"))
                        or {}
                    ).get("name"),
                    "document_type": document.get("document_type"),
                    "document_type_name": (
                        _catalog_value(catalogs["document_types"], document.get("document_type"))
                        or {}
                    ).get("name"),
                    "tags": document.get("tags") or [],
                    "tag_values": [
                        item
                        for tag_id in document.get("tags") or []
                        if (item := _catalog_value(catalogs["tags"], tag_id)) is not None
                    ],
                    "document_summary": build_document_summary(
                        {
                            **document,
                            "correspondent_name": (
                                _catalog_value(
                                    catalogs["correspondents"],
                                    document.get("correspondent"),
                                )
                                or {}
                            ).get("name"),
                            "document_type_name": (
                                _catalog_value(
                                    catalogs["document_types"],
                                    document.get("document_type"),
                                )
                                or {}
                            ).get("name"),
                        }
                    ),
                    "thumbnail_url": f"/api/statements/documents/{document.get('id')}/thumb",
                    "action_counts": counts.get(document.get("id"), {}),
                    "pending_action_count": sum(
                        count
                        for status, count in counts.get(document.get("id"), {}).items()
                        if status not in {"completed", "not_action", "confirmed_no_action"}
                    ),
                }
                for document in documents
            ],
            "total": page.total_count,
            "next_cursor": page.next_cursor,
            "monitor_tags": _monitor_tags(),
        }
    finally:
        await client.aclose()


@router.get("/documents/{document_id}")
async def get_intake_document(request: Request, document_id: int) -> dict[str, Any]:
    return await _document_detail(request, document_id)


@router.get("/documents/{document_id}/core")
async def get_intake_document_core(request: Request, document_id: int) -> dict[str, Any]:
    client = make_paperless_client(request, timeout=15.0)
    try:
        try:
            document, taxonomies = await asyncio.gather(
                client.get_document(document_id),
                _load_taxonomies(client),
            )
        except Exception as exc:
            _raise_paperless_error(exc, "loading the intake document")
        return {
            "document": _native_document_payload(document, taxonomies),
            "taxonomies": taxonomies,
            "preview_url": f"/api/statements/documents/{document_id}/preview",
            "download_url": f"/api/documents/{document_id}/download",
            "paperless_url": _paperless_document_url(request, document_id),
            "write_enabled": bool(request.app.state.hub_settings.write_to_paperless),
            "monitor_tags": _monitor_tags(),
        }
    finally:
        await client.aclose()


@router.get("/documents/{document_id}/relationships")
async def get_intake_document_relationships(
    request: Request,
    document_id: int,
) -> dict[str, Any]:
    relationships = list_document_relationships(document_id)
    related_ids = sorted(
        {
            relationship["target_document_id"]
            if relationship["source_document_id"] == document_id
            else relationship["source_document_id"]
            for relationship in relationships
        }
    )
    if not related_ids:
        return {"relationships": [], "count": 0}

    client = make_paperless_client(request, timeout=20.0)
    try:
        try:
            documents, taxonomies = await asyncio.gather(
                asyncio.gather(*(client.get_document(related_id) for related_id in related_ids)),
                _load_taxonomies(client),
            )
        except Exception as exc:
            _raise_paperless_error(exc, "loading related documents")
        documents_by_id = {int(document["id"]): document for document in documents}
        enriched = []
        for relationship in relationships:
            related_id = (
                relationship["target_document_id"]
                if relationship["source_document_id"] == document_id
                else relationship["source_document_id"]
            )
            related_document = documents_by_id.get(related_id)
            enriched.append(
                {
                    **relationship,
                    "direction": (
                        "outgoing"
                        if relationship["source_document_id"] == document_id
                        else "incoming"
                    ),
                    "related_document": (
                        _relationship_document_payload(related_document, taxonomies)
                        if related_document
                        else None
                    ),
                }
            )
        return {"relationships": enriched, "count": len(enriched)}
    finally:
        await client.aclose()


@router.get("/documents/{document_id}/relationship-candidates")
async def get_intake_relationship_candidates(
    request: Request,
    document_id: int,
    q: str | None = None,
    limit: int = 12,
) -> dict[str, Any]:
    client = make_paperless_client(request, timeout=20.0)
    bounded_limit = max(1, min(limit, 25))
    try:
        try:
            current, taxonomies = await asyncio.gather(
                client.get_document(document_id),
                _load_taxonomies(client),
            )
            query = (q or "").strip()
            if query:
                documents = await client.list_documents(query=query, limit=bounded_limit)
            elif isinstance(current.get("correspondent"), int):
                documents = await client.list_documents(
                    correspondent_id=current["correspondent"],
                    limit=bounded_limit,
                )
            else:
                documents = await client.list_documents(
                    query=str(current.get("title") or "").strip(),
                    limit=bounded_limit,
                )
        except Exception as exc:
            _raise_paperless_error(exc, "finding related documents")

        linked_ids = {
            relationship["target_document_id"]
            if relationship["source_document_id"] == document_id
            else relationship["source_document_id"]
            for relationship in list_document_relationships(document_id)
        }
        current_metadata = _relationship_metadata(current, taxonomies)
        candidates = []
        for document in documents:
            candidate_id = document.get("id")
            if (
                isinstance(candidate_id, bool)
                or not isinstance(candidate_id, int)
                or candidate_id == document_id
                or candidate_id in linked_ids
            ):
                continue
            candidate_metadata = _relationship_metadata(document, taxonomies)
            proposal = classify_related_notice(
                document_id,
                current_metadata,
                candidate_id,
                candidate_metadata,
            )
            if proposal:
                recommendation = proposal.to_dict()
            else:
                same_correspondent = current.get("correspondent") is not None and current.get(
                    "correspondent"
                ) == document.get("correspondent")
                same_type = current.get("document_type") is not None and current.get(
                    "document_type"
                ) == document.get("document_type")
                recommendation = {
                    "source_document_id": document_id,
                    "target_document_id": candidate_id,
                    "relationship_type": (
                        "same_sequence" if same_correspondent and same_type else "supports"
                    ),
                    "confidence": 0.58 if same_correspondent and same_type else 0.35,
                    "reason_codes": [
                        reason
                        for matched, reason in (
                            (same_correspondent, "correspondent_match"),
                            (same_type, "document_type_match"),
                            (bool(query), "search_match"),
                        )
                        if matched
                    ],
                    "priority_adjustment": 0,
                    "priority_explanation": "No relationship priority adjustment",
                    "auto_create": False,
                }
            candidates.append(
                {
                    "document": _relationship_document_payload(document, taxonomies),
                    "suggested": not bool(query),
                    "recommendation": recommendation,
                }
            )
        candidates.sort(
            key=lambda candidate: (
                -float(candidate["recommendation"]["confidence"]),
                str(candidate["document"].get("created") or ""),
                candidate["document"]["id"],
            )
        )
        return {"candidates": candidates[:bounded_limit], "query": query}
    finally:
        await client.aclose()


@router.get("/documents/{document_id}/suggestions")
async def get_intake_document_suggestions(
    request: Request, document_id: int, refresh: bool = False
) -> dict[str, Any]:
    client = make_paperless_client(request, timeout=30.0)
    try:
        try:
            document, taxonomies = await asyncio.gather(
                client.get_document(document_id),
                _load_taxonomies(client),
            )
            suggestions, sources, cached = await _suggestions_for_document(
                client, document, taxonomies, force=refresh
            )
        except HTTPException:
            raise
        except Exception as exc:
            _raise_paperless_error(exc, "loading document suggestions")
        return {
            "suggestions": suggestions,
            "suggestion_sources": sources,
            "cached": cached,
            "source_modified": document.get("modified"),
        }
    finally:
        await client.aclose()


@router.get("/documents/{document_id}/actions")
async def get_intake_document_actions(document_id: int) -> dict[str, Any]:
    return _action_and_quality_payload(document_id)


@router.get("/documents/{document_id}/ocr")
async def get_intake_document_ocr(request: Request, document_id: int) -> dict[str, Any]:
    client = make_paperless_client(request, timeout=30.0)
    try:
        try:
            content = await client.get_document_content(document_id)
        except Exception as exc:
            _raise_paperless_error(exc, "loading OCR text")
        return {"text": content, **_action_and_quality_payload(document_id)["ocr_quality"]}
    finally:
        await client.aclose()


@router.post("/taxonomies/{kind}")
async def create_taxonomy(
    request: Request,
    kind: Literal["tag", "correspondent", "document_type", "storage_path"],
    body: TaxonomyCreateRequest,
) -> dict[str, Any]:
    global _taxonomy_cache
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
        _taxonomy_cache = None
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
            if field != "custom_fields":
                patch[field] = value.isoformat() if isinstance(value, date) else value

        if body.metadata.custom_fields is not None:
            try:
                definitions = await client.list_custom_fields()
            except Exception as exc:
                _raise_paperless_error(exc, "validating custom field updates")
            editable_ids = {
                definition["id"]
                for definition in definitions
                if isinstance(definition, dict)
                and definition.get("id")
                and (matched := _metadata_spec_by_name(str(definition.get("name") or "")))
                and matched[0] in _EDITABLE_CUSTOM_FIELD_KEYS
            }
            requested_ids = {item["field"] for item in body.metadata.custom_fields}
            disallowed = sorted(requested_ids - editable_ids)
            if disallowed:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "custom_field_not_editable",
                        "message": "One or more custom fields are read-only in Inbox Review.",
                        "field_ids": disallowed,
                    },
                )
            requested_by_id = {item["field"]: item["value"] for item in body.metadata.custom_fields}
            merged_custom_fields = [
                {
                    "field": item["field"],
                    "value": requested_by_id.get(item["field"], item.get("value")),
                }
                for item in current.get("custom_fields") or []
                if isinstance(item, dict) and item.get("field")
            ]
            existing_ids = {item["field"] for item in merged_custom_fields}
            merged_custom_fields.extend(
                {"field": field_id, "value": value}
                for field_id, value in requested_by_id.items()
                if field_id not in existing_ids
            )
            patch["custom_fields"] = merged_custom_fields

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
    client = make_paperless_client(request, timeout=30.0)
    try:
        document, taxonomies = await asyncio.gather(
            client.get_document(document_id),
            _load_taxonomies(client),
        )
        suggestions, sources, _ = await _suggestions_for_document(
            client, document, taxonomies, force=True
        )
    finally:
        await client.aclose()
    return {
        "result": result,
        "actions": _action_and_quality_payload(document_id),
        "suggestions": suggestions,
        "suggestion_sources": sources,
    }


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
