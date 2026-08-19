"""Registry-driven metadata inventory and bounded backfill service."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from doc_intelligence_hub.core.paperless import (
    PAPERLESS_METADATA_REGISTRY,
    MetadataCreatePolicy,
    MetadataDiagnosticCode,
    MetadataFieldKey,
    MetadataNormalization,
    PaperlessClient,
    PaperlessMetadataResolver,
    ResolvedMetadataSchema,
    build_metadata_update,
    resolve_metadata_value,
)
from doc_intelligence_hub.core.resilience import PaperlessError

from .models import (
    PROTECTED_SCHEMA_VERSION,
    FieldCompatibility,
    MigrationAction,
    MigrationResult,
    ProtectedRecord,
    ReasonCode,
    RunMode,
    SanitizedSummary,
    to_json_safe,
)
from .state import MigrationStateStore

MIGRATION_KEYS = tuple(
    spec.key for spec in PAPERLESS_METADATA_REGISTRY.values() if spec.compatibility_read
)


def registry_digest() -> str:
    material = [
        {
            "key": spec.key.value,
            "name": spec.canonical_name,
            "types": [item.value for item in spec.compatible_types],
            "aliases": list(spec.aliases),
            "normalization": spec.normalization.value,
            "create_policy": spec.create_policy.value,
            "create_type": spec.create_type.value if spec.create_type else None,
            "schema_version": spec.schema_version,
        }
        for spec in (PAPERLESS_METADATA_REGISTRY[key] for key in MIGRATION_KEYS)
    ]
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(to_json_safe(value), sort_keys=True).encode("utf-8")
    ).hexdigest()


def write_protected_artifact(
    path: str | Path,
    payload: dict[str, Any],
    *,
    require_owner_only: bool = False,
    allow_unverified_windows_permissions: bool = False,
) -> None:
    """Atomically write an explicitly requested protected JSON artifact."""
    destination = Path(path)
    if require_owner_only and os.name == "nt" and not allow_unverified_windows_permissions:
        raise PermissionError(
            "Windows ACL protection cannot be verified for the protected artifact"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    temporary.write_text(
        json.dumps(to_json_safe(payload), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        if require_owner_only:
            raise PermissionError("Unable to protect artifact temporary file") from exc
    os.replace(temporary, destination)
    if require_owner_only and os.name != "nt":
        import stat

        if stat.S_IMODE(destination.stat().st_mode) & 0o077:
            destination.unlink(missing_ok=True)
            raise PermissionError("Protected artifact must be owner-readable only")


class MetadataMigrationService:
    def __init__(self, client: PaperlessClient):
        self.client = client
        self.resolver = PaperlessMetadataResolver(client)

    async def inventory(
        self,
        *,
        batch_size: int = 100,
        protected_output: str | Path | None = None,
    ) -> SanitizedSummary:
        run_id = str(uuid4())
        summary = self._summary(run_id, RunMode.INVENTORY, batch_size)
        schema = await self.resolver.resolve(MIGRATION_KEYS)
        summary.compatibility = self._compatibility(schema)
        self._add_schema_outcomes(summary, schema)
        records: list[ProtectedRecord] = []

        async for page in self.client.iter_document_pages(page_size=batch_size):
            for document in page.results:
                for record in self._plan_document(document, schema):
                    records.append(record)
                    summary.add(record.stable_key, record.result, record.reason_code)

        summary.finish()
        if protected_output is not None:
            definitions = await self.client.list_custom_fields()
            write_protected_artifact(
                protected_output,
                {
                    "schema_version": PROTECTED_SCHEMA_VERSION,
                    "run_id": run_id,
                    "mode": RunMode.INVENTORY.value,
                    "registry_digest": summary.registry_digest,
                    "field_definitions": definitions,
                    "records": records,
                },
            )
        return summary

    async def prepare(
        self,
        *,
        apply: bool = False,
        batch_size: int = 100,
    ) -> SanitizedSummary:
        summary = self._summary(str(uuid4()), RunMode.PREPARE, batch_size)
        schema = await self.resolver.resolve(MIGRATION_KEYS)
        summary.compatibility = self._compatibility(schema)
        missing_creatable = [
            key
            for key in MIGRATION_KEYS
            if schema.field(key).canonical_id is None
            and schema.field(key).spec.create_policy is MetadataCreatePolicy.IF_MISSING
            and {diagnostic.code for diagnostic in schema.field(key).diagnostics}
            == {MetadataDiagnosticCode.MISSING_FIELD}
        ]
        for key in MIGRATION_KEYS:
            resolved = schema.field(key)
            stable_key = key.value
            if resolved.canonical_id is not None and not resolved.is_compatible:
                summary.add(
                    stable_key,
                    MigrationResult.REVIEW_REQUIRED,
                    ReasonCode.INCOMPATIBLE_SCHEMA,
                )
            elif resolved.canonical_id is not None:
                summary.add(stable_key, MigrationResult.SKIPPED, ReasonCode.CANONICAL_PRESENT)
            elif resolved.spec.create_policy is MetadataCreatePolicy.NEVER:
                summary.add(
                    stable_key,
                    MigrationResult.REVIEW_REQUIRED,
                    ReasonCode.TYPE_DECISION_REQUIRED,
                )
            elif key not in missing_creatable:
                summary.add(
                    stable_key,
                    MigrationResult.REVIEW_REQUIRED,
                    ReasonCode.INCOMPATIBLE_SCHEMA,
                )
            elif not apply:
                summary.add(stable_key, MigrationResult.PROPOSED, ReasonCode.MISSING_CANONICAL)
        if apply and missing_creatable:
            created_schema = await self.resolver.ensure(missing_creatable)
            for key in missing_creatable:
                if created_schema.field(key).is_compatible:
                    summary.add(key.value, MigrationResult.APPLIED, ReasonCode.READY)
                else:
                    summary.add(
                        key.value,
                        MigrationResult.FAILED,
                        ReasonCode.INCOMPATIBLE_SCHEMA,
                    )
        summary.finish()
        return summary

    async def backfill(
        self,
        *,
        apply: bool = False,
        batch_size: int = 100,
        max_retries: int = 2,
        state_store: MigrationStateStore | None = None,
        run_id: str | None = None,
        resume: bool = False,
    ) -> SanitizedSummary:
        if apply and state_store is None:
            raise ValueError("Apply mode requires protected migration state")
        if batch_size < 1 or max_retries < 0:
            raise ValueError("batch_size must be positive and max_retries non-negative")

        run_id = run_id or str(uuid4())
        summary = self._summary(run_id, RunMode.BACKFILL, batch_size)
        schema = await self.resolver.resolve(MIGRATION_KEYS)
        summary.compatibility = self._compatibility(schema)
        self._add_schema_outcomes(summary, schema)
        config_digest = _digest({"batch_size": batch_size, "max_retries": max_retries})
        instance_digest = _digest(self.client.base_url)
        cursor: str | None = None
        if apply and state_store is not None:
            if resume:
                cursor = state_store.validate_resume(
                    run_id,
                    registry_digest=summary.registry_digest,
                    config_digest=config_digest,
                    instance_digest=instance_digest,
                    mode=RunMode.BACKFILL.value,
                )
            else:
                state_store.start_run(
                    run_id,
                    registry_digest=summary.registry_digest,
                    config_digest=config_digest,
                    instance_digest=instance_digest,
                    mode=RunMode.BACKFILL.value,
                )

        async for page in self.client.iter_document_pages(page_size=batch_size, cursor=cursor):
            current_cursor = cursor
            for document in page.results:
                for record in self._plan_document(document, schema):
                    final = record
                    if apply and record.result is MigrationResult.PROPOSED:
                        final = await self._apply_record(
                            record,
                            schema,
                            max_retries=max_retries,
                        )
                    summary.add(final.stable_key, final.result, final.reason_code)
                    if apply and state_store is not None:
                        state_store.record_and_checkpoint(run_id, final, current_cursor)
            cursor = page.next_cursor
            if apply and state_store is not None:
                state_store.checkpoint(run_id, cursor)

        if apply and state_store is not None:
            totals, grouped = state_store.sanitized_counts(run_id)
            summary.counts = Counter(totals)
            summary.counts_by_key = {key: Counter(values) for key, values in grouped.items()}
            self._add_schema_outcomes(summary, schema)
        summary.finish()
        if apply and state_store is not None:
            state_store.finish_run(run_id, summary.completion_state.value)
        return summary

    async def _apply_record(
        self,
        record: ProtectedRecord,
        schema: ResolvedMetadataSchema,
        *,
        max_retries: int,
    ) -> ProtectedRecord:
        document = await self.client.get_document(record.document_id)
        refreshed = next(
            (
                item
                for item in self._plan_document(document, schema)
                if item.stable_key == record.stable_key
            ),
            None,
        )
        if refreshed is None or refreshed.result is MigrationResult.SKIPPED:
            return replace(
                record,
                result=MigrationResult.RECONCILED,
                reason_code=ReasonCode.ALREADY_APPLIED,
            )
        if refreshed.result is not MigrationResult.PROPOSED:
            return refreshed

        update = {
            "field": refreshed.target_field_id,
            "value": refreshed.after_value,
        }
        for attempt in range(max_retries + 1):
            try:
                await self.client.update_custom_fields_verified(
                    record.document_id,
                    [update],
                    numeric_field_ids=(
                        {int(refreshed.target_field_id)}
                        if schema.field(refreshed.stable_key).spec.normalization
                        is MetadataNormalization.NUMBER
                        and refreshed.target_field_id is not None
                        else set()
                    ),
                )
                return replace(
                    refreshed,
                    result=MigrationResult.APPLIED,
                    retry_count=attempt,
                )
            except (PaperlessError, httpx.HTTPError) as exc:
                if attempt >= max_retries:
                    return replace(
                        refreshed,
                        result=MigrationResult.FAILED,
                        reason_code=ReasonCode.WRITE_FAILED,
                        error_code=type(exc).__name__,
                        retry_eligible=True,
                        retry_count=attempt,
                    )
        raise AssertionError("retry loop exhausted without a result")

    def _plan_document(
        self, document: dict[str, Any], schema: ResolvedMetadataSchema
    ) -> list[ProtectedRecord]:
        try:
            document_id = int(document["id"])
        except (KeyError, TypeError, ValueError):
            return []
        custom_fields = document.get("custom_fields")
        if not isinstance(custom_fields, list):
            custom_fields = []
        records: list[ProtectedRecord] = []
        for key in MIGRATION_KEYS:
            resolved = schema.field(key)
            value = resolve_metadata_value(key, custom_fields, schema)
            reason = ReasonCode.READY
            result = MigrationResult.PROPOSED
            action = MigrationAction.BACKFILL_VALUE
            target_id = resolved.canonical_id

            if value.conflict is not None:
                reason = ReasonCode.VALUE_CONFLICT
                result = MigrationResult.REVIEW_REQUIRED
                action = MigrationAction.REVIEW
            elif value.validation_error:
                reason = ReasonCode.INVALID_VALUE
                result = MigrationResult.REVIEW_REQUIRED
                action = MigrationAction.REVIEW
            elif not resolved.is_compatible and resolved.canonical_id is not None:
                reason = ReasonCode.INCOMPATIBLE_SCHEMA
                result = MigrationResult.REVIEW_REQUIRED
                action = MigrationAction.REVIEW
            elif value.value is None:
                reason = ReasonCode.NO_LEGACY_VALUE
                result = MigrationResult.SKIPPED
                action = MigrationAction.NONE
            elif value.source_id == resolved.canonical_id:
                reason = ReasonCode.CANONICAL_PRESENT
                result = MigrationResult.SKIPPED
                action = MigrationAction.NONE
            elif resolved.canonical_id is None:
                reason = (
                    ReasonCode.TYPE_DECISION_REQUIRED
                    if key is MetadataFieldKey.NORMALIZED_DOCUMENT_TYPE
                    else ReasonCode.MISSING_CANONICAL
                )
                result = MigrationResult.REVIEW_REQUIRED
                action = MigrationAction.REVIEW
            elif not resolved.is_compatible:
                reason = ReasonCode.INCOMPATIBLE_SCHEMA
                result = MigrationResult.REVIEW_REQUIRED
                action = MigrationAction.REVIEW

            after_value = value.value
            if result is MigrationResult.PROPOSED:
                try:
                    update = build_metadata_update(key, value.value, schema)
                    target_id = int(update["field"])
                    after_value = update["value"]
                except (ValueError, RuntimeError):
                    reason = ReasonCode.INVALID_VALUE
                    result = MigrationResult.REVIEW_REQUIRED
                    action = MigrationAction.REVIEW

            idempotency_key = _digest(
                {
                    "registry": registry_digest(),
                    "document_id": document_id,
                    "stable_key": key.value,
                    "target_field_id": target_id,
                    "normalized_value": value.value,
                }
            )
            records.append(
                ProtectedRecord(
                    document_id=document_id,
                    stable_key=key.value,
                    action=action,
                    result=result,
                    reason_code=reason,
                    idempotency_key=idempotency_key,
                    source_field_id=value.source_id,
                    target_field_id=target_id,
                    before_value=value.value,
                    after_value=after_value,
                )
            )
        return records

    def _compatibility(self, schema: ResolvedMetadataSchema) -> list[FieldCompatibility]:
        compatibility: list[FieldCompatibility] = []
        for key in MIGRATION_KEYS:
            resolved = schema.field(key)
            if resolved.canonical_id is not None and resolved.is_compatible:
                action = MigrationAction.NONE
                reason = ReasonCode.CANONICAL_PRESENT
            elif resolved.canonical_id is None and (
                resolved.spec.create_policy is MetadataCreatePolicy.NEVER
            ):
                action = MigrationAction.REVIEW
                reason = ReasonCode.TYPE_DECISION_REQUIRED
            elif resolved.canonical_id is None:
                action = MigrationAction.CREATE_FIELD
                reason = ReasonCode.MISSING_CANONICAL
            else:
                action = MigrationAction.REVIEW
                reason = ReasonCode.INCOMPATIBLE_SCHEMA
            compatibility.append(
                FieldCompatibility(
                    stable_key=key.value,
                    canonical_present=resolved.canonical_id is not None,
                    expected_types=tuple(item.value for item in resolved.spec.compatible_types),
                    observed_type=resolved.data_type.value if resolved.data_type else None,
                    alias_count=len(resolved.alias_ids),
                    diagnostic_codes=tuple(
                        diagnostic.code.value for diagnostic in resolved.diagnostics
                    ),
                    proposed_action=action,
                    reason_code=reason,
                )
            )
        return compatibility

    @staticmethod
    def _add_schema_outcomes(summary: SanitizedSummary, schema: ResolvedMetadataSchema) -> None:
        for key in MIGRATION_KEYS:
            resolved = schema.field(key)
            if resolved.canonical_id is not None and not resolved.is_compatible:
                summary.add(
                    key.value,
                    MigrationResult.REVIEW_REQUIRED,
                    ReasonCode.INCOMPATIBLE_SCHEMA,
                )
            elif resolved.canonical_id is None:
                diagnostic_codes = {diagnostic.code for diagnostic in resolved.diagnostics}
                if diagnostic_codes != {MetadataDiagnosticCode.MISSING_FIELD}:
                    summary.add(
                        key.value,
                        MigrationResult.REVIEW_REQUIRED,
                        ReasonCode.INCOMPATIBLE_SCHEMA,
                    )
                elif resolved.spec.create_policy is MetadataCreatePolicy.NEVER:
                    summary.add(
                        key.value,
                        MigrationResult.REVIEW_REQUIRED,
                        ReasonCode.TYPE_DECISION_REQUIRED,
                    )
                else:
                    summary.add(
                        key.value,
                        MigrationResult.PROPOSED,
                        ReasonCode.MISSING_CANONICAL,
                    )

    @staticmethod
    def _summary(run_id: str, mode: RunMode, batch_size: int) -> SanitizedSummary:
        return SanitizedSummary(
            registry_digest=registry_digest(),
            run_id=run_id,
            mode=mode,
            batch_size=batch_size,
            started_at=datetime.now(UTC).isoformat(),
        )
