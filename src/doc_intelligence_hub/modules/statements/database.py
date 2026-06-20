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

SCHEMA_VERSION = 2

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
