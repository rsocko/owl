"""Analysis Engine module — configurable rules, insights, and routing.

This module transforms Document Intelligence from a "fix errors" tool
into a proactive document analysis platform.
"""

from doc_intelligence_hub.modules.analysis.database import init_db
from doc_intelligence_hub.modules.analysis.rule_executor import (
    execute_rule,
    execute_scheduled_batch,
    execute_trigger,
)
from doc_intelligence_hub.modules.analysis.rule_registry import (
    get_rule,
    list_rules,
    load_rules,
)

__all__ = [
    "execute_rule",
    "execute_scheduled_batch",
    "execute_trigger",
    "get_rule",
    "init_db",
    "list_rules",
    "load_rules",
]
