"""Byte-free SQLite ledger for outbound canonical document delivery."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from .config import settings

SCHEMA_VERSION = 1

_MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS monarch_delivery_schema_version (
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS monarch_document_deliveries (
    delivery_ref TEXT PRIMARY KEY,
    document_ref_digest TEXT NOT NULL UNIQUE,
    source_ref TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    reason_code TEXT,
    acknowledgement_ref_digest TEXT,
    tyrion_revision INTEGER,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    display_tag_pending INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS monarch_delivery_events (
    event_ref TEXT PRIMARY KEY,
    delivery_ref TEXT NOT NULL,
    event_type TEXT NOT NULL,
    reason_code TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (delivery_ref) REFERENCES monarch_document_deliveries(delivery_ref)
);
"""


def _path_from_url(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("Monarch delivery supports sqlite database URLs only")
    return Path(database_url[len(prefix) :])


def connect() -> sqlite3.Connection:
    path = _path_from_url(settings.database_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def transaction(*, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with transaction(immediate=True) as connection:
        connection.executescript(_MIGRATION_1)
        row = connection.execute(
            "SELECT version FROM monarch_delivery_schema_version LIMIT 1"
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO monarch_delivery_schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
        elif row["version"] != SCHEMA_VERSION:
            raise RuntimeError(
                f"Unsupported Monarch delivery schema version {row['version']}; "
                f"expected {SCHEMA_VERSION}"
            )


def recover_interrupted_submissions() -> int:
    """Force crash-interrupted sends through reconciliation after restart."""
    with transaction(immediate=True) as connection:
        cursor = connection.execute(
            """
            UPDATE monarch_document_deliveries
            SET state = 'unknown',
                reason_code = 'interrupted_submit_requires_reconciliation',
                updated_at = ?
            WHERE state = 'submitting'
            """,
            (datetime.now(UTC).isoformat(),),
        )
        return cursor.rowcount
