"""Obligation and linked-document matching for Action Queue items."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy.orm import Session

from .database import Action, Obligation, ObligationDocument, ObligationEvent
from .risk_scoring import compute_risk_score

ACTIVE_ACTION_STATUSES = {"pending", "acknowledged", "snoozed"}
INVOICE_FEE_TOLERANCE = 5.0
ACTION_LINK_THRESHOLD = 0.75
RECEIPT_SUGGEST_THRESHOLD = 0.75

_REMINDER_PATTERN = re.compile(
    r"\b(reminder|past due|overdue|second notice|2nd notice|final notice|collections?|"
    r"disconnect(?:ion)?|shut[\s-]?off)\b",
    re.I,
)
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


def _base_snapshot(action: Action) -> dict[str, Any]:
    snapshot = action.relationship_base_snapshot
    if isinstance(snapshot, dict):
        return snapshot
    snapshot = {
        "title": action.title,
        "summary": action.summary,
        "due_date": action.due_date.isoformat() if action.due_date else None,
        "amount": action.amount,
        "urgency": action.urgency,
        "severity": action.severity,
        "risk_score": action.risk_score,
        "ai_reasoning": action.ai_reasoning,
    }
    action.relationship_base_snapshot = snapshot
    return snapshot


def _restore_base(action: Action) -> None:
    snapshot = _base_snapshot(action)
    action.title = snapshot.get("title") or action.title
    action.summary = snapshot.get("summary")
    raw_due_date = snapshot.get("due_date")
    action.due_date = date.fromisoformat(raw_due_date) if raw_due_date else None
    action.amount = snapshot.get("amount")
    action.urgency = snapshot.get("urgency") or "LOW"
    action.severity = snapshot.get("severity") or "safe"
    action.risk_score = int(snapshot.get("risk_score") or 0)
    action.ai_reasoning = snapshot.get("ai_reasoning")


def _action_identity(action: Action) -> tuple[str, str, str]:
    data = action.extracted_data if isinstance(action.extracted_data, dict) else {}
    reference = _normalized(
        data.get("reference_number") or data.get("invoice_number") or data.get("claim_number")
    )
    account = _normalized(data.get("account_identifier") or data.get("account_hint"))
    return _normalized(action.correspondent), reference, account


def _identity_key(actions: list[Action], root_document_id: int) -> str:
    providers = sorted(
        {_action_identity(action)[0] for action in actions if _action_identity(action)[0]}
    )
    references = sorted(
        {_action_identity(action)[1] for action in actions if _action_identity(action)[1]}
    )
    accounts = sorted(
        {_action_identity(action)[2] for action in actions if _action_identity(action)[2]}
    )
    evidence = (
        f"provider:{providers[0]}|reference:{references[0]}"
        if providers and references
        else (
            f"provider:{providers[0]}|account:{accounts[0]}|root:{root_document_id}"
            if providers and accounts
            else f"relationship-root:{root_document_id}"
        )
    )
    return hashlib.sha256(evidence.encode("utf-8")).hexdigest()


def _identity_conflict(actions: list[Action]) -> str | None:
    providers = {_action_identity(action)[0] for action in actions if _action_identity(action)[0]}
    references = {_action_identity(action)[1] for action in actions if _action_identity(action)[1]}
    accounts = {_action_identity(action)[2] for action in actions if _action_identity(action)[2]}
    if len(providers) > 1:
        return "Related documents have conflicting providers"
    if len(references) > 1:
        return "Related documents have conflicting invoice or claim identities"
    if not references and len(accounts) > 1:
        return "Related documents have conflicting account identities"
    return None


def _urgency_for_adjustment(base: str, adjustment: int) -> str:
    ranks = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    minimum = (
        "CRITICAL"
        if adjustment >= 35
        else "HIGH"
        if adjustment >= 18
        else ("MEDIUM" if adjustment >= 12 else "LOW")
    )
    return max((base or "LOW").upper(), minimum, key=lambda value: ranks.get(value, 0))


def _relationship_rows() -> list[Any]:
    from doc_intelligence_hub.modules.triage.database import (
        DocumentRelationship,
    )
    from doc_intelligence_hub.modules.triage.database import (
        get_session as get_triage_session,
    )

    triage_db = get_triage_session()
    try:
        return (
            triage_db.query(DocumentRelationship)
            .filter(DocumentRelationship.removed_at.is_(None))
            .order_by(DocumentRelationship.created_at.asc(), DocumentRelationship.id.asc())
            .all()
        )
    finally:
        triage_db.close()


def _route_identity_conflict(db: Session, actions: list[Action], reason: str) -> None:
    from .lifecycle import route_action_to_review

    for action in actions:
        _restore_base(action)
        action.superseded_by_action_id = None
        route_action_to_review(db, action, reason=reason)


def _record_obligation_event(
    db: Session,
    obligation: Obligation,
    *,
    event_type: str,
    relationship_id: str | None,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    if before == after:
        return
    db.add(
        ObligationEvent(
            obligation_id=obligation.id,
            event_type=event_type,
            relationship_id=relationship_id,
            payload={"before": before, "after": after},
        )
    )


def reconcile_relationship_obligations(
    db: Session,
    document_ids: set[int],
    *,
    relationship_id: str | None = None,
    event_type: str = "relationship_recalculated",
) -> int:
    """Rebuild affected PAY obligations from the active typed relationship graph."""
    if not document_ids:
        return 0
    relationships = [
        row
        for row in _relationship_rows()
        if row.relationship_type in {"follows", "supersedes", "same_sequence"}
    ]
    adjacency: dict[int, set[int]] = {}
    for row in relationships:
        adjacency.setdefault(row.source_document_id, set()).add(row.target_document_id)
        adjacency.setdefault(row.target_document_id, set()).add(row.source_document_id)

    expanded = set(document_ids)
    frontier = list(document_ids)
    while frontier:
        current = frontier.pop()
        for neighbor in adjacency.get(current, set()):
            if neighbor not in expanded:
                expanded.add(neighbor)
                frontier.append(neighbor)

    existing_actions = (
        db.query(Action)
        .filter(Action.action_type == "PAY", Action.document_id.in_(expanded))
        .order_by(Action.created_at.asc(), Action.id.asc())
        .all()
    )
    obligation_ids = {action.obligation_id for action in existing_actions if action.obligation_id}
    if obligation_ids:
        prior_actions = (
            db.query(Action)
            .filter(Action.action_type == "PAY", Action.obligation_id.in_(obligation_ids))
            .all()
        )
        existing_actions = list(
            {action.id: action for action in [*existing_actions, *prior_actions]}.values()
        )
        expanded.update(action.document_id for action in prior_actions)

    actions_by_document: dict[int, list[Action]] = {}
    for action in existing_actions:
        actions_by_document.setdefault(action.document_id, []).append(action)
        _restore_base(action)

    components: list[set[int]] = []
    remaining = set(actions_by_document)
    while remaining:
        start = min(remaining)
        component = {start}
        stack = [start]
        while stack:
            current = stack.pop()
            for neighbor in adjacency.get(current, set()):
                if neighbor in actions_by_document and neighbor not in component:
                    component.add(neighbor)
                    stack.append(neighbor)
        remaining -= component
        components.append(component)

    used_obligations: set[str] = set()
    changed = 0
    for component in components:
        component_actions = [
            action for document_id in component for action in actions_by_document[document_id]
        ]
        conflict = _identity_conflict(component_actions) if len(component) > 1 else None
        if conflict:
            _route_identity_conflict(db, component_actions, conflict)
            continue

        outgoing_targets = {
            row.target_document_id
            for row in relationships
            if row.source_document_id in component and row.target_document_id in component
        }
        authoritative_candidates = component - outgoing_targets
        authoritative_document_id = max(
            authoritative_candidates or component,
            key=lambda document_id: (
                max(
                    (
                        action.document_date or date.min
                        for action in actions_by_document[document_id]
                    ),
                    default=date.min,
                ),
                document_id,
            ),
        )
        directed_targets = {
            row.target_document_id
            for row in relationships
            if row.source_document_id in component
            and row.target_document_id in component
            and row.relationship_type in {"follows", "supersedes"}
        }
        roots = component - {
            row.source_document_id
            for row in relationships
            if row.source_document_id in component
            and row.target_document_id in component
            and row.relationship_type in {"follows", "supersedes"}
        }
        root_document_id = min(roots or directed_targets or component)

        existing_obligations = [
            db.query(Obligation).filter_by(id=obligation_id).first()
            for obligation_id in {
                action.obligation_id for action in component_actions if action.obligation_id
            }
            if obligation_id not in used_obligations
        ]
        existing_obligations = [item for item in existing_obligations if item is not None]
        existing_obligations.sort(key=lambda item: (item.created_at or datetime.min, item.id))
        obligation = next(
            (
                item
                for item in existing_obligations
                if item.primary_action_id
                and any(
                    action.id == item.primary_action_id and action.document_id in component
                    for action in component_actions
                )
            ),
            existing_obligations[0] if existing_obligations else None,
        )
        if obligation is None:
            obligation = Obligation(status="open")
            db.add(obligation)
            db.flush()
        used_obligations.add(obligation.id)

        lifecycle_actions = [
            action
            for action in component_actions
            if action.status in {"completed", "acknowledged", "snoozed"}
        ]
        canonical = min(
            lifecycle_actions or component_actions,
            key=lambda action: (
                {"completed": 0, "acknowledged": 1, "snoozed": 2}.get(action.status, 3),
                0 if action.id == obligation.primary_action_id else 1,
                action.created_at or datetime.min,
                action.id,
            ),
        )
        authoritative = min(
            actions_by_document[authoritative_document_id],
            key=lambda action: (0 if action.is_primary else 1, action.id),
        )
        component_relationships = [
            row
            for row in relationships
            if row.source_document_id in component and row.target_document_id in component
        ]
        rules = sorted(
            {
                code
                for row in component_relationships
                for code in json.loads(row.reason_codes_json or "[]")
                if code
            }
        )
        stage_adjustments = {
            "second_notice": 12,
            "past_due": 18,
            "final_notice": 28,
            "collections_or_disconnect": 35,
        }
        calculated_adjustment = max(
            (stage_adjustments.get(code, 0) for code in rules),
            default=0,
        )
        if "explicit_supersession" in rules:
            calculated_adjustment += 8
        adjustment = min(
            100,
            max(
                calculated_adjustment,
                max(
                    (row.priority_adjustment or 0 for row in component_relationships),
                    default=0,
                ),
            ),
        )
        explanations = list(
            dict.fromkeys(
                row.priority_explanation
                for row in component_relationships
                if row.priority_explanation
            )
        )
        explanation = "; ".join(explanations) or "No relationship priority adjustment"
        before = {
            "identity_key": obligation.identity_key,
            "primary_action_id": obligation.primary_action_id,
            "authoritative_document_id": obligation.authoritative_document_id,
            "rules": obligation.relationship_rules or [],
            "explanation": obligation.relationship_explanation,
            "action": (
                {
                    "id": canonical.id,
                    "amount": canonical.amount,
                    "due_date": canonical.due_date.isoformat() if canonical.due_date else None,
                    "urgency": canonical.urgency,
                    "risk_score": canonical.risk_score,
                }
                if canonical
                else None
            ),
        }

        source = _base_snapshot(authoritative)
        canonical.title = source.get("title") or canonical.title
        canonical.summary = source.get("summary")
        raw_due_date = source.get("due_date")
        canonical.due_date = date.fromisoformat(raw_due_date) if raw_due_date else None
        canonical.amount = source.get("amount")
        canonical.urgency = _urgency_for_adjustment(source.get("urgency") or "LOW", adjustment)
        canonical.severity = (
            "critical"
            if canonical.urgency == "CRITICAL"
            else "focus"
            if canonical.urgency in {"HIGH", "MEDIUM"}
            else "safe"
        )
        canonical.risk_score = min(
            100,
            compute_risk_score(
                urgency=source.get("urgency") or "LOW",
                due_date=canonical.due_date,
                amount=canonical.amount,
                confidence=canonical.confidence or 0,
                action_type=canonical.action_type,
            )
            + adjustment,
        )
        base_reasoning = source.get("ai_reasoning")
        canonical.ai_reasoning = (
            f"{base_reasoning}\n\nRelationship update: {explanation}"
            if base_reasoning
            else f"Relationship update: {explanation}"
        )
        canonical.action_ready = canonical.review_state != "needs_review"
        if canonical.review_state == "linked_document":
            canonical.review_state = "ready"
        canonical.superseded_by_action_id = None
        canonical.obligation_id = obligation.id
        canonical.version = (canonical.version or 1) + 1

        for action in component_actions:
            action.obligation_id = obligation.id
            if action.id != canonical.id:
                action.superseded_by_action_id = canonical.id
                action.action_ready = False
                action.review_state = "linked_document"
            _add_action_document(
                db,
                obligation,
                action,
                {},
                confidence=1.0,
                source="typed_relationship" if len(component) > 1 else "action_queue",
            )
        for linked in db.query(ObligationDocument).filter_by(obligation_id=obligation.id).all():
            if linked.document_id not in component and linked.role != "receipt":
                linked.excluded = True
                linked.excluded_at = datetime.utcnow()

        obligation.identity_key = _identity_key(component_actions, root_document_id)
        obligation.primary_action_id = canonical.id
        obligation.authoritative_document_id = authoritative_document_id
        obligation.relationship_rules = rules
        obligation.relationship_explanation = explanation
        after = {
            "identity_key": obligation.identity_key,
            "primary_action_id": obligation.primary_action_id,
            "authoritative_document_id": obligation.authoritative_document_id,
            "rules": rules,
            "explanation": explanation,
            "action": {
                "id": canonical.id,
                "amount": canonical.amount,
                "due_date": canonical.due_date.isoformat() if canonical.due_date else None,
                "urgency": canonical.urgency,
                "risk_score": canonical.risk_score,
            },
        }
        _record_obligation_event(
            db,
            obligation,
            event_type=event_type,
            relationship_id=relationship_id,
            before=before,
            after=after,
        )
        changed += int(before != after)

    for old_obligation in (
        db.query(Obligation).filter(Obligation.id.in_(obligation_ids - used_obligations)).all()
        if obligation_ids - used_obligations
        else []
    ):
        if not db.query(Action).filter_by(obligation_id=old_obligation.id).first():
            db.query(ObligationDocument).filter_by(obligation_id=old_obligation.id).delete()
            db.delete(old_obligation)
    db.flush()
    return changed


def reconcile_relationship_change(
    document_ids: set[int],
    *,
    relationship_id: str,
    event_type: str,
) -> int:
    """Reconcile a committed relationship change in the Action Queue database."""
    from .database import get_session, init_db

    init_db()
    db = get_session()
    try:
        changed = reconcile_relationship_obligations(
            db,
            document_ids,
            relationship_id=relationship_id,
            event_type=event_type,
        )
        db.commit()
        return changed
    finally:
        db.close()


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
    elif linked.excluded and source != "user_link":
        return linked
    linked.excluded = False
    linked.excluded_at = None
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


def _document_excluded(db: Session, obligation_id: str, document_id: int) -> bool:
    return (
        db.query(ObligationDocument)
        .filter_by(
            obligation_id=obligation_id,
            document_id=document_id,
            excluded=True,
        )
        .first()
        is not None
    )


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


def _date_proximity_score(left: date | None, right: date | None) -> float:
    if not left or not right:
        return 0.0
    difference = abs((left - right).days)
    if difference <= 7:
        return 1.0
    if difference <= 45:
        return 0.5
    return 0.0


def _suggestion_amount_score(left: float | None, right: float | None) -> float:
    if left is None or right is None:
        return 0.0
    maximum = max(abs(left), abs(right))
    if maximum == 0:
        return 1.0
    difference = abs(left - right)
    if difference <= 0.01:
        return 1.0
    difference_ratio = difference / maximum
    if difference <= INVOICE_FEE_TOLERANCE or difference_ratio <= 0.05:
        return 0.9
    if difference_ratio <= 0.1:
        return 0.6
    return 0.0


def suggest_related_actions(
    db: Session,
    action: Action,
    *,
    query: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Rank open PAY actions for explicit user-reviewed linking."""
    candidates = (
        db.query(Action)
        .filter(
            Action.id != action.id,
            Action.document_id != action.document_id,
            Action.action_type == "PAY",
            Action.status.in_(ACTIVE_ACTION_STATUSES),
            Action.superseded_by_action_id.is_(None),
        )
        .all()
    )
    needle = _normalized(query)
    if needle:
        candidates = [
            candidate
            for candidate in candidates
            if needle
            in _normalized(
                " ".join(
                    [
                        candidate.title or "",
                        candidate.document_title or "",
                        candidate.correspondent or "",
                        _action_reference(candidate),
                        _action_account(candidate),
                    ]
                )
            )
        ]

    suggestions: list[dict[str, Any]] = []
    for candidate in candidates:
        score = 0.0
        reasons: list[str] = []
        if _action_reference(action) and _action_reference(action) == _action_reference(candidate):
            score += 0.55
            reasons.append("same invoice reference")
        if _action_account(action) and _action_account(action) == _action_account(candidate):
            score += 0.35
            reasons.append("same account")
        if _normalized(action.correspondent) and _normalized(action.correspondent) == _normalized(
            candidate.correspondent
        ):
            score += 0.2
            reasons.append("same correspondent")
        amount_score = _suggestion_amount_score(action.amount, candidate.amount)
        if amount_score:
            score += 0.2 * amount_score
            reasons.append("similar amount" if amount_score < 1.0 else "same amount")
        date_score = _date_proximity_score(action.document_date, candidate.document_date)
        if date_score:
            score += 0.1 * date_score
            reasons.append("nearby document dates")
        due_score = _date_proximity_score(action.due_date, candidate.due_date)
        if due_score:
            score += 0.1 * due_score
            reasons.append("nearby due dates")
        title_score = SequenceMatcher(
            None,
            _normalized(action.document_title or action.title),
            _normalized(candidate.document_title or candidate.title),
        ).ratio()
        if title_score >= 0.5:
            score += 0.1 * title_score
            reasons.append("similar titles")
        suggestions.append(
            {
                "action": candidate,
                "score": round(min(score, 1.0), 3),
                "reasons": reasons,
            }
        )
    suggestions.sort(key=lambda item: (item["score"], item["action"].created_at), reverse=True)
    return suggestions[:limit]


