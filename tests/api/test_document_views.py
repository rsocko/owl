from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from doc_intelligence_hub.api.app import HubSettings
from doc_intelligence_hub.api.routers import document_views
from doc_intelligence_hub.core.document_views import DocumentViewCatalogConfig
from doc_intelligence_hub.core.resilience import PaperlessError, UnsupportedSavedViewError
from doc_intelligence_hub.modules.triage.database import create_queue_item


def _configure_catalog(app, *, paperless_launch: str = "paperless") -> None:
    paperless_view = {
        "id": "inbox",
        "label": "Inbox",
        "provider": "paperless",
        "source_id": 7,
        "launch": paperless_launch,
    }
    if paperless_launch == "owl":
        paperless_view["owl_route"] = "/triage?type=eob_match_review"
    app.state.document_views_configured = True
    app.state.document_views_config = DocumentViewCatalogConfig.model_validate(
        {
            "groups": [
                {
                    "id": "daily-review",
                    "label": "Daily Review",
                    "description": "Synthetic review group",
                    "default_expanded": True,
                    "views": [
                        paperless_view,
                        {
                            "id": "needs-review",
                            "label": "Needs Review",
                            "provider": "owl",
                            "source_id": "triage.pending",
                        },
                    ],
                }
            ]
        }
    )


def test_document_views_returns_mixed_provider_catalog(
    client,
    app,
    mock_paperless,
):
    _configure_catalog(app)
    mock_paperless.count_documents_for_saved_view.return_value = 12
    for index in range(3):
        create_queue_item(
            item_type="eob_match_review",
            source="synthetic_test",
            target_type="eob_match",
            target_id=str(index),
        )

    response = client.get("/api/document-views")

    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is True
    assert data["generated_at"]
    assert data["groups"][0]["default_expanded"] is True
    paperless, owl = data["groups"][0]["views"]
    assert paperless == {
        "id": "inbox",
        "label": "Inbox",
        "description": None,
        "provider": "paperless",
        "source_id": 7,
        "launch": "paperless",
        "href": "https://paperless.browser.test/view/7",
        "count": 12,
        "availability": "ready",
        "checked_at": paperless["checked_at"],
        "error": None,
    }
    assert owl["provider"] == "owl"
    assert owl["source_id"] == "triage.pending"
    assert owl["href"] == "/triage"
    assert owl["count"] == 3
    assert owl["availability"] == "ready"
    assert "documents" not in response.text
    assert "document_title" not in response.text


def test_document_views_keeps_paperless_launch_when_count_is_unsupported(
    client,
    app,
    mock_paperless,
):
    _configure_catalog(app)
    mock_paperless.count_documents_for_saved_view.side_effect = UnsupportedSavedViewError(
        "Unsupported synthetic rule"
    )

    response = client.get("/api/document-views")

    assert response.status_code == 200
    paperless = response.json()["groups"][0]["views"][0]
    assert paperless["count"] is None
    assert paperless["availability"] == "unsupported"
    assert paperless["error"]["code"] == "saved_view_unsupported"
    assert paperless["href"] == "https://paperless.browser.test/view/7"
    owl = response.json()["groups"][0]["views"][1]
    assert owl["availability"] == "ready"


def test_document_views_isolates_malformed_paperless_responses(
    client,
    app,
    mock_paperless,
):
    _configure_catalog(app)
    mock_paperless.count_documents_for_saved_view.side_effect = PaperlessError(
        "Synthetic malformed JSON"
    )

    response = client.get("/api/document-views")

    assert response.status_code == 200
    paperless, owl = response.json()["groups"][0]["views"]
    assert paperless["availability"] == "unavailable"
    assert paperless["error"]["code"] == "paperless_unavailable"
    assert owl["availability"] == "ready"


def test_document_views_resolves_groups_concurrently_with_a_global_bound(
    client,
    app,
    mock_paperless,
):
    app.state.document_views_configured = True
    app.state.document_views_config = DocumentViewCatalogConfig.model_validate(
        {
            "groups": [
                {
                    "id": f"group-{index}",
                    "label": f"Group {index}",
                    "views": [
                        {
                            "id": f"view-{index}",
                            "label": f"View {index}",
                            "provider": "paperless",
                            "source_id": index + 1,
                        }
                    ],
                }
                for index in range(6)
            ]
        }
    )
    active = 0
    max_active = 0

    async def count_view(_view_id):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return 1

    mock_paperless.count_documents_for_saved_view.side_effect = count_view

    response = client.get("/api/document-views")

    assert response.status_code == 200
    assert max_active == 5
    assert [group["id"] for group in response.json()["groups"]] == [
        f"group-{index}" for index in range(6)
    ]


def test_document_views_reports_unconfigured_empty_catalog(client):
    response = client.get("/api/document-views")

    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert response.json()["groups"] == []


def test_document_views_isolates_typed_owl_database_failures(
    client,
    app,
    mock_paperless,
    monkeypatch,
):
    _configure_catalog(app)
    mock_paperless.count_documents_for_saved_view.return_value = 4

    def fail_count(*, item_type):
        raise OperationalError("synthetic count", {}, RuntimeError("database unavailable"))

    monkeypatch.setattr(document_views, "count_queue_items", fail_count)

    response = client.get("/api/document-views")

    assert response.status_code == 200
    paperless, owl = response.json()["groups"][0]["views"]
    assert paperless["availability"] == "ready"
    assert owl["availability"] == "unavailable"
    assert owl["error"]["code"] == "owl_view_unavailable"
    assert owl["href"] == "/triage"


def test_document_views_does_not_expose_service_url_as_browser_link(
    client,
    app,
    mock_paperless,
):
    _configure_catalog(app)
    app.state.hub_settings.paperless_browser_url = None
    mock_paperless.count_documents_for_saved_view.return_value = 2

    response = client.get("/api/document-views")

    assert response.status_code == 200
    paperless = response.json()["groups"][0]["views"][0]
    assert paperless["availability"] == "ready"
    assert paperless["href"] is None
    assert "http://paperless.test" not in response.text


def test_document_views_preserves_owl_launch_when_paperless_is_not_configured(
    client,
    app,
    monkeypatch,
):
    _configure_catalog(app, paperless_launch="owl")

    def fail_client(*args, **kwargs):
        raise HTTPException(status_code=503, detail="synthetic missing configuration")

    monkeypatch.setattr(document_views, "make_paperless_client", fail_client)

    response = client.get("/api/document-views")

    assert response.status_code == 200
    paperless = response.json()["groups"][0]["views"][0]
    assert paperless["availability"] == "unavailable"
    assert paperless["error"]["code"] == "paperless_not_configured"
    assert paperless["href"] == "/triage?type=eob_match_review"


def test_paperless_browser_url_requires_absolute_http_url():
    with pytest.raises(ValidationError, match="absolute HTTP"):
        HubSettings(paperless_browser_url="javascript:alert(1)")

    settings = HubSettings(paperless_browser_url="https://paperless.example.test/")
    assert settings.paperless_browser_url == "https://paperless.example.test"
