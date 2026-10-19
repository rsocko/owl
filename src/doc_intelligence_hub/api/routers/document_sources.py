from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response

from doc_intelligence_hub.api.routers import (
    load_statement_config_from_request,
    make_paperless_client,
    raise_api_error,
)
from doc_intelligence_hub.modules.document_sources.database import SourceDatabase
from doc_intelligence_hub.modules.document_sources.models import (
    CoverageEvaluationRequest,
    CoverageGraceExtension,
    CoverageNearMatch,
    CoverageOccurrence,
    CoverageOccurrenceDetail,
    CoverageReassignment,
    DocumentExpectation,
    DocumentExpectationCreate,
    DocumentExpectationUpdate,
    DocumentSeries,
    DocumentSeriesCreate,
    DocumentSeriesUpdate,
    DocumentSource,
    DocumentSourceUpdate,
    ExternalSignalQueueItem,
    ExternalSignalRelationship,
    ExternalSignalRelationshipUpdate,
    SeriesAnalysisDismiss,
    SeriesMemberPeriod,
    SourceReopenRequest,
    SourceSeriesAnalysisDocument,
    SourceSeriesAnalysisRequest,
    SourceSeriesAnalysisResult,
)
from doc_intelligence_hub.modules.statements.correspondent_analysis import (
    analyze_correspondent_policy,
)
from doc_intelligence_hub.modules.statements.correspondent_models import (
    AcquisitionSource,
    AcquisitionSourceCreate,
    AcquisitionSourceUpdate,
    ExpectationPolicyPreview,
    PolicyApplyRequest,
    PolicyApplyResponse,
    paperless_deployment_identity,
)
from doc_intelligence_hub.modules.statements.correspondent_models import (
    DocumentExpectation as LegacyDocumentExpectation,
)
from doc_intelligence_hub.modules.statements.database import Database
from doc_intelligence_hub.modules.statements.paperless import build_document_records
from doc_intelligence_hub.modules.statements.policy_corrections import apply_policy_operations
from doc_intelligence_hub.modules.statements.policy_evaluation import evaluate_expectation_policy

router = APIRouter(prefix="/api", tags=["document-sources"])


def _database(request: Request) -> SourceDatabase:
    config = load_statement_config_from_request(request)
    settings = request.app.state.hub_settings
    paperless_url = settings.paperless_url or config.source.paperless_url
    if not paperless_url:
        raise_api_error(
            503,
            "paperless_not_configured",
            "A Paperless deployment URL is required for document sources.",
        )
    return SourceDatabase(
        config.runtime.database_path,
        paperless_deployment_identity(paperless_url),
    )


def _legacy_database(request: Request) -> tuple[Database, str]:
    config = load_statement_config_from_request(request)
    settings = request.app.state.hub_settings
    paperless_url = settings.paperless_url or config.source.paperless_url
    if not paperless_url:
        raise_api_error(
            503,
            "paperless_not_configured",
            "A Paperless deployment URL is required for document sources.",
        )
    return Database(config.runtime.database_path), paperless_deployment_identity(paperless_url)


def _domain_error(exc: KeyError | ValueError) -> None:
    code = exc.args[0] if isinstance(exc, KeyError) and exc.args else "policy_conflict"
    if isinstance(exc, KeyError):
        raise_api_error(404, str(code), str(code).replace("_", " "))
    raise_api_error(409, "policy_conflict", str(exc))


@router.post("/sources/sync")
async def sync_document_sources(request: Request) -> dict[str, int]:
    client = make_paperless_client(request, timeout=30.0)
    database = _database(request)
    try:
        return database.sync_sources(await client.list_correspondents())
    finally:
        database.close()
        await client.aclose()


