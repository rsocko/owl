"""Tests for SQLite persistence layer."""

from __future__ import annotations

import sqlite3
from datetime import date

from doc_intelligence_hub.modules.statements.database import Database
from doc_intelligence_hub.modules.statements.models import (
    AnalysisPattern,
    DiscoveryResult,
    ProviderCandidate,
    Recommendation,
    RecommendationResult,
)


def _sample_discovery() -> DiscoveryResult:
    return DiscoveryResult(
        analyzed_documents=100,
        providers=[
            ProviderCandidate(
                provider_key="chase-visa-chase-statement",
                provider_name="Chase Visa",
                statement_name="Chase Statement",
                correspondent_id=42,
                document_count=12,
                normalized_title="chase statement",
                title_consistency=1.0,
                pattern=AnalysisPattern(
                    frequency="monthly",
                    pattern_type="fixed_day",
                    confidence=0.95,
                    anchor_day=3,
                    variance_days=1,
                    grace_period_days=5,
                ),
                sample_document_ids=[10, 11, 12],
                first_seen=date(2025, 1, 3),
                last_seen=date(2025, 12, 3),
            ),
            ProviderCandidate(
                provider_key="vanguard-investment-statement",
                provider_name="Vanguard",
                correspondent_id=30,
                document_count=8,
                normalized_title="investment statement",
                title_consistency=0.9,
                pattern=AnalysisPattern(
                    frequency="quarterly",
                    pattern_type="fixed_day",
                    confidence=0.88,
                    anchor_day=15,
                    variance_days=2,
                    grace_period_days=5,
                ),
                sample_document_ids=[20, 21, 22],
                first_seen=date(2024, 1, 15),
                last_seen=date(2025, 10, 15),
            ),
        ],
    )


def _sample_recommendations() -> RecommendationResult:
    return RecommendationResult(
        as_of=date(2026, 6, 15),
        recommendations=[
            Recommendation(
                provider_key="chase-visa-chase-statement",
                provider_name="Chase Visa",
                expected_date=date(2026, 6, 3),
                earliest_date=date(2026, 6, 2),
                latest_date=date(2026, 6, 9),
                status="missing",
                priority=7,
                days_late=12,
            ),
        ],
    )


def test_database_creates_schema(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    try:
        conn = db.connect()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = {row["name"] for row in tables}
        assert "discovery_runs" in table_names
        assert "providers" in table_names
        assert "recommendation_runs" in table_names
        assert "recommendations" in table_names
        assert "schema_version" in table_names
    finally:
        db.close()


def test_database_migrates_statement_name_column(tmp_path) -> None:
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    connection.execute("INSERT INTO schema_version (version) VALUES (3)")
    connection.execute(
        """
        CREATE TABLE providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discovery_run_id INTEGER NOT NULL,
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
        )
        """
    )
    connection.commit()
    connection.close()

    db = Database(str(db_path))
    try:
        connection = db.connect()
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(providers)").fetchall()
        }
        version = connection.execute("SELECT version FROM schema_version").fetchone()["version"]
        assert "statement_name" in columns
        assert version == 5
    finally:
        db.close()


def test_save_and_load_discovery(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    try:
        original = _sample_discovery()
        run_id = db.save_discovery(original)
        assert run_id is not None

        loaded = db.load_latest_discovery()
        assert loaded is not None
        assert loaded.analyzed_documents == original.analyzed_documents
        assert len(loaded.providers) == len(original.providers)

        # Verify provider details survived round-trip
        chase = next(p for p in loaded.providers if p.provider_key == "chase-visa-chase-statement")
        assert chase.provider_name == "Chase Visa"
        assert chase.statement_name == "Chase Statement"
        assert chase.document_count == 12
        assert chase.pattern.frequency == "monthly"
        assert chase.pattern.confidence == 0.95
        assert chase.pattern.anchor_day == 3
        assert chase.first_seen == date(2025, 1, 3)
        assert chase.last_seen == date(2025, 12, 3)
        assert chase.sample_document_ids == [10, 11, 12]

        vanguard = next(
            p for p in loaded.providers if p.provider_key == "vanguard-investment-statement"
        )
        assert vanguard.pattern.frequency == "quarterly"
    finally:
        db.close()


def test_save_and_load_recommendations(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    try:
        original = _sample_recommendations()
        run_id = db.save_recommendations(original)
        assert run_id is not None

        loaded = db.load_latest_recommendations()
        assert loaded is not None
        assert loaded.as_of == original.as_of
        assert len(loaded.recommendations) == 1
        assert loaded.recommendations[0].provider_name == "Chase Visa"
        assert loaded.recommendations[0].status == "missing"
        assert loaded.recommendations[0].priority == 7
    finally:
        db.close()


def test_multiple_discovery_runs_returns_latest(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    try:
        first = DiscoveryResult(analyzed_documents=50, providers=[])
        db.save_discovery(first)

        second = _sample_discovery()
        db.save_discovery(second)

        loaded = db.load_latest_discovery()
        assert loaded is not None
        assert loaded.analyzed_documents == 100
        assert len(loaded.providers) == 2
    finally:
        db.close()


def test_list_discovery_runs(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    try:
        db.save_discovery(DiscoveryResult(analyzed_documents=50, providers=[]))
        db.save_discovery(_sample_discovery())

        runs = db.list_discovery_runs()
        assert len(runs) == 2
        # Most recent first
        assert runs[0]["analyzed_documents"] == 100
        assert runs[1]["analyzed_documents"] == 50
    finally:
        db.close()


def test_list_recommendation_runs(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    try:
        db.save_recommendations(_sample_recommendations())

        runs = db.list_recommendation_runs()
        assert len(runs) == 1
        assert runs[0]["as_of"] == "2026-06-15"
        assert runs[0]["recommendation_count"] == 1
    finally:
        db.close()


def test_load_returns_none_when_empty(tmp_path) -> None:
    db = Database(str(tmp_path / "test.db"))
    try:
        db.connect()
        assert db.load_latest_discovery() is None
        assert db.load_latest_recommendations() is None
    finally:
        db.close()


def test_service_persists_to_database(tmp_path) -> None:
    """Integration: verify service.run_discovery writes to SQLite."""
    import asyncio

    from doc_intelligence_hub.modules.statements import config as config_mod
    from doc_intelligence_hub.modules.statements.service import run_discovery

    _original_load = config_mod.load_config
    db_path = str(tmp_path / "integration.db")
    snapshot_path = str(tmp_path / "snapshot.json")

    def patched_load(path):
        cfg = _original_load(path)
        cfg.runtime.snapshot_path = snapshot_path
        cfg.runtime.database_path = db_path
        return cfg

    # Temporarily patch
    config_mod.load_config = patched_load
    import doc_intelligence_hub.modules.statements.service as svc_mod

    svc_mod.load_config = patched_load

    try:
        asyncio.run(run_discovery("config/config.fixture.yaml"))
    finally:
        config_mod.load_config = _original_load
        svc_mod.load_config = _original_load

    db = Database(db_path)
    try:
        result = db.load_latest_discovery()
        assert result is not None
        assert result.analyzed_documents == 9
        assert len(result.providers) == 2
    finally:
        db.close()
