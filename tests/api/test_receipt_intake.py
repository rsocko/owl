from __future__ import annotations

import hashlib


def _headers(*, occurrence: str = "a" * 64) -> dict[str, str]:
    return {
        "Authorization": "Bearer receipt-intake-test-token",
        "Content-Type": "application/pdf",
        "X-OWL-Source-Channel": "manual_upload",
        "X-OWL-Source-Occurrence-Version": "1",
        "X-OWL-Source-Occurrence": occurrence,
        "X-OWL-Connector-Ref": "connector-fixture",
        "X-OWL-Source-Observed-At": "2026-10-10T12:00:00Z",
        "X-OWL-Transform-Version": "identity-v1",
    }


def test_receipt_intake_requires_dedicated_bearer(client):
    response = client.post(
        "/api/receipt-intake/v1/occurrences",
        content=b"invented receipt",
        headers={key: value for key, value in _headers().items() if key != "Authorization"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "receipt_intake_auth_required"


def test_receipt_intake_returns_bounded_contract(client, mock_paperless):
    mock_paperless.upload_document.return_value = "task-fixture"
    mock_paperless.get_task.return_value = {
        "status": "success",
        "related_document": 818,
    }
    response = client.post(
        "/api/receipt-intake/v1/occurrences",
        content=b"invented receipt",
        headers=_headers(),
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload == {
        "schema_version": "1.0",
        "intake_ref": payload["intake_ref"],
        "outcome": "new_canonical",
        "attempt_state": "accepted",
        "canonical_document_ref": payload["canonical_document_ref"],
        "review_ref": None,
        "reason_codes": ["paperless_acknowledged"],
        "source_channel": "manual_upload",
        "source_occurrence_version": "1",
        "source_as_of": "2026-10-10T12:00:00Z",
        "retry_safe": True,
        "external_replica_eligible": True,
    }
    serialized = response.text.lower()
    assert all(
        forbidden not in serialized
        for forbidden in ("invented receipt", "818", "filename", "ocr", "signed_url")
    )


def test_lookup_by_exact_hash_does_not_upload_again(client, mock_paperless):
    mock_paperless.upload_document.return_value = "task-fixture"
    mock_paperless.get_task.return_value = {"status": "success", "related_document": 919}
    content = b"hash lookup fixture"
    created = client.post(
        "/api/receipt-intake/v1/occurrences",
        content=content,
        headers=_headers(occurrence="b" * 64),
    )
    assert created.status_code == 202

    lookup = client.get(
        "/api/receipt-intake/v1/lookup",
        headers={
            "Authorization": "Bearer receipt-intake-test-token",
            "X-OWL-Blob-SHA256": hashlib.sha256(content).hexdigest(),
        },
    )
    assert lookup.status_code == 200
    assert lookup.json()["canonical_document_ref"] == created.json()["canonical_document_ref"]
    mock_paperless.upload_document.assert_awaited_once()
