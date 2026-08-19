"""Read-only grouped launcher for Paperless and OWL document views."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from enum import Enum

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from doc_intelligence_hub.api.routers import make_paperless_client
from doc_intelligence_hub.core.document_views import (
    OWL_VIEW_DEFINITIONS,
    DocumentViewConfig,
    DocumentViewGroupConfig,
    ViewLaunch,
    ViewProvider,
)
from doc_intelligence_hub.core.paperless import PaperlessClient
from doc_intelligence_hub.core.resilience import (
    CircuitOpenError,
    PaperlessError,
    UnsupportedSavedViewError,
)
from doc_intelligence_hub.modules.triage.database import count_queue_items

router = APIRouter(prefix="/api/document-views", tags=["document-views"])
_MAX_CONCURRENT_PAPERLESS_COUNTS = 5


class ViewAvailability(str, Enum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"


class DocumentViewError(BaseModel):
    code: str
    message: str


class DocumentViewResponse(BaseModel):
    id: str
    label: str
    description: str | None
    provider: ViewProvider
    source_id: int | str
    launch: ViewLaunch
    href: str | None
    count: int | None
    availability: ViewAvailability
    checked_at: datetime
    error: DocumentViewError | None = None


class DocumentViewGroupResponse(BaseModel):
    id: str
    label: str
    description: str | None
    default_expanded: bool
    views: list[DocumentViewResponse]


class DocumentViewCatalogResponse(BaseModel):
    configured: bool
    generated_at: datetime
    groups: list[DocumentViewGroupResponse]


def _view_error(
    view: DocumentViewConfig,
    checked_at: datetime,
    code: str,
    message: str,
    *,
    availability: ViewAvailability = ViewAvailability.UNAVAILABLE,
    href: str | None = None,
) -> DocumentViewResponse:
    return DocumentViewResponse(
        id=view.id,
        label=view.label,
        description=view.description,
        provider=view.provider,
        source_id=view.resolved_source_id,
        launch=view.resolved_launch,
        href=href,
        count=None,
        availability=availability,
        checked_at=checked_at,
        error=DocumentViewError(code=code, message=message),
    )


async def _resolve_view(
    view: DocumentViewConfig,
    *,
    paperless: PaperlessClient | None,
    paperless_browser_url: str | None,
    paperless_setup_error: DocumentViewError | None,
    semaphore: asyncio.Semaphore,
) -> DocumentViewResponse:
    checked_at = datetime.now(UTC)
    if view.provider is ViewProvider.OWL:
        definition = OWL_VIEW_DEFINITIONS[str(view.source_id)]
        try:
            count = count_queue_items(item_type=definition.item_type)
        except SQLAlchemyError:
            return _view_error(
                view,
                checked_at,
                "owl_view_unavailable",
                "The OWL data source is temporarily unavailable for this view.",
                href=definition.route,
            )
        return DocumentViewResponse(
            id=view.id,
            label=view.label,
            description=view.description,
            provider=view.provider,
            source_id=view.resolved_source_id,
            launch=view.resolved_launch,
            href=definition.route,
            count=count,
            availability=ViewAvailability.READY,
            checked_at=checked_at,
        )

    href = (
        view.resolved_owl_route
        if view.resolved_launch is ViewLaunch.OWL
        else (
            f"{paperless_browser_url}/view/{view.source_id}"
            if paperless_browser_url is not None
            else None
        )
    )
    if paperless is None:
        error = paperless_setup_error or DocumentViewError(
            code="paperless_not_configured",
            message="Paperless connection settings are incomplete.",
        )
        return _view_error(view, checked_at, error.code, error.message, href=href)
    try:
        async with semaphore:
            count = await paperless.count_documents_for_saved_view(int(view.source_id))
    except UnsupportedSavedViewError:
        return _view_error(
            view,
            checked_at,
            "saved_view_unsupported",
            "This saved view uses filter rules OWL cannot safely count.",
            availability=ViewAvailability.UNSUPPORTED,
            href=href,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            code = "saved_view_forbidden"
            message = "The OWL Paperless token cannot access this saved view."
        elif exc.response.status_code == 404:
            code = "saved_view_not_found"
            message = "The configured Paperless saved view no longer exists."
        else:
            code = "paperless_request_failed"
            message = "Paperless could not return this view count."
        return _view_error(view, checked_at, code, message, href=href)
    except (CircuitOpenError, PaperlessError, httpx.RequestError):
        return _view_error(
            view,
            checked_at,
            "paperless_unavailable",
            "Paperless is temporarily unavailable for this view.",
            href=href,
        )
    return DocumentViewResponse(
        id=view.id,
        label=view.label,
        description=view.description,
        provider=view.provider,
        source_id=view.resolved_source_id,
        launch=view.resolved_launch,
        href=href,
        count=count,
        availability=ViewAvailability.READY,
        checked_at=checked_at,
    )


async def _resolve_group(
    group: DocumentViewGroupConfig,
    *,
    paperless: PaperlessClient | None,
    paperless_browser_url: str | None,
    paperless_setup_error: DocumentViewError | None,
    semaphore: asyncio.Semaphore,
) -> DocumentViewGroupResponse:
    views = await asyncio.gather(
        *(
            _resolve_view(
                view,
                paperless=paperless,
                paperless_browser_url=paperless_browser_url,
                paperless_setup_error=paperless_setup_error,
                semaphore=semaphore,
            )
            for view in group.views
        )
    )
    return DocumentViewGroupResponse(
        id=group.id,
        label=group.label,
        description=group.description,
        default_expanded=group.default_expanded,
        views=list(views),
    )


@router.get("", response_model=DocumentViewCatalogResponse)
async def list_document_views(request: Request) -> DocumentViewCatalogResponse:
    """Resolve the configured view allowlist without returning document metadata."""
    catalog = request.app.state.document_views_config
    has_paperless_views = any(
        view.provider is ViewProvider.PAPERLESS for group in catalog.groups for view in group.views
    )
    paperless: PaperlessClient | None = None
    paperless_setup_error: DocumentViewError | None = None
    if has_paperless_views:
        try:
            paperless = make_paperless_client(request, timeout=15.0)
        except HTTPException as exc:
            if exc.status_code != 503:
                raise
            paperless_setup_error = DocumentViewError(
                code="paperless_not_configured",
                message="Paperless connection settings are incomplete.",
            )

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_PAPERLESS_COUNTS)
    try:
        groups = list(
            await asyncio.gather(
                *(
                    _resolve_group(
                        group,
                        paperless=paperless,
                        paperless_browser_url=request.app.state.hub_settings.paperless_browser_url,
                        paperless_setup_error=paperless_setup_error,
                        semaphore=semaphore,
                    )
                    for group in catalog.groups
                )
            )
        )
    finally:
        if paperless is not None:
            await paperless.aclose()

    return DocumentViewCatalogResponse(
        configured=request.app.state.document_views_configured,
        generated_at=datetime.now(UTC),
        groups=groups,
    )


__all__ = ["router"]
