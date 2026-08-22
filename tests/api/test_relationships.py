"""API tests for document relationships."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from doc_intelligence_hub.modules.triage.database import create_duplicate_pair


def test_create_query_and_remove_relationship_without_projection(client):
    create_response = client.post(
        "/api/relationships",
        json={
            "source_document_id": 20,
            "target_document_id": 10,
            "relationship_type": "follows",
            "provenance": "user",
            "reason_codes": ["second_notice"],
            "priority_adjustment": 12,
            "priority_explanation": "Priority +12: second notice",
            "project_to_paperless": False,
        },
    )
    assert create_response.status_code == 200
    body = create_response.json()
    assert body["created"] is True
    assert body["projection"] is None

    query_response = client.get("/api/relationships/documents/10?direction=incoming")
    assert query_response.status_code == 200
    assert query_response.json()["relationships"][0]["source_document_id"] == 20

    delete_response = client.delete(
        f"/api/relationships/{body['relationship']['id']}?project_to_paperless=false"
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["relationship"]["removed_at"] is not None


def test_conflict_returns_409(client):
    first = {
        "source_document_id": 20,
        "target_document_id": 10,
        "relationship_type": "follows",
        "provenance": "user",
        "project_to_paperless": False,
    }
    assert client.post("/api/relationships", json=first).status_code == 200
    response = client.post(
        "/api/relationships",
        json={
            **first,
            "source_document_id": 10,
            "target_document_id": 20,
            "relationship_type": "supersedes",
        },
    )
    assert response.status_code == 409


def test_propose_second_notice(client):
    response = client.post(
        "/api/relationships/propose",
        json={
            "left_document_id": 1,
            "right_document_id": 2,
            "left_metadata": {
                "provider": "Utility",
                "invoice_number": "ABC",
                "amount": 75,
                "document_date": "2026-07-01",
                "title": "Bill",
            },
            "right_metadata": {
                "provider": "Utility",
                "invoice_number": "ABC",
                "amount": 75,
                "document_date": "2026-08-01",
                "title": "Second Notice",
            },
        },
    )
    assert response.status_code == 200
    proposal = response.json()["proposal"]
    assert proposal["source_document_id"] == 2
    assert proposal["relationship_type"] == "follows"
    assert proposal["priority_adjustment"] == 12


def test_projection_failure_is_reported_without_losing_relationship(client):
    projection = AsyncMock(side_effect=RuntimeError("Paperless unavailable"))
    with patch(
        "doc_intelligence_hub.api.routers.relationships.project_relationships_to_paperless",
        projection,
    ):
        response = client.post(
            "/api/relationships",
            json={
                "source_document_id": 2,
                "target_document_id": 1,
                "relationship_type": "follows",
                "provenance": "user",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["relationship"]["paperless_synced"] is False
    assert body["projection"]["error"] == "Paperless unavailable"
    assert client.get("/api/relationships/documents/1").json()["count"] == 1


def test_duplicate_review_related_resolution_applies_notice_priority(client):
    pair = create_duplicate_pair(
        doc_a_id=10,
        doc_b_id=20,
        similarity_score=0.9,
        breakdown={"invoice_number": 1.0},
    )
    metadata = {
        10: {"title": "Original Bill"},
        20: {"title": "Second Notice - Past Due"},
    }
    with (
        patch(
            "doc_intelligence_hub.api.routers.duplicates.get_document_metadata",
            side_effect=lambda document_id: metadata[document_id],
        ),
        patch(
            "doc_intelligence_hub.api.routers.duplicates.project_relationships_to_paperless",
            new=AsyncMock(return_value={"synced": True, "documents": [10, 20], "error": None}),
        ),
    ):
        response = client.post(
            f"/api/duplicates/{pair['id']}/resolve",
            json={
                "resolution": "related",
                "primary_doc_id": 20,
                "relationship_type": "follows",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["duplicate"]["status"] == "not_duplicate"
    assert body["relationship"]["priority_adjustment"] == 18
    assert body["relationship"]["reason_codes"] == ["duplicate_review", "past_due"]
    assert body["relationship"]["priority_explanation"] == "Priority +18: past due"
