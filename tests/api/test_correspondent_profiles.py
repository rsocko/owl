from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx

from doc_intelligence_hub.modules.statements.correspondent_models import (
    DocumentExpectationSignalsV1,
    PolicyPatchOperation,
    paperless_deployment_identity,
)
from doc_intelligence_hub.modules.statements.database import Database
from doc_intelligence_hub.modules.statements.policy_evaluation import policy_operation_id
from doc_intelligence_hub.modules.triage.database import list_correction_events


def _configure_statement_database(app, tmp_path, *, tyrion_base_url: str | None = None) -> str:
    database_path = str(tmp_path / "correspondent-policy.db")
    config_path = tmp_path / "statements.yaml"
    lines = [
        "source:",
        "  mode: paperless",
        "  paperless_url: http://paperless.test",
        "runtime:",
        f"  database_path: '{database_path}'",
    ]
    if tyrion_base_url is not None:
        lines.extend(["external_signals:", f"  base_url: {tyrion_base_url}"])
    config_path.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )
    app.state.statement_tracker_config = str(config_path)
    app.state.statement_tracker_config_loaded = None
    return database_path


def test_profile_expectation_and_acquisition_contract(
    client, app, mock_paperless, tmp_path
) -> None:
    database_path = _configure_statement_database(app, tmp_path)
    mock_paperless.list_correspondents.return_value = [{"id": 42, "name": "Example Bank"}]

    sync = client.post("/api/statements/correspondent-profiles/sync")
    assert sync.status_code == 200
    assert sync.json()["created"] == 1

    profile_update = client.patch(
        "/api/statements/correspondent-profiles/42",
        json={
            "review_status": "reviewed",
            "aliases": ["Example Financial"],
            "notes": "Use the reviewed checking-series policy.",
            "profile_defaults": {
                "title_convention": {
                    "template": "{correspondent} - {kind} - {document_date}",
                    "date_basis": "document_date",
                    "example": "Example Bank - Statement - 2026-08-22",
                },
                "metadata_policy": {"all_of": [7]},
            },
            "observed_summary": {
                "document_count": 12,
                "candidate_series_count": 2,
            },
        },
    )
    assert profile_update.status_code == 200
    profile = profile_update.json()
    assert profile["current_name"] == "Example Bank"
    assert profile["aliases"] == ["Example Financial"]
    assert profile["profile_defaults"]["metadata_policy"]["all_of"] == [7]
    assert profile["observed_summary"]["document_count"] == 12
    assert "deployment_id" not in profile

    database = Database(database_path)
    try:
        database.create_series(
            "checking-series",
            "Checking 1234",
            "Example Bank",
            correspondent_id=42,
        )
    finally:
        database.close()

    source = client.post(
        "/api/statements/acquisition-sources",
        json={
            "channel": "portal_manual",
            "delivery_mode": "pull",
            "portal_url": "https://example.test/statements",
            "instructions": "Sign in and download the monthly PDF.",
        },
    )
    assert source.status_code == 201
    source_id = source.json()["id"]
    source_update = client.patch(
        f"/api/statements/acquisition-sources/{source_id}",
        json={"automation_state": "available"},
    )
    assert source_update.status_code == 200
    assert source_update.json()["automation_state"] == "available"

    expectation = client.post(
        "/api/statements/correspondent-profiles/42/expectations",
        json={
            "kind": "statement",
            "statement_series_id": "checking-series",
            "series_discriminator": "Checking 1234",
            "expectation_mode": "recurring",
            "status": "confirmed",
            "cadence": {
                "frequency": "monthly",
                "expected_day": 3,
                "availability_delay_days": 2,
                "grace_period_days": 5,
            },
            "evidence": {
                "source": "user",
                "reason_codes": ["user_confirmed"],
                "sample_size": 12,
            },
            "title_convention": {
                "template": "{series} - {kind} - {period}",
                "date_basis": "period",
                "example": "Checking 1234 - Statement - 2026-07",
            },
            "metadata_policy": {
                "all_of": [7],
                "any_of": [11, 12],
                "none_of": [99],
                "required_document_type_id": 3,
            },
            "acquisition_source_id": source_id,
        },
    )
    assert expectation.status_code == 201
    body = expectation.json()
    assert body["statement_series_id"] == "checking-series"
    assert body["evidence"]["reason_codes"] == ["user_confirmed"]
    assert "deployment_id" not in body

    eligibility = client.get(
        f"/api/statements/document-expectations/{body['id']}/missing-alert-eligibility"
    )
    assert eligibility.status_code == 200
    assert eligibility.json()["eligible"] is True


