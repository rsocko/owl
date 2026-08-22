"""Analysis rules sub-package.

Importing this package auto-registers all built-in rule classes.
"""

# Import rule modules to trigger @register_rule decorators
from doc_intelligence_hub.modules.analysis.rules import (
    action_queue_rule as _action_queue,  # noqa: F401
)
from doc_intelligence_hub.modules.analysis.rules import basic_rules as _basic  # noqa: F401
from doc_intelligence_hub.modules.analysis.rules import llm_rules as _llm  # noqa: F401
from doc_intelligence_hub.modules.analysis.rules import n8n_rules as _n8n  # noqa: F401
from doc_intelligence_hub.modules.analysis.rules.base import (  # noqa: F401
    AnalysisRule,
    get_all_rule_classes,
    get_rule_class,
    register_rule,
)
