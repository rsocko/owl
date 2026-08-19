"""Account Number Extraction API router.

Endpoints:
    POST   /api/extraction/account-numbers/extract  — Extract from a single document
    POST   /api/extraction/account-numbers/backfill  — Backfill across all documents in statement series
    GET    /api/extraction/account-numbers/patterns  — List supported extraction patterns
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from doc_intelligence_hub.api.routers import make_paperless_client
from doc_intelligence_hub.core.extractors.account_numbers import (
    ACCOUNT_PATTERNS,
    AccountExtractionDecision,
    evaluate_account_identifiers,
    extract_account_numbers,
    extract_from_document,
    write_account_to_paperless,
)
from doc_intelligence_hub.modules.triage.database import (
    create_extraction_correction,
    create_queue_item,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/extraction", tags=["extraction"])


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------


class ExtractRequest(BaseModel):
    document_id: int = Field(..., description="Paperless document ID to extract from")
    write_to_paperless: bool = Field(
        default=False, description="Write result to Paperless custom field"
    )


class BackfillRequest(BaseModel):
    document_ids: list[int] | None = Field(
        default=None, description="Specific document IDs (or all if omitted)"
    )
    write_to_paperless: bool = Field(
        default=False, description="Write results to Paperless custom fields"
    )
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
            {
                "name": name,
                "identifier_class": identifier_class.value,
                "confidence": confidence,
                "pattern": pattern.pattern,
            }
            for name, identifier_class, confidence, pattern in ACCOUNT_PATTERNS
        ],
        "count": len(ACCOUNT_PATTERNS),
    }


@router.post("/account-numbers/extract-text")
async def extract_from_text(body: ExtractTextRequest) -> dict[str, Any]:
    """Classify raw text without returning exact candidates."""
    matches = extract_account_numbers(body.text)
    return _serialize_decision(evaluate_account_identifiers(matches))


def _serialize_decision(decision: AccountExtractionDecision) -> dict[str, Any]:
    projection = decision.projection
    return {
        "candidate_count": decision.candidate_count,
        "identifier_class": projection.identifier_class.value if projection else None,
        "confidence": projection.confidence if projection else None,
        "account_identifier_display": projection.display_value if projection else None,
        "account_identifier_displays": list(decision.display_values),
        "requires_review": decision.requires_review,
        "reason": decision.reason,
    }


def _queue_account_review(document_id: int, decision: AccountExtractionDecision) -> None:
    create_queue_item(
        item_type="metadata_quality_review",
        source="account_identifier_extraction",
        target_type="document",
        target_id=str(document_id),
        reason=decision.reason or "Account Identifier requires review",
        metadata={
            "field_name": "account_identifier",
            "candidate_count": decision.candidate_count,
            "identifier_class": (
                decision.projection.identifier_class.value if decision.projection else None
            ),
            "confidence": decision.projection.confidence if decision.projection else None,
            "account_identifier_display": (
                decision.projection.display_value if decision.projection else None
            ),
            "account_identifier_displays": list(decision.display_values),
        },
        priority=70,
    )


@router.post("/account-numbers/extract")
async def extract_single(body: ExtractRequest, request: Request) -> dict[str, Any]:
    """Extract account numbers from a single Paperless document."""
    client = make_paperless_client(request)
    result = await extract_from_document(body.document_id, client)

    if not result.success:
        raise HTTPException(status_code=422, detail=result.error or "Extraction failed")

    decision = evaluate_account_identifiers(result.pattern_matches)
    written = False

    if body.write_to_paperless and decision.requires_review:
        _queue_account_review(body.document_id, decision)
    elif body.write_to_paperless and decision.candidate and decision.projection:
        written = await write_account_to_paperless(
            body.document_id,
            decision.candidate["value"],
            client,
            identifier_class=decision.projection.identifier_class,
            confidence=decision.projection.confidence,
        )
        if written:
            create_extraction_correction(
                document_id=body.document_id,
                field_name="account_identifier",
                corrected_value=decision.projection.display_value,
                confidence=decision.projection.confidence * 100,
                correction_type="added",
                notes=f"Auto-extracted class={decision.projection.identifier_class.value}",
            )

    return {
        "document_id": body.document_id,
        **_serialize_decision(decision),
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
                    loop = asyncio.get_running_loop()
                    data = await loop.run_in_executor(
                        None, get_fn, f"/api/documents/?page_size={body.limit}&ordering=-id"
                    )

                if isinstance(data, dict) and "results" in data:
                    doc_ids = [d["id"] for d in data["results"][: body.limit]]
        except Exception as exc:
            logger.warning("Could not fetch document list for backfill: %s", exc)
            raise HTTPException(
                status_code=503, detail="Could not fetch document list from Paperless"
            ) from exc

    results: list[dict[str, Any]] = []
    extracted_count = 0
    written_count = 0

    for doc_id in doc_ids[: body.limit]:
        try:
            result = await extract_from_document(doc_id, client)
            decision = (
                evaluate_account_identifiers(result.pattern_matches)
                if result.success
                else AccountExtractionDecision(None, None, 0, False, result.error)
            )

            written = False
            if body.write_to_paperless and decision.requires_review:
                _queue_account_review(doc_id, decision)
            elif body.write_to_paperless and decision.candidate and decision.projection:
                written = await write_account_to_paperless(
                    doc_id,
                    decision.candidate["value"],
                    client,
                    identifier_class=decision.projection.identifier_class,
                    confidence=decision.projection.confidence,
                )
                if written:
                    written_count += 1
                    create_extraction_correction(
                        document_id=doc_id,
                        field_name="account_identifier",
                        corrected_value=decision.projection.display_value,
                        confidence=decision.projection.confidence * 100,
                        correction_type="added",
                        notes=f"Backfill class={decision.projection.identifier_class.value}",
                    )

            if result.success and decision.candidate_count:
                extracted_count += 1

            results.append(
                {
                    "document_id": doc_id,
                    "success": result.success,
                    **_serialize_decision(decision),
                    "written": written,
                    "error": result.error,
                }
            )
        except Exception as exc:
            logger.warning("Extraction failed for doc %d during backfill: %s", doc_id, exc)
            results.append(
                {
                    "document_id": doc_id,
                    "success": False,
                    "candidate_count": 0,
                    "identifier_class": None,
                    "confidence": None,
                    "account_identifier_display": None,
                    "account_identifier_displays": [],
                    "requires_review": False,
                    "reason": "Extraction failed",
                    "written": False,
                    "error": "Extraction failed for this document",
                }
            )

    return {
        "processed": len(results),
        "extracted": extracted_count,
        "written": written_count,
        "results": results,
    }
