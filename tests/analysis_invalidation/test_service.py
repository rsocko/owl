"""Tests for the analysis invalidation / staleness mechanism (issue #114).

Covers the design contract's acceptance-criteria scenarios: accepted version
change, rollback, duplicate delivery, module/config version bump, relevant
metadata change, manual invalidation bounds/scopes, and privacy filtering.

Partial-failure/retry behavior via the Action Queue reference integration is
covered separately in ``tests/action_queue/test_freshness_integration.py``.
"""

from __future__ import annotations

import json

import pytest

from doc_intelligence_hub.modules.analysis_invalidation import config as ai_config
from doc_intelligence_hub.modules.analysis_invalidation.database import (
    InvalidationEvent,
    ModuleAnalysisFingerprint,
    StaleMark,
    get_session,
    init_db,
)
from doc_intelligence_hub.modules.analysis_invalidation.models import (
    FreshnessStatus,
    InvalidationReason,
    StaleReason,
)
from doc_intelligence_hub.modules.analysis_invalidation.service import AnalysisFreshnessService

MODULE = "action_queue"
MODULE_VERSION = "action-queue-analyzer-v1"
CONFIG_HASH = "cfg-hash-1"


@pytest.fixture()
def ai_db(tmp_path):
    original = ai_config.settings.database_url
    ai_config.settings.database_url = f"sqlite:///{tmp_path / 'test_analysis_invalidation.db'}"
    init_db()
    yield
    ai_config.settings.database_url = original


@pytest.fixture()
def svc(ai_db):
    return AnalysisFreshnessService()


def _record_fresh(svc, *, document_id, checksum, metadata=None, config_hash=CONFIG_HASH):
    return svc.record_fingerprint(
        document_id=document_id,
        module_name=MODULE,
        module_version=MODULE_VERSION,
        config_hash=config_hash,
        current_checksum=checksum,
        metadata_fields=metadata,
    )


class TestVersionChangeStaleness:
    def test_unknown_document_is_unknown_not_stale(self, svc):
        result = svc.check_freshness(
            document_id=999,
            module_name=MODULE,
            module_version=MODULE_VERSION,
            config_hash=CONFIG_HASH,
            current_checksum="abc",
        )
        assert result.status == FreshnessStatus.UNKNOWN

    def test_fresh_fingerprint_matching_current_state_is_fresh(self, svc):
        _record_fresh(svc, document_id=1, checksum="abc")
        result = svc.check_freshness(
            document_id=1,
            module_name=MODULE,
            module_version=MODULE_VERSION,
            config_hash=CONFIG_HASH,
            current_checksum="abc",
        )
        assert result.status == FreshnessStatus.FRESH

    def test_accepted_version_change_marks_existing_analysis_stale(self, svc):
        _record_fresh(svc, document_id=1, checksum="abc")
        outcome = svc.record_invalidation(
            document_id=1,
            accepted_checksum="def",
            reason=InvalidationReason.SIMULATED_VERSION_CHANGE,
            triggered_by="test",
        )
        assert outcome["duplicate"] is False
        assert MODULE in outcome["affected_modules"]

        result = svc.check_freshness(
            document_id=1,
            module_name=MODULE,
            module_version=MODULE_VERSION,
            config_hash=CONFIG_HASH,
            current_checksum="abc",  # module hasn't reprocessed yet
        )
        assert result.status == FreshnessStatus.STALE
        assert StaleReason.CONTENT_VERSION_CHANGED in result.reasons

        # A retained StaleMark documents the audit trail; nothing is deleted.
        db = get_session()
        try:
            marks = db.query(StaleMark).filter_by(document_id=1).all()
            assert len(marks) == 1
            assert marks[0].resolved_at is None
            fingerprints = db.query(ModuleAnalysisFingerprint).filter_by(document_id=1).all()
            # The original fingerprint row is retained (flipped to stale in
            # place), not deleted.
            assert len(fingerprints) == 1
            assert fingerprints[0].status == "stale"
        finally:
            db.close()

    def test_reprocessing_resolves_stale_mark_and_becomes_fresh_again(self, svc):
        _record_fresh(svc, document_id=1, checksum="abc")
        svc.record_invalidation(
            document_id=1,
            accepted_checksum="def",
            reason=InvalidationReason.SIMULATED_VERSION_CHANGE,
            triggered_by="test",
        )
        # Module reprocesses against the new checksum.
        _record_fresh(svc, document_id=1, checksum="def")

        result = svc.check_freshness(
            document_id=1,
            module_name=MODULE,
            module_version=MODULE_VERSION,
            config_hash=CONFIG_HASH,
            current_checksum="def",
        )
        assert result.status == FreshnessStatus.FRESH

        db = get_session()
        try:
            marks = db.query(StaleMark).filter_by(document_id=1).all()
            assert len(marks) == 1
            assert marks[0].resolved_at is not None
            # Append-only: two fingerprint rows now exist for audit (the
            # original stale one, plus the new fresh replacement).
            fingerprints = (
                db.query(ModuleAnalysisFingerprint)
                .filter_by(document_id=1)
                .order_by(ModuleAnalysisFingerprint.id)
                .all()
            )
            assert len(fingerprints) == 2
            assert fingerprints[0].status == "stale"
            assert fingerprints[1].status == "fresh"
        finally:
            db.close()


