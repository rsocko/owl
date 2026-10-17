"""Tests for the unified Intake Review API."""

from __future__ import annotations


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


def test_list_intake_documents_uses_configured_source(client, mock_paperless):
    mock_paperless.list_documents.return_value = [_document()]

    response = client.get("/api/intake/documents")

    assert response.status_code == 200
    assert response.json()["documents"][0]["id"] == 42
    mock_paperless.list_documents.assert_awaited_once_with(tags=["Inbox"], limit=100)


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
        ("tags", "Utilities"),
    }


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


def test_confirm_and_file_checks_action_revision_before_paperless_write(client, mock_paperless):
    mock_paperless.get_document.return_value = _document()
    created = client.post(
        "/api/intake/documents/42/actions",
        json={"action_type": "PAY", "title": "Pay utility bill"},
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
            "payee_or_owner": "Utility Co",
            "amount": 84.2,
            "due_date": "2026-10-25",
            "reference": "Account 4821",
        },
    )

    assert created.status_code == 200
    action = created.json()
    assert action["origin"] == "user"
    assert action["payee_or_owner"] == "Utility Co"
    assert action["history"][0]["event_type"] == "created"

    deleted = client.delete(f"/api/intake/actions/{action['id']}")
    assert deleted.status_code == 204
