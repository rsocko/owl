"""EOB Matching service — encapsulates classification, extraction, and matching pipeline."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from doc_intelligence_hub.core.paperless import AccountIdentifierClass, mask_account_identifier
from doc_intelligence_hub.core.resilience import retry_async
from doc_intelligence_hub.core.services.base import BaseService
from doc_intelligence_hub.modules.eob_matching.classifier import classify_document
from doc_intelligence_hub.modules.eob_matching.database import (
    BillRecord,
    EOBRecord,
    MatchEvent,
    MatchingRun,
    MatchRecord,
    init_db,
)
from doc_intelligence_hub.modules.eob_matching.database import (
    get_session as get_db_session,
)
from doc_intelligence_hub.modules.eob_matching.llm_extractor import (
    extract_bill_llm,
    extract_eob_llm,
)
from doc_intelligence_hub.modules.eob_matching.matcher import match_documents
from doc_intelligence_hub.modules.eob_matching.models import DocumentType


class EOBMatchingService(BaseService):
    """Service layer for EOB classification, extraction, and matching.

    Decouples the API router from pipeline internals and adds:
    - Structured logging for each pipeline step
    - Retry for LLM extraction calls
    - Circuit breaker for external Paperless/LLM calls
    """

    service_name = "eob_matching"

    def classify_documents(self, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Classify a batch of documents as EOB, Bill, or Unknown."""
        self.logger.info("Classifying %d documents", len(documents))
        results: list[dict[str, Any]] = []
        for doc in documents:
            classification = classify_document(doc.get("content", ""))
            results.append(
                {
                    "document_id": doc["id"],
                    "title": doc.get("title"),
                    "classification": classification.model_dump(mode="json"),
                }
            )
        self.logger.info(
            "Classification complete: %s",
            self._summarize_classifications(results),
        )
        return results

    @retry_async(max_attempts=3, base_delay=2.0)
    async def extract_eob(self, content: str, document_id: str):
        """Extract EOB data from document content with retry."""
        return await self._call_with_breaker(
            "llm",
            extract_eob_llm(content, document_id=document_id),
        )

    @retry_async(max_attempts=3, base_delay=2.0)
    async def extract_bill(self, content: str, document_id: str):
        """Extract Bill data from document content with retry."""
        return await self._call_with_breaker(
            "llm",
            extract_bill_llm(content, document_id=document_id),
        )

    def match(self, eobs, bills):
        """Run the matching algorithm on extracted EOBs and Bills."""
        self.logger.info("Matching %d EOBs against %d Bills", len(eobs), len(bills))
        matches = match_documents(eobs, bills)
        self.logger.info(
            "Matching complete: %d matches (H=%d, M=%d, L=%d)",
            len(matches),
            sum(1 for m in matches if m.confidence.value == "HIGH"),
            sum(1 for m in matches if m.confidence.value == "MEDIUM"),
            sum(1 for m in matches if m.confidence.value == "LOW"),
        )
        return matches

    async def run_pipeline(
        self,
        documents: list[dict[str, Any]],
        *,
        run_record: MatchingRun,
        verbose: bool = False,
        write_enabled: bool = False,
        enricher=None,
    ) -> dict[str, Any]:
        """Run the full EOB matching pipeline: classify → extract → match → persist.

        Args:
            documents: Hydrated documents with content.
            run_record: The MatchingRun record to update.
            verbose: Include extracted data in response.
            write_enabled: Write results back to Paperless.
            enricher: Optional EOBEnricher instance for Paperless linking.

        Returns:
            Pipeline result summary dict.
        """
        init_db()
        db = get_db_session()
        self.logger.info(
            "Starting pipeline run #%d with %d documents",
            run_record.id,
            len(documents),
        )

        classified_documents: list[dict[str, Any]] = []
        extracted_eobs = []
        extracted_bills = []

        # Step 1: Classify + Extract
        for doc in documents:
            content = doc.get("content", "")
            classification = classify_document(content)
            item: dict[str, Any] = {
                "document_id": doc["id"],
                "title": doc.get("title"),
                "classification": classification.model_dump(mode="json"),
            }

            if classification.type == DocumentType.EOB:
                try:
                    extracted = await self.extract_eob(content, document_id=str(doc["id"]))
                except Exception as exc:
                    self.logger.warning("EOB extraction failed for doc %s: %s", doc["id"], exc)
                    classified_documents.append(item)
                    continue
                extracted_eobs.append(extracted)
                if verbose:
                    item["extracted"] = extracted.model_dump(mode="json")
                    item["extracted"]["policy_number"] = mask_account_identifier(
                        extracted.policy_number,
                        AccountIdentifierClass.POLICY,
                    )

                db.add(
                    EOBRecord(
                        document_id=doc["id"],
                        run_id=run_record.id,
                        title=doc.get("title"),
                        classification_score=classification.confidence_score,
                        insurance_company=extracted.insurance_company,
                        policy_number=mask_account_identifier(
                            extracted.policy_number,
                            AccountIdentifierClass.POLICY,
                        ),
                        patient_name=extracted.patient_name,
                        claim_number=extracted.claim_number,
                        date_of_service=str(extracted.date_of_service)
                        if extracted.date_of_service
                        else None,
                        provider_name=extracted.provider_name,
                        total_billed=extracted.total_billed,
                        total_allowed=extracted.total_allowed,
                        total_plan_pays=extracted.total_plan_pays,
                        total_patient_responsibility=extracted.total_patient_responsibility,
                        services_json=json.dumps(
                            [s.model_dump(mode="json") for s in extracted.services]
                        )
                        if extracted.services
                        else None,
                    )
                )

            elif classification.type == DocumentType.BILL:
                try:
                    extracted = await self.extract_bill(content, document_id=str(doc["id"]))
                except Exception as exc:
                    self.logger.warning("Bill extraction failed for doc %s: %s", doc["id"], exc)
                    classified_documents.append(item)
                    continue
                extracted_bills.append(extracted)
                if verbose:
                    item["extracted"] = extracted.model_dump(mode="json")

                db.add(
                    BillRecord(
                        document_id=doc["id"],
                        run_id=run_record.id,
                        title=doc.get("title"),
                        classification_score=classification.confidence_score,
                        provider_name=extracted.provider_name,
                        patient_name=extracted.patient_name,
                        invoice_number=extracted.invoice_number,
                        date_of_service=str(extracted.date_of_service)
                        if extracted.date_of_service
                        else None,
                        due_date=str(extracted.due_date) if extracted.due_date else None,
                        total_amount=extracted.total_amount,
                        balance_due=extracted.balance_due,
                        payment_status=extracted.payment_status,
                        services_json=json.dumps(
                            [s.model_dump(mode="json") for s in extracted.services]
                        )
                        if extracted.services
                        else None,
                    )
                )

            classified_documents.append(item)

        db.commit()

        # Step 2: Match
        matches = self.match(extracted_eobs, extracted_bills)
        matched_eob_ids = {m.eob_id for m in matches}

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
            db.flush()
            db.add(
                MatchEvent(
                    match_id=match_rec.id,
                    event_type="auto_matched",
                    actor="system",
                    detail=f"Auto-matched with {match.confidence.value} confidence ({match.score:.0f}%)",
                )
            )
            if match.confidence.value in ("LOW", "MEDIUM"):
                db.add(
                    MatchEvent(
                        match_id=match_rec.id,
                        event_type="flagged",
                        actor="system",
                        detail=f"Flagged for review — {match.confidence.value.lower()} confidence",
                    )
                )
        db.commit()

        # Step 3: Write to Paperless (if enabled)
        linked_count = 0
        if write_enabled and enricher and matches:
            if getattr(enricher, "audit_session", None) is None:
                enricher.audit_session = db
            eob_lookup = {e.document_id: e for e in extracted_eobs}
            bill_lookup = {b.document_id: b for b in extracted_bills}
            for match in matches:
                try:
                    eob_data = eob_lookup.get(match.eob_id)
                    audit_records = await enricher.link_match(
                        eob_document_id=int(match.eob_id),
                        bill_document_id=int(match.bill_id),
                        score=match.score,
                        confidence=match.confidence.value,
                        eob=eob_data,
                        bill=bill_lookup.get(match.bill_id),
                    )
                    linked_count += bool(audit_records)
                except Exception as exc:
                    self.logger.debug("Paperless link failed for match: %s", exc)

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

        # Emit alerts (best-effort)
        self._emit_alerts(extracted_eobs, extracted_bills, matches, matched_eob_ids)

        self.logger.info(
            "Pipeline run #%d complete: %d docs → %d EOBs, %d Bills, %d matches",
            run_record.id,
            len(documents),
            len(extracted_eobs),
            len(extracted_bills),
            len(matches),
        )

        return {
            "documents_scanned": len(documents),
            "classifications": classified_documents,
            "summary": self._summarize_classifications(classified_documents),
            "matches": len(matches),
            "high_confidence": high,
            "medium_confidence": medium,
            "low_confidence": low,
            "linked_in_paperless": linked_count,
            "extracted_eobs": len(extracted_eobs),
            "extracted_bills": len(extracted_bills),
        }

    def _emit_alerts(self, extracted_eobs, extracted_bills, matches, matched_eob_ids):
        """Emit unified alerts — best effort, never raises."""
        try:
            from doc_intelligence_hub.core.alerts import check_eob_due_dates, emit_eob_alerts

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
            high_conf = [
                {
                    "eob_document_id": int(m.eob_id),
                    "bill_document_id": int(m.bill_id),
                    "score": m.score,
                    "confidence": m.confidence.value,
                }
                for m in matches
                if m.confidence.value == "HIGH"
            ]
            emit_eob_alerts(
                unmatched_eobs=unmatched,
                low_confidence_matches=low_conf,
                high_confidence_matches=high_conf,
            )

            bill_dicts = [
                {
                    "document_id": int(b.document_id),
                    "provider_name": b.provider_name,
                    "due_date": str(b.due_date) if b.due_date else None,
                    "payment_status": b.payment_status,
                    "balance_due": b.balance_due,
                }
                for b in extracted_bills
            ]
            check_eob_due_dates(bill_dicts)
        except Exception as exc:
            self.logger.debug("Alert emission failed (best-effort): %s", exc)

    @staticmethod
    def _summarize_classifications(classifications: list[dict[str, Any]]) -> dict[str, int]:
        counts = Counter(item["classification"]["type"] for item in classifications)
        return {dt.value: counts.get(dt.value, 0) for dt in DocumentType}
