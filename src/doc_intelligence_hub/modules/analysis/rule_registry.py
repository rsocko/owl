"""Rule Registry — loads, merges, and manages analysis rule configurations.

Rules come from three sources, merged in priority order:
1. Built-in rule classes (registered via @register_rule)
2. YAML config file (config/analysis-rules.yaml)
3. Database overrides (rule_states table — enables/disables, param tweaks, custom rules)
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from doc_intelligence_hub.modules.analysis import database as db
from doc_intelligence_hub.modules.analysis.models import (
    RuleConfig,
    RuleDisplay,
    RuleRouting,
    RuleTier,
    RuleTrigger,
    TriggerType,
)
from doc_intelligence_hub.modules.analysis.rules.base import get_all_rule_classes

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_RULES_PATH = _PROJECT_ROOT / "config" / "analysis-rules.yaml"

# In-memory rule registry
_rules: dict[str, RuleConfig] = {}


def load_rules(rules_path: str | Path | None = None) -> dict[str, RuleConfig]:
    """Load and merge rules from all sources. Returns the full registry."""
    global _rules
    _rules = {}

    # 1. Load built-in defaults from registered rule classes
    _load_builtin_defaults()

    # 2. Load YAML config (overrides/supplements builtins)
    yaml_path = Path(rules_path) if rules_path else _DEFAULT_RULES_PATH
    if yaml_path.exists():
        _load_yaml_rules(yaml_path)
    else:
        logger.info("No rules YAML found at %s — using built-in defaults only.", yaml_path)

    # 3. Apply DB overrides (enable/disable, param tweaks, custom rules)
    _apply_db_overrides()

    logger.info("Rule registry loaded: %d rules (%d enabled).", len(_rules), sum(1 for r in _rules.values() if r.enabled))
    return dict(_rules)


def get_rule(rule_id: str) -> RuleConfig | None:
    """Get a rule by ID from the in-memory registry."""
    return _rules.get(rule_id)


def list_rules() -> list[RuleConfig]:
    """List all rules in the registry."""
    return list(_rules.values())


def update_rule_config(rule_id: str, updates: dict[str, Any]) -> RuleConfig | None:
    """Update a rule's configuration and persist to DB."""
    rule = _rules.get(rule_id)
    if not rule:
        return None

    # Apply updates
    if "enabled" in updates:
        rule.enabled = updates["enabled"]
    if "params" in updates:
        rule.params.update(updates["params"])
    if "routing" in updates:
        rule.routing = RuleRouting(**updates["routing"])
    if "display" in updates:
        rule.display = RuleDisplay(**updates["display"])

    # Persist to DB
    db.upsert_rule_state(
        rule_id,
        enabled=rule.enabled,
        params=rule.params if "params" in updates else None,
        routing=updates.get("routing"),
        display=updates.get("display"),
    )

    return rule


def create_custom_rule(config: dict[str, Any]) -> RuleConfig:
    """Create a new custom rule and persist it."""
    rule = RuleConfig(
        id=config["id"],
        name=config["name"],
        description=config.get("description", ""),
        tier=RuleTier(config.get("tier", "basic")),
        enabled=True,
        trigger=RuleTrigger(**config.get("trigger", {"type": "manual"})),
        analyzer=config.get("analyzer", ""),
        params=config.get("params", {}),
        routing=RuleRouting(**config.get("routing", {})),
        display=RuleDisplay(**config.get("display", {})),
        source="custom",
    )

    _rules[rule.id] = rule

    # Persist full definition to DB
    db.upsert_rule_state(
        rule.id,
        enabled=True,
        params=rule.params,
        routing=config.get("routing", {}),
        display=config.get("display", {}),
        source="custom",
        definition=config,
    )

    return rule


def delete_custom_rule(rule_id: str) -> bool:
    """Delete a custom rule from registry and DB."""
    rule = _rules.get(rule_id)
    if not rule or rule.source != "custom":
        return False

    del _rules[rule_id]
    db.delete_rule_state(rule_id)
    return True


def get_rules_for_trigger(trigger_type: TriggerType, *, document_type: str | None = None) -> list[RuleConfig]:
    """Get enabled rules matching a trigger type and optional document type filter."""
    matching = []
    for rule in _rules.values():
        if not rule.enabled:
            continue
        if rule.trigger.type != trigger_type:
            continue

        # Check document type filter
        if document_type and rule.trigger.filter:
            rule_doc_type = rule.trigger.filter.document_type
            if rule_doc_type:
                if isinstance(rule_doc_type, list):
                    if document_type not in rule_doc_type:
                        continue
                elif rule_doc_type != document_type:
                    continue

        matching.append(rule)

    return matching


# ------------------------------------------------------------------
# Internal loaders
# ------------------------------------------------------------------