@router.get("/sources", response_model=list[DocumentSource])
async def list_document_sources(
    request: Request,
    review_state: Literal["needs_review", "resolved"] | None = None,
    disposition: str | None = None,
    search: str | None = Query(default=None, max_length=200),
) -> list[DocumentSource]:
    database = _database(request)
    try:
        return database.list_sources(
            review_state=review_state,
            disposition=disposition,
            search=search,
        )
    finally:
        database.close()


@router.get("/sources/{correspondent_id}", response_model=DocumentSource)
async def get_document_source(request: Request, correspondent_id: int) -> DocumentSource:
    database = _database(request)
    try:
        source = database.get_source(correspondent_id)
        if source is None:
            raise_api_error(404, "document_source_not_found", "Document source not found.")
        return source
    finally:
        database.close()


@router.put("/sources/{correspondent_id}/disposition", response_model=DocumentSource)
async def set_source_disposition(
    request: Request, correspondent_id: int, body: DocumentSourceUpdate
) -> DocumentSource:
    database = _database(request)
    try:
        try:
            return database.update_source(correspondent_id, body)
        except (KeyError, ValueError) as exc:
            _domain_error(exc)
    finally:
        database.close()


@router.post("/sources/{correspondent_id}/reopen", response_model=DocumentSource)
async def reopen_document_source(
    request: Request, correspondent_id: int, body: SourceReopenRequest
) -> DocumentSource:
    database = _database(request)
    try:
        try:
            return database.reopen_source(correspondent_id, body)
        except (KeyError, ValueError) as exc:
            _domain_error(exc)
    finally:
        database.close()


@router.post("/sources/reopen-due")
async def reopen_due_sources(request: Request, as_of: Annotated[date, Query()]) -> dict[str, int]:
    database = _database(request)
    try:
        return {"reopened": database.reopen_due_waiting_sources(as_of)}
    finally:
        database.close()


@router.get("/series", response_model=list[DocumentSeries])
async def list_document_series(
    request: Request, correspondent_id: int | None = None
) -> list[DocumentSeries]:
    database = _database(request)
    try:
        return database.list_series(correspondent_id)
    finally:
        database.close()


@router.post(
    "/sources/{correspondent_id}/series-analysis",
    response_model=SourceSeriesAnalysisResult,
)
async def analyze_source_series(
    request: Request,
    correspondent_id: int,
    body: SourceSeriesAnalysisRequest | None = None,
) -> SourceSeriesAnalysisResult:
    database = _database(request)
    legacy_database, deployment_id = _legacy_database(request)
    client = make_paperless_client(request, timeout=60.0)
    try:
        source = database.get_source(correspondent_id)
        if source is None:
            raise_api_error(404, "document_source_not_found", "Document source not found.")
        correspondents, tags, document_types = await client.fetch_all_metadata()
        raw_documents = await client.list_documents(correspondent_id=correspondent_id)
        documents = build_document_records(
            raw_documents,
            correspondents,
            tags,
            document_types,
        )
        signal = None
        if body and body.signal_id:
            signal = next(
                (
                    item
                    for item in legacy_database.list_external_candidates(deployment_id)
                    if item.id == body.signal_id
                ),
                None,
            )
            if signal is None:
                raise_api_error(404, "external_signal_not_found", "External signal not found.")
            candidate_terms = (
                [signal.account_last_four]
                if signal.account_last_four
                else [signal.account_name, signal.account_type]
            )
            terms = [
                value.casefold()
                for value in candidate_terms
                if value and len(value.strip()) >= 3
            ]
            documents = [
                document
                for document in documents
                if any(term in document.title.casefold() for term in terms)
            ]
        existing_series = [
            {
                "id": item.id,
                "name": item.name,
                "correspondent_id": item.source_correspondent_id,
                "correspondent_name": source.current_name,
                "document_ids": item.document_ids,
            }
            for item in database.list_series(correspondent_id)
        ]
        result = analyze_correspondent_policy(
            correspondent_id,
            source.current_name,
            documents,
            existing_series,
            candidate_overrides={
                override.document_id: override
                for override in legacy_database.list_candidate_overrides(
                    deployment_id, correspondent_id
                )
            },
        )
        suggestions = [
            suggestion
            for suggestion in result.suggestions
            if suggestion.evidence.sample_size >= 2
        ]
        low_evidence_document_count = len(
            {
                document_id
                for suggestion in result.suggestions
                if suggestion.evidence.sample_size < 2
                for document_id in suggestion.document_ids
            }
        )
        response = SourceSeriesAnalysisResult(
            **result.model_dump(exclude={"suggestions"}),
            suggestions=suggestions,
            signal_id=signal.id if signal else None,
            signal_label=signal.display_hint if signal else None,
            low_evidence_document_count=low_evidence_document_count,
            documents=[
                SourceSeriesAnalysisDocument(
                    id=document.id,
                    title=document.title,
                    created=document.created,
                    document_type=document.document_type,
                    tags=document.tags,
                )
                for document in documents
            ],
        )
        response.observed_summary.candidate_series_count = len(suggestions)
        database.save_series_analysis(response)
        return response
    finally:
        database.close()
        legacy_database.close()
        await client.aclose()


