"""SQLite persistence for statement-tracker (Phase 1.1).

Replaces JSON snapshots with a structured database for discovery results
and recommendations. Uses stdlib sqlite3 to avoid new dependencies.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from doc_intelligence_hub.modules.statements.correspondent_models import (
    AcquisitionSource,
    AcquisitionSourceCreate,
    AcquisitionSourceUpdate,
    Cadence,
    CorrespondentProfile,
    CorrespondentProfileUpdate,
    CorrespondentSyncResult,
    DocumentExpectation,
    DocumentExpectationCreate,
    DocumentExpectationSignalsV1,
    DocumentExpectationUpdate,
    ExpectationEvidence,
    ExternalCandidateReview,
    ExternalCandidateSnapshotResult,
    ExternalDocumentCandidate,
    IdentityResolution,
    LegacyOverrideReviewItem,
    MetadataPolicy,
    ObservedSummary,
    ProfileDefaults,
    TitleConvention,
)
from doc_intelligence_hub.modules.statements.models import (
    AnalysisPattern,
    DiscoveryResult,
    ProviderCandidate,
    Recommendation,
    RecommendationResult,
)

SCHEMA_VERSION = 5

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS discovery_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL DEFAULT (datetime('now')),
    analyzed_documents INTEGER NOT NULL,
    provider_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discovery_run_id INTEGER NOT NULL REFERENCES discovery_runs(id) ON DELETE CASCADE,
    provider_key TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    statement_name TEXT,
    correspondent_id INTEGER,
    document_count INTEGER NOT NULL,
    normalized_title TEXT NOT NULL,
    title_consistency REAL NOT NULL,
    frequency TEXT NOT NULL,
    pattern_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    anchor_day INTEGER,
    variance_days INTEGER NOT NULL DEFAULT 0,
    grace_period_days INTEGER NOT NULL DEFAULT 5,
    sample_document_ids TEXT NOT NULL DEFAULT '[]',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL DEFAULT (datetime('now')),
    as_of TEXT NOT NULL,
    recommendation_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_run_id INTEGER NOT NULL REFERENCES recommendation_runs(id) ON DELETE CASCADE,
    provider_key TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    expected_date TEXT NOT NULL,
    earliest_date TEXT NOT NULL,
    latest_date TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL,
    days_late INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_providers_run ON providers(discovery_run_id);
CREATE INDEX IF NOT EXISTS idx_providers_key ON providers(provider_key);
CREATE INDEX IF NOT EXISTS idx_recommendations_run ON recommendations(recommendation_run_id);

CREATE TABLE IF NOT EXISTS provider_overrides (
    provider_key TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    display_name TEXT,
    frequency_override TEXT,
    anchor_day_override INTEGER,
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS document_type_mapping (
    document_type_id INTEGER PRIMARY KEY,
    document_type_name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Statement series grouping tables
CREATE TABLE IF NOT EXISTS statement_series (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    correspondent_id INTEGER,
    correspondent_name TEXT NOT NULL DEFAULT 'Unknown',
    frequency TEXT DEFAULT 'monthly',
    account_identifier TEXT,
    manually_curated INTEGER NOT NULL DEFAULT 0,
    document_count INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT,
    last_seen TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS series_documents (
    series_id TEXT NOT NULL REFERENCES statement_series(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL,
    title TEXT,
    statement_date TEXT,
    period_label TEXT,
    account_hint TEXT,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (series_id, document_id)
);

CREATE INDEX IF NOT EXISTS idx_series_docs_series ON series_documents(series_id);
CREATE INDEX IF NOT EXISTS idx_series_docs_doc ON series_documents(document_id);

CREATE TABLE IF NOT EXISTS series_overrides (
    id TEXT PRIMARY KEY,
    series_id TEXT NOT NULL,
    override_type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_series_overrides_series ON series_overrides(series_id);

-- Correspondent policy tables (schema v4)
CREATE TABLE IF NOT EXISTS correspondent_profiles (
    deployment_id TEXT NOT NULL,
    correspondent_id INTEGER NOT NULL,
    current_name TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'unreviewed'
        CHECK (review_status IN ('unreviewed', 'reviewed', 'ignored')),
    lifecycle_status TEXT NOT NULL DEFAULT 'active'
        CHECK (lifecycle_status IN ('active', 'orphaned', 'retired')),
    aliases_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT,
    profile_defaults_json TEXT NOT NULL DEFAULT '{}',
    observed_summary_json TEXT NOT NULL DEFAULT '{}',
    last_analyzed_at TEXT,
    last_reviewed_at TEXT,
    orphaned_at TEXT,
    relinked_from_correspondent_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (deployment_id, correspondent_id)
);

CREATE INDEX IF NOT EXISTS idx_correspondent_profiles_review
    ON correspondent_profiles(deployment_id, lifecycle_status, review_status);

CREATE TABLE IF NOT EXISTS acquisition_sources (
    id TEXT PRIMARY KEY,
    deployment_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    delivery_mode TEXT NOT NULL,
    instructions TEXT,
    portal_url TEXT,
    automation_state TEXT NOT NULL,
    connector_type TEXT,
    connector_ref TEXT,
    availability_delay_days INTEGER,
    last_success_at TEXT,
    browser_feasibility TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_acquisition_sources_deployment
    ON acquisition_sources(deployment_id);

CREATE TABLE IF NOT EXISTS document_expectations (
    id TEXT PRIMARY KEY,
    deployment_id TEXT NOT NULL,
    correspondent_id INTEGER NOT NULL,
    kind TEXT NOT NULL
        CHECK (kind IN ('statement', 'invoice', 'bill', 'receipt', 'record', 'other')),
    document_type_id INTEGER,
    statement_series_id TEXT,
    document_ids_json TEXT NOT NULL DEFAULT '[]',
    series_discriminator TEXT,
    expectation_mode TEXT NOT NULL
        CHECK (expectation_mode IN ('recurring', 'periodic', 'one_off', 'irregular', 'not_expected')),
    status TEXT NOT NULL
        CHECK (status IN ('suggested', 'confirmed', 'dismissed', 'retired')),
    cadence_json TEXT,
    evidence_json TEXT NOT NULL,
    title_convention_json TEXT,
    metadata_policy_json TEXT NOT NULL DEFAULT '{}',
    acquisition_source_id TEXT,
    legacy_provider_key TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (deployment_id, correspondent_id)
        REFERENCES correspondent_profiles(deployment_id, correspondent_id)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_expectations_profile
    ON document_expectations(deployment_id, correspondent_id);
CREATE INDEX IF NOT EXISTS idx_expectations_alert_policy
    ON document_expectations(deployment_id, status, expectation_mode);
CREATE UNIQUE INDEX IF NOT EXISTS idx_expectations_active_statement_series
    ON document_expectations(deployment_id, statement_series_id)
    WHERE statement_series_id IS NOT NULL AND status != 'retired';
CREATE UNIQUE INDEX IF NOT EXISTS idx_expectations_legacy_key
    ON document_expectations(deployment_id, legacy_provider_key)
    WHERE legacy_provider_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS legacy_override_migrations (
    deployment_id TEXT NOT NULL,
    provider_key TEXT NOT NULL,
    resolution_status TEXT NOT NULL
        CHECK (resolution_status IN ('migrated', 'review_required', 'unmigrated')),
    reason_code TEXT NOT NULL,
    expectation_id TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (deployment_id, provider_key)
);

CREATE TABLE IF NOT EXISTS correspondent_profile_events (
    id TEXT PRIMARY KEY,
    deployment_id TEXT NOT NULL,
    correspondent_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_correspondent_profile_events_profile
    ON correspondent_profile_events(deployment_id, correspondent_id, created_at);

CREATE TABLE IF NOT EXISTS external_signal_sources (
    deployment_id TEXT NOT NULL,
    connector_ref TEXT NOT NULL,
    source_generation TEXT NOT NULL,
    source_as_of TEXT NOT NULL,
    completeness TEXT NOT NULL CHECK (completeness IN ('complete', 'partial')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (deployment_id, connector_ref)
);

CREATE TABLE IF NOT EXISTS external_signal_generations (
    deployment_id TEXT NOT NULL,
    connector_ref TEXT NOT NULL,
    source_generation TEXT NOT NULL,
    processed_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (deployment_id, connector_ref, source_generation)
);

CREATE TABLE IF NOT EXISTS external_document_candidates (
    id TEXT PRIMARY KEY,
    deployment_id TEXT NOT NULL,
    connector_ref TEXT NOT NULL,
    series_ref TEXT NOT NULL,
    source_generation TEXT NOT NULL,
    source_as_of TEXT NOT NULL,
    kind TEXT NOT NULL
        CHECK (kind IN ('accountStatementCandidate', 'recurringDocumentCandidate')),
    active INTEGER NOT NULL,
    display_hint TEXT NOT NULL,
    cadence TEXT,
    next_expected_date TEXT,
    confidence REAL NOT NULL,
    basis_json TEXT NOT NULL,
    outcome TEXT NOT NULL DEFAULT 'unreviewed'
        CHECK (outcome IN ('unreviewed', 'mapped', 'suggested', 'ambiguous', 'not_applicable')),
    expectation_id TEXT,
    correspondent_id INTEGER,
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (deployment_id, connector_ref, series_ref),
    FOREIGN KEY (expectation_id) REFERENCES document_expectations(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_external_candidates_review
    ON external_document_candidates(deployment_id, active, outcome);
CREATE INDEX IF NOT EXISTS idx_external_candidates_correspondent
    ON external_document_candidates(deployment_id, correspondent_id, kind, active);
"""


