from datetime import date

from fastapi.testclient import TestClient

from doc_intelligence_hub.modules.statements.api import create_app


def test_health_endpoint() -> None:
    client = TestClient(create_app("config/config.fixture.yaml"))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_recommendations_endpoint() -> None:
    client = TestClient(create_app("config/config.fixture.yaml"))

    response = client.post(f"/api/recommendations/run?as_of={date(2026, 5, 12).isoformat()}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["as_of"] == "2026-05-12"
    assert len(payload["recommendations"]) >= 2
