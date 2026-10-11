"""Tests for obligation grouping and receipt completion suggestions."""

import json
from datetime import UTC, date, datetime

import pytest

from doc_intelligence_hub.modules.action_queue.config import settings
from doc_intelligence_hub.modules.action_queue.database import (
    Action,
    Obligation,
    ObligationEvent,
    get_session,
    init_db,
)
from doc_intelligence_hub.modules.action_queue.obligations import (
    associate_pay_action,
    associate_receipt,
    backfill_obligations,
    completion_suggestion,
    linked_documents,
    manually_link_actions,
    manually_link_document,
    reconcile_deleted_source_documents,
    reconcile_relationship_obligations,
    suggest_related_actions,
    sync_obligation_status,
    unlink_document,
)
from doc_intelligence_hub.modules.triage.database import (
    DocumentRelationship,
    TriageQueueItem,
)
from doc_intelligence_hub.modules.triage.database import (
    get_session as get_triage_session,
)
from doc_intelligence_hub.modules.triage.database import (
    init_db as init_triage_db,
)
from doc_intelligence_hub.modules.triage.database import (
    set_db_url as set_triage_db_url,
)
from doc_intelligence_hub.modules.triage.relationships import (
    create_document_relationship,
    remove_document_relationship,
)


@pytest.fixture()
def db(tmp_path):
    original_url = settings.database_url
    settings.database_url = f"sqlite:///{tmp_path / 'actions.db'}"
    set_triage_db_url(f"sqlite:///{tmp_path / 'triage.db'}")
    init_db()
    init_triage_db()
    session = get_session()
    try:
        yield session
    finally:
        session.close()
        settings.database_url = original_url
        set_triage_db_url("sqlite:///data/triage.db")


def _pay_action(document_id: int, title: str, amount: float = 100.0) -> Action:
    return Action(
        document_id=document_id,
        document_title=title,
        action_type="PAY",
        title="Pay Utility Co invoice",
        amount=amount,
        document_amount=amount,
        correspondent="Utility Co",
        document_date=date(2026, 8, document_id),
        extracted_data={"reference_number": "INV-42"},
        status="pending",
    )


def _relationship(
    source_document_id: int,
    target_document_id: int,
    *,
    relationship_type: str = "follows",
    adjustment: int = 12,
    reason_codes: tuple[str, ...] = ("second_notice",),
) -> str:
    triage_db = get_triage_session()
    try:
        relationship = DocumentRelationship(
            source_document_id=source_document_id,
            target_document_id=target_document_id,
            relationship_type=relationship_type,
            provenance="automatic",
            confidence=0.95,
            reason_codes_json=json.dumps(reason_codes),
            priority_adjustment=adjustment,
            priority_explanation=(
                f"Priority +{adjustment}: "
                + ", ".join(code.replace("_", " ") for code in reason_codes)
            ),
        )
        triage_db.add(relationship)
        triage_db.commit()
        return relationship.id
    finally:
        triage_db.close()


def test_related_invoice_actions_share_one_obligation(db):
    _relationship(2, 1, adjustment=18, reason_codes=("past_due",))
    original = _pay_action(1, "Utility invoice")
    db.add(original)
    db.flush()
    obligation = associate_pay_action(db, original, {"id": 1, "title": original.document_title})

    reminder = _pay_action(2, "Second notice - past due", amount=104.0)
    db.add(reminder)
    db.flush()
    matched = associate_pay_action(db, reminder, {"id": 2, "title": reminder.document_title})
    db.flush()

    assert matched.id == obligation.id
    assert reminder.obligation_id == original.obligation_id
    assert reminder.superseded_by_action_id == original.id
    assert reminder.action_ready is False
    documents = linked_documents(db, original)
    assert [document["role"] for document in documents] == ["invoice", "reminder"]


def test_recurring_invoices_with_only_same_account_are_not_collapsed(db):
    july = _pay_action(1, "July utility invoice")
    july.extracted_data = {"account_identifier": "acct-1234"}
    db.add(july)
    db.flush()
    july_obligation = associate_pay_action(db, july, {"id": 1, "title": july.document_title})

    august = _pay_action(2, "August utility invoice")
    august.extracted_data = {"account_identifier": "acct-1234"}
    db.add(august)
    db.flush()
    august_obligation = associate_pay_action(db, august, {"id": 2, "title": august.document_title})

    assert august_obligation.id != july_obligation.id
    assert august.superseded_by_action_id is None


