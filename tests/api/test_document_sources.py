from doc_intelligence_hub.modules.statements.correspondent_models import (
    paperless_deployment_identity,
)


def _configure_database(app, tmp_path) -> str:
    database_path = str(tmp_path / "document-sources.db")
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


def test_document_source_expectation_and_coverage_api(
    client, app, mock_paperless, tmp_path
) -> None:
    _configure_database(app, tmp_path)
    mock_paperless.list_correspondents.return_value = [{"id": 42, "name": "Example Bank"}]

    synced = client.post("/api/sources/sync")
    assert synced.status_code == 200
    assert synced.json()["created"] == 1
    assert mock_paperless.aclose.await_count == 1

    source = client.get("/api/sources/42")
    assert source.status_code == 200
    assert source.json()["review_state"] == "needs_review"
    assert source.json()["reopen_reasons"] == ["new_source"]
    assert "deployment_id" not in source.json()

    series = client.post(
        "/api/series",
        json={
            "source_correspondent_id": 42,
            "name": "Checking 1234",
            "kind": "statement",
            "document_ids": [1, 2],
        },
    )
    assert series.status_code == 201

    expectation = client.post(
        "/api/expectations",
        json={
            "source_correspondent_id": 42,
            "series_id": series.json()["id"],
            "name": "Monthly checking statement",
            "kind": "statement",
            "expectation_mode": "recurring",
            "status": "confirmed",
            "cadence": {"frequency": "monthly", "expected_day": 5},
            "monitoring_mode": "presence",
            "importance": "important",
            "evidence": {"source": "user", "reason_codes": ["user_confirmed"]},
        },
    )
    assert expectation.status_code == 201

    disposition = client.put(
        "/api/sources/42/disposition",
        json={"disposition": "expectations_configured"},
    )
    assert disposition.status_code == 200
    assert disposition.json()["review_state"] == "resolved"

    eligibility = client.get(f"/api/expectations/{expectation.json()['id']}/coverage-eligibility")
    assert eligibility.json() == {"eligible": True}

    recorded_period = client.put(
        f"/api/series/{series.json()['id']}/members/1/period",
        json={"document_date": "2026-09-04", "period_key": "2026-09"},
    )
    assert recorded_period.status_code == 204

    relationship = client.put(
        "/api/external-signals/signal-1/relationship",
        json={
            "source_correspondent_id": 42,
            "resolution": "linked",
            "safe_label": "Checking ending 1234",
        },
    )
    assert relationship.status_code == 200
    assert relationship.json()["expectation_ids"] == []

    signals = client.get("/api/external-signals")
    assert signals.status_code == 200
    assert signals.json() == []

    coverage = client.post(
        "/api/coverage/evaluate",
        json={"as_of": "2026-10-20", "horizon_start": "2026-10-01"},
    )
    assert coverage.status_code == 200
    assert coverage.json()[0]["kind"] == "statement"
    assert coverage.json()[0]["state"] == "missing"


def test_document_source_series_analysis_returns_reviewable_candidates(
    client, app, mock_paperless, tmp_path
) -> None:
    _configure_database(app, tmp_path)
    mock_paperless.list_correspondents.return_value = [{"id": 42, "name": "Example Bank"}]
    mock_paperless.fetch_all_metadata.return_value = (
        {42: "Example Bank"},
        {7: "Finance"},
        {3: "Statement"},
    )
    mock_paperless.list_documents.return_value = [
        {
            "id": month,
            "title": f"Example Bank Checking Statement 2026-{month:02d}",
            "correspondent": 42,
            "document_type": 3,
            "tags": [7],
            "created": f"2026-{month:02d}-03",
        }
        for month in range(1, 5)
    ]
    client.post("/api/sources/sync")

    response = client.post("/api/sources/42/series-analysis")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["observed_summary"]["document_count"] == 4
    assert payload["observed_summary"]["candidate_series_count"] == 1
    assert payload["suggestions"][0]["document_ids"] == [1, 2, 3, 4]
    assert client.get("/api/series?correspondent_id=42").json() == []