def test_sync_marks_deleted_correspondent_orphaned(client, app, mock_paperless, tmp_path) -> None:
    _configure_statement_database(app, tmp_path)
    mock_paperless.list_correspondents.return_value = [{"id": 42, "name": "Old Name"}]
    assert client.post("/api/statements/correspondent-profiles/sync").status_code == 200

    mock_paperless.list_correspondents.return_value = [{"id": 84, "name": "Old Name"}]
    result = client.post("/api/statements/correspondent-profiles/sync")

    assert result.status_code == 200
    assert result.json()["orphaned"] == 1
    profiles = client.get("/api/statements/correspondent-profiles").json()
    by_id = {profile["correspondent_id"]: profile for profile in profiles}
    assert by_id[42]["lifecycle_status"] == "orphaned"
    assert by_id[84]["lifecycle_status"] == "active"


def test_correspondent_analysis_is_explainable_and_read_only(
    client, app, mock_paperless, tmp_path
) -> None:
    _configure_statement_database(app, tmp_path)
    mock_paperless.fetch_all_metadata.return_value = (
        {42: "Example Bank"},
        {7: "Finance"},
        {3: "Statement"},
    )
    mock_paperless.list_documents.return_value = [
        {
            "id": month,
            "title": f"Example Bank Statement 2026-{month:02d}",
            "correspondent": 42,
            "document_type": 3,
            "created_date": f"2026-{month:02d}-03",
            "added": f"2026-{month:02d}-04T08:00:00Z",
            "tags": [7],
        }
        for month in range(1, 5)
    ]
    mock_paperless.list_correspondents.return_value = [{"id": 42, "name": "Example Bank"}]
    assert client.post("/api/statements/correspondent-profiles/sync").status_code == 200

    response = client.post("/api/statements/correspondent-profiles/analyze")

    assert response.status_code == 200
    result = response.json()[0]
    assert result["correspondent_id"] == 42
    assert result["observed_summary"]["document_count"] == 4
    assert result["suggestions"][0]["expectation_mode"] == "recurring"
    assert result["suggestions"][0]["title"]["coverage"] == 1
    assert result["suggestions"][0]["metadata"]["policy"]["all_of"] == [7]
    profile = client.get("/api/statements/correspondent-profiles/42").json()
    assert profile["observed_summary"] == result["observed_summary"]
    assert profile["last_analyzed_at"] == result["analyzed_at"]
    assert mock_paperless.update_document.await_count == 0
    assert mock_paperless.aclose.await_count == 1

    single = client.post("/api/statements/correspondent-profiles/42/analyze")
    assert single.status_code == 200
    mock_paperless.list_documents.assert_awaited_with(correspondent_id=42)
    assert mock_paperless.aclose.await_count == 2


def test_correspondent_analysis_uses_stored_masked_account_identifiers(
    client, app, mock_paperless, tmp_path
) -> None:
    _configure_statement_database(app, tmp_path)
    mock_paperless.fetch_all_metadata.return_value = (
        {42: "Example Bank"},
        {7: "Finance"},
        {3: "Statement"},
    )
    mock_paperless.list_custom_fields.return_value = [
        {"id": 9, "name": "Account Identifier", "data_type": "string"}
    ]
    mock_paperless.list_documents.return_value = [
        {
            "id": document_id,
            "title": f"Checking Statement 2026-{month:02d}",
            "correspondent": 42,
            "document_type": 3,
            "created_date": f"2026-{month:02d}-03",
            "tags": [7],
            "custom_fields": [{"field": 9, "value": f"ending {account}"}],
        }
        for document_id, account, month in (
            (1, 1234, 1),
            (2, 1234, 2),
            (3, 5678, 1),
            (4, 5678, 2),
        )
    ]
    mock_paperless.list_correspondents.return_value = [{"id": 42, "name": "Example Bank"}]
    assert client.post("/api/statements/correspondent-profiles/sync").status_code == 200

    response = client.post("/api/statements/correspondent-profiles/42/analyze")

    assert response.status_code == 200
    assert len(response.json()["suggestions"]) == 2
    assert response.json()["account_identifiers"] == {
        "extraction_requested": False,
        "stored_document_count": 4,
        "extracted_document_count": 0,
        "unresolved_document_count": 0,
        "extraction_failed_document_count": 0,
    }
    mock_paperless.get.assert_not_awaited()


