from __future__ import annotations

import contextlib
import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from starlette.responses import StreamingResponse

from doc_intelligence_hub.api.routers import (
    get_statement_config_path,
    load_statement_config_from_request,
    make_paperless_client,
    raise_api_error,
)
from doc_intelligence_hub.modules.statements.api import (
    _discovery_event_generator,
    _recommendations_event_generator,
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
    LegacyOverrideReviewItem,
    PolicyApplyRequest,
    PolicyApplyResponse,
    PolicyOperationResult,
    PolicyUndoRequest,
    RelinkProfileRequest,
    paperless_deployment_identity,
)
from doc_intelligence_hub.modules.statements.correspondent_service import (
    CorrespondentPolicyService,
)
from doc_intelligence_hub.modules.statements.database import Database
from doc_intelligence_hub.modules.statements.models import (
    MergeSeriesRequest,
    ReassignDocumentRequest,
    RenameSeriesRequest,
    SplitSeriesRequest,
)
from doc_intelligence_hub.modules.statements.paperless import build_document_records
from doc_intelligence_hub.modules.statements.policy_corrections import (
    apply_policy_operations,
    undo_policy_operation,
)
from doc_intelligence_hub.modules.statements.service import run_discovery, run_recommendations

router = APIRouter(tags=["statement-tracker"])


@router.get("/providers")
async def list_discovered_providers(request: Request) -> dict[str, Any]:
    """Return all providers from the latest discovery run."""
    config = load_statement_config_from_request(request)
    db = Database(config.runtime.database_path)
    try:
        discovery = db.load_latest_discovery()
        if discovery is None:
            return {"providers": [], "analyzed_documents": 0, "run_at": None}

        # Also grab the run timestamp
        runs = db.list_discovery_runs(limit=1)
        run_at = runs[0]["run_at"] if runs else None

        providers = [
            {
                "provider_key": p.provider_key,
                "provider_name": p.provider_name,
                "correspondent_id": p.correspondent_id,
                "document_count": p.document_count,
                "normalized_title": p.normalized_title,
                "title_consistency": p.title_consistency,
                "frequency": p.pattern.frequency,
                "pattern_type": p.pattern.pattern_type,
                "confidence": p.pattern.confidence,
                "anchor_day": p.pattern.anchor_day,
                "variance_days": p.pattern.variance_days,
                "grace_period_days": p.pattern.grace_period_days,
                "sample_document_ids": p.sample_document_ids,
                "first_seen": p.first_seen.isoformat(),
                "last_seen": p.last_seen.isoformat(),
            }
            for p in discovery.providers
        ]

        return {
            "providers": providers,
            "analyzed_documents": discovery.analyzed_documents,
            "run_at": run_at,
        }
    finally:
        db.close()


@router.get("/health")
async def statement_health(request: Request) -> dict[str, Any]:
    config = load_statement_config_from_request(request)
    return {
        "status": "ok",
        "module": "statement-tracker",
        "config_path": get_statement_config_path(request),
        "source_mode": config.source.mode,
        "paperless_url": config.source.paperless_url,
    }


@router.post("/discovery/run")
async def discovery_run(request: Request) -> dict[str, Any]:
    result = await run_discovery(load_statement_config_from_request(request))
    return result.model_dump(mode="json")


@router.post("/recommendations/run")
async def recommendations_run(request: Request, as_of: date = Query(...)) -> dict[str, Any]:  # noqa: B008
    result = await run_recommendations(load_statement_config_from_request(request), as_of)
    return result.model_dump(mode="json")


