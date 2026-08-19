from __future__ import annotations

import json
import os
import stat
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from doc_intelligence_hub.core.paperless import PaperlessPage
from doc_intelligence_hub.modules.metadata_migration.models import MigrationResult
from doc_intelligence_hub.modules.paperless_quality.cli import _require_apply_gates
from doc_intelligence_hub.modules.paperless_quality.config import QualityConfig
from doc_intelligence_hub.modules.paperless_quality.registry import (
    QUALITY_VIEW_REGISTRY,
    QualityViewKey,
    quality_view_key_from_name,
)
from doc_intelligence_hub.modules.paperless_quality.service import (
    PaperlessQualityService,
    load_protected_plan,
)


def _config() -> QualityConfig:
    return QualityConfig.model_validate(
        {
            "expected_origin": "https://paperless.example.test",
            "owner_id": 9,
            "document_types": {"record": 10, "other": 11, "manual": 12, "eob": 13},
            "storage_paths": {"manual": 20},
            "account_identifier": {
                "canonical_field_id": 30,
                "canonical_data_type": "string",
                "legacy_field_ids": [31],
            },
            "household_member_tag_ids": [40, 41],
            "review_complete_tag_ids": [42],
            "duplicate_correspondent_ids": [],
            "recent_window_days": 14,
            "expected_counts": {
                "record": 1,
                "other": 1,
                "manual_missing_storage_path": 1,
            },
        }
    )


class FakeStateStore:
    def __init__(self):
        self.runs: list[tuple[str, str]] = []
        self.records = []
        self.finished: list[tuple[str, str]] = []

    def start_run(self, run_id: str, **kwargs) -> None:
        self.runs.append((run_id, kwargs["mode"]))

    def record_and_checkpoint(self, run_id, record, next_cursor) -> None:
        self.records.append(record)

    def finish_run(self, run_id: str, completion_state: str) -> None:
        self.finished.append((run_id, completion_state))


class FakeClient:
    base_url = "https://paperless.example.test"

    def __init__(self):
        self.views: list[dict] = []
        self.created: list[dict] = []
        self.updated_views: list[tuple[int, dict]] = []
        self.updated_documents: list[tuple[int, dict]] = []
        self.documents = {
            100: {
                "id": 100,
                "title": "private title",
                "content": "private content",
                "modified": "2026-08-18T12:00:00Z",
                "document_type": 12,
                "storage_path": None,
                "custom_fields": [{"field": 31, "value": "ending 1234"}],
            },
            101: {
                "id": 101,
                "modified": "2026-08-18T12:00:00Z",
                "document_type": 10,
                "storage_path": None,
                "custom_fields": [
                    {"field": 30, "value": "ending 1111"},
                    {"field": 31, "value": "ending 2222"},
                ],
            },
        }

    async def list_custom_fields(self):
        return [
            {"id": 30, "name": "Account Identifier", "data_type": "string"},
            {"id": 31, "name": "di_account_id", "data_type": "string"},
        ]

    async def list_saved_views(self):
        return deepcopy(self.views)

    async def count_documents(self, params):
        if params.get("document_type__id") == "10":
            return 1
        if params.get("document_type__id") == "11":
            return 1
        if (
            params.get("document_type__id") == "12"
            and params.get("storage_path__isnull") == 1
        ):
            return 1
        if "custom_field_query" in params:
            return 2
        return 0

    async def list_documents_filtered(self, params):
        if (
            params.get("document_type__id") == "12"
            and params.get("storage_path__isnull") == 1
        ):
            return [deepcopy(self.documents[100])]
        return []

    async def iter_document_pages(self, *, page_size: int, cursor=None):
        yield PaperlessPage(tuple(deepcopy(list(self.documents.values()))), None, 2)

    async def create_saved_view(self, definition):
        created = {"id": len(self.views) + 1, **deepcopy(definition)}
        self.views.append(created)
        self.created.append(created)
        return deepcopy(created)

    async def update_saved_view(self, view_id, definition):
        updated = {"id": view_id, **deepcopy(definition)}
        self.views = [updated if item["id"] == view_id else item for item in self.views]
        self.updated_views.append((view_id, updated))
        return deepcopy(updated)

    async def get_document(self, document_id):
        return deepcopy(self.documents[document_id])

    async def update_document(self, document_id, data):
        self.updated_documents.append((document_id, deepcopy(data)))
        self.documents[document_id].update(data)
        self.documents[document_id]["modified"] = "2026-08-18T13:00:00Z"
        return deepcopy(self.documents[document_id])


