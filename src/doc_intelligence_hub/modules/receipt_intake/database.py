"""SQLite schema and repository for the byte-free receipt provenance ledger."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import settings

SCHEMA_VERSION = 1

_MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS receipt_schema_version (
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS receipt_canonical_documents (
    canonical_ref TEXT PRIMARY KEY,
    deployment_fingerprint TEXT NOT NULL,
    paperless_document_ref TEXT NOT NULL,
    blob_sha256 TEXT NOT NULL,
    normalized_content_fingerprint TEXT,
    normalized_content_version TEXT,
    semantic_fingerprint TEXT,
    semantic_fingerprint_version TEXT,
    transform_version TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (deployment_fingerprint, paperless_document_ref),
    UNIQUE (deployment_fingerprint, blob_sha256)
);
CREATE INDEX IF NOT EXISTS ix_receipt_canonical_normalized
    ON receipt_canonical_documents (
        deployment_fingerprint, normalized_content_version, normalized_content_fingerprint
    );
CREATE INDEX IF NOT EXISTS ix_receipt_canonical_semantic
    ON receipt_canonical_documents (
        deployment_fingerprint, semantic_fingerprint_version, semantic_fingerprint
    );
CREATE TABLE IF NOT EXISTS receipt_intake_attempts (
    intake_ref TEXT PRIMARY KEY,
    source_channel TEXT NOT NULL,
    source_occurrence_version TEXT NOT NULL,
    source_occurrence_id TEXT NOT NULL UNIQUE,
    connector_ref TEXT,
    source_observed_at TEXT,
    blob_sha256 TEXT NOT NULL,
    byte_count INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    transform_version TEXT,
    normalized_content_fingerprint TEXT,
    normalized_content_version TEXT,
    semantic_fingerprint TEXT,
    semantic_fingerprint_version TEXT,
    attempt_state TEXT NOT NULL,
    outcome TEXT NOT NULL,
    canonical_ref TEXT,
    paperless_task_ref TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    failure_reason_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (canonical_ref) REFERENCES receipt_canonical_documents(canonical_ref)
);
CREATE INDEX IF NOT EXISTS ix_receipt_attempt_hash
    ON receipt_intake_attempts (blob_sha256);
CREATE TABLE IF NOT EXISTS receipt_match_proposals (
    proposal_ref TEXT PRIMARY KEY,
    intake_ref TEXT NOT NULL,
    candidate_canonical_ref TEXT NOT NULL,
    proposal_kind TEXT NOT NULL,
    proposal_state TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE (intake_ref, candidate_canonical_ref, proposal_kind),
    FOREIGN KEY (intake_ref) REFERENCES receipt_intake_attempts(intake_ref),
    FOREIGN KEY (candidate_canonical_ref)
        REFERENCES receipt_canonical_documents(canonical_ref)
);
CREATE TABLE IF NOT EXISTS receipt_external_replicas (
    replica_ref TEXT PRIMARY KEY,
    canonical_ref TEXT NOT NULL,
    external_system TEXT NOT NULL,
    external_replica_ref TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    source_revision TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (external_system, external_replica_ref),
    FOREIGN KEY (canonical_ref) REFERENCES receipt_canonical_documents(canonical_ref)
);
CREATE TABLE IF NOT EXISTS receipt_audit_events (
    event_ref TEXT PRIMARY KEY,
    intake_ref TEXT,
    canonical_ref TEXT,
    event_type TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (intake_ref) REFERENCES receipt_intake_attempts(intake_ref),
    FOREIGN KEY (canonical_ref) REFERENCES receipt_canonical_documents(canonical_ref)
);
"""


def _path_from_url(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("receipt intake supports sqlite database URLs only")
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
        row = connection.execute("SELECT version FROM receipt_schema_version LIMIT 1").fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO receipt_schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
        elif row["version"] != SCHEMA_VERSION:
            raise RuntimeError(
                f"Unsupported receipt ledger schema version {row['version']}; "
                f"expected {SCHEMA_VERSION}"
            )
