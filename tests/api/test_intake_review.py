"""Tests for the unified Intake Review API."""

from __future__ import annotations

from unittest.mock import AsyncMock

from doc_intelligence_hub.api.routers import intake_review
from doc_intelligence_hub.core.paperless.client import PaperlessPage


def _document(**overrides):
    return {
        "id": 42,
        "title": "Utility bill",
        "correspondent": 1,
        "document_type": 2,
        "storage_path": 1,
        "tags": [1, 2],
        "created": "2026-10-14",
        "added": "2026-10-15",
        "modified": "2026-10-16T12:00:00Z",
        "custom_fields": [{"field": 9, "value": "A-123"}],
        **overrides,
    }


def test_list_intake_documents_uses_inbox_tag_independent_of_action_queue_settings(
    client, mock_paperless
):
    settings_response = client.put(
        "/api/queue/settings",
        json={"scan_mode": "tags", "monitor_tags": ["Medical"]},
    )
    assert settings_response.status_code == 200
    mock_paperless.resolve_tag_ids.return_value = [1]
    mock_paperless.list_document_page.return_value = PaperlessPage(
        results=(_document(),),
        next_cursor="2",
        total_count=213,
    )

    response = client.get("/api/intake/documents")

    assert response.status_code == 200
    assert response.json()["documents"][0]["id"] == 42
    assert response.json()["total"] == 213
    assert response.json()["next_cursor"] == "2"
    mock_paperless.resolve_tag_ids.assert_awaited_once_with(["Inbox"])
    mock_paperless.list_document_page.assert_awaited_once_with(
        page_size=100,
        cursor=None,
        scope_params={"tags__id__in": "1", "ordering": "-id"},
    )
    assert client.get("/api/queue/settings").json()["monitor_tags"] == ["Medical"]


def test_list_intake_documents_reports_missing_inbox_tag(client, mock_paperless):
    mock_paperless.resolve_tag_ids.return_value = []

    response = client.get("/api/intake/documents")

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "paperless_inbox_tag_missing",
        "message": (
            "Inbox Review requires a Paperless tag named 'Inbox'. "
            "Create the tag in Paperless before using Inbox Review."
        ),
    }
    mock_paperless.list_document_page.assert_not_awaited()


def test_list_intake_documents_applies_search_and_missing_type_to_paperless(client, mock_paperless):
    mock_paperless.resolve_tag_ids.return_value = [1]
    mock_paperless.list_document_page.return_value = PaperlessPage(
        results=(),
        next_cursor=None,
        total_count=0,
    )

    response = client.get("/api/intake/documents?search=utility&queue_filter=missing_type")

    assert response.status_code == 200
    mock_paperless.list_document_page.assert_awaited_once_with(
        page_size=100,
        cursor=None,
        scope_params={
            "tags__id__in": "1",
            "ordering": "-id",
            "query": "utility",
            "document_type__isnull": "true",
        },
    )


def test_document_detail_combines_metadata_suggestions_actions_and_ocr(client, mock_paperless):
    mock_paperless.get_document.return_value = _document()
    mock_paperless.get_document_suggestions.return_value = {
        "correspondents": [1, 2],
        "document_types": [3],
        "tags": [1, 2],
    }
    mock_paperless.get_document_ai_suggestions.return_value = {
        "suggested_title": "October utility bill",
        "suggested_tags": ["Utilities"],
    }
    mock_paperless.get_document_content.return_value = "Searchable OCR text"

    response = client.get("/api/intake/documents/42")

    assert response.status_code == 200
    payload = response.json()
    assert payload["document"]["correspondent_value"]["name"] == "UnitedHealth"
    assert payload["ocr"]["text"] == "Searchable OCR text"
    assert payload["suggestion_sources"]["paperless_ml"]["available"] is True
    assert {(suggestion["field"], suggestion["name"]) for suggestion in payload["suggestions"]} == {
        ("correspondent", "Aetna"),
        ("document_type", "Letter"),
        ("title", "October utility bill"),
    }
    assert payload["paperless_url"] == "https://paperless.browser.test/documents/42/details"


def test_tag_suggestions_only_use_existing_paperless_tags(client, mock_paperless):
    intake_review._taxonomy_cache = None
    mock_paperless.get_document.return_value = _document(tags=[1])
    mock_paperless.list_tags.return_value = [
        {"id": 1, "name": "Inbox", "color": "#1f6feb"},
        {"id": 2, "name": "Utilities", "color": "#fbca04"},
    ]
    mock_paperless.get_document_suggestions.return_value = {}
    mock_paperless.get_document_ai_suggestions.return_value = {
        "suggested_tags": ["Utilities", "Brand New Tag"],
    }

    response = client.get("/api/intake/documents/42/suggestions?refresh=true")

    assert response.status_code == 200
    assert response.json()["suggestions"] == [
        {
            "field": "tags",
            "source": "paperless_ai",
            "id": 2,
            "name": "Utilities",
            "color": "#fbca04",
            "colour": "#fbca04",
        }
    ]


