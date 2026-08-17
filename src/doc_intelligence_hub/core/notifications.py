"""External notification channels for Document Intelligence Hub alerts.

Currently supports Gotify push notifications. Configure via environment
variables:

    GOTIFY_URL   — Base URL of the Gotify server (e.g. https://gotify.example.com)
    GOTIFY_TOKEN — Application token for posting messages

If either variable is unset, Gotify notifications are silently skipped.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from doc_intelligence_hub.core.alerts import Alert

logger = logging.getLogger(__name__)

# Gotify priority mapping by alert severity
_GOTIFY_PRIORITY: dict[str, int] = {
    "critical": 10,
    "high": 8,
    "medium": 5,
    "low": 2,
    "info": 1,
}


def _get_gotify_config() -> tuple[str, str] | None:
    """Return (url, token) if Gotify is configured, else None."""
    url = os.environ.get("GOTIFY_URL", "").rstrip("/")
    token = os.environ.get("GOTIFY_TOKEN", "")
    if url and token:
        return url, token
    return None


def send_gotify_notification(
    title: str,
    message: str,
    priority: int = 5,
    *,
    gotify_url: str | None = None,
    gotify_token: str | None = None,
) -> bool:
    """Send a push notification via Gotify REST API.

    Args:
        title: Notification title.
        message: Notification body text.
        priority: Gotify priority (0-10, higher = more urgent).
        gotify_url: Override GOTIFY_URL env var.
        gotify_token: Override GOTIFY_TOKEN env var.

    Returns:
        True if the notification was sent successfully, False otherwise.
    """
    config = _get_gotify_config()
    url = gotify_url or (config[0] if config else "")
    token = gotify_token or (config[1] if config else "")

    if not url or not token:
        logger.debug("Gotify not configured — skipping notification")
        return False

    endpoint = f"{url}/message"
    try:
        resp = httpx.post(
            endpoint,
            params={"token": token},
            json={
                "title": title,
                "message": message,
                "priority": priority,
            },
            timeout=10,
        )
        if resp.status_code < 400:
            logger.info("Gotify notification sent: %s", title)
            return True
        else:
            logger.warning("Gotify returned HTTP %d: %s", resp.status_code, resp.text[:200])
            return False
    except Exception:
        logger.exception("Failed to send Gotify notification")
        return False


def notify_alert(alert: Alert) -> bool:
    """Send an external notification for an alert.

    Called automatically by ``emit_alert()`` for HIGH and CRITICAL severity
    alerts. Dispatches to all configured channels (currently Gotify only).

    Returns:
        True if at least one channel delivered successfully.
    """
    severity = (alert.severity or "medium").lower()
    priority = _GOTIFY_PRIORITY.get(severity, 5)

    title = f"[{severity.upper()}] {alert.title}"
    parts = []
    if alert.description:
        parts.append(alert.description)
    parts.append(f"Module: {alert.module}")
    if alert.action_url:
        parts.append(f"Action: {alert.action_url}")
    message = "\n".join(parts)

    return send_gotify_notification(title, message, priority=priority)
