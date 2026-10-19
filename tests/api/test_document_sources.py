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
