"""Shared logging configuration for the Document Intelligence Hub.

Ensures module loggers under ``doc_intelligence_hub`` emit to stdout at INFO
level, so `docker logs` captures pipeline activity (fetch/analyze/enrich
progress, LLM call timing, per-document errors) instead of only Uvicorn's
HTTP access lines.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the ``doc_intelligence_hub`` logger tree to emit to stdout.

    Idempotent — safe to call multiple times (e.g. from both the API app
    and CLI entry points) without installing duplicate handlers.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    root_logger = logging.getLogger("doc_intelligence_hub")
    root_logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
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
