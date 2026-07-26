"""Tests for the rule registry and configuration loading."""

from __future__ import annotations

import pytest

from doc_intelligence_hub.modules.analysis import database as db
from doc_intelligence_hub.modules.analysis.rule_registry import (
    create_custom_rule,
    delete_custom_rule,
    get_rule,
    get_rules_for_trigger,
    list_rules,
    load_rules,
    update_rule_config,
)
from doc_intelligence_hub.modules.analysis.models import TriggerType


@pytest.fixture(autouse=True)
def _setup(tmp_path):
    """Use temp DB and load rules fresh for each test."""
    db.configure(f"sqlite:///{tmp_path}/test_analysis.db")
    db.init_db()
    load_rules()
    yield


class TestRuleRegistry:
    def test_load_rules_returns_builtin_rules(self):
        rules = list_rules()
        assert len(rules) > 0

        rule_ids = {r.id for r in rules}
        assert "monthly-spend-comparison" in rule_ids
        assert "eob-match-review" in rule_ids
        assert "missing-statement" in rule_ids
        assert "statement-received" in rule_ids
        assert "spend-spike" in rule_ids

    def test_get_rule_by_id(self):
        rule = get_rule("monthly-spend-comparison")
        assert rule is not None
        assert rule.name == "Monthly Spend vs Average"
        assert rule.tier.value == "basic"
        assert rule.enabled is True

    def test_get_nonexistent_rule(self):
        assert get_rule("does-not-exist") is None

    def test_rules_for_trigger_document_added(self):
        rules = get_rules_for_trigger(TriggerType.DOCUMENT_ADDED)
        assert len(rules) > 0
        # Should include statement-received, series-anomaly, etc.
        rule_ids = {r.id for r in rules}
        assert "statement-received" in rule_ids or "series-anomaly" in rule_ids

    def test_rules_for_trigger_with_doc_type_filter(self):
        rules = get_rules_for_trigger(TriggerType.DOCUMENT_ADDED, document_type="statement")
        rule_ids = {r.id for r in rules}
        assert "monthly-spend-comparison" in rule_ids
        assert "statement-received" in rule_ids

    def test_rules_for_trigger_schedule(self):
        rules = get_rules_for_trigger(TriggerType.SCHEDULE)
        rule_ids = {r.id for r in rules}
        assert "missing-statement" in rule_ids

    def test_update_rule_config(self):
        rule = update_rule_config("monthly-spend-comparison", {"enabled": False})
        assert rule is not None
        assert rule.enabled is False

        # Verify it's persisted
        state = db.get_rule_state("monthly-spend-comparison")
        assert state is not None
        assert state["enabled"] is False

    def test_update_rule_params(self):
        rule = update_rule_config("monthly-spend-comparison", {"params": {"comparison_window": 6}})
        assert rule.params["comparison_window"] == 6

    def test_create_custom_rule(self):
        rule = create_custom_rule(
            {
                "id": "my-custom-rule",
                "name": "My Custom Rule",
                "description": "A custom test rule",
                "tier": "basic",
                "trigger": {"type": "manual"},
                "params": {"threshold": 42},
            }
        )

        assert rule.id == "my-custom-rule"
        assert rule.source == "custom"

        # Should be in registry
        fetched = get_rule("my-custom-rule")
        assert fetched is not None

    def test_delete_custom_rule(self):
        create_custom_rule(
            {
                "id": "deletable-rule",
                "name": "Deletable",
                "trigger": {"type": "manual"},
            }
        )

        assert delete_custom_rule("deletable-rule") is True
        assert get_rule("deletable-rule") is None

    def test_cannot_delete_builtin_rule(self):
        assert delete_custom_rule("monthly-spend-comparison") is False

    def test_disabled_rules_excluded_from_trigger_matching(self):
        update_rule_config("statement-received", {"enabled": False})
        rules = get_rules_for_trigger(TriggerType.DOCUMENT_ADDED, document_type="statement")
        rule_ids = {r.id for r in rules}
        assert "statement-received" not in rule_ids

    def test_rule_tiers_present(self):
        rules = list_rules()
        tiers = {r.tier.value for r in rules}
        assert "basic" in tiers
        assert "llm" in tiers
        assert "n8n" in tiers
