"""Tests for the Triage Queue database persistence layer."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from doc_intelligence_hub.modules.triage.database import (
    configure,
    create_queue_item,
    defer_queue_item,
    dismiss_queue_item,
    get_queue_item,
    get_queue_stats,
    get_session,
    init_db,
    list_queue_items,
    resolve_queue_item,
    undo_resolution,
)


@pytest.fixture()
def db():
    """Create an in-memory SQLite database for each test."""
    configure("sqlite:///:memory:")
    init_db()
    session = get_session()
    yield session
    session.close()


def _create_sample_item(**overrides) -> dict:
    defaults = {
        "item_type": "eob_match_review",
        "source": "auto_flag",
        "target_type": "eob_match",
        "target_id": "42",
        "reason": "Low confidence match (55%)",
        "priority": 70,
        "metadata": {"score_pct": 55, "eob_document_id": 100, "bill_document_id": 200},
    }
    defaults.update(overrides)
    return create_queue_item(**defaults)


class TestCreateAndGet:
    def test_create_queue_item_returns_dict(self, db):
        item = _create_sample_item()
        assert item["id"]
        assert item["item_type"] == "eob_match_review"
        assert item["priority"] == 70
        assert item["status"] == "pending"
        assert item["reason"] == "Low confidence match (55%)"
        assert item["metadata"]["score_pct"] == 55

    def test_get_queue_item_by_id(self, db):
        created = _create_sample_item()
        fetched = get_queue_item(created["id"])
        assert fetched is not None
        assert fetched["id"] == created["id"]
        assert fetched["target_id"] == "42"

    def test_get_nonexistent_returns_none(self, db):
        assert get_queue_item("nonexistent") is None

    def test_create_with_no_metadata(self, db):
        item = create_queue_item(
            item_type="orphan_document",
            source="auto_flag",
            target_type="document",
            target_id="eob-99",
        )
        assert item["metadata"] is None


class TestListWithFilters:
    def test_list_defaults_to_pending(self, db):
        _create_sample_item(target_id="1")
        _create_sample_item(target_id="2")
        items = list_queue_items()
        assert len(items) == 2
        assert all(i["status"] == "pending" for i in items)

    def test_filter_by_type(self, db):
        _create_sample_item(item_type="eob_match_review", target_id="1")
        _create_sample_item(item_type="orphan_document", target_id="2", target_type="document")
        eob_items = list_queue_items(item_type="eob_match_review")
        assert len(eob_items) == 1
        assert eob_items[0]["item_type"] == "eob_match_review"

    def test_filter_by_status(self, db):
        item = _create_sample_item()
        dismiss_queue_item(item["id"])
        pending = list_queue_items(status="pending")
        dismissed = list_queue_items(status="dismissed")
        assert len(pending) == 0
        assert len(dismissed) == 1

    def test_sort_by_priority_desc(self, db):
        _create_sample_item(target_id="low", priority=30)
        _create_sample_item(target_id="high", priority=90)
        _create_sample_item(target_id="mid", priority=60)
        items = list_queue_items(sort="priority")
        priorities = [i["priority"] for i in items]
        assert priorities == [90, 60, 30]

    def test_sort_by_created_at(self, db):
        _create_sample_item(target_id="1")
        _create_sample_item(target_id="2")
        items = list_queue_items(sort="created_at")
        assert len(items) == 2

    def test_pagination(self, db):
        for i in range(5):
            _create_sample_item(target_id=str(i))
        page1 = list_queue_items(limit=2, offset=0)
        page2 = list_queue_items(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0]["id"] != page2[0]["id"]


class TestResolve:
    def test_resolve_changes_status(self, db):
        item = _create_sample_item()
        resolved = resolve_queue_item(item["id"], "confirm")
        assert resolved["status"] == "resolved"
        assert resolved["resolved_action"] == "confirm"
        assert resolved["resolved_at"] is not None

    def test_resolve_creates_correction_event(self, db):
        from doc_intelligence_hub.modules.triage.database import CorrectionEvent

        item = _create_sample_item()
        resolve_queue_item(item["id"], "reject", {"reason": "wrong match"})
        session = get_session()
        try:
            events = session.query(CorrectionEvent).all()
            assert len(events) == 1
            assert events[0].event_type == "triage_reject"
            assert events[0].target_id == "42"
            payload = json.loads(events[0].payload_json)
            assert payload["reason"] == "wrong match"
        finally:
            session.close()

    def test_resolve_nonexistent_returns_none(self, db):
        assert resolve_queue_item("nonexistent", "confirm") is None


class TestDefer:
    def test_defer_with_default_date(self, db):
        item = _create_sample_item()
        deferred = defer_queue_item(item["id"])
        assert deferred["status"] == "deferred"
        assert deferred["deferred_until"] is not None
        # Should be ~7 days from now
        defer_dt = datetime.fromisoformat(deferred["deferred_until"])
        now = datetime.now(UTC)
        # Make comparison tz-aware-safe: if defer_dt is naive, assume UTC
        if defer_dt.tzinfo is None:
            defer_dt = defer_dt.replace(tzinfo=UTC)
        assert defer_dt > now + timedelta(days=6)

    def test_defer_with_custom_date(self, db):
        item = _create_sample_item()
        target = "2026-12-25T00:00:00"
        deferred = defer_queue_item(item["id"], until=target)
        assert deferred["status"] == "deferred"
        assert "2026-12-25" in deferred["deferred_until"]

    def test_defer_nonexistent_returns_none(self, db):
        assert defer_queue_item("nonexistent") is None


class TestDismiss:
    def test_dismiss_changes_status(self, db):
        item = _create_sample_item()
        dismissed = dismiss_queue_item(item["id"])
        assert dismissed["status"] == "dismissed"
        assert dismissed["resolved_action"] == "dismissed"
        assert dismissed["resolved_at"] is not None

    def test_dismiss_nonexistent_returns_none(self, db):
        assert dismiss_queue_item("nonexistent") is None


class TestUndo:
    def test_undo_resets_to_pending(self, db):
        item = _create_sample_item()
        resolve_queue_item(item["id"], "confirm")
        undone = undo_resolution(item["id"])
        assert undone["status"] == "pending"
        assert undone["resolved_at"] is None
        assert undone["resolved_action"] is None
        assert undone["deferred_until"] is None

    def test_undo_deferred_item(self, db):
        item = _create_sample_item()
        defer_queue_item(item["id"])
        undone = undo_resolution(item["id"])
        assert undone["status"] == "pending"
        assert undone["deferred_until"] is None

    def test_undo_nonexistent_returns_none(self, db):
        assert undo_resolution("nonexistent") is None


class TestStats:
    def test_empty_stats(self, db):
        stats = get_queue_stats()
        assert stats["total"] == 0
        assert stats["pending"] == 0
        assert stats["by_type"] == {}
        assert stats["by_status"] == {}

    def test_stats_with_mixed_items(self, db):
        _create_sample_item(item_type="eob_match_review", target_id="1")
        _create_sample_item(item_type="eob_match_review", target_id="2")
        _create_sample_item(item_type="orphan_document", target_id="3", target_type="document")

        # Dismiss one
        items = list_queue_items()
        dismiss_queue_item(items[0]["id"])

        stats = get_queue_stats()
        assert stats["total"] == 3
        assert stats["pending"] == 2
        assert stats["by_status"]["pending"] == 2
        assert stats["by_status"]["dismissed"] == 1