@router.get(
    "/sources/{correspondent_id}/series-analysis",
    response_model=SourceSeriesAnalysisResult | None,
)
async def get_source_series_analysis(
    request: Request,
    correspondent_id: int,
    signal_id: str | None = Query(default=None, max_length=200),
) -> SourceSeriesAnalysisResult | None:
    database = _database(request)
    try:
        if database.get_source(correspondent_id) is None:
            raise_api_error(404, "document_source_not_found", "Document source not found.")
        return database.get_series_analysis(correspondent_id, signal_id)
    finally:
        database.close()


@router.post("/sources/{correspondent_id}/series-analysis/dismiss", status_code=204)
async def dismiss_source_series_candidate(
    request: Request,
    correspondent_id: int,
    body: SeriesAnalysisDismiss,
) -> Response:
    database = _database(request)
    legacy_database, deployment_id = _legacy_database(request)
    try:
        if database.get_source(correspondent_id) is None:
            raise_api_error(404, "document_source_not_found", "Document source not found.")
        legacy_database.set_candidate_overrides(
            deployment_id,
            correspondent_id,
            body.document_ids,
            group_key=None,
            excluded=True,
        )
        database.clear_series_analysis(correspondent_id)
        return Response(status_code=204)
    finally:
        database.close()
        legacy_database.close()


@router.post("/series", response_model=DocumentSeries, status_code=201)
async def create_document_series(request: Request, body: DocumentSeriesCreate) -> DocumentSeries:
    database = _database(request)
    try:
        try:
            return database.create_series(body)
        except (KeyError, ValueError) as exc:
            _domain_error(exc)
    finally:
        database.close()


@router.patch("/series/{series_id}", response_model=DocumentSeries)
async def update_document_series(
    request: Request, series_id: str, body: DocumentSeriesUpdate
) -> DocumentSeries:
    database = _database(request)
    try:
        try:
            return database.update_series(series_id, body)
        except (KeyError, ValueError) as exc:
            _domain_error(exc)
    finally:
        database.close()


@router.put("/series/{series_id}/members/{document_id}/period", status_code=204)
async def record_series_member_period(
    request: Request,
    series_id: str,
    document_id: int,
    body: SeriesMemberPeriod,
) -> Response:
    database = _database(request)
    try:
        try:
            database.record_series_member_period(
                series_id,
                document_id,
                body.document_date,
                body.period_key,
            )
        except (KeyError, ValueError) as exc:
            _domain_error(exc)
        return Response(status_code=204)
    finally:
        database.close()


@router.get("/expectations", response_model=list[DocumentExpectation])
async def list_document_expectations(
    request: Request, correspondent_id: int | None = None
) -> list[DocumentExpectation]:
    database = _database(request)
    try:
        return database.list_expectations(correspondent_id)
    finally:
        database.close()


