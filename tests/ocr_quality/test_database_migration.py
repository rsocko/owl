"""Regression tests for the add-missing-columns startup migration (issue #30
follow-up: PR #153 broke production because ``create_all()`` never adds
columns to a table that already exists).
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import inspect

from doc_intelligence_hub.modules.ocr_quality import config as ocr_quality_config
from doc_intelligence_hub.modules.ocr_quality.database import (
    InventoryRun,
    get_engine,
    get_session,
    init_db,
)


@pytest.fixture()
def ocr_quality_database_url(tmp_path):
    original = ocr_quality_config.settings.database_url
    db_path = tmp_path / "migration_test_ocr_quality.db"
    ocr_quality_config.settings.database_url = f"sqlite:///{db_path}"
    yield db_path
    ocr_quality_config.settings.database_url = original


def _create_pre_pr153_runs_table(db_path) -> None:
    """Build ``ocr_quality_runs`` in its shape *before* PR #153/issue #30.

    Mirrors the live production schema this bug was found against: no
    ``actor``, ``trigger``, ``correlation_id``, ``idempotency_key``,
    ``cancel_requested``, ``cancelled_at``, ``retry_count``, or
    ``max_retries`` columns.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE ocr_quality_runs (
                run_id VARCHAR NOT NULL PRIMARY KEY,
                stage VARCHAR NOT NULL,
                scope_digest VARCHAR NOT NULL,
                config_digest VARCHAR NOT NULL,
                instance_digest VARCHAR NOT NULL,
                signal_version VARCHAR NOT NULL,
                seed VARCHAR,
                source_run_id VARCHAR,
                cursor VARCHAR,
                status VARCHAR NOT NULL,
                counts JSON,
                throughput_docs_per_second FLOAT,
                started_at DATETIME,
                finished_at DATETIME
            )
            """
        )
        conn.execute(
            """
            INSERT INTO ocr_quality_runs
                (run_id, stage, scope_digest, config_digest, instance_digest,
                 signal_version, status, started_at)
            VALUES ('pre-existing-run', 'stage_1_corpus_scan', 'scope', 'config',
                    'instance', 'v1', 'completed', '2026-01-01 00:00:00')
            """
        )
        conn.commit()
    finally:
        conn.close()


class TestAddMissingColumnsMigration:
    def test_backfills_missing_columns_without_losing_data(self, ocr_quality_database_url):
        _create_pre_pr153_runs_table(ocr_quality_database_url)

        init_db()

        engine = get_engine()
        columns = {col["name"] for col in inspect(engine).get_columns("ocr_quality_runs")}
        for expected in (
            "actor",
            "trigger",
            "correlation_id",
            "idempotency_key",
            "cancel_requested",
            "cancelled_at",
            "retry_count",
            "max_retries",
        ):
            assert expected in columns, f"missing column {expected!r} after migration"

        db = get_session()
        try:
            run = db.query(InventoryRun).filter_by(run_id="pre-existing-run").one()
            assert run.status == "completed"
            assert run.actor == "system"
            assert run.trigger == "manual"
            assert run.cancel_requested is False
            assert run.retry_count == 0
            assert run.max_retries == 3
        finally:
            db.close()

    def test_migration_is_idempotent(self, ocr_quality_database_url):
        _create_pre_pr153_runs_table(ocr_quality_database_url)

        init_db()
        init_db()  # must not raise (duplicate column) on repeat startup

        engine = get_engine()
        columns = [col["name"] for col in inspect(engine).get_columns("ocr_quality_runs")]
        assert columns.count("actor") == 1

    def test_fresh_database_still_creates_full_schema(self, ocr_quality_database_url):
        """No pre-existing table at all — ``create_all()`` path, unaffected."""
        init_db()

        engine = get_engine()
        columns = {col["name"] for col in inspect(engine).get_columns("ocr_quality_runs")}
        assert "actor" in columns
        assert "run_id" in columns

        db = get_session()
        try:
            db.add(
                InventoryRun(
                    run_id="fresh-run",
                    stage="stage_1_corpus_scan",
                    scope_digest="scope",
                    config_digest="config",
                    instance_digest="instance",
                    signal_version="v1",
                    status="running",
                )
            )
            db.commit()
        finally:
            db.close()
