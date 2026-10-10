from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from doc_intelligence_hub.modules.statements.correspondent_analysis import (
    analyze_correspondent_policy,
)
from doc_intelligence_hub.modules.statements.correspondent_models import (
    AcquisitionSource,
    AcquisitionSourceCreate,
    AcquisitionSourceUpdate,
    CandidateOverride,
    CorrespondentAnalysisResult,
    CorrespondentAnalysisSnapshot,
    CorrespondentAnalysisSummary,
    CorrespondentProfile,
    CorrespondentProfileUpdate,
    CorrespondentReviewWorkspace,
    CorrespondentSyncResult,
    DocumentExpectation,
    DocumentExpectationCreate,
    DocumentExpectationSignalsV1,
    DocumentExpectationUpdate,
    ExpectationPolicyPreview,
    ExternalCandidateReview,
    ExternalCandidateSnapshotResult,
    ExternalDocumentCandidate,
    ExternalSignalConnection,
    ExternalSignalConnectionUpdate,
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
        account_identifier_extraction_requested: bool = False,
        reason: str = "manual_reanalysis",
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
        overrides = {
            override.document_id: override
            for override in self.database.list_candidate_overrides(
                self.deployment_id, correspondent_id
            )
        }
        result = analyze_correspondent_policy(
            correspondent_id,
            profile.current_name,
            documents,
            statement_series,
            analyzed_at=analyzed_at,
            account_identifier_extraction_requested=account_identifier_extraction_requested,
            candidate_overrides=overrides,
        )
        self.update_profile(
            correspondent_id,
            CorrespondentProfileUpdate(
                observed_summary=result.observed_summary,
                last_analyzed_at=result.analyzed_at,
            ),
        )
        self.database.save_correspondent_analysis_snapshot(
            self.deployment_id,
            result,
            algorithm_version="correspondent-analysis-v1",
            reason=reason,
            source_fingerprint=self.analysis_source_fingerprint(
                correspondent_id, documents, statement_series, overrides
            ),
        )
        return result

    def analysis_stale_reason(
        self, correspondent_id: int, documents: list[DocumentRecord]
    ) -> str | None:
        snapshot = self.get_latest_snapshot(correspondent_id)
        if snapshot is None:
            return "No saved analysis"
        if snapshot.algorithm_version != "correspondent-analysis-v1":
            return "Analysis method changed"
        statement_series = self.database.list_series()
        for series in statement_series:
            series["document_ids"] = [
                document["document_id"]
                for document in self.database.get_series_documents(series["id"])
            ]
        overrides = {
            override.document_id: override
            for override in self.database.list_candidate_overrides(
                self.deployment_id, correspondent_id
            )
        }
        fingerprint = self.analysis_source_fingerprint(
            correspondent_id, documents, statement_series, overrides
        )
        if fingerprint != snapshot.source_fingerprint:
            return "Paperless history or candidate grouping changed"
        return None

    @staticmethod
    def analysis_source_fingerprint(
        correspondent_id: int,
        documents: list[DocumentRecord],
        statement_series: list[dict[str, Any]],
        overrides: dict[int, CandidateOverride],
    ) -> str:
        payload = {
            "documents": [
                document.model_dump(mode="json")
                for document in sorted(documents, key=lambda item: item.id)
                if document.correspondent_id == correspondent_id
            ],
            "series": [
                {
                    **series,
                    "document_ids": sorted(series.get("document_ids", [])),
                }
                for series in sorted(statement_series, key=lambda item: str(item.get("id", "")))
                if series.get("correspondent_id") == correspondent_id
            ],
            "overrides": [
                override.model_dump(mode="json") for _, override in sorted(overrides.items())
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _suggestion_is_resolved(suggestion: Any, expectations: list[DocumentExpectation]) -> bool:
        return any(
            expectation.status != "retired"
            and expectation.kind == suggestion.kind
            and (
                (
                    suggestion.statement_series_id is not None
                    and expectation.statement_series_id == suggestion.statement_series_id
                )
                or (
                    suggestion.statement_series_id is None
                    and expectation.series_discriminator == suggestion.series_discriminator
                )
            )
            for expectation in expectations
        )

    def unresolved_suggestion_count(self, correspondent_id: int) -> int:
        snapshot = self.database.get_latest_correspondent_analysis_snapshot(
            self.deployment_id, correspondent_id
        )
        if snapshot is None:
            return 0
        expectations = self.list_expectations(correspondent_id)
        return sum(
            not self._suggestion_is_resolved(suggestion, expectations)
            for suggestion in snapshot.result.suggestions
        )

    def get_latest_snapshot(self, correspondent_id: int) -> CorrespondentAnalysisSnapshot | None:
        return self.database.get_latest_correspondent_analysis_snapshot(
            self.deployment_id, correspondent_id
        )

    def get_review_workspace(self) -> CorrespondentReviewWorkspace:
        profiles = self.list_profiles()
        expectations = self.list_expectations()
        expectations_by_profile: dict[int, list[DocumentExpectation]] = {}
        for expectation in expectations:
            expectations_by_profile.setdefault(expectation.correspondent_id, []).append(expectation)
        summaries = []
        for snapshot in self.database.list_latest_correspondent_analysis_snapshots(
            self.deployment_id
        ):
            unresolved = sum(
                not self._suggestion_is_resolved(
                    suggestion, expectations_by_profile.get(snapshot.correspondent_id, [])
                )
                for suggestion in snapshot.result.suggestions
            )
            stale = snapshot.algorithm_version != "correspondent-analysis-v1"
            summaries.append(
                CorrespondentAnalysisSummary(
                    correspondent_id=snapshot.correspondent_id,
                    snapshot_version=snapshot.snapshot_version,
                    algorithm_version=snapshot.algorithm_version,
                    reason=snapshot.reason,
                    analyzed_at=snapshot.analyzed_at,
                    suggestion_count=len(snapshot.result.suggestions),
                    unresolved_suggestion_count=unresolved,
                    freshness="stale" if stale else "fresh",
                    stale_reason="Analysis method changed" if stale else None,
                )
            )
        return CorrespondentReviewWorkspace(
            profiles=profiles,
            expectations=expectations,
            analysis_summaries=summaries,
        )

    def update_profile(
        self, correspondent_id: int, update: CorrespondentProfileUpdate
    ) -> CorrespondentProfile:
        if (
            update.review_status == "reviewed"
            and not update.acknowledge_unresolved_suggestions
            and self.unresolved_suggestion_count(correspondent_id) > 0
        ):
            raise ValueError(
                "Resolve current suggestions or explicitly acknowledge unresolved suggestions"
            )
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

    def list_candidate_overrides(self, correspondent_id: int) -> list[CandidateOverride]:
        return self.database.list_candidate_overrides(self.deployment_id, correspondent_id)

    def set_candidate_overrides(
        self,
        correspondent_id: int,
        document_ids: list[int],
        *,
        group_key: str | None,
        excluded: bool,
    ) -> list[CandidateOverride]:
        """Merge documents into one candidate (``group_key``) or mark them noise."""
        return self.database.set_candidate_overrides(
            self.deployment_id,
            correspondent_id,
            document_ids,
            group_key=group_key,
            excluded=excluded,
        )

    def clear_candidate_overrides(self, correspondent_id: int, document_ids: list[int]) -> int:
        """Revert documents to automatic candidate grouping."""
        return self.database.clear_candidate_overrides(
            self.deployment_id, correspondent_id, document_ids
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

    def replace_external_candidates(
        self, snapshot: DocumentExpectationSignalsV1
    ) -> ExternalCandidateSnapshotResult:
        return self.database.replace_external_candidate_snapshot(self.deployment_id, snapshot)

    def get_external_signal_connection(self) -> ExternalSignalConnection | None:
        return self.database.get_external_signal_connection(self.deployment_id)

    def get_external_signal_credentials(self) -> dict[str, Any] | None:
        return self.database.get_external_signal_credentials(self.deployment_id)

    def update_external_signal_connection(
        self, update: ExternalSignalConnectionUpdate
    ) -> ExternalSignalConnection:
        return self.database.update_external_signal_connection(self.deployment_id, update)

    def delete_external_signal_connection(self) -> bool:
        return self.database.delete_external_signal_connection(self.deployment_id)

    def list_external_candidates(
        self, correspondent_id: int | None = None
    ) -> list[ExternalDocumentCandidate]:
        return self.database.list_external_candidates(
            self.deployment_id,
            correspondent_id=correspondent_id,
        )

    def review_external_candidate(
        self, candidate_id: str, review: ExternalCandidateReview
    ) -> ExternalDocumentCandidate:
        return self.database.review_external_candidate(
            self.deployment_id,
            candidate_id,
            review,
        )

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
