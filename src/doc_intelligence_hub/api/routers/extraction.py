"""Account Number Extraction API router.

Endpoints:
    POST   /api/extraction/account-numbers/extract  — Extract from a single document
    POST   /api/extraction/account-numbers/backfill  — Backfill across all documents in statement series
    GET    /api/extraction/account-numbers/patterns  — List supported extraction patterns
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from doc_intelligence_hub.api.routers import make_paperless_client
from doc_intelligence_hub.core.extractors.account_numbers import (
    ACCOUNT_PATTERNS,
    ExtractionResult,
    extract_account_numbers,
    extract_from_document,
    pick_best_account_identifier,
    write_account_to_paperless,
)
from doc_intelligence_hub.modules.triage.database import (
    create_extraction_correction,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/extraction", tags=["extraction"])


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------


class ExtractRequest(BaseModel):
    document_id: int = Field(..., description="Paperless document ID to extract from")
    write_to_paperless: bool = Field(default=False, description="Write result to Paperless custom field")


class BackfillRequest(BaseModel):
    document_ids: list[int] | None = Field(default=None, description="Specific document IDs (or all if omitted)")
    write_to_paperless: bool = Field(default=False, description="Write results to Paperless custom fields")
    limit: int = Field(default=100, ge=1, le=1000, description="Max documents to process")


class ExtractTextRequest(BaseModel):
    text: str = Field(..., description="Raw text to extract account numbers from")


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/account-numbers/patterns")
async def list_patterns() -> dict[str, Any]:
    """List supported account number extraction patterns."""
    return {
        "patterns": [
            {"name": name, "pattern": pattern.pattern}
            for name, pattern in ACCOUNT_PATTERNS
        ],
        "count": len(ACCOUNT_PATTERNS),
    }


@router.post("/account-numbers/extract-text")
async def extract_from_text(body: ExtractTextRequest) -> dict[str, Any]:
    """Extract account numbers from raw text (no Paperless interaction)."""
    matches = extract_account_numbers(body.text)
    best = pick_best_account_identifier(matches)
    return {
        "matches": matches,
        "best_identifier": best,
        "count": len(matches),
    }


@router.post("/account-numbers/extract")
async def extract_single(body: ExtractRequest, request: Request) -> dict[str, Any]:
    """Extract account numbers from a single Paperless document."""
    client = make_paperless_client(request)
    result = await extract_from_document(body.document_id, client)

    if not result.success:
        raise HTTPException(status_code=422, detail=result.error or "Extraction failed")

    best = pick_best_account_identifier(result.pattern_matches)
    written = False

    if body.write_to_paperless and best:
        written = await write_account_to_paperless(body.document_id, best, client)
        if written:
            # Record as extraction correction
            create_extraction_correction(
                document_id=body.document_id,
                field_name="account_identifier",
                corrected_value=best,
                correction_type="added",
                notes="Auto-extracted by account number pipeline",
            )

    return {
        "document_id": body.document_id,
        "matches": result.pattern_matches,
        "best_identifier": best,
        "account_numbers": result.account_numbers,
        "written_to_paperless": written,
        "text_length": result.raw_text_length,
    }


@router.post("/account-numbers/backfill")
async def backfill(body: BackfillRequest, request: Request) -> dict[str, Any]:
    """Backfill account numbers across multiple documents.

    If document_ids is not provided, fetches recent documents from Paperless
    that don't have an account identifier set.
    """
    client = make_paperless_client(request)

    doc_ids = body.document_ids or []
    if not doc_ids:
        # Fetch documents that may need account extraction
        try:
            get_fn = getattr(client, "get", None)
            if get_fn:
                import asyncio
                import inspect

                if inspect.iscoroutinefunction(get_fn):
                    data = await get_fn(f"/api/documents/?page_size={body.limit}&ordering=-id")
                else:
                    loop = asyncio.get_event_loop()
                    data = await loop.run_in_executor(None, get_fn, f"/api/documents/?page_size={body.limit}&ordering=-id")

                if isinstance(data, dict) and "results" in data:
                    doc_ids = [d["id"] for d in data["results"][:body.limit]]
        except Exception as exc:
            logger.warning("Could not fetch document list for backfill: %s", exc)
            raise HTTPException(status_code=503, detail=f"Could not fetch documents: {exc}")

    results: list[dict[str, Any]] = []
    extracted_count = 0
    written_count = 0

    for doc_id in doc_ids[:body.limit]:
        try:
            result = await extract_from_document(doc_id, client)
            best = pick_best_account_identifier(result.pattern_matches) if result.success else None

            written = False
            if body.write_to_paperless and best:
                written = await write_account_to_paperless(doc_id, best, client)
                if written:
                    written_count += 1
                    create_extraction_correction(
                        document_id=doc_id,
                        field_name="account_identifier",
                        corrected_value=best,
                        correction_type="added",
                        notes="Backfill by account number pipeline",
                    )

            if result.success and result.account_numbers:
                extracted_count += 1

            results.append({
                "document_id": doc_id,
                "success": result.success,
                "account_numbers": result.account_numbers if result.success else [],
                "best_identifier": best,
                "written": written,
                "error": result.error,
            })
        except Exception as exc:
            results.append({
                "document_id": doc_id,
                "success": False,
                "account_numbers": [],
                "best_identifier": None,
                "written": False,
                "error": str(exc),
            })

    return {
        "processed": len(results),
        "extracted": extracted_count,
        "written": written_count,
        "results": results,
    }
