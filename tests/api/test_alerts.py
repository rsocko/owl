"""Tests for unified alerts endpoints (/api/insights/*)."""

from __future__ import annotations


class TestListAlerts:
    """Tests for GET /api/insights/alerts."""

    def test_empty_alerts(self, client):
        resp = client.get("/api/insights/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["alerts"] == []
        assert data["total"] == 0

    def test_list_alerts_with_data(self, client, seed_alerts):
        resp = client.get("/api/insights/alerts")
        assert resp.status_code == 200
        data = resp.json()
        # Default resolved=False, so resolved alert is excluded
        assert data["total"] == 3

    def test_list_alerts_include_resolved(self, client, seed_alerts):
        resp = client.get("/api/insights/alerts?resolved=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 4

    def test_filter_by_module(self, client, seed_alerts):
        resp = client.get("/api/insights/alerts?module=statements")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert all(a["module"] == "statements" for a in data["alerts"])

    def test_filter_by_severity(self, client, seed_alerts):
        resp = client.get("/api/insights/alerts?severity=critical")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["alerts"][0]["severity"] == "critical"

    def test_filter_unacknowledged(self, client, seed_alerts):
        resp = client.get("/api/insights/alerts?acknowledged=false")
        assert resp.status_code == 200
        data = resp.json()
        # 3 unresolved alerts, all unacknowledged
        assert data["total"] == 3

    def test_pagination(self, client, seed_alerts):
        resp = client.get("/api/insights/alerts?limit=2&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["alerts"]) == 2
        assert data["limit"] == 2
        assert data["offset"] == 0

    def test_alert_structure(self, client, seed_alerts):
        resp = client.get("/api/insights/alerts?limit=1")
        data = resp.json()
        alert = data["alerts"][0]
        assert "id" in alert
        assert "alert_type" in alert
        assert "severity" in alert
        assert "module" in alert
        assert "title" in alert
        assert "description" in alert
        assert "created_at" in alert
        assert "acknowledged_at" in alert
        assert "resolved_at" in alert


class TestAcknowledgeAlert:
    """Tests for PATCH /api/insights/alerts/{id}/acknowledge."""

    def test_acknowledge_alert(self, client, seed_alerts):
        alerts = client.get("/api/insights/alerts?acknowledged=false").json()
        alert_id = alerts["alerts"][0]["id"]

        resp = client.patch(f"/api/insights/alerts/{alert_id}/acknowledge")
        assert resp.status_code == 200
        data = resp.json()
        assert data["acknowledged_at"] is not None

    def test_acknowledge_nonexistent(self, client):
        resp = client.patch("/api/insights/alerts/99999/acknowledge")
        assert resp.status_code == 404


class TestResolveAlert:
    """Tests for PATCH /api/insights/alerts/{id}/resolve."""

    def test_resolve_alert(self, client, seed_alerts):
        alerts = client.get("/api/insights/alerts").json()
        alert_id = alerts["alerts"][0]["id"]

        resp = client.patch(f"/api/insights/alerts/{alert_id}/resolve")
        assert resp.status_code == 200
        data = resp.json()
        assert data["resolved_at"] is not None

    def test_resolve_nonexistent(self, client):
        resp = client.patch("/api/insights/alerts/99999/resolve")
        assert resp.status_code == 404


class TestAlertSummary:
    """Tests for GET /api/insights/alerts/summary."""

    def test_summary_empty(self, client):
        resp = client.get("/api/insights/alerts/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["unacknowledged"] == 0
        assert "by_severity" in data
        assert "by_module" in data

    def test_summary_with_data(self, client, seed_alerts):
        resp = client.get("/api/insights/alerts/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3  # excludes resolved
        assert data["unacknowledged"] == 3
        assert data["by_severity"]["critical"] == 1
        assert data["by_severity"]["high"] == 1
        assert data["by_severity"]["medium"] == 1
        assert data["by_module"]["statements"] == 1
        assert data["by_module"]["eob"] == 1
        assert data["by_module"]["action_queue"] == 1


class TestAlertCleanup:
    """Tests for POST /api/insights/alerts/cleanup."""

    def test_cleanup(self, client, seed_alerts):
        resp = client.post("/api/insights/alerts/cleanup?days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert "resolved" in data
        assert data["retention_days"] == 30

    def test_cleanup_custom_days(self, client):
        resp = client.post("/api/insights/alerts/cleanup?days=7")
        assert resp.status_code == 200
        data = resp.json()
        assert data["retention_days"] == 7
