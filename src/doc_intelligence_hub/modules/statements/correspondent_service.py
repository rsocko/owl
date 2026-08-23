from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from doc_intelligence_hub.modules.statements.correspondent_analysis import (
    analyze_correspondent_policy,
)
from doc_intelligence_hub.modules.statements.correspondent_models import (
    AcquisitionSource,
    AcquisitionSourceCreate,
    AcquisitionSourceUpdate,
    CorrespondentInventoryItem,
    CorrespondentPolicyAnalysis,
    CorrespondentProfile,
    CorrespondentProfileUpdate,
    CorrespondentSyncResult,
    DocumentExpectation,
    DocumentExpectationCreate,
    DocumentExpectationUpdate,
    IdentityResolution,
    LegacyOverrideReviewItem,
    SeriesPolicySuggestion,
    SuggestionDispositionRequest,
)
from doc_intelligence_hub.modules.statements.database import Database


class CorrespondentPolicyService:
    """Deployment-scoped application service for correspondent policy."""

    def __init__(self, database: Database, deployment_id: str) -> None:
        self.database = database
        self.deployment_id = deployment_id

    def synchronize(self, correspondents: list[dict[str, Any]]) -> CorrespondentSyncResult:
        result = self.database.reconcile_correspondents(self.deployment_id, correspondents)
        migrated, review_required = self.database.migrate_legacy_provider_overrides(
            self.deployment_id
        )
        return result.model_copy(
            update={
                "legacy_migrated": migrated,
                "legacy_review_required": review_required,
            }
        )

    def list_profiles(self) -> list[CorrespondentProfile]:
        return self.database.list_correspondent_profiles(self.deployment_id)

    def list_inventory(self) -> list[CorrespondentInventoryItem]:
        profiles = self.list_profiles()
        expectations = self.list_expectations()
        series = self.database.list_series()
        stale_before = datetime.now(timezone.utc) - timedelta(days=30)
        items: list[CorrespondentInventoryItem] = []
        for profile in profiles:
            profile_expectations = [
                item for item in expectations if item.correspondent_id == profile.correspondent_id
            ]
            profile_series = [
                item for item in series if item.get("correspondent_id") == profile.correspondent_id
            ]
            analysis_stale = _timestamp_is_stale(profile.last_analyzed_at, stale_before)
            reasons = []
            if profile.lifecycle_status == "orphaned":
                reasons.append("orphaned_profile")
            if profile.review_status == "unreviewed":
                reasons.append("unreviewed_profile")
            suggested_count = sum(item.status == "suggested" for item in profile_expectations)
            if suggested_count:
                reasons.append("suggested_expectations")
            if analysis_stale:
                reasons.append("stale_analysis")
            if profile_series:
                reasons.append("statement_history")
            items.append(
                CorrespondentInventoryItem(
                    profile=profile,
                    expectation_count=len(profile_expectations),
                    suggested_expectation_count=suggested_count,
                    statement_series_count=len(profile_series),
                    analysis_stale=analysis_stale,
                    priority_reasons=reasons,
                )
            )
        return sorted(
            items,
            key=lambda item: (
                item.profile.lifecycle_status != "orphaned",
                item.profile.review_status != "unreviewed",
                -item.suggested_expectation_count,
                not item.analysis_stale,
                -item.statement_series_count,
                item.profile.current_name.casefold(),
            ),
        )

    def get_profile(self, correspondent_id: int) -> CorrespondentProfile | None:
        return self.database.get_correspondent_profile(self.deployment_id, correspondent_id)

    def update_profile(
        self, correspondent_id: int, update: CorrespondentProfileUpdate
    ) -> CorrespondentProfile:
        return self.database.update_correspondent_profile(
            self.deployment_id, correspondent_id, update
        )

    def relink_profile(
        self, old_correspondent_id: int, new_correspondent_id: int, new_name: str
    ) -> CorrespondentProfile:
        return self.database.relink_correspondent_profile(
            self.deployment_id,
            old_correspondent_id,
            new_correspondent_id,
            new_name,
        )

    def dismiss_suggestion(
        self, correspondent_id: int, suggestion: SuggestionDispositionRequest
    ) -> None:
        if self.get_profile(correspondent_id) is None:
            raise KeyError("correspondent_profile_not_found")
        self.database.record_correspondent_suggestion_dismissal(
            self.deployment_id,
            correspondent_id,
            _suggestion_key(suggestion),
        )

    def create_expectation(
        self, correspondent_id: int, expectation: DocumentExpectationCreate
    ) -> DocumentExpectation:
        return self.database.create_document_expectation(
            self.deployment_id, correspondent_id, expectation
        )

    def list_expectations(self, correspondent_id: int | None = None) -> list[DocumentExpectation]:
        return self.database.list_document_expectations(self.deployment_id, correspondent_id)

    def update_expectation(
        self, expectation_id: str, update: DocumentExpectationUpdate
    ) -> DocumentExpectation:
        return self.database.update_document_expectation(self.deployment_id, expectation_id, update)

    def create_acquisition_source(self, source: AcquisitionSourceCreate) -> AcquisitionSource:
        return self.database.create_acquisition_source(self.deployment_id, source)

    def list_acquisition_sources(self) -> list[AcquisitionSource]:
        return self.database.list_acquisition_sources(self.deployment_id)

    def update_acquisition_source(
        self, source_id: str, update: AcquisitionSourceUpdate
    ) -> AcquisitionSource:
        return self.database.update_acquisition_source(self.deployment_id, source_id, update)

    def can_emit_missing_alert(self, expectation_id: str) -> bool:
        return self.database.expectation_can_emit_missing_alert(self.deployment_id, expectation_id)

    def resolve_identity(self, identity: str) -> IdentityResolution:
        return self.database.resolve_expectation_identity(self.deployment_id, identity)

    def list_legacy_override_review(self) -> list[LegacyOverrideReviewItem]:
        return self.database.list_legacy_override_review(self.deployment_id)

    def analyze(
        self,
        correspondent_id: int,
        documents: list[dict[str, Any]],
        tag_names: dict[int, str],
        document_type_names: dict[int, str],
        mail_rules: list[dict[str, Any]],
    ) -> CorrespondentPolicyAnalysis:
        profile = self.get_profile(correspondent_id)
        if profile is None:
            raise KeyError("correspondent_profile_not_found")
        scoped_documents = [
            item
            for item in documents
            if item.get("correspondent") is None
            or str(item["correspondent"]) == str(correspondent_id)
        ]
        series = [
            item
            for item in self.database.list_series()
            if item.get("correspondent_id") == correspondent_id
        ]
        result = analyze_correspondent_policy(
            profile=profile,
            raw_documents=scoped_documents,
            tag_names=tag_names,
            document_type_names=document_type_names,
            series=series,
            series_documents={
                str(item["id"]): self.database.get_series_documents(str(item["id"]))
                for item in series
            },
            series_overrides={
                str(item["id"]): self.database.get_series_overrides(str(item["id"]))
                for item in series
            },
            expectations=self.list_expectations(correspondent_id),
            acquisition_sources=self.list_acquisition_sources(),
            mail_rules=mail_rules,
        )
        dismissed = self.database.list_dismissed_correspondent_suggestion_keys(
            self.deployment_id, correspondent_id
        )
        return result.model_copy(
            update={
                "suggestions": [
                    suggestion
                    for suggestion in result.suggestions
                    if _suggestion_key(suggestion) not in dismissed
                ]
            }
        )

    def close(self) -> None:
        self.database.close()


def _timestamp_is_stale(value: str | None, stale_before: datetime) -> bool:
    if value is None:
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed < stale_before


def _suggestion_key(
    suggestion: SeriesPolicySuggestion | SuggestionDispositionRequest,
) -> str:
    payload = {
        "statement_series_id": suggestion.statement_series_id,
        "source_statement_series_id": suggestion.source_statement_series_id,
        "series_discriminator": suggestion.series_discriminator,
        "kind": suggestion.kind,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
