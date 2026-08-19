from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from doc_intelligence_hub.core.paperless import (
    PAPERLESS_METADATA_REGISTRY,
    MetadataFieldKey,
    PaperlessPage,
    resolve_metadata_schema,
)
from doc_intelligence_hub.modules.metadata_migration.cli import cli
from doc_intelligence_hub.modules.metadata_migration.models import (
    MigrationAction,
    MigrationResult,
    ProtectedRecord,
    ReasonCode,
)
from doc_intelligence_hub.modules.metadata_migration.service import (
    MIGRATION_KEYS,
    MetadataMigrationService,
)
from doc_intelligence_hub.modules.metadata_migration.state import SQLiteMigrationStateStore


def _definitions(*, include_provider: bool = True) -> list[dict]:
    definitions: list[dict] = []
    field_id = 10
    for key in MIGRATION_KEYS:
        spec = PAPERLESS_METADATA_REGISTRY[key]
        if key is MetadataFieldKey.PROVIDER_NAME and not include_provider:
            field_id += 2
            continue
        data_type = (
            spec.compatible_types[-1].value
            if key is MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE
            else spec.compatible_types[0].value
        )
        definitions.append({"id": field_id, "name": spec.canonical_name, "data_type": data_type})
        if spec.aliases:
            definitions.append(
                {
                    "id": field_id + 1,
                    "name": spec.aliases[0],
                    "data_type": data_type,
                }
            )
        field_id += 2
    return definitions


class FakeClient:
    base_url = "https://private.example.invalid"

    def __init__(self, definitions: list[dict], documents: list[dict]):
        self.definitions = definitions
        self.documents = {int(item["id"]): item for item in documents}
        self.created: list[dict] = []
        self.updates: list[tuple[int, list[dict]]] = []

    async def list_custom_fields(self) -> list[dict]:
        return self.definitions

    async def create_custom_field(self, definition: dict) -> dict:
        created = {"id": 100 + len(self.created), **definition}
        self.created.append(created)
        self.definitions.append(created)
        return created

    async def update_custom_field(self, field_id: int, data: dict) -> dict:
        raise AssertionError("Migration must not rename or mutate legacy definitions")

    async def iter_document_pages(self, *, page_size: int, cursor: str | None = None):
        documents = list(self.documents.values())
        start = int(cursor or "1") - 1
        for offset in range(start, len(documents), page_size):
            page_number = offset // page_size + 1
            next_cursor = str(page_number + 1) if offset + page_size < len(documents) else None
            yield PaperlessPage(
                tuple(documents[offset : offset + page_size]),
                next_cursor,
                len(documents),
            )

    async def get_document(self, document_id: int) -> dict:
        return self.documents[document_id]

    async def update_custom_fields_verified(
        self,
        document_id: int,
        custom_fields: list[dict],
        *,
        numeric_field_ids: set[int] | None = None,
    ) -> dict:
        self.updates.append((document_id, custom_fields))
        document = self.documents[document_id]
        values = {int(item["field"]): item["value"] for item in document.get("custom_fields", [])}
        values.update({int(item["field"]): item["value"] for item in custom_fields})
        document["custom_fields"] = [
            {"field": field_id, "value": value} for field_id, value in values.items()
        ]
        return document


def _provider_ids(definitions: list[dict]) -> tuple[int, int]:
    spec = PAPERLESS_METADATA_REGISTRY[MetadataFieldKey.PROVIDER_NAME]
    canonical = next(item["id"] for item in definitions if item["name"] == spec.canonical_name)
    alias = next(item["id"] for item in definitions if item["name"] == spec.aliases[0])
    return canonical, alias