class Database:
    """SQLite persistence manager for statement-tracker."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    @property
    def path(self) -> str:
        return self._db_path

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._initialize_schema()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _initialize_schema(self) -> None:
        conn = self._conn
        conn.executescript(_SCHEMA_SQL)
        provider_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(providers)").fetchall()
        }
        if "statement_name" not in provider_columns:
            conn.execute("ALTER TABLE providers ADD COLUMN statement_name TEXT")
        expectation_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(document_expectations)").fetchall()
        }
        if "document_ids_json" not in expectation_columns:
            conn.execute(
                "ALTER TABLE document_expectations "
                "ADD COLUMN document_ids_json TEXT NOT NULL DEFAULT '[]'"
            )
        conn.execute(
            """UPDATE document_expectations
               SET status = 'suggested', updated_at = datetime('now')
               WHERE status = 'confirmed'
                 AND kind != 'statement'
                 AND expectation_mode != 'not_expected'
                 AND document_ids_json = '[]'"""
        )
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        elif row["version"] < SCHEMA_VERSION:
            conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
        elif row["version"] > SCHEMA_VERSION:
            raise RuntimeError(
                f"Statement database schema {row['version']} is newer than supported "
                f"version {SCHEMA_VERSION}"
            )
        conn.commit()

    # ----- Discovery persistence -----

    def save_discovery(self, result: DiscoveryResult) -> int:
        """Persist a discovery result. Returns the run ID."""
        conn = self.connect()
        cursor = conn.execute(
            "INSERT INTO discovery_runs (analyzed_documents, provider_count) VALUES (?, ?)",
            (result.analyzed_documents, len(result.providers)),
        )
        run_id = cursor.lastrowid

        for provider in result.providers:
            conn.execute(
                """INSERT INTO providers (
                    discovery_run_id, provider_key, provider_name, statement_name, correspondent_id,
                    document_count, normalized_title, title_consistency,
                    frequency, pattern_type, confidence, anchor_day,
                    variance_days, grace_period_days, sample_document_ids,
                    first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    provider.provider_key,
                    provider.provider_name,
                    provider.statement_name,
                    provider.correspondent_id,
                    provider.document_count,
                    provider.normalized_title,
                    provider.title_consistency,
                    provider.pattern.frequency,
                    provider.pattern.pattern_type,
                    provider.pattern.confidence,
                    provider.pattern.anchor_day,
                    provider.pattern.variance_days,
                    provider.pattern.grace_period_days,
                    json.dumps(provider.sample_document_ids),
                    provider.first_seen.isoformat(),
                    provider.last_seen.isoformat(),
                ),
            )

        conn.commit()
        return run_id

    def load_latest_discovery(self) -> DiscoveryResult | None:
        """Load the most recent discovery result, or None."""
        conn = self.connect()
        run_row = conn.execute(
            "SELECT id, analyzed_documents FROM discovery_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if run_row is None:
            return None

        run_id = run_row["id"]
        rows = conn.execute(
            "SELECT * FROM providers WHERE discovery_run_id = ? ORDER BY provider_name",
            (run_id,),
        ).fetchall()

        providers = [
            ProviderCandidate(
                provider_key=row["provider_key"],
                provider_name=row["provider_name"],
                statement_name=row["statement_name"],
                correspondent_id=row["correspondent_id"],
                document_count=row["document_count"],
                normalized_title=row["normalized_title"],
                title_consistency=row["title_consistency"],
                pattern=AnalysisPattern(
                    frequency=row["frequency"],
                    pattern_type=row["pattern_type"],
                    confidence=row["confidence"],
                    anchor_day=row["anchor_day"],
                    variance_days=row["variance_days"],
                    grace_period_days=row["grace_period_days"],
                ),
                sample_document_ids=json.loads(row["sample_document_ids"]),
                first_seen=date.fromisoformat(row["first_seen"]),
                last_seen=date.fromisoformat(row["last_seen"]),
            )
            for row in rows
        ]

        return DiscoveryResult(
            analyzed_documents=run_row["analyzed_documents"], providers=providers
        )

    # ----- Recommendations persistence -----

    def save_recommendations(self, result: RecommendationResult) -> int:
        """Persist a recommendation result. Returns the run ID."""
        conn = self.connect()
        cursor = conn.execute(
            "INSERT INTO recommendation_runs (as_of, recommendation_count) VALUES (?, ?)",
            (result.as_of.isoformat(), len(result.recommendations)),
        )
        run_id = cursor.lastrowid

        for rec in result.recommendations:
            conn.execute(
                """INSERT INTO recommendations (
                    recommendation_run_id, provider_key, provider_name,
                    expected_date, earliest_date, latest_date,
                    status, priority, days_late
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    rec.provider_key,
                    rec.provider_name,
                    rec.expected_date.isoformat(),
                    rec.earliest_date.isoformat(),
                    rec.latest_date.isoformat(),
                    rec.status,
                    rec.priority,
                    rec.days_late,
                ),
            )

        conn.commit()
        return run_id

    def load_latest_recommendations(self) -> RecommendationResult | None:
        """Load the most recent recommendation result, or None."""
        conn = self.connect()
        run_row = conn.execute(
            "SELECT id, as_of FROM recommendation_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if run_row is None:
            return None

        run_id = run_row["id"]
        rows = conn.execute(
            "SELECT * FROM recommendations WHERE recommendation_run_id = ? ORDER BY priority DESC, expected_date",
            (run_id,),
        ).fetchall()

        recs = [
            Recommendation(
                provider_key=row["provider_key"],
                provider_name=row["provider_name"],
                expected_date=date.fromisoformat(row["expected_date"]),
                earliest_date=date.fromisoformat(row["earliest_date"]),
                latest_date=date.fromisoformat(row["latest_date"]),
                status=row["status"],
                priority=row["priority"],
                days_late=row["days_late"],
            )
            for row in rows
        ]

        return RecommendationResult(
            as_of=date.fromisoformat(run_row["as_of"]), recommendations=recs
        )

    # ----- History -----

    def list_discovery_runs(self, limit: int = 10) -> list[dict]:
        """Return recent discovery run summaries."""
        conn = self.connect()
        rows = conn.execute(
            "SELECT id, run_at, analyzed_documents, provider_count "
            "FROM discovery_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_recommendation_runs(self, limit: int = 10) -> list[dict]:
        """Return recent recommendation run summaries."""
        conn = self.connect()
        rows = conn.execute(
            "SELECT id, run_at, as_of, recommendation_count "
            "FROM recommendation_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ----- Provider lookup -----

    def get_provider_by_key(self, provider_key: str) -> dict | None:
        """Look up a provider from the latest discovery run by its provider_key."""
        conn = self.connect()
        run_row = conn.execute("SELECT id FROM discovery_runs ORDER BY id DESC LIMIT 1").fetchone()
        if run_row is None:
            return None
        row = conn.execute(
            "SELECT * FROM providers WHERE discovery_run_id = ? AND provider_key = ?",
            (run_row["id"], provider_key),
        ).fetchone()
        return dict(row) if row else None

    # ----- Provider overrides -----

    def get_provider_overrides(self) -> dict[str, dict]:
        """Return all provider overrides keyed by provider_key."""
        conn = self.connect()
        rows = conn.execute("SELECT * FROM provider_overrides").fetchall()
        return {row["provider_key"]: dict(row) for row in rows}

    def set_provider_override(
        self,
        provider_key: str,
        status: str = "pending",
        display_name: str | None = None,
        frequency_override: str | None = None,
        anchor_day_override: int | None = None,
        notes: str | None = None,
    ) -> None:
        """Create or update a provider override."""
        conn = self.connect()
        conn.execute(
            """INSERT INTO provider_overrides
                (provider_key, status, display_name, frequency_override, anchor_day_override, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(provider_key) DO UPDATE SET
                status = excluded.status,
                display_name = excluded.display_name,
                frequency_override = excluded.frequency_override,
                anchor_day_override = excluded.anchor_day_override,
                notes = excluded.notes,
                updated_at = datetime('now')""",
            (provider_key, status, display_name, frequency_override, anchor_day_override, notes),
        )
        conn.commit()

    def delete_provider_override(self, provider_key: str) -> None:
        """Remove a provider override."""
        conn = self.connect()
        conn.execute("DELETE FROM provider_overrides WHERE provider_key = ?", (provider_key,))
        conn.commit()

    # ----- Document type mapping -----

    def save_document_type_mapping(self, mapping: list[dict]) -> None:
        """Save document type mapping. Each entry: {id, name, enabled}."""
        conn = self.connect()
        conn.execute("DELETE FROM document_type_mapping")
        for entry in mapping:
            conn.execute(
                """INSERT INTO document_type_mapping (document_type_id, document_type_name, enabled, updated_at)
                VALUES (?, ?, ?, datetime('now'))""",
                (entry["id"], entry["name"], 1 if entry.get("enabled") else 0),
            )
        conn.commit()

    def load_document_type_mapping(self) -> list[dict]:
        """Load the saved document type mapping."""
        conn = self.connect()
        rows = conn.execute(
            "SELECT document_type_id, document_type_name, enabled FROM document_type_mapping"
        ).fetchall()
        return [
            {
                "id": row["document_type_id"],
                "name": row["document_type_name"],
                "enabled": bool(row["enabled"]),
            }
            for row in rows
        ]

    # ----- Statement series -----

    def list_series(
        self,
        correspondent: str | None = None,
        flagged: bool = False,
    ) -> list[dict]:
        """List statement series with optional filters."""
        conn = self.connect()
        sql = "SELECT * FROM statement_series WHERE 1=1"
        params: list = []
        if correspondent:
            sql += " AND correspondent_name = ?"
            params.append(correspondent)
        if flagged:
            # Flagged series: not manually curated (still needs review)
            sql += " AND manually_curated = 0"
        sql += " ORDER BY correspondent_name, name"
        rows = conn.execute(sql, params).fetchall()
        return [self._series_row_to_dict(row) for row in rows]

    def get_series(self, series_id: str) -> dict | None:
        """Get a single statement series by ID."""
        conn = self.connect()
        row = conn.execute("SELECT * FROM statement_series WHERE id = ?", (series_id,)).fetchone()
        return self._series_row_to_dict(row) if row else None

    def get_series_documents(self, series_id: str) -> list[dict]:
        """Get all documents belonging to a series, sorted by date."""
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM series_documents WHERE series_id = ? ORDER BY statement_date ASC",
            (series_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_similar_series(self, series_id: str) -> list[dict]:
        """Get other series from the same correspondent."""
        conn = self.connect()
        row = conn.execute(
            "SELECT correspondent_name FROM statement_series WHERE id = ?", (series_id,)
        ).fetchone()
        if not row:
            return []
        rows = conn.execute(
            "SELECT * FROM statement_series WHERE correspondent_name = ? AND id != ? ORDER BY name",
            (row["correspondent_name"], series_id),
        ).fetchall()
        return [self._series_row_to_dict(row) for row in rows]

    @staticmethod
    def _series_row_to_dict(row: sqlite3.Row) -> dict:
        """Convert a series row to a dict with proper boolean for manually_curated."""
        d = dict(row)
        d["manually_curated"] = bool(d.get("manually_curated"))
        return d

    def create_series(
        self,
        series_id: str,
        name: str,
        correspondent_name: str,
        correspondent_id: int | None = None,
        frequency: str = "monthly",
        account_identifier: str | None = None,
    ) -> dict:
        """Create a new statement series."""
        conn = self.connect()
        conn.execute(
            """INSERT INTO statement_series
                (id, name, correspondent_name, correspondent_id, frequency, account_identifier, manually_curated)
            VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (series_id, name, correspondent_name, correspondent_id, frequency, account_identifier),
        )
        conn.commit()
        return self.get_series(series_id)  # type: ignore[return-value]

    def update_series(
        self,
        series_id: str,
        name: str | None = None,
        account_identifier: str | None = None,
        manually_curated: bool | None = None,
    ) -> dict | None:
        """Update series fields."""
        conn = self.connect()
        updates: list[str] = []
        params: list = []
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if account_identifier is not None:
            updates.append("account_identifier = ?")
            params.append(account_identifier)
        if manually_curated is not None:
            updates.append("manually_curated = ?")
            params.append(1 if manually_curated else 0)
        if not updates:
            return self.get_series(series_id)
        params.append(series_id)
        conn.execute(
            f"UPDATE statement_series SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()
        return self.get_series(series_id)

    def update_series_frequency(self, series_id: str, frequency: str) -> None:
        """Update the frequency field for a series."""
        conn = self.connect()
        conn.execute(
            "UPDATE statement_series SET frequency = ? WHERE id = ?",
            (frequency, series_id),
        )
        conn.commit()

    def add_documents_to_series(self, series_id: str, documents: list[dict]) -> None:
        """Add documents to a series. Each dict: document_id, title?, statement_date?, period_label?, account_hint?."""
        conn = self.connect()
        for doc in documents:
            conn.execute(
                """INSERT OR REPLACE INTO series_documents
                    (series_id, document_id, title, statement_date, period_label, account_hint)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    series_id,
                    doc["document_id"],
                    doc.get("title"),
                    doc.get("statement_date"),
                    doc.get("period_label"),
                    doc.get("account_hint"),
                ),
            )
        self._refresh_series_counts(series_id)
        conn.commit()

    def remove_documents_from_series(self, series_id: str, document_ids: list[str]) -> None:
        """Remove documents from a series."""
        if not document_ids:
            return
        conn = self.connect()
        placeholders = ",".join("?" for _ in document_ids)
        conn.execute(
            f"DELETE FROM series_documents WHERE series_id = ? AND document_id IN ({placeholders})",
            [series_id, *document_ids],
        )
        self._refresh_series_counts(series_id)
        conn.commit()

    def _refresh_series_counts(self, series_id: str) -> None:
        """Recalculate document_count, first_seen, last_seen for a series."""
        conn = self.connect()
        row = conn.execute(
            """SELECT COUNT(*) as cnt,
                      MIN(statement_date) as first_seen,
                      MAX(statement_date) as last_seen
               FROM series_documents WHERE series_id = ?""",
            (series_id,),
        ).fetchone()
        conn.execute(
            "UPDATE statement_series SET document_count = ?, first_seen = ?, last_seen = ? WHERE id = ?",
            (row["cnt"], row["first_seen"], row["last_seen"], series_id),
        )

    def save_series_override(
        self,
        override_id: str,
        series_id: str,
        override_type: str,
        payload: dict,
    ) -> None:
        """Record a series override (rename, merge_into, split_from, add_doc, remove_doc)."""
        conn = self.connect()
        conn.execute(
            "INSERT INTO series_overrides (id, series_id, override_type, payload) VALUES (?, ?, ?, ?)",
            (override_id, series_id, override_type, json.dumps(payload)),
        )
        conn.commit()

    def get_series_overrides(self, series_id: str) -> list[dict]:
        """Get all overrides for a series."""
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM series_overrides WHERE series_id = ? ORDER BY created_at DESC",
            (series_id,),
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
            result.append(d)
        return result

    def delete_series(self, series_id: str) -> None:
        """Delete a series and its documents while retaining correction history."""
        conn = self.connect()
        conn.execute("DELETE FROM series_documents WHERE series_id = ?", (series_id,))
        conn.execute("DELETE FROM statement_series WHERE id = ?", (series_id,))
        conn.commit()

    def get_enabled_document_type_names(self) -> set[str] | None:
        """Return the set of enabled document type names, or None if no mapping configured."""
        conn = self.connect()
        rows = conn.execute(
            "SELECT document_type_name FROM document_type_mapping WHERE enabled = 1"
        ).fetchall()
        if not rows:
            # Check if there's *any* mapping saved (all disabled vs not configured)
            total = conn.execute("SELECT COUNT(*) FROM document_type_mapping").fetchone()[0]
            if total == 0:
                return None  # No mapping configured — use keyword fallback
            return set()  # Mapping exists but nothing enabled
        return {row["document_type_name"] for row in rows}

    # ----- Correspondent profiles and expectations -----

    @staticmethod
    def _profile_row_to_model(row: sqlite3.Row) -> CorrespondentProfile:
        return CorrespondentProfile(
            correspondent_id=row["correspondent_id"],
            current_name=row["current_name"],
            review_status=row["review_status"],
            lifecycle_status=row["lifecycle_status"],
            aliases=json.loads(row["aliases_json"]),
            notes=row["notes"],
            profile_defaults=ProfileDefaults.model_validate_json(row["profile_defaults_json"]),
            observed_summary=ObservedSummary.model_validate_json(row["observed_summary_json"]),
            last_analyzed_at=row["last_analyzed_at"],
            last_reviewed_at=row["last_reviewed_at"],
            orphaned_at=row["orphaned_at"],
            relinked_from_correspondent_id=row["relinked_from_correspondent_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _expectation_row_to_model(row: sqlite3.Row) -> DocumentExpectation:
        return DocumentExpectation(
            id=row["id"],
            correspondent_id=row["correspondent_id"],
            kind=row["kind"],
            document_type_id=row["document_type_id"],
            statement_series_id=row["statement_series_id"],
            document_ids=json.loads(row["document_ids_json"]),
            series_discriminator=row["series_discriminator"],
            expectation_mode=row["expectation_mode"],
            status=row["status"],
            cadence=Cadence.model_validate_json(row["cadence_json"])
            if row["cadence_json"]
            else None,
            evidence=ExpectationEvidence.model_validate_json(row["evidence_json"]),
            title_convention=TitleConvention.model_validate_json(row["title_convention_json"])
            if row["title_convention_json"]
            else None,
            metadata_policy=MetadataPolicy.model_validate_json(row["metadata_policy_json"]),
            acquisition_source_id=row["acquisition_source_id"],
            legacy_provider_key=row["legacy_provider_key"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _acquisition_row_to_model(row: sqlite3.Row) -> AcquisitionSource:
        return AcquisitionSource(
            id=row["id"],
            channel=row["channel"],
            delivery_mode=row["delivery_mode"],
            instructions=row["instructions"],
            portal_url=row["portal_url"],
            automation_state=row["automation_state"],
            connector_type=row["connector_type"],
            connector_ref=row["connector_ref"],
            availability_delay_days=row["availability_delay_days"],
            last_success_at=row["last_success_at"],
            browser_feasibility=row["browser_feasibility"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def reconcile_correspondents(
        self, deployment_id: str, correspondents: list[dict[str, Any]]
    ) -> CorrespondentSyncResult:
        """Sync Paperless identities without guessing rename, deletion, or merge relationships."""
        conn = self.connect()
        current: dict[int, str] = {}
        for item in correspondents:
            correspondent_id = int(item["id"])
            name = str(item["name"]).strip()
            if correspondent_id <= 0 or not name:
                raise ValueError(
                    "Paperless correspondents require a positive ID and non-empty name"
                )
            current[correspondent_id] = name

        created = updated = orphaned = restored = 0
        with conn:
            existing_rows = conn.execute(
                "SELECT * FROM correspondent_profiles WHERE deployment_id = ?",
                (deployment_id,),
            ).fetchall()
            existing = {row["correspondent_id"]: row for row in existing_rows}

            for correspondent_id, name in current.items():
                row = existing.get(correspondent_id)
                if row is None:
                    conn.execute(
                        """INSERT INTO correspondent_profiles
                           (deployment_id, correspondent_id, current_name)
                           VALUES (?, ?, ?)""",
                        (deployment_id, correspondent_id, name),
                    )
                    created += 1
                    continue

                lifecycle = row["lifecycle_status"]
                next_lifecycle = "active" if lifecycle == "orphaned" else lifecycle
                if row["current_name"] != name or next_lifecycle != lifecycle:
                    conn.execute(
                        """UPDATE correspondent_profiles
                           SET current_name = ?, lifecycle_status = ?, orphaned_at = NULL,
                               updated_at = datetime('now')
                           WHERE deployment_id = ? AND correspondent_id = ?""",
                        (name, next_lifecycle, deployment_id, correspondent_id),
                    )
                    updated += 1
                    if lifecycle == "orphaned":
                        restored += 1

            for correspondent_id, row in existing.items():
                if correspondent_id not in current and row["lifecycle_status"] == "active":
                    conn.execute(
                        """UPDATE correspondent_profiles
                           SET lifecycle_status = 'orphaned',
                               orphaned_at = datetime('now'),
                               updated_at = datetime('now')
                           WHERE deployment_id = ? AND correspondent_id = ?""",
                        (deployment_id, correspondent_id),
                    )
                    self._insert_profile_event(
                        conn,
                        deployment_id,
                        correspondent_id,
                        "paperless_correspondent_orphaned",
                    )
                    orphaned += 1

        return CorrespondentSyncResult(
            created=created,
            updated=updated,
            orphaned=orphaned,
            restored=restored,
        )

    def list_correspondent_profiles(self, deployment_id: str) -> list[CorrespondentProfile]:
        conn = self.connect()
        rows = conn.execute(
            """SELECT * FROM correspondent_profiles
               WHERE deployment_id = ?
               ORDER BY lifecycle_status, current_name COLLATE NOCASE""",
            (deployment_id,),
        ).fetchall()
        return [self._profile_row_to_model(row) for row in rows]

    def get_correspondent_profile(
        self, deployment_id: str, correspondent_id: int
    ) -> CorrespondentProfile | None:
        conn = self.connect()
        row = conn.execute(
            """SELECT * FROM correspondent_profiles
               WHERE deployment_id = ? AND correspondent_id = ?""",
            (deployment_id, correspondent_id),
        ).fetchone()
        return self._profile_row_to_model(row) if row else None

    def update_correspondent_profile(
        self,
        deployment_id: str,
        correspondent_id: int,
        update: CorrespondentProfileUpdate,
    ) -> CorrespondentProfile:
        conn = self.connect()
        if self.get_correspondent_profile(deployment_id, correspondent_id) is None:
            raise KeyError("correspondent_profile_not_found")

        values = update.model_dump(exclude_unset=True)
        columns: dict[str, Any] = {}
        direct_fields = {
            "review_status",
            "lifecycle_status",
            "notes",
            "last_analyzed_at",
            "last_reviewed_at",
        }
        for field in direct_fields & values.keys():
            if values[field] is not None or field == "notes":
                columns[field] = values[field]
        if "aliases" in values and values["aliases"] is not None:
            columns["aliases_json"] = json.dumps(values["aliases"])
        if "profile_defaults" in values:
            defaults = update.profile_defaults or ProfileDefaults()
            columns["profile_defaults_json"] = defaults.model_dump_json()
        if "observed_summary" in values:
            summary = update.observed_summary or ObservedSummary()
            columns["observed_summary_json"] = summary.model_dump_json()
        if not columns:
            profile = self.get_correspondent_profile(deployment_id, correspondent_id)
            assert profile is not None
            return profile

        assignments = ", ".join(f"{column} = ?" for column in columns)
        with conn:
            conn.execute(
                f"""UPDATE correspondent_profiles
                    SET {assignments}, updated_at = datetime('now')
                    WHERE deployment_id = ? AND correspondent_id = ?""",
                [*columns.values(), deployment_id, correspondent_id],
            )
            self._insert_profile_event(
                conn,
                deployment_id,
                correspondent_id,
                "profile_updated",
                {"fields": sorted(values)},
            )
        profile = self.get_correspondent_profile(deployment_id, correspondent_id)
        assert profile is not None
        return profile

    def relink_correspondent_profile(
        self,
        deployment_id: str,
        old_correspondent_id: int,
        new_correspondent_id: int,
        new_name: str,
    ) -> CorrespondentProfile:
        """Explicitly relink an orphan to a current Paperless identity."""
        conn = self.connect()
        source = conn.execute(
            """SELECT * FROM correspondent_profiles
               WHERE deployment_id = ? AND correspondent_id = ?""",
            (deployment_id, old_correspondent_id),
        ).fetchone()
        if source is None:
            raise KeyError("correspondent_profile_not_found")
        if source["lifecycle_status"] != "orphaned":
            raise ValueError("Only orphaned profiles can be relinked")

        target = conn.execute(
            """SELECT * FROM correspondent_profiles
               WHERE deployment_id = ? AND correspondent_id = ?""",
            (deployment_id, new_correspondent_id),
        ).fetchone()
        if target is not None:
            target_expectations = conn.execute(
                """SELECT COUNT(*) FROM document_expectations
                   WHERE deployment_id = ? AND correspondent_id = ?""",
                (deployment_id, new_correspondent_id),
            ).fetchone()[0]
            target_has_policy = (
                target_expectations > 0
                or target["review_status"] != "unreviewed"
                or bool(target["notes"])
                or json.loads(target["aliases_json"])
            )
            if target_has_policy:
                raise ValueError("Target correspondent already has reviewed OWL policy")

        with conn:
            if target is not None:
                conn.execute(
                    """DELETE FROM correspondent_profiles
                       WHERE deployment_id = ? AND correspondent_id = ?""",
                    (deployment_id, new_correspondent_id),
                )
            conn.execute(
                """UPDATE statement_series
                   SET correspondent_id = ?, correspondent_name = ?
                   WHERE correspondent_id = ?""",
                (new_correspondent_id, new_name, old_correspondent_id),
            )
            conn.execute(
                """UPDATE correspondent_profiles
                   SET correspondent_id = ?, current_name = ?, lifecycle_status = 'active',
                       orphaned_at = NULL, relinked_from_correspondent_id = ?,
                       updated_at = datetime('now')
                   WHERE deployment_id = ? AND correspondent_id = ?""",
                (
                    new_correspondent_id,
                    new_name,
                    old_correspondent_id,
                    deployment_id,
                    old_correspondent_id,
                ),
            )
            self._insert_profile_event(
                conn,
                deployment_id,
                new_correspondent_id,
                "profile_relinked",
                {"from_correspondent_id": old_correspondent_id},
            )

        profile = self.get_correspondent_profile(deployment_id, new_correspondent_id)
        assert profile is not None
        return profile

    @staticmethod
    def _insert_profile_event(
        conn: sqlite3.Connection,
        deployment_id: str,
        correspondent_id: int,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO correspondent_profile_events
               (id, deployment_id, correspondent_id, event_type, payload_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                uuid.uuid4().hex,
                deployment_id,
                correspondent_id,
                event_type,
                json.dumps(payload or {}, sort_keys=True),
            ),
        )

    def create_acquisition_source(
        self, deployment_id: str, source: AcquisitionSourceCreate
    ) -> AcquisitionSource:
        conn = self.connect()
        source_id = uuid.uuid4().hex
        with conn:
            conn.execute(
                """INSERT INTO acquisition_sources (
                    id, deployment_id, channel, delivery_mode, instructions, portal_url,
                    automation_state, connector_type, connector_ref, availability_delay_days,
                    last_success_at, browser_feasibility
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source_id,
                    deployment_id,
                    source.channel,
                    source.delivery_mode,
                    source.instructions,
                    source.portal_url,
                    source.automation_state,
                    source.connector_type,
                    source.connector_ref,
                    source.availability_delay_days,
                    source.last_success_at,
                    source.browser_feasibility,
                ),
            )
        created = self.get_acquisition_source(deployment_id, source_id)
        assert created is not None
        return created

    def get_acquisition_source(
        self, deployment_id: str, source_id: str
    ) -> AcquisitionSource | None:
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM acquisition_sources WHERE deployment_id = ? AND id = ?",
            (deployment_id, source_id),
        ).fetchone()
        return self._acquisition_row_to_model(row) if row else None

    def list_acquisition_sources(self, deployment_id: str) -> list[AcquisitionSource]:
        conn = self.connect()
        rows = conn.execute(
            """SELECT * FROM acquisition_sources
               WHERE deployment_id = ? ORDER BY created_at, id""",
            (deployment_id,),
        ).fetchall()
        return [self._acquisition_row_to_model(row) for row in rows]

    def update_acquisition_source(
        self,
        deployment_id: str,
        source_id: str,
        update: AcquisitionSourceUpdate,
    ) -> AcquisitionSource:
        current = self.get_acquisition_source(deployment_id, source_id)
        if current is None:
            raise KeyError("acquisition_source_not_found")
        data = current.model_dump(exclude={"id", "created_at", "updated_at"})
        data.update(update.model_dump(exclude_unset=True))
        validated = AcquisitionSourceCreate.model_validate(data)
        conn = self.connect()
        with conn:
            conn.execute(
                """UPDATE acquisition_sources SET
                    channel = ?, delivery_mode = ?, instructions = ?, portal_url = ?,
                    automation_state = ?, connector_type = ?, connector_ref = ?,
                    availability_delay_days = ?, last_success_at = ?,
                    browser_feasibility = ?, updated_at = datetime('now')
                   WHERE deployment_id = ? AND id = ?""",
                (
                    validated.channel,
                    validated.delivery_mode,
                    validated.instructions,
                    validated.portal_url,
                    validated.automation_state,
                    validated.connector_type,
                    validated.connector_ref,
                    validated.availability_delay_days,
                    validated.last_success_at,
                    validated.browser_feasibility,
                    deployment_id,
                    source_id,
                ),
            )
        result = self.get_acquisition_source(deployment_id, source_id)
        assert result is not None
        return result

    def _validate_expectation_references(
        self,
        deployment_id: str,
        correspondent_id: int,
        expectation: DocumentExpectationCreate | DocumentExpectation,
    ) -> None:
        if self.get_correspondent_profile(deployment_id, correspondent_id) is None:
            raise KeyError("correspondent_profile_not_found")
        if expectation.statement_series_id:
            series = self.get_series(expectation.statement_series_id)
            if series is None:
                raise KeyError("statement_series_not_found")
            if series.get("correspondent_id") not in (None, correspondent_id):
                raise ValueError("StatementSeries belongs to a different correspondent")
        if (
            expectation.acquisition_source_id
            and self.get_acquisition_source(deployment_id, expectation.acquisition_source_id)
            is None
        ):
            raise KeyError("acquisition_source_not_found")

    def create_document_expectation(
        self,
        deployment_id: str,
        correspondent_id: int,
        expectation: DocumentExpectationCreate,
        *,
        expectation_id: str | None = None,
        legacy_provider_key: str | None = None,
    ) -> DocumentExpectation:
        self._validate_expectation_references(deployment_id, correspondent_id, expectation)
        conn = self.connect()
        expectation_id = expectation_id or uuid.uuid4().hex
        with conn:
            self._insert_document_expectation(
                conn,
                deployment_id,
                correspondent_id,
                expectation,
                expectation_id=expectation_id,
                legacy_provider_key=legacy_provider_key,
            )
        created = self.get_document_expectation(deployment_id, expectation_id)
        assert created is not None
        return created

    @staticmethod
    def _insert_document_expectation(
        conn: sqlite3.Connection,
        deployment_id: str,
        correspondent_id: int,
        expectation: DocumentExpectationCreate,
        *,
        expectation_id: str,
        legacy_provider_key: str | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO document_expectations (
                id, deployment_id, correspondent_id, kind, document_type_id,
                statement_series_id, document_ids_json, series_discriminator,
                expectation_mode, status,
                cadence_json, evidence_json, title_convention_json, metadata_policy_json,
                acquisition_source_id, legacy_provider_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                expectation_id,
                deployment_id,
                correspondent_id,
                expectation.kind,
                expectation.document_type_id,
                expectation.statement_series_id,
                json.dumps(expectation.document_ids),
                expectation.series_discriminator,
                expectation.expectation_mode,
                expectation.status,
                expectation.cadence.model_dump_json() if expectation.cadence else None,
                expectation.evidence.model_dump_json(),
                expectation.title_convention.model_dump_json()
                if expectation.title_convention
                else None,
                expectation.metadata_policy.model_dump_json(),
                expectation.acquisition_source_id,
                legacy_provider_key,
            ),
        )

    def get_document_expectation(
        self, deployment_id: str, expectation_id: str
    ) -> DocumentExpectation | None:
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM document_expectations WHERE deployment_id = ? AND id = ?",
            (deployment_id, expectation_id),
        ).fetchone()
        return self._expectation_row_to_model(row) if row else None

    def list_document_expectations(
        self, deployment_id: str, correspondent_id: int | None = None
    ) -> list[DocumentExpectation]:
        conn = self.connect()
        sql = "SELECT * FROM document_expectations WHERE deployment_id = ?"
        params: list[Any] = [deployment_id]
        if correspondent_id is not None:
            sql += " AND correspondent_id = ?"
            params.append(correspondent_id)
        sql += " ORDER BY created_at, id"
        rows = conn.execute(sql, params).fetchall()
        return [self._expectation_row_to_model(row) for row in rows]

    def update_document_expectation(
        self,
        deployment_id: str,
        expectation_id: str,
        update: DocumentExpectationUpdate,
    ) -> DocumentExpectation:
        current = self.get_document_expectation(deployment_id, expectation_id)
        if current is None:
            raise KeyError("document_expectation_not_found")
        data = current.model_dump(
            exclude={"id", "correspondent_id", "legacy_provider_key", "created_at", "updated_at"}
        )
        data.update(update.model_dump(exclude_unset=True))
        validated = DocumentExpectationCreate.model_validate(data)
        self._validate_expectation_references(deployment_id, current.correspondent_id, validated)

        conn = self.connect()
        with conn:
            conn.execute(
                """UPDATE document_expectations SET
                    document_type_id = ?, document_ids_json = ?, series_discriminator = ?,
                    expectation_mode = ?,
                    status = ?, cadence_json = ?, evidence_json = ?,
                    title_convention_json = ?, metadata_policy_json = ?,
                    acquisition_source_id = ?, updated_at = datetime('now')
                   WHERE deployment_id = ? AND id = ?""",
                (
                    validated.document_type_id,
                    json.dumps(validated.document_ids),
                    validated.series_discriminator,
                    validated.expectation_mode,
                    validated.status,
                    validated.cadence.model_dump_json() if validated.cadence else None,
                    validated.evidence.model_dump_json(),
                    validated.title_convention.model_dump_json()
                    if validated.title_convention
                    else None,
                    validated.metadata_policy.model_dump_json(),
                    validated.acquisition_source_id,
                    deployment_id,
                    expectation_id,
                ),
            )
        result = self.get_document_expectation(deployment_id, expectation_id)
        assert result is not None
        return result

    def list_alertable_expectations(self, deployment_id: str) -> list[DocumentExpectation]:
        conn = self.connect()
        rows = conn.execute(
            """SELECT e.*, p.lifecycle_status AS profile_lifecycle
               FROM document_expectations e
               JOIN correspondent_profiles p
                 ON p.deployment_id = e.deployment_id
                AND p.correspondent_id = e.correspondent_id
               WHERE e.deployment_id = ?
                 AND e.status = 'confirmed'
                 AND e.expectation_mode IN ('recurring', 'periodic')
                 AND e.cadence_json IS NOT NULL
                 AND p.lifecycle_status = 'active'""",
            (deployment_id,),
        ).fetchall()
        return [self._expectation_row_to_model(row) for row in rows]

    def expectation_can_emit_missing_alert(self, deployment_id: str, expectation_id: str) -> bool:
        conn = self.connect()
        row = conn.execute(
            """SELECT e.*, p.lifecycle_status AS profile_lifecycle
               FROM document_expectations e
               JOIN correspondent_profiles p
                 ON p.deployment_id = e.deployment_id
                AND p.correspondent_id = e.correspondent_id
               WHERE e.deployment_id = ? AND e.id = ?""",
            (deployment_id, expectation_id),
        ).fetchone()
        if row is None:
            raise KeyError("document_expectation_not_found")
        expectation = self._expectation_row_to_model(row)
        return expectation.can_emit_missing_alert(row["profile_lifecycle"])

    def replace_external_candidate_snapshot(
        self,
        deployment_id: str,
        snapshot: DocumentExpectationSignalsV1,
    ) -> ExternalCandidateSnapshotResult:
        """Replace one connector's bounded projection when its opaque generation changes."""
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        processed = conn.execute(
            """SELECT 1 FROM external_signal_generations
               WHERE deployment_id = ? AND connector_ref = ? AND source_generation = ?""",
            (deployment_id, snapshot.connector_ref, snapshot.source_generation),
        ).fetchone()
        if processed:
            active = conn.execute(
                """SELECT COUNT(*) FROM external_document_candidates
                   WHERE deployment_id = ? AND connector_ref = ? AND active = 1""",
                (deployment_id, snapshot.connector_ref),
            ).fetchone()[0]
            result = ExternalCandidateSnapshotResult(
                source_generation=snapshot.source_generation,
                idempotent=True,
                active_candidates=active,
                deactivated_candidates=0,
            )
            conn.commit()
            return result

        current_source = conn.execute(
            """SELECT source_as_of FROM external_signal_sources
               WHERE deployment_id = ? AND connector_ref = ?""",
            (deployment_id, snapshot.connector_ref),
        ).fetchone()
        if current_source and snapshot.source_as_of < datetime.fromisoformat(
            current_source["source_as_of"]
        ):
            conn.execute(
                """INSERT INTO external_signal_generations (
                    deployment_id, connector_ref, source_generation
                ) VALUES (?, ?, ?)""",
                (deployment_id, snapshot.connector_ref, snapshot.source_generation),
            )
            active = conn.execute(
                """SELECT COUNT(*) FROM external_document_candidates
                   WHERE deployment_id = ? AND connector_ref = ? AND active = 1""",
                (deployment_id, snapshot.connector_ref),
            ).fetchone()[0]
            conn.commit()
            return ExternalCandidateSnapshotResult(
                source_generation=snapshot.source_generation,
                idempotent=True,
                active_candidates=active,
                deactivated_candidates=0,
            )

        incoming_refs = {signal.series_ref for signal in snapshot.signals}
        existing_active = {
            row["series_ref"]
            for row in conn.execute(
                """SELECT series_ref FROM external_document_candidates
                   WHERE deployment_id = ? AND connector_ref = ? AND active = 1""",
                (deployment_id, snapshot.connector_ref),
            ).fetchall()
        }
        deactivated_refs = existing_active & {
            signal.series_ref for signal in snapshot.signals if not signal.active
        }
        with conn:
            if snapshot.completeness == "complete":
                missing_refs = sorted(existing_active - incoming_refs)
                deactivated_refs.update(missing_refs)
                if missing_refs:
                    placeholders = ",".join("?" for _ in missing_refs)
                    conn.execute(
                        f"""UPDATE external_document_candidates
                            SET active = 0, source_generation = ?, source_as_of = ?,
                                updated_at = datetime('now')
                            WHERE deployment_id = ? AND connector_ref = ?
                              AND series_ref IN ({placeholders})""",
                        (
                            snapshot.source_generation,
                            snapshot.source_as_of.isoformat(),
                            deployment_id,
                            snapshot.connector_ref,
                            *missing_refs,
                        ),
                    )
            for signal in snapshot.signals:
                candidate_id = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    (
                        f"owl:{deployment_id}:external-candidate:"
                        f"{snapshot.connector_ref}:{signal.series_ref}"
                    ),
                ).hex
                conn.execute(
                    """INSERT INTO external_document_candidates (
                        id, deployment_id, connector_ref, series_ref, source_generation,
                        source_as_of, kind, active, display_hint, cadence,
                        next_expected_date, confidence, basis_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(deployment_id, connector_ref, series_ref) DO UPDATE SET
                        source_generation = excluded.source_generation,
                        source_as_of = excluded.source_as_of,
                        kind = excluded.kind,
                        active = excluded.active,
                        display_hint = excluded.display_hint,
                        cadence = excluded.cadence,
                        next_expected_date = excluded.next_expected_date,
                        confidence = excluded.confidence,
                        basis_json = excluded.basis_json,
                        updated_at = datetime('now')""",
                    (
                        candidate_id,
                        deployment_id,
                        snapshot.connector_ref,
                        signal.series_ref,
                        snapshot.source_generation,
                        snapshot.source_as_of.isoformat(),
                        signal.kind,
                        int(signal.active),
                        signal.display_hint,
                        signal.cadence,
                        signal.next_expected_date.isoformat()
                        if signal.next_expected_date
                        else None,
                        signal.confidence,
                        json.dumps(signal.basis),
                    ),
                )
                if signal.active and signal.kind == "accountStatementCandidate":
                    candidate_mapping = conn.execute(
                        """SELECT outcome, expectation_id
                           FROM external_document_candidates
                           WHERE deployment_id = ? AND connector_ref = ? AND series_ref = ?""",
                        (deployment_id, snapshot.connector_ref, signal.series_ref),
                    ).fetchone()
                    if (
                        candidate_mapping["outcome"] == "mapped"
                        and candidate_mapping["expectation_id"]
                    ):
                        mapping_conflict = conn.execute(
                            """SELECT 1 FROM external_document_candidates
                               WHERE deployment_id = ? AND connector_ref = ?
                                 AND series_ref != ? AND kind = 'accountStatementCandidate'
                                 AND active = 1 AND outcome = 'mapped' AND expectation_id = ?""",
                            (
                                deployment_id,
                                snapshot.connector_ref,
                                signal.series_ref,
                                candidate_mapping["expectation_id"],
                            ),
                        ).fetchone()
                        if mapping_conflict:
                            conn.execute(
                                """UPDATE external_document_candidates
                                   SET outcome = 'ambiguous', expectation_id = NULL,
                                       reviewed_at = datetime('now'), updated_at = datetime('now')
                                   WHERE deployment_id = ? AND connector_ref = ? AND series_ref = ?""",
                                (deployment_id, snapshot.connector_ref, signal.series_ref),
                            )
            conn.execute(
                """INSERT INTO external_signal_sources (
                    deployment_id, connector_ref, source_generation, source_as_of, completeness
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(deployment_id, connector_ref) DO UPDATE SET
                    source_generation = excluded.source_generation,
                    source_as_of = excluded.source_as_of,
                    completeness = excluded.completeness,
                    updated_at = datetime('now')""",
                (
                    deployment_id,
                    snapshot.connector_ref,
                    snapshot.source_generation,
                    snapshot.source_as_of.isoformat(),
                    snapshot.completeness,
                ),
            )
            conn.execute(
                """INSERT OR IGNORE INTO external_signal_generations (
                    deployment_id, connector_ref, source_generation
                ) VALUES (?, ?, ?)""",
                (deployment_id, snapshot.connector_ref, snapshot.source_generation),
            )

        active = conn.execute(
            """SELECT COUNT(*) FROM external_document_candidates
               WHERE deployment_id = ? AND connector_ref = ? AND active = 1""",
            (deployment_id, snapshot.connector_ref),
        ).fetchone()[0]
        return ExternalCandidateSnapshotResult(
            source_generation=snapshot.source_generation,
            idempotent=False,
            active_candidates=active,
            deactivated_candidates=len(deactivated_refs),
        )

    def list_external_candidates(
        self,
        deployment_id: str,
        *,
        correspondent_id: int | None = None,
    ) -> list[ExternalDocumentCandidate]:
        conn = self.connect()
        sql = """
            SELECT c.*, e.status AS expectation_status
            FROM external_document_candidates c
            LEFT JOIN document_expectations e
              ON e.deployment_id = c.deployment_id AND e.id = c.expectation_id
            WHERE c.deployment_id = ?
        """
        params: list[Any] = [deployment_id]
        if correspondent_id is not None:
            sql += " AND c.correspondent_id = ?"
            params.append(correspondent_id)
        sql += " ORDER BY c.active DESC, c.outcome, c.display_hint, c.id"
        rows = conn.execute(sql, params).fetchall()
        series_counts = {
            row["correspondent_id"]: row["candidate_count"]
            for row in conn.execute(
                """SELECT correspondent_id, COUNT(DISTINCT id) AS candidate_count
                   FROM external_document_candidates
                   WHERE deployment_id = ? AND active = 1
                     AND kind = 'accountStatementCandidate'
                     AND correspondent_id IS NOT NULL
                   GROUP BY correspondent_id""",
                (deployment_id,),
            ).fetchall()
        }
        return [
            ExternalDocumentCandidate(
                id=row["id"],
                kind=row["kind"],
                active=bool(row["active"]),
                display_hint=row["display_hint"],
                cadence=row["cadence"],
                next_expected_date=date.fromisoformat(row["next_expected_date"])
                if row["next_expected_date"]
                else None,
                confidence=row["confidence"],
                basis=json.loads(row["basis_json"]),
                source_as_of=row["source_as_of"],
                outcome=row["outcome"],
                expectation_id=row["expectation_id"],
                correspondent_id=row["correspondent_id"],
                likely_multiple_statement_series=series_counts.get(row["correspondent_id"], 0) > 1,
                recurrence_evidence=(
                    "high"
                    if row["active"] and row["kind"] == "accountStatementCandidate"
                    else "none"
                ),
                review_finding=(
                    "source_candidate_inactive_confirmed_policy_preserved"
                    if not row["active"] and row["expectation_status"] == "confirmed"
                    else "source_candidate_inactive"
                    if not row["active"]
                    else None
                ),
                reviewed_at=row["reviewed_at"],
            )
            for row in rows
        ]

    def review_external_candidate(
        self,
        deployment_id: str,
        candidate_id: str,
        review: ExternalCandidateReview,
    ) -> ExternalDocumentCandidate:
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        with conn:
            self._review_external_candidate_locked(
                conn,
                deployment_id,
                candidate_id,
                review,
            )
        return next(
            item for item in self.list_external_candidates(deployment_id) if item.id == candidate_id
        )

    def _review_external_candidate_locked(
        self,
        conn: sqlite3.Connection,
        deployment_id: str,
        candidate_id: str,
        review: ExternalCandidateReview,
    ) -> None:
        candidate = conn.execute(
            """SELECT * FROM external_document_candidates
               WHERE deployment_id = ? AND id = ?""",
            (deployment_id, candidate_id),
        ).fetchone()
        if candidate is None:
            raise KeyError("external_candidate_not_found")

        linked_expectation = (
            self.get_document_expectation(deployment_id, candidate["expectation_id"])
            if candidate["expectation_id"]
            else None
        )
        if (
            candidate["outcome"] == "not_applicable"
            and linked_expectation is not None
            and linked_expectation.status == "confirmed"
            and linked_expectation.expectation_mode == "not_expected"
        ):
            if review.outcome == "not_applicable":
                if review.correspondent_id != linked_expectation.correspondent_id:
                    raise ValueError(
                        "The external signal already has not_expected policy for another "
                        "correspondent"
                    )
                return
            raise ValueError(
                "Retire the confirmed not_expected policy before changing this candidate review"
            )

        expectation_id = review.expectation_id
        correspondent_id = review.correspondent_id
        if review.outcome == "mapped":
            expectation = self.get_document_expectation(deployment_id, review.expectation_id or "")
            if expectation is None:
                raise KeyError("document_expectation_not_found")
            if candidate["kind"] == "accountStatementCandidate" and candidate["active"]:
                conflicting_mapping = conn.execute(
                    """SELECT 1 FROM external_document_candidates
                       WHERE deployment_id = ? AND connector_ref = ?
                         AND id != ? AND kind = 'accountStatementCandidate'
                         AND active = 1 AND outcome = 'mapped' AND expectation_id = ?""",
                    (
                        deployment_id,
                        candidate["connector_ref"],
                        candidate_id,
                        expectation.id,
                    ),
                ).fetchone()
                if conflicting_mapping:
                    raise ValueError(
                        "Another active account candidate already maps to this expectation; "
                        "leave the mapping ambiguous or choose a distinct expectation"
                    )
            expectation_id = expectation.id
            correspondent_id = expectation.correspondent_id
        elif review.outcome == "suggested":
            assert review.correspondent_id is not None
            if review.expectation is not None:
                if (
                    candidate["kind"] == "accountStatementCandidate"
                    and review.expectation.kind != "statement"
                ):
                    raise ValueError("Account candidates may only suggest statement expectations")
                if candidate[
                    "kind"
                ] == "recurringDocumentCandidate" and review.expectation.kind in {
                    "invoice",
                    "bill",
                    "receipt",
                }:
                    raise ValueError(
                        "A recurring obligation alone cannot suggest an invoice, bill, or receipt"
                    )
                self._validate_expectation_references(
                    deployment_id,
                    review.correspondent_id,
                    review.expectation,
                )
                expectation_id = uuid.uuid4().hex
                self._insert_document_expectation(
                    conn,
                    deployment_id,
                    review.correspondent_id,
                    review.expectation,
                    expectation_id=expectation_id,
                )
        elif review.outcome == "not_applicable":
            assert review.correspondent_id is not None
            negative_policy = DocumentExpectationCreate(
                kind=("statement" if candidate["kind"] == "accountStatementCandidate" else "other"),
                expectation_mode="not_expected",
                status="confirmed",
                evidence=ExpectationEvidence(
                    source="user",
                    reason_codes=["external_signal_documentless"],
                ),
            )
            self._validate_expectation_references(
                deployment_id,
                review.correspondent_id,
                negative_policy,
            )
            expectation_id = uuid.uuid4().hex
            self._insert_document_expectation(
                conn,
                deployment_id,
                review.correspondent_id,
                negative_policy,
                expectation_id=expectation_id,
            )

        conn.execute(
            """UPDATE external_document_candidates
               SET outcome = ?, expectation_id = ?, correspondent_id = ?,
                   reviewed_at = datetime('now'), updated_at = datetime('now')
               WHERE deployment_id = ? AND id = ?""",
            (
                review.outcome,
                expectation_id,
                correspondent_id,
                deployment_id,
                candidate_id,
            ),
        )

    def reconcile_expectations_for_series_merge(
        self, deployment_id: str, source_series_id: str, target_series_id: str
    ) -> None:
        """Rebind or retire source policy without deleting its review history."""
        conn = self.connect()
        source = conn.execute(
            """SELECT * FROM document_expectations
               WHERE deployment_id = ? AND statement_series_id = ? AND status != 'retired'""",
            (deployment_id, source_series_id),
        ).fetchone()
        if source is None:
            return
        target = conn.execute(
            """SELECT * FROM document_expectations
               WHERE deployment_id = ? AND statement_series_id = ? AND status != 'retired'""",
            (deployment_id, target_series_id),
        ).fetchone()
        with conn:
            if target is None:
                conn.execute(
                    """UPDATE document_expectations
                       SET statement_series_id = ?, updated_at = datetime('now')
                       WHERE id = ?""",
                    (target_series_id, source["id"]),
                )
            else:
                conn.execute(
                    """UPDATE document_expectations
                       SET status = 'retired', updated_at = datetime('now')
                       WHERE id = ?""",
                    (source["id"],),
                )
            self._insert_profile_event(
                conn,
                deployment_id,
                source["correspondent_id"],
                "expectation_series_merged",
                {
                    "expectation_id": source["id"],
                    "source_series_id": source_series_id,
                    "target_series_id": target_series_id,
                    "resolution": "rebound" if target is None else "retired_duplicate",
                },
            )

    def validate_expectations_for_series_merge(
        self, deployment_id: str, source_series_id: str, target_series_id: str
    ) -> None:
        """Fail before mutation when a merge would cross correspondent policy."""
        conn = self.connect()
        target_series = conn.execute(
            "SELECT correspondent_id FROM statement_series WHERE id = ?",
            (target_series_id,),
        ).fetchone()
        if target_series is None:
            raise KeyError("statement_series_not_found")
        source_expectation = conn.execute(
            """SELECT correspondent_id FROM document_expectations
               WHERE deployment_id = ? AND statement_series_id = ?
                 AND status != 'retired'""",
            (deployment_id, source_series_id),
        ).fetchone()
        if source_expectation is not None and target_series["correspondent_id"] not in (
            None,
            source_expectation["correspondent_id"],
        ):
            raise ValueError(
                "Cannot merge a statement expectation into another correspondent's series"
            )

    def migrate_legacy_provider_overrides(self, deployment_id: str) -> tuple[int, int]:
        """Migrate only overrides that resolve to exactly one existing series."""
        conn = self.connect()
        overrides = conn.execute("SELECT * FROM provider_overrides").fetchall()
        latest_run = conn.execute(
            "SELECT id FROM discovery_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        migrated = review_required = 0

        for override in overrides:
            prior = conn.execute(
                """SELECT resolution_status FROM legacy_override_migrations
                   WHERE deployment_id = ? AND provider_key = ?""",
                (deployment_id, override["provider_key"]),
            ).fetchone()
            if prior and prior["resolution_status"] == "migrated":
                continue

            provider = None
            if latest_run:
                provider = conn.execute(
                    """SELECT * FROM providers
                       WHERE discovery_run_id = ? AND provider_key = ?""",
                    (latest_run["id"], override["provider_key"]),
                ).fetchone()
            if provider is None or provider["correspondent_id"] is None:
                self._record_legacy_resolution(
                    deployment_id,
                    override["provider_key"],
                    "unmigrated",
                    "provider_identity_unavailable",
                )
                review_required += 1
                continue

            profile = self.get_correspondent_profile(deployment_id, provider["correspondent_id"])
            if profile is None:
                self._record_legacy_resolution(
                    deployment_id,
                    override["provider_key"],
                    "unmigrated",
                    "profile_not_synchronized",
                )
                review_required += 1
                continue

            exact = conn.execute(
                "SELECT * FROM statement_series WHERE id = ?",
                (override["provider_key"],),
            ).fetchall()
            candidates = (
                exact
                or conn.execute(
                    "SELECT * FROM statement_series WHERE correspondent_id = ?",
                    (provider["correspondent_id"],),
                ).fetchall()
            )

            if len(candidates) != 1:
                reason = (
                    "ambiguous_statement_series"
                    if len(candidates) > 1
                    else "statement_series_not_found"
                )
                self._record_legacy_resolution(
                    deployment_id,
                    override["provider_key"],
                    "review_required",
                    reason,
                )
                review_required += 1
                continue

            series = candidates[0]
            existing = conn.execute(
                """SELECT id, legacy_provider_key FROM document_expectations
                   WHERE deployment_id = ? AND statement_series_id = ?
                     AND status != 'retired'""",
                (deployment_id, series["id"]),
            ).fetchone()
            if existing:
                existing_key = existing["legacy_provider_key"]
                if existing_key not in (None, override["provider_key"]):
                    self._record_legacy_resolution(
                        deployment_id,
                        override["provider_key"],
                        "review_required",
                        "expectation_identity_conflict",
                    )
                    review_required += 1
                    continue
                if existing_key is None:
                    with conn:
                        conn.execute(
                            """UPDATE document_expectations
                              SET legacy_provider_key = ?, updated_at = datetime('now')
                              WHERE id = ?""",
                            (override["provider_key"], existing["id"]),
                        )
                expectation_id = existing["id"]
            else:
                frequency = override["frequency_override"] or series["frequency"]
                cadence = (
                    Cadence(
                        frequency=frequency,
                        expected_day=override["anchor_day_override"],
                    )
                    if frequency in {"monthly", "quarterly", "annual"}
                    else None
                )
                status = {
                    "confirmed": "confirmed",
                    "ignored": "dismissed",
                    "dismissed": "dismissed",
                }.get(override["status"], "suggested")
                if status == "confirmed" and cadence is None:
                    status = "suggested"
                expectation_id = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"owl:{deployment_id}:legacy-provider:{override['provider_key']}",
                ).hex
                request = DocumentExpectationCreate(
                    kind="statement",
                    statement_series_id=series["id"],
                    series_discriminator=override["display_name"] or series["name"],
                    expectation_mode="recurring" if cadence else "irregular",
                    status=status,
                    cadence=cadence,
                    evidence=ExpectationEvidence(
                        source="legacy_override",
                        reason_codes=["legacy_provider_override"],
                        confidence=1.0,
                    ),
                )
                try:
                    self.create_document_expectation(
                        deployment_id,
                        provider["correspondent_id"],
                        request,
                        expectation_id=expectation_id,
                        legacy_provider_key=override["provider_key"],
                    )
                except sqlite3.IntegrityError:
                    self._record_legacy_resolution(
                        deployment_id,
                        override["provider_key"],
                        "review_required",
                        "expectation_identity_conflict",
                    )
                    review_required += 1
                    continue

            if override["notes"] and not profile.notes:
                self.update_correspondent_profile(
                    deployment_id,
                    profile.correspondent_id,
                    CorrespondentProfileUpdate(notes=override["notes"]),
                )
            self._record_legacy_resolution(
                deployment_id,
                override["provider_key"],
                "migrated",
                "unambiguous_statement_series",
                expectation_id,
            )
            migrated += 1

        return migrated, review_required

    def _record_legacy_resolution(
        self,
        deployment_id: str,
        provider_key: str,
        status: str,
        reason_code: str,
        expectation_id: str | None = None,
    ) -> None:
        conn = self.connect()
        with conn:
            conn.execute(
                """INSERT INTO legacy_override_migrations (
                    deployment_id, provider_key, resolution_status, reason_code,
                    expectation_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(deployment_id, provider_key) DO UPDATE SET
                    resolution_status = excluded.resolution_status,
                    reason_code = excluded.reason_code,
                    expectation_id = excluded.expectation_id,
                    updated_at = datetime('now')""",
                (deployment_id, provider_key, status, reason_code, expectation_id),
            )

    def list_legacy_override_review(self, deployment_id: str) -> list[LegacyOverrideReviewItem]:
        conn = self.connect()
        rows = conn.execute(
            """SELECT provider_key, resolution_status, reason_code, expectation_id
               FROM legacy_override_migrations
               WHERE deployment_id = ?
               ORDER BY provider_key""",
            (deployment_id,),
        ).fetchall()
        return [LegacyOverrideReviewItem.model_validate(dict(row)) for row in rows]

    def resolve_expectation_identity(self, deployment_id: str, identity: str) -> IdentityResolution:
        expectation = self.get_document_expectation(deployment_id, identity)
        if expectation is not None:
            return IdentityResolution(
                status="resolved", canonical_key=expectation.id, expectation=expectation
            )

        conn = self.connect()
        rows = conn.execute(
            """SELECT * FROM document_expectations
               WHERE deployment_id = ? AND legacy_provider_key = ?""",
            (deployment_id, identity),
        ).fetchall()
        if len(rows) == 1:
            resolved = self._expectation_row_to_model(rows[0])
            return IdentityResolution(
                status="resolved", canonical_key=resolved.id, expectation=resolved
            )
        if len(rows) > 1:
            return IdentityResolution(status="ambiguous")

        migration = conn.execute(
            """SELECT resolution_status, reason_code FROM legacy_override_migrations
               WHERE deployment_id = ? AND provider_key = ?""",
            (deployment_id, identity),
        ).fetchone()
        if migration and migration["resolution_status"] == "review_required":
            return IdentityResolution(status="ambiguous")
        return IdentityResolution(status="unmapped")
