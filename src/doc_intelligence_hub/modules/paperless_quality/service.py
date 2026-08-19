"""Plan and reconcile Paperless quality views with protected mutation state."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from doc_intelligence_hub.core.paperless import (
    MetadataFieldKey,
    PaperlessClient,
    PaperlessMetadataResolver,
    resolve_metadata_value,
)
from doc_intelligence_hub.modules.metadata_migration.models import (
    MigrationAction,
    MigrationResult,
    ProtectedRecord,
    ReasonCode,
    to_json_safe,
)
from doc_intelligence_hub.modules.metadata_migration.service import write_protected_artifact
from doc_intelligence_hub.modules.metadata_migration.state import MigrationStateStore

from .config import QualityConfig
from .models import FilterRule, ManualCandidate, ProtectedQualityPlan, QualitySummary, ViewPlan
from .registry import QUALITY_VIEW_REGISTRY, QualityViewKey

NULL_VALUE = None


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(to_json_safe(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _rule(rule_type: int, value: object) -> FilterRule:
    if value is None:
        rendered = None
    elif isinstance(value, bool):
        rendered = str(value).lower()
    else:
        rendered = str(value)
    return FilterRule(rule_type, rendered)


def _query_params(rules: tuple[FilterRule, ...]) -> dict[str, Any]:
    mappings = {
        3: ("correspondent__id", "correspondent__isnull", False),
        4: ("document_type__id", "document_type__isnull", False),
        5: ("is_in_inbox", None, False),
        7: ("is_tagged", None, False),
        14: ("added__date__gt", None, False),
        17: ("tags__id__none", None, True),
        20: ("query", None, False),
        26: ("correspondent__id__in", None, True),
        42: ("custom_field_query", None, False),
        25: ("storage_path__id", "storage_path__isnull", False),
    }
    params: dict[str, Any] = {}
    for rule in rules:
        filter_name, null_name, multi = mappings[rule.rule_type]
        if rule.value is None:
            if null_name is None:
                raise ValueError(f"Rule type {rule.rule_type} does not accept null")
            params[null_name] = 1
        elif multi and filter_name in params:
            params[filter_name] = f"{params[filter_name]},{rule.value}"
        elif rule.rule_type in {5, 7}:
            params[filter_name] = 1 if rule.value == "true" else 0
        else:
            params[filter_name] = rule.value
    return params


def _view_digest(view: dict[str, Any]) -> str:
    return _digest(
        {
            "id": view.get("id"),
            "name": view.get("name"),
            "owner": _owner_id(view.get("owner")),
            "filter_rules": _normalized_rules(view.get("filter_rules")),
            "sort_field": view.get("sort_field"),
            "sort_reverse": view.get("sort_reverse"),
        }
    )


def _owner_id(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _normalized_rules(rules: object) -> list[dict[str, Any]]:
    if not isinstance(rules, list):
        return []
    normalized = [
        {"rule_type": item.get("rule_type"), "value": item.get("value")}
        for item in rules
        if isinstance(item, dict)
    ]
    return sorted(normalized, key=lambda item: (str(item["rule_type"]), str(item["value"])))


def _plan_digest(plan: ProtectedQualityPlan) -> str:
    return _digest(
        {
            "schema_version": plan.schema_version,
            "planned_at": plan.planned_at,
            "config_digest": plan.config_digest,
            "instance_digest": plan.instance_digest,
            "views": plan.views,
            "manual_candidates": plan.manual_candidates,
        }
    )


class PaperlessQualityService:
    def __init__(
        self,
        client: PaperlessClient,
        config: QualityConfig,
        *,
        allow_unverified_windows_permissions: bool = False,
    ):
        self.client = client
        self.config = config
        self.allow_unverified_windows_permissions = allow_unverified_windows_permissions

    def _validate_origin(self) -> None:
        if self.client.base_url.rstrip("/") != self.config.expected_origin:
            raise ValueError("Paperless origin does not match the protected quality manifest")

    async def _view_rules(self) -> dict[QualityViewKey, tuple[FilterRule, ...]]:
        account_schema = await PaperlessMetadataResolver(self.client).resolve(
            [MetadataFieldKey.ACCOUNT_IDENTIFIER]
        )
        account = account_schema.field(MetadataFieldKey.ACCOUNT_IDENTIFIER)
        if (
            account.canonical_id != self.config.account_identifier.canonical_field_id
            or account.data_type is None
            or account.data_type.value != self.config.account_identifier.canonical_data_type
            or set(account.alias_ids.values())
            != set(self.config.account_identifier.legacy_field_ids)
        ):
            raise ValueError("Account Identifier field bindings changed since protected inventory")

        account_query = json.dumps(
            [
                "OR",
                [
                    [account.canonical_id, "exists", False],
                    [account.canonical_id, "isnull", True],
                    *[[alias_id, "exists", True] for alias_id in account.alias_ids.values()],
                ],
            ],
            separators=(",", ":"),
        )
        return {
            QualityViewKey.INBOX: (_rule(5, True),),
            QualityViewKey.MISSING_CORRESPONDENT: (_rule(3, NULL_VALUE),),
            QualityViewKey.MISSING_DOCUMENT_TYPE: (_rule(4, NULL_VALUE),),
            QualityViewKey.NO_TAGS: (_rule(7, False),),
            QualityViewKey.RECORD: (_rule(4, self.config.document_types.record),),
            QualityViewKey.OTHER: (_rule(4, self.config.document_types.other),),
            QualityViewKey.MANUAL_MISSING_STORAGE_PATH: (
                _rule(4, self.config.document_types.manual),
                _rule(25, NULL_VALUE),
            ),
            QualityViewKey.EOB_MISSING_HOUSEHOLD_MEMBER: (
                _rule(4, self.config.document_types.eob),
                *(_rule(17, tag_id) for tag_id in self.config.household_member_tag_ids),
            ),
            QualityViewKey.ACCOUNT_IDENTIFIER_MISSING_OR_CONFLICTING: (_rule(42, account_query),),
            QualityViewKey.DUPLICATE_CORRESPONDENT_CANDIDATES: tuple(
                _rule(26, item) for item in self.config.duplicate_correspondent_ids
            ),
            QualityViewKey.RECENTLY_ADDED_AWAITING_REVIEW: (
                _rule(20, f"added:[-{self.config.recent_window_days} day to now]"),
                *(_rule(17, tag_id) for tag_id in self.config.review_complete_tag_ids),
            ),
        }

    async def plan(self, *, protected_output: str | Path) -> QualitySummary:
        """Perform GET-only inventory and write the locked detailed plan."""
        self._validate_origin()
        planned_at_dt = datetime.now(UTC)
        planned_at = planned_at_dt.isoformat()
        existing_views = await self.client.list_saved_views()
        by_name: dict[str, list[dict]] = {}
        for view in existing_views:
            by_name.setdefault(str(view.get("name", "")), []).append(view)
        rules_by_key = await self._view_rules()

        view_plans: list[ViewPlan] = []
        for key, definition in QUALITY_VIEW_REGISTRY.items():
            rules = rules_by_key[key]
            matches = by_name.get(definition.name, [])
            existing_id = int(matches[0]["id"]) if len(matches) == 1 else None
            desired_rules = [to_json_safe(item) for item in rules]
            if len(matches) > 1:
                action, reason = "review", "duplicate_managed_view"
            elif not matches:
                action, reason = "create", "missing_view"
            elif (
                _normalized_rules(matches[0].get("filter_rules"))
                == _normalized_rules(desired_rules)
                and int(matches[0].get("owner") or 0) == self.config.owner_id
                and matches[0].get("sort_field") == "added"
                and matches[0].get("sort_reverse") is True
            ):
                action, reason = "none", "already_current"
            else:
                action, reason = "update", "definition_drift"
            if key is QualityViewKey.DUPLICATE_CORRESPONDENT_CANDIDATES and not rules:
                action, reason = "review", "candidate_inventory_required"
            observed = await self.client.count_documents(_query_params(rules)) if rules else 0
            exact = observed
            view_plans.append(
                ViewPlan(
                    stable_key=key.value,
                    name=definition.name,
                    rules=rules,
                    expected_count=self.config.expected_counts.get(key.value),
                    observed_count=observed,
                    exact_count=exact,
                    existing_view_id=existing_id,
                    existing_digest=_view_digest(matches[0]) if len(matches) == 1 else None,
                    action=action,
                    reason_code=reason,
                )
            )

        manual_documents = await self._documents_for_rules(
            rules_by_key[QualityViewKey.MANUAL_MISSING_STORAGE_PATH]
        )
        manual_candidates = [
            ManualCandidate(
                document_id=int(document["id"]),
                expected_modified=str(document.get("modified") or ""),
                expected_document_type=int(document["document_type"]),
            )
            for document in manual_documents
        ]
        account_exact = await self._account_exact_count()
        view_plans = [
            replace(item, exact_count=account_exact)
            if item.stable_key == QualityViewKey.ACCOUNT_IDENTIFIER_MISSING_OR_CONFLICTING.value
            else item
            for item in view_plans
        ]
        config_digest = _digest(self.config.model_dump())
        instance_digest = _digest(self.client.base_url)
        protected_plan = ProtectedQualityPlan(
            plan_digest="",
            config_digest=config_digest,
            instance_digest=instance_digest,
            planned_at=planned_at,
            views=view_plans,
            manual_candidates=manual_candidates,
        )
        plan_digest = _plan_digest(protected_plan)
        protected_plan.plan_digest = plan_digest
        write_protected_artifact(
            protected_output,
            to_json_safe(protected_plan),
            require_owner_only=True,
            allow_unverified_windows_permissions=self.allow_unverified_windows_permissions,
        )

        counts = Counter(item.action for item in view_plans)
        mismatches = sum(
            item.expected_count is not None and item.expected_count != item.exact_count
            for item in view_plans
        )
        if mismatches:
            counts["expected_count_mismatch"] = mismatches
        return QualitySummary(
            plan_digest=plan_digest,
            planned_at=planned_at,
            views=[
                {
                    "stable_key": item.stable_key,
                    "expected_count": item.expected_count,
                    "observed_count": item.observed_count,
                    "exact_count": item.exact_count,
                    "action": item.action,
                    "reason_code": item.reason_code,
                }
                for item in view_plans
            ],
            completion_state="review_required" if mismatches or counts["review"] else "planned",
            counts=dict(counts),
        )

    async def apply_views(
        self,
        plan: ProtectedQualityPlan,
        *,
        approval: str,
        state_store: MigrationStateStore,
    ) -> QualitySummary:
        """Reconcile only managed saved views bound to an exact locked plan."""
        self._validate_locked_plan(plan, approval, "saved-views")
        run_id = f"quality-views-{plan.plan_digest[:12]}-{uuid4().hex[:12]}"
        state_store.start_run(
            run_id,
            registry_digest=_digest(tuple(QUALITY_VIEW_REGISTRY)),
            config_digest=plan.config_digest,
            instance_digest=plan.instance_digest,
            mode="quality_saved_views",
        )
        counts: Counter[str] = Counter()
        for index, item in enumerate(plan.views, start=1):
            if item.action == "review":
                result, reason = MigrationResult.REVIEW_REQUIRED, ReasonCode.INCOMPATIBLE_SCHEMA
            else:
                definition = {
                    "name": item.name,
                    "owner": self.config.owner_id,
                    "filter_rules": [to_json_safe(rule) for rule in item.rules],
                    "sort_field": "added",
                    "sort_reverse": True,
                }
                current = await self.client.list_saved_views()
                matches = [view for view in current if view.get("name") == item.name]
                if len(matches) > 1:
                    result, reason = (
                        MigrationResult.REVIEW_REQUIRED,
                        ReasonCode.INCOMPATIBLE_SCHEMA,
                    )
                elif matches and self._view_matches(matches[0], definition):
                    result, reason = (
                        MigrationResult.SKIPPED
                        if item.action == "none"
                        else MigrationResult.RECONCILED,
                        ReasonCode.ALREADY_APPLIED,
                    )
                elif matches and _view_digest(matches[0]) != item.existing_digest:
                    result, reason = MigrationResult.REVIEW_REQUIRED, ReasonCode.VALUE_CONFLICT
                else:
                    if matches and item.existing_view_id == int(matches[0]["id"]):
                        updated = await self.client.update_saved_view(
                            int(matches[0]["id"]), definition
                        )
                    elif not matches and item.existing_view_id is None:
                        updated = await self.client.create_saved_view(definition)
                    else:
                        result, reason = (
                            MigrationResult.REVIEW_REQUIRED,
                            ReasonCode.VALUE_CONFLICT,
                        )
                        updated = None
                    if updated is None:
                        record = ProtectedRecord(
                            document_id=item.existing_view_id or -index,
                            stable_key=item.stable_key,
                            action=MigrationAction.REVIEW,
                            result=result,
                            reason_code=reason,
                            idempotency_key=_digest(
                                {"plan_digest": plan.plan_digest, "stable_key": item.stable_key}
                            ),
                        )
                        state_store.record_and_checkpoint(run_id, record, str(index + 1))
                        counts[result.value] += 1
                        continue
                    self._verify_view(updated, definition)
                    result, reason = MigrationResult.APPLIED, ReasonCode.READY
            record = ProtectedRecord(
                document_id=item.existing_view_id or -index,
                stable_key=item.stable_key,
                action=(
                    MigrationAction.REVIEW
                    if result is MigrationResult.REVIEW_REQUIRED
                    else MigrationAction.NONE
                    if result
                    in {
                        MigrationResult.SKIPPED,
                        MigrationResult.RECONCILED,
                    }
                    else MigrationAction.CREATE_SAVED_VIEW
                    if item.existing_view_id is None
                    else MigrationAction.UPDATE_SAVED_VIEW
                ),
                result=result,
                reason_code=reason,
                idempotency_key=_digest(
                    {"plan_digest": plan.plan_digest, "stable_key": item.stable_key}
                ),
            )
            state_store.record_and_checkpoint(run_id, record, str(index + 1))
            counts[result.value] += 1
        completion = (
            "review_required" if counts[MigrationResult.REVIEW_REQUIRED.value] else "completed"
        )
        state_store.finish_run(run_id, completion)
        return self._apply_summary(plan, counts, completion)

    async def apply_manual_storage_path(
        self,
        plan: ProtectedQualityPlan,
        *,
        approval: str,
        state_store: MigrationStateStore,
        batch_size: int,
    ) -> QualitySummary:
        """Apply bounded Manual storage-path corrections with optimistic checks."""
        self._validate_locked_plan(plan, approval, "manual-storage-path")
        run_id = f"quality-manual-{plan.plan_digest[:12]}-{uuid4().hex[:12]}"
        state_store.start_run(
            run_id,
            registry_digest=_digest(tuple(QUALITY_VIEW_REGISTRY)),
            config_digest=plan.config_digest,
            instance_digest=plan.instance_digest,
            mode="manual_storage_path",
        )
        counts: Counter[str] = Counter()
        attempted = 0
        reconciled = 0
        for index, candidate in enumerate(plan.manual_candidates, start=1):
            current = await self.client.get_document(candidate.document_id)
            if int(current.get("storage_path") or 0) == self.config.storage_paths.manual:
                result, reason = MigrationResult.RECONCILED, ReasonCode.ALREADY_APPLIED
                reconciled += 1
            elif attempted >= batch_size:
                break
            elif (
                str(current.get("modified") or "") != candidate.expected_modified
                or int(current.get("document_type") or 0) != candidate.expected_document_type
                or current.get("storage_path") is not None
            ):
                attempted += 1
                result, reason = MigrationResult.REVIEW_REQUIRED, ReasonCode.VALUE_CONFLICT
            else:
                attempted += 1
                updated = await self.client.update_document(
                    candidate.document_id,
                    {"storage_path": self.config.storage_paths.manual},
                )
                verified = (
                    updated
                    if int(updated.get("storage_path") or 0) == self.config.storage_paths.manual
                    else await self.client.get_document(candidate.document_id)
                )
                if int(verified.get("storage_path") or 0) != self.config.storage_paths.manual:
                    result, reason = MigrationResult.FAILED, ReasonCode.VERIFY_FAILED
                else:
                    result, reason = MigrationResult.APPLIED, ReasonCode.READY
            record = ProtectedRecord(
                document_id=candidate.document_id,
                stable_key=QualityViewKey.MANUAL_MISSING_STORAGE_PATH.value,
                action=(
                    MigrationAction.NONE
                    if result is MigrationResult.RECONCILED
                    else MigrationAction.REVIEW
                    if result is MigrationResult.REVIEW_REQUIRED
                    else MigrationAction.SET_STORAGE_PATH
                ),
                result=result,
                reason_code=reason,
                idempotency_key=_digest(
                    {"plan_digest": plan.plan_digest, "document_id": candidate.document_id}
                ),
                target_field_id=self.config.storage_paths.manual,
            )
            state_store.record_and_checkpoint(run_id, record, str(index + 1))
            counts[result.value] += 1
        remaining = max(len(plan.manual_candidates) - reconciled - attempted, 0)
        if remaining:
            counts["remaining"] = remaining
        if counts[MigrationResult.FAILED.value]:
            completion = "failed"
        elif counts[MigrationResult.REVIEW_REQUIRED.value]:
            completion = "review_required"
        elif remaining:
            completion = "partial"
        else:
            completion = "completed"
        state_store.finish_run(run_id, completion)
        return self._apply_summary(plan, counts, completion)

    async def _documents_for_rules(self, rules: tuple[FilterRule, ...]) -> list[dict]:
        return await self.client.list_documents_filtered(_query_params(rules))

    async def _account_exact_count(self) -> int:
        schema = await PaperlessMetadataResolver(self.client).resolve(
            [MetadataFieldKey.ACCOUNT_IDENTIFIER]
        )
        count = 0
        async for page in self.client.iter_document_pages(page_size=100):
            for document in page.results:
                fields = document.get("custom_fields")
                value = resolve_metadata_value(
                    MetadataFieldKey.ACCOUNT_IDENTIFIER,
                    fields if isinstance(fields, list) else [],
                    schema,
                )
                if (
                    value.value is None
                    or value.source_id
                    != schema.field(MetadataFieldKey.ACCOUNT_IDENTIFIER).canonical_id
                    or value.conflict is not None
                    or value.validation_error
                ):
                    count += 1
        return count

    def _validate_locked_plan(
        self, plan: ProtectedQualityPlan, approval: str, operation: str
    ) -> None:
        self._validate_origin()
        if plan.config_digest != _digest(self.config.model_dump()):
            raise ValueError("Protected manifest changed since planning")
        if plan.instance_digest != _digest(self.client.base_url):
            raise ValueError("Paperless instance changed since planning")
        if plan.plan_digest != _plan_digest(replace(plan, plan_digest="")):
            raise ValueError("Protected plan content does not match its digest")
        if any(
            item.expected_count is not None and item.expected_count != item.exact_count
            for item in plan.views
        ):
            raise ValueError("Apply refused: an expected-count tripwire does not match")
        if approval != f"{operation}:{plan.plan_digest}":
            raise ValueError("Approval must exactly bind the operation and plan digest")

    @staticmethod
    def _verify_view(actual: dict, expected: dict) -> None:
        for key in ("name", "sort_field", "sort_reverse"):
            if actual.get(key) != expected[key]:
                raise ValueError("Saved view verification failed")
        if _owner_id(actual.get("owner")) != _owner_id(expected["owner"]):
            raise ValueError("Saved view verification failed")
        if _normalized_rules(actual.get("filter_rules")) != _normalized_rules(
            expected["filter_rules"]
        ):
            raise ValueError("Saved view verification failed")

    @staticmethod
    def _view_matches(actual: dict, expected: dict) -> bool:
        return (
            all(actual.get(key) == expected[key] for key in ("name", "sort_field", "sort_reverse"))
            and _owner_id(actual.get("owner")) == _owner_id(expected["owner"])
            and _normalized_rules(actual.get("filter_rules"))
            == _normalized_rules(expected["filter_rules"])
        )

    @staticmethod
    def _apply_summary(
        plan: ProtectedQualityPlan, counts: Counter[str], completion: str
    ) -> QualitySummary:
        return QualitySummary(
            plan_digest=plan.plan_digest,
            planned_at=plan.planned_at,
            views=[],
            completion_state=completion,
            counts=dict(counts),
        )


def load_protected_plan(path: str | Path) -> ProtectedQualityPlan:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version", "1.0") != "1.0":
        raise ValueError("Unsupported protected quality plan schema version")
    views = [
        ViewPlan(
            **{
                **item,
                "rules": tuple(FilterRule(**rule) for rule in item["rules"]),
            }
        )
        for item in payload["views"]
    ]
    candidates = [ManualCandidate(**item) for item in payload["manual_candidates"]]
    return ProtectedQualityPlan(
        plan_digest=payload["plan_digest"],
        config_digest=payload["config_digest"],
        instance_digest=payload["instance_digest"],
        planned_at=payload["planned_at"],
        views=views,
        manual_candidates=candidates,
        schema_version=payload.get("schema_version", "1.0"),
    )