@pytest.mark.asyncio
async def test_inventory_is_read_only_and_stdout_summary_is_redacted(tmp_path: Path):
    definitions = _definitions()
    canonical_id, alias_id = _provider_ids(definitions)
    client = FakeClient(
        definitions,
        [
            {
                "id": 7001,
                "title": "must-not-be-retained",
                "content": "must-not-be-retained",
                "custom_fields": [{"field": alias_id, "value": "Synthetic Provider"}],
            }
        ],
    )
    protected = tmp_path / "protected.json"

    summary = await MetadataMigrationService(client).inventory(
        batch_size=1, protected_output=protected
    )

    serialized = summary.to_json()
    assert client.updates == []
    assert "7001" not in serialized
    assert "field_id" not in serialized
    assert "Synthetic Provider" not in serialized
    assert "private.example.invalid" not in serialized
    assert summary.redacted is True
    detailed = json.loads(protected.read_text(encoding="utf-8"))
    assert detailed["records"][2]["document_id"] == 7001
    assert "must-not-be-retained" not in protected.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_account_identifier_audit_values_are_masked(tmp_path: Path):
    definitions = _definitions()
    account_spec = PAPERLESS_METADATA_REGISTRY[MetadataFieldKey.ACCOUNT_IDENTIFIER]
    alias_id = next(item["id"] for item in definitions if item["name"] == account_spec.aliases[0])
    secret = "SENSITIVE123456"
    client = FakeClient(
        definitions,
        [{"id": 7002, "custom_fields": [{"field": alias_id, "value": secret}]}],
    )
    protected = tmp_path / "protected.json"

    await MetadataMigrationService(client).inventory(batch_size=1, protected_output=protected)

    serialized = protected.read_text(encoding="utf-8")
    records = json.loads(serialized)["records"]
    account_record = next(
        record
        for record in records
        if record["stable_key"] == MetadataFieldKey.ACCOUNT_IDENTIFIER.value
    )
    assert account_record["before_value"] == "ending 3456"
    assert account_record["after_value"] == "ending 3456"
    assert secret not in serialized


@pytest.mark.asyncio
async def test_backfill_is_dry_run_by_default_and_apply_is_canonical_only(tmp_path: Path):
    definitions = _definitions()
    canonical_id, alias_id = _provider_ids(definitions)
    document = {
        "id": 42,
        "custom_fields": [{"field": alias_id, "value": "Synthetic Provider"}],
    }
    client = FakeClient(definitions, [document])
    service = MetadataMigrationService(client)

    dry_run = await service.backfill(batch_size=10)
    assert dry_run.counts[MigrationResult.PROPOSED.value] == 1
    assert client.updates == []

    store = SQLiteMigrationStateStore(
        tmp_path / "migration.sqlite", allow_unverified_windows_permissions=True
    )
    applied = await service.backfill(
        apply=True,
        state_store=store,
        run_id="synthetic-run",
        batch_size=10,
    )

    assert applied.counts[MigrationResult.APPLIED.value] == 1
    assert client.updates == [(42, [{"field": canonical_id, "value": "Synthetic Provider"}])]
    assert {item["field"] for item in document["custom_fields"]} == {
        alias_id,
        canonical_id,
    }


@pytest.mark.asyncio
async def test_prepare_creates_only_unambiguous_missing_canonical_fields():
    definitions = _definitions(include_provider=False)
    client = FakeClient(definitions, [])

    summary = await MetadataMigrationService(client).prepare(apply=True)

    assert [item["name"] for item in client.created] == ["Provider Name"]
    assert client.created[0]["data_type"] == "string"
    normalized = PAPERLESS_METADATA_REGISTRY[MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE]
    assert normalized.create_type is None
    assert any(
        item.stable_key == MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE.value
        and item.reason_code is ReasonCode.CANONICAL_PRESENT
        for item in summary.compatibility
    )


@pytest.mark.asyncio
async def test_prepare_refuses_undecided_normalized_document_type_creation():
    spec = PAPERLESS_METADATA_REGISTRY[MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE]
    definitions = [
        item for item in _definitions() if item["name"] not in {spec.canonical_name, *spec.aliases}
    ]
    client = FakeClient(definitions, [])

    summary = await MetadataMigrationService(client).prepare(apply=True)

    assert all(item["name"] != spec.canonical_name for item in client.created)
    assert summary.counts[MigrationResult.REVIEW_REQUIRED.value] == 1
    assert any(
        item.stable_key == MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE.value
        and item.reason_code is ReasonCode.TYPE_DECISION_REQUIRED
        for item in summary.compatibility
    )


@pytest.mark.asyncio
async def test_prepare_does_not_create_over_duplicate_canonical_fields():
    definitions = _definitions()
    spec = PAPERLESS_METADATA_REGISTRY[MetadataFieldKey.PROVIDER_NAME]
    definitions.append({"id": 999, "name": spec.canonical_name, "data_type": "string"})
    client = FakeClient(definitions, [])

    summary = await MetadataMigrationService(client).prepare(apply=True)

    assert client.created == []
    assert summary.counts[MigrationResult.REVIEW_REQUIRED.value] == 1


@pytest.mark.asyncio
async def test_inventory_marks_incompatible_schema_as_review_required():
    definitions = _definitions()
    spec = PAPERLESS_METADATA_REGISTRY[MetadataFieldKey.DATE_OF_SERVICE]
    definition = next(item for item in definitions if item["name"] == spec.canonical_name)
    definition["data_type"] = "integer"
    client = FakeClient(definitions, [])

    summary = await MetadataMigrationService(client).inventory()

    assert summary.counts[MigrationResult.REVIEW_REQUIRED.value] == 1
    assert summary.exit_status == 2