def test_exact_duplicate_invoice_documents_share_one_obligation(db):
    original = _pay_action(1, "Town tax bill")
    duplicate = _pay_action(2, "Town taxes - duplicate scan")
    original.document_date = duplicate.document_date = date(2026, 9, 30)
    original.due_date = duplicate.due_date = date(2026, 11, 2)
    db.add_all([original, duplicate])
    db.flush()

    associate_pay_action(
        db,
        original,
        {"id": original.document_id, "title": original.document_title},
    )
    matched = associate_pay_action(
        db,
        duplicate,
        {"id": duplicate.document_id, "title": duplicate.document_title},
    )

    assert matched.id == original.obligation_id
    assert matched.id == duplicate.obligation_id
    assert duplicate.obligation_id == original.obligation_id
    assert duplicate.superseded_by_action_id == original.id
    assert duplicate.action_ready is False
    assert {document["document_id"] for document in linked_documents(db, original)} == {1, 2}


def test_reused_reference_with_different_due_date_stays_separate(db):
    first = _pay_action(1, "First tax installment")
    second = _pay_action(2, "Second tax installment")
    first.document_date = second.document_date = date(2026, 9, 30)
    first.due_date = date(2026, 11, 2)
    second.due_date = date(2027, 2, 1)
    db.add_all([first, second])
    db.flush()

    first_obligation = associate_pay_action(
        db,
        first,
        {"id": first.document_id, "title": first.document_title},
    )
    second_obligation = associate_pay_action(
        db,
        second,
        {"id": second.document_id, "title": second.document_title},
    )

    assert second_obligation.id != first_obligation.id
    assert first.superseded_by_action_id is None
    assert second.superseded_by_action_id is None


def test_backfill_groups_legacy_invoice_actions(db):
    _relationship(2, 1)
    original = _pay_action(1, "Utility invoice")
    reminder = _pay_action(2, "Second notice - past due", amount=104.0)
    db.add_all([original, reminder])
    db.flush()

    assert backfill_obligations(db) == 2
    assert original.obligation_id is not None
    assert reminder.obligation_id == original.obligation_id
    assert reminder.superseded_by_action_id == original.id


def test_backfill_consolidates_existing_duplicate_obligations(db):
    original = _pay_action(1, "Town tax bill")
    duplicate = _pay_action(2, "Town taxes - duplicate scan")
    original.document_date = duplicate.document_date = date(2026, 9, 30)
    original.due_date = duplicate.due_date = date(2026, 11, 2)
    db.add_all([original, duplicate])
    db.flush()
    original_obligation = Obligation(primary_action_id=original.id, status="open")
    duplicate_obligation = Obligation(primary_action_id=duplicate.id, status="open")
    db.add_all([original_obligation, duplicate_obligation])
    db.flush()
    original.obligation_id = original_obligation.id
    duplicate.obligation_id = duplicate_obligation.id

    assert backfill_obligations(db) == 0

    assert duplicate.obligation_id == original.obligation_id
    assert duplicate.superseded_by_action_id == original.id
    assert duplicate.action_ready is False


def test_deleted_primary_source_promotes_surviving_duplicate(db):
    original = _pay_action(1, "Town tax bill")
    duplicate = _pay_action(2, "Town taxes - duplicate scan")
    original.document_date = duplicate.document_date = date(2026, 9, 30)
    original.due_date = duplicate.due_date = date(2026, 11, 2)
    db.add_all([original, duplicate])
    db.flush()
    associate_pay_action(
        db,
        original,
        {"id": original.document_id, "title": original.document_title},
    )
    associate_pay_action(
        db,
        duplicate,
        {"id": duplicate.document_id, "title": duplicate.document_title},
    )

    assert reconcile_deleted_source_documents(db, {1}) == 1

    assert original.status == "source_deleted"
    assert original.action_ready is False
    assert duplicate.status == "pending"
    assert duplicate.action_ready is True
    assert duplicate.superseded_by_action_id is None
    obligation = db.query(Obligation).filter_by(id=duplicate.obligation_id).one()
    assert obligation.primary_action_id == duplicate.id
    assert obligation.authoritative_document_id == duplicate.document_id
    assert [document["document_id"] for document in linked_documents(db, duplicate)] == [2]


