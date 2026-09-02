from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import pytest

from doc_intelligence_hub.modules.statements.correspondent_models import (
    Cadence,
    CorrespondentProfileUpdate,
    DocumentExpectationCreate,
    DocumentExpectationSignalsV1,
    DocumentExpectationUpdate,
    ExpectationEvidence,
    ExternalCandidateReview,
    ExternalSignalConnectionUpdate,
)
from doc_intelligence_hub.modules.statements.correspondent_service import (
    CorrespondentPolicyService,
)
from doc_intelligence_hub.modules.statements.database import SCHEMA_VERSION, Database
from doc_intelligence_hub.modules.statements.models import (
    AnalysisPattern,
    DiscoveryResult,
    ProviderCandidate,
)

DEPLOYMENT_ID = "paperless:test-deployment"


def _provider(key: str, correspondent_id: int = 42) -> ProviderCandidate:
    return ProviderCandidate(
        provider_key=key,
        provider_name="Example Bank",
        correspondent_id=correspondent_id,
        document_count=12,
        normalized_title="example statement",
        title_consistency=1.0,
        pattern=AnalysisPattern(
            frequency="monthly",
            pattern_type="fixed_day",
            confidence=0.95,
            anchor_day=3,
        ),
        sample_document_ids=[1, 2, 3],
        first_seen=date(2025, 1, 3),
        last_seen=date(2025, 12, 3),
    )


def _confirmed_statement(series_id: str) -> DocumentExpectationCreate:
    return DocumentExpectationCreate(
        kind="statement",
        statement_series_id=series_id,
        document_ids=[103, 101, 103],
        series_discriminator="Checking 1234",
        expectation_mode="recurring",
        status="confirmed",
        cadence=Cadence(frequency="monthly", expected_day=3),
        evidence=ExpectationEvidence(source="user", reason_codes=["user_confirmed"]),
    )


def _external_snapshot(
    generation: str,
    signals: list[dict],
    *,
    completeness: str = "complete",
    source_as_of: str = "2026-08-23T00:00:00Z",
) -> DocumentExpectationSignalsV1:
    return DocumentExpectationSignalsV1.model_validate(
        {
            "contractVersion": "1",
            "connectorRef": "opaque-connector",
            "sourceGeneration": generation,
            "sourceAsOf": source_as_of,
            "completeness": completeness,
            "signals": signals,
        }
    )


def test_correspondent_schema_migration_preserves_series_history(tmp_path) -> None:
    path = str(tmp_path / "statements.db")
    db = Database(path)
    db.create_series("series-1", "Checking", "Example Bank", correspondent_id=42)
    db.save_series_override("event-1", "series-1", "rename", {"old_name": "Old"})
    db.update_external_signal_connection(
        DEPLOYMENT_ID,
        ExternalSignalConnectionUpdate(
            base_url="https://tyrion.test",
            api_token="saved-token",
        ),
    )
    conn = db.connect()
    conn.execute(
        "ALTER TABLE external_signal_connections "
        "ADD COLUMN connector_ref TEXT NOT NULL DEFAULT 'legacy-connector'"
    )
    conn.execute("UPDATE schema_version SET version = 6")
    conn.commit()
    db.close()

    migrated = Database(path)
    try:
        conn = migrated.connect()
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION
        assert migrated.get_series_overrides("series-1")[0]["id"] == "event-1"
        connection_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(external_signal_connections)").fetchall()
        }
        assert "connector_ref" not in connection_columns
        connection = migrated.get_external_signal_credentials(DEPLOYMENT_ID)
        assert connection is not None
        assert connection["base_url"] == "https://tyrion.test"
        assert connection["api_token"] == "saved-token"
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {
            "correspondent_profiles",
            "document_expectations",
            "external_signal_sources",
            "external_signal_generations",
            "external_document_candidates",
            "external_candidate_expectations",
        } <= tables
    finally:
        migrated.close()