@router.post("/expectations", response_model=DocumentExpectation, status_code=201)
async def create_document_expectation(
    request: Request, body: DocumentExpectationCreate
) -> DocumentExpectation:
    database = _database(request)
    try:
        try:
            return database.create_expectation(body)
        except (KeyError, ValueError) as exc:
            _domain_error(exc)
    finally:
        database.close()


@router.patch("/expectations/{expectation_id}", response_model=DocumentExpectation)
async def update_document_expectation(
    request: Request, expectation_id: str, body: DocumentExpectationUpdate
) -> DocumentExpectation:
    database = _database(request)
    try:
        try:
            return database.update_expectation(expectation_id, body)
        except (KeyError, ValueError) as exc:
            _domain_error(exc)
    finally:
        database.close()


@router.get("/expectations/{expectation_id}/coverage-eligibility")
async def get_expectation_coverage_eligibility(
    request: Request, expectation_id: str
) -> dict[str, bool]:
    database = _database(request)
    try:
        try:
            return {"eligible": database.expectation_is_coverage_eligible(expectation_id)}
        except (KeyError, ValueError) as exc:
            _domain_error(exc)
    finally:
        database.close()


@router.post(
    "/expectations/{expectation_id}/policy-preview",
    response_model=ExpectationPolicyPreview,
)
async def preview_expectation_policy(
    request: Request, expectation_id: str
) -> ExpectationPolicyPreview:
    database = _database(request)
    client = make_paperless_client(request, timeout=60.0)
    try:
        expectation = database.get_expectation(expectation_id)
        if expectation is None:
            raise_api_error(
                404, "document_expectation_not_found", "Document expectation not found."
            )
        series = database.get_series(expectation.series_id) if expectation.series_id else None
        if series is None:
            raise_api_error(
                409,
                "expectation_series_missing",
                "Policy preview requires a reviewed document series.",
            )
        correspondents, tags, document_types = await client.fetch_all_metadata()
        raw_documents = await client.list_documents(
            correspondent_id=expectation.source_correspondent_id
        )
        documents = [
            document
            for document in build_document_records(
                raw_documents, correspondents, tags, document_types
            )
            if document.id in set(series.document_ids)
        ]
        legacy = LegacyDocumentExpectation(
            id=expectation.id,
            correspondent_id=expectation.source_correspondent_id,
            kind=expectation.kind,
            statement_series_id=series.id if expectation.kind == "statement" else None,
            document_ids=series.document_ids,
            series_discriminator=series.discriminator or series.name,
            expectation_mode=expectation.expectation_mode,
            status=expectation.status,
            cadence=expectation.cadence,
            importance=expectation.importance,
            monitoring_mode=expectation.monitoring_mode,
            calendar_rule=expectation.calendar_rule,
            availability_window=expectation.availability_window,
            grace_period_days=expectation.grace_period_days,
            escalation_policy=expectation.escalation_policy,
            fulfillment_rule=expectation.fulfillment_rule,
            evidence=expectation.evidence,
            title_convention=expectation.title_convention,
            metadata_policy=expectation.metadata_policy,
            acquisition_source_id=expectation.acquisition_source_id,
        )
        member_rows = (
            database.connect()
            .execute(
                """SELECT document_id, document_date AS statement_date,
                      period_key AS period_label
               FROM document_series_members_v2 WHERE series_id = ?""",
                (series.id,),
            )
            .fetchall()
        )
        return evaluate_expectation_policy(
            legacy,
            database.get_source(expectation.source_correspondent_id).current_name,
            documents,
            tag_names=tags,
            document_type_names=document_types,
            series_documents={row["document_id"]: dict(row) for row in member_rows},
            preview_signing_key=client.token,
        )
    except (KeyError, ValueError) as exc:
        _domain_error(exc)
    finally:
        database.close()
        await client.aclose()


