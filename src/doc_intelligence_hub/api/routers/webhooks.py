"""Webhook endpoints for n8n and external automation integration.

Provides registration and callback endpoints so external workflow engines
(primarily n8n) can subscribe to statement-tracking events and report back
when statements are found.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from doc_intelligence_hub.api.routers import load_statement_config_from_request
from doc_intelligence_hub.core.webhooks import (
    VALID_EVENT_TYPES,
    WebhookDB,
    dispatch_to_subscribers,
    get_webhook_db,
)
from doc_intelligence_hub.modules.statements.correspondent_models import (
    paperless_deployment_identity,
)
from doc_intelligence_hub.modules.statements.database import Database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

_ALLOWED_EVENT_TYPES = sorted(VALID_EVENT_TYPES | {"*"})


def _get_db() -> WebhookDB:
    db_path = os.environ.get("WEBHOOK_DB_PATH", "data/webhook_log.db")
    db = get_webhook_db(db_path)
    db.connect()
    return db


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class WebhookSubscriptionRequest(BaseModel):
    """Register a webhook subscription."""

    url: str = Field(..., description="The callback URL to POST events to.", max_length=2048)
    event_type: str = Field(
        default="*",
        description=(
            f"Event type to subscribe to. Valid values: {', '.join(_ALLOWED_EVENT_TYPES)}"
        ),
    )
    description: str | None = Field(
        default=None,
        description="Human-readable description of this subscription.",
        max_length=500,
    )


class WebhookSubscriptionResponse(BaseModel):
    id: int
    event_type: str
    url: str
    description: str | None = None
    active: bool = True


class StatementFoundRequest(BaseModel):
    """Payload n8n sends when it has retrieved a missing statement."""

    expectation_id: str | None = Field(
        default=None,
        description="The stable OWL expectation ID from a missing-statement alert.",
    )
    provider_key: str | None = Field(
        default=None,
        description="Legacy provider key accepted only when it resolves unambiguously.",
    )
    expected_date: str = Field(..., description="The expected statement date (YYYY-MM-DD).")
    document_id: str | None = Field(
        default=None,
        description="Paperless document ID if the statement was ingested.",
    )
    source: str = Field(
        default="n8n",
        description="Source system that found the statement.",
    )
    notes: str | None = None

    @model_validator(mode="after")
    def require_identity(self) -> StatementFoundRequest:
        if not self.expectation_id and not self.provider_key:
            raise ValueError("expectation_id or provider_key is required")
        return self


class StatementMissingNotification(BaseModel):
    """Payload for manually triggering a missing-statement webhook."""

    provider_key: str
    provider_name: str
    expected_date: str
    status: Literal["missing", "overdue"] = Field(
        default="missing", description="'missing' or 'overdue'"
    )
    priority: int = Field(default=5, ge=1, le=10)
    days_late: int = 0


# ---------------------------------------------------------------------------
# Subscription management endpoints
# ---------------------------------------------------------------------------


@router.get("/subscriptions", summary="List webhook subscriptions")
async def list_subscriptions(
    event_type: str | None = Query(default=None, description="Filter by event type"),
    include_inactive: bool = Query(default=False, description="Include inactive subscriptions"),
) -> list[dict[str, Any]]:
    db = _get_db()
    try:
        return db.list_subscriptions(event_type=event_type, active_only=not include_inactive)
    finally:
        db.close()


@router.post("/subscriptions", summary="Register a webhook subscription", status_code=201)
async def create_subscription(
    body: WebhookSubscriptionRequest,
) -> WebhookSubscriptionResponse:
    if body.event_type not in _ALLOWED_EVENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_event_type",
                "message": f"event_type must be one of: {', '.join(_ALLOWED_EVENT_TYPES)}",
            },
        )
    db = _get_db()
    try:
        sub_id = db.add_subscription(
            event_type=body.event_type,
            url=body.url,
            description=body.description,
        )
        return WebhookSubscriptionResponse(
            id=sub_id,
            event_type=body.event_type,
            url=body.url,
            description=body.description,
        )
    finally:
        db.close()


@router.delete("/subscriptions/{subscription_id}", summary="Remove a webhook subscription")
async def delete_subscription(subscription_id: int) -> dict[str, Any]:
    db = _get_db()
    try:
        removed = db.remove_subscription(subscription_id)
    finally:
        db.close()
    if not removed:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {"status": "removed", "id": subscription_id}


@router.patch(
    "/subscriptions/{subscription_id}/toggle",
    summary="Enable or disable a subscription",
)
async def toggle_subscription(
    subscription_id: int,
    active: bool = Query(..., description="Set to true to enable, false to disable"),
) -> dict[str, Any]:
    db = _get_db()
    try:
        updated = db.set_subscription_active(subscription_id, active)
    finally:
        db.close()
    if not updated:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {"status": "updated", "id": subscription_id, "active": active}


# ---------------------------------------------------------------------------
# Webhook trigger / callback endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/statement-missing",
    summary="Notify subscribers of a missing statement",
    description=(
        "Manually trigger a missing-statement webhook to all subscribers. "
        "This is also called internally by the recommendations engine."
    ),
)
async def notify_statement_missing(
    body: StatementMissingNotification,
) -> dict[str, Any]:
    db = _get_db()
    try:
        event_type = f"statement.{body.status}"

        # Check de-duplication state
        if db.was_already_alerted(body.provider_key, body.expected_date, event_type):
            return {
                "status": "already_alerted",
                "provider_key": body.provider_key,
                "expected_date": body.expected_date,
            }

        payload = body.model_dump()

        # Include default n8n webhook URL if configured
        extra_urls: list[str] = []
        n8n_url = os.environ.get("N8N_WEBHOOK_URL")
        if n8n_url:
            extra_urls.append(n8n_url)

        results = await dispatch_to_subscribers(event_type, payload, db, extra_urls=extra_urls)

        # Mark as alerted regardless of delivery outcome to prevent repeated
        # fire-and-forget attempts on every cycle
        db.mark_alerted(body.provider_key, body.expected_date, event_type)

        return {
            "status": "dispatched",
            "event_type": event_type,
            "provider_key": body.provider_key,
            "expected_date": body.expected_date,
            "deliveries": {url: ok for url, ok in results.items()},
        }
    finally:
        db.close()


@router.post(
    "/statement-found",
    summary="Callback: a missing statement was found",
    description=(
        "n8n (or another automation) calls this endpoint to report that "
        "a previously-missing statement has been successfully retrieved."
    ),
)
async def statement_found(request: Request, body: StatementFoundRequest) -> dict[str, Any]:
    config = load_statement_config_from_request(request)
    settings = request.app.state.hub_settings
    paperless_url = settings.paperless_url or config.source.paperless_url
    canonical_key = body.expectation_id or body.provider_key
    resolved_expectation_id = body.expectation_id
    if paperless_url:
        policy_db = Database(config.runtime.database_path)
        try:
            deployment_id = paperless_deployment_identity(paperless_url)
            if body.expectation_id:
                expectation = policy_db.get_document_expectation(deployment_id, body.expectation_id)
                if expectation is None:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "code": "document_expectation_not_found",
                            "message": "Document expectation not found.",
                        },
                    )
                canonical_key = expectation.id
            elif body.provider_key:
                resolution = policy_db.resolve_expectation_identity(
                    deployment_id, body.provider_key
                )
                if resolution.status == "ambiguous":
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "ambiguous_legacy_provider_key",
                            "message": "Legacy provider key requires explicit review.",
                        },
                    )
                if resolution.status == "resolved":
                    canonical_key = resolution.canonical_key
                    resolved_expectation_id = resolution.canonical_key
        finally:
            policy_db.close()

    assert canonical_key is not None
    db = _get_db()
    try:
        # Mark a resolved tombstone so future recommendation cycles don't
        # re-alert for this provider+date, even if the recommendation engine
        # still lists it as missing (ingestion may not have happened yet).
        db.mark_alerted(canonical_key, body.expected_date, "statement.found")

        payload = body.model_dump()
        payload["provider_key"] = canonical_key
        payload["expectation_id"] = resolved_expectation_id

        extra_urls: list[str] = []
        n8n_url = os.environ.get("N8N_WEBHOOK_URL")
        if n8n_url:
            extra_urls.append(n8n_url)

        results = await dispatch_to_subscribers(
            "statement.found", payload, db, extra_urls=extra_urls
        )

        return {
            "status": "acknowledged",
            "provider_key": canonical_key,
            "expectation_id": resolved_expectation_id,
            "expected_date": body.expected_date,
            "document_id": body.document_id,
            "deliveries": {url: ok for url, ok in results.items()},
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Delivery log
# ---------------------------------------------------------------------------


@router.get("/logs", summary="View recent webhook delivery logs")
async def get_webhook_logs(
    limit: int = Query(default=50, ge=1, le=500, description="Number of log entries to return"),
) -> list[dict[str, Any]]:
    db = _get_db()
    try:
        return db.get_recent_logs(limit=limit)
    finally:
        db.close()