def _load_builtin_defaults() -> None:
    """Create default RuleConfig entries for all registered rule classes."""
    rule_classes = get_all_rule_classes()

    # Default configs for built-in rules
    defaults: dict[str, dict[str, Any]] = {
        "monthly-spend-comparison": {
            "name": "Monthly Spend vs Average",
            "description": "Compare statement amount to rolling 3-month average for the same series",
            "tier": "basic",
            "trigger": {"type": "document_added", "filter": {"document_type": "statement"}},
            "context": ["current_document", "series_history: 6", {"extracted_fields": ["total_amount", "statement_period"]}],
            "analyzer": "builtin:spend_comparison",
            "params": {"comparison_window": 3},
            "routing": {"default": "informational", "escalation": [
                {"condition": "pct_change > 50", "route": "actionable", "severity": "warning", "mc_alert": True},
                {"condition": "pct_change > 100", "route": "actionable", "severity": "critical", "mc_alert": True},
            ]},
            "display": {"card_type": "comparison", "highlight_fields": ["total_amount", "pct_change"]},
        },
        "eob-match-review": {
            "name": "EOB Match Confidence",
            "description": "Flag EOB matches below confidence threshold for human review",
            "tier": "basic",
            "trigger": {"type": "document_added", "filter": {"document_type": ["eob", "bill"]}},
            "context": ["current_document", "related_matches"],
            "analyzer": "builtin:eob_match_review",
            "params": {"confidence_threshold": 75},
            "routing": {"default": "actionable"},
            "display": {"card_type": "alert"},
        },
        "series-anomaly": {
            "name": "Statement Grouping Anomaly",
            "description": "Detect anomalies in statement series grouping or timing",
            "tier": "basic",
            "trigger": {"type": "document_added"},
            "context": ["current_document", "series_history: 6"],
            "analyzer": "builtin:series_anomaly",
            "routing": {"default": "actionable"},
            "display": {"card_type": "alert"},
        },
        "missing-statement": {
            "name": "Missing Expected Statement",
            "description": "Detect missing statements based on series recurrence patterns",
            "tier": "basic",
            "trigger": {"type": "schedule", "cron": "0 2 * * *"},
            "context": ["series_history: 3"],
            "analyzer": "builtin:missing_statement",
            "params": {"grace_days": 7},
            "routing": {"default": "actionable", "escalation": [
                {"condition": "days_late > 30", "route": "actionable", "severity": "critical", "mc_alert": True},
            ]},
            "display": {"card_type": "alert"},
        },
        "statement-received": {
            "name": "Statement Arrival Confirmation",
            "description": "Confirm that a statement arrived on time",
            "tier": "basic",
            "trigger": {"type": "document_added", "filter": {"document_type": "statement"}},
            "context": ["current_document"],
            "analyzer": "builtin:statement_received",
            "routing": {"default": "informational"},
            "display": {"card_type": "summary"},
        },
        "spend-spike": {
            "name": "Spend Spike Detection",
            "description": "Detect sudden spend increases above a configurable threshold",
            "tier": "basic",
            "trigger": {"type": "document_added", "filter": {"document_type": "statement"}},
            "context": ["current_document", "series_history: 6"],
            "analyzer": "builtin:spend_spike",
            "params": {"spike_threshold_pct": 30},
            "routing": {"default": "informational", "escalation": [
                {"condition": "pct_change > 50", "route": "actionable", "severity": "warning", "mc_alert": True},
                {"condition": "pct_change > 100", "route": "actionable", "severity": "critical", "mc_alert": True},
            ]},
            "display": {"card_type": "alert", "highlight_fields": ["pct_change", "current_amount"]},
        },
        "document-classification": {
            "name": "Document Classification",
            "description": "Classify unknown or ambiguous documents using AI",
            "tier": "llm",
            "trigger": {"type": "document_added"},
            "context": ["current_document"],
            "analyzer": "llm:classify",
            "params": {"categories": ["statement", "bill", "eob", "receipt", "letter", "notice", "contract", "other"]},
            "routing": {"default": "informational"},
            "display": {"card_type": "summary"},
        },
        "coverage-analysis": {
            "name": "Insurance Coverage Analysis",
            "description": "Analyze insurance coverage patterns from EOB data using AI",
            "tier": "llm",
            "trigger": {"type": "schedule", "cron": "0 3 * * 0"},
            "context": ["series_history: 10"],
            "analyzer": "llm:coverage_analysis",
            "routing": {"default": "informational"},
            "display": {"card_type": "trend"},
        },
        "n8n-webhook": {
            "name": "n8n Workflow",
            "description": "Run an external n8n workflow for custom analysis",
            "tier": "n8n",
            "trigger": {"type": "manual"},
            "context": ["current_document"],
            "analyzer": "n8n:webhook",
            "params": {"webhook_url": "", "timeout": 30},
            "routing": {"default": "informational"},
            "display": {"card_type": "summary"},
            "enabled": False,  # Disabled by default — needs webhook_url configured
        },
        "cross-reference-check": {
            "name": "Cross-Reference Check",
            "description": "Cross-reference documents against external systems via n8n",
            "tier": "n8n",
            "trigger": {"type": "schedule", "cron": "0 4 * * 0"},
            "context": ["current_document"],
            "analyzer": "n8n:cross_reference",
            "params": {"webhook_url": "", "timeout": 60, "check_systems": ["monarch"]},
            "routing": {"default": "informational"},
            "display": {"card_type": "table"},
            "enabled": False,
        },
    }

    for rule_id, cls in rule_classes.items():
        config_data = defaults.get(rule_id, {})
        if not config_data:
            # Minimal config for undocumented rules
            config_data = {
                "name": rule_id.replace("-", " ").title(),
                "trigger": {"type": "manual"},
            }

        _rules[rule_id] = RuleConfig(
            id=rule_id,
            name=config_data.get("name", rule_id),
            description=config_data.get("description", ""),
            tier=RuleTier(config_data.get("tier", "basic")),
            enabled=config_data.get("enabled", True),
            trigger=RuleTrigger(**config_data.get("trigger", {"type": "manual"})),
            context=config_data.get("context"),
            analyzer=config_data.get("analyzer", ""),
            params=config_data.get("params", {}),
            routing=RuleRouting(**config_data.get("routing", {})),
            display=RuleDisplay(**config_data.get("display", {})),
            source="builtin",
        )