def test_correspondent_analysis_can_extract_missing_identifiers_without_writing(
    client, app, mock_paperless, tmp_path
) -> None:
    _configure_statement_database(app, tmp_path)
    mock_paperless.fetch_all_metadata.return_value = (
        {42: "Example Bank"},
        {7: "Finance"},
        {3: "Statement"},
    )
    mock_paperless.list_documents.return_value = [
        {
            "id": document_id,
            "title": f"Checking Statement 2026-{month:02d}",
            "correspondent": 42,
            "document_type": 3,
            "created_date": f"2026-{month:02d}-03",
            "tags": [7],
            "custom_fields": [],
        }
        for document_id, month in ((1, 1), (2, 2), (3, 1), (4, 2))
    ]
    mock_paperless.get.side_effect = lambda path: {
        "content": f"Account #****{'1234' if int(path.split('/')[-2]) <= 2 else '5678'}"
    }
    mock_paperless.list_correspondents.return_value = [{"id": 42, "name": "Example Bank"}]
    assert client.post("/api/statements/correspondent-profiles/sync").status_code == 200

    response = client.post(
        "/api/statements/correspondent-profiles/42/analyze?extract_missing_account_identifiers=true"
    )

    assert response.status_code == 200
    assert len(response.json()["suggestions"]) == 2
    assert response.json()["account_identifiers"] == {
        "extraction_requested": True,
        "stored_document_count": 0,
        "extracted_document_count": 4,
        "unresolved_document_count": 0,
        "extraction_failed_document_count": 0,
    }
    assert mock_paperless.get.await_count == 4
    mock_paperless.update_custom_fields.assert_not_awaited()


def test_statement_expectation_must_bind_existing_series(
    client, app, mock_paperless, tmp_path
) -> None:
    _configure_statement_database(app, tmp_path)
    mock_paperless.list_correspondents.return_value = [{"id": 42, "name": "Example Bank"}]
    client.post("/api/statements/correspondent-profiles/sync")

    response = client.post(
        "/api/statements/correspondent-profiles/42/expectations",
        json={
            "kind": "statement",
            "expectation_mode": "irregular",
            "status": "confirmed",
            "evidence": {"source": "user"},
        },
    )

    assert response.status_code == 422


def test_acquisition_source_rejects_credential_bearing_url(client, app, tmp_path) -> None:
    _configure_statement_database(app, tmp_path)

    response = client.post(
        "/api/statements/acquisition-sources",
        json={
            "channel": "portal_manual",
            "delivery_mode": "pull",
            "portal_url": "https://user:secret@example.test/path?account=123",
        },
    )

    assert response.status_code == 422