def test_correspondent_sync_marks_orphans_and_requires_explicit_relink(tmp_path) -> None:
    db = Database(str(tmp_path / "statements.db"))
    try:
        db.reconcile_correspondents(DEPLOYMENT_ID, [{"id": 42, "name": "Example Bank"}])
        db.create_series("series-1", "Checking", "Example Bank", correspondent_id=42)
        expectation = db.create_document_expectation(
            DEPLOYMENT_ID, 42, _confirmed_statement("series-1")
        )

        result = db.reconcile_correspondents(DEPLOYMENT_ID, [{"id": 84, "name": "Example Bank"}])

        assert result.created == 1
        assert result.orphaned == 1
        assert db.get_correspondent_profile(DEPLOYMENT_ID, 42).lifecycle_status == "orphaned"
        assert db.get_correspondent_profile(DEPLOYMENT_ID, 84).review_status == "unreviewed"

        relinked = db.relink_correspondent_profile(DEPLOYMENT_ID, 42, 84, "Example Bank")

        assert relinked.correspondent_id == 84
        assert relinked.relinked_from_correspondent_id == 42
        assert db.get_correspondent_profile(DEPLOYMENT_ID, 42) is None
        assert db.get_document_expectation(DEPLOYMENT_ID, expectation.id).correspondent_id == 84
        assert db.get_series("series-1")["correspondent_id"] == 84
        updated = db.update_document_expectation(
            DEPLOYMENT_ID,
            expectation.id,
            DocumentExpectationUpdate(series_discriminator="Checking 5678"),
        )
        assert updated.series_discriminator == "Checking 5678"
        assert updated.document_ids == [101, 103]
    finally:
        db.close()


def test_candidate_override_crud_roundtrip(tmp_path) -> None:
    db = Database(str(tmp_path / "statements.db"))
    try:
        db.reconcile_correspondents(DEPLOYMENT_ID, [{"id": 42, "name": "Example Bank"}])

        assert db.list_candidate_overrides(DEPLOYMENT_ID, 42) == []

        created = db.set_candidate_overrides(
            DEPLOYMENT_ID, 42, [1, 2], group_key="household-bills", excluded=False
        )
        assert [override.document_id for override in created] == [1, 2]
        assert all(override.group_key == "household-bills" for override in created)
        assert all(override.excluded is False for override in created)
        assert all(override.updated_at is not None for override in created)

        updated = db.set_candidate_overrides(DEPLOYMENT_ID, 42, [2], group_key=None, excluded=True)
        by_id = {override.document_id: override for override in updated}
        assert by_id[1].group_key == "household-bills"
        assert by_id[1].excluded is False
        assert by_id[2].group_key is None
        assert by_id[2].excluded is True

        cleared = db.clear_candidate_overrides(DEPLOYMENT_ID, 42, [1])
        assert cleared == 1
        remaining = db.list_candidate_overrides(DEPLOYMENT_ID, 42)
        assert [override.document_id for override in remaining] == [2]

        assert db.clear_candidate_overrides(DEPLOYMENT_ID, 42, [999]) == 0
    finally:
        db.close()


def test_only_confirmed_cadenced_active_policy_is_alertable(tmp_path) -> None:
    db = Database(str(tmp_path / "statements.db"))
    try:
        db.reconcile_correspondents(DEPLOYMENT_ID, [{"id": 42, "name": "Example Bank"}])
        db.create_series("series-1", "Checking", "Example Bank", correspondent_id=42)
        alertable = db.create_document_expectation(
            DEPLOYMENT_ID, 42, _confirmed_statement("series-1")
        )
        for status, mode in [
            ("suggested", "recurring"),
            ("dismissed", "recurring"),
            ("retired", "recurring"),
            ("confirmed", "irregular"),
            ("confirmed", "not_expected"),
        ]:
            db.create_document_expectation(
                DEPLOYMENT_ID,
                42,
                DocumentExpectationCreate(
                    kind="invoice",
                    document_ids=[1] if status == "confirmed" and mode != "not_expected" else [],
                    expectation_mode=mode,
                    status=status,
                    cadence=Cadence(frequency="monthly") if mode == "recurring" else None,
                    evidence=ExpectationEvidence(source="user"),
                ),
            )

        assert [item.id for item in db.list_alertable_expectations(DEPLOYMENT_ID)] == [alertable.id]

        db.reconcile_correspondents(DEPLOYMENT_ID, [])
        assert not db.expectation_can_emit_missing_alert(DEPLOYMENT_ID, alertable.id)
    finally:
        db.close()


