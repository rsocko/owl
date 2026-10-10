"""API tests for document relationships."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from doc_intelligence_hub.modules.action_queue.database import (
    Action,
    Obligation,
    ObligationDocument,
    ObligationPaymentEdge,
    PaymentEvidence,
)
from doc_intelligence_hub.modules.action_queue.database import (
    get_session as get_action_queue_session,
)
from doc_intelligence_hub.modules.triage.database import (
    DocumentDuplicate,
    DocumentRelationship,
    TriageQueueItem,
    create_duplicate_pair,
    set_triage_setting,
)
from doc_intelligence_hub.modules.triage.database import (
    get_session as get_triage_session,
)
from doc_intelligence_hub.modules.triage.relationship_scans import (
    create_scan_run,
    get_scan_run,
    update_scan_run,
)
from doc_intelligence_hub.modules.triage.relationships import (
    create_document_relationship,
    get_document_relationship,
    set_projection_result,
)


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
    assert body["projection"]["error"] == "Failed to sync relationship projection to Paperless"
    assert client.get("/api/relationships/documents/1").json()["count"] == 1


def test_ingestion_endpoint_projects_relationship_and_is_retry_safe(client):
    set_triage_setting("duplicate_auto_detect", "true")
    relationship = {
        "id": "relationship-1",
        "source_document_id": 2,
        "target_document_id": 1,
    }
    detection_results = [
        {
            "document_id": 2,
            "skipped": False,
            "relationships_created": 1,
            "relationship_ids": ["relationship-1"],
        },
        {
            "document_id": 2,
            "skipped": False,
            "relationships_created": 0,
            "relationship_ids": ["relationship-1"],
        },
    ]
    projection = AsyncMock(return_value={"synced": True, "documents": [1, 2], "error": None})
    with (
        patch(
            "doc_intelligence_hub.api.routers.duplicates.on_document_ingested",
            side_effect=detection_results,
        ),
        patch(
            "doc_intelligence_hub.api.routers.duplicates.get_document_relationship",
            return_value=relationship,
        ),
        patch(
            "doc_intelligence_hub.api.routers.duplicates.set_projection_result"
        ) as record_projection,
        patch(
            "doc_intelligence_hub.api.routers.duplicates.project_relationships_to_paperless",
            new=projection,
        ),
    ):
        first = client.post("/api/duplicates/document-ingested", json={"document_id": 2})
        retried = client.post("/api/duplicates/document-ingested", json={"document_id": 2})

    assert first.status_code == 200
    assert retried.status_code == 200
    assert first.json()["projection"]["synced"] is True
    assert retried.json()["relationships_created"] == 0
    assert projection.await_count == 2
    assert record_projection.call_count == 2


def test_reconcile_projection_failures_after_restart(client):
    relationship, _ = create_document_relationship(
        source_document_id=2,
        target_document_id=1,
        relationship_type="follows",
        provenance="automatic",
    )
    set_projection_result(relationship["id"], synced=False, error="Paperless unavailable")
    projection = AsyncMock(return_value={"synced": True, "documents": [1, 2], "error": None})
    with patch(
        "doc_intelligence_hub.api.routers.relationships.project_relationships_to_paperless",
        new=projection,
    ):
        response = client.post("/api/relationships/reconcile-projections")

    assert response.status_code == 200
    assert response.json()["attempted"] == 1
    assert response.json()["succeeded"] == 1
    assert response.json()["failed"] == 0
    assert response.json()["remaining"] == 0
    assert response.json()["errors"] == []
    assert get_document_relationship(relationship["id"])["paperless_synced"] is True


def test_projection_health_distinguishes_opt_out_pending_and_failed(client):
    opted_out = client.post(
        "/api/relationships",
        json={
            "source_document_id": 20,
            "target_document_id": 10,
            "relationship_type": "follows",
            "project_to_paperless": False,
        },
    ).json()["relationship"]
    pending, _ = create_document_relationship(
        source_document_id=30,
        target_document_id=10,
        relationship_type="supports",
        provenance="automatic",
    )
    failed, _ = create_document_relationship(
        source_document_id=40,
        target_document_id=10,
        relationship_type="supports",
        provenance="automatic",
    )
    set_projection_result(failed["id"], synced=False, error="Paperless unavailable")

    response = client.get("/api/relationships/projection-health")

    assert response.status_code == 200
    assert response.json() == {
        "pending": 1,
        "failed": 1,
        "not_intended": 1,
        "last_error": {
            "relationship_id": failed["id"],
            "message": "Paperless unavailable",
            "recorded_at": response.json()["last_error"]["recorded_at"],
        },
    }
    assert get_document_relationship(opted_out["id"])["paperless_projection_enabled"] is False


def test_reconcile_projection_reports_partial_failure(client):
    first, _ = create_document_relationship(
        source_document_id=2,
        target_document_id=1,
        relationship_type="follows",
        provenance="automatic",
    )
    second, _ = create_document_relationship(
        source_document_id=3,
        target_document_id=1,
        relationship_type="supports",
        provenance="automatic",
    )
    projection = AsyncMock(
        side_effect=[
            {"synced": True, "documents": [1, 2], "error": None},
            {"synced": False, "documents": [1, 3], "error": "Paperless unavailable"},
        ]
    )
    with patch(
        "doc_intelligence_hub.api.routers.relationships.project_relationships_to_paperless",
        new=projection,
    ):
        response = client.post("/api/relationships/reconcile-projections")

    assert response.status_code == 200
    assert response.json() == {
        "attempted": 2,
        "succeeded": 1,
        "failed": 1,
        "remaining": 1,
        "errors": [
            {
                "relationship_id": second["id"],
                "message": "Paperless unavailable",
            }
        ],
    }
    assert get_document_relationship(first["id"])["paperless_synced"] is True
    assert get_document_relationship(second["id"])["paperless_synced"] is False


def test_historical_scan_preview_does_not_mutate(client, seed_eob):
    metadata = {
        200: {
            "title": "Original utility bill",
            "provider": "Utility",
            "invoice_number": "INV-001",
            "amount": 150,
            "document_date": "2026-06-01",
        },
        201: {
            "title": "Second notice utility bill",
            "provider": "Utility",
            "invoice_number": "INV-001",
            "amount": 150,
            "document_date": "2026-07-01",
        },
    }
    with patch(
        "doc_intelligence_hub.modules.triage.duplicates.get_document_metadata",
        side_effect=lambda document_id: metadata.get(document_id),
    ):
        response = client.post("/api/duplicates/scan/preview")

    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert response.json()["documents_examined"] == 2
    assert response.json()["relationships_created"] == 1
    db = get_triage_session()
    try:
        assert db.query(DocumentRelationship).count() == 0
        assert db.query(DocumentDuplicate).count() == 0
        assert db.query(TriageQueueItem).count() == 0
    finally:
        db.close()


def test_historical_scan_application_is_idempotent(client, seed_eob):
    metadata = {
        200: {
            "title": "Original utility bill",
            "provider": "Utility",
            "invoice_number": "INV-001",
            "amount": 150,
            "document_date": "2026-06-01",
        },
        201: {
            "title": "Second notice utility bill",
            "provider": "Utility",
            "invoice_number": "INV-001",
            "amount": 150,
            "document_date": "2026-07-01",
        },
    }
    projection = AsyncMock(return_value={"synced": True, "documents": [200, 201], "error": None})
    with (
        patch(
            "doc_intelligence_hub.modules.triage.duplicates.get_document_metadata",
            side_effect=lambda document_id: metadata.get(document_id),
        ),
        patch(
            "doc_intelligence_hub.api.routers.duplicates.project_relationships_to_paperless",
            new=projection,
        ),
    ):
        first = client.post("/api/duplicates/scan")
        second = client.post("/api/duplicates/scan")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["relationships_created"] == 1
    assert second.json()["relationships_created"] == 0
    assert second.json()["relationships_reused"] == 1
    db = get_triage_session()
    try:
        assert db.query(DocumentRelationship).count() == 1
    finally:
        db.close()


def test_tracked_scan_persists_completed_status(client):
    result = {
        "documents_examined": 12,
        "relationships_created": 2,
        "relationships_reused": 1,
        "triage_items_created": 3,
        "conflicts": 0,
        "failures": 0,
        "errors": [],
        "relationship_ids": [],
    }
    with patch(
        "doc_intelligence_hub.api.routers.duplicates.scan_all_duplicates",
        return_value=result,
    ):
        started = client.post("/api/duplicates/scan/runs")

    assert started.status_code == 202
    status = client.get(f"/api/duplicates/scan/runs/{started.json()['run_id']}")
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["counts"]["documents_examined"] == 12


def test_tracked_scan_persists_running_progress(client):
    run = create_scan_run()

    update_scan_run(
        run["run_id"],
        status="running",
        counts={"documents_examined": 4, "documents_total": 12},
    )

    stored = get_scan_run(run["run_id"])
    assert stored is not None
    assert stored["status"] == "running"
    assert stored["started_at"] is not None
    assert stored["counts"] == {"documents_examined": 4, "documents_total": 12}


def test_tracked_scan_surfaces_failure(client):
    with patch(
        "doc_intelligence_hub.api.routers.duplicates.scan_all_duplicates",
        side_effect=RuntimeError("private connector detail"),
    ):
        started = client.post("/api/duplicates/scan/runs")

    status = client.get(f"/api/duplicates/scan/runs/{started.json()['run_id']}")
    assert status.json()["status"] == "failed"
    assert status.json()["errors"] == [
        {
            "code": "RuntimeError",
            "message": "The relationship scan could not be completed.",
        }
    ]
    assert "private connector detail" not in str(status.json())


def test_tracked_scan_rejects_concurrent_run(client):
    from doc_intelligence_hub.api.routers import duplicates as duplicates_router

    active = create_scan_run()
    duplicates_router._active_scan_run_ids.add(active["run_id"])
    try:
        response = client.post("/api/duplicates/scan/runs")
    finally:
        duplicates_router._active_scan_run_ids.discard(active["run_id"])

    assert response.status_code == 409
    assert response.json()["error"]["run_id"] == active["run_id"]


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


def test_graph_projects_only_durable_owl_relationship_identities(client):
    db = get_action_queue_session()
    try:
        obligation = Obligation(
            id="obligation-graph",
            identity_key="provider:account:invoice",
            authoritative_document_id=20,
            relationship_explanation="Second notice follows the original statement.",
            relationship_rules=["second_notice"],
        )
        action = Action(
            document_id=20,
            document_title="Second notice",
            action_type="PAY",
            title="Pay utility balance",
            status="pending",
            obligation_id=obligation.id,
        )
        db.add_all([obligation, action])
        db.flush()
        obligation.primary_action_id = action.id
        db.add_all(
            [
                ObligationDocument(
                    obligation_id=obligation.id,
                    document_id=10,
                    role="invoice",
                    title="Utility statement",
                    source="relationship",
                ),
                ObligationDocument(
                    obligation_id=obligation.id,
                    document_id=20,
                    role="reminder",
                    title="Second notice",
                    source="relationship",
                ),
            ]
        )
        evidence = PaymentEvidence(
            id="evidence-graph",
            source_system="tyrion_bill_match",
            connector_ref="opaque-connector",
            evidence_ref="opaque-evidence",
            kind="posted_transaction",
            amount_minor=12500,
            currency="USD",
            source_state="posted",
            match_state="matched",
            payment_status="paid",
            reason_codes=["amount_exact"],
            source_generation="generation-1",
            source_as_of=datetime(2026, 10, 10, 12, tzinfo=UTC),
            payload_digest="digest",
        )
        db.add(evidence)
        db.add(
            ObligationPaymentEdge(
                id="payment-edge-graph",
                obligation_id=obligation.id,
                evidence_id=evidence.id,
                state="active",
                reason_codes=["amount_exact"],
            )
        )
        db.commit()
        action_id = action.id
    finally:
        db.close()

    triage_db = get_triage_session()
    try:
        triage_db.add(
            DocumentRelationship(
                id="relationship-graph",
                source_document_id=20,
                target_document_id=10,
                relationship_type="follows",
                provenance="automatic",
                reason_codes_json='["second_notice"]',
            )
        )
        triage_db.commit()
    finally:
        triage_db.close()

    response = client.get(f"/api/relationships/graph?action_id={action_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "1.0"
    assert body["scope"] == {
        "action_id": action_id,
        "obligation_id": "obligation-graph",
    }
    assert {node["id"] for node in body["nodes"]} == {
        "obligation:obligation-graph",
        f"action:{action_id}",
        "document:10",
        "document:20",
        "payment-evidence:evidence-graph",
    }
    assert "document-relationship:relationship-graph" in {edge["id"] for edge in body["edges"]}
    payment_node = next(
        node for node in body["nodes"] if node["id"] == "payment-evidence:evidence-graph"
    )
    assert payment_node["metadata"]["source_system"] == "tyrion_bill_match"
    assert "evidence_ref" not in payment_node["metadata"]
    assert body["warnings"] == [
        {
            "code": "mission_control_task_identity_unavailable",
            "message": (
                "Mission Control task nodes are omitted: OWL exports actions but does not "
                "receive a durable Mission Control task identity."
            ),
        },
        {
            "code": "tyrion_source_record_identity_private",
            "message": (
                "Tyrion source-record nodes are omitted: the graph uses OWL's durable, "
                "privacy-bounded payment-evidence identity instead of upstream references."
            ),
        },
    ]


def test_graph_returns_404_for_unknown_action(client):
    response = client.get("/api/relationships/graph?action_id=999999")

    assert response.status_code == 404
