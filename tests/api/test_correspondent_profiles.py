from __future__ import annotations

from doc_intelligence_hub.modules.statements.database import Database


def _configure_statement_database(app, tmp_path) -> str:
    database_path = str(tmp_path / "correspondent-policy.db")
    config_path = tmp_path / "statements.yaml"
    config_path.write_text(
        "\n".join(
            [
                "source:",
                "  mode: paperless",
                "  paperless_url: http://paperless.test",
                "runtime:",
                f"  database_path: '{database_path}'",
            ]
        ),
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


def test_correspondent_analysis_is_typed_and_read_only(
    client, app, mock_paperless, tmp_path
) -> None:
    database_path = _configure_statement_database(app, tmp_path)
    mock_paperless.list_correspondents.return_value = [{"id": 42, "name": "Example Bank"}]
    assert client.post("/api/statements/correspondent-profiles/sync").status_code == 200
    database = Database(database_path)
    try:
        database.create_series(
            "checking",
            "Checking 1234",
            "Example Bank",
            correspondent_id=42,
            frequency="monthly",
        )
        database.add_documents_to_series(
            "checking",
            [
                {
                    "document_id": str(index),
                    "title": f"Checking 1234 - Statement - 2026-0{index}",
                    "statement_date": f"2026-0{index}-03",
                    "period_label": f"2026-0{index}",
                }
                for index in range(1, 4)
            ],
        )
    finally:
        database.close()
    mock_paperless.list_documents.return_value = [
        {
            "id": index,
            "title": f"Checking 1234 - Statement - 2026-0{index}",
            "created": f"2026-0{index}-03",
            "added": f"2026-0{index}-04T08:00:00Z",
            "tags": [1],
            "document_type": 1,
        }
        for index in range(1, 4)
    ]
    mock_paperless.list_mail_rules.return_value = [
        {"id": 7, "name": "Private rule name", "enabled": True, "assign_correspondent": 42}
    ]
    mock_paperless.fetch_all_metadata.return_value = (
        {42: "Example Bank"},
        {1: "Financial"},
        {1: "Statement"},
    )

    response = client.get("/api/statements/correspondent-profiles/42/analysis")

    assert response.status_code == 200
    body = response.json()
    assert body["suggestions"][0]["expectation_mode"] == "recurring"
    assert body["suggestions"][0]["title"]["coverage"] == 1.0
    assert body["suggestions"][0]["acquisition"]["channel"] == "paperless_mail"
    assert body["suggestions"][0]["acquisition"]["reason_codes"] == [
        "configured_mail_rule_evidence"
    ]
    assert "Private rule name" not in response.text
    assert client.get("/api/statements/correspondent-profiles/42/expectations").json() == []
    mock_paperless.list_documents.assert_awaited_once_with(correspondent_id=42)


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