def test_external_snapshot_replacement_is_generation_idempotent_and_bounded(tmp_path) -> None:
    db = Database(str(tmp_path / "statements.db"))
    first = _external_snapshot(
        "generation-1",
        [
            {
                "seriesRef": "opaque-account-one",
                "kind": "accountStatementCandidate",
                "active": True,
                "displayHint": "Credit account",
                "cadence": None,
                "nextExpectedDate": None,
                "confidence": 0.6,
                "basis": ["active_non_cash_account"],
                "accountName": "Travel Rewards",
                "institutionName": "Example Bank",
                "accountType": "credit",
                "accountLastFour": "1234",
            },
            {
                "seriesRef": "opaque-account-two",
                "kind": "accountStatementCandidate",
                "active": True,
                "displayHint": "Deposit account",
                "cadence": None,
                "nextExpectedDate": None,
                "confidence": 0.6,
                "basis": ["active_non_cash_account"],
            },
        ],
    )
    try:
        result = db.replace_external_candidate_snapshot(DEPLOYMENT_ID, first)
        repeated = db.replace_external_candidate_snapshot(DEPLOYMENT_ID, first)

        assert result.active_candidates == 2
        assert repeated.idempotent is True
        candidates = db.list_external_candidates(DEPLOYMENT_ID)
        assert len(candidates) == 2
        assert all(candidate.recurrence_evidence == "high" for candidate in candidates)
        enriched = next(candidate for candidate in candidates if candidate.account_name)
        assert enriched.account_name == "Travel Rewards"
        assert enriched.institution_name == "Example Bank"
        assert enriched.account_type == "credit"
        assert enriched.account_last_four == "1234"
        assert not any(
            key
            in dict(
                db.connect()
                .execute("SELECT * FROM external_document_candidates LIMIT 1")
                .fetchone()
            )
            for key in ("balance", "transactions", "account_identifier")
        )

        partial = _external_snapshot("generation-2", [], completeness="partial")
        partial_result = db.replace_external_candidate_snapshot(DEPLOYMENT_ID, partial)
        assert partial_result.deactivated_candidates == 0
        assert partial_result.active_candidates == 2
        assert all(candidate.active for candidate in db.list_external_candidates(DEPLOYMENT_ID))

        explicit_inactive = _external_snapshot(
            "generation-3",
            [
                {
                    "seriesRef": "opaque-account-one",
                    "kind": "accountStatementCandidate",
                    "active": False,
                    "displayHint": "Credit account",
                    "cadence": None,
                    "nextExpectedDate": None,
                    "confidence": 0.6,
                    "basis": ["inactive_non_cash_account"],
                }
            ],
            completeness="partial",
        )
        inactive_result = db.replace_external_candidate_snapshot(DEPLOYMENT_ID, explicit_inactive)
        assert inactive_result.deactivated_candidates == 1
        assert inactive_result.active_candidates == 1

        complete = _external_snapshot("generation-4", [])
        assert (
            db.replace_external_candidate_snapshot(DEPLOYMENT_ID, complete).deactivated_candidates
            == 1
        )
        assert not any(candidate.active for candidate in db.list_external_candidates(DEPLOYMENT_ID))

        stale_replay = db.replace_external_candidate_snapshot(DEPLOYMENT_ID, first)
        assert stale_replay.idempotent is True
        assert stale_replay.active_candidates == 0
        assert not any(candidate.active for candidate in db.list_external_candidates(DEPLOYMENT_ID))
    finally:
        db.close()


