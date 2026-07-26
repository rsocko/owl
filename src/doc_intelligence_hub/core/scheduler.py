"""Built-in job scheduler for the Document Intelligence Hub.

Uses APScheduler (v3) to run each module's pipeline on a cron schedule.
Jobs call the hub's own API endpoints via httpx so that all request-level
middleware, error handling, and state management is exercised identically
to external callers.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger("doc_intelligence_hub.scheduler")

# Default schedule definitions — mirrors _DEFAULT_SCHEDULES in admin.py
DEFAULT_SCHEDULES: dict[str, dict[str, Any]] = {
    "statement_discovery": {
        "cron": "0 9 * * *",
        "endpoint": "/api/statements/discovery/run",
        "method": "POST",
        "enabled": True,
    },
    "statement_gap_check": {
        "cron": "30 9 * * *",
        "endpoint": "/api/statements/recommendations/run",
        "method": "POST",
        "enabled": True,
    },
    "action_queue": {
        "cron": "0 8,14 * * *",
        "endpoint": "/api/queue/run",
        "method": "POST",
        "body": {"dry_run": False, "limit": 50},
        "limit": 50,
        "enabled": True,
    },
    "eob_matching": {
        "cron": "0 10 * * *",
        "endpoint": "/api/eob/run",
        "method": "POST",
        "body": {"limit": 200, "tags": ["medical"], "since_last_run": True},
        "limit": 200,
        "enabled": True,
    },
    "eob_benchmark": {
        "cron": "0 6 * * 1",
        "endpoint": "/api/eob/benchmark",
        "method": "POST",
        "body": {
            "models": ["phi3:mini", "gpt-4o-mini", "gpt-4o"],
            "limit": 5,
            "trigger": "scheduled",
        },
        "enabled": True,
    },
}


def _parse_cron(expr: str) -> dict[str, str]:
    """Parse a 5-field cron expression into APScheduler CronTrigger kwargs."""
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5-field cron expression, got: {expr!r}")
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
    }


class HubScheduler:
    """Manages cron-based job scheduling for all DI Hub modules."""

    def __init__(self, port: int = 8001) -> None:
        self._scheduler = AsyncIOScheduler()
        self._port = port
        self._schedules: dict[str, dict[str, Any]] = {}
        # Track last run results for the admin API
        self.last_runs: dict[str, dict[str, Any]] = {}

    @property
    def running(self) -> bool:
        return self._scheduler.running

    def get_schedules(self) -> dict[str, dict[str, Any]]:
        """Return current schedule configs with next_run times."""
        result = {}
        for key, config in self._schedules.items():
            entry = dict(config)
            job = self._scheduler.get_job(key)
            if job and job.next_run_time:
                entry["next_run"] = job.next_run_time.isoformat()
            if key in self.last_runs:
                entry["last_run"] = self.last_runs[key]
            result[key] = entry
        return result

    def configure(self, schedules: dict[str, dict[str, Any]] | None = None) -> None:
        """Load schedule configs and add/update jobs on the scheduler."""
        self._schedules = {
            k: dict(v) for k, v in (schedules or DEFAULT_SCHEDULES).items()
        }
        for key, config in self._schedules.items():
            self._upsert_job(key, config)

    def update_schedule(self, key: str, config: dict[str, Any]) -> None:
        """Update a single schedule and reschedule its job."""
        current = self._schedules.get(key, {})
        merged = {**current, **config}
        self._schedules[key] = merged
        self._upsert_job(key, merged)

    def _upsert_job(self, key: str, config: dict[str, Any]) -> None:
        """Add or replace a scheduled job."""
        # Remove existing job if present
        if self._scheduler.get_job(key):
            self._scheduler.remove_job(key)

        if not config.get("enabled", True):
            logger.info("Schedule '%s' is disabled — skipping.", key)
            return

        cron_expr = config.get("cron", "0 0 * * *")
        try:
            trigger_kwargs = _parse_cron(cron_expr)
        except ValueError:
            logger.error("Invalid cron for '%s': %s", key, cron_expr)
            return

        self._scheduler.add_job(
            self._execute_job,
            trigger=CronTrigger(**trigger_kwargs),
            id=key,
            name=f"di-hub-{key}",
            kwargs={"job_key": key, "config": config},
            replace_existing=True,
        )
        logger.info("Scheduled '%s' with cron '%s'.", key, cron_expr)

    async def _execute_job(self, job_key: str, config: dict[str, Any]) -> None:
        """Execute a scheduled job by calling the hub's own API."""
        endpoint = config.get("endpoint", "")
        method = config.get("method", "POST").upper()
        body = config.get("body")
        base_url = f"http://127.0.0.1:{self._port}"

        # For gap check, inject today's date as query param
        url = f"{base_url}{endpoint}"
        if job_key == "statement_gap_check":
            today = date.today().isoformat()
            url = f"{url}?as_of={today}"

        started_at = datetime.utcnow().isoformat()
        logger.info("▶ Running scheduled job '%s': %s %s", job_key, method, endpoint)

        try:
            async with httpx.AsyncClient(timeout=300) as client:
                if method == "POST":
                    resp = await client.post(url, json=body)
                else:
                    resp = await client.get(url)

            finished_at = datetime.utcnow().isoformat()
            status = "ok" if resp.status_code < 400 else "error"

            self.last_runs[job_key] = {
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "http_status": resp.status_code,
            }

            if status == "ok":
                logger.info(
                    "✓ Job '%s' completed (HTTP %d) in %s→%s.",
                    job_key, resp.status_code, started_at, finished_at,
                )
            else:
                logger.warning(
                    "⚠ Job '%s' returned HTTP %d: %s",
                    job_key, resp.status_code, resp.text[:200],
                )

        except Exception as exc:
            finished_at = datetime.utcnow().isoformat()
            self.last_runs[job_key] = {
                "status": "error",
                "started_at": started_at,
                "finished_at": finished_at,
                "error": str(exc),
            }
            logger.error("✗ Job '%s' failed: %s", job_key, exc)

    def start(self) -> None:
        """Start the scheduler."""
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("Scheduler started with %d jobs.", len(self._scheduler.get_jobs()))

    def shutdown(self) -> None:
        """Gracefully shut down the scheduler."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler shut down.")
