"""Shared Action Queue lifecycle and feedback behavior."""

from __future__ import annotations

import json
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
VALID_REVIEW_STATES = {"ready", "needs_review", "resolved_no_action"}
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


def action_has_critical_details(action: Action) -> bool:
    """Return whether a corrected classification is safe to keep action-ready."""
    if not (action.title or "").strip():
        return False
    if (action.action_type or "").upper() == "PAY" and action.amount is None:
        return False
    return True


def refresh_recommended_cta(action: Action) -> None:
    """Recompute the contextual CTA after a type or detail correction."""
    from .analyzer import _normalize_cta

    previous = action.recommended_cta
    if isinstance(previous, str):
        try:
            previous = json.loads(previous)
        except (json.JSONDecodeError, TypeError):
            previous = None
    # A corrected action type must replace the previous authoritative CTA.
    # Keep only additive metadata that may still be useful to consumers.
    raw = (
        {"metadata": previous["metadata"]}
        if isinstance(previous, dict) and isinstance(previous.get("metadata"), dict)
        else None
    )
    normalized = _normalize_cta(
        raw,
        {
            "action_type": action.action_type,
            "title": action.title,
            "amount": action.amount,
        },
        {"extracted_data": action.extracted_data or {}},
    )
    action.recommended_cta = json.dumps(normalized)


def route_action_to_review(db: Session, action: Action, *, reason: str) -> str:
    """Mark an action untrusted and create its deep-linkable Needs Review item."""
    from doc_intelligence_hub.modules.triage.database import (
        create_action_classification_review,
    )
    from doc_intelligence_hub.modules.triage.database import (
        init_db as init_triage_db,
    )

    db.flush()
    init_triage_db()
    item = create_action_classification_review(
        action_id=action.id,
        document_id=action.document_id,
        confidence=action.confidence or 0,
        reason=reason,
        metadata={
            "document_title": action.document_title,
            "title": action.title,
            "summary": action.summary,
            "action_type": action.action_type,
            "due_date": action.due_date.isoformat() if action.due_date else None,
            "amount": action.amount,
            "recommended_cta": json.loads(action.recommended_cta)
            if isinstance(action.recommended_cta, str) and action.recommended_cta.startswith("{")
            else None,
        },
    )
    action.action_ready = False
    action.review_state = "needs_review"
    action.review_item_id = item["id"]
    return item["id"]


def mark_action_ready(action: Action) -> None:
    action.action_ready = True
    action.review_state = "ready"
    action.review_item_id = None


def document_action_status(db: Session, document_id: int) -> str:
    """Derive one Paperless document status from all non-superseded OWL actions."""
    actions = (
        db.query(Action)
        .filter(
            Action.document_id == document_id,
            Action.superseded_by_action_id.is_(None),
        )
        .all()
    )
    statuses = {
        normalize_action_status(action.status)
        for action in actions
        if normalize_action_status(action.status) != "not_an_action"
    }
    if not statuses:
        return "not_an_action"
    if "pending" in statuses:
        return "pending"
    if "acknowledged" in statuses:
        return "acknowledged"
    if "snoozed" in statuses:
        return "snoozed"
    if "completed" in statuses:
        return "completed"
    return "dismissed"


async def project_action_metadata(
    action: Action,
    *,
    action_status: str | None,
) -> None:
    """Project current durable facts while replacing stale action inference."""
    if not settings.write_to_paperless or not action.document_id:
        return
    from .enricher import PaperlessEnricher

    await PaperlessEnricher().enrich_document(
        action.document_id,
        {
            "amount": action.amount,
            "extracted_data": action.extracted_data or {},
        },
        action_status=action_status,
        clear_action_inference=True,
    )


async def file_action_in_paperless(db: Session, action: Action) -> None:
    """Perform the source filing action before committing local completion."""
    if (action.action_type or "").upper() not in {"FILE", "ARCHIVE"}:
        raise ValueError("Only FILE or ARCHIVE actions can be filed")
    if not settings.write_to_paperless:
        raise PermissionError("Paperless writes are disabled")
    if not action.document_id:
        raise ValueError("Action is not linked to a Paperless document")
    from .enricher import PaperlessEnricher

    await PaperlessEnricher().sync_status(action.document_id, "completed")
    action.last_synced_status = "completed"
    transition_action_status(action, "completed")
    action.version = (action.version or 1) + 1
    db.commit()


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
        action.completed_at = None
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

        aggregate_status = document_action_status(db, action.document_id)
        await PaperlessEnricher().sync_status(action.document_id, aggregate_status)
        siblings = db.query(Action).filter_by(document_id=action.document_id).all()
        for sibling in siblings:
            sibling.last_synced_status = aggregate_status
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
    corrected_amount_supplied: bool = False,
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
        action.action_ready = False
        action.review_state = "resolved_no_action"
        action.review_item_id = None
    elif feedback_type == "misclassified" and normalized_action_type:
        action.action_type = normalized_action_type
        action_changed = True
        recalculate_action_risk(action)
        refresh_recommended_cta(action)
    elif feedback_type == "wrong_urgency" and normalized_urgency:
        action.urgency = normalized_urgency
        action_changed = True
        recalculate_action_risk(action)
    elif feedback_type == "wrong_amount" and corrected_amount_supplied:
        action.amount = corrected_amount
        action.document_amount = corrected_amount
        action.document_amount_overridden = True
        action_changed = True
        recalculate_action_risk(action)

    if action_changed:
        action.version = (action.version or 1) + 1
    return feedback, action_changed
