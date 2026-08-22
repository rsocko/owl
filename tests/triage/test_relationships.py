"""Tests for typed document relationships and related-notice classification."""

from __future__ import annotations

import json

import pytest

from doc_intelligence_hub.modules.triage.database import (
    CorrectionEvent,
    configure,
    get_session,
    init_db,
)
from doc_intelligence_hub.modules.triage.relationships import (
    RelationshipConflictError,
    calculate_priority_adjustment,
    classify_related_notice,
    create_document_relationship,
    list_document_relationships,
    remove_document_relationship,
)


@pytest.fixture()
def db():
    configure("sqlite:///:memory:")
    init_db()
    yield


def _bill(*, title: str, document_date: str, amount: float = 120.0) -> dict:
    return {
        "provider": "City Utilities",
        "invoice_number": "INV-2026-100",
        "amount": amount,
        "document_date": document_date,
        "title": title,
    }


def test_second_notice_is_linked_to_original_with_explainable_priority():
    proposal = classify_related_notice(
        10,
        _bill(title="Utility bill", document_date="2026-07-01"),
        20,
        _bill(title="Second Notice - Past Due", document_date="2026-08-01"),
    )

    assert proposal is not None
    assert proposal.source_document_id == 20
    assert proposal.target_document_id == 10
    assert proposal.relationship_type == "follows"
    assert proposal.auto_create is True
    assert proposal.priority_adjustment == 18
    assert "past_due" in proposal.reason_codes
    assert proposal.priority_explanation == "Priority +18: past due"


def test_final_revised_notice_supersedes_and_combines_compatible_rules():
    proposal = classify_related_notice(
        1,
        _bill(title="Original bill", document_date="2026-06-01"),
        2,
        _bill(title="Revised Final Notice", document_date="2026-08-01"),
    )

    assert proposal is not None
    assert proposal.relationship_type == "supersedes"
    assert proposal.priority_adjustment == 36
    assert proposal.reason_codes[-2:] == ("final_notice", "explicit_supersession")


def test_classifier_requires_provider_and_obligation_identity():
    unrelated = _bill(title="Second notice", document_date="2026-08-01")
    unrelated["provider"] = "Different Utility"
    assert (
        classify_related_notice(
            1,
            _bill(title="Original bill", document_date="2026-07-01"),
            2,
            unrelated,
        )
        is None
    )

    no_identity = _bill(title="Second notice", document_date="2026-08-01")
    no_identity["invoice_number"] = "OTHER"
    assert (
        classify_related_notice(
            1,
            _bill(title="Original bill", document_date="2026-07-01"),
            2,
            no_identity,
        )
        is None
    )


def test_priority_uses_highest_notice_stage_only():
    adjustment, reasons, explanation = calculate_priority_adjustment(
        "Second notice. Final notice before collections.", "follows"
    )
    assert adjustment == 35
    assert reasons == ("collections_or_disconnect",)
    assert explanation == "Priority +35: collections or disconnect"


def test_create_is_idempotent_and_queryable_in_both_directions(db):
    created, was_created = create_document_relationship(
        source_document_id=20,
        target_document_id=10,
        relationship_type="follows",
        provenance="user",
        confidence=0.95,
        reason_codes=["second_notice"],
        priority_adjustment=12,
        priority_explanation="Priority +12: second notice",
    )
    repeated, repeated_created = create_document_relationship(
        source_document_id=20,
        target_document_id=10,
        relationship_type="follows",
        provenance="user",
    )

    assert was_created is True
    assert repeated_created is False
    assert repeated["id"] == created["id"]
    assert list_document_relationships(20, "outgoing")[0]["target_document_id"] == 10
    assert list_document_relationships(10, "incoming")[0]["source_document_id"] == 20


def test_symmetric_reverse_creation_is_idempotent(db):
    first, _ = create_document_relationship(
        source_document_id=2,
        target_document_id=1,
        relationship_type="same_sequence",
        provenance="user",
    )
    reverse, created = create_document_relationship(
        source_document_id=1,
        target_document_id=2,
        relationship_type="same_sequence",
        provenance="user",
    )
    assert created is False
    assert reverse["id"] == first["id"]
    assert first["source_document_id"] == 1


def test_conflicting_sequence_relationship_is_rejected(db):
    create_document_relationship(
        source_document_id=2,
        target_document_id=1,
        relationship_type="follows",
        provenance="user",
    )
    with pytest.raises(RelationshipConflictError, match="incompatible"):
        create_document_relationship(
            source_document_id=1,
            target_document_id=2,
            relationship_type="supersedes",
            provenance="user",
        )


def test_removal_preserves_audit_history(db):
    relationship, _ = create_document_relationship(
        source_document_id=2,
        target_document_id=1,
        relationship_type="follows",
        provenance="user",
    )
    removed = remove_document_relationship(relationship["id"], removed_by="reviewer")

    assert removed is not None
    assert removed["removed_at"] is not None
    assert list_document_relationships(1) == []
    assert len(list_document_relationships(1, include_removed=True)) == 1

    session = get_session()
    try:
        events = (
            session.query(CorrectionEvent)
            .filter(CorrectionEvent.target_id == relationship["id"])
            .order_by(CorrectionEvent.created_at)
            .all()
        )
        assert [event.event_type for event in events] == [
            "relationship_created",
            "relationship_removed",
        ]
        assert json.loads(events[-1].payload_json)["removed_by"] == "reviewer"
    finally:
        session.close()
