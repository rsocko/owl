from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
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
    notes: str | None = Field(default=None, description="Optional reviewer notes for this match decision")


class MatchConfirmRequest(BaseModel):
    notes: str | None = Field(default=None, description="Optional reviewer notes")
    triage_item_id: str | None = Field(default=None, description="If provided, also resolve this triage queue item")


class MatchRejectRequest(BaseModel):
    reason: str | None = Field(default=None, description="Rejection reason")
    reassign_to: int | None = Field(default=None, description="Alternative match ID to reassign to")
    triage_item_id: str | None = Field(default=None, description="If provided, also resolve this triage queue item")


class ManualMatchRequest(BaseModel):
    eob_doc_id: int = Field(..., description="EOB document ID")
    bill_doc_id: int = Field(..., description="Bill document ID")
    notes: str | None = Field(default=None, description="Optional notes")


class BulkUpdateRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=500, description="EOBRecord IDs to update")
    action: str = Field(..., pattern=r"^(mark_orphan|mark_paid)$")


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


def _serialize_eob_details(eob: EOBRecord | None) -> dict[str, Any] | None:
    if eob is None:
        return None
    return {
        "provider_name": eob.provider_name,
        "patient_name": eob.patient_name,
        "insurance_company": eob.insurance_company,
        "date_of_service": eob.date_of_service,
        "total_billed": eob.total_billed,
        "total_patient_responsibility": eob.total_patient_responsibility,
        "claim_number": eob.claim_number,
    }


def _serialize_eob_full(eob: EOBRecord | None) -> dict[str, Any] | None:
    """Full EOB record serialization for detail endpoints."""
    if eob is None:
        return None
    return {
        "id": eob.id,
        "document_id": eob.document_id,
        "run_id": eob.run_id,
        "title": eob.title,
        "classification_score": eob.classification_score,
        "insurance_company": eob.insurance_company,
        "policy_number": eob.policy_number,
        "patient_name": eob.patient_name,
        "claim_number": eob.claim_number,
        "date_of_service": eob.date_of_service,
        "provider_name": eob.provider_name,
        "total_billed": eob.total_billed,
        "total_allowed": eob.total_allowed,
        "total_plan_pays": eob.total_plan_pays,
        "total_patient_responsibility": eob.total_patient_responsibility,
        "services_json": eob.services_json,
        "created_at": eob.created_at.isoformat() if eob.created_at else None,
    }


def _serialize_bill_details(bill: BillRecord | None) -> dict[str, Any] | None:
    if bill is None:
        return None
    return {
        "provider_name": bill.provider_name,
        "patient_name": bill.patient_name,
        "date_of_service": bill.date_of_service,
        "total_amount": bill.total_amount,
        "balance_due": bill.balance_due,
        "invoice_number": bill.invoice_number,
    }


def _serialize_bill_full(bill: BillRecord | None) -> dict[str, Any] | None:
    """Full Bill record serialization for detail endpoints."""
    if bill is None:
        return None
    return {
        "id": bill.id,
        "document_id": bill.document_id,
        "run_id": bill.run_id,
        "title": bill.title,
        "classification_score": bill.classification_score,
        "provider_name": bill.provider_name,
        "patient_name": bill.patient_name,
        "invoice_number": bill.invoice_number,
        "date_of_service": bill.date_of_service,
        "due_date": bill.due_date,
        "total_amount": bill.total_amount,
        "balance_due": bill.balance_due,
        "payment_status": bill.payment_status,
        "services_json": bill.services_json,
        "created_at": bill.created_at.isoformat() if bill.created_at else None,
    }


def _serialize_match(
    m: MatchRecord,
    paperless_url: str = "",
    eob: EOBRecord | None = None,
    bill: BillRecord | None = None,
) -> dict[str, Any]:
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
        "notes": m.notes,
        "user_status": m.user_status or "unreviewed",
        "reviewed_at": m.reviewed_at.isoformat() if m.reviewed_at else None,
        "user_notes": m.user_notes,
        "eob_details": _serialize_eob_details(eob),
        "bill_details": _serialize_bill_details(bill),
    }