def test_external_candidate_poll_and_review_contract(client, app, tmp_path) -> None:
    database_path = _configure_statement_database(
        app, tmp_path, tyrion_base_url="https://tyrion.test"
    )
    database = Database(database_path)
    try:
        database.reconcile_correspondents(
            paperless_deployment_identity("http://paperless.test"),
            [{"id": 42, "name": "Example Bank"}],
        )
    finally:
        database.close()
    snapshot = DocumentExpectationSignalsV1.model_validate(
        {
            "contractVersion": "1",
            "connectorRef": "opaque-connector",
            "sourceGeneration": "opaque-generation",
            "sourceAsOf": "2026-08-23T00:00:00Z",
            "completeness": "complete",
            "signals": [
                {
                    "seriesRef": "opaque-series",
                    "kind": "accountStatementCandidate",
                    "active": True,
                    "displayHint": "Credit account",
                    "cadence": None,
                    "nextExpectedDate": None,
                    "confidence": 0.6,
                    "basis": ["active_non_cash_account"],
                }
            ],
        }
    )
    with patch(
        "doc_intelligence_hub.api.routers.statements.DocumentExpectationSignalsClient"
    ) as client_type:
        source_client = client_type.return_value
        source_client.fetch = AsyncMock(return_value=snapshot)
        source_client.close = AsyncMock()
        response = client.post(
            "/api/statements/external-candidates/poll",
            json={
                "source_generation": "opaque-generation",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "source_generation": "opaque-generation",
        "idempotent": False,
        "active_candidates": 1,
        "deactivated_candidates": 0,
    }
    source_client.fetch.assert_awaited_once_with("opaque-generation")

    candidates = client.get("/api/statements/external-candidates")
    assert candidates.status_code == 200
    candidate = candidates.json()[0]
    assert candidate["display_hint"] == "Credit account"
    assert candidate["recurrence_evidence"] == "high"
    assert "series_ref" not in candidate
    assert "connector_ref" not in candidate

    reviewed = client.put(
        f"/api/statements/external-candidates/{candidate['id']}/review",
        json={"outcome": "ambiguous"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["outcome"] == "ambiguous"

    documentless = client.put(
        f"/api/statements/external-candidates/{candidate['id']}/review",
        json={"outcome": "not_applicable", "correspondent_id": 42},
    )
    assert documentless.status_code == 200
    assert documentless.json()["outcome"] == "not_applicable"
    assert documentless.json()["expectation_id"]

    expectations = client.get("/api/statements/correspondent-profiles/42/expectations")
    assert expectations.status_code == 200
    assert expectations.json()[0]["expectation_mode"] == "not_expected"
    assert expectations.json()[0]["status"] == "confirmed"


def test_saved_external_connection_drives_candidate_sync(client, app, tmp_path) -> None:
    _configure_statement_database(app, tmp_path)
    saved = client.put(
        "/api/statements/external-candidates/connection",
        json={
            "base_url": "https://tyrion-ui.test",
            "api_token": "saved-secret",
            "verify_ssl": True,
            "timeout_seconds": 45,
        },
    )
    assert saved.status_code == 200
    assert saved.json() == {
        "configured": True,
        "source": "saved",
        "base_url": "https://tyrion-ui.test",
        "token_configured": True,
        "verify_ssl": True,
        "timeout_seconds": 45,
        "last_source_generation": None,
        "last_source_as_of": None,
        "last_synced_at": None,
    }
    assert "saved-secret" not in saved.text

    snapshot = DocumentExpectationSignalsV1.model_validate(
        {
            "contractVersion": "1",
            "connectorRef": "saved-connector",
            "sourceGeneration": "generation-2",
            "sourceAsOf": "2026-08-23T10:00:00Z",
            "completeness": "complete",
            "signals": [],
        }
    )
    with patch(
        "doc_intelligence_hub.api.routers.statements.DocumentExpectationSignalsClient"
    ) as client_type:
        source_client = client_type.return_value
        source_client.fetch_latest = AsyncMock(return_value=snapshot)
        source_client.close = AsyncMock()
        response = client.post("/api/statements/external-candidates/sync")

    assert response.status_code == 200
    client_type.assert_called_once_with(
        "https://tyrion-ui.test",
        api_token="saved-secret",
        verify_ssl=True,
        timeout_seconds=45,
    )
    source_client.fetch_latest.assert_awaited_once_with()

    connection = client.get("/api/statements/external-candidates/connection")
    assert connection.status_code == 200
    assert connection.json()["last_source_generation"] == "generation-2"
    assert connection.json()["last_source_as_of"] == "2026-08-23T10:00:00Z"


def test_candidate_sync_requires_saved_connection(client, app, tmp_path) -> None:
    _configure_statement_database(app, tmp_path)

    response = client.post("/api/statements/external-candidates/sync")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "external_signal_source_not_configured"


def test_candidate_sync_reports_rejected_tyrion_credentials(client, app, tmp_path) -> None:
    _configure_statement_database(app, tmp_path)
    assert (
        client.put(
            "/api/statements/external-candidates/connection",
            json={
                "base_url": "https://tyrion.test",
                "api_token": "expired-secret",
            },
        ).status_code
        == 200
    )
    request = httpx.Request(
        "GET", "https://tyrion.test/api/connector/v1/document-expectation-signals"
    )
    response = httpx.Response(401, request=request)
    with patch(
        "doc_intelligence_hub.api.routers.statements.DocumentExpectationSignalsClient"
    ) as client_type:
        source_client = client_type.return_value
        source_client.fetch_latest = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Unauthorized",
                request=request,
                response=response,
            )
        )
        source_client.close = AsyncMock()
        result = client.post("/api/statements/external-candidates/sync")

    assert result.status_code == 502
    assert result.json()["error"] == {
        "code": "external_signal_source_failed",
        "message": (
            "Tyrion rejected the saved credentials. Update the Tyrion API token in Settings."
        ),
        "details": {"upstream_status": 401},
    }
    assert "expired-secret" not in result.text


def test_external_connection_does_not_send_saved_token_to_new_origin(client, app, tmp_path) -> None:
    _configure_statement_database(app, tmp_path)
    assert (
        client.put(
            "/api/statements/external-candidates/connection",
            json={
                "base_url": "https://tyrion-one.test",
                "api_token": "origin-one-secret",
            },
        ).status_code
        == 200
    )

    changed = client.put(
        "/api/statements/external-candidates/connection",
        json={
            "base_url": "https://tyrion-two.test",
        },
    )

    assert changed.status_code == 200
    assert changed.json()["token_configured"] is False


def test_external_connection_rejects_token_over_plain_http(client, app, tmp_path) -> None:
    _configure_statement_database(app, tmp_path)

    response = client.put(
        "/api/statements/external-candidates/connection",
        json={
            "base_url": "http://tyrion.test",
            "api_token": "insecure-secret",
        },
    )

    assert response.status_code == 422
    assert "insecure-secret" not in response.text


def test_cross_correspondent_series_merge_fails_before_mutation(client, app, tmp_path) -> None:
    database_path = _configure_statement_database(app, tmp_path)
    database = Database(database_path)
    try:
        database.create_series("source", "Checking", "Bank One", correspondent_id=42)
        database.create_series("target", "Savings", "Bank Two", correspondent_id=84)
        database.add_documents_to_series(
            "source",
            [{"document_id": "doc-1", "title": "Statement"}],
        )
    finally:
        database.close()

    response = client.post(
        "/api/statements/series/merge",
        json={"source_series_id": "source", "target_series_id": "target"},
    )

    assert response.status_code == 409
    database = Database(database_path)
    try:
        assert [item["document_id"] for item in database.get_series_documents("source")] == [
            "doc-1"
        ]
        assert database.get_series_documents("target") == []
    finally:
        database.close()


def test_confirmed_expectation_policy_preview_is_read_only_and_apply_ready(
    client, app, mock_paperless, tmp_path
) -> None:
    database_path = _configure_statement_database(app, tmp_path)
    mock_paperless.list_correspondents.return_value = [{"id": 42, "name": "Example Bank"}]
    mock_paperless.fetch_all_metadata.return_value = (
        {42: "Example Bank"},
        {7: "Finance", 10: "DOG", 11: "DOG:Quinn", 12: "DOG:Avery", 99: "Old"},
        {3: "Statement", 4: "Invoice"},
    )
    mock_paperless.list_documents.return_value = [
        {
            "id": 101,
            "title": "Old statement title",
            "correspondent": 42,
            "document_type": 4,
            "created_date": "2026-07-03",
            "tags": [10, 99],
        },
        {
            "id": 999,
            "title": "Not in the series",
            "correspondent": 42,
            "document_type": 4,
            "created_date": "2026-07-03",
            "tags": [],
        },
    ]
    assert client.post("/api/statements/correspondent-profiles/sync").status_code == 200
    database = Database(database_path)
    try:
        database.create_series(
            "checking-series",
            "Checking",
            "Example Bank",
            correspondent_id=42,
        )
        database.add_documents_to_series(
            "checking-series",
            [
                {
                    "document_id": "101",
                    "title": "Old statement title",
                    "statement_date": "2026-07-03",
                    "period_label": "2026-07",
                }
            ],
        )
    finally:
        database.close()

    created = client.post(
        "/api/statements/correspondent-profiles/42/expectations",
        json={
            "kind": "statement",
            "statement_series_id": "checking-series",
            "series_discriminator": "Checking",
            "expectation_mode": "recurring",
            "status": "confirmed",
            "cadence": {"frequency": "monthly"},
            "evidence": {"source": "user"},
            "title_convention": {
                "template": "{series} - {kind} - {period}",
                "date_basis": "period",
                "example": "Checking - Statement - 2026-07",
            },
            "metadata_policy": {
                "all_of": [7],
                "any_of": [11, 12],
                "none_of": [99],
                "required_document_type_id": 3,
            },
        },
    )
    expectation_id = created.json()["id"]

    first = client.post(f"/api/statements/document-expectations/{expectation_id}/policy-preview")
    second = client.post(f"/api/statements/document-expectations/{expectation_id}/policy-preview")

    assert first.status_code == 200
    assert first.json() == second.json()
    assert first.json()["matched_document_count"] == 1
    operation = first.json()["findings"][0]["operation"]
    assert operation["document_id"] == 101
    assert operation["expected"]["title"] == "Old statement title"
    assert operation["patch"] == {
        "title": "Checking - Statement - 2026-07",
        "tags": [7, 10],
        "document_type": 3,
    }
    assert mock_paperless.update_document.await_count == 0
    assert mock_paperless.aclose.await_count == 2

    invoice = client.post(
        "/api/statements/correspondent-profiles/42/expectations",
        json={
            "kind": "invoice",
            "document_ids": [101],
            "series_discriminator": "Veterinary invoices",
            "expectation_mode": "irregular",
            "status": "confirmed",
            "evidence": {"source": "user"},
            "metadata_policy": {"all_of": [7]},
        },
    )
    invoice_preview = client.post(
        f"/api/statements/document-expectations/{invoice.json()['id']}/policy-preview"
    )
    assert invoice_preview.status_code == 200
    assert invoice_preview.json()["matched_document_count"] == 1
    assert invoice_preview.json()["findings"][0]["operation"]["document_id"] == 101


def test_selected_policy_apply_is_exact_audited_and_bounded_undo(
    client, app, mock_paperless, tmp_path
) -> None:
    _configure_statement_database(app, tmp_path)
    mock_paperless.list_correspondents.return_value = [{"id": 42, "name": "Example Bank"}]
    mock_paperless.fetch_all_metadata.return_value = (
        {42: "Example Bank"},
        {7: "Finance", 55: "Added later", 99: "Old"},
        {3: "Statement", 4: "Invoice"},
    )
    old_document = {
        "id": 101,
        "title": "Account 123456789 statement",
        "correspondent": 42,
        "document_type": 4,
        "created_date": "2026-07-03",
        "tags": [99],
    }
    mock_paperless.list_documents.return_value = [old_document]
    mock_paperless.get_document.return_value = old_document
    assert client.post("/api/statements/correspondent-profiles/sync").status_code == 200
    created = client.post(
        "/api/statements/correspondent-profiles/42/expectations",
        json={
            "kind": "invoice",
            "document_ids": [101],
            "series_discriminator": "Checking",
            "expectation_mode": "irregular",
            "status": "confirmed",
            "evidence": {"source": "user"},
            "title_convention": {
                "template": "{series} - {document_date}",
                "date_basis": "document_date",
                "example": "Checking - 2026-07-03",
            },
            "metadata_policy": {
                "all_of": [7],
                "none_of": [99],
                "required_document_type_id": 3,
            },
        },
    )
    expectation_id = created.json()["id"]
    finding = client.post(
        f"/api/statements/document-expectations/{expectation_id}/policy-preview"
    ).json()["findings"][0]

    applied = client.post(
        f"/api/statements/document-expectations/{expectation_id}/policy-apply",
        json={
            "actor": "reviewer",
            "reason": "Approved account 123456789 correction",
            "operations": [
                {"preview_id": finding["preview_id"], "operation": finding["operation"]}
            ],
        },
    )

    assert applied.status_code == 200
    result = applied.json()["results"][0]
    assert result["status"] == "succeeded"
    assert mock_paperless.update_document.await_args_list[0].args == (
        101,
        {"title": "Checking - 2026-07-03", "tags": [7], "document_type": 3},
    )
    audit = list_correction_events(event_type="paperless_policy_correction")[0]
    assert audit["created_by"] == "reviewer"
    assert audit["payload"]["expectation_id"] == expectation_id
    assert audit["payload"]["reason"] == "Approved account [redacted] correction"
    assert "123456789" not in str(audit["payload"])
    assert audit["payload"]["old_display"]["title"] == "Account [redacted] statement"

    mock_paperless.get_document.return_value = {
        **old_document,
        "title": "Changed after apply",
        "document_type": 3,
        "tags": [7, 55],
    }
    conflicted = client.post(
        f"/api/statements/policy-corrections/{result['audit_event_id']}/undo",
        json={
            "actor": "reviewer",
            "reason": "Revert selected correction",
            "preview_id": finding["preview_id"],
            "operation": finding["operation"],
        },
    )
    assert conflicted.json()["error_code"] == "undo_conflict"

    mock_paperless.get_document.return_value = {
        **old_document,
        "title": "Checking - 2026-07-03",
        "document_type": 3,
        "tags": [7, 55],
    }
    undone = client.post(
        f"/api/statements/policy-corrections/{result['audit_event_id']}/undo",
        json={
            "actor": "reviewer",
            "reason": "Revert selected correction",
            "preview_id": finding["preview_id"],
            "operation": finding["operation"],
        },
    )

    assert undone.status_code == 200
    assert undone.json()["status"] == "succeeded"
    assert mock_paperless.update_document.await_args_list[1].args == (
        101,
        {
            "title": "Account 123456789 statement",
            "document_type": 4,
            "tags": [55, 99],
        },
    )
    events = list_correction_events(include_undone=True)
    assert [event["event_type"] for event in events[:2]] == [
        "paperless_policy_correction_undo",
        "paperless_policy_correction",
    ]
    assert events[1]["undone"] is True


def test_policy_apply_reports_tampered_and_stale_operations_independently(
    client, app, mock_paperless, tmp_path
) -> None:
    _configure_statement_database(app, tmp_path)
    mock_paperless.list_correspondents.return_value = [{"id": 42, "name": "Example Bank"}]
    mock_paperless.fetch_all_metadata.return_value = (
        {42: "Example Bank"},
        {7: "Finance", 99: "Old"},
        {3: "Statement", 4: "Invoice"},
    )
    old_document = {
        "id": 101,
        "title": "Old title",
        "correspondent": 42,
        "document_type": 4,
        "created_date": "2026-07-03",
        "tags": [99],
    }
    mock_paperless.list_documents.return_value = [old_document]
    assert client.post("/api/statements/correspondent-profiles/sync").status_code == 200
    created = client.post(
        "/api/statements/correspondent-profiles/42/expectations",
        json={
            "kind": "invoice",
            "document_ids": [101],
            "series_discriminator": "Checking",
            "expectation_mode": "irregular",
            "status": "confirmed",
            "evidence": {"source": "user"},
            "metadata_policy": {"all_of": [7], "none_of": [99]},
        },
    )
    expectation_id = created.json()["id"]
    finding = client.post(
        f"/api/statements/document-expectations/{expectation_id}/policy-preview"
    ).json()["findings"][0]
    tampered = {
        **finding["operation"],
        "patch": {**finding["operation"]["patch"], "title": "Unapproved title"},
    }
    attacker_preview_id = policy_operation_id(PolicyPatchOperation.model_validate(tampered))
    mock_paperless.get_document.return_value = {**old_document, "tags": [7, 99]}

    response = client.post(
        f"/api/statements/document-expectations/{expectation_id}/policy-apply",
        json={
            "reason": "Apply reviewed policy",
            "operations": [
                {"preview_id": attacker_preview_id, "operation": tampered},
                {"preview_id": finding["preview_id"], "operation": finding["operation"]},
            ],
        },
    )

    assert response.status_code == 200
    assert [result["error_code"] for result in response.json()["results"]] == [
        "tampered_preview",
        "stale_document",
    ]
    mock_paperless.update_document.assert_not_awaited()