def manually_link_actions(db: Session, primary: Action, related: Action) -> Obligation:
    """Merge two action obligations, preserving the selected primary action."""
    if primary.id == related.id or primary.document_id == related.document_id:
        raise ValueError("Choose an action from a different document")
    if primary.action_type != "PAY" or related.action_type != "PAY":
        raise ValueError("Only PAY actions can be linked as one obligation")
    if related.superseded_by_action_id is not None:
        raise ValueError("The related action is already linked to another obligation")

    primary_obligation = ensure_obligation(db, primary)
    related_obligation = ensure_obligation(db, related)
    _add_action_document(db, primary_obligation, primary, {}, source="action_queue")

    if related_obligation.id != primary_obligation.id:
        related_documents = (
            db.query(ObligationDocument).filter_by(obligation_id=related_obligation.id).all()
        )
        for document in related_documents:
            existing = (
                db.query(ObligationDocument)
                .filter_by(
                    obligation_id=primary_obligation.id,
                    document_id=document.document_id,
                )
                .first()
            )
            if existing:
                db.delete(document)
            else:
                document.obligation_id = primary_obligation.id
        for obligation_action in db.query(Action).filter_by(obligation_id=related_obligation.id):
            obligation_action.obligation_id = primary_obligation.id
        if related_obligation.completion_suggested:
            primary_obligation.completion_suggested = True
            primary_obligation.suggestion_reason = related_obligation.suggestion_reason
            primary_obligation.status = related_obligation.status
        db.delete(related_obligation)

    related.obligation_id = primary_obligation.id
    related.superseded_by_action_id = primary.id
    related.action_ready = False
    related.review_state = "linked_document"
    _add_action_document(
        db,
        primary_obligation,
        related,
        {},
        confidence=1.0,
        source="user_link",
    )
    primary_obligation.primary_action_id = primary.id
    db.flush()
    return primary_obligation


