"""Unified Paperless intake review API."""

from __future__ import annotations

import asyncio
import logging
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
from doc_intelligence_hub.core.extractors.account_numbers import (
    evaluate_account_identifiers,
    extract_account_numbers,
    normalize_masked_account_identifier,
)
from doc_intelligence_hub.core.paperless import (
    AccountIdentifierClass,
    MetadataFieldKey,
    MetadataProjectionPolicy,
    MetadataSchemaError,
    MetadataValueError,
    PaperlessRevisionConflict,
    build_account_identifier_update,
    get_metadata_field_spec,
    resolve_metadata_schema,
)
from doc_intelligence_hub.core.paperless.writeback import (
    audit_paperless_write,
    build_write_conflicts,
    is_idempotent_replay,
    sanitize_initiator,
    values_equal,
)
from doc_intelligence_hub.modules.action_queue.analyzer import OllamaAnalyzer
from doc_intelligence_hub.modules.action_queue.database import (
    VALID_ACTION_TYPES,
    Action,
    ActionLifecycleEvent,
    IntakeSuggestionSnapshot,
    ProcessingHistory,
    get_session,
    init_db,
)
from doc_intelligence_hub.modules.action_queue.lifecycle import mark_action_ready
from doc_intelligence_hub.modules.action_queue.pipeline import (
    ACTION_QUEUE_ANALYSIS_MODULE_NAME,
    ACTION_QUEUE_ANALYSIS_MODULE_VERSION,
    _analysis_config_hash,
    run_pipeline,
)
from doc_intelligence_hub.modules.analysis_invalidation.config import (
    settings as invalidation_settings,
)
from doc_intelligence_hub.modules.analysis_invalidation.database import (
    init_db as init_invalidation_db,
)
from doc_intelligence_hub.modules.analysis_invalidation.fingerprint import (
    document_source_fingerprint,
)
from doc_intelligence_hub.modules.analysis_invalidation.models import (
    FreshnessStatus,
    InvalidationReason,
)
from doc_intelligence_hub.modules.analysis_invalidation.service import (
    AnalysisFreshnessService,
)
from doc_intelligence_hub.modules.analysis_invalidation.targeted_reanalysis import (
    TargetedReanalysisService,
    spawn_debounced_reanalysis,
)
from doc_intelligence_hub.modules.triage.reference_discovery import (
    extract_explicit_references,
    resolve_references,
)
from doc_intelligence_hub.modules.triage.relationship_discovery import (
    normalize_paperless_document,
)
from doc_intelligence_hub.modules.triage.relationships import (
    classify_related_notice,
    list_document_relationships,
)

router = APIRouter(prefix="/api/intake", tags=["intake-review"])
logger = logging.getLogger(__name__)

_TAXONOMY_METHODS = {
    "tag": ("list_tags", "create_tag"),
    "correspondent": ("list_correspondents", "create_correspondent"),
    "document_type": ("list_document_types", "create_document_type"),
    "storage_path": ("list_storage_paths", "create_storage_path"),
}
_TAXONOMY_CACHE_TTL = timedelta(minutes=5)
_SUGGESTION_CACHE_TTL = timedelta(hours=6)
_INBOX_TAG_NAME = "Inbox"
_taxonomy_cache: tuple[datetime, dict[str, list[dict[str, Any]]]] | None = None
_taxonomy_cache_lock = asyncio.Lock()
_EDITABLE_CUSTOM_FIELD_KEYS = {
    MetadataFieldKey.ACCOUNT_IDENTIFIER,
    MetadataFieldKey.DOCUMENT_AMOUNT,
    MetadataFieldKey.DOCUMENT_DUE_DATE,
    MetadataFieldKey.INVOICE_NUMBER,
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
    "OWL Related Documents",
    "Relationship Summary",
    "Related Documents",
    "Obligation ID",
    "Obligation Role",
}


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class IntakeCustomFieldPatch(BaseModel):
    field: int = Field(gt=0)
    value: Any = None
    identifier_class: AccountIdentifierClass | None = None