def test_deleted_only_source_hides_action_and_tombstones_obligation(db):
    action = _pay_action(1, "Town tax bill")
    db.add(action)
    db.flush()
    obligation = associate_pay_action(
        db,
        action,
        {"id": action.document_id, "title": action.document_title},
    )

    assert reconcile_deleted_source_documents(db, {1}) == 1

    assert action.status == "source_deleted"
    assert action.action_ready is False
    assert obligation.status == "source_deleted"
    assert obligation.primary_action_id is None
    assert obligation.authoritative_document_id is None


def test_notice_sequence_uses_final_notice_as_authority_and_preserves_timeline(db):
    _relationship(
        2,
        1,
        relationship_type="supersedes",
        adjustment=20,
        reason_codes=("second_notice", "explicit_supersession"),
    )
    _relationship(3, 2, adjustment=28, reason_codes=("final_notice",))
    original = _pay_action(1, "Original invoice", amount=100)
    second = _pay_action(2, "Second notice", amount=112)
    final = _pay_action(3, "Final notice", amount=128)
    final.due_date = date(2026, 8, 4)
    final.urgency = "HIGH"
    final.ai_reasoning = "Final notice requires payment."
    db.add_all([final, original, second])
    db.flush()

    for action in (final, original, second):
        associate_pay_action(db, action, {"id": action.document_id, "title": action.document_title})
    db.flush()

    canonical = db.query(Action).filter(Action.superseded_by_action_id.is_(None)).one()
    obligation = db.query(Obligation).filter_by(id=canonical.obligation_id).one()
    assert obligation.authoritative_document_id == 3
    assert canonical.amount == 128
    assert canonical.due_date == date(2026, 8, 4)
    assert canonical.urgency == "CRITICAL"
    assert canonical.risk_score >= 36
    assert obligation.relationship_rules == [
        "explicit_supersession",
        "final_notice",
        "second_notice",
    ]
    assert [item["document_id"] for item in linked_documents(db, canonical)] == [1, 2, 3]
    assert [item["is_authoritative"] for item in linked_documents(db, canonical)] == [
        False,
        False,
        True,
    ]
    assert db.query(ObligationEvent).count() >= 1


def test_out_of_order_notice_arrival_keeps_stable_obligation_identity(db):
    _relationship(2, 1)
    reminder = _pay_action(2, "Second notice", amount=112)
    db.add(reminder)
    db.flush()
    first = associate_pay_action(db, reminder, {"id": 2, "title": reminder.document_title})
    identity = first.identity_key

    original = _pay_action(1, "Original invoice", amount=100)
    db.add(original)
    db.flush()
    obligation = associate_pay_action(db, original, {"id": 1, "title": original.document_title})

    assert obligation.id == first.id
    assert obligation.identity_key == identity
    assert obligation.authoritative_document_id == 2
    assert db.query(Action).filter(Action.superseded_by_action_id.is_(None)).count() == 1


def test_relationship_removal_splits_actions_and_recalculates_original_values(db):
    relationship_id = _relationship(2, 1, adjustment=28, reason_codes=("final_notice",))
    original = _pay_action(1, "Original invoice", amount=100)
    original.due_date = date(2026, 8, 10)
    final = _pay_action(2, "Final notice", amount=145)
    final.due_date = date(2026, 8, 3)
    db.add_all([original, final])
    db.flush()
    associate_pay_action(db, original, {"id": 1, "title": original.document_title})
    associate_pay_action(db, final, {"id": 2, "title": final.document_title})
    assert db.query(Action).filter(Action.superseded_by_action_id.is_(None)).count() == 1

    triage_db = get_triage_session()
    try:
        relationship = triage_db.query(DocumentRelationship).filter_by(id=relationship_id).one()
        relationship.removed_at = datetime.now(UTC)
        triage_db.commit()
    finally:
        triage_db.close()
    reconcile_relationship_obligations(
        db,
        {1, 2},
        relationship_id=relationship_id,
        event_type="relationship_removed",
    )

    active = (
        db.query(Action)
        .filter(Action.superseded_by_action_id.is_(None))
        .order_by(Action.document_id)
        .all()
    )
    assert len(active) == 2
    assert active[0].amount == 100
    assert active[0].due_date == date(2026, 8, 10)
    assert active[1].amount == 145
    assert active[1].due_date == date(2026, 8, 3)
    assert active[0].obligation_id != active[1].obligation_id