def test_idempotency_key_is_stable_across_numeric_write_representation():
    definitions = _definitions()
    spec = PAPERLESS_METADATA_REGISTRY[MetadataFieldKey.PATIENT_RESPONSIBILITY]
    canonical_id = next(item["id"] for item in definitions if item["name"] == spec.canonical_name)
    alias_id = next(item["id"] for item in definitions if item["name"] == spec.aliases[0])
    client = FakeClient(definitions, [])
    service = MetadataMigrationService(client)

    resolved = resolve_metadata_schema(definitions, MIGRATION_KEYS)
    proposed = next(
        item
        for item in service._plan_document(
            {"id": 9, "custom_fields": [{"field": alias_id, "value": "12.50"}]},
            resolved,
        )
        if item.stable_key == MetadataFieldKey.PATIENT_RESPONSIBILITY.value
    )
    after_write = next(
        item
        for item in service._plan_document(
            {
                "id": 9,
                "custom_fields": [
                    {"field": alias_id, "value": "12.50"},
                    {"field": canonical_id, "value": "12.500"},
                ],
            },
            resolved,
        )
        if item.stable_key == MetadataFieldKey.PATIENT_RESPONSIBILITY.value
    )

    assert proposed.idempotency_key == after_write.idempotency_key
    assert proposed.result is MigrationResult.PROPOSED
    assert after_write.result is MigrationResult.SKIPPED


def test_resume_refuses_changed_registry_or_configuration(tmp_path: Path):
    store = SQLiteMigrationStateStore(
        tmp_path / "migration.sqlite", allow_unverified_windows_permissions=True
    )
    store.start_run(
        "run-1",
        registry_digest="registry-a",
        config_digest="config-a",
        instance_digest="instance-a",
        mode="backfill",
    )
    record = ProtectedRecord(
        document_id=1,
        stable_key="provider_name",
        action=MigrationAction.BACKFILL_VALUE,
        result=MigrationResult.APPLIED,
        reason_code=ReasonCode.READY,
        idempotency_key="idempotent",
    )
    store.record_and_checkpoint("run-1", record, "2")
    assert (
        store.validate_resume(
            "run-1",
            registry_digest="registry-a",
            config_digest="config-a",
            instance_digest="instance-a",
            mode="backfill",
        )
        == "2"
    )
    with pytest.raises(ValueError, match="Resume refused"):
        store.validate_resume(
            "run-1",
            registry_digest="registry-b",
            config_digest="config-a",
            instance_digest="instance-a",
            mode="backfill",
        )
    with pytest.raises(ValueError, match="already exists"):
        store.start_run(
            "run-1",
            registry_digest="registry-a",
            config_digest="config-a",
            instance_digest="instance-a",
            mode="backfill",
        )


def test_latest_outcome_supersedes_review_in_reconciliation(tmp_path: Path):
    store = SQLiteMigrationStateStore(
        tmp_path / "migration.sqlite", allow_unverified_windows_permissions=True
    )
    store.start_run(
        "run-2",
        registry_digest="registry",
        config_digest="config",
        instance_digest="instance",
        mode="backfill",
    )
    review = ProtectedRecord(
        document_id=2,
        stable_key="provider_name",
        action=MigrationAction.REVIEW,
        result=MigrationResult.REVIEW_REQUIRED,
        reason_code=ReasonCode.VALUE_CONFLICT,
        idempotency_key="old-value",
    )
    applied = ProtectedRecord(
        document_id=2,
        stable_key="provider_name",
        action=MigrationAction.BACKFILL_VALUE,
        result=MigrationResult.APPLIED,
        reason_code=ReasonCode.READY,
        idempotency_key="corrected-value",
    )

    store.record_and_checkpoint("run-2", review, "1")
    store.record_and_checkpoint("run-2", applied, "1")

    totals, _ = store.sanitized_counts("run-2")
    assert totals == {MigrationResult.APPLIED.value: 1}
    assert (
        store.connection.execute(
            "SELECT COUNT(*) FROM migration_results WHERE run_id = 'run-2'"
        ).fetchone()[0]
        == 2
    )


def test_cli_apply_requires_single_writer_preflight(monkeypatch):
    monkeypatch.setenv("WRITE_TO_PAPERLESS", "true")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "backfill",
            "--paperless-url",
            "https://synthetic.invalid",
            "--paperless-token",
            "synthetic-token",
            "--apply",
            "--external-writers-disabled",
            "--state-db",
            "state.sqlite",
        ],
    )
    assert result.exit_code != 0
    assert "WRITE_TO_PAPERLESS" in result.output