class IntakeMetadataPatch(BaseModel):
    title: str | None = None
    correspondent: int | None = Field(default=None, ge=1)
    document_type: int | None = Field(default=None, ge=1)
    storage_path: int | None = Field(default=None, ge=1)
    created: date | None = None
    tags: list[int] | None = None
    custom_fields: list[IntakeCustomFieldPatch] | None = None

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
    def valid_custom_fields(
        cls, value: list[IntakeCustomFieldPatch] | None
    ) -> list[IntakeCustomFieldPatch] | None:
        if value is None:
            return None
        field_ids = [item.field for item in value]
        if len(field_ids) != len(set(field_ids)):
            raise ValueError("custom fields cannot contain duplicate field IDs")
        return value


class IntakeSaveRequest(BaseModel):
    expected_modified: str
    metadata: IntakeMetadataPatch
    base_metadata: IntakeMetadataPatch | None = None
    disposition: Literal["save", "defer", "file_only", "confirm_and_file"] = "save"
    action_versions: dict[int, int] = Field(default_factory=dict)
    initiated_by: str = Field(default="owl-intake-review", min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_confirmation(self) -> "IntakeSaveRequest":
        if self.disposition == "confirm_and_file" and not self.action_versions:
            raise ValueError("action_versions are required when confirming actions")
        return self


_INTAKE_FIELD_LABELS = {
    "title": "Title",
    "created": "Document date",
    "correspondent": "Correspondent",
    "document_type": "Document type",
    "storage_path": "Storage path",
    "tags": "Tags",
}


def _intake_patch_values(patch: IntakeMetadataPatch | None) -> dict[str, Any]:
    if patch is None:
        return {}
    values: dict[str, Any] = {}
    for field in patch.model_fields_set:
        value = getattr(patch, field)
        if field == "custom_fields":
            for item in value or []:
                values[f"custom_fields.{item.field}"] = item.value
        else:
            values[field] = value.isoformat() if isinstance(value, date) else value
    return values


def _intake_document_values(document: dict[str, Any], fields: set[str]) -> dict[str, Any]:
    custom_fields = {
        int(item["field"]): item.get("value")
        for item in document.get("custom_fields") or []
        if isinstance(item, dict) and str(item.get("field", "")).isdigit()
    }
    return {
        field: (
            custom_fields.get(int(field.rsplit(".", 1)[1]))
            if field.startswith("custom_fields.")
            else document.get(field)
        )
        for field in fields
    }


def _intake_field_labels(definitions: list[dict[str, Any]]) -> dict[str, str]:
    labels = dict(_INTAKE_FIELD_LABELS)
    labels.update(
        {
            f"custom_fields.{definition['id']}": str(definition.get("name") or "Custom field")
            for definition in definitions
            if isinstance(definition, dict) and definition.get("id")
        }
    )
    return labels


def _source_revision_conflict(
    *,
    expected_modified: str,
    current: dict[str, Any],
    loaded_values: dict[str, Any],
    proposed_values: dict[str, Any],
    labels: dict[str, str],
) -> HTTPException:
    current_values = _intake_document_values(current, set(proposed_values))
    return HTTPException(
        status_code=409,
        detail={
            "code": "source_revision_conflict",
            "message": "This document changed in Paperless. Resolve the changed fields before saving.",
            "expected_modified": expected_modified,
            "current_modified": current.get("modified"),
            "conflicts": build_write_conflicts(
                loaded=loaded_values,
                current=current_values,
                proposed=proposed_values,
                labels=labels,
            ),
        },
    )


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
    summary: str | None = Field(default=None, max_length=10_000)
    payee_or_owner: str | None = None
    amount: float | None = Field(default=None, ge=0)
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

    @field_validator("summary", "payee_or_owner", "reference")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def require_payment_amount(self) -> "UserActionCreateRequest":
        if self.action_type == "PAY" and self.amount is None:
            raise ValueError("amount is required for PAY actions")
        return self


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


async def _resolve_inbox_tag_id(client: Any) -> int:
    tag_ids = await client.resolve_tag_ids([_INBOX_TAG_NAME])
    if not tag_ids:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "paperless_inbox_tag_missing",
                "message": (
                    "Inbox Review requires a Paperless tag named 'Inbox'. "
                    "Create the tag in Paperless before using Inbox Review."
                ),
            },
        )
    return tag_ids[0]


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
    document: dict[str, Any],
    definitions: list[dict[str, Any]],
    *,
    content: str | None = None,
) -> list[dict[str, Any]]:
    values_by_id = {
        item.get("field"): item.get("value")
        for item in document.get("custom_fields") or []
        if isinstance(item, dict) and item.get("field")
    }
    fields: list[dict[str, Any]] = []
    for definition in definitions:
        if not isinstance(definition, dict) or not definition.get("id"):
            continue
        field_id = definition["id"]
        name = str(definition.get("name") or f"Field {field_id}")
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
        if field_id not in values_by_id and key not in _EDITABLE_CUSTOM_FIELD_KEYS:
            continue
        stored_value = values_by_id.get(field_id)
        value = stored_value
        recovered_identifier_class: str | None = None
        recovered_from_document = False
        if (
            key is MetadataFieldKey.ACCOUNT_IDENTIFIER
            and normalize_masked_account_identifier(stored_value)
            and content
        ):
            decision = evaluate_account_identifiers(extract_account_numbers(content))
            if (
                decision.candidate
                and decision.projection
                and not decision.requires_review
                and normalize_masked_account_identifier(decision.projection.display_value)
                == normalize_masked_account_identifier(stored_value)
            ):
                value = decision.projection.paperless_value
                recovered_identifier_class = decision.projection.identifier_class.value
                recovered_from_document = value is not None and value != stored_value
        fields.append(
            {
                "field": field_id,
                "field_key": key.value if key else None,
                "name": name,
                "value": value,
                **(
                    {
                        "stored_value": stored_value,
                        "identifier_class": recovered_identifier_class,
                        "recovered_from_document": True,
                    }
                    if recovered_from_document
                    else {}
                ),
                "data_type": definition.get("data_type"),
                "editable": key in _EDITABLE_CUSTOM_FIELD_KEYS,
                "audit": False,
            }
        )
    return fields


