from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from doc_intelligence_hub.api.routers import make_paperless_client
from doc_intelligence_hub.modules.eob_matching.classifier import classify_document
from doc_intelligence_hub.modules.eob_matching.database import (
    BillRecord,
    EOBRecord,
    MatchEvent,
    MatchRecord,
    MatchingRun,
    add_match_event,
    get_match_events,
    get_session as get_db_session,
    init_db,
)
from doc_intelligence_hub.modules.eob_matching.enricher import EOBEnricher
from doc_intelligence_hub.modules.eob_matching.extractor import extract_bill, extract_eob
from doc_intelligence_hub.modules.eob_matching.llm_extractor import extract_bill_llm, extract_eob_llm
from doc_intelligence_hub.modules.eob_matching.matcher import match_documents
from doc_intelligence_hub.modules.eob_matching.models import DocumentType

router = APIRouter(prefix="/api/eob", tags=["eob-matching"])


# ------------------------------------------------------------------
# Request / response models
# ------------------------------------------------------------------


class ClassifyRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=500)
    tags: list[str] | None = None
    correspondent: str | None = None
    document_type: str | None = Field(default=None, description="Paperless document type filter")
    created_after: str | None = Field(
        default=None, description="Only docs created on/after this date (YYYY-MM-DD)"
    )
    created_before: str | None = Field(
        default=None, description="Only docs created on/before this date (YYYY-MM-DD)"
    )


class RunRequest(ClassifyRequest):
    verbose: bool = False
    write_to_paperless: bool | None = None  # None = inherit from hub settings


class MatchUpdateRequest(BaseModel):
    status: str = Field(..., pattern=r"^(confirmed|rejected|candidate)$")


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _is_write_enabled(request: Request) -> bool:
    """Check if writes to Paperless are enabled via hub settings."""
    return getattr(request.app.state.hub_settings, "write_to_paperless", False)