def _service(client: FakeClient, config: QualityConfig | None = None) -> PaperlessQualityService:
    return PaperlessQualityService(
        client,
        config or _config(),
        allow_unverified_windows_permissions=True,
    )


@pytest.mark.asyncio
async def test_plan_is_get_only_redacted_and_locks_private_details(tmp_path: Path):
    client = FakeClient()
    protected = tmp_path / "quality-plan.json"

    summary = await _service(client).plan(protected_output=protected)

    assert client.created == []
    assert client.updated_documents == []
    assert len(summary.views) == len(QUALITY_VIEW_REGISTRY)
    assert summary.counts["review"] == 1
    account = next(
        item
        for item in summary.views
        if item["stable_key"]
        == QualityViewKey.ACCOUNT_IDENTIFIER_MISSING_OR_CONFLICTING.value
    )
    assert account["observed_count"] == 2
    assert account["exact_count"] == 2
    rendered = summary.to_json()
    assert "private title" not in rendered
    assert "private content" not in rendered
    assert '"document_id"' not in rendered
    assert "paperless.example.test" not in rendered
    detailed = json.loads(protected.read_text(encoding="utf-8"))
    assert detailed["manual_candidates"][0]["document_id"] == 100
    assert "private title" not in protected.read_text(encoding="utf-8")
    assert detailed["plan_digest"] == summary.plan_digest


@pytest.mark.asyncio
async def test_view_apply_is_idempotent_and_never_auto_merges_correspondents(tmp_path: Path):
    client = FakeClient()
    protected = tmp_path / "quality-plan.json"
    service = _service(client)
    await service.plan(protected_output=protected)
    plan = load_protected_plan(protected)

    first_store = FakeStateStore()
    first = await service.apply_views(
        plan,
        approval=f"saved-views:{plan.plan_digest}",
        state_store=first_store,
    )

    assert len(client.created) == len(QUALITY_VIEW_REGISTRY) - 1
    assert first.counts[MigrationResult.APPLIED.value] == len(QUALITY_VIEW_REGISTRY) - 1
    duplicate_record = next(
        record
        for record in first_store.records
        if record.stable_key == QualityViewKey.DUPLICATE_CORRESPONDENT_CANDIDATES.value
    )
    assert duplicate_record.result is MigrationResult.REVIEW_REQUIRED
    assert client.updated_documents == []

    second_store = FakeStateStore()
    second = await service.apply_views(
        plan,
        approval=f"saved-views:{plan.plan_digest}",
        state_store=second_store,
    )
    assert len(client.created) == len(QUALITY_VIEW_REGISTRY) - 1
    assert second.counts[MigrationResult.RECONCILED.value] == len(QUALITY_VIEW_REGISTRY) - 1


@pytest.mark.asyncio
async def test_view_apply_refuses_drift_after_plan(tmp_path: Path):
    client = FakeClient()
    definition = QUALITY_VIEW_REGISTRY[QualityViewKey.INBOX]
    client.views.append(
        {
            "id": 88,
            "name": definition.name,
            "owner": 9,
            "filter_rules": [{"rule_type": 5, "value": "true"}],
            "sort_field": "added",
            "sort_reverse": True,
        }
    )
    protected = tmp_path / "quality-plan.json"
    service = _service(client)
    await service.plan(protected_output=protected)
    plan = load_protected_plan(protected)
    client.views[0]["owner"] = 10

    store = FakeStateStore()
    summary = await service.apply_views(
        plan,
        approval=f"saved-views:{plan.plan_digest}",
        state_store=store,
    )

    inbox = next(record for record in store.records if record.stable_key == "inbox")
    assert inbox.result is MigrationResult.REVIEW_REQUIRED
    assert summary.completion_state == "review_required"
    assert client.updated_views == []