def test_committed_relationship_create_and_remove_reconcile_action_database(db):
    original = _pay_action(1, "Original invoice", amount=100)
    reminder = _pay_action(2, "Second notice", amount=112)
    db.add_all([original, reminder])
    db.flush()
    associate_pay_action(db, original, {"id": 1, "title": original.document_title})
    associate_pay_action(db, reminder, {"id": 2, "title": reminder.document_title})
    db.commit()

    relationship, created = create_document_relationship(
        source_document_id=2,
        target_document_id=1,
        relationship_type="follows",
        provenance="automatic",
        confidence=0.95,
        reason_codes=("second_notice",),
        priority_adjustment=12,
        priority_explanation="Priority +12: second notice",
    )
    assert created is True
    db.expire_all()
    assert db.query(Action).filter(Action.superseded_by_action_id.is_(None)).count() == 1

    remove_document_relationship(relationship["id"], removed_by="test")
    db.expire_all()
    assert db.query(Action).filter(Action.superseded_by_action_id.is_(None)).count() == 2
    assert db.query(ObligationEvent).filter_by(event_type="relationship_removed").count() >= 1


def test_conflicting_relationship_identities_route_to_needs_review_without_merge(db):
    _relationship(2, 1)
    original = _pay_action(1, "Utility invoice")
    original.extracted_data = {"reference_number": "INV-42"}
    conflict = _pay_action(2, "Second notice")
    conflict.extracted_data = {"reference_number": "INV-99"}
    db.add_all([original, conflict])
    db.flush()
    associate_pay_action(db, original, {"id": 1, "title": original.document_title})
    associate_pay_action(db, conflict, {"id": 2, "title": conflict.document_title})

    assert original.obligation_id != conflict.obligation_id
    assert original.review_state == conflict.review_state == "needs_review"
    assert original.superseded_by_action_id is None
    assert conflict.superseded_by_action_id is None
    triage_db = get_triage_session()
    try:
        items = triage_db.query(TriageQueueItem).filter_by(item_type="action_classification").all()
        assert len(items) == 2
        assert all("conflicting invoice" in (item.reason or "").lower() for item in items)
    finally:
        triage_db.close()


def test_unrelated_bills_from_same_provider_stay_separate_without_typed_relationship(db):
    first = _pay_action(1, "July invoice")
    second = _pay_action(2, "August invoice")
    first.extracted_data = {"reference_number": "INV-JULY"}
    second.extracted_data = {"reference_number": "INV-AUGUST"}
    db.add_all([first, second])
    db.flush()
    associate_pay_action(db, first, {"id": 1, "title": first.document_title})
    associate_pay_action(db, second, {"id": 2, "title": second.document_title})

    assert first.obligation_id != second.obligation_id
    assert first.superseded_by_action_id is None
    assert second.superseded_by_action_id is None


@pytest.mark.parametrize("status", ["acknowledged", "completed"])
def test_notice_consolidation_preserves_explicit_action_lifecycle(db, status):
    _relationship(2, 1)
    original = _pay_action(1, "Utility invoice")
    original.status = status
    reminder = _pay_action(2, "Second notice", amount=112)
    db.add_all([original, reminder])
    db.flush()
    associate_pay_action(db, original, {"id": 1, "title": original.document_title})
    associate_pay_action(db, reminder, {"id": 2, "title": reminder.document_title})

    assert original.status == status
    assert original.superseded_by_action_id is None
    assert reminder.superseded_by_action_id == original.id
    assert db.query(Action).filter(Action.superseded_by_action_id.is_(None)).count() == 1


def test_suggestions_use_account_and_dates_without_auto_linking(db):
    original = _pay_action(1, "July water billing", amount=1382.38)
    original.extracted_data = {"account_identifier": "0513"}
    copy = _pay_action(2, "READ CODE Total Current Billing", amount=229.0)
    copy.extracted_data = {"account_identifier": "0513"}
    db.add_all([original, copy])
    db.flush()

    suggestions = suggest_related_actions(db, copy)

    assert suggestions[0]["action"].id == original.id
    assert "same account" in suggestions[0]["reasons"]
    assert "same correspondent" in suggestions[0]["reasons"]
    assert "same amount" not in suggestions[0]["reasons"]
    assert "similar amount" not in suggestions[0]["reasons"]
    assert suggestions[0]["score"] >= 0.5


