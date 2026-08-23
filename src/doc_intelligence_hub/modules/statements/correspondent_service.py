from __future__ import annotations

from datetime import datetime
from typing import Any

from doc_intelligence_hub.modules.statements.correspondent_analysis import (
    analyze_correspondent_policy,
)
from doc_intelligence_hub.modules.statements.correspondent_models import (
    AcquisitionSource,
    AcquisitionSourceCreate,
    AcquisitionSourceUpdate,
    CorrespondentAnalysisResult,
    CorrespondentProfile,
    CorrespondentProfileUpdate,
    CorrespondentSyncResult,
    DocumentExpectation,
    DocumentExpectationCreate,
    DocumentExpectationUpdate,
    ExpectationPolicyPreview,
    IdentityResolution,
    LegacyOverrideReviewItem,
)
from doc_intelligence_hub.modules.statements.database import Database
from doc_intelligence_hub.modules.statements.models import DocumentRecord
from doc_intelligence_hub.modules.statements.policy_evaluation import evaluate_expectation_policy


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

    def get_profile(self, correspondent_id: int) -> CorrespondentProfile | None:
        return self.database.get_correspondent_profile(self.deployment_id, correspondent_id)

    def analyze_profile(
        self,
        correspondent_id: int,
        documents: list[DocumentRecord],
        *,
        analyzed_at: datetime | None = None,
    ) -> CorrespondentAnalysisResult:
        profile = self.get_profile(correspondent_id)
        if profile is None:
            raise KeyError("correspondent_profile_not_found")
        statement_series = self.database.list_series()
        for series in statement_series:
            series["document_ids"] = [
                document["document_id"]
                for document in self.database.get_series_documents(series["id"])
            ]
        result = analyze_correspondent_policy(
            correspondent_id,
            profile.current_name,
            documents,
            statement_series,
            analyzed_at=analyzed_at,
        )
        self.update_profile(
            correspondent_id,
            CorrespondentProfileUpdate(
                observed_summary=result.observed_summary,
                last_analyzed_at=result.analyzed_at,
            ),
        )
        return result

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

    def create_expectation(
        self, correspondent_id: int, expectation: DocumentExpectationCreate
    ) -> DocumentExpectation:
        return self.database.create_document_expectation(
            self.deployment_id, correspondent_id, expectation
        )

    def list_expectations(self, correspondent_id: int | None = None) -> list[DocumentExpectation]:
        return self.database.list_document_expectations(self.deployment_id, correspondent_id)

    def get_expectation(self, expectation_id: str) -> DocumentExpectation | None:
        return self.database.get_document_expectation(self.deployment_id, expectation_id)

    def update_expectation(
        self, expectation_id: str, update: DocumentExpectationUpdate
    ) -> DocumentExpectation:
        return self.database.update_document_expectation(self.deployment_id, expectation_id, update)

    def preview_expectation_policy(
        self,
        expectation_id: str,
        documents: list[DocumentRecord],
        *,
        tag_names: dict[int, str],
        document_type_names: dict[int, str],
        preview_signing_key: str | None = None,
    ) -> ExpectationPolicyPreview:
        expectation = self.database.get_document_expectation(self.deployment_id, expectation_id)
        if expectation is None:
            raise KeyError("document_expectation_not_found")
        profile = self.get_profile(expectation.correspondent_id)
        if profile is None:
            raise KeyError("correspondent_profile_not_found")

        series_documents: dict[int, dict] = {}
        if expectation.statement_series_id:
            rows = self.database.get_series_documents(expectation.statement_series_id)
            series_documents = {int(row["document_id"]): row for row in rows}
            documents = [document for document in documents if document.id in series_documents]
        else:
            if not expectation.document_ids:
                raise ValueError("Confirmed expectation has no durable document scope")
            scoped_ids = set(expectation.document_ids)
            documents = [
                document
                for document in documents
                if document.id in scoped_ids
                and document.correspondent_id == expectation.correspondent_id
            ]

        return evaluate_expectation_policy(
            expectation,
            profile.current_name,
            documents,
            tag_names=tag_names,
            document_type_names=document_type_names,
            series_documents=series_documents,
            preview_signing_key=preview_signing_key,
        )

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

    def close(self) -> None:
        self.database.close()
