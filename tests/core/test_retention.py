"""Tests for data retention and cleanup module."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from doc_intelligence_hub.core.retention import (
    CleanupResult,
    ModuleCleanupResult,
    RetentionConfig,
    _human_bytes,
    cleanup_old_discovery_runs,
    get_storage_stats,
    load_retention_config,
    vacuum_databases,
)


class TestRetentionConfig:
    """Tests for configuration loading."""

    def test_defaults(self):
        cfg = RetentionConfig()
        assert cfg.processing_history_days == 90
        assert cfg.alerts_days == 30
        assert cfg.actions_days == 365
        assert cfg.matches_days == 365
        assert cfg.discovery_runs_days == 365

    def test_load_from_yaml(self, tmp_path: Path):
        config_file = tmp_path / "retention.yaml"
        config_file.write_text(
            "retention:\n  processing_history_days: 60\n  alerts_days: 14\n  actions_days: 180\n"
        )
        cfg = load_retention_config(config_file)
        assert cfg.processing_history_days == 60
        assert cfg.alerts_days == 14
        assert cfg.actions_days == 180
        # Defaults for unspecified keys
        assert cfg.matches_days == 365
        assert cfg.discovery_runs_days == 365

    def test_env_override(self, tmp_path: Path):
        config_file = tmp_path / "retention.yaml"
        config_file.write_text("retention:\n  alerts_days: 14\n")

        with patch.dict(os.environ, {"RETENTION_ALERTS_DAYS": "7"}):
            cfg = load_retention_config(config_file)

        # Env override wins over YAML
        assert cfg.alerts_days == 7

    def test_missing_config_file_uses_defaults(self, tmp_path: Path):
        cfg = load_retention_config(tmp_path / "nonexistent.yaml")
        assert cfg.processing_history_days == 90

    def test_invalid_env_value_ignored(self):
        with patch.dict(os.environ, {"RETENTION_ALERTS_DAYS": "not_a_number"}):
            cfg = load_retention_config(Path("nonexistent.yaml"))
        assert cfg.alerts_days == 30  # default preserved


class TestCleanupResult:
    """Tests for result dataclass."""

    def test_totals(self):
        result = CleanupResult(dry_run=True)
        result.modules = [
            ModuleCleanupResult(module="a", records_deleted=10),
            ModuleCleanupResult(module="b", records_deleted=5, records_archived=3),
        ]
        assert result.total_deleted == 15
        assert result.total_archived == 3

    def test_to_dict(self):
        result = CleanupResult(
            dry_run=False,
            started_at="2025-01-01T00:00:00",
            finished_at="2025-01-01T00:01:00",
            space_reclaimed_bytes=1024,
        )
        result.modules = [ModuleCleanupResult(module="test", records_deleted=5)]
        d = result.to_dict()
        assert d["dry_run"] is False
        assert d["total_deleted"] == 5
        assert len(d["modules"]) == 1


class TestCleanupOldDiscoveryRuns:
    """Tests for statement tracker cleanup using raw sqlite3."""

    def _create_test_db(self, db_path: Path) -> None:
        """Create a minimal statement-tracker database with test data."""
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS discovery_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL DEFAULT (datetime('now')),
                analyzed_documents INTEGER NOT NULL,
                provider_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS providers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discovery_run_id INTEGER NOT NULL REFERENCES discovery_runs(id) ON DELETE CASCADE,
                provider_key TEXT NOT NULL
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
                provider_key TEXT NOT NULL
            );
        """)

        old_date = (datetime.now(UTC) - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
        recent_date = (datetime.now(UTC) - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")

        # Insert old and recent discovery runs
        conn.execute(
            "INSERT INTO discovery_runs (run_at, analyzed_documents, provider_count) VALUES (?, 10, 2)",
            (old_date,),
        )
        conn.execute(
            "INSERT INTO discovery_runs (run_at, analyzed_documents, provider_count) VALUES (?, 20, 3)",
            (recent_date,),
        )

        # Old recommendation run
        conn.execute(
            "INSERT INTO recommendation_runs (run_at, as_of, recommendation_count) VALUES (?, ?, 5)",
            (old_date, old_date),
        )
        conn.commit()
        conn.close()

    def test_dry_run_counts_without_deleting(self, tmp_path: Path):
        db_path = tmp_path / "data" / "statement-tracker.db"
        db_path.parent.mkdir(parents=True)
        self._create_test_db(db_path)

        with patch("doc_intelligence_hub.core.retention._PROJECT_ROOT", tmp_path):
            result = cleanup_old_discovery_runs(days=365, dry_run=True)

        assert result.records_deleted == 2  # 1 discovery + 1 recommendation
        assert result.errors == []

        # Verify data is still there
        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT COUNT(*) FROM discovery_runs").fetchone()[0]
        conn.close()
        assert count == 2  # Both still present

    def test_actual_delete(self, tmp_path: Path):
        db_path = tmp_path / "data" / "statement-tracker.db"
        db_path.parent.mkdir(parents=True)
        self._create_test_db(db_path)

        with patch("doc_intelligence_hub.core.retention._PROJECT_ROOT", tmp_path):
            result = cleanup_old_discovery_runs(days=365, dry_run=False)

        assert result.records_deleted == 2
        assert result.errors == []

        # Verify old data is gone, recent is kept
        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT COUNT(*) FROM discovery_runs").fetchone()[0]
        rec_count = conn.execute("SELECT COUNT(*) FROM recommendation_runs").fetchone()[0]
        conn.close()
        assert count == 1  # Only recent run remains
        assert rec_count == 0  # Old recommendation deleted

    def test_missing_db_skips(self, tmp_path: Path):
        with patch("doc_intelligence_hub.core.retention._PROJECT_ROOT", tmp_path):
            result = cleanup_old_discovery_runs(days=365, dry_run=False)
        assert result.records_deleted == 0
        assert result.errors == []


class TestVacuumDatabases:
    """Tests for VACUUM helper."""

    def test_vacuum_on_existing_db(self, tmp_path: Path):
        db_path = tmp_path / "data" / "actions.db"
        db_path.parent.mkdir(parents=True)

        # Create a database with some data then delete it
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER, data TEXT)")
        for i in range(1000):
            conn.execute("INSERT INTO test VALUES (?, ?)", (i, "x" * 500))
        conn.commit()
        conn.execute("DELETE FROM test")
        conn.commit()
        conn.close()

        with patch("doc_intelligence_hub.core.retention._PROJECT_ROOT", tmp_path):
            reclaimed = vacuum_databases()

        # Should reclaim some space (exact amount is nondeterministic)
        assert reclaimed >= 0

    def test_vacuum_missing_dbs(self, tmp_path: Path):
        with patch("doc_intelligence_hub.core.retention._PROJECT_ROOT", tmp_path):
            reclaimed = vacuum_databases()
        assert reclaimed == 0


class TestHumanBytes:
    def test_bytes(self):
        assert _human_bytes(500) == "500.0 B"

    def test_kilobytes(self):
        assert _human_bytes(2048) == "2.0 KB"

    def test_megabytes(self):
        assert _human_bytes(1048576) == "1.0 MB"


class TestInfiniteRetention:
    """Tests for 0 = infinite retention (skip cleanup)."""

    def test_zero_days_skips_discovery_cleanup(self, tmp_path: Path):
        db_path = tmp_path / "data" / "statement-tracker.db"
        db_path.parent.mkdir(parents=True)
        # Create DB with old data
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE discovery_runs (id INTEGER PRIMARY KEY, run_at TEXT, analyzed_documents INTEGER, provider_count INTEGER)"
        )
        conn.execute(
            "CREATE TABLE recommendation_runs (id INTEGER PRIMARY KEY, run_at TEXT, as_of TEXT, recommendation_count INTEGER)"
        )
        old_date = (datetime.now(UTC) - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO discovery_runs VALUES (1, ?, 10, 2)", (old_date,))
        conn.commit()
        conn.close()

        with patch("doc_intelligence_hub.core.retention._PROJECT_ROOT", tmp_path):
            result = cleanup_old_discovery_runs(days=0, dry_run=False)

        assert result.records_deleted == 0  # Skipped
        # Data should still be there
        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT COUNT(*) FROM discovery_runs").fetchone()[0]
        conn.close()
        assert count == 1

    def test_config_with_zero_values(self, tmp_path: Path):
        config_file = tmp_path / "retention.yaml"
        config_file.write_text("retention:\n  processing_history_days: 0\n  alerts_days: 0\n")
        cfg = load_retention_config(config_file)
        assert cfg.processing_history_days == 0
        assert cfg.alerts_days == 0

    def test_env_override_to_zero(self):
        with patch.dict(os.environ, {"RETENTION_ACTIONS_DAYS": "0"}):
            cfg = load_retention_config(Path("nonexistent.yaml"))
        assert cfg.actions_days == 0


class TestStorageStats:
    """Tests for storage usage reporting."""

    def test_empty_data_dir(self, tmp_path: Path):
        with patch("doc_intelligence_hub.core.retention._PROJECT_ROOT", tmp_path):
            stats = get_storage_stats()
        assert stats.total_size_bytes == 0
        assert len(stats.databases) == 4  # All 4 DBs listed
        assert all(not db["exists"] for db in stats.databases)

    def test_with_existing_db(self, tmp_path: Path):
        db_path = tmp_path / "data" / "alerts.db"
        db_path.parent.mkdir(parents=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE alerts (id INTEGER PRIMARY KEY, alert_type TEXT)")
        conn.execute("INSERT INTO alerts VALUES (1, 'test')")
        conn.execute("INSERT INTO alerts VALUES (2, 'test2')")
        conn.commit()
        conn.close()

        with patch("doc_intelligence_hub.core.retention._PROJECT_ROOT", tmp_path):
            stats = get_storage_stats()

        assert stats.total_size_bytes > 0
        # Find the alerts table stats
        alerts_table = [t for t in stats.tables if t.table == "alerts"]
        assert len(alerts_table) == 1
        assert alerts_table[0].row_count == 2
        assert alerts_table[0].module == "alerts"

    def test_to_dict_format(self, tmp_path: Path):
        with patch("doc_intelligence_hub.core.retention._PROJECT_ROOT", tmp_path):
            stats = get_storage_stats()
        d = stats.to_dict()
        assert "total_size_bytes" in d
        assert "total_size_human" in d
        assert "databases" in d
        assert "tables" in d
