"""Registry-driven metadata inventory and bounded backfill service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

import httpx

from doc_intelligence_hub.core.paperless import (
    PAPERLESS_METADATA_REGISTRY,
    MetadataCreatePolicy,
    MetadataDiagnosticCode,
    MetadataFieldKey,
    MetadataNormalization,
    MetadataRetirementPolicy,
    PaperlessClient,
    PaperlessMetadataResolver,
    ResolvedMetadataSchema,
    build_metadata_update,
    resolve_metadata_schema,
    resolve_metadata_value,
)
from doc_intelligence_hub.core.resilience import PaperlessError

from .models import (
    PROTECTED_SCHEMA_VERSION,
    FieldCompatibility,
    FieldDisposition,
    FieldReview,
    FieldReviewState,
    FieldReviewSummary,
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
            "retirement_policy": spec.retirement_policy.value,
            "schema_version": spec.schema_version,
        }
        for spec in (PAPERLESS_METADATA_REGISTRY[key] for key in MIGRATION_KEYS)
    ]
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(to_json_safe(value), sort_keys=True).encode("utf-8")
    ).hexdigest()


def write_protected_artifact(path: str | Path, payload: dict[str, Any]) -> None:
    """Atomically write an explicitly requested protected JSON artifact."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    temporary.write_text(
        json.dumps(to_json_safe(payload), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, destination)


def _field_id(definition: dict[str, Any]) -> int | None:
    value = definition.get("id", definition.get("field"))
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _has_custom_field_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def _payload_references_field(
    payload: Any,
    *,
    field_id: int,
    field_name: str,
    custom_field_context: bool = False,
) -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_context = custom_field_context or "custom_field" in str(key).lower()
            if _payload_references_field(
                value,
                field_id=field_id,
                field_name=field_name,
                custom_field_context=key_context,
            ):
                return True
        return False
    if isinstance(payload, list):
        return any(
            _payload_references_field(
                item,
                field_id=field_id,
                field_name=field_name,
                custom_field_context=custom_field_context,
            )
            for item in payload
        )
    if isinstance(payload, str):
        stripped = payload.strip()
        if stripped == field_name:
            return True
        if custom_field_context and stripped == str(field_id):
            return True
        if stripped.startswith(("[", "{")):
            try:
                decoded = json.loads(stripped)
            except ValueError:
                return False
            return _payload_references_field(
                decoded,
                field_id=field_id,
                field_name=field_name,
                custom_field_context=custom_field_context,
            )
        return False
    return custom_field_context and payload == field_id


