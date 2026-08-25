"""Obligation and linked-document matching for Action Queue items."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from .database import Action, Obligation, ObligationDocument

ACTIVE_ACTION_STATUSES = {"pending", "acknowledged", "snoozed"}
INVOICE_FEE_TOLERANCE = 5.0
ACTION_LINK_THRESHOLD = 0.75
RECEIPT_SUGGEST_THRESHOLD = 0.75

_REMINDER_PATTERN = re.compile(r"\b(reminder|past due|overdue|second notice|2nd notice)\b", re.I)
_REVISION_PATTERN = re.compile(r"\b(revised|updated|corrected|replacement|supersedes)\b", re.I)
_AMOUNT_PATTERN = re.compile(r"(?:\$|USD\s*)\s*([0-9][0-9,]*(?:\.\d{2})?)", re.I)
_REFERENCE_PATTERN = re.compile(
    r"\b(?:invoice|reference)"
    r"(?:[ \t]*(?:number|no\.?))?[ \t]*[:#-]?[ \t]*([A-Z0-9][A-Z0-9-]{3,})\b",
    re.I,
)
_ACCOUNT_PATTERN = re.compile(
    r"\baccount(?:[ \t]*(?:number|no\.?))?[ \t]*[:#-]?[ \t]*([A-Z0-9][A-Z0-9-]{3,})\b",
    re.I,
)


def _normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _action_reference(action: Action) -> str:
    data = action.extracted_data if isinstance(action.extracted_data, dict) else {}
    return _normalized(data.get("reference_number"))


def _action_account(action: Action) -> str:
    data = action.extracted_data if isinstance(action.extracted_data, dict) else {}
    return _normalized(data.get("account_identifier"))


def _document_reference(document: dict[str, Any], content: str = "") -> str:
    for key in ("reference_number", "invoice_number"):
        value = document.get(key)
        if value:
            return _normalized(value)
    matches = _REFERENCE_PATTERN.findall(f"{document.get('title', '')}\n{content[:4000]}")
    return _normalized(matches[-1]) if matches else ""


def _document_account(document: dict[str, Any], content: str = "") -> str:
    value = document.get("account_identifier")
    if value:
        return _normalized(value)
    matches = _ACCOUNT_PATTERN.findall(f"{document.get('title', '')}\n{content[:4000]}")
    return _normalized(matches[-1]) if matches else ""


def _document_amount(document: dict[str, Any], content: str = "") -> float | None:
    for key in ("amount", "total_amount", "document_amount"):
        value = document.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    matches = _AMOUNT_PATTERN.findall(f"{document.get('title', '')}\n{content[:4000]}")
    if not matches:
        return None
    try:
        return float(matches[-1].replace(",", ""))
    except ValueError:
        return None


def _document_date(document: dict[str, Any]) -> date | None:
    value = document.get("created") or document.get("document_date")
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _amount_score(left: float | None, right: float | None) -> float:
    if left is None or right is None:
        return 0.0
    difference = abs(left - right)
    if difference <= 0.01:
        return 1.0
    if difference <= max(INVOICE_FEE_TOLERANCE, abs(right) * 0.05):
        return 0.9
    if 0 < left < right:
        return 0.4
    return 0.0


def _document_role(title: str, document_type: str | None = None) -> str:
    text = f"{title} {document_type or ''}"
    if re.search(r"\breceipts?\b", text, re.I):
        return "receipt"
    if _REVISION_PATTERN.search(text):
        return "revision"
    if _REMINDER_PATTERN.search(text):
        return "reminder"
    return "invoice"


def ensure_obligation(db: Session, action: Action) -> Obligation:
    """Return the action's obligation, creating one when needed."""
    obligation = (
        db.query(Obligation).filter_by(id=action.obligation_id).first()
        if action.obligation_id
        else None
    )
    if obligation:
        return obligation
    obligation = Obligation(primary_action_id=action.id, status="open")
    db.add(obligation)
    db.flush()
    action.obligation_id = obligation.id
    return obligation


def add_document(
    db: Session,
    obligation: Obligation,
    *,
    document_id: int,
    role: str,
    title: str | None,
    document_type: str | None,
    correspondent: str | None,
    document_date: date | None,
    amount: float | None,
    reference_number: str | None,
    confidence: float,
    source: str,
) -> ObligationDocument:
    """Create or refresh one timeline entry without duplicating a document."""
    linked = (
        db.query(ObligationDocument)
        .filter_by(obligation_id=obligation.id, document_id=document_id)
        .first()
    )
    if not linked:
        linked = ObligationDocument(obligation_id=obligation.id, document_id=document_id)
        db.add(linked)
    linked.role = role
    linked.title = title
    linked.document_type = document_type
    linked.correspondent = correspondent
    linked.document_date = document_date
    linked.amount = amount
    linked.reference_number = reference_number
    linked.confidence = round(confidence, 3)
    linked.source = source
    return linked