def test_external_account_maps_to_correspondent_and_multiple_document_series(tmp_path) -> None:
    db = Database(str(tmp_path / "statements.db"))
    try:
        db.reconcile_correspondents(DEPLOYMENT_ID, [{"id": 42, "name": "Example Bank"}])
        db.create_series(
            "checking",
            "Mortgage statements",
            "Example Bank",
            correspondent_id=42,
            account_identifier="ending 1234",
        )
        db.create_series("savings", "Savings", "Example Bank", correspondent_id=42)
        checking = db.create_document_expectation(
            DEPLOYMENT_ID, 42, _confirmed_statement("checking")
        )
        tax_form = db.create_document_expectation(
            DEPLOYMENT_ID,
            42,
            DocumentExpectationCreate(
                kind="record",
                series_discriminator="Annual mortgage tax form",
                expectation_mode="irregular",
                status="suggested",
                evidence=ExpectationEvidence(source="user"),
            ),
        )
        db.replace_external_candidate_snapshot(
            DEPLOYMENT_ID,
            _external_snapshot(
                "generation-1",
                [
                    {
                        "seriesRef": "opaque-one",
                        "kind": "accountStatementCandidate",
                        "active": True,
                        "displayHint": "Credit account",
                        "cadence": None,
                        "nextExpectedDate": None,
                        "confidence": 0.6,
                        "basis": ["active_non_cash_account"],
                        "accountLastFour": "1234",
                    },
                    {
                        "seriesRef": "opaque-two",
                        "kind": "accountStatementCandidate",
                        "active": True,
                        "displayHint": "Deposit account",
                        "cadence": None,
                        "nextExpectedDate": None,
                        "confidence": 0.6,
                        "basis": ["active_non_cash_account"],
                    },
                ],
            ),
        )
        candidates = db.list_external_candidates(DEPLOYMENT_ID)
        db.review_external_candidate(
            DEPLOYMENT_ID,
            candidates[0].id,
            ExternalCandidateReview(
                outcome="mapped",
                correspondent_id=42,
                expectation_ids=[checking.id, tax_form.id],
            ),
        )
        with pytest.raises(ValueError, match="already maps to this expectation"):
            db.review_external_candidate(
                DEPLOYMENT_ID,
                candidates[1].id,
                ExternalCandidateReview(
                    outcome="mapped",
                    correspondent_id=42,
                    expectation_ids=[checking.id],
                ),
            )
        db.review_external_candidate(
            DEPLOYMENT_ID,
            candidates[1].id,
            ExternalCandidateReview(outcome="mapped", correspondent_id=42),
        )
        other_connector_snapshot = _external_snapshot(
            "other-generation",
            [
                {
                    "seriesRef": "other-account",
                    "kind": "accountStatementCandidate",
                    "active": True,
                    "displayHint": "Other connector account",
                    "cadence": None,
                    "nextExpectedDate": None,
                    "confidence": 0.6,
                    "basis": ["active_non_cash_account"],
                }
            ],
            completeness="partial",
        ).model_copy(update={"connector_ref": "other-connector"})
        db.replace_external_candidate_snapshot(DEPLOYMENT_ID, other_connector_snapshot)
        other_candidate = next(
            candidate
            for candidate in db.list_external_candidates(DEPLOYMENT_ID)
            if candidate.display_hint == "Other connector account"
        )
        with pytest.raises(ValueError, match="already maps to this expectation"):
            db.review_external_candidate(
                DEPLOYMENT_ID,
                other_candidate.id,
                ExternalCandidateReview(
                    outcome="mapped",
                    correspondent_id=42,
                    expectation_ids=[checking.id],
                ),
            )

        mapped = db.list_external_candidates(DEPLOYMENT_ID, correspondent_id=42)
        mortgage = next(candidate for candidate in mapped if candidate.account_last_four == "1234")
        assert mortgage.expectation_ids == [checking.id, tax_form.id]
        assert mortgage.identifier_match_expectation_ids == [checking.id]
        assert mortgage.likely_multiple_statement_series is True
        assert (
            next(candidate for candidate in mapped if candidate.id != mortgage.id).expectation_ids
            == []
        )

        db.replace_external_candidate_snapshot(
            DEPLOYMENT_ID,
            _external_snapshot("generation-2", []),
        )
        inactive = db.list_external_candidates(DEPLOYMENT_ID, correspondent_id=42)
        assert {candidate.review_finding for candidate in inactive} == {
            "source_candidate_inactive_confirmed_policy_preserved",
            "source_candidate_inactive",
        }
        db.review_external_candidate(
            DEPLOYMENT_ID,
            mortgage.id,
            ExternalCandidateReview(
                outcome="mapped",
                correspondent_id=42,
                expectation_ids=[checking.id, tax_form.id],
            ),
        )
        acknowledged = db.list_external_candidates(DEPLOYMENT_ID, correspondent_id=42)
        assert (
            next(
                candidate for candidate in acknowledged if candidate.id == mortgage.id
            ).review_finding
            is None
        )
        assert db.get_document_expectation(DEPLOYMENT_ID, checking.id).status == "confirmed"
        assert db.get_document_expectation(DEPLOYMENT_ID, tax_form.id).status == "suggested"
        db.replace_external_candidate_snapshot(
            DEPLOYMENT_ID,
            _external_snapshot(
                "generation-3",
                [
                    {
                        "seriesRef": "opaque-one",
                        "kind": "accountStatementCandidate",
                        "active": True,
                        "displayHint": "Credit account",
                        "cadence": None,
                        "nextExpectedDate": None,
                        "confidence": 0.6,
                        "basis": ["active_non_cash_account"],
                        "accountLastFour": "1234",
                    },
                    {
                        "seriesRef": "opaque-two",
                        "kind": "accountStatementCandidate",
                        "active": True,
                        "displayHint": "Deposit account",
                        "cadence": None,
                        "nextExpectedDate": None,
                        "confidence": 0.6,
                        "basis": ["active_non_cash_account"],
                    },
                ],
            ),
        )
        reactivated = db.list_external_candidates(DEPLOYMENT_ID, correspondent_id=42)
        assert {candidate.outcome for candidate in reactivated} == {"mapped"}
        assert next(
            candidate for candidate in reactivated if candidate.account_last_four == "1234"
        ).expectation_ids == [checking.id, tax_form.id]
        assert db.get_document_expectation(DEPLOYMENT_ID, checking.id).status == "confirmed"
        assert db.get_document_expectation(DEPLOYMENT_ID, tax_form.id).status == "suggested"
    finally:
        db.close()