def manually_link_document(
    db: Session,
    action: Action,
    document: dict[str, Any],
) -> ObligationDocument:
    """Attach a Paperless document without requiring it to have an action."""
    document_id = int(document["id"])
    if document_id == action.document_id:
        raise ValueError("Choose a different document")

    title = str(document.get("title") or f"Document #{document_id}")
    raw_document_type = document.get("document_type_name") or document.get("document_type")
    document_type = raw_document_type if isinstance(raw_document_type, str) else None
    raw_correspondent = document.get("correspondent_name") or document.get("correspondent")
    correspondent = raw_correspondent if isinstance(raw_correspondent, str) else None
    content = str(document.get("content") or "")
    role = _document_role(title, document_type)
    if role == "invoice" and not re.search(
        r"\b(invoice|bill)\b", f"{title} {document_type or ''}", re.I
    ):
        role = "supporting"

    obligation = ensure_obligation(db, action)
    _add_action_document(db, obligation, action, {}, source="action_queue")
    linked = add_document(
        db,
        obligation,
        document_id=document_id,
        role=role,
        title=title,
        document_type=document_type,
        correspondent=correspondent,
        document_date=_document_date(document),
        amount=_document_amount(document, content),
        reference_number=_document_reference(document, content)
        or _document_account(document, content)
        or None,
        confidence=1.0,
        source="user_link",
    )
    if role == "receipt":
        obligation.status = "payment_detected"
        obligation.completion_suggested = True
        obligation.suggestion_reason = (
            f"Payment receipt #{document_id} was manually linked to this obligation."
        )
    db.flush()
    return linked