def test_manual_link_merges_obligations_and_suppresses_related_action(db):
    primary = _pay_action(1, "July water billing", amount=1382.38)
    related = _pay_action(2, "READ CODE Total Current Billing", amount=229.0)
    primary.extracted_data = {"account_identifier": "0513"}
    related.extracted_data = {"account_identifier": "0513"}
    db.add_all([primary, related])
    db.flush()
    associate_pay_action(db, primary, {"id": 1, "title": primary.document_title})
    associate_pay_action(db, related, {"id": 2, "title": related.document_title})

    obligation = manually_link_actions(db, primary, related)

    assert primary.obligation_id == obligation.id
    assert related.obligation_id == obligation.id
    assert related.superseded_by_action_id == primary.id
    assert related.action_ready is False
    assert {document["document_id"] for document in linked_documents(db, primary)} == {1, 2}


def test_manual_link_receipt_suggests_completion_without_receipt_action(db):
    action = _pay_action(1, "Utility invoice")
    db.add(action)
    db.flush()

    linked = manually_link_document(
        db,
        action,
        {
            "id": 9,
            "title": "Payment Receipt",
            "document_type_name": "Receipt",
            "correspondent_name": "Utility Co",
            "created": "2026-08-20",
            "content": "Payment completed. Invoice INV-42. Total $100.00",
        },
    )

    assert linked.role == "receipt"
    assert linked.source == "user_link"
    assert completion_suggestion(db, action)["receipt_document_id"] == 9


def test_unlink_document_hides_auto_match_and_prevents_relink(db):
    action = _pay_action(1, "Utility invoice")
    db.add(action)
    db.flush()
    associate_pay_action(db, action, {"id": 1, "title": action.document_title})
    associate_receipt(
        db,
        {
            "id": 9,
            "title": "Payment Receipt",
            "document_type_name": "Receipt",
            "correspondent_name": "Utility Co",
        },
        "Invoice INV-42 paid $100.00",
    )

    assert unlink_document(db, action, 9) == []
    assert [document["document_id"] for document in linked_documents(db, action)] == [1]
    assert completion_suggestion(db, action) is None
    assert (
        associate_receipt(
            db,
            {
                "id": 9,
                "title": "Payment Receipt",
                "document_type_name": "Receipt",
                "correspondent_name": "Utility Co",
            },
            "Invoice INV-42 paid $100.00",
        )
        is None
    )


def test_unlink_action_document_restores_suppressed_action(db):
    primary = _pay_action(1, "July water billing", amount=1382.38)
    related = _pay_action(2, "READ CODE Total Current Billing", amount=229.0)
    db.add_all([primary, related])
    db.flush()
    associate_pay_action(db, primary, {"id": 1, "title": primary.document_title})
    manually_link_actions(db, primary, related)

    restored = unlink_document(db, primary, 2)

    assert restored == [related]
    assert related.obligation_id != primary.obligation_id
    assert related.superseded_by_action_id is None
    assert related.action_ready is True
    assert related.review_state == "ready"
    assert [document["document_id"] for document in linked_documents(db, primary)] == [1]
    assert [document["document_id"] for document in linked_documents(db, related)] == [2]


def test_receipt_suggests_completion_without_closing_action(db):
    action = _pay_action(1, "Utility invoice")
    db.add(action)
    db.flush()
    associate_pay_action(db, action, {"id": 1, "title": action.document_title})

    match = associate_receipt(
        db,
        {
            "id": 9,
            "title": "Payment Receipt",
            "document_type_name": "Receipt",
            "correspondent_name": "Utility Co",
            "created": "2026-08-20",
        },
        "Payment completed. Invoice INV-42. Total $104.00",
    )
    db.flush()

    assert match is not None
    assert match[0].id == action.id
    assert action.status == "pending"
    assert [document["role"] for document in linked_documents(db, action)] == [
        "invoice",
        "receipt",
    ]
    suggestion = completion_suggestion(db, action)
    assert suggestion is not None
    assert suggestion["receipt_document_id"] == 9


def test_explicit_completion_settles_obligation_and_reopen_preserves_receipt(db):
    action = _pay_action(1, "Utility invoice")
    db.add(action)
    db.flush()
    associate_pay_action(db, action, {"id": 1, "title": action.document_title})
    associate_receipt(
        db,
        {
            "id": 9,
            "title": "Payment Receipt",
            "document_type_name": "Receipt",
            "correspondent_name": "Utility Co",
        },
        "Invoice INV-42 paid $100.00",
    )

    action.status = "completed"
    sync_obligation_status(db, action)
    obligation = db.query(Obligation).filter_by(id=action.obligation_id).one()
    assert obligation.status == "settled"
    assert completion_suggestion(db, action) is None

    action.status = "pending"
    sync_obligation_status(db, action)
    assert obligation.status == "payment_detected"
    assert completion_suggestion(db, action) is not None