def _needs_account_identifier_recovery(
    document: dict[str, Any],
    definitions: list[dict[str, Any]],
) -> bool:
    return any(
        field["field_key"] == MetadataFieldKey.ACCOUNT_IDENTIFIER.value
        and normalize_masked_account_identifier(field["value"])
        for field in _review_custom_fields(document, definitions)
    )


def _intake_core_payload(
    request: Request,
    document_id: int,
    document: dict[str, Any],
    taxonomies: dict[str, list[dict[str, Any]]],
    *,
    content: str | None = None,
) -> dict[str, Any]:
    return {
        "document": _native_document_payload(document, taxonomies, content=content),
        "taxonomies": taxonomies,
        "preview_url": f"/api/statements/documents/{document_id}/preview",
        "download_url": f"/api/documents/{document_id}/download",
        "paperless_url": _paperless_document_url(request, document_id),
        "write_enabled": bool(request.app.state.hub_settings.write_to_paperless),
        "analysis": _analysis_freshness_payload(document, taxonomies),
    }


def _analysis_input_ids(
    taxonomies: dict[str, list[dict[str, Any]]],
) -> tuple[set[int], set[int]]:
    governed_custom_field_ids = {
        int(definition["id"])
        for definition in taxonomies["custom_fields"]
        if isinstance(definition, dict)
        and definition.get("id")
        and (matched := _metadata_spec_by_name(str(definition.get("name") or "")))
        and matched[0] in _EDITABLE_CUSTOM_FIELD_KEYS
    }
    ignored_tag_ids = {
        int(tag["id"])
        for tag in taxonomies["tags"]
        if isinstance(tag, dict)
        and tag.get("id")
        and str(tag.get("name") or "").strip().casefold() == _INBOX_TAG_NAME.casefold()
    }
    return governed_custom_field_ids, ignored_tag_ids