class TestRollback:
    def test_rollback_to_earlier_checksum_creates_new_invalidation_cycle(self, svc):
        _record_fresh(svc, document_id=1, checksum="A")
        first = svc.record_invalidation(
            document_id=1, accepted_checksum="B", reason=InvalidationReason.VERSION_CHANGED
        )
        assert first["duplicate"] is False

        # Roll back to the original checksum "A" — a genuine new transition,
        # not a duplicate of the very first (pre-history) state.
        rollback = svc.record_invalidation(
            document_id=1, accepted_checksum="A", reason=InvalidationReason.ROLLBACK
        )
        assert rollback["duplicate"] is False
        assert rollback["event_id"] != first["event_id"]

        db = get_session()
        try:
            events = db.query(InvalidationEvent).filter_by(document_id=1).all()
            assert len(events) == 2
        finally:
            db.close()


class TestDuplicateDelivery:
    def test_identical_transition_delivered_twice_is_a_noop(self, svc):
        first = svc.record_invalidation(
            document_id=1,
            accepted_checksum="abc",
            reason=InvalidationReason.SIMULATED_VERSION_CHANGE,
        )
        second = svc.record_invalidation(
            document_id=1,
            accepted_checksum="abc",
            reason=InvalidationReason.SIMULATED_VERSION_CHANGE,
        )
        assert first["duplicate"] is False
        assert second["duplicate"] is True
        assert second["event_id"] == first["event_id"]

        db = get_session()
        try:
            events = db.query(InvalidationEvent).filter_by(document_id=1).all()
            assert len(events) == 1
        finally:
            db.close()

    def test_duplicate_delivery_does_not_double_mark_stale(self, svc):
        _record_fresh(svc, document_id=1, checksum="abc")
        svc.record_invalidation(document_id=1, accepted_checksum="def")
        svc.record_invalidation(document_id=1, accepted_checksum="def")  # duplicate

        db = get_session()
        try:
            marks = db.query(StaleMark).filter_by(document_id=1).all()
            assert len(marks) == 1
        finally:
            db.close()


class TestModuleAndConfigVersionBump:
    def test_module_version_bump_is_stale_without_new_invalidation_event(self, svc):
        _record_fresh(svc, document_id=1, checksum="abc")
        result = svc.check_freshness(
            document_id=1,
            module_name=MODULE,
            module_version="action-queue-analyzer-v2",
            config_hash=CONFIG_HASH,
            current_checksum="abc",
        )
        assert result.status == FreshnessStatus.STALE
        assert StaleReason.MODULE_VERSION_CHANGED in result.reasons

        db = get_session()
        try:
            assert db.query(InvalidationEvent).count() == 0
        finally:
            db.close()

    def test_config_hash_change_is_stale(self, svc):
        _record_fresh(svc, document_id=1, checksum="abc", config_hash="cfg-1")
        result = svc.check_freshness(
            document_id=1,
            module_name=MODULE,
            module_version=MODULE_VERSION,
            config_hash="cfg-2",
            current_checksum="abc",
        )
        assert result.status == FreshnessStatus.STALE
        assert StaleReason.CONFIG_CHANGED in result.reasons


class TestMetadataChange:
    def test_relevant_metadata_change_is_stale(self, svc):
        _record_fresh(svc, document_id=1, checksum="abc", metadata={"correspondent": "9"})
        result = svc.check_freshness(
            document_id=1,
            module_name=MODULE,
            module_version=MODULE_VERSION,
            config_hash=CONFIG_HASH,
            current_checksum="abc",
            metadata_fields={"correspondent": "42"},
        )
        assert result.status == FreshnessStatus.STALE
        assert StaleReason.METADATA_CHANGED in result.reasons

    def test_unchanged_metadata_stays_fresh(self, svc):
        _record_fresh(svc, document_id=1, checksum="abc", metadata={"correspondent": "9"})
        result = svc.check_freshness(
            document_id=1,
            module_name=MODULE,
            module_version=MODULE_VERSION,
            config_hash=CONFIG_HASH,
            current_checksum="abc",
            metadata_fields={"correspondent": "9"},
        )
        assert result.status == FreshnessStatus.FRESH