@router.post(
    "/expectations/{expectation_id}/policy-apply",
    response_model=PolicyApplyResponse,
)
async def apply_expectation_policy(
    request: Request,
    expectation_id: str,
    body: PolicyApplyRequest,
) -> PolicyApplyResponse:
    database = _database(request)
    client = make_paperless_client(request, timeout=60.0)
    try:
        expectation = database.get_expectation(expectation_id)
        if expectation is None:
            raise_api_error(
                404, "document_expectation_not_found", "Document expectation not found."
            )
        if expectation.status != "confirmed":
            raise_api_error(
                409,
                "expectation_not_confirmed",
                "Only confirmed expectations can apply policy corrections.",
            )
        _, tags, document_types = await client.fetch_all_metadata()
        return await apply_policy_operations(
            expectation_id=expectation_id,
            request=body,
            client=client,
            tag_names=tags,
            document_type_names=document_types,
            preview_signing_key=client.token,
        )
    finally:
        database.close()
        await client.aclose()


@router.post("/acquisition-sources", response_model=AcquisitionSource, status_code=201)
async def create_acquisition_source(
    request: Request, body: AcquisitionSourceCreate
) -> AcquisitionSource:
    database, deployment_id = _legacy_database(request)
    try:
        return database.create_acquisition_source(deployment_id, body)
    finally:
        database.close()


@router.get("/acquisition-sources", response_model=list[AcquisitionSource])
async def list_acquisition_sources(request: Request) -> list[AcquisitionSource]:
    database, deployment_id = _legacy_database(request)
    try:
        return database.list_acquisition_sources(deployment_id)
    finally:
        database.close()


@router.patch("/acquisition-sources/{source_id}", response_model=AcquisitionSource)
async def update_acquisition_source(
    request: Request, source_id: str, body: AcquisitionSourceUpdate
) -> AcquisitionSource:
    database, deployment_id = _legacy_database(request)
    try:
        try:
            return database.update_acquisition_source(deployment_id, source_id, body)
        except (KeyError, ValueError) as exc:
            _domain_error(exc)
    finally:
        database.close()


@router.put(
    "/external-signals/{signal_id}/relationship",
    response_model=ExternalSignalRelationship,
)
async def set_external_signal_relationship(
    request: Request,
    signal_id: str,
    body: ExternalSignalRelationshipUpdate,
) -> ExternalSignalRelationship:
    database = _database(request)
    try:
        try:
            return database.set_external_relationship(signal_id, body)
        except (KeyError, ValueError) as exc:
            _domain_error(exc)
    finally:
        database.close()


@router.get("/external-signals", response_model=list[ExternalSignalQueueItem])
async def list_external_signals(
    request: Request,
    active_only: bool = True,
) -> list[ExternalSignalQueueItem]:
    source_database = _database(request)
    legacy_database, deployment_id = _legacy_database(request)
    try:
        items = legacy_database.list_external_candidates(deployment_id)
        results: list[ExternalSignalQueueItem] = []
        for item in items:
            if active_only and not item.active:
                continue
            try:
                relationship = source_database.get_external_relationship(item.id)
            except KeyError:
                relationship = None
            results.append(
                ExternalSignalQueueItem(
                    id=item.id,
                    kind=item.kind,
                    active=item.active,
                    display_hint=item.display_hint,
                    confidence=item.confidence,
                    basis=item.basis,
                    source_as_of=item.source_as_of,
                    account_name=item.account_name,
                    institution_name=item.institution_name,
                    account_type=item.account_type,
                    account_last_four=item.account_last_four,
                    relationship=relationship,
                )
            )
        return results
    finally:
        source_database.close()
        legacy_database.close()


@router.post("/coverage/evaluate", response_model=list[CoverageOccurrence])
async def evaluate_document_coverage(
    request: Request, body: CoverageEvaluationRequest
) -> list[CoverageOccurrence]:
    database = _database(request)
    try:
        return database.evaluate_coverage(body.as_of, body.horizon_start)
    finally:
        database.close()