def test_stale_unseen_external_generation_cannot_overwrite_newer_snapshot(tmp_path) -> None:
    db = Database(str(tmp_path / "statements.db"))
    try:
        newer = _external_snapshot(
            "opaque-newer",
            [
                {
                    "seriesRef": "opaque-account",
                    "kind": "accountStatementCandidate",
                    "active": True,
                    "displayHint": "Current label",
                    "cadence": None,
                    "nextExpectedDate": None,
                    "confidence": 0.6,
                    "basis": ["active_non_cash_account"],
                }
            ],
            source_as_of="2026-08-23T12:00:00Z",
        )
        stale = _external_snapshot(
            "opaque-stale",
            [],
            source_as_of="2026-08-23T11:00:00Z",
        )

        db.replace_external_candidate_snapshot(DEPLOYMENT_ID, newer)
        result = db.replace_external_candidate_snapshot(DEPLOYMENT_ID, stale)

        assert result.idempotent is True
        assert result.active_candidates == 1
        candidate = db.list_external_candidates(DEPLOYMENT_ID)[0]
        assert candidate.active is True
        assert candidate.display_hint == "Current label"
        assert db.replace_external_candidate_snapshot(DEPLOYMENT_ID, stale).idempotent is True
    finally:
        db.close()


def test_external_not_applicable_creates_durable_policy_and_suppresses_repeat_prompt(
    tmp_path,
) -> None:
    db = Database(str(tmp_path / "statements.db"))
    try:
        db.reconcile_correspondents(DEPLOYMENT_ID, [{"id": 42, "name": "Example Bank"}])
        first = _external_snapshot(
            "generation-1",
            [
                {
                    "seriesRef": "opaque-recurring",
                    "kind": "recurringDocumentCandidate",
                    "active": True,
                    "displayHint": "Recurring expense",
                    "cadence": None,
                    "nextExpectedDate": None,
                    "confidence": 0.6,
                    "basis": ["active_recurring_obligation"],
                }
            ],
        )
        db.replace_external_candidate_snapshot(DEPLOYMENT_ID, first)
        candidate = db.list_external_candidates(DEPLOYMENT_ID)[0]

        barrier = Barrier(2)

        def record_not_expected():
            concurrent_db = Database(db.path)
            try:
                concurrent_db.connect()
                barrier.wait()
                return concurrent_db.review_external_candidate(
                    DEPLOYMENT_ID,
                    candidate.id,
                    ExternalCandidateReview(outcome="not_applicable", correspondent_id=42),
                )
            finally:
                concurrent_db.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            reviewed, concurrent_repeat = executor.map(
                lambda _: record_not_expected(),
                range(2),
            )

        assert concurrent_repeat.expectation_id == reviewed.expectation_id
        expectation = db.get_document_expectation(DEPLOYMENT_ID, reviewed.expectation_id or "")

        assert reviewed.outcome == "not_applicable"
        assert expectation is not None
        assert expectation.kind == "other"
        assert expectation.expectation_mode == "not_expected"
        assert expectation.status == "confirmed"
        assert expectation.statement_series_id is None
        assert expectation.evidence.reason_codes == ["external_signal_documentless"]

        repeated = db.review_external_candidate(
            DEPLOYMENT_ID,
            candidate.id,
            ExternalCandidateReview(outcome="not_applicable", correspondent_id=42),
        )
        assert repeated.expectation_id == reviewed.expectation_id
        assert len(db.list_document_expectations(DEPLOYMENT_ID)) == 1

        unchanged = _external_snapshot(
            "generation-2",
            [
                {
                    "seriesRef": "opaque-recurring",
                    "kind": "recurringDocumentCandidate",
                    "active": True,
                    "displayHint": "Updated advisory label",
                    "cadence": None,
                    "nextExpectedDate": None,
                    "confidence": 0.6,
                    "basis": ["active_recurring_obligation"],
                }
            ],
        )
        db.replace_external_candidate_snapshot(DEPLOYMENT_ID, unchanged)
        reconciled = db.list_external_candidates(DEPLOYMENT_ID)[0]
        assert reconciled.outcome == "not_applicable"
        assert reconciled.expectation_id == reviewed.expectation_id

        db.replace_external_candidate_snapshot(
            DEPLOYMENT_ID,
            _external_snapshot("generation-3", []),
        )
        inactive = db.list_external_candidates(DEPLOYMENT_ID)[0]
        assert inactive.active is False
        assert inactive.review_finding == "source_candidate_inactive_confirmed_policy_preserved"
        assert (
            db.get_document_expectation(DEPLOYMENT_ID, reviewed.expectation_id or "").status
            == "confirmed"
        )

        with pytest.raises(ValueError, match="Retire the confirmed not_expected policy"):
            db.review_external_candidate(
                DEPLOYMENT_ID,
                candidate.id,
                ExternalCandidateReview(outcome="ambiguous", correspondent_id=42),
            )
    finally:
        db.close()


