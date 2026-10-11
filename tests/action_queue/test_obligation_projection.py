from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest

from doc_intelligence_hub.modules.action_queue.config import settings
from doc_intelligence_hub.modules.action_queue.database import (
    Action,
    Obligation,
    ObligationDocument,
    get_session,
    init_db,
)
from doc_intelligence_hub.modules.action_queue.obligation_projection import (
    project_obligations_to_paperless,
)
from doc_intelligence_hub.modules.action_queue.obligations import (
    add_document,
    ensure_obligation,
    manually_link_document,
    unlink_document,
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


def _action(document_id: int = 10) -> Action:
    return Action(
        document_id=document_id,
        document_title="Utility invoice",
        action_type="PAY",
        title="Pay utility invoice",
        status="pending",
        document_date=date(2026, 10, 1),
    )


def _client() -> AsyncMock:
    client = AsyncMock()
    client.list_custom_fields.return_value = [
        {"id": 101, "name": "Related Documents", "data_type": "documentlink"},
        {"id": 102, "name": "Obligation ID", "data_type": "string"},
        {
            "id": 103,
            "name": "Obligation Role",
            "data_type": "select",
            "extra_data": {
                "select_options": [
                    {"id": 1, "label": "invoice"},
                    {"id": 2, "label": "receipt"},
                    {"id": 3, "label": "reminder"},
                    {"id": 4, "label": "revision"},
                    {"id": 5, "label": "supporting"},
                ]
            },
        },
    ]
    client.update_custom_fields_verified.return_value = {}
    return client


def _updates_by_document(client: AsyncMock) -> dict[int, list[dict]]:
    return {
        call.args[0]: call.args[1] for call in client.update_custom_fields_verified.await_args_list
    }


@pytest.mark.asyncio
async def test_projects_navigation_id_role_to_every_obligation_document(db):
    action = _action()
    db.add(action)
    db.flush()
    obligation = ensure_obligation(db, action)
    add_document(
        db,
        obligation,
        document_id=10,
        role="invoice",
        title="Utility invoice",
        document_type="Invoice",
        correspondent="Utility Co",
        document_date=date(2026, 10, 1),
        amount=100,
        reference_number="INV-10",
        confidence=1,
        source="action_queue",
    )
    add_document(
        db,
        obligation,
        document_id=20,
        role="reminder",
        title="Utility reminder",
        document_type="Letter",
        correspondent="Utility Co",
        document_date=date(2026, 10, 5),
        amount=100,
        reference_number="INV-10",
        confidence=0.95,
        source="relationship",
    )
    db.commit()
    client = _client()

    result = await project_obligations_to_paperless(db, client)

    assert result["synced"] is True
    updates = _updates_by_document(client)
    assert updates[10] == [
        {"field": 101, "value": [20]},
        {"field": 102, "value": obligation.id},
        {"field": 103, "value": 1},
    ]
    assert updates[20] == [
        {"field": 101, "value": [10]},
        {"field": 102, "value": obligation.id},
        {"field": 103, "value": 3},
    ]
    assert {update["field"] for values in updates.values() for update in values} == {101, 102, 103}


@pytest.mark.asyncio
async def test_unlink_clears_projection_and_rejected_automatic_relink_stays_cleared(db):
    action = _action()
    db.add(action)
    db.flush()
    manually_link_document(
        db,
        action,
        {"id": 20, "title": "Payment receipt", "document_type_name": "Receipt"},
    )
    db.commit()

    unlink_document(db, action, 20)
    db.commit()
    excluded = db.query(ObligationDocument).filter_by(document_id=20, excluded=True).one()
    add_document(
        db,
        db.get(Obligation, action.obligation_id),
        document_id=20,
        role="receipt",
        title="Payment receipt",
        document_type="Receipt",
        correspondent=None,
        document_date=None,
        amount=None,
        reference_number=None,
        confidence=0.99,
        source="automatic",
    )
    db.commit()
    assert excluded.excluded is True
    client = _client()

    result = await project_obligations_to_paperless(db, client, document_ids={10, 20})

    assert result["synced"] is True
    assert _updates_by_document(client)[20] == [
        {"field": 101, "value": []},
        {"field": 102, "value": None},
        {"field": 103, "value": None},
    ]


@pytest.mark.asyncio
async def test_backfill_is_idempotent(db):
    action = _action()
    db.add(action)
    db.flush()
    obligation = ensure_obligation(db, action)
    add_document(
        db,
        obligation,
        document_id=10,
        role="invoice",
        title="Utility invoice",
        document_type="Invoice",
        correspondent=None,
        document_date=None,
        amount=None,
        reference_number=None,
        confidence=1,
        source="action_queue",
    )
    db.commit()
    client = _client()

    first = await project_obligations_to_paperless(db, client)
    first_updates = client.update_custom_fields_verified.await_args_list.copy()
    client.update_custom_fields_verified.reset_mock()
    second = await project_obligations_to_paperless(db, client)

    assert first == second
    assert client.update_custom_fields_verified.await_args_list == first_updates


@pytest.mark.asyncio
async def test_partial_paperless_failure_reports_failed_document_and_continues(db):
    action = _action()
    db.add(action)
    db.flush()
    obligation = ensure_obligation(db, action)
    for document_id in (10, 20):
        add_document(
            db,
            obligation,
            document_id=document_id,
            role="invoice" if document_id == 10 else "supporting",
            title=f"Document {document_id}",
            document_type=None,
            correspondent=None,
            document_date=None,
            amount=None,
            reference_number=None,
            confidence=1,
            source="action_queue",
        )
    db.commit()
    client = _client()

    async def update(document_id, _updates):
        if document_id == 10:
            raise RuntimeError("Paperless permission denied")
        return {}

    client.update_custom_fields_verified.side_effect = update

    result = await project_obligations_to_paperless(db, client)

    assert result["synced"] is False
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert result["errors"] == [{"document_id": 10, "message": "Paperless permission denied"}]
    assert client.update_custom_fields_verified.await_count == 2


@pytest.mark.asyncio
async def test_read_only_mode_reports_deferred_projection_without_paperless_calls(db):
    action = _action()
    db.add(action)
    db.flush()
    obligation = ensure_obligation(db, action)
    add_document(
        db,
        obligation,
        document_id=10,
        role="invoice",
        title="Utility invoice",
        document_type="Invoice",
        correspondent=None,
        document_date=None,
        amount=None,
        reference_number=None,
        confidence=1,
        source="action_queue",
    )
    db.commit()
    client = _client()

    result = await project_obligations_to_paperless(
        db,
        client,
        write_enabled=False,
    )

    assert result["synced"] is False
    assert result["documents"] == [10]
    assert result["error"] == "Paperless writes are disabled"
    client.list_custom_fields.assert_not_awaited()
    client.update_custom_fields_verified.assert_not_awaited()
