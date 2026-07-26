"""Analysis Engine module — configurable rules, insights, and routing.

This module transforms Document Intelligence from a "fix errors" tool
into a proactive document analysis platform.
"""

from doc_intelligence_hub.modules.analysis.database import init_db  # noqa: F401
from doc_intelligence_hub.modules.analysis.rule_executor import (  # noqa: F401
    execute_rule,
    execute_scheduled_batch,
    execute_trigger,
)
from doc_intelligence_hub.modules.analysis.rule_registry import (  # noqa: F401
    load_rules,
    get_rule,
    list_rules,
)

__all__ = [
    "init_db",
    "load_rules",
    "get_rule",
    "list_rules",
    "execute_rule",
    "execute_trigger",
    "execute_scheduled_batch",
]