def test_core_document_load_does_not_wait_for_suggestions_or_ocr(client, mock_paperless):
    mock_paperless.get_document.return_value = _document()

    response = client.get("/api/intake/documents/42/core")

    assert response.status_code == 200
    assert response.json()["document"]["title"] == "Utility bill"
    mock_paperless.get_document_suggestions.assert_not_awaited()
    mock_paperless.get_document_ai_suggestions.assert_not_awaited()
    mock_paperless.get_document_content.assert_not_awaited()


def test_relationship_candidates_suggest_same_recurring_series(client, mock_paperless):
    intake_review._taxonomy_cache = None
    mock_paperless.get_document.return_value = _document()
    mock_paperless.list_documents.return_value = [
        _document(),
        _document(id=77, title="September utility bill", created="2026-09-14"),
    ]

    response = client.get("/api/intake/documents/42/relationship-candidates")

    assert response.status_code == 200
    candidate = response.json()["candidates"][0]
    assert candidate["document"]["id"] == 77
    assert candidate["suggested"] is True
    assert candidate["recommendation"]["relationship_type"] == "same_sequence"
    assert candidate["recommendation"]["reason_codes"] == [
        "correspondent_match",
        "document_type_match",
    ]
    mock_paperless.list_documents.assert_awaited_once_with(correspondent_id=1, limit=12)


def test_suggestions_are_cached_for_the_document_revision(client, mock_paperless):
    mock_paperless.get_document.return_value = _document()
    mock_paperless.get_document_suggestions.return_value = {"correspondents": [2]}
    mock_paperless.get_document_ai_suggestions.return_value = {}

    first = client.get("/api/intake/documents/42/suggestions")
    second = client.get("/api/intake/documents/42/suggestions")

    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert second.status_code == 200
    assert second.json()["cached"] is True
    mock_paperless.get_document_suggestions.assert_awaited_once_with(42)
    mock_paperless.get_document_ai_suggestions.assert_awaited_once_with(42)


def test_queue_resolves_names_and_includes_action_state(client, mock_paperless):
    mock_paperless.get_document.return_value = _document()
    client.post(
        "/api/intake/documents/42/actions",
        json={"action_type": "PAY", "title": "Pay utility bill", "amount": 84.2},
    )
    mock_paperless.resolve_tag_ids.return_value = [1]
    mock_paperless.list_document_page.return_value = PaperlessPage(
        results=(_document(),),
        next_cursor=None,
        total_count=1,
    )

    response = client.get("/api/intake/documents?queue_filter=actions")

    assert response.status_code == 200
    item = response.json()["documents"][0]
    assert item["correspondent_name"] == "UnitedHealth"
    assert item["document_type_name"] == "Bill"
    assert item["thumbnail_url"].endswith("/42/thumb")
    assert item["pending_action_count"] == 1
    mock_paperless.list_document_page.assert_awaited_once_with(
        page_size=100,
        cursor=None,
        scope_params={
            "tags__id__in": "1",
            "ordering": "-id",
            "id__in": "42",
        },
    )


def test_core_hides_operational_fields_and_exposes_editable_document_fields(client, mock_paperless):
    intake_review._taxonomy_cache = None
    mock_paperless.get_document.return_value = _document(
        custom_fields=[
            {"field": 9, "value": "A-123"},
            {"field": 10, "value": "84.20"},
            {"field": 11, "value": "pending"},
        ]
    )
    mock_paperless.list_custom_fields.return_value = [
        {"id": 9, "name": "Account Identifier", "data_type": "string"},
        {"id": 10, "name": "Document Amount", "data_type": "monetary"},
        {"id": 11, "name": "Action Status", "data_type": "string"},
    ]

    response = client.get("/api/intake/documents/42/core")

    assert response.status_code == 200
    fields = response.json()["document"]["review_fields"]
    assert [(field["name"], field["field_key"], field["editable"]) for field in fields] == [
        ("Account Identifier", "account_identifier", True),
        ("Document Amount", "document_amount", True),
    ]
    assert fields[0]["value"] == "A-123"
    assert fields[0]["audit"] is False


