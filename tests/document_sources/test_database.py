from datetime import date

import pytest

from doc_intelligence_hub.modules.document_sources.database import SourceDatabase
from doc_intelligence_hub.modules.document_sources.models import (
    DocumentExpectationCreate,
    DocumentSeriesCreate,
    DocumentSeriesUpdate,
    DocumentSourceUpdate,
    ExternalSignalRelationshipUpdate,
    SourceReopenRequest,
)
from doc_intelligence_hub.modules.statements.correspondent_models import (
    Cadence,
    ExpectationEvidence,
    FulfillmentRule,
)

DEPLOYMENT_ID = "paperless:test"


def _database(tmp_path) -> SourceDatabase:
    return SourceDatabase(tmp_path / "sources.db", DEPLOYMENT_ID)


def _sync_bank(database: SourceDatabase) -> None:
    database.sync_sources([{"id": 42, "name": "Example Bank"}])


def _series(database: SourceDatabase, *, kind: str = "statement"):
    return database.create_series(
        DocumentSeriesCreate(
            source_correspondent_id=42,
            name="Checking 1234",
            kind=kind,
            document_ids=[101, 102],
        )
    )


def _expectation(database: SourceDatabase, series_id: str, *, kind: str = "statement"):
    return database.create_expectation(
        DocumentExpectationCreate(
            source_correspondent_id=42,
            series_id=series_id,
            name="Monthly checking statement",
            kind=kind,
            expectation_mode="recurring",
            status="confirmed",
            cadence=Cadence(frequency="monthly", expected_day=5),
            monitoring_mode="presence",
            evidence=ExpectationEvidence(source="user"),
        )
    )


def test_dispositions_are_durable_and_waiting_reopens_only_for_evidence_or_due_date(
    tmp_path,
) -> None:
    database = _database(tmp_path)
    try:
        _sync_bank(database)
        resolved = database.update_source(
            42,
            DocumentSourceUpdate(disposition="no_series_expected"),
        )
        assert resolved.review_state == "resolved"
        assert resolved.reopen_reasons == []

        database.sync_sources([{"id": 42, "name": "Example Bank"}])
        assert database.get_source(42).review_state == "resolved"

        reopened = database.reopen_source(
            42,
            SourceReopenRequest(reason="new_document", detail="Paperless document 99 arrived"),
        )
        assert reopened.review_state == "needs_review"
        assert reopened.reopen_reasons == ["new_document"]

        waiting = database.update_source(
            42,
            DocumentSourceUpdate(
                disposition="waiting_for_history",
                waiting_revisit_date=date(2026, 11, 1),
            ),
        )
        assert waiting.review_state == "resolved"
        assert database.reopen_due_waiting_sources(date(2026, 10, 31)) == 0
        assert database.reopen_due_waiting_sources(date(2026, 11, 1)) == 1
        assert database.get_source(42).reopen_reasons == ["waiting_revisit_due"]
    finally:
        database.close()


def test_source_and_expectation_external_links_are_independent(tmp_path) -> None:
    database = _database(tmp_path)
    try:
        _sync_bank(database)
        source_only = database.set_external_relationship(
            "signal-1",
            ExternalSignalRelationshipUpdate(
                source_correspondent_id=42,
                resolution="linked",
                safe_label="Checking ending 1234",
            ),
        )
        assert source_only.expectation_ids == []
        assert database.list_expectations() == []

        series = _series(database)
        expectation = _expectation(database, series.id)
        linked = database.set_external_relationship(
            "signal-1",
            ExternalSignalRelationshipUpdate(
                source_correspondent_id=42,
                expectation_ids=[expectation.id],
                resolution="linked",
            ),
        )
        assert linked.expectation_ids == [expectation.id]

        database.sync_sources([{"id": 42, "name": "Example Bank"}, {"id": 84, "name": "Other"}])
        other_series = database.create_series(
            DocumentSeriesCreate(
                source_correspondent_id=84,
                name="Other",
                kind="statement",
            )
        )
        other_expectation = database.create_expectation(
            DocumentExpectationCreate(
                source_correspondent_id=84,
                series_id=other_series.id,
                name="Other statement",
                kind="statement",
                expectation_mode="irregular",
                status="confirmed",
                evidence=ExpectationEvidence(source="user"),
            )
        )
        with pytest.raises(ValueError, match="selected source"):
            database.set_external_relationship(
                "signal-1",
                ExternalSignalRelationshipUpdate(
                    source_correspondent_id=42,
                    expectation_ids=[other_expectation.id],
                    resolution="linked",
                ),
            )
    finally:
        database.close()


