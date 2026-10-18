"""Privacy-safe source inputs used to decide whether analysis remains valid."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


def stable_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def document_content_identity(document: dict[str, Any]) -> str:
    """Hash effective file/OCR version signals without persisting document text."""
    versions = document.get("versions")
    latest_version = versions[-1] if isinstance(versions, list) and versions else None
    stable_version_payload = {
        "checksum": document.get("checksum"),
        "archive_checksum": document.get("archive_checksum"),
        "original_checksum": document.get("original_checksum"),
        "effective_version": (
            {
                "id": latest_version.get("id"),
                "checksum": latest_version.get("checksum"),
            }
            if isinstance(latest_version, dict)
            else None
        ),
        "content_digest": (
            stable_digest(document["content"]) if isinstance(document.get("content"), str) else None
        ),
    }
    if any(value is not None for value in stable_version_payload.values()):
        payload = stable_version_payload
    else:
        payload = {
            "modified": document.get("modified"),
            "added": document.get("added"),
        }
    return stable_digest(payload)


def document_metadata_fields(
    document: dict[str, Any],
    *,
    governed_custom_field_ids: Iterable[int] = (),
    ignored_tag_ids: Iterable[int] = (),
) -> dict[str, Any]:
    """Return only analyzer inputs; OWL-owned projections are excluded by default."""
    selected_ids = {int(field_id) for field_id in governed_custom_field_ids}
    ignored_tags = {str(tag_id) for tag_id in ignored_tag_ids}
    custom_fields: dict[str, Any] = {}
    for entry in document.get("custom_fields") or []:
        if not isinstance(entry, dict):
            continue
        field_id = entry.get("field")
        if isinstance(field_id, bool):
            continue
        try:
            normalized_id = int(field_id)
        except (TypeError, ValueError):
            continue
        if normalized_id in selected_ids:
            custom_fields[str(normalized_id)] = entry.get("value")

    return {
        "title": document.get("title"),
        "correspondent": document.get("correspondent"),
        "document_type": document.get("document_type"),
        "created": document.get("created") or document.get("created_date"),
        "tags": sorted(
            str(tag) for tag in (document.get("tags") or []) if str(tag) not in ignored_tags
        ),
        "governed_custom_fields": custom_fields,
    }


def analysis_source_fingerprint(
    content_identity: str,
    metadata_fields: dict[str, Any] | None,
) -> str:
    """Combine persisted privacy-safe analysis inputs into one displayable identity."""
    return analysis_source_fingerprint_from_digests(
        content_identity,
        stable_digest(metadata_fields) if metadata_fields else None,
    )


def analysis_source_fingerprint_from_digests(
    content_identity: str,
    metadata_fingerprint: str | None,
) -> str:
    return stable_digest(
        {
            "content_identity": content_identity,
            "metadata_fingerprint": metadata_fingerprint,
        }
    )


def document_source_fingerprint(
    document: dict[str, Any],
    *,
    governed_custom_field_ids: Iterable[int] = (),
    ignored_tag_ids: Iterable[int] = (),
) -> tuple[str, str, dict[str, Any]]:
    content_identity = document_content_identity(document)
    metadata_fields = document_metadata_fields(
        document,
        governed_custom_field_ids=governed_custom_field_ids,
        ignored_tag_ids=ignored_tag_ids,
    )
    return (
        analysis_source_fingerprint(content_identity, metadata_fields),
        content_identity,
        metadata_fields,
    )