def test_recurring_obligation_alone_cannot_suggest_document_requirement(tmp_path) -> None:
    db = Database(str(tmp_path / "statements.db"))
    try:
        db.reconcile_correspondents(DEPLOYMENT_ID, [{"id": 42, "name": "Example Bank"}])
        db.replace_external_candidate_snapshot(
            DEPLOYMENT_ID,
            _external_snapshot(
                "generation-1",
                [
                    {
                        "seriesRef": "opaque-recurring",
                        "kind": "recurringDocumentCandidate",
                        "active": True,
                        "displayHint": "Recurring expense",
                        "cadence": None,
                        "nextExpectedDate": None,
                        "confidence": 0.6,
                        "basis": ["active_recurring_obligation"],
                    }
                ],
            ),
        )
        candidate = db.list_external_candidates(DEPLOYMENT_ID)[0]
        review = ExternalCandidateReview(
            outcome="suggested",
            correspondent_id=42,
            expectation=DocumentExpectationCreate(
                kind="invoice",
                expectation_mode="irregular",
                status="suggested",
                evidence=ExpectationEvidence(source="user"),
            ),
        )

        with pytest.raises(ValueError, match="recurring obligation alone"):
            db.review_external_candidate(DEPLOYMENT_ID, candidate.id, review)
        assert db.list_document_expectations(DEPLOYMENT_ID) == []
    finally:
        db.close()


