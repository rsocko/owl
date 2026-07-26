"""Base class and registry for analysis rules."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from doc_intelligence_hub.modules.analysis.models import (
    ContextData,
    RuleConfig,
    RuleExecutionResult,
    RuleTier,
)

logger = logging.getLogger(__name__)

# Global rule class registry — maps rule_id → rule class
_RULE_CLASSES: dict[str, type[AnalysisRule]] = {}


class AnalysisRule(ABC):
    """Abstract base class for all analysis rules.

    Subclasses implement `execute()` which receives assembled context data
    and returns a RuleExecutionResult.
    """

    # Subclasses should set these
    rule_id: ClassVar[str] = ""
    default_config: ClassVar[dict[str, Any]] = {}

    def __init__(self, config: RuleConfig) -> None:
        self.config = config

    @abstractmethod
    async def execute(self, context: ContextData) -> RuleExecutionResult:
        """Run the rule against the given context data.

        Returns a RuleExecutionResult — set success=False with error message
        if the rule cannot produce a result (missing data, etc.).
        """
        ...

    @property
    def tier(self) -> RuleTier:
        return self.config.tier

    def get_param(self, key: str, default: Any = None) -> Any:
        """Get a parameter from the rule config, with fallback."""
        return self.config.params.get(key, default)


def register_rule(rule_id: str):
    """Decorator to register a rule class in the global registry."""

    def decorator(cls: type[AnalysisRule]) -> type[AnalysisRule]:
        cls.rule_id = rule_id
        _RULE_CLASSES[rule_id] = cls
        logger.debug("Registered rule class: %s → %s", rule_id, cls.__name__)
        return cls

    return decorator


def get_rule_class(rule_id: str) -> type[AnalysisRule] | None:
    """Look up a registered rule class by ID."""
    return _RULE_CLASSES.get(rule_id)


def get_all_rule_classes() -> dict[str, type[AnalysisRule]]:
    """Return a copy of the full rule class registry."""
    return dict(_RULE_CLASSES)