@router.get("/coverage", response_model=list[CoverageOccurrence])
async def list_document_coverage(
    request: Request,
    as_of: Annotated[date, Query()],
    horizon_start: date | None = None,
) -> list[CoverageOccurrence]:
    database = _database(request)
    try:
        return database.evaluate_coverage(as_of, horizon_start)
    finally:
        database.close()


@router.get(
    "/coverage/occurrences/{occurrence_id}",
    response_model=CoverageOccurrenceDetail,
)
async def get_coverage_occurrence(request: Request, occurrence_id: str) -> CoverageOccurrenceDetail:
    database = _database(request)
    try:
        try:
            return database.get_occurrence_detail(occurrence_id)
        except (KeyError, ValueError) as exc:
            _domain_error(exc)
    finally:
        database.close()


@router.post(
    "/coverage/occurrences/{occurrence_id}/extend-grace",
    response_model=CoverageOccurrence,
)
async def extend_coverage_occurrence_grace(
    request: Request,
    occurrence_id: str,
    body: CoverageGraceExtension,
) -> CoverageOccurrence:
    database = _database(request)
    try:
        try:
            return database.extend_occurrence_grace(occurrence_id, body.extend_until)
        except (KeyError, ValueError) as exc:
            _domain_error(exc)
    finally:
        database.close()


@router.get(
    "/coverage/occurrences/{occurrence_id}/near-matches",
    response_model=list[CoverageNearMatch],
)
async def list_coverage_near_matches(
    request: Request, occurrence_id: str
) -> list[CoverageNearMatch]:
    database = _database(request)
    client = make_paperless_client(request, timeout=30.0)
    try:
        try:
            detail = database.get_occurrence_detail(occurrence_id)
        except (KeyError, ValueError) as exc:
            _domain_error(exc)
        _, _, document_types = await client.fetch_all_metadata()
        documents = await client.list_documents(
            correspondent_id=detail.occurrence.source_correspondent_id,
            created_after=(detail.occurrence.due_start - date.resolution * 62).isoformat(),
            created_before=(detail.occurrence.due_end + date.resolution * 62).isoformat(),
            limit=50,
        )
        assigned = database.occurrence_assigned_document_ids()
        return [
            CoverageNearMatch(
                document_id=int(item["id"]),
                title=str(item.get("title") or "Untitled document"),
                document_date=item.get("created_date") or item.get("created"),
                correspondent_id=item.get("correspondent"),
                document_type=document_types.get(item.get("document_type")),
                already_assigned=int(item["id"]) in assigned,
            )
            for item in documents
        ]
    finally:
        database.close()
        await client.aclose()


@router.post(
    "/coverage/occurrences/{occurrence_id}/reassign",
    response_model=CoverageOccurrence,
)
async def reassign_coverage_occurrence_document(
    request: Request,
    occurrence_id: str,
    body: CoverageReassignment,
) -> CoverageOccurrence:
    database = _database(request)
    client = make_paperless_client(request, timeout=30.0)
    try:
        try:
            detail = database.get_occurrence_detail(occurrence_id)
        except (KeyError, ValueError) as exc:
            _domain_error(exc)
        candidates = await client.list_documents(
            correspondent_id=detail.occurrence.source_correspondent_id,
            limit=200,
        )
        if body.document_id not in {int(item["id"]) for item in candidates}:
            raise_api_error(
                409,
                "coverage_document_not_eligible",
                "The selected Paperless document does not belong to this occurrence source.",
            )
        try:
            return database.reassign_occurrence_document(
                occurrence_id,
                body.document_id,
                body.document_date,
            )
        except (KeyError, ValueError) as exc:
            _domain_error(exc)
    finally:
        database.close()
        await client.aclose()


@router.delete("/sources/legacy-contract", include_in_schema=False)
async def legacy_contract_removed() -> Response:
    return Response(status_code=410)