def _load_yaml_rules(path: Path) -> None:
    """Load rules from YAML config, merging with builtins."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f)

        if not data or "rules" not in data:
            return

        for rule_data in data["rules"]:
            rule_id = rule_data.get("id")
            if not rule_id:
                continue

            trigger_data = rule_data.get("trigger", {"type": "manual"})
            routing_data = rule_data.get("routing", {})
            display_data = rule_data.get("display", {})

            rule = RuleConfig(
                id=rule_id,
                name=rule_data.get("name", rule_id),
                description=rule_data.get("description", ""),
                tier=RuleTier(rule_data.get("tier", "basic")),
                enabled=rule_data.get("enabled", True),
                trigger=RuleTrigger(**trigger_data),
                context=rule_data.get("context"),
                analyzer=rule_data.get("analyzer", ""),
                params=rule_data.get("params", {}),
                routing=RuleRouting(**routing_data),
                display=RuleDisplay(**display_data),
                source="yaml",
            )

            # Merge with existing builtin (YAML overrides builtin params)
            if rule_id in _rules:
                existing = _rules[rule_id]
                rule.source = "yaml"
                # Preserve builtin context if YAML doesn't specify
                if not rule.context:
                    rule.context = existing.context
            _rules[rule_id] = rule

        logger.info("Loaded %d rules from YAML: %s", len(data["rules"]), path)

    except Exception as exc:
        logger.warning("Failed to load rules YAML from %s: %s", path, exc)


def _apply_db_overrides() -> None:
    """Apply persisted rule state overrides from the database."""
    try:
        states = db.list_rule_states()
    except Exception:
        logger.debug("Could not load rule states from DB (table may not exist yet)")
        return

    for state in states:
        rule_id = state["id"]

        # Handle custom rules stored in DB
        if state["source"] == "custom" and rule_id not in _rules and state.get("definition"):
            definition = state["definition"]
            _rules[rule_id] = RuleConfig(
                id=rule_id,
                name=definition.get("name", rule_id),
                description=definition.get("description", ""),
                tier=RuleTier(definition.get("tier", "basic")),
                enabled=state["enabled"],
                trigger=RuleTrigger(**definition.get("trigger", {"type": "manual"})),
                context=definition.get("context"),
                analyzer=definition.get("analyzer", ""),
                params=definition.get("params", {}),
                routing=RuleRouting(**definition.get("routing", {})),
                display=RuleDisplay(**definition.get("display", {})),
                source="custom",
            )
            continue

        # Apply overrides to existing rules
        if rule_id in _rules:
            rule = _rules[rule_id]
            rule.enabled = state["enabled"]
            if state.get("params"):
                rule.params.update(state["params"])
            if state.get("routing"):
                rule.routing = RuleRouting(**state["routing"])
            if state.get("display"):
                rule.display = RuleDisplay(**state["display"])
            if state.get("last_run_at"):
                # DB returns ISO string — parse back to datetime
                raw = state["last_run_at"]
                if isinstance(raw, str):
                    try:
                        rule.last_run_at = datetime.fromisoformat(raw)
                    except (ValueError, TypeError):
                        pass
                else:
                    rule.last_run_at = raw
            if state.get("last_run_status"):
                rule.last_run_status = state["last_run_status"]
            rule.insight_count = state.get("insight_count", 0)
