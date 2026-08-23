from __future__ import annotations

from typing import Any

from doc_intelligence_hub.modules.statements.correspondent_models import (
    AcquisitionSource,
    AcquisitionSourceCreate,
    AcquisitionSourceUpdate,
    CorrespondentProfile,
    CorrespondentProfileUpdate,
    CorrespondentSyncResult,
    DocumentExpectation,
    DocumentExpectationCreate,
    DocumentExpectationUpdate,
    IdentityResolution,
    LegacyOverrideReviewItem,
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

    def close(self) -> None:
        self.database.close()