def test_core_exposes_unset_editable_document_fields(client, mock_paperless):
    intake_review._taxonomy_cache = None
    mock_paperless.get_document.return_value = _document(custom_fields=[])
    mock_paperless.list_custom_fields.return_value = [
        {"id": 9, "name": "Account Identifier", "data_type": "string"},
        {"id": 10, "name": "Document Amount", "data_type": "monetary"},
        {"id": 11, "name": "Document Due Date", "data_type": "date"},
        {"id": 12, "name": "Action Status", "data_type": "string"},
        {"id": 13, "name": "Household Note", "data_type": "string"},
    ]

    response = client.get("/api/intake/documents/42/core")

    assert response.status_code == 200
    fields = response.json()["document"]["review_fields"]
    assert [(field["name"], field["value"], field["editable"]) for field in fields] == [
        ("Account Identifier", None, True),
        ("Document Amount", None, True),
        ("Document Due Date", None, True),
    ]


def test_refresh_returns_updated_document_metadata(client, mock_paperless, monkeypatch):
    intake_review._taxonomy_cache = None
    mock_paperless.get_document.side_effect = [
        _document(),
        _document(
            modified="2026-10-17T08:00:00Z",
            custom_fields=[
                {"field": 9, "value": "A-123"},
                {"field": 10, "value": 520.0},
            ],
        ),
    ]
    mock_paperless.list_custom_fields.return_value = [
        {"id": 9, "name": "Account Identifier", "data_type": "string"},
        {"id": 10, "name": "Document Amount", "data_type": "monetary"},
    ]
    monkeypatch.setattr(
        intake_review,
        "run_pipeline",
        AsyncMock(return_value={"status": "completed"}),
    )
    monkeypatch.setattr(
        intake_review,
        "_suggestions_for_document",
        AsyncMock(return_value=([], {}, False)),
    )
    monkeypatch.setattr(
        intake_review,
        "_action_and_quality_payload",
        lambda document_id: {
            "actions": [],
            "analysis": {"available": True},
            "ocr_quality": {
                "quality_score": None,
                "quality_band": "unknown",
                "poor_quality": False,
            },
        },
    )

    response = client.post(
        "/api/intake/documents/42/refresh",
        params={"expected_modified": "2026-10-16T12:00:00Z"},
    )

    assert response.status_code == 200
    refreshed = response.json()["core"]["document"]
    assert refreshed["modified"] == "2026-10-17T08:00:00Z"
    assert refreshed["review_fields"][1]["name"] == "Document Amount"
    assert refreshed["review_fields"][1]["value"] == 520.0


