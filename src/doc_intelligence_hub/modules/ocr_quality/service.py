"""Orchestration for the OCR quality baseline inventory (issue #25).

Stage 1 (``run_corpus_scan``): resumable, non-mutating full-corpus scan that
computes and persists per-document text/metadata signals.

Stage 2 (``run_stratified_sample``): deterministic stratified sample
selection over an existing Stage-1 run's results, followed by page-aware PDF
profiling for the sampled documents only.

``build_aggregate_report``: privacy-safe aggregate-only summary suitable for
export outside the trusted boundary.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from doc_intelligence_hub.core.paperless import PaperlessClient

from .database import DocumentAssessment, InventoryRun, PdfProfile, RunFailure, SampleSelection
from .models import (
    INVENTORY_SIGNAL_VERSION,
    PDF_PROFILE_VERSION,
    Disposition,
    DocumentProfile,
    ReasonCode,
    RunStage,
    RunStatus,
)
from .pdf_profiling import PdfProfilingError, profile_pdf
from .sampling import SampleCandidate, select_stratified_sample
from .scorer import assess_document as run_quality_scorer
from .scoring_models import AssessmentStatus
from .signals import compute_text_signals

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


class ManualStage2DocumentNotFoundError(Exception):
    """Raised by ``run_manual_stage2`` when the document has no Stage-1 assessment yet."""

    def __init__(self, document_id: int) -> None:
        self.document_id = document_id
        super().__init__(f"No OCR quality assessment for document {document_id}")


class ManualStage2ProfilingFailedError(Exception):
    """Raised by ``run_manual_stage2`` when PDF fetch/profiling failed for the document."""

    def __init__(self, document_id: int, reason_code: str | None) -> None:
        self.document_id = document_id
        self.reason_code = reason_code
        super().__init__(f"Stage-2 profiling failed for document {document_id} ({reason_code})")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _score_quality(
    *, document_id: int, text_content: str | None = None, pdf_bytes: bytes | None = None
) -> dict[str, Any] | None:
    """Run the issue #29 scorer and return persistable fields, or ``None`` on failure.

    Never raises — a scoring failure must not abort the Stage-1/2 scan. Callers
    merge the returned dict into a ``DocumentAssessment`` row's fields; ``None``
    means "leave any existing quality fields unchanged".
    """
    try:
        assessment = run_quality_scorer(text_content=text_content, pdf_bytes=pdf_bytes)
    except Exception as exc:  # noqa: BLE001 - scoring is best-effort, never fatal
        logger.warning(
            "OCR quality scorer (#29) failed for document %s: %s",
            document_id,
            type(exc).__name__,
        )
        return None

    return {
        "overlay_score": assessment.overlay_score,
        "machine_score": assessment.machine_score,
        "review_status": assessment.review_status.value,
        "reasons": json.loads(json.dumps([r.model_dump(mode="json") for r in assessment.reasons])),
        "document_profile": json.loads(assessment.document_profile.model_dump_json()),
        "quality_scorer_version": assessment.scorer_version,
    }


def _document_version_key(document: dict[str, Any]) -> str:
    """Best-effort proxy for detecting a changed document version."""
    for key in ("checksum", "modified", "added"):
        value = document.get(key)
        if value:
            return f"{key}:{value}"
    return "unknown"


def _legacy_score_lookup(action_queue_session: Session | None) -> Callable[[int], int | None]:
    if action_queue_session is None:
        return lambda _document_id: None

    def _lookup(document_id: int) -> int | None:
        try:
            from doc_intelligence_hub.modules.action_queue.database import ProcessingHistory

            row = (
                action_queue_session.query(ProcessingHistory)
                .filter(ProcessingHistory.document_id == document_id)
                .order_by(ProcessingHistory.processed_at.desc())
                .first()
            )
            return row.text_quality_score if row else None
        except Exception:  # pragma: no cover - legacy signal is best-effort only
            return None

    return _lookup


def _downstream_outcome_lookup(
    action_queue_session: Session | None,
) -> Callable[[int], str | None]:
    if action_queue_session is None:
        return lambda _document_id: None

    def _lookup(document_id: int) -> str | None:
        try:
            from doc_intelligence_hub.modules.action_queue.database import ProcessingHistory

            row = (
                action_queue_session.query(ProcessingHistory)
                .filter(ProcessingHistory.document_id == document_id)
                .order_by(ProcessingHistory.processed_at.desc())
                .first()
            )
            return row.disposition if row else None
        except Exception:  # pragma: no cover - best-effort only
            return None

    return _lookup


class OcrQualityInventoryService:
    """Coordinates Stage-1 and Stage-2 non-mutating inventory runs."""

    def __init__(
        self,
        client: PaperlessClient,
        session_factory: SessionFactory,
        *,
        action_queue_session: Session | None = None,
    ):
        self.client = client
        self.session_factory = session_factory
        self._legacy_score = _legacy_score_lookup(action_queue_session)
        self._downstream_outcome = _downstream_outcome_lookup(action_queue_session)

    # ------------------------------------------------------------------
    # Stage 1 — full-corpus text/metadata scan
    # ------------------------------------------------------------------

    async def run_corpus_scan(
        self,
        *,
        batch_size: int = 100,
        run_id: str | None = None,
        resume: bool = False,
        scope_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")

        scope_digest = _digest(scope_params or {})
        config_digest = _digest(
            {"batch_size": batch_size, "signal_version": INVENTORY_SIGNAL_VERSION}
        )
        instance_digest = _digest(self.client.base_url)

        db = self.session_factory()
        try:
            cursor: str | None = None
            if resume:
                if not run_id:
                    raise ValueError("resume requires an explicit run_id")
                run = db.query(InventoryRun).filter_by(run_id=run_id).one_or_none()
                if run is None:
                    raise ValueError(f"Unknown inventory run {run_id}")
                if run.status == RunStatus.COMPLETED.value:
                    raise ValueError(f"Inventory run {run_id} already completed")
                if (
                    run.scope_digest != scope_digest
                    or run.config_digest != config_digest
                    or run.instance_digest != instance_digest
                ):
                    raise ValueError(
                        "Resume refused: scope, configuration, or Paperless instance changed"
                    )
                cursor = run.cursor
            else:
                run_id = run_id or str(uuid4())
                run = InventoryRun(
                    run_id=run_id,
                    stage=RunStage.STAGE_1_CORPUS_SCAN.value,
                    scope_digest=scope_digest,
                    config_digest=config_digest,
                    instance_digest=instance_digest,
                    signal_version=INVENTORY_SIGNAL_VERSION,
                    status=RunStatus.RUNNING.value,
                    counts={},
                    started_at=datetime.utcnow(),
                )
                db.add(run)
                db.commit()

            counts: Counter[str] = Counter(run.counts or {})
            start_time = time.monotonic()
            processed = 0

            async for page in self.client.iter_document_pages(
                page_size=batch_size, cursor=cursor, scope_params=scope_params
            ):
                for document in page.results:
                    disposition = self._assess_document(db, run_id, document)
                    counts[disposition.value] += 1
                    processed += 1
                cursor = page.next_cursor
                run.cursor = cursor
                run.counts = dict(counts)
                db.commit()

            elapsed = max(time.monotonic() - start_time, 1e-9)
            run.status = RunStatus.COMPLETED.value
            run.finished_at = datetime.utcnow()
            run.throughput_docs_per_second = round(processed / elapsed, 4) if processed else 0.0
            run.counts = dict(counts)
            db.commit()

            return {
                "run_id": run_id,
                "stage": RunStage.STAGE_1_CORPUS_SCAN.value,
                "status": run.status,
                "counts": dict(counts),
                "throughput_docs_per_second": run.throughput_docs_per_second,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "schema_version": "1.0",
                "redacted": True,
            }
        finally:
            db.close()

    def _assess_document(self, db: Session, run_id: str, document: dict[str, Any]) -> Disposition:
        try:
            document_id = int(document["id"])
        except (KeyError, TypeError, ValueError):
            db.add(
                RunFailure(
                    run_id=run_id,
                    document_id=None,
                    stage=RunStage.STAGE_1_CORPUS_SCAN.value,
                    reason_code=ReasonCode.MISSING_DOCUMENT_ID.value,
                )
            )
            return Disposition.SKIPPED

        try:
            content = document.get("content")
            signals = compute_text_signals(content)
            version_key = _document_version_key(document)

            existing = (
                db.query(DocumentAssessment)
                .filter_by(
                    document_id=document_id,
                    document_version_key=version_key,
                    scorer_version=INVENTORY_SIGNAL_VERSION,
                )
                .one_or_none()
            )

            document_type = document.get("document_type")
            correspondent = document.get("correspondent")
            created = document.get("created")
            legacy_score = self._legacy_score(document_id)
            downstream_outcome = self._downstream_outcome(document_id)

            fields = {
                "run_id": run_id,
                "content_length": signals.content_length,
                "word_count": signals.word_count,
                "non_ascii_ratio": signals.non_ascii_ratio,
                "whitespace_ratio": signals.whitespace_ratio,
                "repetition_ratio": signals.repetition_ratio,
                "avg_token_length": signals.avg_token_length,
                "distinct_token_ratio": signals.distinct_token_ratio,
                "table_shape_hint": signals.table_shape_hint,
                "code_shape_hint": signals.code_shape_hint,
                "preliminary_score": signals.preliminary_score,
                "disposition": Disposition.ASSESSED.value,
                "reason_codes": [r.value for r in signals.reason_codes],
                "document_type": str(document_type) if document_type is not None else None,
                "correspondent": str(correspondent) if correspondent is not None else None,
                "document_created": str(created) if created is not None else None,
                "legacy_action_queue_score": legacy_score,
                "downstream_outcome": downstream_outcome,
            }

            # Issue #29 machine-only score (no PDF fetched at Stage 1 — overlay
            # score remains unavailable until Stage 2 profiles the PDF).
            quality_fields = _score_quality(document_id=document_id, text_content=content)
            if quality_fields is not None:
                fields.update(quality_fields)

            if existing is not None:
                for key, value in fields.items():
                    setattr(existing, key, value)
            else:
                db.add(
                    DocumentAssessment(
                        document_id=document_id,
                        document_version_key=version_key,
                        scorer_version=INVENTORY_SIGNAL_VERSION,
                        first_seen_run_id=run_id,
                        **fields,
                    )
                )
            return Disposition.ASSESSED
        except Exception as exc:  # noqa: BLE001 - continue the run, never abort on one doc
            logger.warning(
                "OCR quality Stage-1 assessment failed for document %s: %s",
                document_id,
                type(exc).__name__,
            )
            db.add(
                RunFailure(
                    run_id=run_id,
                    document_id=document_id,
                    stage=RunStage.STAGE_1_CORPUS_SCAN.value,
                    reason_code=ReasonCode.FETCH_FAILED.value,
                    error_type=type(exc).__name__,
                )
            )
            return Disposition.FAILED

    def _apply_pdf_quality_score(
        self, db: Session, run_id: str, document_id: int, pdf_bytes: bytes
    ) -> None:
        """Upgrade a document's #29 score to include overlay signals.

        Stage 1 already scored this document machine-only (no PDF). Now that
        Stage 2 has fetched PDF bytes, re-run the scorer with full page
        geometry and overwrite the most recent assessment row for this
        document in place, rather than creating a duplicate row.
        """
        quality_fields = _score_quality(document_id=document_id, pdf_bytes=pdf_bytes)
        if quality_fields is None:
            return

        row = (
            db.query(DocumentAssessment)
            .filter(DocumentAssessment.document_id == document_id)
            .order_by(DocumentAssessment.id.desc())
            .first()
        )
        if row is None:
            return

        for key, value in quality_fields.items():
            setattr(row, key, value)
        row.run_id = run_id

    # ------------------------------------------------------------------
    # Stage 2 — deterministic stratified sample + PDF profiling
    # ------------------------------------------------------------------

    async def run_stratified_sample(
        self,
        *,
        source_run_id: str,
        sample_size: int,
        seed: str,
        min_per_stratum: int = 2,
        pdf_profile_max_pages: int = 50,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        db = self.session_factory()
        try:
            source_run = db.query(InventoryRun).filter_by(run_id=source_run_id).one_or_none()
            if source_run is None:
                raise ValueError(f"Unknown source inventory run {source_run_id}")

            run_id = run_id or str(uuid4())
            sample_run = InventoryRun(
                run_id=run_id,
                stage=RunStage.STAGE_2_STRATIFIED_SAMPLE.value,
                scope_digest=source_run.scope_digest,
                config_digest=_digest(
                    {"sample_size": sample_size, "min_per_stratum": min_per_stratum}
                ),
                instance_digest=source_run.instance_digest,
                signal_version=PDF_PROFILE_VERSION,
                seed=seed,
                source_run_id=source_run_id,
                status=RunStatus.RUNNING.value,
                counts={},
                started_at=datetime.utcnow(),
            )
            db.add(sample_run)
            db.commit()

            assessments = (
                db.query(DocumentAssessment)
                .filter(DocumentAssessment.run_id == source_run_id)
                .all()
            )
            candidates = [
                SampleCandidate(
                    document_id=a.document_id,
                    preliminary_score=a.preliminary_score,
                    document_type=a.document_type,
                    correspondent=a.correspondent,
                    created=a.document_created,
                    downstream_outcome=a.downstream_outcome,
                    content_length=a.content_length,
                )
                for a in assessments
            ]
            decisions = select_stratified_sample(
                candidates,
                seed=seed,
                target_size=sample_size,
                min_per_stratum=min_per_stratum,
            )

            counts: Counter[str] = Counter()
            for decision in decisions:
                db.add(
                    SampleSelection(
                        run_id=run_id,
                        source_run_id=source_run_id,
                        document_id=decision.document_id,
                        stratum_key=decision.stratum_key,
                        selection_rank=decision.selection_rank,
                    )
                )
                disposition = await self._profile_sampled_document(
                    db, run_id, decision.document_id, pdf_profile_max_pages
                )
                counts[disposition.value] += 1
            db.commit()

            sample_run.status = RunStatus.COMPLETED.value
            sample_run.finished_at = datetime.utcnow()
            sample_run.counts = dict(counts)
            db.commit()

            return {
                "run_id": run_id,
                "source_run_id": source_run_id,
                "stage": RunStage.STAGE_2_STRATIFIED_SAMPLE.value,
                "status": sample_run.status,
                "sample_size_requested": sample_size,
                "sample_size_selected": len(decisions),
                "counts": dict(counts),
                "schema_version": "1.0",
                "redacted": True,
            }
        finally:
            db.close()

    async def _profile_sampled_document(
        self, db: Session, run_id: str, document_id: int, max_pages: int
    ) -> Disposition:
        try:
            pdf_bytes, _content_type = await self.client.get_document_preview(document_id)
            result = profile_pdf(pdf_bytes, max_pages=max_pages)
            db.add(
                PdfProfile(
                    run_id=run_id,
                    document_id=document_id,
                    profile_version=PDF_PROFILE_VERSION,
                    profile=result.profile.value,
                    page_count=result.page_count,
                    digital_pages=result.digital_pages,
                    scanned_overlay_pages=result.scanned_overlay_pages,
                    no_text_pages=result.no_text_pages,
                    reason_codes=[r.value for r in result.reason_codes],
                )
            )
            self._apply_pdf_quality_score(db, run_id, document_id, pdf_bytes)
            return Disposition.ASSESSED
        except PdfProfilingError as exc:
            db.add(
                PdfProfile(
                    run_id=run_id,
                    document_id=document_id,
                    profile_version=PDF_PROFILE_VERSION,
                    profile=DocumentProfile.UNKNOWN.value,
                    reason_codes=[ReasonCode.PDF_PARSE_FAILED.value],
                )
            )
            db.add(
                RunFailure(
                    run_id=run_id,
                    document_id=document_id,
                    stage=RunStage.STAGE_2_STRATIFIED_SAMPLE.value,
                    reason_code=ReasonCode.PDF_PARSE_FAILED.value,
                    error_type=type(exc).__name__,
                )
            )
            return Disposition.FAILED
        except Exception as exc:  # noqa: BLE001 - continue sampling other documents
            logger.warning(
                "OCR quality Stage-2 profiling failed for document %s: %s",
                document_id,
                type(exc).__name__,
            )
            db.add(
                RunFailure(
                    run_id=run_id,
                    document_id=document_id,
                    stage=RunStage.STAGE_2_STRATIFIED_SAMPLE.value,
                    reason_code=ReasonCode.PDF_DOWNLOAD_FAILED.value,
                    error_type=type(exc).__name__,
                )
            )
            return Disposition.FAILED

    async def run_manual_stage2(
        self,
        *,
        document_id: int,
        max_pages: int = 50,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Force Stage-2 PDF profiling + quality re-score for one arbitrary document.

        Unlike ``run_stratified_sample`` (a corpus-wide random draw), this is
        an explicit single-document override — e.g. triggered from the
        document detail page. Requires an existing Stage-1
        ``DocumentAssessment`` for ``document_id`` (raises
        ``ManualStage2DocumentNotFoundError`` otherwise); its ``run_id``
        becomes the new manual run's ``source_run_id`` so it stays traceable
        back to the corpus scan that first discovered the document, while the
        resulting ``PdfProfile``/updated ``DocumentAssessment`` are attached
        to a fresh dedicated manual ``run_id`` (stage
        ``STAGE_2_MANUAL_SINGLE_DOCUMENT``). Reusing
        ``_profile_sampled_document`` unchanged means existing per-document
        detail queries (keyed on the most recent assessment row) pick up the
        result without any new querying logic. Raises
        ``ManualStage2ProfilingFailedError`` if the PDF fetch/parse failed.
        """
        db = self.session_factory()
        try:
            assessment = (
                db.query(DocumentAssessment)
                .filter(DocumentAssessment.document_id == document_id)
                .order_by(DocumentAssessment.id.desc())
                .first()
            )
            if assessment is None:
                raise ManualStage2DocumentNotFoundError(document_id)

            manual_run_id = run_id or str(uuid4())
            manual_run = InventoryRun(
                run_id=manual_run_id,
                stage=RunStage.STAGE_2_MANUAL_SINGLE_DOCUMENT.value,
                scope_digest=_digest({"document_id": document_id}),
                config_digest=_digest({"document_id": document_id, "max_pages": max_pages}),
                instance_digest=_digest({"manual_stage2": document_id}),
                signal_version=PDF_PROFILE_VERSION,
                source_run_id=assessment.run_id,
                status=RunStatus.RUNNING.value,
                counts={},
                started_at=datetime.utcnow(),
            )
            db.add(manual_run)
            db.commit()

            disposition = await self._profile_sampled_document(
                db, manual_run_id, document_id, max_pages
            )
            db.commit()

            manual_run.status = RunStatus.COMPLETED.value
            manual_run.finished_at = datetime.utcnow()
            manual_run.counts = {disposition.value: 1}
            db.commit()

            if disposition == Disposition.FAILED:
                failure = (
                    db.query(RunFailure)
                    .filter(
                        RunFailure.run_id == manual_run_id,
                        RunFailure.document_id == document_id,
                    )
                    .order_by(RunFailure.id.desc())
                    .first()
                )
                raise ManualStage2ProfilingFailedError(
                    document_id, failure.reason_code if failure else None
                )

            refreshed = (
                db.query(DocumentAssessment)
                .filter(DocumentAssessment.document_id == document_id)
                .order_by(DocumentAssessment.id.desc())
                .first()
            )
            return _assessment_detail(refreshed)
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Privacy-safe aggregate reporting
    # ------------------------------------------------------------------

    def build_aggregate_report(self, run_id: str) -> dict[str, Any]:
        """Aggregate counts/distributions only — no titles, text, or IDs."""
        db = self.session_factory()
        try:
            run = db.query(InventoryRun).filter_by(run_id=run_id).one_or_none()
            if run is None:
                raise ValueError(f"Unknown inventory run {run_id}")

            report: dict[str, Any] = {
                "run_id": run_id,
                "stage": run.stage,
                "status": run.status,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "throughput_docs_per_second": run.throughput_docs_per_second,
                "counts": dict(run.counts or {}),
                "schema_version": "1.0",
                "redacted": True,
            }

            if run.stage == RunStage.STAGE_1_CORPUS_SCAN.value:
                score_rows = (
                    db.query(DocumentAssessment.preliminary_score)
                    .filter(DocumentAssessment.run_id == run_id)
                    .all()
                )
                decile_counts: Counter[str] = Counter()
                for (score,) in score_rows:
                    bucket = min(int(score) // 10, 9) * 10
                    decile_counts[f"{bucket}-{bucket + 9}"] += 1
                report["preliminary_score_decile_distribution"] = dict(decile_counts)

                doc_type_counts = (
                    db.query(DocumentAssessment.document_type, func.count())
                    .filter(DocumentAssessment.run_id == run_id)
                    .group_by(DocumentAssessment.document_type)
                    .all()
                )
                report["document_type_distribution"] = {
                    (doc_type or "unknown"): count for doc_type, count in doc_type_counts
                }
                outcome_counts = (
                    db.query(DocumentAssessment.downstream_outcome, func.count())
                    .filter(DocumentAssessment.run_id == run_id)
                    .group_by(DocumentAssessment.downstream_outcome)
                    .all()
                )
                report["downstream_outcome_distribution"] = {
                    (outcome or "unknown"): count for outcome, count in outcome_counts
                }
            elif run.stage == RunStage.STAGE_2_STRATIFIED_SAMPLE.value:
                profile_counts = (
                    db.query(PdfProfile.profile, func.count())
                    .filter(PdfProfile.run_id == run_id)
                    .group_by(PdfProfile.profile)
                    .all()
                )
                report["pdf_profile_distribution"] = {
                    profile: count for profile, count in profile_counts
                }
                stratum_counts = (
                    db.query(SampleSelection.stratum_key, func.count())
                    .filter(SampleSelection.run_id == run_id)
                    .group_by(SampleSelection.stratum_key)
                    .all()
                )
                report["sample_size_by_stratum"] = dict(stratum_counts)

            return report
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Read-only query surface for the OWL review UI (issue #115)
    # ------------------------------------------------------------------

    _RISK_ORDER = {
        AssessmentStatus.FAILED.value: 0,
        AssessmentStatus.REVIEW_RECOMMENDED.value: 1,
        AssessmentStatus.UNCERTAIN.value: 2,
        AssessmentStatus.GOOD.value: 3,
    }

    def _latest_assessment_query(self, db: Session):
        """Base query over only the most recent assessment row per document."""
        latest_ids = (
            db.query(func.max(DocumentAssessment.id).label("id"))
            .group_by(DocumentAssessment.document_id)
            .subquery()
        )
        return db.query(DocumentAssessment).join(
            latest_ids, DocumentAssessment.id == latest_ids.c.id
        )

    def _apply_document_filters(
        self,
        query,
        *,
        review_status: str | None,
        document_type: str | None,
        correspondent: str | None,
        document_profile: str | None,
        downstream_outcome: str | None,
        created_after: str | None,
        created_before: str | None,
    ):
        if review_status:
            query = query.filter(DocumentAssessment.review_status == review_status)
        if document_type:
            query = query.filter(DocumentAssessment.document_type == document_type)
        if correspondent:
            query = query.filter(DocumentAssessment.correspondent == correspondent)
        if downstream_outcome:
            query = query.filter(DocumentAssessment.downstream_outcome == downstream_outcome)
        if document_profile:
            query = query.filter(
                DocumentAssessment.document_profile["dominant_classification"].as_string()
                == document_profile
            )
        if created_after:
            query = query.filter(DocumentAssessment.document_created >= created_after)
        if created_before:
            query = query.filter(DocumentAssessment.document_created <= created_before)
        return query

    # Columns the review queue UI is allowed to sort by, mapped to the
    # underlying ORM column. Keep in sync with the frontend's sortable
    # `DataTable` column keys in `OcrQualityReviewQueue.tsx`.
    _SORTABLE_COLUMNS = {
        "document_id": DocumentAssessment.document_id,
        "document_type": DocumentAssessment.document_type,
        "correspondent": DocumentAssessment.correspondent,
        "document_created": DocumentAssessment.document_created,
        "review_status": DocumentAssessment.review_status,
        "overlay_score": DocumentAssessment.overlay_score,
        "machine_score": DocumentAssessment.machine_score,
        "downstream_outcome": DocumentAssessment.downstream_outcome,
    }

    def list_document_assessments(
        self,
        *,
        review_status: str | None = None,
        document_type: str | None = None,
        correspondent: str | None = None,
        document_profile: str | None = None,
        downstream_outcome: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        sort_by: str | None = None,
        sort_dir: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Paginated, filtered list of the latest per-document #29 assessment.

        Read-only over this module's own SQLite state — never contacts
        Paperless. Only aggregate-safe/metadata fields are returned; no raw
        OCR text.

        When ``sort_by`` names one of ``_SORTABLE_COLUMNS`` the result is
        ordered by that column at the database level (so sorting is stable
        across pages of a large corpus); otherwise the historical default
        ordering (most recent document first, then grouped by risk) is used.
        """
        db = self.session_factory()
        try:
            base_query = self._apply_document_filters(
                self._latest_assessment_query(db),
                review_status=review_status,
                document_type=document_type,
                correspondent=correspondent,
                document_profile=document_profile,
                downstream_outcome=downstream_outcome,
                created_after=created_after,
                created_before=created_before,
            )
            total = base_query.count()
            sort_column = self._SORTABLE_COLUMNS.get(sort_by or "")
            if sort_column is not None:
                order_column = sort_column.asc() if sort_dir == "asc" else sort_column.desc()
                rows = (
                    base_query.order_by(order_column, DocumentAssessment.document_id.desc())
                    .offset(max(offset, 0))
                    .limit(max(1, min(limit, 200)))
                    .all()
                )
            else:
                rows = (
                    base_query.order_by(DocumentAssessment.document_id.desc())
                    .offset(max(offset, 0))
                    .limit(max(1, min(limit, 200)))
                    .all()
                )
                rows.sort(key=lambda r: self._RISK_ORDER.get(r.review_status or "", 4))
            return {
                "documents": [_assessment_summary(row) for row in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
                "redacted": True,
            }
        finally:
            db.close()

    def list_downstream_outcomes(self) -> list[str]:
        """Distinct, non-null ``downstream_outcome`` values seen in the corpus.

        Backs the review queue's "Downstream outcome" filter dropdown so it
        always reflects the actual values written by the action-queue
        pipeline (see ``action_queue.database.ProcessingHistory.disposition``)
        instead of a hand-maintained enum that could drift out of date.
        """
        db = self.session_factory()
        try:
            rows = (
                db.query(DocumentAssessment.downstream_outcome)
                .filter(DocumentAssessment.downstream_outcome.isnot(None))
                .distinct()
                .all()
            )
            return sorted({value for (value,) in rows if value})
        finally:
            db.close()

    def get_document_assessment(self, document_id: int) -> dict[str, Any] | None:
        """Full detail for the most recent assessment of one document."""
        db = self.session_factory()
        try:
            row = (
                db.query(DocumentAssessment)
                .filter(DocumentAssessment.document_id == document_id)
                .order_by(DocumentAssessment.id.desc())
                .first()
            )
            if row is None:
                return None
            return _assessment_detail(row)
        finally:
            db.close()

    def build_corpus_distribution(self) -> dict[str, Any]:
        """Privacy-safe corpus-wide snapshot across the latest assessment per document."""
        db = self.session_factory()
        try:
            latest = self._latest_assessment_query(db)
            total = latest.count()

            status_counts = Counter((row.review_status or "unscored") for row in latest.all())

            def _decile_distribution(column) -> dict[str, int]:
                buckets: Counter[str] = Counter()
                for (value,) in latest.with_entities(column).all():
                    if value is None:
                        buckets["unavailable"] += 1
                        continue
                    bucket = min(int(value) // 10, 9) * 10
                    buckets[f"{bucket}-{bucket + 9}"] += 1
                return dict(buckets)

            scorer_versions = Counter(
                (row.quality_scorer_version or "unscored") for row in latest.all()
            )
            assessed_ats = [row.updated_at for row in latest.all() if row.updated_at]

            return {
                "total_documents": total,
                "review_status_distribution": dict(status_counts),
                "overlay_score_decile_distribution": _decile_distribution(
                    DocumentAssessment.overlay_score
                ),
                "machine_score_decile_distribution": _decile_distribution(
                    DocumentAssessment.machine_score
                ),
                "scorer_version_distribution": dict(scorer_versions),
                "oldest_assessed_at": min(assessed_ats).isoformat() if assessed_ats else None,
                "newest_assessed_at": max(assessed_ats).isoformat() if assessed_ats else None,
                "schema_version": "1.0",
                "redacted": True,
            }
        finally:
            db.close()


def _assessment_summary(row: DocumentAssessment) -> dict[str, Any]:
    return {
        "document_id": row.document_id,
        "document_type": row.document_type,
        "correspondent": row.correspondent,
        "document_created": row.document_created,
        "overlay_score": row.overlay_score,
        "machine_score": row.machine_score,
        "review_status": row.review_status,
        "downstream_outcome": row.downstream_outcome,
        "dominant_classification": (row.document_profile or {}).get("dominant_classification"),
        "quality_scorer_version": row.quality_scorer_version,
        "assessed_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _assessment_detail(row: DocumentAssessment) -> dict[str, Any]:
    return {
        **_assessment_summary(row),
        "run_id": row.run_id,
        "first_seen_run_id": row.first_seen_run_id,
        "preliminary_score": row.preliminary_score,
        "legacy_action_queue_score": row.legacy_action_queue_score,
        "reasons": row.reasons or [],
        "document_profile": row.document_profile,
        "reason_codes": row.reason_codes or [],
    }


__all__ = ["OcrQualityInventoryService"]