def _add_action_document(
    db: Session,
    obligation: Obligation,
    action: Action,
    document: dict[str, Any],
    *,
    confidence: float = 1.0,
    source: str = "action_queue",
) -> ObligationDocument:
    title = action.document_title or str(document.get("title") or f"Document #{action.document_id}")
    return add_document(
        db,
        obligation,
        document_id=action.document_id,
        role=_document_role(title, action.document_type),
        title=title,
        document_type=action.document_type,
        correspondent=action.correspondent,
        document_date=action.document_date or _document_date(document),
        amount=action.document_amount if action.document_amount is not None else action.amount,
        reference_number=_action_reference(action) or None,
        confidence=confidence,
        source=source,
    )


def _action_match_score(candidate: Action, action: Action) -> float:
    score = 0.0
    candidate_reference, action_reference = _action_reference(candidate), _action_reference(action)
    if candidate_reference and candidate_reference == action_reference:
        score += 0.55
    amount_score = _amount_score(candidate.amount, action.amount)
    candidate_account, action_account = _action_account(candidate), _action_account(action)
    is_follow_up = bool(_REMINDER_PATTERN.search(action.document_title or "")) or bool(
        _REVISION_PATTERN.search(action.document_title or "")
    )
    if (
        not action_reference
        and is_follow_up
        and amount_score >= 0.9
        and candidate_account
        and candidate_account == action_account
    ):
        score += 0.4
    candidate_correspondent = _normalized(candidate.correspondent)
    action_correspondent = _normalized(action.correspondent)
    if candidate_correspondent and candidate_correspondent == action_correspondent:
        score += 0.25
    score += 0.15 * amount_score
    if _normalized(candidate.title) == _normalized(action.title):
        score += 0.05
    return min(score, 1.0)


def associate_pay_action(db: Session, action: Action, document: dict[str, Any]) -> Obligation:
    """Attach a PAY action to an existing obligation or create a new one."""
    if action.action_type != "PAY":
        return ensure_obligation(db, action)

    candidates = (
        db.query(Action)
        .filter(
            Action.id != action.id,
            Action.document_id != action.document_id,
            Action.action_type == "PAY",
            Action.status.in_(ACTIVE_ACTION_STATUSES),
            Action.superseded_by_action_id.is_(None),
        )
        .order_by(Action.created_at.desc())
        .all()
    )
    candidates = [
        candidate
        for candidate in candidates
        if (
            candidate.created_at is None
            or action.created_at is None
            or (candidate.created_at, candidate.id) < (action.created_at, action.id)
        )
    ]
    scored = sorted(
        ((_action_match_score(candidate, action), candidate) for candidate in candidates),
        key=lambda item: item[0],
        reverse=True,
    )
    best_score, canonical = scored[0] if scored else (0.0, None)

    if canonical is not None and best_score >= ACTION_LINK_THRESHOLD:
        obligation = ensure_obligation(db, canonical)
        _add_action_document(db, obligation, canonical, {}, source="action_queue")
        action.obligation_id = obligation.id
        action.superseded_by_action_id = canonical.id
        action.action_ready = False
        action.review_state = "linked_document"
        _add_action_document(
            db,
            obligation,
            action,
            document,
            confidence=best_score,
            source="invoice_match",
        )
        return obligation

    obligation = ensure_obligation(db, action)
    _add_action_document(db, obligation, action, document)
    return obligation


def backfill_obligations(db: Session) -> int:
    """Group legacy open PAY actions that predate the obligation schema."""
    actions = (
        db.query(Action)
        .filter(
            Action.action_type == "PAY",
            Action.status.in_(ACTIVE_ACTION_STATUSES),
            Action.superseded_by_action_id.is_(None),
            Action.obligation_id.is_(None),
        )
        .order_by(Action.created_at.asc(), Action.id.asc())
        .all()
    )
    for action in actions:
        associate_pay_action(
            db,
            action,
            {
                "id": action.document_id,
                "title": action.document_title,
                "created": action.document_date,
            },
        )
    db.flush()
    return len(actions)


def _receipt_match_score(
    action: Action,
    *,
    correspondent: str,
    reference_number: str,
    account_identifier: str,
    amount: float | None,
) -> float:
    score = 0.0
    if reference_number and reference_number == _action_reference(action):
        score += 0.55
    amount_score = _amount_score(amount, action.amount)
    if (
        not reference_number
        and account_identifier
        and account_identifier == _action_account(action)
        and amount_score >= 0.9
    ):
        score += 0.35
    if correspondent and correspondent == _normalized(action.correspondent):
        score += 0.25
    score += 0.2 * amount_score
    return min(score, 1.0)