def test_legacy_override_migrates_only_with_one_series(tmp_path) -> None:
    db = Database(str(tmp_path / "statements.db"))
    try:
        db.save_discovery(
            DiscoveryResult(
                analyzed_documents=12,
                providers=[_provider("example-bank-monthly")],
            )
        )
        db.create_series("series-1", "Example Bank", "Example Bank", correspondent_id=42)
        db.set_provider_override(
            "example-bank-monthly",
            status="confirmed",
            frequency_override="monthly",
            anchor_day_override=3,
            notes="Reviewed from the legacy tracker.",
        )
        db.reconcile_correspondents(DEPLOYMENT_ID, [{"id": 42, "name": "Example Bank"}])

        migrated, review = db.migrate_legacy_provider_overrides(DEPLOYMENT_ID)

        assert (migrated, review) == (1, 0)
        expectation = db.list_document_expectations(DEPLOYMENT_ID)[0]
        assert expectation.statement_series_id == "series-1"
        assert expectation.legacy_provider_key == "example-bank-monthly"
        assert expectation.can_emit_missing_alert()
        assert (
            db.resolve_expectation_identity(DEPLOYMENT_ID, "example-bank-monthly").canonical_key
            == expectation.id
        )
        assert (
            db.get_correspondent_profile(DEPLOYMENT_ID, 42).notes
            == "Reviewed from the legacy tracker."
        )
    finally:
        db.close()


def test_ambiguous_legacy_override_stays_in_review(tmp_path) -> None:
    db = Database(str(tmp_path / "statements.db"))
    try:
        db.save_discovery(
            DiscoveryResult(
                analyzed_documents=12,
                providers=[_provider("example-bank")],
            )
        )
        db.create_series("checking", "Example Bank", "Example Bank", correspondent_id=42)
        db.create_series("savings", "Savings", "Example Bank", correspondent_id=42)
        db.set_provider_override("example-bank", status="confirmed", display_name="Example Bank")
        db.reconcile_correspondents(DEPLOYMENT_ID, [{"id": 42, "name": "Example Bank"}])

        migrated, review = db.migrate_legacy_provider_overrides(DEPLOYMENT_ID)

        assert (migrated, review) == (0, 1)
        item = db.list_legacy_override_review(DEPLOYMENT_ID)[0]
        assert item.resolution_status == "review_required"
        assert item.reason_code == "ambiguous_statement_series"
        assert db.resolve_expectation_identity(DEPLOYMENT_ID, "example-bank").status == "ambiguous"
    finally:
        db.close()


