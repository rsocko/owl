"""Versioned contracts for the analysis invalidation / staleness mechanism (issue #114).

Schema versions are recorded so a later change to comparison logic can be
told apart from the data it produced.
"""

from __future__ import annotations

import dataclasses
from enum import Enum

# Bump whenever the shape/semantics of a persisted record change in a way
# that should be distinguishable from older rows.
INVALIDATION_SCHEMA_VERSION = "analysis-invalidation-v1"


class InvalidationReason(str, Enum):
    """Why an ``InvalidationEvent`` was created."""

    VERSION_CHANGED = "version_changed"
    ROLLBACK = "rollback"
    SIMULATED_VERSION_CHANGE = "simulated_version_change"
    MANUAL_ALL = "manual_all"
    MANUAL_SCOPE = "manual_scope"
    MANUAL_DOCUMENT = "manual_document"


class StaleReason(str, Enum):
    """Machine-readable reason a module's cached analysis is considered stale."""

    CONTENT_VERSION_CHANGED = "content_version_changed"
    METADATA_CHANGED = "metadata_changed"
    MODULE_VERSION_CHANGED = "module_version_changed"
    CONFIG_CHANGED = "config_changed"
    MANUAL_INVALIDATION = "manual_invalidation"


class FreshnessStatus(str, Enum):
    """Result of asking "is my last analysis for this document still fresh?"."""

    FRESH = "fresh"
    STALE = "stale"
    # No fingerprint has ever been recorded for this (document, module) pair.
    UNKNOWN = "unknown"


# Manual invalidation reasons map 1:1 onto invalidation reasons above; kept
# distinct so ``StaleReason`` never needs to grow every time a new
# ``InvalidationReason`` is added, and so downstream modules only ever see
# the small, stable stale-reason vocabulary.
_MANUAL_REASONS = {
    InvalidationReason.MANUAL_ALL,
    InvalidationReason.MANUAL_SCOPE,
    InvalidationReason.MANUAL_DOCUMENT,
}


def stale_reason_for_invalidation(reason: InvalidationReason) -> StaleReason:
    """Map an invalidation-event reason onto the ``StaleReason`` a mark should carry."""
    if reason in _MANUAL_REASONS:
        return StaleReason.MANUAL_INVALIDATION
    return StaleReason.CONTENT_VERSION_CHANGED


def is_manual_reason(reason: InvalidationReason) -> bool:
    """Whether ``reason`` is an operator-triggered manual invalidation.

    Manual invalidations are deliberately never treated as "duplicate
    delivery" no-ops (unlike real/simulated version-change transitions):
    an operator re-running ``invalidate`` should always be honored, even if
    the document's checksum/metadata happen not to have changed.
    """
    return reason in _MANUAL_REASONS


@dataclasses.dataclass(frozen=True)
class FreshnessResult:
    """Outcome of ``AnalysisFreshnessService.check_freshness``."""

    status: FreshnessStatus
    reasons: tuple[StaleReason, ...] = ()
    fingerprint_id: int | None = None
    checked_at: str | None = None

    @property
    def is_fresh(self) -> bool:
        return self.status == FreshnessStatus.FRESH

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "reasons": [r.value for r in self.reasons],
            "fingerprint_id": self.fingerprint_id,
            "checked_at": self.checked_at,
        }
