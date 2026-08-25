"""Tests for obligation grouping and receipt completion suggestions."""

from datetime import date

import pytest

from doc_intelligence_hub.modules.action_queue.config import settings
from doc_intelligence_hub.modules.action_queue.database import (
    Action,
    Obligation,
    get_session,
    init_db,
)
from doc_intelligence_hub.modules.action_queue.obligations import (
    associate_pay_action,
    associate_receipt,
    backfill_obligations,
    completion_suggestion,
    linked_documents,
    sync_obligation_status,
)


@pytest.fixture()
def db(tmp_path):
    original_url = settings.database_url
    settings.database_url = f"sqlite:///{tmp_path / 'actions.db'}"
    init_db()
    session = get_session()
    try:
        yield session
    finally:
        session.close()
        settings.database_url = original_url


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


def test_related_invoice_actions_share_one_obligation(db):
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


def test_backfill_groups_legacy_invoice_actions(db):
    original = _pay_action(1, "Utility invoice")
    reminder = _pay_action(2, "Second notice - past due", amount=104.0)
    db.add_all([original, reminder])
    db.flush()

    assert backfill_obligations(db) == 2
    assert original.obligation_id is not None
    assert reminder.obligation_id == original.obligation_id
    assert reminder.superseded_by_action_id == original.id


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
