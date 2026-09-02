"""Orchestration for OCR candidate generation, comparison, and staging (issue #18, slice 1).

This module intentionally never writes to Paperless. It only:

- reads the current document's PDF/metadata via ``PaperlessClient`` (GET only);
- generates candidate PDF/text via a provider (:mod:`providers`);
- stores candidate artifacts under ``settings.candidate_storage_dir`` and
  metadata in the ``ocr_quality_candidates`` table;
- compares a READY candidate to the current document (:mod:`comparison`); and
- records a reviewer's accept/reject decision in that same table.

Applying an accepted candidate as the new Paperless version, version
preservation, and rollback are a later slice gated on issue #114.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from doc_intelligence_hub.core.paperless import PaperlessClient

from . import comparison as comparison_module
from .candidate_models import (
    CANCELLABLE_STATES,
    CandidateGenResult,
    CandidateState,
    Decision,
    EngineName,
)
from .config import settings
from .database import OcrQualityCandidate
from .pdf_loader import load_pdf_pages
from .providers.azure_document_intelligence import AzureDocumentIntelligenceProvider
from .providers.base import OcrProvider
from .providers.ocrmypdf_tesseract import OcrMyPdfProvider
from .scorer import assess_document
from .service import _document_version_key

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


class BatchCapExceeded(ValueError):
    """Raised when a requested batch violates a configured cap."""


class UnsupportedProvider(ValueError):
    """Raised when a requested provider is unknown or not allowlisted."""


def _providers() -> dict[str, OcrProvider]:
    return {
        EngineName.OCRMYPDF_TESSERACT.value: OcrMyPdfProvider(),
        EngineName.AZURE_PREBUILT_READ.value: AzureDocumentIntelligenceProvider(),
    }


def _checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _storage_dir() -> Path:
    path = Path(settings.candidate_storage_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _artifact_paths(candidate_id: str) -> tuple[Path, Path]:
    base = _storage_dir()
    return base / f"{candidate_id}.pdf", base / f"{candidate_id}.txt"


def _save_artifacts(candidate_id: str, pdf_bytes: bytes | None, text: str | None) -> None:
    pdf_path, text_path = _artifact_paths(candidate_id)
    if pdf_bytes:
        pdf_path.write_bytes(pdf_bytes)
    if text is not None:
        text_path.write_text(text, encoding="utf-8")


def _load_candidate_pdf_bytes(candidate_id: str) -> bytes | None:
    pdf_path, _ = _artifact_paths(candidate_id)
    return pdf_path.read_bytes() if pdf_path.exists() else None


def _load_candidate_text(candidate_id: str) -> str | None:
    _, text_path = _artifact_paths(candidate_id)
    return text_path.read_text(encoding="utf-8") if text_path.exists() else None


def _delete_artifacts(candidate_id: str) -> None:
    pdf_path, text_path = _artifact_paths(candidate_id)
    pdf_path.unlink(missing_ok=True)
    text_path.unlink(missing_ok=True)


class OcrCandidateService:
    """Coordinates candidate generation, comparison, and review decisions."""

    def __init__(self, client: PaperlessClient, session_factory: SessionFactory):
        self.client = client
        self.session_factory = session_factory

    # ------------------------------------------------------------------
    # Requesting candidates (design doc "Batch behavior")
    # ------------------------------------------------------------------

    async def request_candidates(
        self,
        *,
        document_ids: list[int],
        engines: list[str],
        provider_settings: dict[str, Any] | None = None,
        actor: str = "system",
    ) -> list[str]:
        """Create ``REQUESTED`` candidate rows for each (document, engine) pair.

        Validates batch caps and the provider allowlist up front. Does not
        run generation itself — callers (the API router) dispatch
        :meth:`run_generation_for_candidate` per candidate as a background
        task so the request returns immediately, matching the existing
        Stage-1/2 run pattern.
        """
        if not document_ids:
            raise ValueError("document_ids must not be empty")
        if len(document_ids) > settings.candidate_max_documents_per_batch:
            raise BatchCapExceeded(
                f"Batch of {len(document_ids)} documents exceeds the configured "
                f"cap of {settings.candidate_max_documents_per_batch}"
            )

        allowlist = set(settings.candidate_provider_allowlist)
        for engine in engines:
            if engine not in {e.value for e in EngineName}:
                raise UnsupportedProvider(f"Unknown provider '{engine}'")
            if engine not in allowlist:
                raise UnsupportedProvider(
                    f"Provider '{engine}' is not in the configured allowlist {sorted(allowlist)}"
                )

        candidate_ids: list[str] = []
        total_pages = 0
        db = self.session_factory()
        try:
            for document_id in document_ids:
                document = await self.client.get_document(document_id)
                pdf_bytes, _content_type = await self.client.get_document_preview(document_id)
                if not pdf_bytes:
                    raise ValueError(f"Could not fetch PDF for document {document_id}")

                pages = load_pdf_pages(pdf_bytes)
                total_pages += len(pages) or 1
                if total_pages > settings.candidate_max_total_pages_per_batch:
                    raise BatchCapExceeded(
                        f"Batch page count exceeds the configured cap of "
                        f"{settings.candidate_max_total_pages_per_batch}"
                    )

                source_checksum = _checksum(pdf_bytes)
                source_version_id = _document_version_key(document)
                _save_source_snapshot(document_id, source_checksum, pdf_bytes)

                for engine in engines:
                    candidate_id = str(uuid4())
                    now = datetime.utcnow()
                    row = OcrQualityCandidate(
                        candidate_id=candidate_id,
                        document_id=document_id,
                        source_version_id=source_version_id,
                        source_checksum=source_checksum,
                        state=CandidateState.REQUESTED.value,
                        engine=engine,
                        model_version=_providers()[engine].model_version(),
                        settings=provider_settings or {},
                        actor=actor,
                        requested_at=now,
                        expires_at=now
                        + timedelta(days=settings.candidate_retention_window_days),
                        retention_window_days=settings.candidate_retention_window_days,
                    )
                    db.add(row)
                    candidate_ids.append(candidate_id)
            db.commit()
        finally:
            db.close()

        return candidate_ids

    # ------------------------------------------------------------------
    # Generation (background)
    # ------------------------------------------------------------------

    async def run_generation_for_candidate(self, candidate_id: str) -> None:
        """Run the candidate's provider, score it, and compare it.

        Never raises — all failures are captured on the candidate row as
        ``FAILED`` with a ``failure_reason``. This never mutates Paperless.
        """
        db = self.session_factory()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one_or_none()
            if row is None:
                logger.warning("Candidate %s not found for generation", candidate_id)
                return
            if row.state != CandidateState.REQUESTED.value:
                logger.info(
                    "Candidate %s is in state %s; skipping generation", candidate_id, row.state
                )
                return

            row.state = CandidateState.RUNNING.value
            row.started_at = datetime.utcnow()
            document_id = row.document_id
            engine = row.engine
            source_checksum = row.source_checksum
            provider_settings = dict(row.settings or {})
            db.commit()
        finally:
            db.close()

        source_pdf_bytes = _load_source_snapshot(document_id, source_checksum)
        provider = _providers().get(engine)
        gen_result: CandidateGenResult
        if provider is None or source_pdf_bytes is None:
            gen_result = CandidateGenResult(
                success=False,
                error_message=f"Unknown provider or missing source snapshot for '{engine}'",
            )
        else:
            gen_result = await provider.generate_candidate(
                source_pdf_bytes,
                settings=provider_settings,
                timeout_seconds=settings.candidate_generation_timeout_seconds,
            )

        current_text: str | None = None
        if gen_result.success:
            # Best-effort: the live document's already-extracted OCR text,
            # fetched via a plain GET (same call used by get_candidate_text),
            # so the persisted comparison's text_diff_summary/similarity is
            # computed against real current text rather than None.
            try:
                current_text = await self.client.get_document_content(document_id)
            except Exception:  # noqa: BLE001 - comparison must not fail if this fetch fails
                logger.exception(
                    "Failed to fetch current document text for candidate %s comparison", candidate_id
                )

        db = self.session_factory()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one_or_none()
            if row is None:
                return
            # Cooperative cancellation: a reviewer may have marked this
            # FAILED/REJECTED while generation was running.
            if row.state != CandidateState.RUNNING.value:
                return

            if not gen_result.success:
                row.state = CandidateState.FAILED.value
                row.failure_reason = gen_result.error_message
                row.completed_at = datetime.utcnow()
                row.runtime_seconds = gen_result.runtime_seconds
                db.commit()
                return

            _save_artifacts(candidate_id, gen_result.candidate_pdf_bytes, gen_result.candidate_text)

            candidate_scores = None
            try:
                candidate_scores = assess_document(
                    pdf_bytes=gen_result.candidate_pdf_bytes,
                    text_content=gen_result.candidate_text,
                )
            except Exception:  # noqa: BLE001 - scoring is best-effort
                logger.exception("Scoring failed for candidate %s", candidate_id)

            current_scores = None
            try:
                current_scores = assess_document(pdf_bytes=source_pdf_bytes)
            except Exception:  # noqa: BLE001
                logger.exception("Scoring the current document failed for candidate %s", candidate_id)

            comparison = comparison_module.compare_candidate(
                current_pdf_bytes=source_pdf_bytes,
                current_text=current_text,
                current_overlay_score=current_scores.overlay_score if current_scores else None,
                current_machine_score=current_scores.machine_score if current_scores else None,
                candidate_pdf_bytes=gen_result.candidate_pdf_bytes,
                candidate_text=gen_result.candidate_text,
                candidate_overlay_score=candidate_scores.overlay_score if candidate_scores else None,
                candidate_machine_score=candidate_scores.machine_score if candidate_scores else None,
                expected_page_count=row.page_count or None,
            )

            row.state = CandidateState.READY.value
            row.completed_at = datetime.utcnow()
            row.runtime_seconds = gen_result.runtime_seconds
            row.cost_estimate = gen_result.cost_estimate
            row.provider_operation_id = gen_result.provider_operation_id
            row.page_count = gen_result.page_count
            row.candidate_pdf_checksum = (
                _checksum(gen_result.candidate_pdf_bytes) if gen_result.candidate_pdf_bytes else None
            )
            row.candidate_text_checksum = (
                _checksum(gen_result.candidate_text.encode("utf-8"))
                if gen_result.candidate_text
                else None
            )
            if candidate_scores is not None:
                row.overlay_score = candidate_scores.overlay_score
                row.machine_score = candidate_scores.machine_score
                row.scorer_version = candidate_scores.scorer_version

            row.comparison_id = comparison.comparison_id
            row.blocking_findings = [f.value for f in comparison.blocking_findings]
            row.text_diff_summary = comparison.text_diff_summary
            row.overlay_score_delta = comparison.overlay_score_delta
            row.machine_score_delta = comparison.machine_score_delta
            row.comparison_performed_at = comparison.performed_at

            db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Review (reads/writes OWL's own tables ONLY — never Paperless)
    # ------------------------------------------------------------------

    def list_candidates(
        self, *, document_id: int | None = None, state: str | None = None
    ) -> dict[str, Any]:
        db = self.session_factory()
        try:
            query = db.query(OcrQualityCandidate)
            if document_id is not None:
                query = query.filter(OcrQualityCandidate.document_id == document_id)
            if state is not None:
                query = query.filter(OcrQualityCandidate.state == state)
            rows = query.order_by(OcrQualityCandidate.requested_at.desc()).all()
            return {"candidates": [_candidate_summary(r) for r in rows]}
        finally:
            db.close()

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        db = self.session_factory()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one_or_none()
            return _candidate_detail(row) if row else None
        finally:
            db.close()

    async def get_candidate_text(self, candidate_id: str) -> dict[str, Any] | None:
        """Read-only side-by-side text for a candidate's comparison view.

        Fetches the *current* document's already-extracted OCR text from
        Paperless via a plain GET (``get_document_content`` — never a write)
        and the candidate's own extracted text from its on-disk artifact.
        Returns ``None`` if the candidate is unknown.
        """
        db = self.session_factory()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one_or_none()
            if row is None:
                return None
            document_id = row.document_id
        finally:
            db.close()

        current_text: str | None = None
        try:
            current_text = await self.client.get_document_content(document_id)
        except Exception:  # noqa: BLE001 - text fetch is best-effort for display
            logger.exception("Failed to fetch current document text for candidate %s", candidate_id)

        return {
            "candidate_id": candidate_id,
            "current_text": current_text,
            "candidate_text": _load_candidate_text(candidate_id),
        }

    async def decide_candidate(
        self,
        candidate_id: str,
        *,
        decision: Decision,
        reason: str | None,
        actor: str,
    ) -> dict[str, Any]:
        """Record a reviewer's accept/reject decision.

        This method makes ZERO Paperless write calls — it only issues a
        read-only GET to re-check the current document's checksum (design
        doc invariant #5: "acceptance requires a fresh comparison against the
        same source checksum"). Applying an accepted candidate to Paperless
        is out of scope for this slice.
        """
        db = self.session_factory()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one_or_none()
            if row is None:
                raise ValueError(f"Unknown candidate {candidate_id}")
            if row.state != CandidateState.READY.value:
                raise ValueError(
                    f"Candidate {candidate_id} is in state {row.state}; only a READY "
                    "candidate can be accepted or rejected"
                )

            if decision == Decision.ACCEPTED:
                document = await self.client.get_document(row.document_id)
                pdf_bytes, _content_type = await self.client.get_document_preview(row.document_id)
                live_checksum = _checksum(pdf_bytes) if pdf_bytes else None
                if live_checksum != row.source_checksum:
                    raise ValueError(
                        "The current document has changed since this candidate was "
                        "compared; regenerate the candidate before accepting "
                        "(design doc invariant: acceptance requires a fresh comparison)."
                    )
                _ = document  # re-read for parity with future apply-slice checks; unused here

            row.decision = decision.value
            row.decision_reason = reason
            row.decided_at = datetime.utcnow()
            row.state = (
                CandidateState.ACCEPTED.value
                if decision == Decision.ACCEPTED
                else CandidateState.REJECTED.value
            )
            db.commit()
            return _candidate_detail(row)
        finally:
            db.close()

    def cancel_candidate(self, candidate_id: str, *, reason: str = "cancelled_by_user") -> dict[str, Any]:
        """Best-effort cancellation of a REQUESTED/RUNNING candidate.

        Generation checks the candidate's state before writing results, so a
        candidate cancelled mid-run will not be overwritten back to READY.
        """
        db = self.session_factory()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one_or_none()
            if row is None:
                raise ValueError(f"Unknown candidate {candidate_id}")
            if row.state not in {s.value for s in CANCELLABLE_STATES}:
                raise ValueError(f"Candidate {candidate_id} is in state {row.state}; cannot cancel")
            row.state = CandidateState.FAILED.value
            row.failure_reason = reason
            row.completed_at = datetime.utcnow()
            db.commit()
            return _candidate_detail(row)
        finally:
            db.close()

    def expire_stale_candidates(self) -> int:
        """Mark past-retention candidates ``EXPIRED`` and delete their artifacts."""
        db = self.session_factory()
        try:
            now = datetime.utcnow()
            rows = (
                db.query(OcrQualityCandidate)
                .filter(
                    OcrQualityCandidate.expires_at < now,
                    OcrQualityCandidate.state.notin_(
                        [CandidateState.EXPIRED.value, CandidateState.ACCEPTED.value]
                    ),
                )
                .all()
            )
            for row in rows:
                row.state = CandidateState.EXPIRED.value
                _delete_artifacts(row.candidate_id)
            db.commit()
            return len(rows)
        finally:
            db.close()


def _candidate_summary(row: OcrQualityCandidate) -> dict[str, Any]:
    return {
        "candidate_id": row.candidate_id,
        "document_id": row.document_id,
        "state": row.state,
        "engine": row.engine,
        "model_version": row.model_version,
        "overlay_score": row.overlay_score,
        "machine_score": row.machine_score,
        "page_count": row.page_count,
        "requested_at": row.requested_at.isoformat() if row.requested_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "decision": row.decision,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


def _candidate_detail(row: OcrQualityCandidate) -> dict[str, Any]:
    return {
        **_candidate_summary(row),
        "source_version_id": row.source_version_id,
        "source_checksum": row.source_checksum,
        "settings": dict(row.settings or {}),
        "candidate_pdf_checksum": row.candidate_pdf_checksum,
        "candidate_text_checksum": row.candidate_text_checksum,
        "runtime_seconds": row.runtime_seconds,
        "cost_estimate": row.cost_estimate,
        "provider_operation_id": row.provider_operation_id,
        "scorer_version": row.scorer_version,
        "comparison": {
            "comparison_id": row.comparison_id,
            "blocking_findings": list(row.blocking_findings or []),
            "text_diff_summary": dict(row.text_diff_summary or {}),
            "overlay_score_delta": row.overlay_score_delta,
            "machine_score_delta": row.machine_score_delta,
            "performed_at": (
                row.comparison_performed_at.isoformat() if row.comparison_performed_at else None
            ),
        }
        if row.comparison_id
        else None,
        "actor": row.actor,
        "decision_reason": row.decision_reason,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "failure_reason": row.failure_reason,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "retention_window_days": row.retention_window_days,
    }


# ----------------------------------------------------------------------
# Source PDF snapshot — the *original* Paperless PDF captured at request
# time, kept alongside candidate artifacts so generation/comparison never
# need to re-fetch (or risk racing) Paperless mid-batch. Never written back.
# ----------------------------------------------------------------------


def _source_snapshot_path(document_id: int, checksum_value: str) -> Path:
    return _storage_dir() / f"source-{document_id}-{checksum_value[:16]}.pdf"


def _save_source_snapshot(document_id: int, checksum_value: str, pdf_bytes: bytes) -> None:
    _source_snapshot_path(document_id, checksum_value).write_bytes(pdf_bytes)


def _load_source_snapshot(document_id: int, checksum_value: str) -> bytes | None:
    path = _source_snapshot_path(document_id, checksum_value)
    return path.read_bytes() if path.exists() else None
