"""Document type mapping API router — fetch Paperless types, save/load mapping."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from doc_intelligence_hub.api.routers import (
    load_statement_config_from_request,
    make_paperless_client,
)
from doc_intelligence_hub.modules.statements.database import Database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Keywords used for smart-suggesting which document types are "statement-like"
_SUGGEST_KEYWORDS = ("statement", "bill", "invoice", "eob", "explanation of benefits", "credit report")


def _is_suggested(name: str) -> bool:
    """Return True if the document type name matches a smart-suggest keyword."""
    lowered = name.lower()
    return any(keyword in lowered for keyword in _SUGGEST_KEYWORDS)


@router.get("/document-types")
async def list_document_types(request: Request) -> dict[str, Any]:
    """Fetch document types from the connected Paperless instance.

    Returns the list of available types with smart-suggest flags indicating
    which types are likely "statement-like" based on keyword matching.
    Also merges in current mapping state if one is saved.
    """
    client = make_paperless_client(request, timeout=15.0)
    http_client = client._get_client()

    # Paginate through /api/document_types/
    types: list[dict[str, Any]] = []
    page = 1
    while True:
        resp = await http_client.get("/api/document_types/", params={"page": page})
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("results", []):
            types.append({"id": int(item["id"]), "name": item["name"]})
        if not data.get("next"):
            break
        page += 1

    # Load saved mapping to merge enabled state
    config = load_statement_config_from_request(request)
    db = Database(config.runtime.database_path)
    db.connect()
    saved_mapping = {entry["id"]: entry["enabled"] for entry in db.load_document_type_mapping()}
    db.close()

    has_saved_mapping = len(saved_mapping) > 0

    result = []
    for doc_type in types:
        type_id = doc_type["id"]
        suggested = _is_suggested(doc_type["name"])
        # If a mapping exists in DB, use its enabled state; otherwise use suggested as default
        if has_saved_mapping:
            enabled = saved_mapping.get(type_id, False)
        else:
            enabled = suggested
        result.append({
            "id": type_id,
            "name": doc_type["name"],
            "suggested": suggested,
            "enabled": enabled,
        })

    result.sort(key=lambda t: t["name"].lower())
    return {"types": result, "has_saved_mapping": has_saved_mapping}


class DocumentTypeMappingEntry(BaseModel):
    id: int
    name: str
    enabled: bool = False


class DocumentTypeMappingUpdate(BaseModel):
    types: list[DocumentTypeMappingEntry]


@router.get("/document-type-mapping")
async def get_document_type_mapping(request: Request) -> dict[str, Any]:
    """Load the saved document type mapping from the database."""
    config = load_statement_config_from_request(request)
    db = Database(config.runtime.database_path)
    db.connect()
    mapping = db.load_document_type_mapping()
    db.close()
    return {"types": mapping, "configured": len(mapping) > 0}


@router.put("/document-type-mapping")
async def update_document_type_mapping(request: Request, body: DocumentTypeMappingUpdate) -> dict[str, Any]:
    """Save the document type mapping to the database.

    This mapping is used by the statement discovery pipeline to determine
    which document types are considered "statement-like".
    """
    config = load_statement_config_from_request(request)
    db = Database(config.runtime.database_path)
    db.connect()
    db.save_document_type_mapping([entry.model_dump() for entry in body.types])
    enabled_count = sum(1 for entry in body.types if entry.enabled)
    db.close()
    logger.info("Document type mapping saved: %d types, %d enabled", len(body.types), enabled_count)
    return {"status": "ok", "total": len(body.types), "enabled": enabled_count}
