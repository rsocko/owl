"""Outbound webhook dispatcher for Document Intelligence Hub.

Dispatches JSON payloads to registered webhook URLs with timeout and retry
logic. Delivery attempts are logged to a SQLite ``webhook_log`` table.

Event types:
    - ``statement.missing``  — a statement is expected but not yet found
    - ``statement.overdue``  — a statement is past its grace window
    - ``statement.found``    — a previously-missing statement has been located
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

VALID_EVENT_TYPES = {"statement.missing", "statement.overdue", "statement.found"}

_DEFAULT_TIMEOUT = 10.0  # seconds
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_DB_PATH = "data/webhook_log.db"

_WEBHOOK_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS webhook_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    url TEXT NOT NULL,
    description TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ws_event_type ON webhook_subscriptions(event_type);
CREATE INDEX IF NOT EXISTS idx_ws_active ON webhook_subscriptions(active);

CREATE TABLE IF NOT EXISTS webhook_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    url TEXT NOT NULL,
    payload TEXT NOT NULL,
    status_code INTEGER,
    response_body TEXT,
    success INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_wl_event_type ON webhook_log(event_type);
CREATE INDEX IF NOT EXISTS idx_wl_created_at ON webhook_log(created_at);

CREATE TABLE IF NOT EXISTS webhook_alert_state (
    provider_key TEXT NOT NULL,
    expected_date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    alerted_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (provider_key, expected_date, event_type)
);
"""


