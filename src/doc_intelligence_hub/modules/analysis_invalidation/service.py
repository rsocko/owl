"""Service layer for the analysis invalidation / staleness mechanism (issue #114).

``AnalysisFreshnessService`` is the generic contract any downstream module
adopts:

- ``check_freshness`` — "is my last analysis for this document still fresh?"
- ``record_fingerprint`` — "I just (re)computed results — record that."

``record_invalidation`` / ``simulate_version_change`` / ``manual_invalidate``
are the producer side: they create the durable, privacy-safe
``InvalidationEvent`` record and mark existing fresh fingerprints stale.
Nothing in this module ever touches Paperless or writes document content —
callers pass in whatever checksum/metadata they already have.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from .config import settings
from .database import (
    DocumentVersionIdentity,
    InvalidationEvent,
    ModuleAnalysisFingerprint,
    StaleMark,
)
from .models import (
    FreshnessResult,
    FreshnessStatus,
    InvalidationReason,
    StaleReason,
    is_manual_reason,
    stale_reason_for_invalidation,
)

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


def _digest(value: Any) -> str:
    """Stable sha256 digest of a JSON-serializable value.

    Used both for the metadata fingerprint (so raw metadata values are never
    persisted) and for the invalidation dedup key.
    """
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def compute_metadata_fingerprint(metadata_fields: dict[str, Any] | None) -> str | None:
    """Hash only the metadata field values a caller says it depends on.

    Returns ``None`` if no fields were provided (module has no metadata
    dependency at all). Field values are hashed together — never persisted
    as plain text anywhere.
    """
    if not metadata_fields:
        return None
    return _digest(metadata_fields)


class AnalysisFreshnessService:
    """Generic invalidation-record + staleness-check/fingerprint contract."""

    def __init__(self, session_factory: SessionFactory | None = None):
        from .database import get_session as default_session_factory

        self._session_factory = session_factory or default_session_factory

    # ------------------------------------------------------------------
    # Producer side — creating invalidation events
    # ------------------------------------------------------------------

    def record_invalidation(
        self,
        *,
        document_id: int,
        accepted_checksum: str,
        metadata_fields: dict[str, Any] | None = None,
        reason: InvalidationReason = InvalidationReason.VERSION_CHANGED,
        triggered_by: str | None = None,
    ) -> dict[str, Any]:
        """Durably record that ``document_id``'s accepted version changed.

        Idempotent in two complementary ways:

        - **Duplicate delivery**: for real/simulated version-change reasons
          (never for manual invalidations, which an operator should always be
          able to re-trigger), if the document's tracked identity *already*
          reflects the exact ``(accepted_checksum, metadata_fingerprint)``
          being submitted, nothing has actually changed — this is treated as
          a no-op duplicate and returns the most recent matching event with
          ``duplicate=True`` rather than creating a new one or re-marking
          anything stale.
        - **Rollback**: rolling back to an earlier checksum is a genuine
          state transition (the identity's *current* checksum differs from
          the one being rolled back to), so it always creates a new
          invalidation cycle even though that checksum was seen before.

        A ``dedup_key`` (over document/previous-checksum/new-checksum/
        metadata/reason) is additionally persisted with a uniqueness
        constraint as defensive, DB-level protection against the same exact
        transition being recorded twice.
        """
        db = self._session_factory()
        try:
            metadata_fingerprint = compute_metadata_fingerprint(metadata_fields)
            identity = (
                db.query(DocumentVersionIdentity).filter_by(document_id=document_id).one_or_none()
            )
            previous_checksum = identity.content_checksum if identity else None
            previous_metadata_fingerprint = identity.metadata_fingerprint if identity else None

            no_real_change = (
                identity is not None
                and not is_manual_reason(reason)
                and previous_checksum == accepted_checksum
                and previous_metadata_fingerprint == metadata_fingerprint
            )
            if no_real_change:
                existing = (
                    db.query(InvalidationEvent)
                    .filter_by(document_id=document_id, accepted_checksum=accepted_checksum)
                    .order_by(InvalidationEvent.id.desc())
                    .first()
                )
                return {
                    "event_id": existing.id if existing is not None else None,
                    "duplicate": True,
                    "affected_modules": [],
                }

            dedup_key = _digest(
                {
                    "document_id": document_id,
                    "previous_checksum": previous_checksum,
                    "accepted_checksum": accepted_checksum,
                    "metadata_fingerprint": metadata_fingerprint,
                    "reason": reason.value,
                }
            )

            existing = db.query(InvalidationEvent).filter_by(dedup_key=dedup_key).one_or_none()
            if existing is not None:
                return {
                    "event_id": existing.id,
                    "duplicate": True,
                    "affected_modules": [],
                }

            event = InvalidationEvent(
                document_id=document_id,
                reason=reason.value,
                previous_checksum=previous_checksum,
                accepted_checksum=accepted_checksum,
                metadata_fingerprint=metadata_fingerprint,
                dedup_key=dedup_key,
                triggered_by=triggered_by,
            )
            db.add(event)
            db.flush()  # assign event.id

            if identity is None:
                db.add(
                    DocumentVersionIdentity(
                        document_id=document_id,
                        content_checksum=accepted_checksum,
                        metadata_fingerprint=metadata_fingerprint,
                    )
                )
            else:
                identity.content_checksum = accepted_checksum
                identity.metadata_fingerprint = metadata_fingerprint

            affected_modules = self._mark_fresh_fingerprints_stale(
                db,
                document_id=document_id,
                invalidation_event_id=event.id,
                stale_reason=stale_reason_for_invalidation(reason),
            )

            db.commit()
            return {
                "event_id": event.id,
                "duplicate": False,
                "affected_modules": affected_modules,
            }
        finally:
            db.close()

    def simulate_version_change(
        self,
        *,
        document_id: int,
        new_checksum: str,
        metadata_fields: dict[str, Any] | None = None,
        triggered_by: str = "simulated",
    ) -> dict[str, Any]:
        """Manually/programmatically simulate "this document's OCR version changed".

        This is the supported stand-in for issue #18's future "apply an
        accepted OCR candidate" step, which does not exist yet. Once #18's
        apply step is built, it should call ``record_invalidation`` directly
        with ``reason=InvalidationReason.VERSION_CHANGED`` (or ``ROLLBACK``
        when applying an earlier version) instead of this method.
        """
        return self.record_invalidation(
            document_id=document_id,
            accepted_checksum=new_checksum,
            metadata_fields=metadata_fields,
            reason=InvalidationReason.SIMULATED_VERSION_CHANGE,
            triggered_by=triggered_by,
        )

    def manual_invalidate(
        self,
        *,
        document_ids: list[int],
        reason: InvalidationReason,
        triggered_by: str,
    ) -> dict[str, Any]:
        """Force-invalidate a specific, bounded set of documents.

        Does not require (or fabricate) a real checksum change — an operator
        is explicitly declaring these documents' cached analysis untrusted.
        Bounded by ``settings.max_manual_invalidation_batch`` regardless of
        scope (all/scoped/specific-ids) — callers resolve the scope to a
        document-id list before calling this.
        """
        if reason not in (
            InvalidationReason.MANUAL_ALL,
            InvalidationReason.MANUAL_SCOPE,
            InvalidationReason.MANUAL_DOCUMENT,
        ):
            raise ValueError(f"manual_invalidate requires a manual reason, got {reason!r}")
        if len(document_ids) > settings.max_manual_invalidation_batch:
            raise ValueError(
                f"Manual invalidation of {len(document_ids)} documents exceeds the configured "
                f"limit of {settings.max_manual_invalidation_batch}."
            )

        results = []
        for document_id in document_ids:
            db = self._session_factory()
            try:
                identity = (
                    db.query(DocumentVersionIdentity)
                    .filter_by(document_id=document_id)
                    .one_or_none()
                )
                # Manual invalidation isn't a real version change — carry
                # forward the last known checksum (or a stable sentinel if
                # we've never seen one) so it never fabricates content
                # identity.
                checksum = identity.content_checksum if identity else "unknown"
            finally:
                db.close()
            result = self.record_invalidation(
                document_id=document_id,
                accepted_checksum=checksum,
                reason=reason,
                triggered_by=triggered_by,
            )
            results.append({"document_id": document_id, **result})

        return {
            "invalidated_count": len(results),
            "results": results,
        }

    # ------------------------------------------------------------------
    # Consumer side — the generic contract downstream modules adopt
    # ------------------------------------------------------------------

    def check_freshness(
        self,
        *,
        document_id: int,
        module_name: str,
        module_version: str,
        config_hash: str,
        current_checksum: str,
        metadata_fields: dict[str, Any] | None = None,
    ) -> FreshnessResult:
        """Ask "is my last analysis for this document still fresh?"."""
        db = self._session_factory()
        try:
            fingerprint = (
                db.query(ModuleAnalysisFingerprint)
                .filter_by(document_id=document_id, module_name=module_name)
                .order_by(ModuleAnalysisFingerprint.id.desc())
                .first()
            )
            if fingerprint is None:
                return FreshnessResult(status=FreshnessStatus.UNKNOWN)

            reasons: list[StaleReason] = []
            if fingerprint.status == "stale" and fingerprint.stale_reason:
                try:
                    reasons.append(StaleReason(fingerprint.stale_reason))
                except ValueError:
                    reasons.append(StaleReason.CONTENT_VERSION_CHANGED)

            if fingerprint.content_checksum != current_checksum:
                reasons.append(StaleReason.CONTENT_VERSION_CHANGED)
            if fingerprint.module_version != module_version:
                reasons.append(StaleReason.MODULE_VERSION_CHANGED)
            if fingerprint.config_hash != config_hash:
                reasons.append(StaleReason.CONFIG_CHANGED)
            current_metadata_fingerprint = compute_metadata_fingerprint(metadata_fields)
            if fingerprint.metadata_fingerprint != current_metadata_fingerprint:
                reasons.append(StaleReason.METADATA_CHANGED)

            # De-duplicate while preserving encounter order.
            deduped = tuple(dict.fromkeys(reasons))
            status = FreshnessStatus.STALE if deduped else FreshnessStatus.FRESH
            return FreshnessResult(
                status=status,
                reasons=deduped,
                fingerprint_id=fingerprint.id,
                checked_at=datetime.utcnow().isoformat(),
            )
        finally:
            db.close()

    def record_fingerprint(
        self,
        *,
        document_id: int,
        module_name: str,
        module_version: str,
        config_hash: str,
        current_checksum: str,
        metadata_fields: dict[str, Any] | None = None,
    ) -> int:
        """Record that ``module_name`` just (re)computed results for this document.

        Inserts a new fresh fingerprint row (append-only — prior rows stay
        for audit) and resolves any open ``StaleMark``s for this
        (document, module) pair. Returns the new fingerprint's id.
        """
        db = self._session_factory()
        try:
            metadata_fingerprint = compute_metadata_fingerprint(metadata_fields)
            fingerprint = ModuleAnalysisFingerprint(
                document_id=document_id,
                module_name=module_name,
                module_version=module_version,
                config_hash=config_hash,
                content_checksum=current_checksum,
                metadata_fingerprint=metadata_fingerprint,
                metadata_fields=sorted(metadata_fields.keys()) if metadata_fields else None,
                status="fresh",
                stale_reason=None,
            )
            db.add(fingerprint)
            db.flush()

            now = datetime.utcnow()
            (
                db.query(StaleMark)
                .filter(
                    StaleMark.document_id == document_id,
                    StaleMark.module_name == module_name,
                    StaleMark.resolved_at.is_(None),
                )
                .update(
                    {"resolved_at": now, "resolved_by_fingerprint_id": fingerprint.id},
                    synchronize_session=False,
                )
            )
            db.commit()
            return fingerprint.id
        finally:
            db.close()

    def get_document_status(self, document_id: int) -> dict[str, Any]:
        """Observability: per-module latest fingerprint + open stale marks for one document."""
        db = self._session_factory()
        try:
            fingerprints = (
                db.query(ModuleAnalysisFingerprint)
                .filter_by(document_id=document_id)
                .order_by(
                    ModuleAnalysisFingerprint.module_name, ModuleAnalysisFingerprint.id.desc()
                )
                .all()
            )
            latest_by_module: dict[str, ModuleAnalysisFingerprint] = {}
            for fp in fingerprints:
                latest_by_module.setdefault(fp.module_name, fp)

            open_marks = (
                db.query(StaleMark)
                .filter_by(document_id=document_id, resolved_at=None)
                .order_by(StaleMark.created_at.desc())
                .all()
            )

            return {
                "document_id": document_id,
                "modules": [
                    {
                        "module_name": fp.module_name,
                        "module_version": fp.module_version,
                        "status": fp.status,
                        "stale_reason": fp.stale_reason,
                        "computed_at": fp.computed_at.isoformat() if fp.computed_at else None,
                        "fingerprint_id": fp.id,
                    }
                    for fp in latest_by_module.values()
                ],
                "open_stale_marks": [
                    {
                        "module_name": mark.module_name,
                        "reason": mark.reason,
                        "created_at": mark.created_at.isoformat() if mark.created_at else None,
                        "invalidation_event_id": mark.invalidation_event_id,
                    }
                    for mark in open_marks
                ],
            }
        finally:
            db.close()

    def list_known_document_ids(self, *, limit: int) -> list[int]:
        """All document IDs this system has ever recorded a fingerprint for.

        Used to resolve the "all documents" manual-invalidation scope to a
        bounded, concrete document-id list — deliberately does not reach out
        to Paperless to enumerate the full corpus.
        """
        db = self._session_factory()
        try:
            rows = (
                db.query(ModuleAnalysisFingerprint.document_id)
                .distinct()
                .order_by(ModuleAnalysisFingerprint.document_id)
                .limit(limit)
                .all()
            )
            return [row[0] for row in rows]
        finally:
            db.close()

    def list_events(
        self, *, document_id: int | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Redacted, aggregate list of recent invalidation events."""
        db = self._session_factory()
        try:
            query = db.query(InvalidationEvent)
            if document_id is not None:
                query = query.filter_by(document_id=document_id)
            events = query.order_by(InvalidationEvent.created_at.desc()).limit(limit).all()
            return [
                {
                    "event_id": e.id,
                    "document_id": e.document_id,
                    "reason": e.reason,
                    "triggered_by": e.triggered_by,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in events
            ]
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _mark_fresh_fingerprints_stale(
        db: Session,
        *,
        document_id: int,
        invalidation_event_id: int,
        stale_reason: StaleReason,
    ) -> list[str]:
        """Mark every module's latest fresh fingerprint for this document stale.

        Existing fingerprint rows are retained (only their ``status``/
        ``stale_reason`` change) and a durable ``StaleMark`` audit row is
        created per affected module.
        """
        latest_ids_subquery = (
            db.query(
                ModuleAnalysisFingerprint.module_name,
                ModuleAnalysisFingerprint.id,
            )
            .filter(ModuleAnalysisFingerprint.document_id == document_id)
            .order_by(ModuleAnalysisFingerprint.module_name, ModuleAnalysisFingerprint.id.desc())
            .all()
        )
        latest_id_by_module: dict[str, int] = {}
        for module_name, fp_id in latest_ids_subquery:
            latest_id_by_module.setdefault(module_name, fp_id)

        affected: list[str] = []
        for module_name, fp_id in latest_id_by_module.items():
            fingerprint = db.get(ModuleAnalysisFingerprint, fp_id)
            if fingerprint is None or fingerprint.status == "stale":
                continue
            fingerprint.status = "stale"
            fingerprint.stale_reason = stale_reason.value
            db.add(
                StaleMark(
                    invalidation_event_id=invalidation_event_id,
                    document_id=document_id,
                    module_name=module_name,
                    reason=stale_reason.value,
                )
            )
            affected.append(module_name)
        return affected