def test_series_membership_changes_do_not_rewrite_expectation_policy(tmp_path) -> None:
    database = _database(tmp_path)
    try:
        _sync_bank(database)
        series = _series(database)
        expectation = _expectation(database, series.id)
        database.update_source(
            42,
            DocumentSourceUpdate(disposition="expectations_configured"),
        )

        updated = database.update_series(
            series.id,
            DocumentSeriesUpdate(document_ids=[102, 103]),
        )
        persisted = database.get_expectation(expectation.id)

        assert updated.membership_revision == 2
        assert updated.document_ids == [102, 103]
        assert persisted.cadence == expectation.cadence
        assert persisted.monitoring_mode == "presence"
        assert database.get_source(42).reopen_reasons == ["series_membership_changed"]
    finally:
        database.close()


def test_expectation_eligibility_requires_source_disposition_and_monitoring_policy(
    tmp_path,
) -> None:
    database = _database(tmp_path)
    try:
        _sync_bank(database)
        series = _series(database)
        recurring = _expectation(database, series.id)
        assert database.expectation_is_coverage_eligible(recurring.id) is False

        configured = database.update_source(
            42,
            DocumentSourceUpdate(disposition="expectations_configured"),
        )
        assert configured.review_state == "resolved"
        assert database.expectation_is_coverage_eligible(recurring.id) is True

        irregular = database.create_expectation(
            DocumentExpectationCreate(
                source_correspondent_id=42,
                series_id=series.id,
                name="Recognized but not monitored",
                kind="statement",
                expectation_mode="irregular",
                status="confirmed",
                evidence=ExpectationEvidence(source="user"),
            )
        )
        assert database.expectation_is_coverage_eligible(irregular.id) is False
    finally:
        database.close()


def test_source_defined_expectation_can_monitor_and_be_fulfilled_without_series(
    tmp_path,
) -> None:
    database = _database(tmp_path)
    try:
        _sync_bank(database)
        expectation = database.create_expectation(
            DocumentExpectationCreate(
                source_correspondent_id=42,
                series_id=None,
                name="Annual account tax form",
                kind="tax_document",
                expectation_mode="periodic",
                status="confirmed",
                cadence=Cadence(frequency="annual", expected_day=15),
                monitoring_mode="presence",
                importance="important",
                fulfillment_rule=FulfillmentRule(match_series=False),
                evidence=ExpectationEvidence(
                    source="user",
                    reason_codes=["user_defined_without_series"],
                ),
                acquisition_notes="Download it from the account portal.",
            )
        )
        database.update_source(
            42,
            DocumentSourceUpdate(disposition="expectations_configured"),
        )

        assert database.expectation_is_coverage_eligible(expectation.id) is True
        assert database.get_expectation(expectation.id).acquisition_notes == (
            "Download it from the account portal."
        )

        occurrence = next(
            item
            for item in database.evaluate_coverage(
                date(2026, 10, 20),
                horizon_start=date(2026, 1, 1),
            )
            if item.expectation_id == expectation.id
        )
        assert occurrence.state == "missing"

        fulfilled = database.reassign_occurrence_document(
            occurrence.id,
            501,
            date(2026, 1, 14),
            as_of=date(2026, 10, 20),
        )
        assert fulfilled.state == "fulfilled"
        assert fulfilled.fulfilled_document_id == 501
        assert 501 in database.occurrence_assigned_document_ids()

        refreshed = next(
            item
            for item in database.evaluate_coverage(
                date(2026, 10, 20),
                horizon_start=date(2026, 1, 1),
            )
            if item.expectation_id == expectation.id
        )
        assert refreshed.state == "fulfilled"
        assert refreshed.fulfilled_document_id == 501
        assert database.get_occurrence_detail(refreshed.id).recent_document_ids == [501]
    finally:
        database.close()


def test_cross_kind_coverage_evaluates_missing_fulfilled_and_unmonitored(tmp_path) -> None:
    database = _database(tmp_path)
    try:
        _sync_bank(database)
        statement_series = _series(database)
        statement = _expectation(database, statement_series.id)
        tax_series = database.create_series(
            DocumentSeriesCreate(
                source_correspondent_id=42,
                name="Annual tax form",
                kind="tax_document",
                document_ids=[201],
            )
        )
        tax = database.create_expectation(
            DocumentExpectationCreate(
                source_correspondent_id=42,
                series_id=tax_series.id,
                name="Annual tax form",
                kind="tax_document",
                expectation_mode="periodic",
                status="confirmed",
                cadence=Cadence(frequency="annual", expected_day=15),
                evidence=ExpectationEvidence(source="user"),
            )
        )
        database.update_source(
            42,
            DocumentSourceUpdate(disposition="expectations_configured"),
        )
        database.record_series_member_period(
            statement_series.id,
            101,
            date(2026, 9, 4),
            "2026-09",
        )

        coverage = database.evaluate_coverage(
            date(2026, 10, 20),
            horizon_start=date(2026, 9, 1),
        )
        by_key = {(item.expectation_id, item.period_key): item for item in coverage}

        assert by_key[(statement.id, "2026-09")].state == "fulfilled"
        assert by_key[(statement.id, "2026-10")].state == "missing"
        assert by_key[(tax.id, "2026")].state == "unmonitored"
        assert {item.kind for item in coverage} == {"statement", "tax_document"}
    finally:
        database.close()