class MetadataMigrationService:
    def __init__(
        self,
        client: PaperlessClient,
        *,
        owl_document_coverage: Mapping[MetadataFieldKey, set[int]] | None = None,
    ):
        self.client = client
        self.resolver = PaperlessMetadataResolver(client)
        self.owl_document_coverage = owl_document_coverage or {}

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

    async def review_fields(
        self,
        *,
        apply: bool = False,
        discard_retired_values: bool = False,
        batch_size: int = 100,
        protected_output: str | Path | None = None,
    ) -> FieldReviewSummary:
        """Review every registered/deployed field and optionally retire safe empty fields."""
        if batch_size < 1:
            raise ValueError("batch_size must be positive")

        run_id = str(uuid4())
        started_at = datetime.now(UTC).isoformat()
        definitions, saved_views, document_types, workflows = await asyncio.gather(
            self.client.list_custom_fields(),
            self.client.list_saved_views(),
            self.client.list_document_types(),
            self.client.list_workflows(),
        )
        definitions_by_name: dict[str, list[dict[str, Any]]] = {}
        non_empty_documents: Counter[int] = Counter()
        populated_document_ids: dict[int, list[int]] = defaultdict(list)
        for definition in definitions:
            name = definition.get("name")
            if isinstance(name, str) and name.strip():
                definitions_by_name.setdefault(name.strip(), []).append(definition)

        async for page in self.client.iter_document_pages(page_size=batch_size):
            for document in page.results:
                try:
                    document_id = int(document["id"])
                except (KeyError, TypeError, ValueError):
                    continue
                seen: set[int] = set()
                for custom_field in document.get("custom_fields") or []:
                    if not isinstance(custom_field, dict):
                        continue
                    field_id = _field_id(custom_field)
                    if (
                        field_id is not None
                        and field_id not in seen
                        and _has_custom_field_value(custom_field.get("value"))
                    ):
                        non_empty_documents[field_id] += 1
                        populated_document_ids[field_id].append(document_id)
                        seen.add(field_id)

        known_names = {
            name
            for spec in PAPERLESS_METADATA_REGISTRY.values()
            for name in (spec.canonical_name, *spec.aliases)
        }
        unmanaged = [
            definition
            for definition in definitions
            if str(definition.get("name", "")).strip() not in known_names
        ]
        reviews: list[FieldReview] = []
        protected_fields: list[dict[str, Any]] = []

        for spec in PAPERLESS_METADATA_REGISTRY.values():
            resolved = resolve_metadata_schema(definitions, (spec.key,)).field(spec.key)
            canonical_diagnostics = tuple(
                diagnostic.code.value
                for diagnostic in resolved.diagnostics
                if diagnostic.field_name == spec.canonical_name
            )
            canonical_disposition = (
                FieldDisposition.RETIRE_WHEN_EMPTY
                if spec.retirement_policy is MetadataRetirementPolicy.RETIRE_WHEN_EMPTY
                else FieldDisposition.KEEP
            )
            canonical_reviews, canonical_details = self._review_named_field(
                stable_key=spec.key.value,
                field_name=spec.canonical_name,
                role="canonical",
                expected_types=tuple(item.value for item in spec.compatible_types),
                disposition=canonical_disposition,
                definitions=definitions_by_name.get(spec.canonical_name, []),
                non_empty_documents=non_empty_documents,
                populated_document_ids=populated_document_ids,
                owl_document_ids=self.owl_document_coverage.get(spec.key, set()),
                saved_views=saved_views,
                document_types=document_types,
                workflows=workflows,
                schema_diagnostics=canonical_diagnostics,
            )
            reviews.extend(canonical_reviews)
            protected_fields.extend(canonical_details)

            for alias in spec.aliases:
                alias_reviews, alias_details = self._review_named_field(
                    stable_key=f"{spec.key.value}:alias:{alias}",
                    field_name=alias,
                    role="alias",
                    expected_types=tuple(item.value for item in spec.compatible_types),
                    disposition=FieldDisposition.MIGRATE_THEN_RETIRE,
                    definitions=definitions_by_name.get(alias, []),
                    non_empty_documents=non_empty_documents,
                    populated_document_ids=populated_document_ids,
                    owl_document_ids=set(),
                    saved_views=saved_views,
                    document_types=document_types,
                    workflows=workflows,
                    include_absent=False,
                )
                reviews.extend(alias_reviews)
                protected_fields.extend(alias_details)

        if apply:
            updated_reviews: list[FieldReview] = []
            deleted_ids: set[int] = set()
            cleared_document_values = 0
            for review, detail in zip(reviews, protected_fields, strict=True):
                field_id = detail.get("field_id")
                should_clear_retired_values = (
                    review.state is FieldReviewState.BLOCKED_POPULATED
                    and review.disposition is FieldDisposition.RETIRE_WHEN_EMPTY
                    and isinstance(field_id, int)
                )
                if should_clear_retired_values:
                    covered_document_ids = detail.get("owl_covered_document_ids")
                    uncovered_document_ids = detail.get("uncovered_document_ids")
                    if not isinstance(covered_document_ids, list) or not isinstance(
                        uncovered_document_ids, list
                    ):
                        raise PaperlessError(
                            f"Protected retirement plan is missing documents for {review.field_name}"
                        )
                    document_ids = [
                        *covered_document_ids,
                        *(uncovered_document_ids if discard_retired_values else []),
                    ]
                    for document_id in document_ids:
                        await self.client.update_custom_fields_verified(
                            int(document_id),
                            [{"field": field_id, "value": None}],
                        )
                    cleared_document_values += len(document_ids)
                    detail["cleared_document_values"] = len(document_ids)
                    remaining = len(uncovered_document_ids) if not discard_retired_values else 0
                    next_state = (
                        FieldReviewState.ELIGIBLE
                        if remaining == 0
                        else FieldReviewState.BLOCKED_POPULATED
                    )
                    detail["state"] = next_state.value
                    review = replace(
                        review,
                        state=next_state,
                        non_empty_documents=remaining,
                        owl_covered_documents=0,
                        uncovered_documents=remaining,
                    )
                if review.state is FieldReviewState.ELIGIBLE and isinstance(field_id, int):
                    await self.client.delete_custom_field(field_id)
                    deleted_ids.add(field_id)
                    review = replace(review, state=FieldReviewState.DELETED)
                    detail["state"] = FieldReviewState.DELETED.value
                updated_reviews.append(review)
            reviews = updated_reviews
            remaining_ids = {
                field_id
                for definition in await self.client.list_custom_fields()
                if (field_id := _field_id(definition)) is not None
            }
            if deleted_ids & remaining_ids:
                raise PaperlessError("Paperless custom-field deletion verification failed")
        else:
            cleared_document_values = 0

        summary = FieldReviewSummary(
            run_id=run_id,
            mode=(
                "retire_discard"
                if apply and discard_retired_values
                else "retire"
                if apply
                else "review"
            ),
            started_at=started_at,
            fields=reviews,
            unmanaged_fields=len(unmanaged),
            apply=apply,
            cleared_document_values=cleared_document_values,
        )
        summary.finish()
        if protected_output is not None:
            write_protected_artifact(
                protected_output,
                {
                    "schema_version": "1.0",
                    "run_id": run_id,
                    "mode": summary.mode,
                    "registry_digest": registry_digest(),
                    "fields": protected_fields,
                    "unmanaged_fields": unmanaged,
                },
            )
        return summary

    @staticmethod
    def _review_named_field(
        *,
        stable_key: str,
        field_name: str,
        role: str,
        expected_types: tuple[str, ...],
        disposition: FieldDisposition,
        definitions: list[dict[str, Any]],
        non_empty_documents: Counter[int],
        populated_document_ids: dict[int, list[int]],
        owl_document_ids: set[int],
        saved_views: list[dict],
        document_types: list[dict],
        workflows: list[dict],
        schema_diagnostics: tuple[str, ...] = (),
        include_absent: bool = True,
    ) -> tuple[list[FieldReview], list[dict[str, Any]]]:
        if not definitions:
            if not include_absent:
                return [], []
            return [
                FieldReview(
                    stable_key=stable_key,
                    field_name=field_name,
                    role=role,
                    disposition=disposition,
                    state=FieldReviewState.ABSENT,
                    deployed_count=0,
                    expected_types=expected_types,
                    observed_types=(),
                    non_empty_documents=0,
                    owl_covered_documents=0,
                    uncovered_documents=0,
                    saved_view_references=0,
                    document_type_references=0,
                    workflow_references=0,
                )
            ], [{"stable_key": stable_key, "field_name": field_name, "field_id": None}]

        observed_types = tuple(
            sorted(
                {
                    str(definition.get("data_type"))
                    for definition in definitions
                    if definition.get("data_type") is not None
                }
            )
        )
        duplicate = len(definitions) > 1
        reviews: list[FieldReview] = []
        details: list[dict[str, Any]] = []
        for definition in definitions:
            field_id = _field_id(definition)
            populated = non_empty_documents[field_id] if field_id is not None else 0
            populated_ids = set(populated_document_ids.get(field_id, ()))
            covered_ids = populated_ids & owl_document_ids
            uncovered_ids = populated_ids - covered_ids
            saved_view_refs = (
                sum(
                    _payload_references_field(
                        item,
                        field_id=field_id,
                        field_name=field_name,
                    )
                    for item in saved_views
                )
                if field_id is not None
                else 0
            )
            document_type_refs = (
                sum(
                    _payload_references_field(
                        item,
                        field_id=field_id,
                        field_name=field_name,
                    )
                    for item in document_types
                )
                if field_id is not None
                else 0
            )
            workflow_refs = (
                sum(
                    _payload_references_field(
                        item,
                        field_id=field_id,
                        field_name=field_name,
                    )
                    for item in workflows
                )
                if field_id is not None
                else 0
            )
            actual_type = str(definition.get("data_type"))
            diagnostic_codes: tuple[str, ...] = ()
            review_disposition = disposition
            if duplicate:
                state = FieldReviewState.DUPLICATE
                review_disposition = FieldDisposition.REVIEW_REQUIRED
                diagnostic_codes = (MetadataDiagnosticCode.DUPLICATE_FIELD.value,)
            elif field_id is None or actual_type not in expected_types:
                state = FieldReviewState.INCOMPATIBLE
                review_disposition = FieldDisposition.REVIEW_REQUIRED
                diagnostic_codes = (MetadataDiagnosticCode.INCOMPATIBLE_TYPE.value,)
            elif disposition is FieldDisposition.KEEP and any(
                code
                in {
                    MetadataDiagnosticCode.MISSING_SELECT_OPTION.value,
                    MetadataDiagnosticCode.INVALID_SELECT_OPTION.value,
                }
                for code in schema_diagnostics
            ):
                state = FieldReviewState.INCOMPATIBLE
                review_disposition = FieldDisposition.REVIEW_REQUIRED
                diagnostic_codes = schema_diagnostics
            elif disposition is FieldDisposition.KEEP:
                state = FieldReviewState.RETAINED
            elif saved_view_refs or document_type_refs or workflow_refs:
                state = FieldReviewState.BLOCKED_DEPENDENCY
            elif populated:
                state = FieldReviewState.BLOCKED_POPULATED
            else:
                state = FieldReviewState.ELIGIBLE

            reviews.append(
                FieldReview(
                    stable_key=stable_key,
                    field_name=field_name,
                    role=role,
                    disposition=review_disposition,
                    state=state,
                    deployed_count=len(definitions),
                    expected_types=expected_types,
                    observed_types=observed_types,
                    non_empty_documents=populated,
                    owl_covered_documents=len(covered_ids),
                    uncovered_documents=len(uncovered_ids),
                    saved_view_references=saved_view_refs,
                    document_type_references=document_type_refs,
                    workflow_references=workflow_refs,
                    diagnostic_codes=diagnostic_codes,
                )
            )
            details.append(
                {
                    "stable_key": stable_key,
                    "field_name": field_name,
                    "field_id": field_id,
                    "role": role,
                    "disposition": review_disposition.value,
                    "state": state.value,
                    "non_empty_documents": populated,
                    "populated_document_ids": sorted(populated_ids),
                    "owl_covered_document_ids": sorted(covered_ids),
                    "uncovered_document_ids": sorted(uncovered_ids),
                    "saved_view_references": saved_view_refs,
                    "document_type_references": document_type_refs,
                    "workflow_references": workflow_refs,
                }
            )
        return reviews, details

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
                reason = ReasonCode.MISSING_CANONICAL
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
