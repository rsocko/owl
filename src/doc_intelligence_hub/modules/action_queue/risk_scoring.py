"""Composite risk scoring for Action Queue items.

Computes a 0–100 risk_score based on multiple factors:
- Urgency level (CRITICAL=40, HIGH=25, MEDIUM=15, LOW=5)
- Due date proximity (overdue actions score higher)
- Financial amount (higher amounts = higher risk)
- Confidence level (higher confidence = more trust in risk)
- Action type weight (PAY/RESPOND weigh more than FILE/REVIEW)

The score is used to sort the Action Queue so highest-risk items surface first.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Urgency base weights (0–40 range)
_URGENCY_WEIGHTS: dict[str, int] = {
    "CRITICAL": 40,
    "HIGH": 25,
    "MEDIUM": 15,
    "LOW": 5,
}

# Action type multipliers (applied to urgency weight)
_ACTION_TYPE_MULTIPLIERS: dict[str, float] = {
    "PAY": 1.3,
    "RESPOND": 1.2,
    "DISPUTE": 1.2,
    "CANCEL": 1.1,
    "CALL": 1.1,
    "RENEW": 1.1,
    "REVIEW": 1.0,
    "FILE": 0.9,
    "SCHEDULE": 0.8,
}

# Due date scoring constants
_MAX_DUE_DATE_SCORE = 35
_OVERDUE_PENALTY_PER_DAY = 3  # +3 per overdue day, capped at max
_DUE_SOON_DAYS = 14  # Within 14 days = approaching


def compute_risk_score(
    *,
    urgency: str = "LOW",
    due_date: date | str | None = None,
    amount: float | None = None,
    confidence: int = 0,
    action_type: str = "REVIEW",
    as_of: date | None = None,
) -> int:
    """Compute a composite risk score (0–100) for an action item.

    Args:
        urgency: Action urgency level (CRITICAL, HIGH, MEDIUM, LOW).
        due_date: Due date as date object or ISO string.
        amount: Financial amount (dollars).
        confidence: AI confidence score (0–100).
        action_type: Type of action (PAY, RESPOND, FILE, etc.).
        as_of: Reference date for due-date calculations (defaults to today).

    Returns:
        Integer risk score from 0 to 100.
    """
    if as_of is None:
        as_of = date.today()

    score = 0.0

    # Factor 1: Urgency base (0–40)
    urgency_upper = (urgency or "LOW").upper()
    urgency_base = _URGENCY_WEIGHTS.get(urgency_upper, 5)

    # Apply action type multiplier
    action_type_upper = (action_type or "REVIEW").upper()
    multiplier = _ACTION_TYPE_MULTIPLIERS.get(action_type_upper, 1.0)
    score += urgency_base * multiplier

    # Factor 2: Due date proximity (0–35)
    due_date_score = _compute_due_date_score(due_date, as_of)
    score += due_date_score

    # Factor 3: Financial amount (0–15)
    amount_score = _compute_amount_score(amount)
    score += amount_score

    # Factor 4: Confidence adjustment (-5 to +10)
    # High confidence = we trust the risk assessment more → boost score
    # Low confidence = uncertain → slight penalty
    if confidence >= 80:
        score += 10
    elif confidence >= 60:
        score += 5
    elif confidence < 30 and confidence > 0:
        score -= 5

    # Clamp to 0–100
    return max(0, min(100, round(score)))


def _compute_due_date_score(due_date: date | str | None, as_of: date) -> float:
    """Score based on due date proximity. Overdue items score highest."""
    if due_date is None:
        return 5.0  # Small base score for undated items (could be urgent)

    if isinstance(due_date, str):
        try:
            due_date = date.fromisoformat(due_date)
        except (ValueError, TypeError):
            return 5.0

    days_until = (due_date - as_of).days

    if days_until < 0:
        # Overdue: penalty increases with days overdue
        overdue_days = abs(days_until)
        return min(_MAX_DUE_DATE_SCORE, 20 + overdue_days * _OVERDUE_PENALTY_PER_DAY)
    elif days_until == 0:
        # Due today
        return 25.0
    elif days_until <= 3:
        # Due within 3 days
        return 20.0
    elif days_until <= 7:
        # Due within a week
        return 15.0
    elif days_until <= _DUE_SOON_DAYS:
        # Due within 2 weeks
        return 10.0
    else:
        # Far future
        return 3.0


def _compute_amount_score(amount: float | None) -> float:
    """Score based on financial amount. Higher amounts = higher risk."""
    if amount is None or amount <= 0:
        return 0.0

    if amount >= 5000:
        return 15.0
    elif amount >= 1000:
        return 12.0
    elif amount >= 500:
        return 9.0
    elif amount >= 100:
        return 6.0
    elif amount >= 25:
        return 3.0
    else:
        return 1.0


def recalculate_risk_scores(actions: list[Any]) -> int:
    """Recalculate risk_score for a list of Action ORM objects.

    Args:
        actions: List of Action model instances with urgency, due_date,
                 amount, confidence, action_type attributes.

    Returns:
        Number of actions whose score changed.
    """
    changed = 0
    for action in actions:
        new_score = compute_risk_score(
            urgency=action.urgency or "LOW",
            due_date=action.due_date,
            amount=action.amount,
            confidence=action.confidence or 0,
            action_type=action.action_type or "REVIEW",
        )
        if action.risk_score != new_score:
            action.risk_score = new_score
            changed += 1
    return changed
