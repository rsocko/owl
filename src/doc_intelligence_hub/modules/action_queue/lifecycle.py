"""Shared Action Queue lifecycle and feedback behavior."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from .config import settings
from .database import VALID_ACTION_TYPES, VALID_STATUSES, Action, ActionFeedback
from .risk_scoring import compute_risk_score

VALID_FEEDBACK_TYPES = {
    "not_an_action",
    "misclassified",
    "wrong_urgency",
    "wrong_amount",
}
VALID_URGENCIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
STATUS_ALIASES = {"done": "completed", "reopen": "pending"}


def normalize_action_status(status: str | None) -> str:
    """Normalize supported external aliases to OWL lifecycle statuses."""
    normalized = STATUS_ALIASES.get(status or "pending", status or "pending")
    if normalized not in VALID_STATUSES:
        raise ValueError(f"Unsupported action status: {status}")
    return normalized


def stored_status_values(status: str) -> set[str]:
    """Return canonical and legacy stored values represented by one status."""
    normalized = normalize_action_status(status)
    return {
        stored
        for stored in {*VALID_STATUSES, *STATUS_ALIASES}
        if normalize_action_status(stored) == normalized
    }


def normalize_utc_datetime(value: datetime) -> datetime:
    """Normalize API timestamps to the UTC-naive convention used by the database."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def serialize_utc_datetime(value: datetime | None) -> str | None:
    """Serialize a database timestamp explicitly as UTC."""
    if value is None:
        return None
    normalized = value if value.tzinfo else value.replace(tzinfo=UTC)
    return normalized.astimezone(UTC).isoformat().replace("+00:00", "Z")


def recalculate_action_risk(action: Action) -> None:
    """Recalculate the action's risk score after a corrected classifier input."""
    action.risk_score = compute_risk_score(
        urgency=action.urgency or "LOW",
        due_date=action.due_date,
        amount=action.amount,
        confidence=action.confidence or 0,
        action_type=action.action_type or "REVIEW",
    )


def transition_action_status(
    action: Action,
    status: str,
    *,
    snoozed_until: datetime | None = None,
    now: datetime | None = None,
) -> bool:
    """Apply one lifecycle transition and its timestamp side effects."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Unsupported action status: {status}")
    if status == "snoozed" and snoozed_until is None:
        raise ValueError("snoozed_until is required when status is 'snoozed'")

    previous = (
        action.status,
        action.completed_at,
        action.acknowledged_at,
        action.snoozed_until,
    )
    transition_time = normalize_utc_datetime(now) if now else datetime.utcnow()
    if snoozed_until:
        snoozed_until = normalize_utc_datetime(snoozed_until)
    action.status = status

    if status == "completed":
        if previous[0] != "completed" or action.completed_at is None:
            action.completed_at = transition_time
        action.snoozed_until = None
    elif status == "pending":
        action.completed_at = None
        action.acknowledged_at = None
        action.snoozed_until = None
        recalculate_action_risk(action)
    elif status == "acknowledged":
        if previous[0] != "acknowledged" or action.acknowledged_at is None:
            action.acknowledged_at = transition_time
        action.completed_at = None
        action.snoozed_until = None
    elif status == "snoozed":
        action.completed_at = None
        action.snoozed_until = snoozed_until
    elif status in {"dismissed", "not_an_action"}:
        action.snoozed_until = None

    current = (
        action.status,
        action.completed_at,
        action.acknowledged_at,
        action.snoozed_until,
    )
    return current != previous


async def sync_action_status(
    db: Session,
    action: Action,
    status: str,
    *,
    logger: logging.Logger,
) -> bool:
    """Best-effort Paperless write-back with successful-state tracking."""
    if not settings.write_to_paperless or not action.document_id:
        return False

    try:
        from .enricher import PaperlessEnricher

        await PaperlessEnricher().sync_status(action.document_id, status)
        action.last_synced_status = status
        db.commit()
        return True
    except Exception as exc:
        logger.warning(
            "Failed to sync status to Paperless for action %d (doc %d): %s",
            action.id,
            action.document_id,
            exc,
        )
        return False


def record_action_feedback(
    db: Session,
    action: Action,
    *,
    feedback_type: str,
    corrected_action_type: str | None = None,
    corrected_urgency: str | None = None,
    corrected_amount: float | None = None,
    reason: str | None = None,
) -> tuple[ActionFeedback, bool]:
    """Record classifier feedback and apply validated corrections to the action."""
    if feedback_type not in VALID_FEEDBACK_TYPES:
        raise ValueError(f"Unsupported feedback type: {feedback_type}")

    normalized_action_type = corrected_action_type.upper() if corrected_action_type else None
    normalized_urgency = corrected_urgency.upper() if corrected_urgency else None
    if normalized_action_type and normalized_action_type not in VALID_ACTION_TYPES:
        raise ValueError("corrected_action_type is not a supported action type")
    if normalized_urgency and normalized_urgency not in VALID_URGENCIES:
        raise ValueError("corrected_urgency is not a supported urgency")

    feedback = ActionFeedback(
        action_id=action.id,
        feedback_type=feedback_type,
        original_action_type=action.action_type,
        corrected_action_type=normalized_action_type,
        original_urgency=action.urgency,
        corrected_urgency=normalized_urgency,
        original_amount=action.amount,
        corrected_amount=corrected_amount,
        reason=reason,
    )
    db.add(feedback)

    action_changed = False
    if feedback_type == "not_an_action":
        action_changed = transition_action_status(action, "not_an_action")
    elif feedback_type == "misclassified" and normalized_action_type:
        action.action_type = normalized_action_type
        action_changed = True
        recalculate_action_risk(action)
    elif feedback_type == "wrong_urgency" and normalized_urgency:
        action.urgency = normalized_urgency
        action_changed = True
        recalculate_action_risk(action)
    elif feedback_type == "wrong_amount" and corrected_amount is not None:
        action.amount = corrected_amount
        action_changed = True
        recalculate_action_risk(action)

    if action_changed:
        action.version = (action.version or 1) + 1
    return feedback, action_changed
