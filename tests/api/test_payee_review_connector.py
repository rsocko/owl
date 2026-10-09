from __future__ import annotations

from pydantic import SecretStr

from doc_intelligence_hub.api.app import HubSettings
from doc_intelligence_hub.modules.statements.correspondent_models import (
    paperless_deployment_identity,
)
from doc_intelligence_hub.modules.statements.database import Database
from doc_intelligence_hub.modules.statements.payee_review import (
    PayeePatternProjectionV1,
    PayeeReviewService,
)


def _configure(app, tmp_path) -> str:
    database_path = str(tmp_path / "payee-review.db")
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
    app.state.hub_settings.mission_control_api_token = SecretStr("mc-secret")
    return database_path


def _seed(database_path: str) -> None:
    deployment_id = paperless_deployment_identity("http://paperless.test")
    service = PayeeReviewService(Database(database_path), deployment_id)
    try:
        service.database.reconcile_correspondents(
            deployment_id, [{"id": 42, "name": "Example Utility"}]
        )
        service.replace_snapshot(
            PayeePatternProjectionV1.model_validate(
                {
                    "contractVersion": "1",
                    "connectorRef": "connector",
                    "sourceGeneration": "generation",
                    "sourceAsOf": "2026-10-08T12:00:00Z",
                    "completeness": "complete",
                    "payees": [
                        {
                            "payeeRef": "opaque-payee",
                            "displayName": "Example Utility",
                            "activity": "active",
                            "classification": "regular",
                            "observationCount": 4,
                            "observationWindow": {
                                "firstObservedOn": "2026-06-01",
                                "lastObservedOn": "2026-09-01",
                            },
                            "intervalEvidence": None,
                            "confidence": 0.7,
                            "basis": ["regular_activity"],
                            "provenance": {
                                "transactionHistory": True,
                                "monarchRecurring": False,
                            },
                            "monarchConfirmedRecurring": None,
                        }
                    ],
                }
            )
        )
    finally:
        service.close()


def test_hub_settings_loads_documented_payee_environment(monkeypatch) -> None:
    monkeypatch.setenv("OWL_MISSION_CONTROL_API_TOKEN", "configured-token")
    monkeypatch.setenv("OWL_TYRION_PAYEE_CONNECTOR_REF", "opaque-connector")

    settings = HubSettings(_env_file=None)

    assert settings.mission_control_api_token is not None
    assert settings.mission_control_api_token.get_secret_value() == "configured-token"
    assert settings.tyrion_payee_connector_ref == "opaque-connector"


def test_mc_payee_contract_is_protected_and_records_explicit_actions(client, app, tmp_path) -> None:
    database_path = _configure(app, tmp_path)
    _seed(database_path)

    assert client.get("/api/mc/v1/payee-document-reviews").status_code == 401
    headers = {"Authorization": "Bearer mc-secret"}
    queue = client.get("/api/mc/v1/payee-document-reviews", headers=headers)
    assert queue.status_code == 200
    item = queue.json()[0]
    assert item["review_status"] == "unreviewed"
    assert item["classification"] == "regular"
    assert "payee_ref" not in item
    assert {action["id"] for action in item["source_actions"]} == {
        "set_mapping",
        "mark_no_documents_expected",
    }

    correspondents = client.get("/api/mc/v1/correspondents?query=utility", headers=headers)
    assert correspondents.status_code == 200
    assert correspondents.json()[0]["id"] == 42

    mapped = client.put(
        f"/api/mc/v1/payee-document-reviews/{item['id']}/mapping",
        headers=headers,
        json={"mappings": [{"correspondent_id": 42}], "expectation_ids": []},
    )
    assert mapped.status_code == 200
    assert mapped.json()["document_decision"] == "documents_expected"

    reviewed = client.post(
        f"/api/mc/v1/payee-document-reviews/{item['id']}/no-documents-expected",
        headers=headers,
        json={"mappings": [{"correspondent_id": 42}], "notes": "No statement issued."},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["document_decision"] == "no_documents_expected"

    history = client.get(
        f"/api/statements/payee-document-reviews/{item['id']}/history",
        headers=headers,
    )
    assert history.status_code == 200
    assert [event["new_state"]["document_decision"] for event in history.json()] == [
        "documents_expected",
        "no_documents_expected",
    ]