@pytest.mark.asyncio
async def test_string_owner_id_is_equivalent(tmp_path: Path):
    client = FakeClient()
    definition = QUALITY_VIEW_REGISTRY[QualityViewKey.INBOX]
    client.views.append(
        {
            "id": 88,
            "name": definition.name,
            "owner": "9",
            "filter_rules": [{"rule_type": 5, "value": "true"}],
            "sort_field": "added",
            "sort_reverse": True,
        }
    )
    protected = tmp_path / "quality-plan.json"
    service = _service(client)
    summary = await service.plan(protected_output=protected)
    plan = load_protected_plan(protected)

    inbox_plan = next(item for item in summary.views if item["stable_key"] == "inbox")
    assert inbox_plan["action"] == "none"
    applied = await service.apply_views(
        plan,
        approval=f"saved-views:{plan.plan_digest}",
        state_store=FakeStateStore(),
    )
    assert applied.counts[MigrationResult.SKIPPED.value] == 1
    assert client.updated_views == []


@pytest.mark.asyncio
async def test_plan_reports_sort_drift_as_update(tmp_path: Path):
    client = FakeClient()
    definition = QUALITY_VIEW_REGISTRY[QualityViewKey.INBOX]
    client.views.append(
        {
            "id": 88,
            "name": definition.name,
            "owner": 9,
            "filter_rules": [{"rule_type": 5, "value": "true"}],
            "sort_field": "created",
            "sort_reverse": False,
        }
    )

    summary = await _service(client).plan(
        protected_output=tmp_path / "quality-plan.json"
    )

    inbox = next(item for item in summary.views if item["stable_key"] == "inbox")
    assert inbox["action"] == "update"
    assert inbox["reason_code"] == "definition_drift"


@pytest.mark.asyncio
async def test_manual_apply_checks_conflicts_verifies_and_reconciles(tmp_path: Path):
    client = FakeClient()
    protected = tmp_path / "quality-plan.json"
    service = _service(client)
    await service.plan(protected_output=protected)
    plan = load_protected_plan(protected)

    first_store = FakeStateStore()
    first = await service.apply_manual_storage_path(
        plan,
        approval=f"manual-storage-path:{plan.plan_digest}",
        state_store=first_store,
        batch_size=25,
    )
    assert first.counts == {MigrationResult.APPLIED.value: 1}
    assert client.updated_documents == [(100, {"storage_path": 20})]

    second_store = FakeStateStore()
    second = await service.apply_manual_storage_path(
        plan,
        approval=f"manual-storage-path:{plan.plan_digest}",
        state_store=second_store,
        batch_size=25,
    )
    assert second.counts == {MigrationResult.RECONCILED.value: 1}
    assert len(client.updated_documents) == 1


@pytest.mark.asyncio
async def test_manual_apply_leaves_changed_document_queued(tmp_path: Path):
    client = FakeClient()
    protected = tmp_path / "quality-plan.json"
    service = _service(client)
    await service.plan(protected_output=protected)
    plan = load_protected_plan(protected)
    client.documents[100]["modified"] = "2026-08-18T14:00:00Z"

    store = FakeStateStore()
    summary = await service.apply_manual_storage_path(
        plan,
        approval=f"manual-storage-path:{plan.plan_digest}",
        state_store=store,
        batch_size=25,
    )

    assert summary.completion_state == "review_required"
    assert client.updated_documents == []
    assert store.records[0].result is MigrationResult.REVIEW_REQUIRED


