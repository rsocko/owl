"""Typed document relationships and deterministic related-notice classification."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from doc_intelligence_hub.modules.triage.database import (
    CorrectionEvent,
    DocumentRelationship,
    get_session,
)

RELATIONSHIP_TYPES = frozenset({"follows", "supersedes", "supports", "same_sequence"})
PROVENANCE_TYPES = frozenset({"automatic", "user", "imported"})
SYMMETRIC_TYPES = frozenset({"same_sequence"})
AUTOMATIC_CONFIDENCE_THRESHOLD = 0.85

_NOTICE_PATTERNS: tuple[tuple[str, int, re.Pattern[str]], ...] = (
    (
        "collections_or_disconnect",
        35,
        re.compile(r"\b(collections?|collection agency|disconnect(?:ion)?|shut[\s-]?off)\b", re.I),
    ),
    (
        "final_notice",
        28,
        re.compile(r"\b(final notice|last chance|final warning|final opportunity)\b", re.I),
    ),
    (
        "past_due",
        18,
        re.compile(r"\b(past due|overdue|delinquent|in arrears)\b", re.I),
    ),
    (
        "second_notice",
        12,
        re.compile(r"\b(second notice|2nd notice|reminder|follow[\s-]?up)\b", re.I),
    ),
)
_SUPERSESSION_PATTERN = re.compile(
    r"\b(revised|updated|corrected|replacement|replaces|supersedes)\b", re.I
)


class RelationshipConflictError(ValueError):
    """Raised when a requested active relationship conflicts with the graph."""


@dataclass(frozen=True)
class RelationshipProposal:
    source_document_id: int
    target_document_id: int
    relationship_type: str
    confidence: float
    reason_codes: tuple[str, ...]
    priority_adjustment: int
    priority_explanation: str
    auto_create: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_document_id": self.source_document_id,
            "target_document_id": self.target_document_id,
            "relationship_type": self.relationship_type,
            "confidence": self.confidence,
            "reason_codes": list(self.reason_codes),
            "priority_adjustment": self.priority_adjustment,
            "priority_explanation": self.priority_explanation,
            "auto_create": self.auto_create,
        }


def _relationship_to_dict(relationship: DocumentRelationship) -> dict[str, Any]:
    try:
        reason_codes = json.loads(relationship.reason_codes_json or "[]")
    except (json.JSONDecodeError, TypeError):
        reason_codes = []
    return {
        "id": relationship.id,
        "source_document_id": relationship.source_document_id,
        "target_document_id": relationship.target_document_id,
        "relationship_type": relationship.relationship_type,
        "provenance": relationship.provenance,
        "confidence": relationship.confidence,
        "reason_codes": reason_codes,
        "priority_adjustment": relationship.priority_adjustment,
        "priority_explanation": relationship.priority_explanation,
        "source_duplicate_pair_id": relationship.source_duplicate_pair_id,
        "paperless_synced": bool(relationship.paperless_synced),
        "projection_error": relationship.projection_error,
        "created_at": relationship.created_at.isoformat() if relationship.created_at else None,
        "removed_at": relationship.removed_at.isoformat() if relationship.removed_at else None,
        "removed_by": relationship.removed_by,
    }


def _normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _amount_close(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_value = left.get("amount", left.get("total_amount"))
    right_value = right.get("amount", right.get("total_amount"))
    if left_value is None or right_value is None:
        return False
    try:
        left_amount, right_amount = float(left_value), float(right_value)
    except (TypeError, ValueError):
        return False
    maximum = max(abs(left_amount), abs(right_amount))
    return abs(left_amount - right_amount) <= max(1.0, maximum * 0.05)


def _parse_date(metadata: dict[str, Any]) -> date | None:
    for key in ("document_date", "notice_date", "created", "date"):
        value = metadata.get(key)
        if not value:
            continue
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            continue
    return None


def _document_text(metadata: dict[str, Any]) -> str:
    return " ".join(str(metadata.get(key) or "") for key in ("title", "summary", "content", "text"))


def calculate_priority_adjustment(
    text: str, relationship_type: str
) -> tuple[int, tuple[str, ...], str]:
    """Return the deterministic relationship priority adjustment and fired rules."""
    stage_code = ""
    stage_adjustment = 0
    for code, adjustment, pattern in _NOTICE_PATTERNS:
        if pattern.search(text):
            stage_code = code
            stage_adjustment = adjustment
            break

    reason_codes = [stage_code] if stage_code else []
    adjustment = stage_adjustment
    if relationship_type == "supersedes":
        reason_codes.append("explicit_supersession")
        adjustment += 8
    adjustment = min(100, adjustment)
    explanation = (
        f"Priority +{adjustment}: " + ", ".join(code.replace("_", " ") for code in reason_codes)
        if reason_codes
        else "No relationship priority adjustment"
    )
    return adjustment, tuple(reason_codes), explanation


def classify_related_notice(
    left_document_id: int,
    left: dict[str, Any],
    right_document_id: int,
    right: dict[str, Any],
) -> RelationshipProposal | None:
    """Classify two documents using explicit provider, obligation, stage, and date evidence."""
    left_provider = _normalized(
        left.get("provider") or left.get("provider_name") or left.get("correspondent")
    )
    right_provider = _normalized(
        right.get("provider") or right.get("provider_name") or right.get("correspondent")
    )
    if not left_provider or left_provider != right_provider:
        return None

    reasons = ["provider_match"]
    confidence = 0.2
    identity_match = False
    for key in ("invoice_number", "claim_number"):
        left_id, right_id = _normalized(left.get(key)), _normalized(right.get(key))
        if left_id and left_id == right_id:
            reasons.append(f"{key}_match")
            confidence += 0.5
            identity_match = True
            break

    if not identity_match:
        left_account = _normalized(left.get("account_identifier") or left.get("account_hint"))
        right_account = _normalized(right.get("account_identifier") or right.get("account_hint"))
        if left_account and left_account == right_account and _amount_close(left, right):
            reasons.extend(("account_match", "amount_close"))
            confidence += 0.4
            identity_match = True
        elif (
            left.get("date_of_service")
            and left.get("date_of_service") == right.get("date_of_service")
            and _amount_close(left, right)
        ):
            reasons.extend(("service_date_match", "amount_close"))
            confidence += 0.4
            identity_match = True
    if not identity_match:
        return None

    left_date, right_date = _parse_date(left), _parse_date(right)
    if left_date and right_date and left_date != right_date:
        if left_date > right_date:
            source_id, target_id, source = left_document_id, right_document_id, left
        else:
            source_id, target_id, source = right_document_id, left_document_id, right
        confidence += 0.1
        reasons.append("chronology")
    else:
        source_id, target_id, source = left_document_id, right_document_id, left

    source_text = _document_text(source)
    relationship_type = "supersedes" if _SUPERSESSION_PATTERN.search(source_text) else "follows"
    adjustment, stage_reasons, explanation = calculate_priority_adjustment(
        source_text, relationship_type
    )
    if stage_reasons:
        confidence += 0.2
        reasons.extend(stage_reasons)
    else:
        relationship_type = "same_sequence"

    confidence = min(1.0, round(confidence, 2))
    return RelationshipProposal(
        source_document_id=source_id,
        target_document_id=target_id,
        relationship_type=relationship_type,
        confidence=confidence,
        reason_codes=tuple(dict.fromkeys(reasons)),
        priority_adjustment=adjustment,
        priority_explanation=explanation,
        auto_create=confidence >= AUTOMATIC_CONFIDENCE_THRESHOLD,
    )


def list_document_relationships(
    document_id: int, direction: str = "all", include_removed: bool = False
) -> list[dict[str, Any]]:
    if direction not in {"all", "incoming", "outgoing"}:
        raise ValueError("direction must be all, incoming, or outgoing")
    session = get_session()
    try:
        query = session.query(DocumentRelationship)
        if not include_removed:
            query = query.filter(DocumentRelationship.removed_at.is_(None))
        if direction == "incoming":
            query = query.filter(DocumentRelationship.target_document_id == document_id)
        elif direction == "outgoing":
            query = query.filter(DocumentRelationship.source_document_id == document_id)
        else:
            query = query.filter(
                (DocumentRelationship.source_document_id == document_id)
                | (DocumentRelationship.target_document_id == document_id)
            )
        return [
            _relationship_to_dict(item) for item in query.order_by(DocumentRelationship.created_at)
        ]
    finally:
        session.close()


def get_document_relationship(relationship_id: str) -> dict[str, Any] | None:
    session = get_session()
    try:
        relationship = (
            session.query(DocumentRelationship)
            .filter(DocumentRelationship.id == relationship_id)
            .first()
        )
        return _relationship_to_dict(relationship) if relationship else None
    finally:
        session.close()


def create_document_relationship(
    *,
    source_document_id: int,
    target_document_id: int,
    relationship_type: str,
    provenance: str,
    confidence: float | None = None,
    reason_codes: list[str] | tuple[str, ...] = (),
    priority_adjustment: int = 0,
    priority_explanation: str = "",
    source_duplicate_pair_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    if source_document_id == target_document_id:
        raise ValueError("A document cannot be related to itself")
    if relationship_type not in RELATIONSHIP_TYPES:
        raise ValueError(f"Unsupported relationship type: {relationship_type}")
    if provenance not in PROVENANCE_TYPES:
        raise ValueError(f"Unsupported provenance: {provenance}")
    if confidence is not None and not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    if not 0 <= priority_adjustment <= 100:
        raise ValueError("priority_adjustment must be between 0 and 100")

    if relationship_type in SYMMETRIC_TYPES and source_document_id > target_document_id:
        source_document_id, target_document_id = target_document_id, source_document_id

    session = get_session()
    try:
        active = (
            session.query(DocumentRelationship)
            .filter(
                DocumentRelationship.removed_at.is_(None),
                (
                    (DocumentRelationship.source_document_id == source_document_id)
                    & (DocumentRelationship.target_document_id == target_document_id)
                )
                | (
                    (DocumentRelationship.source_document_id == target_document_id)
                    & (DocumentRelationship.target_document_id == source_document_id)
                ),
            )
            .all()
        )
        for existing in active:
            same_direction = (
                existing.source_document_id == source_document_id
                and existing.target_document_id == target_document_id
            )
            if existing.relationship_type == relationship_type and (
                same_direction or relationship_type in SYMMETRIC_TYPES
            ):
                return _relationship_to_dict(existing), False
            if relationship_type in {"follows", "supersedes"} and existing.relationship_type in {
                "follows",
                "supersedes",
            }:
                raise RelationshipConflictError(
                    "The document pair already has an incompatible active sequence relationship"
                )

        relationship = DocumentRelationship(
            source_document_id=source_document_id,
            target_document_id=target_document_id,
            relationship_type=relationship_type,
            provenance=provenance,
            confidence=confidence,
            reason_codes_json=json.dumps(list(reason_codes)),
            priority_adjustment=priority_adjustment,
            priority_explanation=priority_explanation,
            source_duplicate_pair_id=source_duplicate_pair_id,
        )
        session.add(relationship)
        session.flush()
        session.add(
            CorrectionEvent(
                event_type="relationship_created",
                target_type="document_relationship",
                target_id=relationship.id,
                payload_json=json.dumps(
                    {
                        "source_document_id": source_document_id,
                        "target_document_id": target_document_id,
                        "relationship_type": relationship_type,
                        "provenance": provenance,
                        "reason_codes": list(reason_codes),
                        "priority_adjustment": priority_adjustment,
                    }
                ),
            )
        )
        session.commit()
        session.refresh(relationship)
        return _relationship_to_dict(relationship), True
    finally:
        session.close()


def remove_document_relationship(
    relationship_id: str, *, removed_by: str = "user"
) -> dict[str, Any] | None:
    session = get_session()
    try:
        relationship = (
            session.query(DocumentRelationship)
            .filter(DocumentRelationship.id == relationship_id)
            .first()
        )
        if not relationship:
            return None
        if relationship.removed_at is None:
            relationship.removed_at = datetime.now(UTC)
            relationship.removed_by = removed_by
            relationship.paperless_synced = 0
            session.add(
                CorrectionEvent(
                    event_type="relationship_removed",
                    target_type="document_relationship",
                    target_id=relationship.id,
                    payload_json=json.dumps(
                        {
                            "source_document_id": relationship.source_document_id,
                            "target_document_id": relationship.target_document_id,
                            "relationship_type": relationship.relationship_type,
                            "removed_by": removed_by,
                        }
                    ),
                )
            )
            session.commit()
        return _relationship_to_dict(relationship)
    finally:
        session.close()


def set_projection_result(
    relationship_id: str, *, synced: bool, error: str | None
) -> dict[str, Any] | None:
    session = get_session()
    try:
        relationship = (
            session.query(DocumentRelationship)
            .filter(DocumentRelationship.id == relationship_id)
            .first()
        )
        if not relationship:
            return None
        relationship.paperless_synced = int(synced)
        relationship.projection_error = error
        session.commit()
        return _relationship_to_dict(relationship)
    finally:
        session.close()
