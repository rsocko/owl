"""Shared logging configuration for the Document Intelligence Hub.

Supports two output formats controlled by the ``LOG_FORMAT`` environment variable:

* ``text`` (default): Human-readable format for local development.
* ``json``: Structured JSON lines for production (``docker logs``, log aggregators).

Ensures module loggers under ``doc_intelligence_hub`` emit to stdout at INFO
level, so ``docker logs`` captures pipeline activity (fetch/analyze/enrich
progress, LLM call timing, per-document errors) instead of only Uvicorn's
HTTP access lines.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime

_CONFIGURED = False


class _JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "extra_data"):
            log_entry["extra"] = record.extra_data  # type: ignore[attr-defined]
        return json.dumps(log_entry, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the ``doc_intelligence_hub`` logger tree to emit to stdout.

    Set ``LOG_FORMAT=json`` for structured JSON output (recommended in
    Docker / production).  Defaults to human-readable text.

    Idempotent — safe to call multiple times (e.g. from both the API app
    and CLI entry points) without installing duplicate handlers.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    root_logger = logging.getLogger("doc_intelligence_hub")
    root_logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)

    log_format = os.environ.get("LOG_FORMAT", "text").lower()
    if log_format == "json":
        handler.setFormatter(_JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    root_logger.addHandler(handler)
    root_logger.propagate = False

    _CONFIGURED = True


__all__ = ["configure_logging"]