@pytest.mark.asyncio
async def test_expected_count_tripwire_blocks_apply(tmp_path: Path):
    client = FakeClient()
    config = _config()
    config.expected_counts["record"] = 999
    protected = tmp_path / "quality-plan.json"
    service = _service(client, config)
    summary = await service.plan(protected_output=protected)
    plan = load_protected_plan(protected)

    assert summary.completion_state == "review_required"
    with pytest.raises(ValueError, match="expected-count tripwire"):
        await service.apply_views(
            plan,
            approval=f"saved-views:{plan.plan_digest}",
            state_store=FakeStateStore(),
        )
    assert client.created == []


@pytest.mark.asyncio
async def test_manual_batch_reports_remaining_and_advances_on_next_run(tmp_path: Path):
    client = FakeClient()
    client.documents[102] = {
        "id": 102,
        "modified": "2026-08-18T12:00:00Z",
        "document_type": 12,
        "storage_path": None,
        "custom_fields": [],
    }

    async def two_manuals(params):
        return [deepcopy(client.documents[100]), deepcopy(client.documents[102])]

    client.list_documents_filtered = two_manuals
    protected = tmp_path / "quality-plan.json"
    service = _service(client)
    await service.plan(protected_output=protected)
    plan = load_protected_plan(protected)

    first = await service.apply_manual_storage_path(
        plan,
        approval=f"manual-storage-path:{plan.plan_digest}",
        state_store=FakeStateStore(),
        batch_size=1,
    )
    assert first.completion_state == "partial"
    assert first.counts["remaining"] == 1

    second = await service.apply_manual_storage_path(
        plan,
        approval=f"manual-storage-path:{plan.plan_digest}",
        state_store=FakeStateStore(),
        batch_size=1,
    )
    assert second.completion_state == "completed"
    assert [document_id for document_id, _ in client.updated_documents] == [100, 102]


def test_tampered_plan_is_rejected(tmp_path: Path):
    protected = tmp_path / "quality-plan.json"
    protected.write_text(
        json.dumps(
            {
                "plan_digest": "not-a-real-digest",
                "config_digest": "config",
                "instance_digest": "instance",
                "planned_at": "2026-08-18T12:00:00+00:00",
                "views": [],
                "manual_candidates": [],
            }
        ),
        encoding="utf-8",
    )
    plan = load_protected_plan(protected)
    service = _service(FakeClient())

    with pytest.raises(ValueError, match="manifest changed|content does not match"):
        service._validate_locked_plan(plan, "saved-views:not-a-real-digest", "saved-views")


@pytest.mark.asyncio
async def test_protected_plan_permissions_fail_closed_or_are_owner_only(tmp_path: Path):
    protected = tmp_path / "quality-plan.json"
    service = PaperlessQualityService(FakeClient(), _config())

    if os.name == "nt":
        with pytest.raises(PermissionError, match="ACL protection cannot be verified"):
            await service.plan(protected_output=protected)
        assert not protected.exists()
    else:
        await service.plan(protected_output=protected)
        assert stat.S_IMODE(protected.stat().st_mode) & 0o077 == 0


def test_apply_gates_fail_closed(monkeypatch):
    monkeypatch.setenv("WRITE_TO_PAPERLESS", "true")
    with pytest.raises(Exception) as exc_info:
        _require_apply_gates(
            external_writers_disabled=True,
            state_db="protected.sqlite",
        )
    assert "WRITE_TO_PAPERLESS" in str(exc_info.value)


def test_apply_gates_reject_future_backup_attestation(monkeypatch):
    monkeypatch.setenv("WRITE_TO_PAPERLESS", "false")
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    with pytest.raises(Exception, match="under 24h old"):
        _require_apply_gates(
            external_writers_disabled=True,
            state_db="protected.sqlite",
            backup_verified_at=(future,),
        )


def test_managed_name_maps_to_action_queue_stable_key():
    definition = QUALITY_VIEW_REGISTRY[QualityViewKey.MISSING_CORRESPONDENT]
    assert quality_view_key_from_name(definition.name) == "missing_correspondent"