async def _load_documents(
    request: Request,
    *,
    limit: int,
    tags: list[str] | None,
    correspondent: str | None,
    document_type: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
) -> list[dict[str, Any]]:
    client = make_paperless_client(request, timeout=30.0)
    documents = await client.list_documents(
        tags=tags,
        correspondent=correspondent,
        document_type=document_type,
        created_after=created_after,
        created_before=created_before,
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


def _serialize_run(run: MatchingRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "documents_scanned": run.documents_scanned,
        "eobs_found": run.eobs_found,
        "bills_found": run.bills_found,
        "matches_found": run.matches_found,
        "high_confidence": run.high_confidence,
        "medium_confidence": run.medium_confidence,
        "low_confidence": run.low_confidence,
        "tags_filter": run.tags_filter,
        "correspondent_filter": run.correspondent_filter,
    }


def _serialize_match(m: MatchRecord, paperless_url: str = "") -> dict[str, Any]:
    base_url = paperless_url.rstrip("/") if paperless_url else ""
    return {
        "id": m.id,
        "run_id": m.run_id,
        "eob_document_id": m.eob_document_id,
        "bill_document_id": m.bill_document_id,
        "score": m.score,
        "confidence": m.confidence,
        "breakdown": {
            "date": m.breakdown_date,
            "provider": m.breakdown_provider,
            "patient": m.breakdown_patient,
            "amount": m.breakdown_amount,
            "procedures": m.breakdown_procedures,
        },
        "status": m.status,
        "linked_in_paperless": bool(m.linked_in_paperless),
        "eob_preview_url": f"{base_url}/documents/{m.eob_document_id}/details" if base_url else None,
        "bill_preview_url": f"{base_url}/documents/{m.bill_document_id}/details" if base_url else None,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "confirmed_at": m.confirmed_at.isoformat() if m.confirmed_at else None,
    }


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/check")
async def eob_check(request: Request) -> dict[str, Any]:
    """Health check — Paperless connectivity and module status."""
    client = make_paperless_client(request, timeout=10.0)
    health = await client.health_check()
    write_enabled = _is_write_enabled(request)
    return {
        "status": "ok",
        "module": "eob-matching",
        "read_only": not write_enabled,
        "write_to_paperless": write_enabled,
        "paperless": health,
    }


@router.post("/classify")
async def classify_documents(request: Request, body: ClassifyRequest) -> dict[str, Any]:
    """Classify documents as EOB, Bill, or Unknown (always read-only)."""
    documents = await _load_documents(
        request,
        limit=body.limit,
        tags=body.tags,
        correspondent=body.correspondent,
        document_type=body.document_type,
        created_after=body.created_after,
        created_before=body.created_before,
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
    """Run the full pipeline: fetch → classify → extract → match → persist.

    Results are always stored to SQLite. If write_to_paperless is enabled
    (via request body or hub settings), match metadata is also written
    back to Paperless custom fields.
    """
    init_db()
    db = get_db_session()

    # Determine write mode
    write_enabled = body.write_to_paperless if body.write_to_paperless is not None else _is_write_enabled(request)

    # Create run record
    run_record = MatchingRun(
        tags_filter=",".join(body.tags) if body.tags else None,
        correspondent_filter=body.correspondent,
    )
    db.add(run_record)
    db.commit()
    db.refresh(run_record)

    try:
        # Step 1: Fetch documents
        documents = await _load_documents(
            request,
            limit=body.limit,
            tags=body.tags,
            correspondent=body.correspondent,
            document_type=body.document_type,
            created_after=body.created_after,
            created_before=body.created_before,
        )

        # Step 2: Classify + Extract
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
                extracted = await extract_eob_llm(content, document_id=str(document["id"]))
                extracted_eobs.append(extracted)
                if body.verbose:
                    item["extracted"] = extracted.model_dump(mode="json")

                # Persist EOB record
                db.add(EOBRecord(
                    document_id=document["id"],
                    run_id=run_record.id,
                    title=document.get("title"),
                    classification_score=classification.confidence_score,
                    insurance_company=extracted.insurance_company,
                    policy_number=extracted.policy_number,
                    patient_name=extracted.patient_name,
                    claim_number=extracted.claim_number,
                    date_of_service=str(extracted.date_of_service) if extracted.date_of_service else None,
                    provider_name=extracted.provider_name,
                    total_billed=extracted.total_billed,
                    total_allowed=extracted.total_allowed,
                    total_plan_pays=extracted.total_plan_pays,
                    total_patient_responsibility=extracted.total_patient_responsibility,
                    services_json=json.dumps(
                        [s.model_dump(mode="json") for s in extracted.services]
                    ) if extracted.services else None,
                ))

            elif classification.type == DocumentType.BILL:
                extracted = await extract_bill_llm(content, document_id=str(document["id"]))
                extracted_bills.append(extracted)
                if body.verbose:
                    item["extracted"] = extracted.model_dump(mode="json")

                # Persist Bill record
                db.add(BillRecord(
                    document_id=document["id"],
                    run_id=run_record.id,
                    title=document.get("title"),
                    classification_score=classification.confidence_score,
                    provider_name=extracted.provider_name,
                    patient_name=extracted.patient_name,
                    invoice_number=extracted.invoice_number,
                    date_of_service=str(extracted.date_of_service) if extracted.date_of_service else None,
                    due_date=str(extracted.due_date) if extracted.due_date else None,
                    total_amount=extracted.total_amount,
                    balance_due=extracted.balance_due,
                    payment_status=extracted.payment_status,
                    services_json=json.dumps(
                        [s.model_dump(mode="json") for s in extracted.services]
                    ) if extracted.services else None,
                ))

            classified_documents.append(item)

        db.commit()

        # Step 3: Match
        matches = match_documents(extracted_eobs, extracted_bills)
        matched_eob_ids = {match.eob_id for match in matches}
        matched_bill_ids = {match.bill_id for match in matches}

        high = sum(1 for m in matches if m.confidence.value == "HIGH")
        medium = sum(1 for m in matches if m.confidence.value == "MEDIUM")
        low = sum(1 for m in matches if m.confidence.value == "LOW")

        # Persist match records
        for match in matches:
            match_rec = MatchRecord(
                run_id=run_record.id,
                eob_document_id=int(match.eob_id),
                bill_document_id=int(match.bill_id),
                score=match.score,
                confidence=match.confidence.value,
                breakdown_date=match.breakdown.date,
                breakdown_provider=match.breakdown.provider,
                breakdown_patient=match.breakdown.patient,
                breakdown_amount=match.breakdown.amount,
                breakdown_procedures=match.breakdown.procedures,
                status="candidate",
            )
            db.add(match_rec)
            db.flush()  # populate match_rec.id
            db.add(MatchEvent(
                match_id=match_rec.id,
                event_type="auto_matched",
                actor="system",
                detail=f"Auto-matched with {match.confidence.value} confidence ({match.score:.0f}%)",
            ))
            if match.confidence.value in ("LOW", "MEDIUM"):
                db.add(MatchEvent(
                    match_id=match_rec.id,
                    event_type="flagged",
                    actor="system",
                    detail=f"Flagged for review — {match.confidence.value.lower()} confidence",
                ))
        db.commit()

        # Step 4: Write to Paperless (if enabled)
        linked_count = 0
        if write_enabled and matches:
            client = make_paperless_client(request, timeout=30.0)
            enricher = EOBEnricher(client)
            eob_lookup = {e.document_id: e for e in extracted_eobs}
            for match in matches:
                try:
                    eob_data = eob_lookup.get(match.eob_id)
                    patient_resp = eob_data.total_patient_responsibility if eob_data else None
                    await enricher.link_match(
                        eob_document_id=int(match.eob_id),
                        bill_document_id=int(match.bill_id),
                        score=match.score,
                        confidence=match.confidence.value,
                        patient_responsibility=patient_resp,
                    )
                    linked_count += 1
                except Exception:
                    pass  # Non-fatal: match is still persisted in DB

            # Update linked status in DB
            if linked_count > 0:
                for match_rec in db.query(MatchRecord).filter_by(run_id=run_record.id).all():
                    match_rec.linked_in_paperless = 1
                db.commit()

        # Finalize run record
        run_record.documents_scanned = len(documents)
        run_record.eobs_found = len(extracted_eobs)
        run_record.bills_found = len(extracted_bills)
        run_record.matches_found = len(matches)
        run_record.high_confidence = high
        run_record.medium_confidence = medium
        run_record.low_confidence = low
        run_record.finished_at = datetime.now(UTC)
        db.commit()

        # Emit unified alerts for unmatched EOBs and low-confidence matches
        try:
            from doc_intelligence_hub.core.alerts import emit_eob_alerts

            unmatched = [
                {"document_id": int(e.document_id), "provider_name": e.provider_name}
                for e in extracted_eobs
                if e.document_id not in matched_eob_ids
            ]
            low_conf = [
                {
                    "eob_document_id": int(m.eob_id),
                    "bill_document_id": int(m.bill_id),
                    "score": m.score,
                    "confidence": m.confidence.value,
                }
                for m in matches
                if m.confidence.value == "LOW"
            ]
            emit_eob_alerts(unmatched_eobs=unmatched, low_confidence_matches=low_conf)
        except Exception:
            pass  # Alert emission is best-effort

        paperless_url = getattr(request.app.state.hub_settings, "paperless_url", "") or ""

        return {
            "status": "ok",
            "run_id": run_record.id,
            "run_at": run_record.started_at.isoformat() if run_record.started_at else None,
            "write_to_paperless": write_enabled,
            "documents_scanned": len(documents),
            "summary": {
                **_summarize_classifications(classified_documents),
                "matches": len(matches),
                "high_confidence": high,
                "medium_confidence": medium,
                "low_confidence": low,
                "linked_in_paperless": linked_count,
                "unmatched_eobs": len([e for e in extracted_eobs if e.document_id not in matched_eob_ids]),
                "unmatched_bills": len([b for b in extracted_bills if b.document_id not in matched_bill_ids]),
            },
            "classifications": classified_documents,
            "matches": [match.model_dump(mode="json") for match in matches],
            "extracted_eobs": [e.model_dump(mode="json") for e in extracted_eobs] if body.verbose else [],
            "extracted_bills": [b.model_dump(mode="json") for b in extracted_bills] if body.verbose else [],
        }
    finally:
        db.close()


@router.get("/results")
async def get_last_results(request: Request) -> dict[str, Any]:
    """Get the most recent pipeline run results from the database."""
    init_db()
    db = get_db_session()
    try:
        run = (
            db.query(MatchingRun)
            .order_by(MatchingRun.started_at.desc())
            .first()
        )
        if not run:
            return {"status": "idle", "message": "No EOB matching run has been executed yet."}

        paperless_url = getattr(request.app.state.hub_settings, "paperless_url", "") or ""
        match_records = (
            db.query(MatchRecord)
            .filter_by(run_id=run.id)
            .order_by(MatchRecord.score.desc())
            .all()
        )

        return {
            "status": "ok",
            "run": _serialize_run(run),
            "matches": [_serialize_match(m, paperless_url) for m in match_records],
        }
    finally:
        db.close()


@router.get("/runs")
async def list_runs(
    request: Request,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """List recent pipeline runs with summary stats."""
    init_db()
    db = get_db_session()
    try:
        query = db.query(MatchingRun).order_by(MatchingRun.started_at.desc())
        total = query.count()
        runs = query.offset(offset).limit(limit).all()
        return {
            "runs": [_serialize_run(r) for r in runs],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        db.close()


@router.get("/matches")
async def list_matches(
    request: Request,
    status: str | None = None,
    run_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List match records with optional filtering by status or run."""
    init_db()
    db = get_db_session()
    try:
        query = db.query(MatchRecord).order_by(MatchRecord.score.desc())
        if status:
            query = query.filter_by(status=status)
        if run_id is not None:
            query = query.filter_by(run_id=run_id)
        total = query.count()
        matches = query.offset(offset).limit(limit).all()

        paperless_url = getattr(request.app.state.hub_settings, "paperless_url", "") or ""
        return {
            "matches": [_serialize_match(m, paperless_url) for m in matches],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        db.close()


@router.patch("/matches/{match_id}")
async def update_match(
    request: Request,
    match_id: int,
    body: MatchUpdateRequest,
) -> dict[str, Any]:
    """Update a match's status (confirm, reject, or reset to candidate).

    Confirming a HIGH-confidence match with write_to_paperless enabled
    will also write the link back to Paperless.
    """
    init_db()
    db = get_db_session()
    try:
        match = db.query(MatchRecord).filter_by(id=match_id).first()
        if not match:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"Match {match_id} not found")

        match.status = body.status
        event_type_map = {"confirmed": "confirmed", "rejected": "rejected", "candidate": "reset"}
        db.add(MatchEvent(
            match_id=match_id,
            event_type=event_type_map.get(body.status, body.status),
            actor="user",
            detail=f"Manually {body.status} by reviewer",
        ))
        if body.status == "confirmed":
            match.confirmed_at = datetime.now(UTC)

            # Write to Paperless if enabled and not already linked
            if _is_write_enabled(request) and not match.linked_in_paperless:
                try:
                    client = make_paperless_client(request, timeout=30.0)
                    enricher = EOBEnricher(client)
                    await enricher.link_match(
                        eob_document_id=match.eob_document_id,
                        bill_document_id=match.bill_document_id,
                        score=match.score,
                        confidence=match.confidence,
                    )
                    match.linked_in_paperless = 1
                except Exception:
                    pass  # Non-fatal
        elif body.status == "candidate":
            match.confirmed_at = None

        db.commit()

        paperless_url = getattr(request.app.state.hub_settings, "paperless_url", "") or ""
        return _serialize_match(match, paperless_url)
    finally:
        db.close()


@router.get("/matches/{match_id}/history")
async def match_history(match_id: int) -> dict[str, Any]:
    """Return the audit-trail timeline for a single match."""
    init_db()
    db = get_db_session()
    try:
        events = get_match_events(db, match_id)
        return {
            "match_id": match_id,
            "events": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "actor": e.actor,
                    "detail": e.detail,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in events
            ],
        }
    finally:
        db.close()


class PurgeStaleRequest(BaseModel):
    dry_run: bool = Field(default=False, description="If true, return stale records without deleting")


@router.post("/purge-stale")
async def purge_stale_records(body: PurgeStaleRequest | None = None) -> dict[str, Any]:
    """Purge stale EOB records with garbage extracted data.

    Identifies records where the provider_name field contains document
    boilerplate text that was incorrectly extracted before validation was added.

    Pass dry_run=true to preview which records would be deleted.
    """
    from doc_intelligence_hub.modules.eob_matching.purge import (
        find_stale_eobs,
        purge_stale_eobs,
    )

    init_db()
    db = get_db_session()
    dry_run = body.dry_run if body else False

    try:
        if dry_run:
            stale = find_stale_eobs(db)
            return {
                "status": "dry_run",
                "stale_count": len(stale),
                "records": [
                    {
                        "id": r.id,
                        "document_id": r.document_id,
                        "provider_name": r.provider_name,
                        "total_billed": r.total_billed,
                        "total_allowed": r.total_allowed,
                        "total_plan_pays": r.total_plan_pays,
                        "total_patient_responsibility": r.total_patient_responsibility,
                    }
                    for r in stale
                ],
            }
        else:
            result = purge_stale_eobs(db)
            return {
                "status": "ok",
                "purged_count": result.purged_count,
                "orphaned_matches_removed": result.orphaned_matches_removed,
                "document_ids": result.document_ids,
            }
    finally:
        db.close()


# ------------------------------------------------------------------
# Benchmark endpoint
# ------------------------------------------------------------------


class BenchmarkRequest(BaseModel):
    models: list[str] = Field(
        ...,
        min_length=1,
        description="List of model names to benchmark (e.g. ['phi3:mini', 'gpt-4o-mini'])",
    )
    limit: int = Field(default=5, ge=1, le=50, description="Number of documents to test per model")
    tags: list[str] | None = Field(default=None, description="Paperless tag filter")
    document_type: str | None = Field(default=None, description="Paperless document type filter")
    created_after: str | None = Field(
        default=None, description="Only docs created on/after this date (YYYY-MM-DD)"
    )
    created_before: str | None = Field(
        default=None, description="Only docs created on/before this date (YYYY-MM-DD)"
    )
    bifrost_url: str = Field(
        default="https://service-001.example.invalid/openai/v1",
        description="Bifrost gateway URL override",
    )


@router.post("/benchmark")
async def run_model_benchmark(request: Request, body: BenchmarkRequest) -> dict[str, Any]:
    """Benchmark LLM models on EOB extraction for speed and accuracy.

    Compares multiple models by running them against real EOB documents
    from Paperless. Returns timing, success rate, confidence, and cost data.
    """
    import os

    from doc_intelligence_hub.core.llm import reset_llm_client
    from doc_intelligence_hub.modules.eob_matching.benchmark import (
        benchmark_to_json,
        fetch_eob_documents,
        run_benchmark,
    )

    # Configure Bifrost URL
    os.environ["LLM_BASE_URL"] = body.bifrost_url
    reset_llm_client()

    # Fetch documents from Paperless
    settings = request.app.state.hub_settings

    paperless_url = settings.paperless_url
    paperless_token = settings.resolved_paperless_token

    if not paperless_url or not paperless_token:
        raise_api_error(
            503,
            "paperless_not_configured",
            "Paperless connection settings required for benchmark.",
        )

    documents = await fetch_eob_documents(
        paperless_url=paperless_url,
        paperless_token=paperless_token,
        limit=body.limit,
        tags=body.tags,
        document_type=body.document_type,
        created_after=body.created_after,
        created_before=body.created_before,
    )

    if not documents:
        return {
            "status": "no_documents",
            "message": "No documents found matching the specified criteria.",
            "models": body.models,
            "results": [],
        }

    # Run benchmark
    summaries = await run_benchmark(documents, body.models)
    results = benchmark_to_json(summaries)

    return {
        "status": "ok",
        "documents_tested": len(documents),
        "models_tested": len(body.models),
        "results": results,
    }
