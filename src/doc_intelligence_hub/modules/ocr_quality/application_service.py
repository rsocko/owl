"""Apply an accepted OCR candidate to Paperless, and roll it back (issue #18, slice 2).

This is the only module in ``ocr_quality`` that ever writes to Paperless.
It implements the design doc's ("ocr-remediation-engine.md") apply sequence:

1. Acquire a document-scoped lock (``OcrApplicationLock``) so concurrent
   apply/rollback calls for the same document serialize, even across
   process restarts.
2. Re-check the source is still fresh (byte-for-byte, via SHA-256 of the
   current preview) — reject stale candidates with zero Paperless writes.
   A live checksum equal to the *candidate's own* PDF checksum is treated
   as fresh too (not stale): it means a prior attempt already landed this
   exact content in Paperless, so the apply resumes/verifies rather than
   rejecting its own successful work as "changed since staged".
3. Idempotency check against Paperless's own version history, so a resumed
   apply after a crash never re-uploads.
4. Upload the candidate PDF as a new Paperless *version* of the same
   document (``update_version``) — never a duplicate top-level document,
   never a content-only metadata PATCH. Poll the returned Celery task with
   bounded attempts.
5. Verify the new version is what Paperless now serves as latest.
6. Durably record downstream invalidation via
   ``AnalysisFreshnessService.record_invalidation`` (issue #114) BEFORE
   reporting the operation complete.
7. Persist the candidate as ACCEPTED with an audit trail row.

Rollback reverses this by deleting the version(s) newer than a target,
which is Paperless's own primitive for "make an older version current
again" (there is no separate "promote" call).

Every step that can fail before Paperless is actually mutated fails with
*zero* writes. Every step after Paperless is mutated is designed so a
failure never corrupts or removes the newly-current version — at worst it
leaves ``invalidation_recorded=False`` for a caller/retry to resolve.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from doc_intelligence_hub.core.paperless import PaperlessClient
from doc_intelligence_hub.modules.analysis_invalidation.models import InvalidationReason
from doc_intelligence_hub.modules.analysis_invalidation.service import AnalysisFreshnessService

from .candidate_models import CandidateState
from .candidate_service import _load_candidate_pdf_bytes
from .config import settings
from .database import OcrApplicationEvent, OcrApplicationLock, OcrQualityCandidate

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]

_TERMINAL_TASK_STATUSES = frozenset({"SUCCESS", "FAILURE"})


class ApplicationError(RuntimeError):
    """An apply/rollback attempt failed. ``retryable`` hints whether a caller
    should let the candidate be retried (True) or treat it as exhausted."""

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class LockHeldError(ApplicationError):
    """Another apply/rollback is already in progress for this document."""

    def __init__(self, document_id: int):
        super().__init__(
            f"An apply or rollback is already in progress for document {document_id}",
            retryable=True,
        )


def _checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class OcrCandidateApplicationService:
    """Applies an accepted candidate to Paperless, and supports rollback."""

    def __init__(
        self,
        client: PaperlessClient,
        session_factory: SessionFactory,
        *,
        freshness_service: AnalysisFreshnessService | None = None,
    ):
        self.client = client
        self.session_factory = session_factory
        # AnalysisFreshnessService owns its own (separate) database; it is
        # never given this module's session_factory. Injectable for tests.
        self._freshness_service = freshness_service or AnalysisFreshnessService()

    # ------------------------------------------------------------------
    # Document-scoped lock
    # ------------------------------------------------------------------

    def _acquire_lock(
        self, document_id: int, *, operation: str, candidate_id: str | None
    ) -> None:
        db = self.session_factory()
        try:
            now = datetime.utcnow()
            existing = (
                db.query(OcrApplicationLock).filter_by(document_id=document_id).one_or_none()
            )
            if existing is not None:
                if existing.expires_at > now:
                    raise LockHeldError(document_id)
                # Stale lock (holder presumably crashed) — reclaim it.
                db.delete(existing)
                db.flush()
            db.add(
                OcrApplicationLock(
                    document_id=document_id,
                    locked_by=candidate_id or operation,
                    locked_at=now,
                    expires_at=now + timedelta(seconds=settings.candidate_apply_lock_ttl_seconds),
                    operation=operation,
                    candidate_id=candidate_id,
                )
            )
            db.commit()
        finally:
            db.close()

    def _release_lock(self, document_id: int) -> None:
        db = self.session_factory()
        try:
            db.query(OcrApplicationLock).filter_by(document_id=document_id).delete()
            db.commit()
        finally:
            db.close()

    def _record_event(
        self,
        *,
        document_id: int,
        candidate_id: str | None,
        action: str,
        actor: str,
        outcome: str,
        error_message: str | None = None,
        paperless_task_id: str | None = None,
        previous_version_id: int | None = None,
        new_version_id: int | None = None,
        invalidation_recorded: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        db = self.session_factory()
        try:
            db.add(
                OcrApplicationEvent(
                    document_id=document_id,
                    candidate_id=candidate_id,
                    action=action,
                    actor=actor,
                    outcome=outcome,
                    error_message=error_message,
                    paperless_task_id=paperless_task_id,
                    previous_version_id=previous_version_id,
                    new_version_id=new_version_id,
                    invalidation_recorded=invalidation_recorded,
                    details=details or {},
                )
            )
            db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    async def apply_candidate(self, candidate_id: str, *, actor: str) -> dict[str, Any]:
        """Apply an ``APPLYING`` candidate to Paperless as the new latest version.

        Expects the candidate to already be in ``APPLYING`` state (the
        caller — ``decide_candidate`` — transitions ``READY -> APPLYING``
        synchronously as part of accept; this method does the actual work,
        normally dispatched as a background task).
        """
        db = self.session_factory()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one_or_none()
            if row is None:
                raise ValueError(f"Unknown candidate {candidate_id}")
            if row.state != CandidateState.APPLYING.value:
                raise ValueError(
                    f"Candidate {candidate_id} is in state {row.state}; expected APPLYING"
                )
            document_id = row.document_id
            source_checksum = row.source_checksum
            candidate_pdf_checksum = row.candidate_pdf_checksum
            existing_task_id = row.paperless_task_id
        finally:
            db.close()

        try:
            self._acquire_lock(
                document_id,
                operation="apply",
                candidate_id=candidate_id,
            )
        except LockHeldError as exc:
            # Contention, not a failed apply attempt — return the candidate
            # to READY without consuming one of its bounded apply_attempts,
            # so the same accept can be retried immediately.
            self._return_to_ready(candidate_id, str(exc))
            return {"error": str(exc)}

        try:
            result = await self._apply_locked(
                candidate_id=candidate_id,
                document_id=document_id,
                source_checksum=source_checksum,
                candidate_pdf_checksum=candidate_pdf_checksum,
                existing_task_id=existing_task_id,
                actor=actor,
            )
            return result
        except Exception as exc:  # noqa: BLE001 - never leave a candidate stuck in APPLYING
            logger.exception("Unexpected error applying candidate %s", candidate_id)
            self._fail_apply(candidate_id, f"Unexpected error: {exc}")
            self._record_event(
                document_id=document_id,
                candidate_id=candidate_id,
                action="apply",
                actor=actor,
                outcome="failure",
                error_message=str(exc),
            )
            return {"error": str(exc)}
        finally:
            self._release_lock(document_id)

    async def _apply_locked(
        self,
        *,
        candidate_id: str,
        document_id: int,
        source_checksum: str,
        candidate_pdf_checksum: str | None,
        existing_task_id: str | None,
        actor: str,
    ) -> dict[str, Any]:
        # Step 2: freshness re-check — byte-for-byte, zero writes so far.
        try:
            pdf_bytes, _content_type = await self.client.get_document_preview(document_id)
        except Exception as exc:  # noqa: BLE001 - surfaced as a bounded apply failure
            self._fail_apply(candidate_id, f"Failed to re-fetch source document: {exc}")
            return {"error": str(exc)}

        live_checksum = _checksum(pdf_bytes) if pdf_bytes else None
        # A live checksum matching the *candidate's* own PDF is not staleness
        # — it means a previous attempt already got this exact content into
        # Paperless (e.g. the upload succeeded but we crashed/failed before
        # persisting ACCEPTED). Resume via the idempotency check below rather
        # than rejecting a perfectly-applied resume as "stale".
        if live_checksum != source_checksum and live_checksum != candidate_pdf_checksum:
            self._fail_apply(
                candidate_id,
                "The source document changed since this candidate was staged "
                "(stale checksum) — regenerate the candidate before accepting.",
                terminal=True,
                failure_reason="stale_source",
            )
            self._record_event(
                document_id=document_id,
                candidate_id=candidate_id,
                action="apply",
                actor=actor,
                outcome="failure",
                error_message="stale_source",
            )
            return {"error": "stale_source"}

        # Step 3: idempotency check against Paperless's actual version history.
        try:
            versions_before = await self.client.list_document_versions(document_id)
        except Exception as exc:  # noqa: BLE001
            self._fail_apply(candidate_id, f"Failed to list Paperless versions: {exc}")
            return {"error": str(exc)}

        matching = next(
            (v for v in versions_before if v.get("checksum") == candidate_pdf_checksum),
            None,
        )

        task_id = existing_task_id
        if matching is None:
            candidate_pdf_bytes = _load_candidate_pdf_bytes(candidate_id)
            if not candidate_pdf_bytes:
                self._fail_apply(
                    candidate_id,
                    "Candidate PDF artifact is missing on disk; cannot apply.",
                    terminal=True,
                    failure_reason="missing_candidate_artifact",
                )
                return {"error": "missing_candidate_artifact"}

            if task_id is None:
                # Step 4: the actual Paperless write. Never retried internally
                # (upload_document_version uses max_attempts=1) — a failure
                # here means nothing was written, or Paperless itself is now
                # the source of truth for an in-flight consume we must poll.
                try:
                    task_id = await self.client.upload_document_version(
                        document_id,
                        f"ocr-candidate-{candidate_id}.pdf",
                        candidate_pdf_bytes,
                        version_label=f"owl-candidate-{candidate_id}",
                    )
                except Exception as exc:  # noqa: BLE001
                    self._fail_apply(candidate_id, f"Paperless upload failed: {exc}")
                    self._record_event(
                        document_id=document_id,
                        candidate_id=candidate_id,
                        action="apply",
                        actor=actor,
                        outcome="failure",
                        error_message=str(exc),
                    )
                    return {"error": str(exc)}
                self._persist_task_id(candidate_id, task_id)

            # Poll the task with bounded attempts/backoff.
            outcome = await self._poll_task(task_id)
            if outcome is None or outcome.get("status") != "SUCCESS":
                message = (
                    f"Paperless task {task_id} did not complete successfully: "
                    f"{outcome.get('status') if outcome else 'timed out'}"
                )
                self._fail_apply(candidate_id, message)
                self._record_event(
                    document_id=document_id,
                    candidate_id=candidate_id,
                    action="apply",
                    actor=actor,
                    outcome="failure",
                    error_message=message,
                    paperless_task_id=task_id,
                )
                return {"error": message}

            versions_after = await self.client.list_document_versions(document_id)
            matching = next(
                (v for v in versions_after if v.get("checksum") == candidate_pdf_checksum),
                None,
            )
            if matching is None:
                # Task reported success but we can't find our content — do
                # NOT assume the current version is corrupt; just surface a
                # bounded failure. The previously-current version is untouched.
                message = (
                    "Paperless task succeeded but no version with the candidate's "
                    "checksum was found afterward."
                )
                self._fail_apply(candidate_id, message)
                self._record_event(
                    document_id=document_id,
                    candidate_id=candidate_id,
                    action="apply",
                    actor=actor,
                    outcome="failure",
                    error_message=message,
                    paperless_task_id=task_id,
                )
                return {"error": message}

        new_version_id = matching["id"]

        # Step 5/7: confirm Paperless now actually serves this content as
        # latest (preview/content/download all resolve to latest by design)
        # — checksum-match the bytes rather than trusting any single field.
        try:
            confirm_bytes, _ = await self.client.get_document_preview(document_id)
        except Exception as exc:  # noqa: BLE001
            self._fail_apply(candidate_id, f"Failed to verify applied version: {exc}")
            return {"error": str(exc)}

        if _checksum(confirm_bytes) != candidate_pdf_checksum:
            message = (
                "Paperless does not yet serve the newly-applied version as latest "
                "(checksum mismatch on verification)."
            )
            self._fail_apply(candidate_id, message)
            self._record_event(
                document_id=document_id,
                candidate_id=candidate_id,
                action="apply",
                actor=actor,
                outcome="failure",
                error_message=message,
                paperless_task_id=task_id,
                new_version_id=new_version_id,
            )
            return {"error": message}

        # Best-effort audit label — never fails the apply.
        try:
            await self.client.label_document_version(
                document_id, new_version_id, f"owl-candidate-{candidate_id}"
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to label Paperless version %s for candidate %s (non-fatal)",
                new_version_id,
                candidate_id,
            )

        # Step 6: durably record downstream invalidation BEFORE reporting
        # complete (issue #114). The Paperless write is already valid and is
        # never undone if this fails after retries — the candidate is still
        # marked ACCEPTED but flagged so freshness can be retried later.
        invalidation_recorded = await self._record_invalidation_with_retries(
            document_id=document_id,
            accepted_checksum=candidate_pdf_checksum,
            reason=InvalidationReason.VERSION_CHANGED,
            actor=actor,
        )

        self._finish_apply(
            candidate_id,
            new_version_id=new_version_id,
            invalidation_recorded=invalidation_recorded,
        )
        self._record_event(
            document_id=document_id,
            candidate_id=candidate_id,
            action="apply",
            actor=actor,
            outcome="success",
            paperless_task_id=task_id,
            new_version_id=new_version_id,
            invalidation_recorded=invalidation_recorded,
        )
        return {
            "candidate_id": candidate_id,
            "state": CandidateState.ACCEPTED.value,
            "applied_paperless_version_id": new_version_id,
            "invalidation_recorded": invalidation_recorded,
        }

    async def _poll_task(self, task_id: str) -> dict[str, Any] | None:
        deadline = asyncio.get_event_loop().time() + settings.candidate_apply_task_poll_timeout_seconds
        last: dict[str, Any] | None = None
        while asyncio.get_event_loop().time() < deadline:
            try:
                last = await self.client.get_task(task_id)
            except Exception:  # noqa: BLE001 - transient poll error, keep trying until deadline
                logger.exception("Error polling Paperless task %s", task_id)
                last = None
            if last is not None and last.get("status") in _TERMINAL_TASK_STATUSES:
                return last
            await asyncio.sleep(settings.candidate_apply_task_poll_seconds)
        return last

    async def _record_invalidation_with_retries(
        self,
        *,
        document_id: int,
        accepted_checksum: str | None,
        reason: InvalidationReason,
        actor: str,
        max_attempts: int = 3,
    ) -> bool:
        for attempt in range(1, max_attempts + 1):
            try:
                self._freshness_service.record_invalidation(
                    document_id=document_id,
                    accepted_checksum=accepted_checksum or "",
                    reason=reason,
                    triggered_by=actor,
                )
                return True
            except Exception:  # noqa: BLE001
                logger.exception(
                    "record_invalidation attempt %s/%s failed for document %s",
                    attempt,
                    max_attempts,
                    document_id,
                )
                if attempt < max_attempts:
                    await asyncio.sleep(0.5 * attempt)
        return False

    # ------------------------------------------------------------------
    # Candidate row mutation helpers
    # ------------------------------------------------------------------

    def _persist_task_id(self, candidate_id: str, task_id: str) -> None:
        db = self.session_factory()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one_or_none()
            if row is not None:
                row.paperless_task_id = task_id
                db.commit()
        finally:
            db.close()

    def _return_to_ready(self, candidate_id: str, error_message: str) -> None:
        """Return an APPLYING candidate to READY without penalizing apply_attempts.

        Used for lock contention only — the candidate never got far enough
        to make (or attempt) a Paperless write.
        """
        db = self.session_factory()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one_or_none()
            if row is None:
                return
            row.apply_last_error = error_message
            row.state = CandidateState.READY.value
            db.commit()
        finally:
            db.close()

    def _fail_apply(
        self,
        candidate_id: str,
        error_message: str,
        *,
        terminal: bool = False,
        failure_reason: str | None = None,
    ) -> None:
        db = self.session_factory()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one_or_none()
            if row is None:
                return
            row.apply_attempts = (row.apply_attempts or 0) + 1
            row.apply_last_error = error_message
            if terminal or row.apply_attempts >= settings.candidate_max_apply_attempts:
                row.state = CandidateState.FAILED.value
                row.failure_reason = failure_reason or "apply_attempts_exceeded"
                row.completed_at = datetime.utcnow()
            else:
                # Bounded retry: return to READY so the same candidate can
                # be re-accepted. Never leaves Paperless mid-write — every
                # failure path above runs before or after a confirmed
                # Paperless mutation, never during one.
                row.state = CandidateState.READY.value
            db.commit()
        finally:
            db.close()

    def _finish_apply(
        self, candidate_id: str, *, new_version_id: int, invalidation_recorded: bool
    ) -> None:
        db = self.session_factory()
        try:
            row = db.query(OcrQualityCandidate).filter_by(candidate_id=candidate_id).one_or_none()
            if row is None:
                return
            row.state = CandidateState.ACCEPTED.value
            row.applied_paperless_version_id = new_version_id
            row.applied_at = datetime.utcnow()
            row.invalidation_recorded = invalidation_recorded
            db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    async def rollback(
        self, document_id: int, *, actor: str, target_candidate_id: str | None = None
    ) -> dict[str, Any]:
        """Restore a prior Paperless version for ``document_id``.

        Synchronous (not backgrounded) — a rollback is a small, bounded
        number of ``DELETE`` calls, not a re-OCR cycle.
        """
        try:
            self._acquire_lock(
                document_id, operation="rollback", candidate_id=target_candidate_id
            )
        except LockHeldError as exc:
            return {"error": str(exc)}

        try:
            return await self._rollback_locked(
                document_id=document_id, actor=actor, target_candidate_id=target_candidate_id
            )
        finally:
            self._release_lock(document_id)

    async def _rollback_locked(
        self, *, document_id: int, actor: str, target_candidate_id: str | None
    ) -> dict[str, Any]:
        db = self.session_factory()
        try:
            target_checksum: str | None = None
            if target_candidate_id:
                target_row = (
                    db.query(OcrQualityCandidate)
                    .filter_by(candidate_id=target_candidate_id, document_id=document_id)
                    .one_or_none()
                )
                if target_row is None or target_row.state != CandidateState.ACCEPTED.value:
                    return {
                        "error": (
                            f"target_candidate_id {target_candidate_id!r} is not an "
                            "accepted candidate for this document"
                        )
                    }
                target_checksum = target_row.candidate_pdf_checksum
            else:
                # Default: the version current immediately before the most
                # recently accepted candidate for this document.
                previous = (
                    db.query(OcrQualityCandidate)
                    .filter_by(document_id=document_id, state=CandidateState.ACCEPTED.value)
                    .order_by(OcrQualityCandidate.applied_at.desc())
                    .first()
                )
                target_checksum = previous.source_checksum if previous else None
        finally:
            db.close()

        try:
            versions = await self.client.list_document_versions(document_id)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"Failed to list Paperless versions: {exc}"}

        if target_checksum is None:
            # No accepted candidate on record — fall back to the root/
            # original version (the one Paperless refuses to ever delete).
            target = next((v for v in versions if v.get("is_root")), None)
        else:
            target = next((v for v in versions if v.get("checksum") == target_checksum), None)

        if target is None:
            return {"error": "Could not resolve a rollback target version in Paperless."}

        target_id = target["id"]
        to_delete = [v for v in versions if v["id"] != target_id and not v.get("is_root")]

        current_version_id = None
        for version in to_delete:
            try:
                result = await self.client.delete_document_version(document_id, version["id"])
                current_version_id = result.get("current_version_id")
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    # Already gone — treat as already-rolled-back, not a failure.
                    continue
                self._record_event(
                    document_id=document_id,
                    candidate_id=target_candidate_id,
                    action="rollback",
                    actor=actor,
                    outcome="failure",
                    error_message=str(exc),
                )
                return {"error": f"Failed to delete Paperless version {version['id']}: {exc}"}

        try:
            confirm_bytes, _ = await self.client.get_document_preview(document_id)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"Failed to verify rollback: {exc}"}

        if target_checksum is not None and _checksum(confirm_bytes) != target_checksum:
            message = "Paperless does not serve the rollback target as latest after deletion."
            self._record_event(
                document_id=document_id,
                candidate_id=target_candidate_id,
                action="rollback",
                actor=actor,
                outcome="failure",
                error_message=message,
                new_version_id=current_version_id,
            )
            return {"error": message}

        invalidation_recorded = await self._record_invalidation_with_retries(
            document_id=document_id,
            accepted_checksum=target_checksum,
            reason=InvalidationReason.ROLLBACK,
            actor=actor,
        )

        self._record_event(
            document_id=document_id,
            candidate_id=target_candidate_id,
            action="rollback",
            actor=actor,
            outcome="success",
            new_version_id=target_id,
            invalidation_recorded=invalidation_recorded,
        )

        return {
            "document_id": document_id,
            "current_version_id": target_id,
            "invalidation_recorded": invalidation_recorded,
        }