def associate_receipt(
    db: Session,
    document: dict[str, Any],
    content: str,
) -> tuple[Action, float] | None:
    """Attach a receipt to the strongest open PAY action and suggest completion."""
    document_id = int(document["id"])
    correspondent = _normalized(document.get("correspondent_name") or document.get("correspondent"))
    reference_number = _document_reference(document, content)
    account_identifier = _document_account(document, content)
    amount = _document_amount(document, content)
    candidates = (
        db.query(Action)
        .filter(
            Action.action_type == "PAY",
            Action.status.in_(ACTIVE_ACTION_STATUSES),
            Action.superseded_by_action_id.is_(None),
        )
        .all()
    )
    scored = sorted(
        (
            (
                _receipt_match_score(
                    candidate,
                    correspondent=correspondent,
                    reference_number=reference_number,
                    account_identifier=account_identifier,
                    amount=amount,
                ),
                candidate,
            )
            for candidate in candidates
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    best_score, action = scored[0] if scored else (0.0, None)
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if (
        action is None
        or best_score < RECEIPT_SUGGEST_THRESHOLD
        or second_score >= best_score - 0.05
    ):
        return None

    obligation = ensure_obligation(db, action)
    _add_action_document(db, obligation, action, {}, source="action_queue")
    add_document(
        db,
        obligation,
        document_id=document_id,
        role="receipt",
        title=str(document.get("title") or f"Receipt #{document_id}"),
        document_type=str(document.get("document_type_name") or "Receipt"),
        correspondent=str(document.get("correspondent_name") or "") or None,
        document_date=_document_date(document),
        amount=amount,
        reference_number=reference_number or account_identifier or None,
        confidence=best_score,
        source="receipt_match",
    )
    obligation.status = "payment_detected"
    obligation.completion_suggested = True
    obligation.suggestion_reason = (
        f"Payment receipt #{document_id} matches this obligation at "
        f"{round(best_score * 100)}% confidence."
    )
    return action, best_score


def sync_obligation_status(db: Session, action: Action) -> None:
    """Keep the obligation lifecycle aligned with explicit user action."""
    if not action.obligation_id:
        return
    obligation = db.query(Obligation).filter_by(id=action.obligation_id).first()
    if not obligation:
        return
    if action.status == "completed":
        obligation.status = "settled"
        obligation.completion_suggested = False
    elif action.status == "pending" and obligation.status == "settled":
        receipt = (
            db.query(ObligationDocument)
            .filter_by(obligation_id=obligation.id, role="receipt")
            .order_by(ObligationDocument.created_at.desc())
            .first()
        )
        obligation.status = "payment_detected" if receipt else "open"
        obligation.completion_suggested = receipt is not None
        if receipt and not obligation.suggestion_reason:
            obligation.suggestion_reason = (
                f"Payment receipt #{receipt.document_id} remains linked to this obligation."
            )


def linked_documents(db: Session, action: Action) -> list[dict[str, Any]]:
    """Return timeline documents for serialization, including legacy actions."""
    rows = (
        db.query(ObligationDocument)
        .filter_by(obligation_id=action.obligation_id)
        .order_by(ObligationDocument.document_date.asc(), ObligationDocument.created_at.asc())
        .all()
        if action.obligation_id
        else []
    )
    if not rows:
        return [
            {
                "document_id": action.document_id,
                "role": _document_role(action.document_title or "", action.document_type),
                "title": action.document_title,
                "document_type": action.document_type,
                "correspondent": action.correspondent,
                "document_date": action.document_date.isoformat() if action.document_date else None,
                "amount": action.document_amount
                if action.document_amount is not None
                else action.amount,
                "reference_number": _action_reference(action) or None,
                "confidence": 1.0,
                "source": "action_queue",
            }
        ]
    return [
        {
            "document_id": row.document_id,
            "role": row.role,
            "title": row.title,
            "document_type": row.document_type,
            "correspondent": row.correspondent,
            "document_date": row.document_date.isoformat() if row.document_date else None,
            "amount": row.amount,
            "reference_number": row.reference_number,
            "confidence": row.confidence,
            "source": row.source,
        }
        for row in rows
    ]


def completion_suggestion(db: Session, action: Action) -> dict[str, Any] | None:
    """Return an actionable receipt suggestion for an open obligation."""
    if not action.obligation_id:
        return None
    obligation = db.query(Obligation).filter_by(id=action.obligation_id).first()
    if not obligation or not obligation.completion_suggested:
        return None
    receipt = (
        db.query(ObligationDocument)
        .filter_by(obligation_id=obligation.id, role="receipt")
        .order_by(ObligationDocument.created_at.desc())
        .first()
    )
    return {
        "type": "payment_receipt",
        "reason": obligation.suggestion_reason,
        "receipt_document_id": receipt.document_id if receipt else None,
        "confidence": receipt.confidence if receipt else None,
    }
