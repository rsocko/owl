"""Tests for correction-hint persistence in modules.triage.database (issue #171)."""

from __future__ import annotations

import pytest

from doc_intelligence_hub.modules.triage.database import (
    configure,
    create_extraction_correction,
    get_corrections_for_document,
    get_latest_label_anchor,
    group_corrections_by_correspondent,
    init_db,
    list_corrections_for_correspondent,
)


@pytest.fixture()
def db():
    """Create an in-memory SQLite database for each test."""
    configure("sqlite:///:memory:")
    init_db()
    yield


class TestCreateExtractionCorrectionStoresHintFields:
    def test_stores_correspondent_and_label_anchor(self, db):
        correction = create_extraction_correction(
            document_id=100,
            field_name="document_amount",
            original_value="$50.00",
            corrected_value="$142.50",
            correction_type="corrected",
            correspondent="City Utilities",
            label_anchor="Total Due:",
        )
        assert correction["correspondent"] == "City Utilities"
        assert correction["label_anchor"] == "Total Due:"

    def test_defaults_to_none_when_not_supplied(self, db):
        correction = create_extraction_correction(
            document_id=101,
            field_name="document_amount",
            corrected_value="$10.00",
            correction_type="added",
        )
        assert correction["correspondent"] is None
        assert correction["label_anchor"] is None

    def test_round_trips_through_get_corrections_for_document(self, db):
        create_extraction_correction(
            document_id=102,
            field_name="account_identifier",
            corrected_value="ending 4321",
            correction_type="confirmed",
            correspondent="Chase Visa",
            label_anchor="Account Number:",
        )
        corrections = get_corrections_for_document(102)
        assert len(corrections) == 1
        assert corrections[0]["correspondent"] == "Chase Visa"
        assert corrections[0]["label_anchor"] == "Account Number:"


class TestListCorrectionsForCorrespondent:
    def test_filters_by_correspondent_and_field(self, db):
        create_extraction_correction(
            document_id=1,
            field_name="document_amount",
            corrected_value="$142.50",
            correction_type="corrected",
            correspondent="City Utilities",
        )
        create_extraction_correction(
            document_id=2,
            field_name="document_amount",
            corrected_value="$99.00",
            correction_type="corrected",
            correspondent="Other Co",
        )
        create_extraction_correction(
            document_id=3,
            field_name="account_identifier",
            corrected_value="ending 1234",
            correction_type="corrected",
            correspondent="City Utilities",
        )

        results = list_corrections_for_correspondent("City Utilities", "document_amount")
        assert len(results) == 1
        assert results[0]["document_id"] == 1

    def test_excludes_added_corrections_by_default(self, db):
        create_extraction_correction(
            document_id=1,
            field_name="account_identifier",
            corrected_value="ending 1234",
            correction_type="added",
            correspondent="City Utilities",
        )
        assert list_corrections_for_correspondent("City Utilities", "account_identifier") == []

    def test_no_matches_returns_empty_list(self, db):
        assert list_corrections_for_correspondent("Nonexistent Co", "document_amount") == []


class TestGroupCorrectionsByCorrespondent:
    def test_groups_rows_with_resolved_correspondent(self, db):
        create_extraction_correction(
            document_id=1,
            field_name="document_amount",
            corrected_value="$142.50",
            correction_type="corrected",
            correspondent="City Utilities",
        )
        create_extraction_correction(
            document_id=2,
            field_name="document_amount",
            corrected_value="$88.00",
            correction_type="confirmed",
            correspondent="City Utilities",
        )
        create_extraction_correction(
            document_id=3,
            field_name="document_amount",
            corrected_value="$12.00",
            correction_type="corrected",
            correspondent="Other Co",
        )

        grouped = group_corrections_by_correspondent("document_amount")
        assert sorted(grouped) == ["City Utilities", "Other Co"]
        assert len(grouped["City Utilities"]) == 2

    def test_excludes_rows_with_no_resolved_correspondent(self, db):
        create_extraction_correction(
            document_id=1,
            field_name="document_amount",
            corrected_value="$142.50",
            correction_type="corrected",
            correspondent=None,
        )
        assert group_corrections_by_correspondent("document_amount") == {}

    def test_scopes_to_requested_field_name(self, db):
        create_extraction_correction(
            document_id=1,
            field_name="account_identifier",
            corrected_value="ending 1234",
            correction_type="corrected",
            correspondent="City Utilities",
        )
        assert group_corrections_by_correspondent("document_amount") == {}


class TestGetLatestLabelAnchor:
    def test_returns_most_recent_anchor_for_correspondent_and_field(self, db):
        import time

        create_extraction_correction(
            document_id=1,
            field_name="document_amount",
            corrected_value="$50.00",
            correction_type="corrected",
            correspondent="City Utilities",
            label_anchor="Balance Due:",
        )
        time.sleep(0.01)
        create_extraction_correction(
            document_id=2,
            field_name="document_amount",
            corrected_value="$142.50",
            correction_type="corrected",
            correspondent="City Utilities",
            label_anchor="Total Due:",
        )

        assert get_latest_label_anchor("City Utilities", "document_amount") == "Total Due:"

    def test_returns_none_when_no_correspondent(self, db):
        assert get_latest_label_anchor(None, "document_amount") is None
        assert get_latest_label_anchor("", "document_amount") is None

    def test_returns_none_when_no_hint_exists(self, db):
        assert get_latest_label_anchor("Unknown Co", "document_amount") is None

    def test_ignores_added_corrections(self, db):
        create_extraction_correction(
            document_id=1,
            field_name="account_identifier",
            corrected_value="ending 1234",
            correction_type="added",
            correspondent="City Utilities",
            label_anchor="Account Number:",
        )
        assert get_latest_label_anchor("City Utilities", "account_identifier") is None

    def test_ignores_rows_without_a_label_anchor(self, db):
        create_extraction_correction(
            document_id=1,
            field_name="document_amount",
            corrected_value="$50.00",
            correction_type="corrected",
            correspondent="City Utilities",
        )
        assert get_latest_label_anchor("City Utilities", "document_amount") is None