@router.get("/discovery/stream")
async def discovery_stream(request: Request) -> StreamingResponse:
    return StreamingResponse(
        _discovery_event_generator(load_statement_config_from_request(request)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/recommendations/stream")
async def recommendations_stream(request: Request, as_of: date = Query(...)) -> StreamingResponse:  # noqa: B008
    return StreamingResponse(
        _recommendations_event_generator(load_statement_config_from_request(request), as_of),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/providers/overrides")
async def get_provider_overrides(request: Request) -> dict[str, Any]:
    config = load_statement_config_from_request(request)
    db = Database(config.runtime.database_path)
    try:
        return db.get_provider_overrides()
    finally:
        db.close()


@router.post("/providers/{provider_key}/override")
async def set_provider_override(
    request: Request, provider_key: str, body: dict[str, Any]
) -> dict[str, Any]:
    config = load_statement_config_from_request(request)
    db = Database(config.runtime.database_path)
    try:
        db.set_provider_override(
            provider_key=provider_key,
            status=body.get("status", "confirmed"),
            display_name=body.get("display_name"),
            frequency_override=body.get("frequency_override"),
            anchor_day_override=body.get("anchor_day_override"),
            notes=body.get("notes"),
        )
        return {"status": "ok", "provider_key": provider_key}
    finally:
        db.close()


@router.delete("/providers/{provider_key}/override")
async def delete_provider_override(request: Request, provider_key: str) -> dict[str, Any]:
    config = load_statement_config_from_request(request)
    db = Database(config.runtime.database_path)
    try:
        db.delete_provider_override(provider_key)
        return {"status": "ok", "provider_key": provider_key}
    finally:
        db.close()


@router.get("/config/paperless-url")
async def get_paperless_url(request: Request) -> dict[str, Any]:
    config = load_statement_config_from_request(request)
    return {"paperless_url": config.source.paperless_url}


@router.get("/documents/{doc_id}/thumb")
async def document_thumbnail(request: Request, doc_id: int) -> Response:
    client = make_paperless_client(request, timeout=20.0)
    content, media_type = await client.get_document_thumbnail(doc_id)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/documents/{doc_id}/preview")
async def document_preview(request: Request, doc_id: int) -> Response:
    client = make_paperless_client(request, timeout=30.0)
    content, media_type = await client.get_document_preview(doc_id)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ---------------------------------------------------------------------------
# Statement series endpoints
# ---------------------------------------------------------------------------


def _get_db(request: Request) -> Database:
    config = load_statement_config_from_request(request)
    return Database(config.runtime.database_path)


def _get_deployment_id_or_none(request: Request) -> str | None:
    config = load_statement_config_from_request(request)
    settings = request.app.state.hub_settings
    paperless_url = settings.paperless_url or config.source.paperless_url
    return paperless_deployment_identity(paperless_url) if paperless_url else None


def _get_deployment_id(request: Request) -> str:
    deployment_id = _get_deployment_id_or_none(request)
    if deployment_id is None:
        raise_api_error(
            503,
            "paperless_not_configured",
            "A Paperless deployment URL is required for correspondent profiles.",
        )
    return deployment_id


def _get_policy_service(request: Request) -> CorrespondentPolicyService:
    config = load_statement_config_from_request(request)
    return CorrespondentPolicyService(
        Database(config.runtime.database_path),
        _get_deployment_id(request),
    )


def _raise_policy_error(exc: KeyError | ValueError) -> None:
    code = exc.args[0] if isinstance(exc, KeyError) and exc.args else "invalid_policy"
    if code in {
        "correspondent_profile_not_found",
        "statement_series_not_found",
        "acquisition_source_not_found",
        "document_expectation_not_found",
    }:
        raise_api_error(404, str(code), str(code).replace("_", " "))
    raise_api_error(409, "policy_conflict", str(exc))


async def _load_policy_analysis_documents(request: Request, *, correspondent_id: int | None = None):
    client = make_paperless_client(request, timeout=60.0)
    try:
        correspondents, tags, document_types = await client.fetch_all_metadata()
        raw_documents = await client.list_documents(correspondent_id=correspondent_id)
        return (
            correspondents,
            build_document_records(raw_documents, correspondents, tags, document_types),
        )
    finally:
        await client.aclose()


@router.post(
    "/correspondent-profiles/sync",
    response_model=CorrespondentSyncResult,
    summary="Synchronize Paperless correspondent identities",
)
async def sync_correspondent_profiles(request: Request) -> CorrespondentSyncResult:
    client = make_paperless_client(request, timeout=30.0)
    correspondents = await client.list_correspondents()
    service = _get_policy_service(request)
    try:
        return service.synchronize(correspondents)
    finally:
        service.close()


@router.post(
    "/correspondent-profiles/analyze",
    response_model=list[CorrespondentAnalysisResult],
    summary="Analyze all Paperless correspondents for policy suggestions",
)
async def analyze_correspondent_profiles(request: Request) -> list[CorrespondentAnalysisResult]:
    _, documents = await _load_policy_analysis_documents(request)
    service = _get_policy_service(request)
    try:
        return [
            service.analyze_profile(profile.correspondent_id, documents)
            for profile in service.list_profiles()
            if profile.lifecycle_status == "active"
        ]
    finally:
        service.close()


@router.post(
    "/correspondent-profiles/{correspondent_id}/analyze",
    response_model=CorrespondentAnalysisResult,
    summary="Analyze one Paperless correspondent for policy suggestions",
)
async def analyze_correspondent_profile(
    request: Request, correspondent_id: int
) -> CorrespondentAnalysisResult:
    service = _get_policy_service(request)
    try:
        profile = service.get_profile(correspondent_id)
        if profile is None:
            raise_api_error(
                404,
                "correspondent_profile_not_found",
                "Correspondent profile not found.",
            )
        _, documents = await _load_policy_analysis_documents(
            request, correspondent_id=correspondent_id
        )
        return service.analyze_profile(correspondent_id, documents)
    finally:
        service.close()


@router.get(
    "/correspondent-profiles",
    response_model=list[CorrespondentProfile],
    summary="List OWL-local correspondent profiles",
)
async def list_correspondent_profiles(request: Request) -> list[CorrespondentProfile]:
    service = _get_policy_service(request)
    try:
        return service.list_profiles()
    finally:
        service.close()


@router.get(
    "/correspondent-profiles/{correspondent_id}",
    response_model=CorrespondentProfile,
    summary="Get a correspondent profile",
)
async def get_correspondent_profile(
    request: Request, correspondent_id: int
) -> CorrespondentProfile:
    service = _get_policy_service(request)
    try:
        profile = service.get_profile(correspondent_id)
        if profile is None:
            raise_api_error(
                404,
                "correspondent_profile_not_found",
                "Correspondent profile not found.",
            )
        return profile
    finally:
        service.close()


@router.patch(
    "/correspondent-profiles/{correspondent_id}",
    response_model=CorrespondentProfile,
    summary="Update reviewed correspondent policy",
)
async def update_correspondent_profile(
    request: Request,
    correspondent_id: int,
    body: CorrespondentProfileUpdate,
) -> CorrespondentProfile:
    service = _get_policy_service(request)
    try:
        try:
            return service.update_profile(correspondent_id, body)
        except (KeyError, ValueError) as exc:
            _raise_policy_error(exc)
    finally:
        service.close()


@router.post(
    "/correspondent-profiles/{correspondent_id}/relink",
    response_model=CorrespondentProfile,
    summary="Relink an orphaned profile to a current Paperless correspondent",
)
async def relink_correspondent_profile(
    request: Request,
    correspondent_id: int,
    body: RelinkProfileRequest,
) -> CorrespondentProfile:
    client = make_paperless_client(request, timeout=30.0)
    correspondents = await client.list_correspondents()
    target = next(
        (item for item in correspondents if int(item["id"]) == body.correspondent_id),
        None,
    )
    if target is None:
        raise_api_error(
            404,
            "paperless_correspondent_not_found",
            "The relink target does not exist in the configured Paperless deployment.",
        )

    service = _get_policy_service(request)
    try:
        service.synchronize(correspondents)
        try:
            return service.relink_profile(
                correspondent_id,
                body.correspondent_id,
                str(target["name"]),
            )
        except (KeyError, ValueError) as exc:
            _raise_policy_error(exc)
    finally:
        service.close()


@router.get(
    "/correspondent-profiles/{correspondent_id}/expectations",
    response_model=list[DocumentExpectation],
    summary="List a correspondent's document expectations",
)
async def list_profile_expectations(
    request: Request, correspondent_id: int
) -> list[DocumentExpectation]:
    service = _get_policy_service(request)
    try:
        if service.get_profile(correspondent_id) is None:
            raise_api_error(
                404,
                "correspondent_profile_not_found",
                "Correspondent profile not found.",
            )
        return service.list_expectations(correspondent_id)
    finally:
        service.close()


@router.post(
    "/correspondent-profiles/{correspondent_id}/expectations",
    response_model=DocumentExpectation,
    status_code=201,
    summary="Create a reviewed document expectation",
)
async def create_document_expectation(
    request: Request,
    correspondent_id: int,
    body: DocumentExpectationCreate,
) -> DocumentExpectation:
    service = _get_policy_service(request)
    try:
        try:
            return service.create_expectation(correspondent_id, body)
        except (KeyError, ValueError) as exc:
            _raise_policy_error(exc)
    finally:
        service.close()


@router.patch(
    "/document-expectations/{expectation_id}",
    response_model=DocumentExpectation,
    summary="Update a document expectation",
)
async def update_document_expectation(
    request: Request,
    expectation_id: str,
    body: DocumentExpectationUpdate,
) -> DocumentExpectation:
    service = _get_policy_service(request)
    try:
        try:
            return service.update_expectation(expectation_id, body)
        except (KeyError, ValueError) as exc:
            _raise_policy_error(exc)
    finally:
        service.close()


@router.post(
    "/document-expectations/{expectation_id}/policy-preview",
    response_model=ExpectationPolicyPreview,
    summary="Preview confirmed expectation policy violations",
)
async def preview_document_expectation_policy(
    request: Request, expectation_id: str
) -> ExpectationPolicyPreview:
    client = make_paperless_client(request, timeout=60.0)
    service = _get_policy_service(request)
    try:
        correspondents, tags, document_types = await client.fetch_all_metadata()
        raw_documents = await client.list_documents()
        documents = build_document_records(raw_documents, correspondents, tags, document_types)
        try:
            return service.preview_expectation_policy(
                expectation_id,
                documents,
                tag_names=tags,
                document_type_names=document_types,
                preview_signing_key=client.token,
            )
        except (KeyError, ValueError) as exc:
            _raise_policy_error(exc)
    finally:
        service.close()
        await client.aclose()


@router.post(
    "/document-expectations/{expectation_id}/policy-apply",
    response_model=PolicyApplyResponse,
    summary="Apply selected expectation policy preview operations",
)
async def apply_document_expectation_policy(
    request: Request,
    expectation_id: str,
    body: PolicyApplyRequest,
) -> PolicyApplyResponse:
    client = make_paperless_client(request, timeout=60.0)
    service = _get_policy_service(request)
    try:
        expectation = service.get_expectation(expectation_id)
        if expectation is None:
            raise_api_error(
                404,
                "document_expectation_not_found",
                "Document expectation not found.",
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
        service.close()
        await client.aclose()


@router.post(
    "/policy-corrections/{event_id}/undo",
    response_model=PolicyOperationResult,
    summary="Undo one audited expectation policy correction",
)
async def undo_document_expectation_policy(
    request: Request,
    event_id: str,
    body: PolicyUndoRequest,
) -> PolicyOperationResult:
    client = make_paperless_client(request, timeout=60.0)
    try:
        _, tags, document_types = await client.fetch_all_metadata()
        return await undo_policy_operation(
            event_id=event_id,
            request=body,
            client=client,
            tag_names=tags,
            document_type_names=document_types,
            preview_signing_key=client.token,
        )
    finally:
        await client.aclose()


@router.get(
    "/document-expectations/{expectation_id}/missing-alert-eligibility",
    summary="Evaluate the missing-document alert contract",
)
async def get_missing_alert_eligibility(
    request: Request, expectation_id: str
) -> dict[str, str | bool]:
    service = _get_policy_service(request)
    try:
        try:
            eligible = service.can_emit_missing_alert(expectation_id)
        except (KeyError, ValueError) as exc:
            _raise_policy_error(exc)
        return {"expectation_id": expectation_id, "eligible": eligible}
    finally:
        service.close()


@router.post(
    "/acquisition-sources",
    response_model=AcquisitionSource,
    status_code=201,
    summary="Create a safe acquisition-source reference",
)
async def create_acquisition_source(
    request: Request, body: AcquisitionSourceCreate
) -> AcquisitionSource:
    service = _get_policy_service(request)
    try:
        return service.create_acquisition_source(body)
    finally:
        service.close()


@router.get(
    "/acquisition-sources",
    response_model=list[AcquisitionSource],
    summary="List safe acquisition-source references",
)
async def list_acquisition_sources(request: Request) -> list[AcquisitionSource]:
    service = _get_policy_service(request)
    try:
        return service.list_acquisition_sources()
    finally:
        service.close()


@router.patch(
    "/acquisition-sources/{source_id}",
    response_model=AcquisitionSource,
    summary="Update a safe acquisition-source reference",
)
async def update_acquisition_source(
    request: Request,
    source_id: str,
    body: AcquisitionSourceUpdate,
) -> AcquisitionSource:
    service = _get_policy_service(request)
    try:
        try:
            return service.update_acquisition_source(source_id, body)
        except (KeyError, ValueError) as exc:
            _raise_policy_error(exc)
    finally:
        service.close()


@router.get(
    "/legacy-provider-overrides/review",
    response_model=list[LegacyOverrideReviewItem],
    summary="List explicit legacy override migration outcomes",
)
async def list_legacy_provider_override_review(
    request: Request,
) -> list[LegacyOverrideReviewItem]:
    service = _get_policy_service(request)
    try:
        service.database.migrate_legacy_provider_overrides(service.deployment_id)
        return service.list_legacy_override_review()
    finally:
        service.close()


def _build_timeline(documents: list[dict]) -> list[dict]:
    """Build timeline entries from documents, calculating gap_before_days."""
    from datetime import date as date_type

    timeline = []
    prev_date: date_type | None = None
    for doc in sorted(documents, key=lambda d: d.get("statement_date") or ""):
        gap_days = None
        stmt_date_str = doc.get("statement_date")
        if stmt_date_str and prev_date:
            try:
                current = date_type.fromisoformat(stmt_date_str)
                gap_days = (current - prev_date).days
            except (ValueError, TypeError):
                pass
        if stmt_date_str:
            with contextlib.suppress(ValueError, TypeError):
                prev_date = date_type.fromisoformat(stmt_date_str)
        timeline.append(
            {
                "document_id": doc["document_id"],
                "title": doc.get("title"),
                "statement_date": stmt_date_str,
                "period_label": doc.get("period_label"),
                "account_hint": doc.get("account_hint"),
                "gap_before_days": gap_days,
            }
        )
    return timeline


def _record_correction_event(
    event_type: str,
    target_type: str,
    target_id: str,
    payload: dict | None = None,
) -> str | None:
    """Record a correction event in the triage database. Returns the event ID or None."""
    try:
        import json

        from doc_intelligence_hub.modules.triage.database import (
            CorrectionEvent,
        )
        from doc_intelligence_hub.modules.triage.database import (
            get_session as get_triage_session,
        )

        session = get_triage_session()
        try:
            event = CorrectionEvent(
                event_type=event_type,
                target_type=target_type,
                target_id=target_id,
                payload_json=json.dumps(payload or {}),
            )
            session.add(event)
            session.commit()
            return event.id
        finally:
            session.close()
    except Exception:
        pass  # Don't fail the main operation if triage DB is unavailable
    return None


async def _sync_to_paperless(
    request: Request,
    event_id: str | None,
    *,
    series_name: str | None = None,
    account_identifier: str | None = None,
    target_series_name: str | None = None,
) -> None:
    """Best-effort sync a correction event to Paperless-ngx."""
    if not event_id:
        return
    try:
        from doc_intelligence_hub.modules.triage.paperless_sync import sync_correction_event

        client = make_paperless_client(request, timeout=30.0)
        await sync_correction_event(
            client,
            event_id,
            series_name=series_name,
            account_identifier=account_identifier,
            target_series_name=target_series_name,
        )
    except Exception:
        pass  # Best-effort; don't fail the main operation


def _recalculate_frequency(db: Database, series_id: str) -> None:
    """Recalculate the frequency field for a series based on document date gaps."""
    from datetime import date as date_type
    from statistics import median

    documents = db.get_series_documents(series_id)
    dates: list[date_type] = []
    for doc in documents:
        stmt_date = doc.get("statement_date")
        if stmt_date:
            try:
                dates.append(date_type.fromisoformat(stmt_date))
            except (ValueError, TypeError):
                pass

    if len(dates) < 2:
        return  # Not enough data to determine frequency

    dates.sort()
    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    # Filter out zero/negative gaps (duplicates or bad data)
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return

    median_gap = median(gaps)
    if median_gap < 45:
        frequency = "monthly"
    elif median_gap <= 120:
        frequency = "quarterly"
    else:
        frequency = "annual"

    db.update_series_frequency(series_id, frequency)


def _resolve_triage_item_for_series(
    series_id: str, action: str, payload: dict | None = None
) -> None:
    """Resolve any pending triage queue items targeting this series."""
    try:
        from datetime import UTC, datetime

        from doc_intelligence_hub.modules.triage.database import (
            TriageQueueItem,
        )
        from doc_intelligence_hub.modules.triage.database import (
            get_session as get_triage_session,
        )

        session = get_triage_session()
        try:
            items = (
                session.query(TriageQueueItem)
                .filter(
                    TriageQueueItem.target_id == series_id,
                    TriageQueueItem.status == "pending",
                )
                .all()
            )
            for item in items:
                item.status = "resolved"
                item.resolved_at = datetime.now(UTC)
                item.resolved_action = action
            session.commit()
        finally:
            session.close()
    except Exception:
        pass


@router.get("/series")
async def list_series(
    request: Request,
    correspondent: str | None = Query(None),
    flagged: bool = Query(False),
) -> dict[str, Any]:
    """List statement series with optional filters."""
    db = _get_db(request)
    try:
        series = db.list_series(correspondent=correspondent, flagged=flagged)
        return {"series": series, "count": len(series)}
    finally:
        db.close()


@router.get("/series/{series_id}")
async def get_series_detail(request: Request, series_id: str) -> dict[str, Any]:
    """Get full detail view for a statement series.

    Looks up by series ID first; falls back to provider_key from the latest
    discovery run so that clicking a discovered provider in the UI works even
    before the provider has been promoted to a curated series.
    """
    db = _get_db(request)
    try:
        series = db.get_series(series_id)

        if series:
            series["statement_name"] = series["name"]
            documents = db.get_series_documents(series_id)
            similar = db.get_similar_series(series_id)
        else:
            # Fallback: try to resolve as a provider_key from discovery
            provider = db.get_provider_by_key(series_id)
            if not provider:
                raise_api_error(404, "series_not_found", f"Series '{series_id}' not found")

            # Build a synthetic series dict from the provider record
            import json as _json

            sample_ids = (
                _json.loads(provider["sample_document_ids"])
                if isinstance(provider["sample_document_ids"], str)
                else provider["sample_document_ids"]
            )
            series = {
                "id": provider["provider_key"],
                "name": provider.get("statement_name") or provider["provider_name"],
                "statement_name": provider.get("statement_name")
                or provider.get("normalized_title", "").title()
                or provider["provider_name"],
                "correspondent_id": provider.get("correspondent_id"),
                "correspondent_name": provider.get("provider_name", "Unknown"),
                "frequency": provider.get("frequency", "monthly"),
                "account_identifier": None,
                "manually_curated": False,
                "document_count": provider.get("document_count", 0),
                "first_seen": provider.get("first_seen"),
                "last_seen": provider.get("last_seen"),
                "created_at": None,
                # Extra provider metadata useful for the detail UI
                "source": "discovery",
                "normalized_title": provider.get("normalized_title"),
                "confidence": provider.get("confidence"),
                "pattern_type": provider.get("pattern_type"),
                "anchor_day": provider.get("anchor_day"),
                "variance_days": provider.get("variance_days"),
            }

            # Build minimal document list from sample_document_ids
            documents = [
                {"document_id": str(doc_id), "series_id": series_id} for doc_id in sample_ids
            ]
            similar = []

        timeline = _build_timeline(documents)

        # Derive anomaly indicators from the data
        anomaly_indicators: list[str] = []
        account_hints = {d.get("account_hint") for d in documents if d.get("account_hint")}
        if len(account_hints) > 1:
            anomaly_indicators.append(
                f"Multiple account numbers detected: {', '.join(sorted(account_hints))}"
            )
        anomaly_indicators.extend(
            f"Large gap of {entry['gap_before_days']} days before {entry.get('period_label', entry.get('statement_date', 'unknown'))}"
            for entry in timeline
            if entry.get("gap_before_days") and entry["gap_before_days"] > 60
        )

        # Build suggested split groups if multiple account hints exist
        suggested_split_groups: list[dict] = []
        if len(account_hints) > 1:
            for hint in sorted(account_hints):
                group_doc_ids = [
                    d["document_id"] for d in documents if d.get("account_hint") == hint
                ]
                suggested_split_groups.append(
                    {
                        "account_hint": hint,
                        "document_ids": group_doc_ids,
                    }
                )

        return {
            "series": series,
            "documents": documents,
            "timeline": timeline,
            "similar_series": similar,
            "anomaly_indicators": anomaly_indicators,
            "suggested_split_groups": suggested_split_groups,
        }
    finally:
        db.close()


@router.get("/series/{series_id}/timeline")
async def get_series_timeline(request: Request, series_id: str) -> dict[str, Any]:
    """Get timeline data for a series with gap indicators."""
    db = _get_db(request)
    try:
        series = db.get_series(series_id)
        if not series:
            raise_api_error(404, "series_not_found", f"Series '{series_id}' not found")

        documents = db.get_series_documents(series_id)
        timeline = _build_timeline(documents)
        return {"series_id": series_id, "timeline": timeline}
    finally:
        db.close()


@router.post("/series/{series_id}/split")
async def split_series(
    request: Request, series_id: str, body: SplitSeriesRequest
) -> dict[str, Any]:
    """Split documents from one series into a new series."""
    db = _get_db(request)
    try:
        series = db.get_series(series_id)
        if not series:
            raise_api_error(404, "series_not_found", f"Series '{series_id}' not found")

        if not body.document_ids:
            raise_api_error(400, "no_documents", "At least one document_id is required for split")

        # Get the documents being moved
        all_docs = db.get_series_documents(series_id)
        moving_docs = [d for d in all_docs if d["document_id"] in body.document_ids]

        if not moving_docs:
            raise_api_error(
                400, "documents_not_found", "None of the specified documents belong to this series"
            )

        # Create new series
        new_id = uuid.uuid4().hex[:12]
        db.create_series(
            series_id=new_id,
            name=body.new_series_name,
            correspondent_name=series["correspondent_name"],
            correspondent_id=series.get("correspondent_id"),
            frequency=series.get("frequency", "monthly"),
            account_identifier=body.account_identifier,
        )

        # Move documents to new series
        db.remove_documents_from_series(series_id, body.document_ids)
        db.add_documents_to_series(new_id, moving_docs)

        # Record override
        override_id = uuid.uuid4().hex[:12]
        db.save_series_override(
            override_id,
            series_id,
            "split_from",
            {
                "new_series_id": new_id,
                "new_series_name": body.new_series_name,
                "document_ids": body.document_ids,
                "account_identifier": body.account_identifier,
            },
        )

        # Record correction event and resolve triage item
        # Recalculate frequency for both series
        _recalculate_frequency(db, series_id)
        _recalculate_frequency(db, new_id)

        event_id = _record_correction_event(
            "series_split",
            "statement_series",
            series_id,
            {
                "new_series_id": new_id,
                "document_ids": body.document_ids,
            },
        )
        _resolve_triage_item_for_series(series_id, "split")

        result = {
            "status": "ok",
            "original_series": db.get_series(series_id),
            "new_series": db.get_series(new_id),
        }
    finally:
        db.close()

    await _sync_to_paperless(request, event_id, series_name=body.new_series_name)
    return result


@router.post("/series/merge")
async def merge_series(request: Request, body: MergeSeriesRequest) -> dict[str, Any]:
    """Merge source series into target series."""
    if body.source_series_id == body.target_series_id:
        raise_api_error(
            400,
            "same_series",
            "Source and target series must be different.",
        )
    deployment_id = _get_deployment_id_or_none(request)
    db = _get_db(request)
    try:
        source = db.get_series(body.source_series_id)
        target = db.get_series(body.target_series_id)

        if not source:
            raise_api_error(
                404, "series_not_found", f"Source series '{body.source_series_id}' not found"
            )
        if not target:
            raise_api_error(
                404, "series_not_found", f"Target series '{body.target_series_id}' not found"
            )
        source_correspondent = source.get("correspondent_id")
        target_correspondent = target.get("correspondent_id")
        if (
            source_correspondent is not None
            and target_correspondent is not None
            and source_correspondent != target_correspondent
        ):
            raise_api_error(
                409,
                "cross_correspondent_merge",
                "Statement series from different correspondents cannot be merged.",
            )
        if deployment_id is not None:
            try:
                db.validate_expectations_for_series_merge(
                    deployment_id,
                    body.source_series_id,
                    body.target_series_id,
                )
            except (KeyError, ValueError) as exc:
                _raise_policy_error(exc)

        # Move all source documents to target
        source_docs = db.get_series_documents(body.source_series_id)
        if source_docs:
            db.add_documents_to_series(body.target_series_id, source_docs)

        if deployment_id is not None:
            db.reconcile_expectations_for_series_merge(
                deployment_id,
                body.source_series_id,
                body.target_series_id,
            )

        # Record override on target
        override_id = uuid.uuid4().hex[:12]
        db.save_series_override(
            override_id,
            body.target_series_id,
            "merge_into",
            {
                "merged_series_id": body.source_series_id,
                "merged_series_name": source["name"],
                "document_count": len(source_docs),
            },
        )

        # Delete source series
        db.delete_series(body.source_series_id)

        # Mark target as manually curated
        db.update_series(body.target_series_id, manually_curated=True)

        # Recalculate frequency for the merged series
        _recalculate_frequency(db, body.target_series_id)

        # Record correction event and resolve triage items for both series
        event_id = _record_correction_event(
            "series_merge",
            "statement_series",
            body.target_series_id,
            {
                "source_series_id": body.source_series_id,
                "source_series_name": source["name"],
                "document_ids": [d["document_id"] for d in source_docs],
            },
        )
        _resolve_triage_item_for_series(body.source_series_id, "merge")
        _resolve_triage_item_for_series(body.target_series_id, "merge")

        target_name = target["name"]
        result = {
            "status": "ok",
            "merged_series": db.get_series(body.target_series_id),
            "documents_moved": len(source_docs),
        }
    finally:
        db.close()

    await _sync_to_paperless(request, event_id, target_series_name=target_name)
    return result


@router.post("/series/{series_id}/reassign")
async def reassign_document(
    request: Request, series_id: str, body: ReassignDocumentRequest
) -> dict[str, Any]:
    """Move a single document from one series to another."""
    db = _get_db(request)
    try:
        source = db.get_series(series_id)
        target = db.get_series(body.target_series_id)

        if not source:
            raise_api_error(404, "series_not_found", f"Source series '{series_id}' not found")
        if not target:
            raise_api_error(
                404, "series_not_found", f"Target series '{body.target_series_id}' not found"
            )

        # Get the document being moved
        all_docs = db.get_series_documents(series_id)
        doc = next((d for d in all_docs if d["document_id"] == body.document_id), None)
        if not doc:
            raise_api_error(
                400,
                "document_not_found",
                f"Document '{body.document_id}' not in series '{series_id}'",
            )

        # Move the document
        db.remove_documents_from_series(series_id, [body.document_id])
        db.add_documents_to_series(body.target_series_id, [doc])

        # Recalculate frequency for both affected series
        _recalculate_frequency(db, series_id)
        _recalculate_frequency(db, body.target_series_id)

        # Record override
        override_id = uuid.uuid4().hex[:12]
        db.save_series_override(
            override_id,
            series_id,
            "remove_doc",
            {
                "document_id": body.document_id,
                "target_series_id": body.target_series_id,
            },
        )

        event_id = _record_correction_event(
            "series_reassign",
            "statement_series",
            series_id,
            {
                "document_id": body.document_id,
                "target_series_id": body.target_series_id,
            },
        )

        target_name = target["name"]
        result = {
            "status": "ok",
            "source_series": db.get_series(series_id),
            "target_series": db.get_series(body.target_series_id),
        }
    finally:
        db.close()

    await _sync_to_paperless(request, event_id, target_series_name=target_name)
    return result


@router.post("/series/{series_id}/rename")
async def rename_series(
    request: Request, series_id: str, body: RenameSeriesRequest
) -> dict[str, Any]:
    """Rename a series and/or set its account identifier."""
    db = _get_db(request)
    try:
        series = db.get_series(series_id)
        if not series:
            raise_api_error(404, "series_not_found", f"Series '{series_id}' not found")

        old_name = series["name"]
        updated = db.update_series(
            series_id,
            name=body.name,
            account_identifier=body.account_identifier,
            manually_curated=True,
        )

        # Record override
        override_id = uuid.uuid4().hex[:12]
        db.save_series_override(
            override_id,
            series_id,
            "rename",
            {
                "old_name": old_name,
                "new_name": body.name,
                "account_identifier": body.account_identifier,
            },
        )

        # Get document IDs for Paperless sync
        doc_ids = [d["document_id"] for d in db.get_series_documents(series_id)]

        event_id = _record_correction_event(
            "series_rename",
            "statement_series",
            series_id,
            {
                "old_name": old_name,
                "new_name": body.name,
                "document_ids": doc_ids,
            },
        )
        # Note: rename does NOT resolve the triage item — the grouping issue
        # may still need a split or merge after renaming.

        result = {"status": "ok", "series": updated}
    finally:
        db.close()

    await _sync_to_paperless(
        request,
        event_id,
        series_name=body.name,
        account_identifier=body.account_identifier,
    )
    return result
