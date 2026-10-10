"""Idempotent receipt intake orchestration and provenance recording."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import uuid
from collections.abc import AsyncIterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from doc_intelligence_hub.core.resilience import PaperlessError

from . import database
from .config import settings
from .models import (
    AttemptState,
    ExternalReplicaResult,
    IntakeMetadata,
    IntakeOutcome,
    IntakeResult,
    ProposalKind,
    ProposalState,
)

ALLOWED_MEDIA_TYPES = frozenset({"application/pdf", "image/jpeg", "image/png", "image/tiff"})
_SUCCESS_TASK_STATES = frozenset({"success", "succeeded"})
_FAILURE_TASK_STATES = frozenset({"failure", "failed", "revoked"})


class IntakeValidationError(ValueError):
    pass


class PaperlessIntakeGateway(Protocol):
    async def upload_document(self, path: Path, media_type: str) -> str: ...

    async def get_task(self, task_id: str) -> dict[str, Any] | None: ...

    async def find_document_by_checksum(self, checksum: str) -> dict[str, Any] | None: ...


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _ref(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _deployment_fingerprint() -> str:
    return hashlib.sha256(settings.paperless_deployment_id.encode()).hexdigest()


def _paperless_document_ref(document_id: Any) -> str:
    if isinstance(document_id, bool) or not isinstance(document_id, (str, int)):
        raise PaperlessError("Paperless task did not return a document reference")
    value = str(document_id).strip()
    if not value:
        raise PaperlessError("Paperless task returned an empty document reference")
    return value


class ReceiptIntakeService:
    def __init__(self, gateway: PaperlessIntakeGateway):
        self.gateway = gateway
        database.init_db()

    async def intake(
        self,
        metadata: IntakeMetadata,
        body: AsyncIterable[bytes],
        *,
        media_type: str,
    ) -> IntakeResult:
        if media_type not in ALLOWED_MEDIA_TYPES:
            raise IntakeValidationError("unsupported_media_type")

        temporary_path: Path | None = None
        try:
            temporary_path, blob_sha256, byte_count = await self._spool(body)
            prior = self._get_attempt_by_occurrence(metadata.source_occurrence_id)
            if prior is not None:
                if (
                    prior["source_channel"] != metadata.source_channel
                    or prior["source_occurrence_version"] != metadata.source_occurrence_version
                ):
                    raise IntakeValidationError("source_occurrence_identity_conflict")
                if prior["blob_sha256"] != blob_sha256:
                    raise IntakeValidationError("source_occurrence_hash_conflict")
                if prior["attempt_state"] == AttemptState.RETRYABLE:
                    claim = self._claim_retry(prior["intake_ref"])
                    if claim == "exhausted":
                        return self._set_terminal(
                            prior["intake_ref"],
                            state=AttemptState.FAILED,
                            outcome=IntakeOutcome.UPLOAD_FAILED,
                            reason_code="paperless_retry_budget_exhausted",
                            retry_safe=False,
                        )
                    if claim != "claimed":
                        return self.get_status(prior["intake_ref"])
                    return await self._submit_and_resolve(
                        prior["intake_ref"],
                        metadata,
                        temporary_path,
                        blob_sha256,
                        semantic_candidate_ref=(
                            candidate["canonical_ref"]
                            if (candidate := self._find_semantic_candidate(metadata))
                            else None
                        ),
                    )
                return await self._resolve_prior(prior)

            exact = self._find_canonical_by_hash(blob_sha256)
            if exact is not None:
                return self._record_reuse(
                    metadata,
                    blob_sha256=blob_sha256,
                    byte_count=byte_count,
                    media_type=media_type,
                    canonical_ref=exact["canonical_ref"],
                    outcome=IntakeOutcome.EXACT_HASH_REUSED,
                    reason_code="exact_hash_match",
                )

            normalized = self._find_normalized_candidate(metadata)
            if normalized is not None:
                return self._record_normalized_review(
                    metadata,
                    blob_sha256=blob_sha256,
                    byte_count=byte_count,
                    media_type=media_type,
                    candidate_ref=normalized["canonical_ref"],
                )

            semantic = self._find_semantic_candidate(metadata)
            intake_ref, created = self._record_pending(
                metadata,
                blob_sha256=blob_sha256,
                byte_count=byte_count,
                media_type=media_type,
            )
            if not created:
                return self.get_status(intake_ref)
            return await self._submit_and_resolve(
                intake_ref,
                metadata,
                temporary_path,
                blob_sha256,
                semantic_candidate_ref=semantic["canonical_ref"] if semantic else None,
            )
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    async def _spool(self, body: AsyncIterable[bytes]) -> tuple[Path, str, int]:
        digest = hashlib.sha256()
        total = 0
        descriptor, raw_path = tempfile.mkstemp(prefix="owl-receipt-", suffix=".tmp")
        path = Path(raw_path)
        completed = False
        try:
            with os.fdopen(descriptor, "wb") as output:
                async for chunk in body:
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > settings.max_bytes:
                        raise IntakeValidationError("payload_too_large")
                    digest.update(chunk)
                    output.write(chunk)
            if total == 0:
                raise IntakeValidationError("empty_payload")
            completed = True
            return path, digest.hexdigest(), total
        finally:
            if not completed:
                path.unlink(missing_ok=True)

    async def _resolve_prior(self, row: Any) -> IntakeResult:
        if row["attempt_state"] == AttemptState.UNKNOWN:
            try:
                existing = await self.gateway.find_document_by_checksum(row["blob_sha256"])
            except (PaperlessError, httpx.HTTPError, TimeoutError, ConnectionError):
                return self._result_from_row(row)
            if existing is not None:
                return self._accept_existing_paperless_document(
                    row["intake_ref"],
                    existing["id"],
                    IntakeOutcome.SOURCE_OCCURRENCE_REUSED,
                    "unknown_outcome_verified_by_hash",
                )
            return self._result_from_row(row)
        result = self.get_status(row["intake_ref"])
        if result.attempt_state == AttemptState.ACCEPTED:
            return result.model_copy(update={"outcome": IntakeOutcome.SOURCE_OCCURRENCE_REUSED})
        return result

    async def _submit_and_resolve(
        self,
        intake_ref: str,
        metadata: IntakeMetadata,
        path: Path,
        blob_sha256: str,
        *,
        semantic_candidate_ref: str | None,
    ) -> IntakeResult:
        self._increment_attempt_count(intake_ref)
        try:
            task_ref = await self.gateway.upload_document(path, self._media_type(intake_ref))
        except (PaperlessError, httpx.HTTPError, TimeoutError, ConnectionError) as exc:
            if self._is_unknown_upload_error(exc):
                return self._set_terminal(
                    intake_ref,
                    state=AttemptState.UNKNOWN,
                    outcome=IntakeOutcome.UPLOAD_OUTCOME_UNKNOWN,
                    reason_code="paperless_upload_outcome_unknown",
                    retry_safe=False,
                )
            if self._is_retryable_upload_error(exc):
                return self._set_terminal(
                    intake_ref,
                    state=AttemptState.RETRYABLE,
                    outcome=IntakeOutcome.UPLOAD_RETRYABLE,
                    reason_code="paperless_upload_retryable",
                    retry_safe=True,
                )
            return self._set_terminal(
                intake_ref,
                state=AttemptState.FAILED,
                outcome=IntakeOutcome.UPLOAD_FAILED,
                reason_code="paperless_upload_rejected",
                retry_safe=False,
            )

        self._set_task_ref(intake_ref, task_ref)
        for attempt in range(settings.task_poll_attempts):
            try:
                task = await self.gateway.get_task(task_ref)
            except (PaperlessError, httpx.HTTPError, TimeoutError, ConnectionError):
                return self._set_terminal(
                    intake_ref,
                    state=AttemptState.UNKNOWN,
                    outcome=IntakeOutcome.UPLOAD_OUTCOME_UNKNOWN,
                    reason_code="paperless_task_query_unknown",
                    retry_safe=False,
                )
            if task is not None:
                status = str(task.get("status", "")).lower()
                if status in _SUCCESS_TASK_STATES:
                    document_ref = task.get("related_document") or task.get("document_id")
                    result = self._accept_new_document(
                        intake_ref,
                        metadata,
                        blob_sha256,
                        _paperless_document_ref(document_ref),
                        semantic_candidate_ref=semantic_candidate_ref,
                    )
                    return result
                if status in _FAILURE_TASK_STATES:
                    duplicate_ref = task.get("related_document") or task.get("document_id")
                    if self._task_is_duplicate(task) and duplicate_ref is not None:
                        return self._accept_existing_paperless_document(
                            intake_ref,
                            duplicate_ref,
                            IntakeOutcome.PAPERLESS_DUPLICATE,
                            "paperless_duplicate_response",
                        )
                    return self._set_terminal(
                        intake_ref,
                        state=AttemptState.RETRYABLE,
                        outcome=IntakeOutcome.UPLOAD_RETRYABLE,
                        reason_code="paperless_task_retryable",
                        retry_safe=True,
                    )
            if attempt + 1 < settings.task_poll_attempts:
                await asyncio.sleep(settings.task_poll_interval_seconds)

        return self._set_terminal(
            intake_ref,
            state=AttemptState.UNKNOWN,
            outcome=IntakeOutcome.UPLOAD_OUTCOME_UNKNOWN,
            reason_code="paperless_task_not_terminal",
            retry_safe=False,
        )

    def _record_pending(
        self,
        metadata: IntakeMetadata,
        *,
        blob_sha256: str,
        byte_count: int,
        media_type: str,
    ) -> tuple[str, bool]:
        intake_ref = _ref("intake")
        timestamp = _now()
        with database.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM receipt_intake_attempts WHERE source_occurrence_id = ?",
                (metadata.source_occurrence_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["source_channel"] != metadata.source_channel
                    or existing["source_occurrence_version"] != metadata.source_occurrence_version
                ):
                    raise IntakeValidationError("source_occurrence_identity_conflict")
                if existing["blob_sha256"] != blob_sha256:
                    raise IntakeValidationError("source_occurrence_hash_conflict")
                return existing["intake_ref"], False
            connection.execute(
                """
                INSERT INTO receipt_intake_attempts (
                    intake_ref, source_channel, source_occurrence_version,
                    source_occurrence_id, connector_ref,
                    source_observed_at, blob_sha256, byte_count, media_type,
                    transform_version, normalized_content_fingerprint,
                    normalized_content_version, semantic_fingerprint,
                    semantic_fingerprint_version, attempt_state, outcome, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intake_ref,
                    metadata.source_channel,
                    metadata.source_occurrence_version,
                    metadata.source_occurrence_id,
                    metadata.connector_ref,
                    metadata.source_observed_at.isoformat()
                    if metadata.source_observed_at
                    else None,
                    blob_sha256,
                    byte_count,
                    media_type,
                    metadata.transform_version,
                    metadata.normalized_content_fingerprint,
                    metadata.normalized_content_version,
                    metadata.semantic_fingerprint,
                    metadata.semantic_fingerprint_version,
                    AttemptState.PENDING,
                    IntakeOutcome.UPLOAD_OUTCOME_UNKNOWN,
                    timestamp,
                    timestamp,
                ),
            )
        return intake_ref, True

    def _record_reuse(
        self,
        metadata: IntakeMetadata,
        *,
        blob_sha256: str,
        byte_count: int,
        media_type: str,
        canonical_ref: str,
        outcome: IntakeOutcome,
        reason_code: str,
    ) -> IntakeResult:
        intake_ref, created = self._record_pending(
            metadata,
            blob_sha256=blob_sha256,
            byte_count=byte_count,
            media_type=media_type,
        )
        if not created:
            return self.get_status(intake_ref)
        with database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE receipt_intake_attempts
                SET attempt_state = ?, outcome = ?, canonical_ref = ?, updated_at = ?
                WHERE intake_ref = ?
                """,
                (AttemptState.ACCEPTED, outcome, canonical_ref, _now(), intake_ref),
            )
            self._audit(
                connection,
                intake_ref=intake_ref,
                canonical_ref=canonical_ref,
                event_type="provenance_attached",
                reason_code=reason_code,
            )
        return self.get_status(intake_ref)

    def _record_normalized_review(
        self,
        metadata: IntakeMetadata,
        *,
        blob_sha256: str,
        byte_count: int,
        media_type: str,
        candidate_ref: str,
    ) -> IntakeResult:
        intake_ref, created = self._record_pending(
            metadata,
            blob_sha256=blob_sha256,
            byte_count=byte_count,
            media_type=media_type,
        )
        if not created:
            return self.get_status(intake_ref)
        proposal_ref = _ref("review")
        with database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE receipt_intake_attempts
                SET attempt_state = ?, outcome = ?, updated_at = ?
                WHERE intake_ref = ?
                """,
                (
                    AttemptState.REVIEW_REQUIRED,
                    IntakeOutcome.NORMALIZED_CONTENT_REVIEW,
                    _now(),
                    intake_ref,
                ),
            )
            connection.execute(
                """
                INSERT INTO receipt_match_proposals (
                    proposal_ref, intake_ref, candidate_canonical_ref, proposal_kind,
                    proposal_state, reason_code, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_ref,
                    intake_ref,
                    candidate_ref,
                    ProposalKind.NORMALIZED_CONTENT,
                    ProposalState.PENDING,
                    "strong_normalized_content_match",
                    _now(),
                ),
            )
            self._audit(
                connection,
                intake_ref=intake_ref,
                canonical_ref=candidate_ref,
                event_type="review_proposed",
                reason_code="strong_normalized_content_match",
            )
        result = self.get_status(intake_ref)
        return result.model_copy(
            update={
                "review_ref": proposal_ref,
                "reason_codes": ["strong_normalized_content_match"],
            }
        )

    def _accept_new_document(
        self,
        intake_ref: str,
        metadata: IntakeMetadata,
        blob_sha256: str,
        paperless_document_ref: str,
        *,
        semantic_candidate_ref: str | None,
    ) -> IntakeResult:
        canonical_ref = _ref("document")
        outcome = (
            IntakeOutcome.SEMANTIC_REVIEW_CANDIDATE
            if semantic_candidate_ref
            else IntakeOutcome.NEW_CANONICAL
        )
        review_ref = _ref("review") if semantic_candidate_ref else None
        with database.transaction(immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT canonical_ref FROM receipt_canonical_documents
                WHERE deployment_fingerprint = ? AND blob_sha256 = ?
                """,
                (_deployment_fingerprint(), blob_sha256),
            ).fetchone()
            if existing is not None:
                canonical_ref = existing["canonical_ref"]
                outcome = IntakeOutcome.EXACT_HASH_REUSED
            else:
                connection.execute(
                    """
                    INSERT INTO receipt_canonical_documents (
                        canonical_ref, deployment_fingerprint, paperless_document_ref,
                        blob_sha256, normalized_content_fingerprint,
                        normalized_content_version, semantic_fingerprint,
                        semantic_fingerprint_version, transform_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        canonical_ref,
                        _deployment_fingerprint(),
                        paperless_document_ref,
                        blob_sha256,
                        metadata.normalized_content_fingerprint,
                        metadata.normalized_content_version,
                        metadata.semantic_fingerprint,
                        metadata.semantic_fingerprint_version,
                        metadata.transform_version,
                        _now(),
                    ),
                )
            connection.execute(
                """
                UPDATE receipt_intake_attempts
                SET attempt_state = ?, outcome = ?, canonical_ref = ?, updated_at = ?
                WHERE intake_ref = ?
                """,
                (AttemptState.ACCEPTED, outcome, canonical_ref, _now(), intake_ref),
            )
            self._audit(
                connection,
                intake_ref=intake_ref,
                canonical_ref=canonical_ref,
                event_type="canonical_accepted",
                reason_code="paperless_acknowledged",
            )
            if semantic_candidate_ref and review_ref:
                connection.execute(
                    """
                    INSERT INTO receipt_match_proposals (
                        proposal_ref, intake_ref, candidate_canonical_ref, proposal_kind,
                        proposal_state, reason_code, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review_ref,
                        intake_ref,
                        semantic_candidate_ref,
                        ProposalKind.SEMANTIC,
                        ProposalState.PENDING,
                        "semantic_similarity_only",
                        _now(),
                    ),
                )
        result = self.get_status(intake_ref)
        if review_ref:
            return result.model_copy(
                update={
                    "review_ref": review_ref,
                    "reason_codes": ["paperless_acknowledged", "semantic_similarity_only"],
                }
            )
        return result.model_copy(update={"reason_codes": ["paperless_acknowledged"]})

    def _accept_existing_paperless_document(
        self,
        intake_ref: str,
        paperless_document_ref: Any,
        outcome: IntakeOutcome,
        reason_code: str,
    ) -> IntakeResult:
        paperless_ref = _paperless_document_ref(paperless_document_ref)
        with database.transaction(immediate=True) as connection:
            attempt = connection.execute(
                "SELECT * FROM receipt_intake_attempts WHERE intake_ref = ?", (intake_ref,)
            ).fetchone()
            existing = connection.execute(
                """
                SELECT canonical_ref FROM receipt_canonical_documents
                WHERE deployment_fingerprint = ? AND paperless_document_ref = ?
                """,
                (_deployment_fingerprint(), paperless_ref),
            ).fetchone()
            canonical_ref = existing["canonical_ref"] if existing else _ref("document")
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO receipt_canonical_documents (
                        canonical_ref, deployment_fingerprint, paperless_document_ref,
                        blob_sha256, normalized_content_fingerprint,
                        normalized_content_version, semantic_fingerprint,
                        semantic_fingerprint_version, transform_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        canonical_ref,
                        _deployment_fingerprint(),
                        paperless_ref,
                        attempt["blob_sha256"],
                        attempt["normalized_content_fingerprint"],
                        attempt["normalized_content_version"],
                        attempt["semantic_fingerprint"],
                        attempt["semantic_fingerprint_version"],
                        attempt["transform_version"],
                        _now(),
                    ),
                )
            connection.execute(
                """
                UPDATE receipt_intake_attempts
                SET attempt_state = ?, outcome = ?, canonical_ref = ?, updated_at = ?
                WHERE intake_ref = ?
                """,
                (AttemptState.ACCEPTED, outcome, canonical_ref, _now(), intake_ref),
            )
            self._audit(
                connection,
                intake_ref=intake_ref,
                canonical_ref=canonical_ref,
                event_type="paperless_duplicate_resolved",
                reason_code=reason_code,
            )
        return self.get_status(intake_ref).model_copy(update={"reason_codes": [reason_code]})

    def reject_proposal(self, review_ref: str) -> None:
        with database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM receipt_match_proposals WHERE proposal_ref = ?", (review_ref,)
            ).fetchone()
            if row is None:
                raise KeyError(review_ref)
            connection.execute(
                """
                UPDATE receipt_match_proposals
                SET proposal_state = ?, resolved_at = ?
                WHERE proposal_ref = ?
                """,
                (ProposalState.REJECTED, _now(), review_ref),
            )
            if row["proposal_kind"] == ProposalKind.NORMALIZED_CONTENT:
                connection.execute(
                    """
                    UPDATE receipt_intake_attempts
                    SET attempt_state = ?, outcome = ?, failure_reason_code = ?, updated_at = ?
                    WHERE intake_ref = ? AND attempt_state = ?
                    """,
                    (
                        AttemptState.RETRYABLE,
                        IntakeOutcome.UPLOAD_RETRYABLE,
                        "normalized_match_rejected",
                        _now(),
                        row["intake_ref"],
                        AttemptState.REVIEW_REQUIRED,
                    ),
                )
            self._audit(
                connection,
                intake_ref=row["intake_ref"],
                canonical_ref=row["candidate_canonical_ref"],
                event_type="review_rejected",
                reason_code=f"{row['proposal_kind']}_rejected",
            )

    def record_external_replica(
        self,
        *,
        canonical_ref: str,
        external_system: str,
        external_replica_ref: str,
        lifecycle_state: str,
        source_revision: str | None = None,
        expected_revision: int | None = None,
    ) -> ExternalReplicaResult:
        """Upsert bounded replica lifecycle state without retaining remote payloads."""
        from .models import ExternalReplicaState

        state = ExternalReplicaState(lifecycle_state)
        if not external_system or len(external_system) > 64:
            raise ValueError("external_system must be between 1 and 64 characters")
        if not external_replica_ref or len(external_replica_ref) > 200:
            raise ValueError("external_replica_ref must be between 1 and 200 characters")
        timestamp = _now()
        with database.transaction(immediate=True) as connection:
            canonical = connection.execute(
                "SELECT canonical_ref FROM receipt_canonical_documents WHERE canonical_ref = ?",
                (canonical_ref,),
            ).fetchone()
            if canonical is None:
                raise KeyError(canonical_ref)
            existing = connection.execute(
                """
                SELECT replica_ref, canonical_ref FROM receipt_external_replicas
                WHERE external_system = ? AND external_replica_ref = ?
                """,
                (external_system, external_replica_ref),
            ).fetchone()
            replica_ref = existing["replica_ref"] if existing else _ref("replica")
            if existing:
                if existing["canonical_ref"] != canonical_ref:
                    raise ValueError("external_replica_canonical_conflict")
                current = connection.execute(
                    "SELECT revision FROM receipt_external_replicas WHERE replica_ref = ?",
                    (replica_ref,),
                ).fetchone()
                if expected_revision is None or expected_revision != current["revision"]:
                    raise ValueError("external_replica_revision_conflict")
                revision = current["revision"] + 1
                connection.execute(
                    """
                    UPDATE receipt_external_replicas
                    SET lifecycle_state = ?, source_revision = ?, revision = ?, updated_at = ?
                    WHERE replica_ref = ?
                    """,
                    (state, source_revision, revision, timestamp, replica_ref),
                )
            else:
                if expected_revision not in {None, 0}:
                    raise ValueError("external_replica_revision_conflict")
                revision = 1
                connection.execute(
                    """
                    INSERT INTO receipt_external_replicas (
                        replica_ref, canonical_ref, external_system, external_replica_ref,
                        lifecycle_state, source_revision, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        replica_ref,
                        canonical_ref,
                        external_system,
                        external_replica_ref,
                        state,
                        source_revision,
                        revision,
                        timestamp,
                        timestamp,
                    ),
                )
            self._audit(
                connection,
                intake_ref=None,
                canonical_ref=canonical_ref,
                event_type="external_replica_state_changed",
                reason_code=f"external_replica_{state}",
            )
        return ExternalReplicaResult(
            replica_ref=replica_ref,
            canonical_document_ref=canonical_ref,
            lifecycle_state=state,
            revision=revision,
        )

    async def reconcile_unknown(self, intake_ref: str) -> IntakeResult:
        """Resolve an unknown upload by exact checksum lookup; never re-upload."""
        with database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM receipt_intake_attempts WHERE intake_ref = ?", (intake_ref,)
            ).fetchone()
        if row is None:
            raise KeyError(intake_ref)
        if row["attempt_state"] != AttemptState.UNKNOWN:
            return self._result_from_row(row)
        try:
            existing = await self.gateway.find_document_by_checksum(row["blob_sha256"])
        except (PaperlessError, httpx.HTTPError, TimeoutError, ConnectionError):
            return self._result_from_row(row)
        if existing is None:
            return self._result_from_row(row)
        return self._accept_existing_paperless_document(
            intake_ref,
            existing["id"],
            IntakeOutcome.SOURCE_OCCURRENCE_REUSED,
            "unknown_outcome_verified_by_hash",
        )

    def lookup(
        self,
        *,
        source_occurrence_id: str | None = None,
        blob_sha256: str | None = None,
    ) -> IntakeResult | None:
        """Look up one bounded ledger result by occurrence or exact hash."""
        if bool(source_occurrence_id) == bool(blob_sha256):
            raise ValueError("specify exactly one lookup identity")
        with database.transaction() as connection:
            if source_occurrence_id:
                row = connection.execute(
                    "SELECT * FROM receipt_intake_attempts WHERE source_occurrence_id = ?",
                    (source_occurrence_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT * FROM receipt_intake_attempts
                    WHERE blob_sha256 = ?
                    ORDER BY created_at LIMIT 1
                    """,
                    (blob_sha256,),
                ).fetchone()
        return self._result_from_row(row) if row else None

    def get_status(self, intake_ref: str) -> IntakeResult:
        with database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM receipt_intake_attempts WHERE intake_ref = ?", (intake_ref,)
            ).fetchone()
            if row is None:
                raise KeyError(intake_ref)
            proposal = connection.execute(
                """
                SELECT proposal_ref, reason_code FROM receipt_match_proposals
                WHERE intake_ref = ? AND proposal_state = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (intake_ref, ProposalState.PENDING),
            ).fetchone()
        result = self._result_from_row(row)
        if proposal:
            return result.model_copy(
                update={
                    "review_ref": proposal["proposal_ref"],
                    "reason_codes": [proposal["reason_code"]],
                }
            )
        return result

    def _result_from_row(
        self, row: Any, forced_outcome: IntakeOutcome | None = None
    ) -> IntakeResult:
        state = AttemptState(row["attempt_state"])
        outcome = forced_outcome or IntakeOutcome(row["outcome"])
        return IntakeResult(
            intake_ref=row["intake_ref"],
            outcome=outcome,
            attempt_state=state,
            canonical_document_ref=row["canonical_ref"],
            reason_codes=[row["failure_reason_code"]] if row["failure_reason_code"] else [],
            source_channel=row["source_channel"],
            source_occurrence_version=row["source_occurrence_version"],
            source_as_of=row["source_observed_at"],
            retry_safe=state in {AttemptState.ACCEPTED, AttemptState.RETRYABLE},
            external_replica_eligible=(
                row["source_channel"] != "monarch_recovery" and state == AttemptState.ACCEPTED
            ),
        )

    def _set_terminal(
        self,
        intake_ref: str,
        *,
        state: AttemptState,
        outcome: IntakeOutcome,
        reason_code: str,
        retry_safe: bool,
    ) -> IntakeResult:
        with database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE receipt_intake_attempts
                SET attempt_state = ?, outcome = ?, failure_reason_code = ?, updated_at = ?
                WHERE intake_ref = ?
                """,
                (state, outcome, reason_code, _now(), intake_ref),
            )
            self._audit(
                connection,
                intake_ref=intake_ref,
                canonical_ref=None,
                event_type="intake_terminal",
                reason_code=reason_code,
            )
        result = self.get_status(intake_ref)
        return result.model_copy(update={"retry_safe": retry_safe})

    def _set_task_ref(self, intake_ref: str, task_ref: str) -> None:
        with database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE receipt_intake_attempts SET paperless_task_ref = ?, updated_at = ?
                WHERE intake_ref = ?
                """,
                (task_ref, _now(), intake_ref),
            )

    def _increment_attempt_count(self, intake_ref: str) -> None:
        with database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE receipt_intake_attempts
                SET attempt_count = attempt_count + 1, updated_at = ?
                WHERE intake_ref = ?
                """,
                (_now(), intake_ref),
            )

    def _claim_retry(self, intake_ref: str) -> str:
        with database.transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT attempt_state, attempt_count FROM receipt_intake_attempts
                WHERE intake_ref = ?
                """,
                (intake_ref,),
            ).fetchone()
            if row is None or row["attempt_state"] != AttemptState.RETRYABLE:
                return "not_claimed"
            if row["attempt_count"] >= settings.max_upload_attempts:
                return "exhausted"
            cursor = connection.execute(
                """
                UPDATE receipt_intake_attempts
                SET attempt_state = ?, outcome = ?, paperless_task_ref = NULL,
                    failure_reason_code = NULL, updated_at = ?
                WHERE intake_ref = ? AND attempt_state = ? AND attempt_count = ?
                """,
                (
                    AttemptState.PENDING,
                    IntakeOutcome.UPLOAD_OUTCOME_UNKNOWN,
                    _now(),
                    intake_ref,
                    AttemptState.RETRYABLE,
                    row["attempt_count"],
                ),
            )
            return "claimed" if cursor.rowcount == 1 else "not_claimed"

    def _get_attempt_by_occurrence(self, occurrence_id: str) -> Any | None:
        with database.transaction() as connection:
            return connection.execute(
                "SELECT * FROM receipt_intake_attempts WHERE source_occurrence_id = ?",
                (occurrence_id,),
            ).fetchone()

    def _find_canonical_by_hash(self, blob_sha256: str) -> Any | None:
        with database.transaction() as connection:
            return connection.execute(
                """
                SELECT * FROM receipt_canonical_documents
                WHERE deployment_fingerprint = ? AND blob_sha256 = ?
                """,
                (_deployment_fingerprint(), blob_sha256),
            ).fetchone()

    def _find_normalized_candidate(self, metadata: IntakeMetadata) -> Any | None:
        if not metadata.normalized_content_fingerprint:
            return None
        with database.transaction() as connection:
            return connection.execute(
                """
                SELECT * FROM receipt_canonical_documents
                WHERE deployment_fingerprint = ?
                  AND normalized_content_version = ?
                  AND normalized_content_fingerprint = ?
                ORDER BY created_at LIMIT 1
                """,
                (
                    _deployment_fingerprint(),
                    metadata.normalized_content_version,
                    metadata.normalized_content_fingerprint,
                ),
            ).fetchone()

    def _find_semantic_candidate(self, metadata: IntakeMetadata) -> Any | None:
        if not metadata.semantic_fingerprint:
            return None
        with database.transaction() as connection:
            return connection.execute(
                """
                SELECT * FROM receipt_canonical_documents
                WHERE deployment_fingerprint = ?
                  AND semantic_fingerprint_version = ?
                  AND semantic_fingerprint = ?
                ORDER BY created_at LIMIT 1
                """,
                (
                    _deployment_fingerprint(),
                    metadata.semantic_fingerprint_version,
                    metadata.semantic_fingerprint,
                ),
            ).fetchone()

    def _media_type(self, intake_ref: str) -> str:
        with database.transaction() as connection:
            row = connection.execute(
                "SELECT media_type FROM receipt_intake_attempts WHERE intake_ref = ?",
                (intake_ref,),
            ).fetchone()
        return row["media_type"]

    @staticmethod
    def _task_is_duplicate(task: dict[str, Any]) -> bool:
        code = str(task.get("error_code", "")).lower()
        result = str(task.get("result", "")).lower()
        return code == "duplicate" or "duplicate" in result

    @staticmethod
    def _is_unknown_upload_error(exc: Exception) -> bool:
        if isinstance(exc, PaperlessError):
            return exc.status_code is None
        return isinstance(exc, (TimeoutError, ConnectionError)) or exc.__class__.__name__ in {
            "ConnectError",
            "ReadTimeout",
            "WriteTimeout",
            "PoolTimeout",
        }

    @staticmethod
    def _is_retryable_upload_error(exc: Exception) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in {408, 429, 500, 502, 503, 504}
        return isinstance(exc, PaperlessError) and exc.status_code in {
            408,
            429,
            500,
            502,
            503,
            504,
        }

    @staticmethod
    def _audit(
        connection: Any,
        *,
        intake_ref: str | None,
        canonical_ref: str | None,
        event_type: str,
        reason_code: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO receipt_audit_events (
                event_ref, intake_ref, canonical_ref, event_type, reason_code, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (_ref("event"), intake_ref, canonical_ref, event_type, reason_code, _now()),
        )
