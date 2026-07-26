"""Tests for admin endpoints (/api/admin/*)."""

from __future__ import annotations


class TestGetWeights:
    """Tests for GET /api/admin/weights."""

    def test_default_weights(self, client):
        resp = client.get("/api/admin/weights")
        assert resp.status_code == 200
        data = resp.json()
        assert data["date"] == 0.30
        assert data["provider"] == 0.25
        assert data["patient"] == 0.20
        assert data["amount"] == 0.15
        assert data["procedures"] == 0.10
        assert abs(sum(data.values()) - 1.0) < 0.01


class TestUpdateWeights:
    """Tests for PUT /api/admin/weights."""

    def test_update_weights(self, client):
        new_weights = {
            "date": 0.25,
            "provider": 0.25,
            "patient": 0.25,
            "amount": 0.15,
            "procedures": 0.10,
        }
        resp = client.put("/api/admin/weights", json=new_weights)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["weights"]["date"] == 0.25

        # Verify persistence within session
        resp2 = client.get("/api/admin/weights")
        assert resp2.json()["date"] == 0.25

    def test_weights_must_sum_to_one(self, client):
        bad_weights = {
            "date": 0.50,
            "provider": 0.50,
            "patient": 0.50,
            "amount": 0.50,
            "procedures": 0.50,
        }
        resp = client.put("/api/admin/weights", json=bad_weights)
        assert resp.status_code == 422


class TestGetSchedules:
    """Tests for GET /api/admin/schedules."""

    def test_default_schedules(self, client):
        resp = client.get("/api/admin/schedules")
        assert resp.status_code == 200
        data = resp.json()
        assert "action_queue" in data
        assert "eob_matching" in data
        assert data["action_queue"]["enabled"] is True
        assert "cron" in data["action_queue"]


class TestUpdateSchedules:
    """Tests for PUT /api/admin/schedules."""

    def test_update_schedules(self, client):
        update = {
            "action_queue": {
                "cron": "0 */4 * * *",
                "limit": 100,
                "enabled": False,
            },
        }
        resp = client.put("/api/admin/schedules", json=update)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["schedules"]["action_queue"]["cron"] == "0 */4 * * *"
        assert data["schedules"]["action_queue"]["enabled"] is False

        # eob_matching should retain defaults
        assert data["schedules"]["eob_matching"]["enabled"] is True

    def test_update_partial(self, client):
        """Updating only one schedule preserves the other."""
        resp = client.put(
            "/api/admin/schedules",
            json={"eob_matching": {"cron": "0 3 * * *", "limit": 100, "enabled": True}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["schedules"]["eob_matching"]["cron"] == "0 3 * * *"
        assert "action_queue" in data["schedules"]
