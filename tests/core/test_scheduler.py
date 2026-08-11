"""Tests for core.scheduler — cron parsing, HubScheduler config & schedules."""

from __future__ import annotations

import pytest

from doc_intelligence_hub.core.scheduler import (
    DEFAULT_SCHEDULES,
    HubScheduler,
    _build_request_body,
    _parse_cron,
)


class TestParseCron:
    def test_standard_five_field(self):
        result = _parse_cron("30 9 * * *")
        assert result == {
            "minute": "30",
            "hour": "9",
            "day": "*",
            "month": "*",
            "day_of_week": "*",
        }

    def test_complex_expression(self):
        result = _parse_cron("0 8,14 * * 1-5")
        assert result["hour"] == "8,14"
        assert result["day_of_week"] == "1-5"

    def test_rejects_wrong_field_count(self):
        with pytest.raises(ValueError, match="5-field"):
            _parse_cron("* * *")

    def test_rejects_six_fields(self):
        with pytest.raises(ValueError, match="5-field"):
            _parse_cron("0 0 * * * *")

    def test_strips_whitespace(self):
        result = _parse_cron("  0 0 1 1 *  ")
        assert result["minute"] == "0"
        assert result["month"] == "1"


class TestHubScheduler:
    def test_initial_state(self):
        sched = HubScheduler()
        assert sched.running is False
        assert sched.last_runs == {}

    def test_configure_loads_defaults(self):
        sched = HubScheduler()
        sched.configure()
        schedules = sched.get_schedules()
        assert "statement_discovery" in schedules
        assert "eob_matching" in schedules

    def test_configure_custom_schedules(self):
        sched = HubScheduler()
        sched.configure(
            {
                "my_job": {
                    "cron": "0 12 * * *",
                    "endpoint": "/api/test",
                    "method": "POST",
                    "enabled": True,
                }
            }
        )
        schedules = sched.get_schedules()
        assert "my_job" in schedules
        assert "statement_discovery" not in schedules

    def test_disabled_job_not_scheduled(self):
        sched = HubScheduler()
        sched.configure(
            {
                "disabled_job": {
                    "cron": "0 0 * * *",
                    "endpoint": "/api/noop",
                    "enabled": False,
                }
            }
        )
        schedules = sched.get_schedules()
        assert "disabled_job" in schedules
        # Job should not have a next_run since it's disabled
        assert "next_run" not in schedules["disabled_job"]

    def test_update_schedule(self):
        sched = HubScheduler()
        sched.configure()
        sched.update_schedule("statement_discovery", {"cron": "0 6 * * *"})
        schedules = sched.get_schedules()
        assert schedules["statement_discovery"]["cron"] == "0 6 * * *"

    def test_invalid_cron_handled_gracefully(self):
        sched = HubScheduler()
        # Should not raise — logs an error and skips
        sched.configure(
            {
                "bad_cron": {
                    "cron": "not a cron",
                    "endpoint": "/api/test",
                    "enabled": True,
                }
            }
        )
        schedules = sched.get_schedules()
        assert "bad_cron" in schedules


class TestDefaultSchedules:
    def test_all_defaults_have_required_keys(self):
        for key, config in DEFAULT_SCHEDULES.items():
            assert "cron" in config, f"{key} missing cron"
            assert "endpoint" in config, f"{key} missing endpoint"
            assert "method" in config, f"{key} missing method"

    def test_all_defaults_have_valid_cron(self):
        for config in DEFAULT_SCHEDULES.values():
            # Should not raise
            _parse_cron(config["cron"])


class TestScheduledRequestBody:
    def test_live_schedule_limit_overrides_static_body(self):
        body = _build_request_body(
            {
                "body": {"dry_run": False, "limit": 50},
                "limit": 12,
            }
        )

        assert body == {"dry_run": False, "limit": 12}