def _batch_load_records(
    db,
    match_records: list[MatchRecord],
    run_id: int | None = None,
) -> tuple[dict[int, EOBRecord], dict[int, BillRecord]]:
    """Batch-load EOB and Bill records for a list of matches."""
    eob_doc_ids = {m.eob_document_id for m in match_records}
    bill_doc_ids = {m.bill_document_id for m in match_records}

    eob_query = db.query(EOBRecord).filter(EOBRecord.document_id.in_(eob_doc_ids))
    bill_query = db.query(BillRecord).filter(BillRecord.document_id.in_(bill_doc_ids))
    if run_id is not None:
        eob_query = eob_query.filter_by(run_id=run_id)
        bill_query = bill_query.filter_by(run_id=run_id)

    # Keep only the latest record per document_id
    eob_map: dict[int, EOBRecord] = {}
    for eob in eob_query.order_by(EOBRecord.id.asc()).all():
        eob_map[eob.document_id] = eob

    bill_map: dict[int, BillRecord] = {}
    for bill in bill_query.order_by(BillRecord.id.asc()).all():
        bill_map[bill.document_id] = bill

    return eob_map, bill_map


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

        eob_map, bill_map = _batch_load_records(db, match_records, run_id=run.id)

        return {
            "status": "ok",
            "run": _serialize_run(run),
            "matches": [
                _serialize_match(
                    m,
                    paperless_url,
                    eob=eob_map.get(m.eob_document_id),
                    bill=bill_map.get(m.bill_document_id),
                )
                for m in match_records
            ],
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

        eob_map, bill_map = _batch_load_records(db, matches, run_id=run_id)

        return {
            "matches": [
                _serialize_match(
                    m,
                    paperless_url,
                    eob=eob_map.get(m.eob_document_id),
                    bill=bill_map.get(m.bill_document_id),
                )
                for m in matches
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    finally:
        db.close()


@router.get("/matches/{match_id}")
async def get_match(request: Request, match_id: int) -> dict[str, Any]:
    """Get a single match record by ID."""
    init_db()
    db = get_db_session()
    try:
        match = db.query(MatchRecord).filter_by(id=match_id).first()
        if not match:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail=f"Match {match_id} not found")

        paperless_url = getattr(request.app.state.hub_settings, "paperless_url", "") or ""
        return _serialize_match(match, paperless_url)
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
        if body.notes is not None:
            match.notes = body.notes
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
        eob = (
            db.query(EOBRecord)
            .filter_by(document_id=match.eob_document_id)
            .order_by(EOBRecord.id.desc())
            .first()
        )
        bill = (
            db.query(BillRecord)
            .filter_by(document_id=match.bill_document_id)
            .order_by(BillRecord.id.desc())
            .first()
        )
        return _serialize_match(match, paperless_url, eob=eob, bill=bill)
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


@router.get("/records/{document_id}")
async def get_record_detail(document_id: int) -> dict[str, Any]:
    """Return the full extraction details for a specific document (EOB or Bill).

    Looks up in both eob_records and bill_records by document_id and returns
    the most recent record (latest run_id).
    """
    from fastapi import HTTPException

    init_db()
    db = get_db_session()
    try:
        eob = (
            db.query(EOBRecord)
            .filter_by(document_id=document_id)
            .order_by(EOBRecord.id.desc())
            .first()
        )
        bill = (
            db.query(BillRecord)
            .filter_by(document_id=document_id)
            .order_by(BillRecord.id.desc())
            .first()
        )
        if not eob and not bill:
            raise HTTPException(status_code=404, detail=f"No records found for document {document_id}")

        result: dict[str, Any] = {"document_id": document_id}
        if eob:
            result["type"] = "eob"
            result["eob"] = _serialize_eob_full(eob)
        if bill:
            result["type"] = "bill" if not eob else "both"
            result["bill"] = _serialize_bill_full(bill)
        return result
    finally:
        db.close()


@router.get("/matches/{match_id}/detail")
async def get_match_detail(request: Request, match_id: int) -> dict[str, Any]:
    """Return both sides of a match with their full extracted fields.

    Given a match_id, loads the MatchRecord, then loads the corresponding
    EOBRecord and BillRecord, and returns everything together.
    """
    from fastapi import HTTPException

    init_db()
    db = get_db_session()
    try:
        match = db.query(MatchRecord).filter_by(id=match_id).first()
        if not match:
            raise HTTPException(status_code=404, detail=f"Match {match_id} not found")

        eob = (
            db.query(EOBRecord)
            .filter_by(document_id=match.eob_document_id)
            .order_by(EOBRecord.id.desc())
            .first()
        )
        bill = (
            db.query(BillRecord)
            .filter_by(document_id=match.bill_document_id)
            .order_by(BillRecord.id.desc())
            .first()
        )

        paperless_url = getattr(request.app.state.hub_settings, "paperless_url", "") or ""
        return {
            "match": _serialize_match(match, paperless_url, eob=eob, bill=bill),
            "eob_record": _serialize_eob_full(eob),
            "bill_record": _serialize_bill_full(bill),
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
# EOB Match Correction endpoints (triage-aware confirm/reject/manual)
# ------------------------------------------------------------------


def _resolve_triage_item(triage_item_id: str, action: str, match_id: int) -> None:
    """Resolve a triage queue item and create a correction event.

    Validates the item exists, is pending, and targets this match before resolving.
    """
    try:
        from doc_intelligence_hub.modules.triage.database import get_queue_item, resolve_queue_item

        item = get_queue_item(triage_item_id)
        if not item:
            return
        # Verify it's an EOB review targeting this match
        if item.get("item_type") != "eob_match_review":
            return
        if str(item.get("target_id")) != str(match_id):
            return
        if item.get("status") != "pending":
            return
        resolve_queue_item(triage_item_id, action, {"match_id": match_id})
    except Exception:
        pass  # Non-fatal: triage integration is best-effort


@router.post("/matches/{match_id}/confirm")
async def confirm_match(
    request: Request,
    match_id: int,
    body: MatchConfirmRequest | None = None,
) -> dict[str, Any]:
    """Confirm a match — updates status, creates correction event, optionally resolves triage item."""
    init_db()
    db = get_db_session()
    try:
        match = db.query(MatchRecord).filter_by(id=match_id).first()
        if not match:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail=f"Match {match_id} not found")

        match.status = "confirmed"
        match.user_status = "confirmed"
        match.confirmed_at = datetime.now(UTC)
        match.reviewed_at = datetime.now(UTC)
        if body and body.notes:
            match.notes = body.notes
            match.user_notes = body.notes

        db.add(MatchEvent(
            match_id=match_id,
            event_type="confirmed",
            actor="user",
            detail=f"Match confirmed by reviewer{(': ' + body.notes) if body and body.notes else ''}",
        ))

        # Write to Paperless if enabled
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

        db.commit()

        # Create correction event and resolve triage item
        if body and body.triage_item_id:
            _resolve_triage_item(body.triage_item_id, "confirm", match_id)

        paperless_url = getattr(request.app.state.hub_settings, "paperless_url", "") or ""
        return {
            "status": "ok",
            "match": _serialize_match(match, paperless_url),
        }
    finally:
        db.close()


@router.post("/matches/{match_id}/reject")
async def reject_match(
    request: Request,
    match_id: int,
    body: MatchRejectRequest | None = None,
) -> dict[str, Any]:
    """Reject a match — updates status, creates correction event, optionally resolves triage item."""
    init_db()
    db = get_db_session()
    try:
        match = db.query(MatchRecord).filter_by(id=match_id).first()
        if not match:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail=f"Match {match_id} not found")

        match.status = "rejected"
        match.user_status = "rejected"
        match.reviewed_at = datetime.now(UTC)
        match.confirmed_at = None  # Clear if previously confirmed
        detail_parts = ["Match rejected by reviewer"]
        if body and body.reason:
            match.notes = body.reason
            match.user_notes = body.reason
            detail_parts.append(f"Reason: {body.reason}")
        if body and body.reassign_to:
            detail_parts.append(f"Reassigned to match #{body.reassign_to}")

        db.add(MatchEvent(
            match_id=match_id,
            event_type="rejected",
            actor="user",
            detail=". ".join(detail_parts),
        ))
        db.commit()

        # Resolve triage item
        if body and body.triage_item_id:
            _resolve_triage_item(body.triage_item_id, "reject", match_id)

        paperless_url = getattr(request.app.state.hub_settings, "paperless_url", "") or ""
        return {
            "status": "ok",
            "match": _serialize_match(match, paperless_url),
        }
    finally:
        db.close()


@router.post("/matches/manual")
async def create_manual_match(body: ManualMatchRequest) -> dict[str, Any]:
    """Create a manual EOB↔Bill match — placeholder for future implementation (#877)."""
    from fastapi import HTTPException

    raise HTTPException(
        status_code=501,
        detail="Manual match creation is not yet implemented. See #877.",
    )


@router.get("/candidates/{doc_id}")
async def get_candidates(
    request: Request,
    doc_id: int,
    type: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Find alternative match candidates for a document.

    Returns other matches that share the same EOB or Bill document ID,
    useful for re-linking when the current match is rejected.
    """
    init_db()
    db = get_db_session()
    try:
        # Find matches that share the same EOB or Bill document
        query = db.query(MatchRecord).filter(
            (MatchRecord.eob_document_id == doc_id) | (MatchRecord.bill_document_id == doc_id)
        )
        if type == "eob":
            query = db.query(MatchRecord).filter(MatchRecord.eob_document_id == doc_id)
        elif type == "bill":
            query = db.query(MatchRecord).filter(MatchRecord.bill_document_id == doc_id)

        candidates = (
            query
            .filter(MatchRecord.score > 50)
            .order_by(MatchRecord.score.desc())
            .limit(max(1, min(limit, 100)))
            .all()
        )

        paperless_url = getattr(request.app.state.hub_settings, "paperless_url", "") or ""
        return {
            "doc_id": doc_id,
            "candidates": [_serialize_match(m, paperless_url) for m in candidates],
            "count": len(candidates),
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


# ------------------------------------------------------------------
# Bulk update unmatched EOB status
# ------------------------------------------------------------------


@router.post("/bulk-update")
async def bulk_update_eobs(body: BulkUpdateRequest):
    """Bulk update EOB record statuses (mark as orphan or paid)."""
    status_map = {"mark_orphan": "orphan", "mark_paid": "paid"}
    new_status = status_map[body.action]

    init_db()
    db = get_db_session()
    try:
        try:
            int_ids = [int(i) for i in body.ids]
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="All IDs must be valid integers.")
        updated = (
            db.query(EOBRecord)
            .filter(EOBRecord.id.in_(int_ids))
            .update({EOBRecord.status: new_status}, synchronize_session="fetch")
        )
        db.commit()
        return {"status": "ok", "updated": updated, "new_status": new_status}
    except Exception as exc:
        db.rollback()
        raise exc
    finally:
        db.close()


# ------------------------------------------------------------------
# Coverage analysis
# ------------------------------------------------------------------


@router.get("/coverage")
async def get_coverage_analysis(
    group_by: str | None = None,
) -> dict[str, Any]:
    """Aggregate insurance coverage stats across all EOB records.

    Returns overall totals (billed, plan_pays, patient_responsibility) and
    optional breakdowns grouped by ``insurance_company``, ``provider``, or
    ``month``.  Pass ``group_by`` as a comma-separated string to request
    multiple groupings (e.g. ``?group_by=insurance_company,provider``).
    Valid values: ``insurance_company``, ``provider``, ``month``.
    """

    init_db()
    db = get_db_session()
    try:
        eobs = (
            db.query(EOBRecord)
            .filter(EOBRecord.total_billed.isnot(None))
            .all()
        )

        # --- Overall totals ---
        total_billed = 0.0
        total_allowed = 0.0
        total_plan_pays = 0.0
        total_patient_responsibility = 0.0
        record_count = len(eobs)

        for eob in eobs:
            total_billed += eob.total_billed or 0.0
            total_allowed += eob.total_allowed or 0.0
            total_plan_pays += eob.total_plan_pays or 0.0
            total_patient_responsibility += eob.total_patient_responsibility or 0.0

        coverage_pct = round(total_plan_pays / total_billed * 100, 1) if total_billed else 0.0

        summary = {
            "record_count": record_count,
            "total_billed": round(total_billed, 2),
            "total_allowed": round(total_allowed, 2),
            "total_plan_pays": round(total_plan_pays, 2),
            "total_patient_responsibility": round(total_patient_responsibility, 2),
            "coverage_pct": coverage_pct,
        }

        # --- Breakdowns ---
        requested = {g.strip() for g in (group_by or "").split(",") if g.strip()}
        valid_groups = {"insurance_company", "provider", "month"}
        requested = requested & valid_groups if requested else valid_groups

        def _bucket_key(eob: EOBRecord, kind: str) -> str:
            if kind == "insurance_company":
                return eob.insurance_company or "Unknown"
            if kind == "provider":
                return eob.provider_name or "Unknown"
            # month — derive from date_of_service (stored as string YYYY-MM-DD)
            dos = eob.date_of_service or ""
            return dos[:7] if len(dos) >= 7 else "Unknown"

        breakdowns: dict[str, list[dict[str, Any]]] = {}

        for kind in sorted(requested):
            buckets: dict[str, dict[str, float]] = {}
            for eob in eobs:
                key = _bucket_key(eob, kind)
                if key not in buckets:
                    buckets[key] = {
                        "total_billed": 0.0,
                        "total_plan_pays": 0.0,
                        "total_patient_responsibility": 0.0,
                        "count": 0,
                    }
                b = buckets[key]
                b["total_billed"] += eob.total_billed or 0.0
                b["total_plan_pays"] += eob.total_plan_pays or 0.0
                b["total_patient_responsibility"] += eob.total_patient_responsibility or 0.0
                b["count"] += 1

            rows = []
            for name, vals in sorted(buckets.items(), key=lambda x: -x[1]["total_billed"]):
                billed = vals["total_billed"]
                pct = round(vals["total_plan_pays"] / billed * 100, 1) if billed else 0.0
                rows.append({
                    "name": name,
                    "count": int(vals["count"]),
                    "total_billed": round(billed, 2),
                    "total_plan_pays": round(vals["total_plan_pays"], 2),
                    "total_patient_responsibility": round(vals["total_patient_responsibility"], 2),
                    "coverage_pct": pct,
                })
            breakdowns[kind] = rows

        return {
            "status": "ok",
            "summary": summary,
            "by_insurance_company": breakdowns.get("insurance_company", []),
            "by_provider": breakdowns.get("provider", []),
            "by_month": breakdowns.get("month", []),
        }
    finally:
        db.close()