def associate_pay_action(db: Session, action: Action, document: dict[str, Any]) -> Obligation:
    """Attach a PAY action, using typed relationships as the consolidation authority."""
    if action.action_type != "PAY":
        return ensure_obligation(db, action)
    obligation = ensure_obligation(db, action)
    _add_action_document(db, obligation, action, document)
    reconcile_relationship_obligations(db, {action.document_id})
    return db.query(Obligation).filter_by(id=action.obligation_id).one()


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
    if _document_excluded(db, obligation.id, document_id):
        return None
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


def unlink_document(db: Session, action: Action, document_id: int) -> list[Action]:
    """Hide a mistaken link and restore any action represented by that document."""
    if not action.obligation_id:
        raise ValueError("This action has no linked documents")
    if document_id == action.document_id:
        raise ValueError("The action's source document cannot be unlinked")

    obligation = db.query(Obligation).filter_by(id=action.obligation_id).first()
    linked = (
        db.query(ObligationDocument)
        .filter_by(
            obligation_id=action.obligation_id,
            document_id=document_id,
            excluded=False,
        )
        .first()
    )
    if not obligation or not linked:
        raise ValueError("The related document link was not found")

    linked.excluded = True
    linked.excluded_at = datetime.utcnow()

    restored = (
        db.query(Action)
        .filter_by(obligation_id=obligation.id, document_id=document_id)
        .order_by(Action.id.asc())
        .all()
    )
    if restored:
        restored_primary = next(
            (candidate for candidate in restored if candidate.superseded_by_action_id == action.id),
            restored[0],
        )
        restored_obligation = Obligation(primary_action_id=restored_primary.id, status="open")
        db.add(restored_obligation)
        db.flush()
        for restored_action in restored:
            restored_action.obligation_id = restored_obligation.id
            restored_action.superseded_by_action_id = None
            restored_action.action_ready = True
            if restored_action.review_state == "linked_document":
                restored_action.review_state = "ready"
            restored_action.version = (restored_action.version or 1) + 1
        _add_action_document(
            db,
            restored_obligation,
            restored_primary,
            {},
            source="action_queue",
        )

    remaining_receipt = (
        db.query(ObligationDocument)
        .filter_by(obligation_id=obligation.id, role="receipt", excluded=False)
        .first()
    )
    if not remaining_receipt:
        obligation.completion_suggested = False
        obligation.suggestion_reason = None
        if obligation.status == "payment_detected":
            obligation.status = "open"

    db.flush()
    return restored


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
    obligation = (
        db.query(Obligation).filter_by(id=action.obligation_id).first()
        if action.obligation_id
        else None
    )
    rows = (
        db.query(ObligationDocument)
        .filter_by(obligation_id=action.obligation_id, excluded=False)
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
            "is_authoritative": bool(
                obligation and row.document_id == obligation.authoritative_document_id
            ),
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