class TestManualInvalidation:
    def test_manual_invalidate_bounded_batch(self, svc):
        ai_config.settings.max_manual_invalidation_batch = 2
        try:
            with pytest.raises(ValueError):
                svc.manual_invalidate(
                    document_ids=[1, 2, 3],
                    reason=InvalidationReason.MANUAL_ALL,
                    triggered_by="test",
                )
        finally:
            ai_config.settings.max_manual_invalidation_batch = 2000

    def test_manual_invalidate_specific_documents_marks_all_stale(self, svc):
        _record_fresh(svc, document_id=1, checksum="abc")
        _record_fresh(svc, document_id=2, checksum="xyz")

        outcome = svc.manual_invalidate(
            document_ids=[1, 2],
            reason=InvalidationReason.MANUAL_DOCUMENT,
            triggered_by="cli:test",
        )
        assert outcome["invalidated_count"] == 2

        for doc_id in (1, 2):
            result = svc.check_freshness(
                document_id=doc_id,
                module_name=MODULE,
                module_version=MODULE_VERSION,
                config_hash=CONFIG_HASH,
                current_checksum="abc" if doc_id == 1 else "xyz",
            )
            assert result.status == FreshnessStatus.STALE
            assert StaleReason.MANUAL_INVALIDATION in result.reasons

    def test_manual_invalidation_always_creates_new_event_even_if_rerun(self, svc):
        # Unlike real version-change duplicate-delivery, an operator
        # re-running manual invalidation should always be honored.
        first = svc.manual_invalidate(
            document_ids=[1], reason=InvalidationReason.MANUAL_ALL, triggered_by="cli"
        )
        second = svc.manual_invalidate(
            document_ids=[1], reason=InvalidationReason.MANUAL_ALL, triggered_by="cli"
        )
        assert first["results"][0]["duplicate"] is False
        assert second["results"][0]["duplicate"] is False

        db = get_session()
        try:
            assert db.query(InvalidationEvent).filter_by(document_id=1).count() == 2
            # But stale-marking itself stays idempotent — no duplicate marks
            # for a fingerprint that's already stale.
        finally:
            db.close()


class TestPrivacyFiltering:
    """No OCR text, document bodies, or raw metadata values may ever be persisted."""

    SENSITIVE_TITLE = "Acme Corp Invoice #12345 - Confidential"
    SENSITIVE_CORRESPONDENT = "Very Secret Correspondent Name LLC"
    SENSITIVE_OCR_TEXT = "This is the actual OCR'd body text of the document, containing secrets."

    def _assert_no_sensitive_strings(self, *blobs: str) -> None:
        haystack = "\n".join(blobs)
        assert self.SENSITIVE_TITLE not in haystack
        assert self.SENSITIVE_CORRESPONDENT not in haystack
        assert self.SENSITIVE_OCR_TEXT not in haystack

    def test_invalidation_event_never_stores_raw_metadata_or_ocr_text(self, svc):
        svc.record_invalidation(
            document_id=1,
            accepted_checksum="abc123checksum",
            metadata_fields={
                "title": self.SENSITIVE_TITLE,
                "correspondent": self.SENSITIVE_CORRESPONDENT,
                # A caller could accidentally pass extracted text; the
                # service must not care/store it beyond hashing.
                "ocr_text_excerpt": self.SENSITIVE_OCR_TEXT,
            },
            reason=InvalidationReason.SIMULATED_VERSION_CHANGE,
            triggered_by="test",
        )
        db = get_session()
        try:
            event = db.query(InvalidationEvent).one()
            row_repr = json.dumps(
                {
                    "document_id": event.document_id,
                    "reason": event.reason,
                    "previous_checksum": event.previous_checksum,
                    "accepted_checksum": event.accepted_checksum,
                    "metadata_fingerprint": event.metadata_fingerprint,
                    "dedup_key": event.dedup_key,
                    "triggered_by": event.triggered_by,
                }
            )
            self._assert_no_sensitive_strings(row_repr)
        finally:
            db.close()

    def test_fingerprint_never_stores_raw_metadata_values(self, svc):
        _record_fresh(
            svc,
            document_id=1,
            checksum="abc",
            metadata={
                "title": self.SENSITIVE_TITLE,
                "correspondent": self.SENSITIVE_CORRESPONDENT,
            },
        )
        db = get_session()
        try:
            fp = db.query(ModuleAnalysisFingerprint).one()
            row_repr = json.dumps(
                {
                    "module_name": fp.module_name,
                    "module_version": fp.module_version,
                    "config_hash": fp.config_hash,
                    "content_checksum": fp.content_checksum,
                    "metadata_fingerprint": fp.metadata_fingerprint,
                    # Only field *names* are allowed here, never values.
                    "metadata_fields": fp.metadata_fields,
                }
            )
            self._assert_no_sensitive_strings(row_repr)
            assert fp.metadata_fields == ["correspondent", "title"]
        finally:
            db.close()

    def test_document_status_response_is_privacy_safe(self, svc):
        _record_fresh(
            svc,
            document_id=1,
            checksum="abc",
            metadata={"title": self.SENSITIVE_TITLE},
        )
        svc.record_invalidation(
            document_id=1,
            accepted_checksum="def",
            metadata_fields={"title": self.SENSITIVE_TITLE},
        )
        status = svc.get_document_status(1)
        self._assert_no_sensitive_strings(json.dumps(status))