def test_save_rejects_external_edit_conflict(client, mock_paperless):
    mock_paperless.get_document.return_value = _document(modified="2026-10-17T09:00:00Z")

    response = client.put(
        "/api/intake/documents/42",
        json={
            "expected_modified": "2026-10-16T12:00:00Z",
            "metadata": {"title": "Updated title"},
            "disposition": "save",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "source_revision_conflict"
    mock_paperless.update_document.assert_not_awaited()


def test_file_only_removes_inbox_without_resolving_actions(client, mock_paperless):
    settings_response = client.put(
        "/api/queue/settings",
        json={"scan_mode": "tags", "monitor_tags": ["Medical"]},
    )
    assert settings_response.status_code == 200
    mock_paperless.get_document.return_value = _document()

    response = client.put(
        "/api/intake/documents/42",
        json={
            "expected_modified": "2026-10-16T12:00:00Z",
            "metadata": {"title": "Filed utility bill", "tags": [1, 2]},
            "disposition": "file_only",
        },
    )

    assert response.status_code == 200
    assert response.json()["filed"] is True
    mock_paperless.update_document.assert_awaited_once_with(
        42, {"title": "Filed utility bill", "tags": [2]}
    )
    mock_paperless.resolve_tag_ids.assert_awaited_once_with(["Inbox"])
    assert client.get("/api/queue/settings").json()["monitor_tags"] == ["Medical"]


def test_file_only_reports_missing_inbox_tag_without_saving(client, mock_paperless):
    mock_paperless.get_document.return_value = _document()
    mock_paperless.resolve_tag_ids.return_value = []

    response = client.put(
        "/api/intake/documents/42",
        json={
            "expected_modified": "2026-10-16T12:00:00Z",
            "metadata": {"title": "Filed utility bill"},
            "disposition": "file_only",
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "paperless_inbox_tag_missing"
    mock_paperless.update_document.assert_not_awaited()


def test_save_merges_editable_custom_fields(client, mock_paperless):
    mock_paperless.get_document.return_value = _document(
        custom_fields=[
            {"field": 9, "value": "A-123"},
            {"field": 10, "value": 80.0},
        ]
    )
    mock_paperless.list_custom_fields.return_value = [
        {"id": 9, "name": "Account Identifier", "data_type": "string"},
        {"id": 10, "name": "Document Amount", "data_type": "float"},
    ]

    response = client.put(
        "/api/intake/documents/42",
        json={
            "expected_modified": "2026-10-16T12:00:00Z",
            "metadata": {"custom_fields": [{"field": 10, "value": 84.2}]},
            "disposition": "save",
        },
    )

    assert response.status_code == 200
    mock_paperless.update_document.assert_awaited_once_with(
        42,
        {
            "custom_fields": [
                {"field": 9, "value": "A-123"},
                {"field": 10, "value": 84.2},
            ]
        },
    )


def test_save_updates_exact_classified_account_identifier(client, mock_paperless):
    mock_paperless.get_document.return_value = _document(
        custom_fields=[{"field": 9, "value": "ending 2025"}]
    )
    mock_paperless.list_custom_fields.return_value = [
        {"id": 9, "name": "Account Identifier", "data_type": "string"},
    ]

    response = client.put(
        "/api/intake/documents/42",
        json={
            "expected_modified": "2026-10-16T12:00:00Z",
            "metadata": {
                "custom_fields": [
                    {
                        "field": 9,
                        "value": "ACCT-2025-8675309",
                        "identifier_class": "service_account",
                    }
                ]
            },
            "disposition": "save",
        },
    )

    assert response.status_code == 200
    mock_paperless.update_document.assert_awaited_once_with(
        42,
        {"custom_fields": [{"field": 9, "value": "ACCT-2025-8675309"}]},
    )


def test_save_requires_account_identifier_class(client, mock_paperless):
    mock_paperless.get_document.return_value = _document(
        custom_fields=[{"field": 9, "value": "ending 2025"}]
    )
    mock_paperless.list_custom_fields.return_value = [
        {"id": 9, "name": "Account Identifier", "data_type": "string"},
    ]

    response = client.put(
        "/api/intake/documents/42",
        json={
            "expected_modified": "2026-10-16T12:00:00Z",
            "metadata": {"custom_fields": [{"field": 9, "value": "ACCT-2025-8675309"}]},
            "disposition": "save",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "account_identifier_class_required"
    mock_paperless.update_document.assert_not_awaited()


def test_confirm_and_file_checks_action_revision_before_paperless_write(client, mock_paperless):
    mock_paperless.get_document.return_value = _document()
    created = client.post(
        "/api/intake/documents/42/actions",
        json={"action_type": "PAY", "title": "Pay utility bill", "amount": 84.2},
    ).json()
    mock_paperless.update_document.reset_mock()

    response = client.put(
        "/api/intake/documents/42",
        json={
            "expected_modified": "2026-10-16T12:00:00Z",
            "metadata": {"title": "Filed utility bill"},
            "disposition": "confirm_and_file",
            "action_versions": {str(created["id"]): created["version"] + 1},
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "action_version_conflict"
    mock_paperless.update_document.assert_not_awaited()


def test_taxonomy_creation_prevents_case_insensitive_duplicates(client, mock_paperless):
    response = client.post("/api/intake/taxonomies/tag", json={"name": " inbox "})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "taxonomy_duplicate"
    mock_paperless.create_tag.assert_not_awaited()


def test_user_action_can_be_created_and_deleted(client, mock_paperless):
    mock_paperless.get_document.return_value = _document()

    created = client.post(
        "/api/intake/documents/42/actions",
        json={
            "action_type": "PAY",
            "title": "Pay utility bill",
            "summary": "Use the household checking account.",
            "payee_or_owner": "Utility Co",
            "amount": 84.2,
            "due_date": "2026-10-25",
            "reference": "Account 4821",
        },
    )

    assert created.status_code == 200
    action = created.json()
    assert action["origin"] == "user"
    assert action["summary"] == "Use the household checking account."
    assert action["payee_or_owner"] == "Utility Co"
    assert action["history"][0]["event_type"] == "created"

    deleted = client.delete(f"/api/intake/actions/{action['id']}")
    assert deleted.status_code == 204


def test_user_pay_action_requires_amount(client, mock_paperless):
    response = client.post(
        "/api/intake/documents/42/actions",
        json={"action_type": "PAY", "title": "Pay utility bill"},
    )

    assert response.status_code == 422
    mock_paperless.get_document.assert_not_awaited()