class WebhookDB:
    """Manages webhook subscriptions and delivery logs."""

    def __init__(self, db_path: str = _DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_WEBHOOK_SCHEMA_SQL)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- Subscriptions -------------------------------------------------------

    def add_subscription(
        self,
        event_type: str,
        url: str,
        description: str | None = None,
    ) -> int:
        conn = self.connect()
        cursor = conn.execute(
            "INSERT INTO webhook_subscriptions (event_type, url, description) VALUES (?, ?, ?)",
            (event_type, url, description),
        )
        conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def remove_subscription(self, subscription_id: int) -> bool:
        conn = self.connect()
        cursor = conn.execute("DELETE FROM webhook_subscriptions WHERE id = ?", (subscription_id,))
        conn.commit()
        return cursor.rowcount > 0

    def list_subscriptions(
        self, event_type: str | None = None, active_only: bool = True
    ) -> list[dict[str, Any]]:
        conn = self.connect()
        query = "SELECT * FROM webhook_subscriptions WHERE 1=1"
        params: list[Any] = []
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        if active_only:
            query += " AND active = 1"
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_urls_for_event(self, event_type: str) -> list[str]:
        """Return active webhook URLs for the given event type, including wildcard subscribers."""
        conn = self.connect()
        rows = conn.execute(
            "SELECT DISTINCT url FROM webhook_subscriptions "
            "WHERE active = 1 AND (event_type = ? OR event_type = '*')",
            (event_type,),
        ).fetchall()
        return [row["url"] for row in rows]

    def set_subscription_active(self, subscription_id: int, active: bool) -> bool:
        conn = self.connect()
        cursor = conn.execute(
            "UPDATE webhook_subscriptions SET active = ?, updated_at = datetime('now') WHERE id = ?",
            (1 if active else 0, subscription_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    # -- Delivery log --------------------------------------------------------

    def log_delivery(
        self,
        event_type: str,
        url: str,
        payload: str,
        status_code: int | None,
        response_body: str | None,
        success: bool,
        attempt: int,
        error: str | None = None,
    ) -> int:
        conn = self.connect()
        cursor = conn.execute(
            "INSERT INTO webhook_log "
            "(event_type, url, payload, status_code, response_body, success, attempt, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event_type, url, payload, status_code, response_body, int(success), attempt, error),
        )
        conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def get_recent_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM webhook_log ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    # -- Alert de-duplication state ------------------------------------------

    def was_already_alerted(self, provider_key: str, expected_date: str, event_type: str) -> bool:
        conn = self.connect()
        row = conn.execute(
            "SELECT 1 FROM webhook_alert_state "
            "WHERE provider_key = ? AND expected_date = ? AND event_type = ?",
            (provider_key, expected_date, event_type),
        ).fetchone()
        return row is not None

    def mark_alerted(self, provider_key: str, expected_date: str, event_type: str) -> None:
        conn = self.connect()
        conn.execute(
            "INSERT OR IGNORE INTO webhook_alert_state "
            "(provider_key, expected_date, event_type) VALUES (?, ?, ?)",
            (provider_key, expected_date, event_type),
        )
        conn.commit()

    def clear_alert_state(self, provider_key: str, expected_date: str | None = None) -> int:
        """Clear alert state for a provider (optionally for a specific date)."""
        conn = self.connect()
        if expected_date:
            cursor = conn.execute(
                "DELETE FROM webhook_alert_state WHERE provider_key = ? AND expected_date = ?",
                (provider_key, expected_date),
            )
        else:
            cursor = conn.execute(
                "DELETE FROM webhook_alert_state WHERE provider_key = ?",
                (provider_key,),
            )
        conn.commit()
        return cursor.rowcount


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


async def dispatch_webhook(
    event_type: str,
    payload: dict[str, Any],
    url: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    db: WebhookDB | None = None,
) -> bool:
    """POST *payload* as JSON to *url*.

    Returns ``True`` if the webhook was delivered successfully (2xx).
    Retries up to *max_retries* times on failure. Each attempt is logged
    to the ``webhook_log`` table when *db* is provided.
    """
    if event_type not in VALID_EVENT_TYPES:
        logger.warning("Invalid webhook event type: %s", event_type)
        return False

    envelope = {
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": payload,
    }
    payload_json = json.dumps(envelope, default=str)

    for attempt in range(1, max_retries + 1):
        status_code = None
        response_body = None
        error_msg = None
        success = False

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    url,
                    content=payload_json,
                    headers={"Content-Type": "application/json"},
                )
                status_code = resp.status_code
                response_body = resp.text[:1000]  # cap stored response size
                success = 200 <= resp.status_code < 300
        except httpx.TimeoutException:
            error_msg = "Request timed out"
            logger.warning(
                "Webhook timeout (attempt %d/%d): %s -> %s",
                attempt,
                max_retries,
                event_type,
                url,
            )
        except Exception as exc:
            error_msg = str(exc)[:500]
            logger.warning(
                "Webhook error (attempt %d/%d): %s -> %s: %s",
                attempt,
                max_retries,
                event_type,
                url,
                exc,
            )

        if db:
            db.log_delivery(
                event_type=event_type,
                url=url,
                payload=payload_json,
                status_code=status_code,
                response_body=response_body,
                success=success,
                attempt=attempt,
                error=error_msg,
            )

        if success:
            logger.info("Webhook delivered: %s -> %s (attempt %d)", event_type, url, attempt)
            return True

    logger.error(
        "Webhook delivery failed after %d attempts: %s -> %s",
        max_retries,
        event_type,
        url,
    )
    return False


async def dispatch_to_subscribers(
    event_type: str,
    payload: dict[str, Any],
    db: WebhookDB,
    *,
    extra_urls: list[str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> dict[str, bool]:
    """Dispatch a webhook event to all subscribers for *event_type*.

    Returns a dict mapping URL -> delivery success.
    """
    urls = set(db.get_urls_for_event(event_type))
    if extra_urls:
        urls.update(extra_urls)

    results: dict[str, bool] = {}
    for url in urls:
        results[url] = await dispatch_webhook(
            event_type, payload, url, timeout=timeout, max_retries=max_retries, db=db
        )
    return results


def get_webhook_db(db_path: str = _DEFAULT_DB_PATH) -> WebhookDB:
    """Get a WebhookDB instance for the given path."""
    return WebhookDB(db_path)