def test_second_legacy_key_for_one_expectation_requires_review(tmp_path) -> None:
    db = Database(str(tmp_path / "statements.db"))
    try:
        db.save_discovery(
            DiscoveryResult(
                analyzed_documents=12,
                providers=[
                    _provider("legacy-key-one"),
                    _provider("legacy-key-two"),
                ],
            )
        )
        db.create_series("series-1", "Example Bank", "Example Bank", correspondent_id=42)
        db.set_provider_override("legacy-key-one", status="confirmed")
        db.set_provider_override("legacy-key-two", status="confirmed")
        db.reconcile_correspondents(DEPLOYMENT_ID, [{"id": 42, "name": "Example Bank"}])

        migrated, review = db.migrate_legacy_provider_overrides(DEPLOYMENT_ID)

        assert (migrated, review) == (1, 1)
        outcomes = {
            item.provider_key: item for item in db.list_legacy_override_review(DEPLOYMENT_ID)
        }
        assert outcomes["legacy-key-one"].resolution_status == "migrated"
        assert outcomes["legacy-key-two"].reason_code == "expectation_identity_conflict"
        assert (
            db.resolve_expectation_identity(DEPLOYMENT_ID, "legacy-key-two").status == "ambiguous"
        )
    finally:
        db.close()


def test_series_merge_rebinds_expectation_and_preserves_override_history(tmp_path) -> None:
    db = Database(str(tmp_path / "statements.db"))
    try:
        db.reconcile_correspondents(DEPLOYMENT_ID, [{"id": 42, "name": "Example Bank"}])
        db.create_series("source", "Old Checking", "Example Bank", correspondent_id=42)
        db.create_series("target", "Checking", "Example Bank", correspondent_id=42)
        db.save_series_override("source-event", "source", "rename", {"old_name": "Old"})
        expectation = db.create_document_expectation(
            DEPLOYMENT_ID, 42, _confirmed_statement("source")
        )

        db.validate_expectations_for_series_merge(DEPLOYMENT_ID, "source", "target")
        db.reconcile_expectations_for_series_merge(DEPLOYMENT_ID, "source", "target")
        db.delete_series("source")

        assert (
            db.get_document_expectation(DEPLOYMENT_ID, expectation.id).statement_series_id
            == "target"
        )
        assert db.get_series_overrides("source")[0]["id"] == "source-event"
    finally:
        db.close()


def test_series_merge_policy_rejects_cross_correspondent_target(tmp_path) -> None:
    db = Database(str(tmp_path / "statements.db"))
    try:
        db.reconcile_correspondents(
            DEPLOYMENT_ID,
            [
                {"id": 42, "name": "Example Bank"},
                {"id": 84, "name": "Other Bank"},
            ],
        )
        db.create_series("source", "Checking", "Example Bank", correspondent_id=42)
        db.create_series("target", "Savings", "Other Bank", correspondent_id=84)
        db.create_document_expectation(DEPLOYMENT_ID, 42, _confirmed_statement("source"))

        with pytest.raises(ValueError, match="another correspondent"):
            db.validate_expectations_for_series_merge(DEPLOYMENT_ID, "source", "target")
    finally:
        db.close()


def test_policy_service_uses_deployment_scope(tmp_path) -> None:
    db = Database(str(tmp_path / "statements.db"))
    service = CorrespondentPolicyService(db, DEPLOYMENT_ID)
    try:
        result = service.synchronize([{"id": 42, "name": "Example Bank"}])
        assert result.created == 1

        profile = service.update_profile(
            42,
            CorrespondentProfileUpdate(review_status="reviewed", aliases=["Example Financial"]),
        )
        assert profile.review_status == "reviewed"
        assert profile.aliases == ["Example Financial"]
    finally:
        service.close()