def _document_analysis_inputs(
    document: dict[str, Any],
    taxonomies: dict[str, list[dict[str, Any]]],
) -> tuple[str, str, dict[str, Any]]:
    governed_custom_field_ids, ignored_tag_ids = _analysis_input_ids(taxonomies)
    return document_source_fingerprint(
        document,
        governed_custom_field_ids=governed_custom_field_ids,
        ignored_tag_ids=ignored_tag_ids,
    )


def _analysis_freshness_payload(
    document: dict[str, Any],
    taxonomies: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    init_invalidation_db()
    source_fingerprint, content_identity, metadata_fields = _document_analysis_inputs(
        document, taxonomies
    )
    freshness_service = AnalysisFreshnessService()
    freshness = freshness_service.check_freshness(
        document_id=int(document["id"]),
        module_name=ACTION_QUEUE_ANALYSIS_MODULE_NAME,
        module_version=ACTION_QUEUE_ANALYSIS_MODULE_VERSION,
        config_hash=_analysis_config_hash(OllamaAnalyzer().model),
        current_checksum=content_identity,
        metadata_fields=metadata_fields,
    )
    latest = freshness_service.get_latest_fingerprint(
        document_id=int(document["id"]),
        module_name=ACTION_QUEUE_ANALYSIS_MODULE_NAME,
    )
    request = TargetedReanalysisService().get(int(document["id"]))
    status = freshness.status.value
    if freshness.status is FreshnessStatus.UNKNOWN:
        status = "not_analyzed"
    if request and request["source_fingerprint"] == source_fingerprint:
        if request["status"] in {"pending", "running"}:
            status = "reanalyzing"
        elif request["status"] == "failed":
            status = "failed"
    return {
        "status": status,
        "available": latest is not None,
        "reasons": [reason.value for reason in freshness.reasons],
        "reason": request["reason"] if request else None,
        "analyzer_version": ACTION_QUEUE_ANALYSIS_MODULE_VERSION,
        "current_source_fingerprint": source_fingerprint,
        "evaluated_source_fingerprint": latest["source_fingerprint"] if latest else None,
        "evaluated_at": latest["computed_at"] if latest else None,
        "requested_at": request["requested_at"] if request else None,
        "due_at": request["due_at"] if request else None,
        "last_error_code": request["last_error_code"] if request else None,
    }


async def _run_targeted_analysis(
    request: Request,
    service: TargetedReanalysisService,
    document_id: int,
    expected_source_fingerprint: str,
    *,
    force: bool,
    verify_source: bool = True,
) -> dict[str, Any]:
    if verify_source:
        client = make_paperless_client(request, timeout=30.0)
        try:
            document, taxonomies = await asyncio.gather(
                client.get_document(document_id),
                _load_taxonomies(client),
            )
        finally:
            await client.aclose()
        current_source_fingerprint, _, _ = _document_analysis_inputs(document, taxonomies)
        if current_source_fingerprint != expected_source_fingerprint:
            replacement = service.schedule(
                document_id=document_id,
                source_fingerprint=current_source_fingerprint,
                source_modified=document.get("modified"),
                reason="source_changed_during_reanalysis",
                analyzer_version=ACTION_QUEUE_ANALYSIS_MODULE_VERSION,
                debounce_seconds=invalidation_settings.targeted_reanalysis_debounce_seconds,
            )
            spawn_debounced_reanalysis(
                service=service,
                document_id=document_id,
                generation=replacement["generation"],
                delay_seconds=invalidation_settings.targeted_reanalysis_debounce_seconds,
                analyze=lambda target_id, fingerprint: _run_targeted_analysis(
                    request,
                    service,
                    target_id,
                    fingerprint,
                    force=False,
                ),
            )
            return {"status": "superseded"}

    _sync_action_queue_settings(request)
    result = await run_pipeline(document_id=document_id, force=force, dry_run=False)
    if result.get("failed") or result.get("fetch_timed_out") or result.get("timed_out"):
        raise RuntimeError("targeted action analysis failed")
    return result


def _native_document_payload(
    document: dict[str, Any],
    taxonomies: dict[str, list[dict[str, Any]]],
    *,
    content: str | None = None,
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
        "review_fields": _review_custom_fields(
            document,
            taxonomies["custom_fields"],
            content=content,
        ),
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
    document_types = {
        int(item["id"]): str(item["name"])
        for item in taxonomies["document_types"]
        if isinstance(item.get("id"), int) and item.get("name")
    }
    return normalize_paperless_document(
        document,
        correspondents=correspondents,
        field_names=field_names,
        document_types=document_types,
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
            "document": _native_document_payload(document, taxonomies, content=content),
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
                **_analysis_freshness_payload(document, taxonomies),
            },
            "preview_url": f"/api/statements/documents/{document_id}/preview",
            "download_url": f"/api/documents/{document_id}/download",
            "paperless_url": _paperless_document_url(request, document_id),
            "write_enabled": bool(request.app.state.hub_settings.write_to_paperless),
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
    sort: Literal["newest", "oldest", "document_date", "title"] = "newest",
) -> dict[str, Any]:
    client = make_paperless_client(request, timeout=30.0)
    try:
        try:
            inbox_tag_id = await _resolve_inbox_tag_id(client)
            ordering = {
                "newest": "-id",
                "oldest": "id",
                "document_date": "-created",
                "title": "title",
            }[sort]
            base_scope_params = {
                "tags__id__in": str(inbox_tag_id),
                "ordering": ordering,
            }
            if search.strip():
                base_scope_params["query"] = search.strip()
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
            filter_scope_params = {
                "all": dict(base_scope_params),
                "actions": {
                    **base_scope_params,
                    "id__in": (
                        ",".join(str(document_id) for document_id in active_document_ids)
                        if active_document_ids
                        else "0"
                    ),
                },
                "missing_type": {
                    **base_scope_params,
                    "document_type__isnull": "true",
                },
            }
            count_filters = [
                filter_name for filter_name in filter_scope_params if filter_name != queue_filter
            ]
            page, taxonomies, *count_pages = await asyncio.gather(
                client.list_document_page(
                    page_size=page_size,
                    cursor=cursor,
                    scope_params=filter_scope_params[queue_filter],
                ),
                _load_taxonomies(client),
                *(
                    client.list_document_page(
                        page_size=1,
                        cursor=None,
                        scope_params=filter_scope_params[filter_name],
                    )
                    for filter_name in count_filters
                ),
            )
            filter_counts = {
                queue_filter: page.total_count,
                **{
                    filter_name: count_page.total_count
                    for filter_name, count_page in zip(count_filters, count_pages, strict=True)
                },
            }
            documents = list(page.results)
        except HTTPException:
            raise
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
            "filter_counts": filter_counts,
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
        content = document.get("content")
        if _needs_account_identifier_recovery(document, taxonomies["custom_fields"]):
            try:
                content = await client.get_document_content(document_id)
            except Exception as exc:
                logger.warning(
                    "Could not recover an exact Account Identifier for document %d: %s",
                    document_id,
                    type(exc).__name__,
                )
        return _intake_core_payload(
            request,
            document_id,
            document,
            taxonomies,
            content=content if isinstance(content, str) else None,
        )
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
            current, taxonomies, current_content = await asyncio.gather(
                client.get_document(document_id),
                _load_taxonomies(client),
                client.get_document_content(document_id),
            )
            current_relationship_metadata = _relationship_metadata(current, taxonomies)
            query = (q or "").strip()
            if query:
                documents = await client.list_documents(query=query, limit=bounded_limit)
                reference_matches: dict[int, list[str]] = {}
            else:
                references = await extract_explicit_references(
                    current_content if isinstance(current_content, str) else "",
                    title=str(current.get("title") or ""),
                    document_type=str(
                        current_relationship_metadata.get("document_type_name") or ""
                    ),
                )
                resolutions = await resolve_references(
                    client,
                    references,
                    exclude_document_id=document_id,
                )
                reference_matches = {}
                reference_document_ids: set[int] = set()
                for resolution in resolutions:
                    if resolution.status != "resolved":
                        continue
                    for related_id in resolution.document_ids:
                        reference_document_ids.add(related_id)
                        reference_matches.setdefault(related_id, []).append(
                            resolution.reference.value
                        )

                if isinstance(current.get("correspondent"), int):
                    standard_documents = await client.list_documents(
                        correspondent_id=current["correspondent"],
                        limit=bounded_limit,
                    )
                else:
                    standard_documents = await client.list_documents(
                        query=str(current.get("title") or "").strip(),
                        limit=bounded_limit,
                    )
                reference_documents = await asyncio.gather(
                    *(
                        client.get_document(related_id)
                        for related_id in sorted(reference_document_ids)
                    )
                )
                documents_by_id = {
                    int(document["id"]): document
                    for document in [*standard_documents, *reference_documents]
                    if isinstance(document.get("id"), int)
                    and not isinstance(document.get("id"), bool)
                }
                documents = list(documents_by_id.values())
        except Exception as exc:
            _raise_paperless_error(exc, "finding related documents")

        linked_ids = {
            relationship["target_document_id"]
            if relationship["source_document_id"] == document_id
            else relationship["source_document_id"]
            for relationship in list_document_relationships(document_id)
        }
        base_current_metadata = current_relationship_metadata
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
            current_metadata = dict(base_current_metadata)
            candidate_metadata = _relationship_metadata(document, taxonomies)
            matched_references = reference_matches.get(candidate_id, [])
            if matched_references:
                current_metadata["explicit_references"] = matched_references
                candidate_metadata["explicit_references"] = matched_references
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
                    "confidence": (
                        0.68
                        if matched_references
                        else 0.58
                        if same_correspondent and same_type
                        else 0.35
                    ),
                    "reason_codes": [
                        reason
                        for matched, reason in (
                            (bool(matched_references), "explicit_reference_match"),
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
    initiator = sanitize_initiator(body.initiated_by, fallback="owl-intake-review")
    proposed_values = _intake_patch_values(body.metadata)
    loaded_values = _intake_patch_values(body.base_metadata)
    write_fields = list(proposed_values)
    definitions: list[dict[str, Any]] = []
    try:
        try:
            current = await client.get_document(document_id)
        except Exception as exc:
            _raise_paperless_error(exc, "checking the current document revision")
        if body.metadata.custom_fields is not None:
            try:
                definitions = await client.list_custom_fields()
            except Exception as exc:
                _raise_paperless_error(exc, "validating custom field updates")
        labels = _intake_field_labels(definitions)
        current_values = _intake_document_values(current, set(proposed_values))
        replayed = current.get("modified") != body.expected_modified and is_idempotent_replay(
            current_values, proposed_values
        )
        if current.get("modified") != body.expected_modified and not replayed:
            audit_paperless_write(
                logger,
                initiator=initiator,
                surface="intake-review",
                fields=write_fields,
                outcome="conflict",
            )
            raise _source_revision_conflict(
                expected_modified=body.expected_modified,
                current=current,
                loaded_values=loaded_values,
                proposed_values=proposed_values,
                labels=labels,
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
            editable_keys_by_id = {
                definition["id"]: matched[0]
                for definition in definitions
                if isinstance(definition, dict)
                and definition.get("id")
                and (matched := _metadata_spec_by_name(str(definition.get("name") or "")))
                and matched[0] in _EDITABLE_CUSTOM_FIELD_KEYS
            }
            requested_ids = {item.field for item in body.metadata.custom_fields}
            disallowed = sorted(requested_ids - editable_keys_by_id.keys())
            if disallowed:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "custom_field_not_editable",
                        "message": "One or more custom fields are read-only in Inbox Review.",
                        "field_ids": disallowed,
                    },
                )
            requested_by_id: dict[int, Any] = {}
            for item in body.metadata.custom_fields:
                key = editable_keys_by_id[item.field]
                if key is MetadataFieldKey.ACCOUNT_IDENTIFIER:
                    if item.value is None or (
                        isinstance(item.value, str) and not item.value.strip()
                    ):
                        requested_by_id[item.field] = None
                        continue
                    if item.identifier_class is None:
                        raise HTTPException(
                            status_code=422,
                            detail={
                                "code": "account_identifier_class_required",
                                "message": "Identifier type is required for Account Identifier.",
                                "field_ids": [item.field],
                            },
                        )
                    try:
                        schema = resolve_metadata_schema(
                            definitions, (MetadataFieldKey.ACCOUNT_IDENTIFIER,)
                        )
                        update, _projection = build_account_identifier_update(
                            item.value,
                            item.identifier_class,
                            1.0,
                            schema,
                        )
                    except (MetadataSchemaError, MetadataValueError) as exc:
                        raise HTTPException(
                            status_code=422,
                            detail={
                                "code": "invalid_account_identifier",
                                "message": str(exc),
                                "field_ids": [item.field],
                            },
                        ) from exc
                    requested_by_id[int(update["field"])] = update["value"]
                else:
                    requested_by_id[item.field] = item.value
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
            inbox_tag_id = await _resolve_inbox_tag_id(client)
            source_tags = patch.get("tags", current.get("tags") or [])
            patch["tags"] = [tag_id for tag_id in source_tags if tag_id != inbox_tag_id]
            if "tags" not in write_fields:
                write_fields.append("tags")

        try:
            effective_patch = {
                field: value
                for field, value in patch.items()
                if not values_equal(current.get(field), value)
            }
            updated_response = (
                await client.update_document(
                    document_id,
                    effective_patch,
                    expected_modified=current.get("modified"),
                )
                if effective_patch
                else current
            )
            updated = {**current, **updated_response}
        except PaperlessRevisionConflict as exc:
            audit_paperless_write(
                logger,
                initiator=initiator,
                surface="intake-review",
                fields=write_fields,
                outcome="conflict",
            )
            raise _source_revision_conflict(
                expected_modified=current.get("modified"),
                current=exc.current_document,
                loaded_values=_intake_document_values(current, set(proposed_values)),
                proposed_values=proposed_values,
                labels=labels,
            ) from exc
        except Exception as exc:
            audit_paperless_write(
                logger,
                initiator=initiator,
                surface="intake-review",
                fields=write_fields,
                outcome="failed",
            )
            _raise_paperless_error(exc, "saving document metadata")

        reanalysis: dict[str, Any] | None = None
        warnings: list[dict[str, str]] = []
        if patch:
            try:
                taxonomies = await _load_taxonomies(client)
            except Exception as exc:
                logger.warning(
                    "Paperless write succeeded but reanalysis preparation failed: %s", exc
                )
                warnings.append(
                    {
                        "code": "reanalysis_not_scheduled",
                        "message": "Changes were saved, but automatic reanalysis could not be scheduled.",
                    }
                )
            else:
                before_source_fingerprint, _, _ = _document_analysis_inputs(current, taxonomies)
                (
                    after_source_fingerprint,
                    content_identity,
                    metadata_fields,
                ) = _document_analysis_inputs(updated, taxonomies)
                if before_source_fingerprint != after_source_fingerprint:
                    init_invalidation_db()
                    AnalysisFreshnessService().record_invalidation(
                        document_id=document_id,
                        accepted_checksum=content_identity,
                        metadata_fields=metadata_fields,
                        reason=InvalidationReason.METADATA_CHANGED,
                        triggered_by=initiator,
                    )
                    targeted_service = TargetedReanalysisService()
                    reanalysis = targeted_service.schedule(
                        document_id=document_id,
                        source_fingerprint=after_source_fingerprint,
                        source_modified=updated.get("modified"),
                        reason="metadata_changed",
                        analyzer_version=ACTION_QUEUE_ANALYSIS_MODULE_VERSION,
                        debounce_seconds=invalidation_settings.targeted_reanalysis_debounce_seconds,
                    )
                    spawn_debounced_reanalysis(
                        service=targeted_service,
                        document_id=document_id,
                        generation=reanalysis["generation"],
                        delay_seconds=invalidation_settings.targeted_reanalysis_debounce_seconds,
                        analyze=lambda target_id, fingerprint: _run_targeted_analysis(
                            request,
                            targeted_service,
                            target_id,
                            fingerprint,
                            force=False,
                        ),
                    )

        if body.disposition == "confirm_and_file" and action_db is not None:
            try:
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
            except Exception as exc:
                action_db.rollback()
                audit_paperless_write(
                    logger,
                    initiator=initiator,
                    surface="intake-review",
                    fields=write_fields,
                    outcome="partial_failure",
                )
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "partial_write_failure",
                        "message": (
                            "Paperless was updated, but action confirmation failed. "
                            "Reload before retrying."
                        ),
                        "paperless_updated": True,
                        "current_modified": updated.get("modified"),
                    },
                ) from exc

        audit_paperless_write(
            logger,
            initiator=initiator,
            surface="intake-review",
            fields=write_fields,
            outcome="replayed" if replayed else "succeeded",
        )

        return {
            "document": updated,
            "disposition": body.disposition,
            "filed": body.disposition in {"file_only", "confirm_and_file"},
            "confirmed_action_ids": sorted(body.action_versions)
            if body.disposition == "confirm_and_file"
            else [],
            "reanalysis": reanalysis,
            "warnings": warnings,
            "idempotent_replay": replayed,
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
            current, current_taxonomies = await asyncio.gather(
                client.get_document(document_id),
                _load_taxonomies(client),
            )
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

    source_fingerprint, _, _ = _document_analysis_inputs(current, current_taxonomies)
    targeted_service = TargetedReanalysisService()
    reanalysis = targeted_service.schedule(
        document_id=document_id,
        source_fingerprint=source_fingerprint,
        source_modified=current.get("modified"),
        reason="manual_reanalysis",
        analyzer_version=ACTION_QUEUE_ANALYSIS_MODULE_VERSION,
        debounce_seconds=0,
    )
    result = await targeted_service.run_if_current(
        document_id=document_id,
        generation=reanalysis["generation"],
        analyze=lambda target_id, fingerprint: _run_targeted_analysis(
            request,
            targeted_service,
            target_id,
            fingerprint,
            force=True,
            verify_source=False,
        ),
    )
    if result.get("status") == "failed":
        raise HTTPException(
            status_code=502,
            detail={
                "code": "analysis_failed",
                "message": "The targeted analysis failed. Metadata changes were preserved.",
            },
        )
    client = make_paperless_client(request, timeout=30.0)
    try:
        document, taxonomies = await asyncio.gather(
            client.get_document(document_id),
            _load_taxonomies(client),
        )
        content = document.get("content")
        if _needs_account_identifier_recovery(document, taxonomies["custom_fields"]):
            content = await client.get_document_content(document_id)
        suggestions, sources, _ = await _suggestions_for_document(
            client, document, taxonomies, force=True
        )
    finally:
        await client.aclose()
    return {
        "result": result,
        "core": _intake_core_payload(
            request,
            document_id,
            document,
            taxonomies,
            content=content if isinstance(content, str) else None,
        ),
        "actions": _action_and_quality_payload(document_id),
        "suggestions": suggestions,
        "suggestion_sources": sources,
    }


@router.post("/reanalysis/drain")
async def drain_targeted_reanalysis(request: Request, limit: int = 25) -> dict[str, Any]:
    targeted_service = TargetedReanalysisService()
    due = targeted_service.list_due(limit=max(1, min(limit, 100)))
    results = []
    for pending in due:
        results.append(
            await targeted_service.run_if_current(
                document_id=pending["document_id"],
                generation=pending["generation"],
                analyze=lambda target_id, fingerprint: _run_targeted_analysis(
                    request,
                    targeted_service,
                    target_id,
                    fingerprint,
                    force=False,
                ),
            )
        )
    return {"processed": len(results), "results": results}


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
            summary=body.summary,
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