def test_dismissed_series_candidate_stays_excluded_from_analysis(
    client, app, mock_paperless, tmp_path
) -> None:
    _configure_database(app, tmp_path)
    mock_paperless.list_correspondents.return_value = [{"id": 42, "name": "Example Bank"}]
    mock_paperless.fetch_all_metadata.return_value = (
        {42: "Example Bank"},
        {7: "Finance"},
        {3: "Statement"},
    )
    mock_paperless.list_documents.return_value = [
        {
            "id": month,
            "title": f"Example Bank Checking Statement 2026-{month:02d}",
            "correspondent": 42,
            "document_type": 3,
            "tags": [7],
            "created": f"2026-{month:02d}-03",
        }
        for month in range(1, 5)
    ]
    client.post("/api/sources/sync")
    first = client.post("/api/sources/42/series-analysis").json()
    document_ids = first["suggestions"][0]["document_ids"]

    dismissed = client.post(
        "/api/sources/42/series-analysis/dismiss",
        json={"document_ids": document_ids},
    )
    second = client.post("/api/sources/42/series-analysis")

    assert dismissed.status_code == 204
    assert second.status_code == 200
    assert second.json()["suggestions"] == []
    assert second.json()["observed_summary"]["excluded_document_count"] == 4


def test_source_defined_expectation_does_not_require_existing_documents(
    client, app, mock_paperless, tmp_path
) -> None:
    _configure_database(app, tmp_path)
    mock_paperless.list_correspondents.return_value = [{"id": 42, "name": "Example Bank"}]
    client.post("/api/sources/sync")

    expectation = client.post(
        "/api/expectations",
        json={
            "source_correspondent_id": 42,
            "series_id": None,
            "name": "Annual account tax form",
            "kind": "tax_document",
            "expectation_mode": "periodic",
            "status": "confirmed",
            "cadence": {"frequency": "annual", "expected_day": 15},
            "monitoring_mode": "presence",
            "importance": "important",
            "fulfillment_rule": {
                "match_series": False,
                "match_kind": True,
                "match_calendar_period": True,
                "required_tag_ids": [],
                "required_document_type_id": None,
            },
            "evidence": {
                "source": "user",
                "reason_codes": ["user_defined_without_series"],
            },
            "acquisition_notes": "Download it from the account portal.",
        },
    )

    assert expectation.status_code == 201, expectation.text
    assert expectation.json()["series_id"] is None
    assert expectation.json()["acquisition_notes"] == "Download it from the account portal."
    client.put("/api/sources/42/disposition", json={"disposition": "expectations_configured"})
    assert client.get(
        f"/api/expectations/{expectation.json()['id']}/coverage-eligibility"
    ).json() == {"eligible": True}

    coverage = client.post(
        "/api/coverage/evaluate",
        json={"as_of": "2026-10-20", "horizon_start": "2026-01-01"},
    ).json()
    occurrence = next(item for item in coverage if item["expectation_id"] == expectation.json()["id"])
    assert occurrence["state"] == "missing"


def test_document_source_contract_uses_stable_deployment_identity(
    client, app, mock_paperless, tmp_path
) -> None:
    database_path = _configure_database(app, tmp_path)
    mock_paperless.list_correspondents.return_value = [{"id": 42, "name": "Original Name"}]
    client.post("/api/sources/sync")
    mock_paperless.list_correspondents.return_value = [{"id": 42, "name": "Renamed Bank"}]
    client.post("/api/sources/sync")

    source = client.get("/api/sources/42").json()
    assert source["current_name"] == "Renamed Bank"
    assert source["correspondent_id"] == 42
    assert paperless_deployment_identity("http://paperless.test").startswith("paperless:")
    assert database_path


