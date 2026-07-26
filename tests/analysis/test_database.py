"""Tests for the analysis engine database layer."""

from __future__ import annotations

import pytest

from doc_intelligence_hub.modules.analysis import database as db


@pytest.fixture(autouse=True)
def _setup_db(tmp_path):
    """Use an in-memory database for each test."""
    db.configure(f"sqlite:///{tmp_path}/test_analysis.db")
    db.init_db()
    yield


class TestInsightCRUD:
    def test_create_and_get_insight(self):
        insight = db.create_insight(
            rule_id="test-rule",
            rule_name="Test Rule",
            insight_type="comparison",
            route="informational",
            severity="info",
            title="Test insight",
            summary="This is a test",
            detail={"key": "value"},
            series_id="series-1",
            document_ids=[1, 2],
            correspondent="Chase",
            period="Jun 2024",
        )

        assert insight["id"]
        assert insight["rule_id"] == "test-rule"
        assert insight["title"] == "Test insight"
        assert insight["detail"] == {"key": "value"}
        assert insight["document_ids"] == [1, 2]

        fetched = db.get_insight(insight["id"])
        assert fetched is not None
        assert fetched["id"] == insight["id"]
        assert fetched["status"] == "new"

    def test_list_insights_with_filters(self):
        db.create_insight(rule_id="r1", rule_name="R1", insight_type="comparison", route="informational", title="Info 1", severity="info")
        db.create_insight(rule_id="r2", rule_name="R2", insight_type="anomaly", route="actionable", title="Action 1", severity="warning")
        db.create_insight(rule_id="r1", rule_name="R1", insight_type="comparison", route="informational", title="Info 2", severity="info")

        # All (non-superseded)
        items, total = db.list_insights()
        assert total == 3

        # Filter by route
        items, total = db.list_insights(route="informational")
        assert total == 2

        # Filter by rule_id
        items, total = db.list_insights(rule_id="r2")
        assert total == 1
        assert items[0]["title"] == "Action 1"

        # Filter by severity
        items, total = db.list_insights(severity="warning")
        assert total == 1

    def test_update_insight_status(self):
        insight = db.create_insight(rule_id="r1", rule_name="R1", insight_type="trend", route="informational", title="Test")

        updated = db.update_insight_status(insight["id"], "acknowledged")
        assert updated["status"] == "acknowledged"
        assert updated["acknowledged_at"] is not None

    def test_bulk_update_status(self):
        i1 = db.create_insight(rule_id="r1", rule_name="R1", insight_type="trend", route="informational", title="T1")
        i2 = db.create_insight(rule_id="r1", rule_name="R1", insight_type="trend", route="informational", title="T2")

        count = db.bulk_update_insight_status([i1["id"], i2["id"]], "archived")
        assert count == 2

    def test_supersede_insight(self):
        i1 = db.create_insight(rule_id="r1", rule_name="R1", insight_type="comparison", route="informational", title="Old", period="Jun 2024", series_id="s1")

        superseded_id = db.supersede_insight("r1", "s1", "Jun 2024")
        assert superseded_id == i1["id"]

        # Old insight should be superseded
        old = db.get_insight(i1["id"])
        assert old["status"] == "superseded"

        # Superseded items excluded from default listing
        items, total = db.list_insights()
        assert total == 0

    def test_get_insight_summary(self):
        db.create_insight(rule_id="r1", rule_name="R1", insight_type="comparison", route="informational", title="I1", severity="info")
        db.create_insight(rule_id="r2", rule_name="R2", insight_type="anomaly", route="actionable", title="I2", severity="warning")

        summary = db.get_insight_summary()
        assert summary["total"] == 2
        assert summary["new"] == 2
        assert summary["by_type"]["comparison"] == 1
        assert summary["by_severity"]["warning"] == 1
        assert summary["by_route"]["informational"] == 1


class TestInsightHistory:
    def test_create_and_get_history(self):
        db.create_history_entry(rule_id="r1", series_id="s1", period="Jan 2024", metric_name="total_amount", metric_value=1500.0)
        db.create_history_entry(rule_id="r1", series_id="s1", period="Feb 2024", metric_name="total_amount", metric_value=1600.0)
        db.create_history_entry(rule_id="r1", series_id="s1", period="Jan 2024", metric_name="pct_change", metric_value=5.0)

        entries = db.get_history_for_series("s1")
        assert len(entries) == 3

        entries = db.get_history_for_series("s1", metric_name="total_amount")
        assert len(entries) == 2


class TestRuleState:
    def test_upsert_and_get_rule_state(self):
        state = db.upsert_rule_state("r1", enabled=True, params={"threshold": 50}, source="builtin")
        assert state["id"] == "r1"
        assert state["enabled"] is True
        assert state["params"]["threshold"] == 50

        fetched = db.get_rule_state("r1")
        assert fetched is not None
        assert fetched["params"]["threshold"] == 50

    def test_history_dedup_on_rerun(self):
        """Test that rerunning a rule updates existing history entries instead of duplicating."""
        db.create_history_entry(
            rule_id="spend", series_id="s1", period="Jun 2024",
            metric_name="pct_change", metric_value=15.0,
        )
        # Second run with updated value for same key
        db.create_history_entry(
            rule_id="spend", series_id="s1", period="Jun 2024",
            metric_name="pct_change", metric_value=18.0,
        )
        entries = db.get_history_for_series("s1", metric_name="pct_change")
        assert len(entries) == 1  # Should NOT be 2
        assert entries[0]["metric_value"] == 18.0  # Updated value

    def test_update_rule_state(self):
        db.upsert_rule_state("r1", enabled=True, params={"threshold": 50})
        db.upsert_rule_state("r1", enabled=False, params={"threshold": 75})

        fetched = db.get_rule_state("r1")
        assert fetched["enabled"] is False
        assert fetched["params"]["threshold"] == 75

    def test_delete_rule_state(self):
        db.upsert_rule_state("r1", enabled=True)
        assert db.delete_rule_state("r1") is True
        assert db.get_rule_state("r1") is None
        assert db.delete_rule_state("r1") is False

    def test_insight_count_increment(self):
        db.upsert_rule_state("r1")
        db.upsert_rule_state("r1", insight_count_increment=3)
        state = db.get_rule_state("r1")
        assert state["insight_count"] == 3

        db.upsert_rule_state("r1", insight_count_increment=2)
        state = db.get_rule_state("r1")
        assert state["insight_count"] == 5
