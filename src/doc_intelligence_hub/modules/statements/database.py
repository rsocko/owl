"""SQLite persistence for statement-tracker (Phase 1.1).

Replaces JSON snapshots with a structured database for discovery results
and recommendations. Uses stdlib sqlite3 to avoid new dependencies.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from doc_intelligence_hub.modules.statements.models import (
    AnalysisPattern,
    DiscoveryResult,
    ProviderCandidate,
    Recommendation,
    RecommendationResult,
)

SCHEMA_VERSION = 3

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
        row = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
        if row[0] == 0:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
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
                    discovery_run_id, provider_key, provider_name, correspondent_id,
                    document_count, normalized_title, title_consistency,
                    frequency, pattern_type, confidence, anchor_day,
                    variance_days, grace_period_days, sample_document_ids,
                    first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    provider.provider_key,
                    provider.provider_name,
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

        return DiscoveryResult(analyzed_documents=run_row["analyzed_documents"], providers=providers)

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

        return RecommendationResult(as_of=date.fromisoformat(run_row["as_of"]), recommendations=recs)

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
            {"id": row["document_type_id"], "name": row["document_type_name"], "enabled": bool(row["enabled"])}
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
        row = conn.execute("SELECT correspondent_name FROM statement_series WHERE id = ?", (series_id,)).fetchone()
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
            [series_id] + document_ids,
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
        """Delete a series and its document associations."""
        conn = self.connect()
        conn.execute("DELETE FROM series_documents WHERE series_id = ?", (series_id,))
        conn.execute("DELETE FROM series_overrides WHERE series_id = ?", (series_id,))
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