def test_occurrence_recovery_is_scoped_idempotent_and_preserves_policy(
    client, app, mock_paperless, tmp_path
) -> None:
    _configure_database(app, tmp_path)
    mock_paperless.list_correspondents.return_value = [{"id": 42, "name": "Example Bank"}]
    client.post("/api/sources/sync")
    series = client.post(
        "/api/series",
        json={
            "source_correspondent_id": 42,
            "name": "Checking 1234",
            "kind": "statement",
            "document_ids": [1],
        },
    ).json()
    expectation = client.post(
        "/api/expectations",
        json={
            "source_correspondent_id": 42,
            "series_id": series["id"],
            "name": "Monthly checking statement",
            "kind": "statement",
            "expectation_mode": "recurring",
            "status": "confirmed",
            "cadence": {"frequency": "monthly", "expected_day": 5},
            "monitoring_mode": "presence",
            "importance": "critical",
            "grace_period_days": 2,
            "evidence": {"source": "user", "reason_codes": ["user_confirmed"]},
        },
    ).json()
    client.put("/api/sources/42/disposition", json={"disposition": "expectations_configured"})
    occurrences = client.post(
        "/api/coverage/evaluate",
        json={"as_of": "2026-10-20", "horizon_start": "2026-09-01"},
    ).json()
    september = next(item for item in occurrences if item["period_key"] == "2026-09")
    october = next(item for item in occurrences if item["period_key"] == "2026-10")
    assert september["state"] == "missing"
    original_policy = client.get("/api/expectations").json()[0]

    first_extension = client.post(
        f"/api/coverage/occurrences/{september['id']}/extend-grace",
        json={"extend_until": "2026-11-01"},
    )
    second_extension = client.post(
        f"/api/coverage/occurrences/{september['id']}/extend-grace",
        json={"extend_until": "2026-11-01"},
    )
    assert first_extension.status_code == second_extension.status_code == 200
    assert first_extension.json()["grace_extended_until"] == "2026-11-01"
    assert second_extension.json()["grace_extended_until"] == "2026-11-01"

    mock_paperless.list_documents.return_value = [
        {
            "id": 99,
            "title": "September checking statement",
            "created_date": "2026-09-30",
            "correspondent": 42,
            "document_type": None,
        }
    ]
    reassigned = client.post(
        f"/api/coverage/occurrences/{september['id']}/reassign",
        json={"document_id": 99, "document_date": "2026-09-30"},
    )
    repeated = client.post(
        f"/api/coverage/occurrences/{september['id']}/reassign",
        json={"document_id": 99, "document_date": "2026-09-30"},
    )
    assert reassigned.status_code == repeated.status_code == 200
    assert reassigned.json()["state"] == repeated.json()["state"] == "fulfilled_late"
    assert reassigned.json()["fulfilled_document_id"] == 99

    refreshed = client.post(
        "/api/coverage/evaluate",
        json={"as_of": "2026-10-20", "horizon_start": "2026-09-01"},
    ).json()
    refreshed_september = next(item for item in refreshed if item["id"] == september["id"])
    refreshed_october = next(item for item in refreshed if item["id"] == october["id"])
    assert refreshed_september["state"] == "fulfilled_late"
    assert refreshed_october["fulfilled_document_id"] is None
    assert client.get("/api/expectations").json()[0] == original_policy
    assert (
        client.get(f"/api/coverage/occurrences/{september['id']}").json()["expectation"]["id"]
        == expectation["id"]
    )


def test_occurrence_near_matches_are_source_scoped(client, app, mock_paperless, tmp_path) -> None:
    _configure_database(app, tmp_path)
    mock_paperless.list_correspondents.return_value = [{"id": 42, "name": "Example Bank"}]
    client.post("/api/sources/sync")
    series = client.post(
        "/api/series",
        json={
            "source_correspondent_id": 42,
            "name": "Annual tax form",
            "kind": "tax_document",
            "document_ids": [],
        },
    ).json()
    client.post(
        "/api/expectations",
        json={
            "source_correspondent_id": 42,
            "series_id": series["id"],
            "name": "Annual tax form",
            "kind": "tax_document",
            "expectation_mode": "recurring",
            "status": "confirmed",
            "cadence": {"frequency": "annual", "expected_day": 15},
            "monitoring_mode": "presence",
            "importance": "important",
            "evidence": {"source": "user", "reason_codes": ["user_confirmed"]},
        },
    )
    client.put("/api/sources/42/disposition", json={"disposition": "expectations_configured"})
    occurrence = client.post(
        "/api/coverage/evaluate",
        json={"as_of": "2026-10-20", "horizon_start": "2026-01-01"},
    ).json()[0]
    mock_paperless.fetch_all_metadata.return_value = ({42: "Example Bank"}, {}, {7: "Tax form"})
    mock_paperless.list_documents.return_value = [
        {
            "id": 77,
            "title": "2026 tax statement",
            "created_date": "2026-01-18",
            "correspondent": 42,
            "document_type": 7,
        }
    ]

    response = client.get(f"/api/coverage/occurrences/{occurrence['id']}/near-matches")

    assert response.status_code == 200
    assert response.json() == [
        {
            "document_id": 77,
            "title": "2026 tax statement",
            "document_date": "2026-01-18",
            "correspondent_id": 42,
            "document_type": "Tax form",
            "already_assigned": False,
        }
    ]
    assert mock_paperless.list_documents.await_args.kwargs["correspondent_id"] == 42
