from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from doc_intelligence_hub.api.routers import make_paperless_client
from doc_intelligence_hub.modules.eob_matching.classifier import classify_document
from doc_intelligence_hub.modules.eob_matching.extractor import extract_bill, extract_eob
from doc_intelligence_hub.modules.eob_matching.matcher import match_documents
from doc_intelligence_hub.modules.eob_matching.models import DocumentType

router = APIRouter(prefix="/api/eob", tags=["eob-matching"])


class ClassifyRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=500)
    tags: list[str] | None = None
    correspondent: str | None = None


class RunRequest(ClassifyRequest):
    verbose: bool = False


async def _load_documents(
    request: Request,
    *,
    limit: int,
    tags: list[str] | None,
    correspondent: str | None,
) -> list[dict[str, Any]]:
    client = make_paperless_client(request, timeout=30.0)
    documents = await client.list_documents(
        tags=tags,
        correspondent=correspondent,
        page_size=min(limit, 100),
    )
    documents = documents[:limit]

    async def with_content(document: dict[str, Any]) -> dict[str, Any]:
        hydrated = dict(document)
        if not hydrated.get("content"):
            hydrated["content"] = await client.get_document_content(int(hydrated["id"]))
        return hydrated

    return await asyncio.gather(*(with_content(document) for document in documents))


def _summarize_classifications(classifications: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(item["classification"]["type"] for item in classifications)
    return {document_type.value: counts.get(document_type.value, 0) for document_type in DocumentType}


@router.get("/check")
async def eob_check(request: Request) -> dict[str, Any]:
    client = make_paperless_client(request, timeout=10.0)
    health = await client.health_check()
    return {
        "status": "ok",
        "module": "eob-matching",
        "read_only": True,
        "paperless": health,
    }


@router.post("/classify")
async def classify_documents(request: Request, body: ClassifyRequest) -> dict[str, Any]:
    documents = await _load_documents(
        request,
        limit=body.limit,
        tags=body.tags,
        correspondent=body.correspondent,
    )

    classifications: list[dict[str, Any]] = []
    for document in documents:
        classification = classify_document(document.get("content", ""))
        classifications.append(
            {
                "document_id": document["id"],
                "title": document.get("title"),
                "classification": classification.model_dump(mode="json"),
            }
        )

    return {
        "status": "ok",
        "documents_scanned": len(classifications),
        "summary": _summarize_classifications(classifications),
        "results": classifications,
    }


@router.post("/run")
async def run_matching_pipeline(request: Request, body: RunRequest) -> dict[str, Any]:
    documents = await _load_documents(
        request,
        limit=body.limit,
        tags=body.tags,
        correspondent=body.correspondent,
    )

    classified_documents: list[dict[str, Any]] = []
    extracted_eobs = []
    extracted_bills = []

    for document in documents:
        content = document.get("content", "")
        classification = classify_document(content)
        item: dict[str, Any] = {
            "document_id": document["id"],
            "title": document.get("title"),
            "classification": classification.model_dump(mode="json"),
        }

        if classification.type == DocumentType.EOB:
            extracted = extract_eob(content, document_id=str(document["id"]))
            extracted_eobs.append(extracted)
            if body.verbose:
                item["extracted"] = extracted.model_dump(mode="json")
        elif classification.type == DocumentType.BILL:
            extracted = extract_bill(content, document_id=str(document["id"]))
            extracted_bills.append(extracted)
            if body.verbose:
                item["extracted"] = extracted.model_dump(mode="json")

        classified_documents.append(item)

    matches = match_documents(extracted_eobs, extracted_bills)
    matched_eob_ids = {match.eob_id for match in matches}
    matched_bill_ids = {match.bill_id for match in matches}

    result = {
        "status": "ok",
        "run_at": datetime.utcnow().isoformat(),
        "read_only": True,
        "documents_scanned": len(documents),
        "summary": {
            **_summarize_classifications(classified_documents),
            "matches": len(matches),
            "unmatched_eobs": len([item for item in extracted_eobs if item.document_id not in matched_eob_ids]),
            "unmatched_bills": len([item for item in extracted_bills if item.document_id not in matched_bill_ids]),
        },
        "classifications": classified_documents,
        "matches": [match.model_dump(mode="json") for match in matches],
        "extracted_eobs": [item.model_dump(mode="json") for item in extracted_eobs] if body.verbose else [],
        "extracted_bills": [item.model_dump(mode="json") for item in extracted_bills] if body.verbose else [],
    }
    request.app.state.last_eob_results = result
    return result


@router.get("/results")
async def get_last_results(request: Request) -> dict[str, Any]:
    return request.app.state.last_eob_results or {
        "status": "idle",
        "message": "No EOB matching run has been executed yet.",
    }
