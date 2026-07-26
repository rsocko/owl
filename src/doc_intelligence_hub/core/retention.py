"""Data retention and cleanup for Document Intelligence Hub.

Provides configurable retention policies and cleanup functions for all
DI Hub databases.  Each module's stale data can be purged independently,
and a unified ``run_cleanup`` orchestrator handles the full sweep with
optional dry-run support.

Configuration is loaded from ``config/retention.yaml`` with environment
variable overrides (``RETENTION_<KEY>_DAYS``).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "retention.yaml"


# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------


@dataclass
class RetentionConfig:
    """Per-module retention periods (in days).

    A value of ``0`` means "keep forever" (infinite retention).
    """

    processing_history_days: int = 90
    alerts_days: int = 30
    actions_days: int = 365
    matches_days: int = 365
    discovery_runs_days: int = 365


def load_retention_config(config_path: Path | None = None) -> RetentionConfig:
    """Load retention settings from YAML, then apply env-var overrides."""
    cfg = RetentionConfig()

    path = config_path or _DEFAULT_CONFIG_PATH
    if path.exists():
        try:
            with open(path) as f:
                raw = yaml.safe_load(f) or {}
            section = raw.get("retention", {})
            for key in (
                "processing_history_days",
                "alerts_days",
                "actions_days",
                "matches_days",
                "discovery_runs_days",
            ):
                if key in section:
                    setattr(cfg, key, int(section[key]))
        except Exception:
            logger.warning("Could not load retention config from %s — using defaults", path)

    # Environment variable overrides: RETENTION_PROCESSING_HISTORY_DAYS etc.
    for key in (
        "processing_history_days",
        "alerts_days",
        "actions_days",
        "matches_days",
        "discovery_runs_days",
    ):
        env_key = f"RETENTION_{key.upper()}"
        env_val = os.environ.get(env_key)
        if env_val is not None:
            try:
                setattr(cfg, key, int(env_val))
            except ValueError:
                logger.warning("Invalid env override %s=%s — ignoring", env_key, env_val)

    return cfg


# ------------------------------------------------------------------
# Cleanup result
# ------------------------------------------------------------------


@dataclass
class ModuleCleanupResult:
    """Cleanup outcome for a single module."""

    module: str
    records_deleted: int = 0
    records_archived: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class CleanupResult:
    """Aggregate result of a full cleanup run."""

    dry_run: bool
    started_at: str = ""
    finished_at: str = ""
    modules: list[ModuleCleanupResult] = field(default_factory=list)
    space_reclaimed_bytes: int = 0

    @property
    def total_deleted(self) -> int:
        return sum(m.records_deleted for m in self.modules)

    @property
    def total_archived(self) -> int:
        return sum(m.records_archived for m in self.modules)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_deleted": self.total_deleted,
            "total_archived": self.total_archived,
            "space_reclaimed_bytes": self.space_reclaimed_bytes,
            "modules": [
                {
                    "module": m.module,
                    "records_deleted": m.records_deleted,
                    "records_archived": m.records_archived,
                    "errors": m.errors,
                }
                for m in self.modules
            ],
        }


# ------------------------------------------------------------------
# Per-module cleanup functions
# ------------------------------------------------------------------


def cleanup_processing_history(days: int = 90, *, dry_run: bool = False) -> ModuleCleanupResult:
    """Delete processing_history records older than *days* from the Action Queue DB."""
    result = ModuleCleanupResult(module="processing_history")
    if days <= 0:
        logger.info("Processing history retention set to infinite — skipping cleanup")
        return result

    from doc_intelligence_hub.modules.action_queue.database import ProcessingHistory, get_session

    result = ModuleCleanupResult(module="processing_history")
    cutoff = datetime.utcnow() - timedelta(days=days)

    session = get_session()
    try:
        query = session.query(ProcessingHistory).filter(ProcessingHistory.processed_at < cutoff)
        count = query.count()
        result.records_deleted = count
        if not dry_run and count > 0:
            query.delete(synchronize_session=False)
            session.commit()
            logger.info("Deleted %d processing_history records older than %d days", count, days)
        elif dry_run:
            logger.info("[DRY RUN] Would delete %d processing_history records", count)
    except Exception as exc:
        session.rollback()
        result.errors.append(str(exc))
        logger.exception("Error cleaning processing_history")
    finally:
        session.close()

    return result


def cleanup_old_alerts(days: int = 30, *, dry_run: bool = False) -> ModuleCleanupResult:
    """Delete resolved/acknowledged alerts older than *days*."""
    result = ModuleCleanupResult(module="alerts")
    if days <= 0:
        logger.info("Alerts retention set to infinite — skipping cleanup")
        return result

    from doc_intelligence_hub.core.alerts import Alert, get_session, init_db

    cutoff = datetime.now(UTC) - timedelta(days=days)

    init_db()
    session = get_session()
    try:
        # Delete alerts that are resolved AND older than cutoff
        query = session.query(Alert).filter(
            Alert.resolved_at.isnot(None),
            Alert.created_at < cutoff,
        )
        count = query.count()
        result.records_deleted = count
        if not dry_run and count > 0:
            query.delete(synchronize_session=False)
            session.commit()
            logger.info("Deleted %d resolved alerts older than %d days", count, days)
        elif dry_run:
            logger.info("[DRY RUN] Would delete %d resolved alerts", count)
    except Exception as exc:
        session.rollback()
        result.errors.append(str(exc))
        logger.exception("Error cleaning alerts")
    finally:
        session.close()

    return result


def archive_old_actions(days: int = 365, *, dry_run: bool = False) -> ModuleCleanupResult:
    """Delete completed/dismissed actions older than *days*."""
    result = ModuleCleanupResult(module="actions")
    if days <= 0:
        logger.info("Actions retention set to infinite — skipping cleanup")
        return result

    from doc_intelligence_hub.modules.action_queue.database import Action, get_session

    cutoff = datetime.utcnow() - timedelta(days=days)

    session = get_session()
    try:
        query = session.query(Action).filter(
            Action.status.in_(["completed", "dismissed"]),
            Action.created_at < cutoff,
        )
        count = query.count()
        result.records_deleted = count
        if not dry_run and count > 0:
            query.delete(synchronize_session=False)
            session.commit()
            logger.info("Deleted %d old actions (completed/dismissed, >%d days)", count, days)
        elif dry_run:
            logger.info("[DRY RUN] Would delete %d old actions", count)
    except Exception as exc:
        session.rollback()
        result.errors.append(str(exc))
        logger.exception("Error cleaning actions")
    finally:
        session.close()

    return result


def archive_old_matches(days: int = 365, *, dry_run: bool = False) -> ModuleCleanupResult:
    """Delete old matching runs and their associated records older than *days*."""
    result = ModuleCleanupResult(module="eob_matching")
    if days <= 0:
        logger.info("EOB matching retention set to infinite — skipping cleanup")
        return result

    from doc_intelligence_hub.modules.eob_matching.database import (
        BillRecord,
        EOBRecord,
        MatchingRun,
        MatchRecord,
        get_session,
    )

    result = ModuleCleanupResult(module="eob_matching")
    cutoff = datetime.now(UTC) - timedelta(days=days)

    session = get_session()
    try:
        # Find old run IDs
        old_runs = session.query(MatchingRun).filter(MatchingRun.started_at < cutoff).all()
        old_run_ids = [r.id for r in old_runs]

        if not old_run_ids:
            logger.info("No EOB matching runs older than %d days to clean up", days)
            return result

        # Count records to be deleted
        match_count = session.query(MatchRecord).filter(MatchRecord.run_id.in_(old_run_ids)).count()
        eob_count = session.query(EOBRecord).filter(EOBRecord.run_id.in_(old_run_ids)).count()
        bill_count = session.query(BillRecord).filter(BillRecord.run_id.in_(old_run_ids)).count()
        total = match_count + eob_count + bill_count + len(old_run_ids)

        result.records_deleted = total

        if not dry_run and total > 0:
            session.query(MatchRecord).filter(MatchRecord.run_id.in_(old_run_ids)).delete(
                synchronize_session=False
            )
            session.query(EOBRecord).filter(EOBRecord.run_id.in_(old_run_ids)).delete(
                synchronize_session=False
            )
            session.query(BillRecord).filter(BillRecord.run_id.in_(old_run_ids)).delete(
                synchronize_session=False
            )
            session.query(MatchingRun).filter(MatchingRun.id.in_(old_run_ids)).delete(
                synchronize_session=False
            )
            session.commit()
            logger.info(
                "Deleted %d EOB matching records (%d runs, %d matches, %d EOBs, %d bills) older than %d days",
                total,
                len(old_run_ids),
                match_count,
                eob_count,
                bill_count,
                days,
            )
        elif dry_run:
            logger.info(
                "[DRY RUN] Would delete %d EOB records (%d runs, %d matches, %d EOBs, %d bills)",
                total,
                len(old_run_ids),
                match_count,
                eob_count,
                bill_count,
            )
    except Exception as exc:
        session.rollback()
        result.errors.append(str(exc))
        logger.exception("Error cleaning EOB matching data")
    finally:
        session.close()

    return result


def cleanup_old_discovery_runs(days: int = 365, *, dry_run: bool = False) -> ModuleCleanupResult:
    """Delete old statement discovery and recommendation runs older than *days*.

    Uses raw sqlite3 since the statements module uses stdlib sqlite3, not SQLAlchemy.
    CASCADE deletes handle child rows (providers, recommendations).
    """
    result = ModuleCleanupResult(module="discovery_runs")
    if days <= 0:
        logger.info("Discovery runs retention set to infinite — skipping cleanup")
        return result

    cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    db_path = _PROJECT_ROOT / "data" / "statement-tracker.db"
    if not db_path.exists():
        logger.info("Statement tracker DB not found at %s — skipping", db_path)
        return result

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")

        # Discovery runs
        row = conn.execute(
            "SELECT COUNT(*) FROM discovery_runs WHERE run_at < ?", (cutoff,)
        ).fetchone()
        discovery_count = row[0] if row else 0

        # Recommendation runs
        row = conn.execute(
            "SELECT COUNT(*) FROM recommendation_runs WHERE run_at < ?", (cutoff,)
        ).fetchone()
        rec_count = row[0] if row else 0

        total = discovery_count + rec_count
        result.records_deleted = total

        if not dry_run and total > 0:
            conn.execute("DELETE FROM discovery_runs WHERE run_at < ?", (cutoff,))
            conn.execute("DELETE FROM recommendation_runs WHERE run_at < ?", (cutoff,))
            conn.commit()
            logger.info(
                "Deleted %d discovery runs and %d recommendation runs older than %d days",
                discovery_count,
                rec_count,
                days,
            )
        elif dry_run:
            logger.info(
                "[DRY RUN] Would delete %d discovery runs, %d recommendation runs",
                discovery_count,
                rec_count,
            )
    except Exception as exc:
        result.errors.append(str(exc))
        logger.exception("Error cleaning discovery runs")
    finally:
        conn.close()

    return result


# ------------------------------------------------------------------
# VACUUM helper
# ------------------------------------------------------------------


def _get_db_size(path: Path) -> int:
    """Return file size in bytes, or 0 if the file does not exist."""
    try:
        return path.stat().st_size if path.exists() else 0
    except OSError:
        return 0


def vacuum_databases() -> int:
    """Run VACUUM on all DI Hub databases to reclaim disk space.

    Returns:
        Total bytes reclaimed across all databases.
    """
    db_paths = [
        _PROJECT_ROOT / "data" / "statement-tracker.db",
        _PROJECT_ROOT / "data" / "eob_matching.db",
        _PROJECT_ROOT / "data" / "actions.db",
        _PROJECT_ROOT / "data" / "alerts.db",
    ]

    total_reclaimed = 0
    for db_path in db_paths:
        if not db_path.exists():
            continue
        try:
            size_before = _get_db_size(db_path)
            conn = sqlite3.connect(str(db_path))
            conn.execute("VACUUM")
            conn.close()
            size_after = _get_db_size(db_path)
            reclaimed = max(0, size_before - size_after)
            total_reclaimed += reclaimed
            if reclaimed > 0:
                logger.info(
                    "VACUUM %s: reclaimed %s",
                    db_path.name,
                    _human_bytes(reclaimed),
                )
        except Exception:
            logger.exception("VACUUM failed for %s", db_path.name)

    return total_reclaimed


def _human_bytes(n: int) -> str:
    """Format byte count for human-readable logging."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} TB"


# ------------------------------------------------------------------
# Orchestrator
# ------------------------------------------------------------------


def run_cleanup(
    *,
    dry_run: bool = False,
    config: RetentionConfig | None = None,
) -> CleanupResult:
    """Execute the full cleanup sweep across all modules.

    Args:
        dry_run: If True, count records but don't delete anything.
        config: Retention configuration (loaded from YAML/env if None).

    Returns:
        CleanupResult with per-module breakdown.
    """
    cfg = config or load_retention_config()
    result = CleanupResult(
        dry_run=dry_run,
        started_at=datetime.now(UTC).isoformat(),
    )

    def _fmt(d: int) -> str:
        return "∞" if d <= 0 else f"{d}d"

    logger.info(
        "Starting %scleanup: processing_history=%s, alerts=%s, actions=%s, "
        "matches=%s, discovery=%s",
        "[DRY RUN] " if dry_run else "",
        _fmt(cfg.processing_history_days),
        _fmt(cfg.alerts_days),
        _fmt(cfg.actions_days),
        _fmt(cfg.matches_days),
        _fmt(cfg.discovery_runs_days),
    )

    result.modules.append(cleanup_processing_history(cfg.processing_history_days, dry_run=dry_run))
    result.modules.append(cleanup_old_alerts(cfg.alerts_days, dry_run=dry_run))
    result.modules.append(archive_old_actions(cfg.actions_days, dry_run=dry_run))
    result.modules.append(archive_old_matches(cfg.matches_days, dry_run=dry_run))
    result.modules.append(cleanup_old_discovery_runs(cfg.discovery_runs_days, dry_run=dry_run))

    # VACUUM to reclaim space (skip on dry run)
    if not dry_run and result.total_deleted > 0:
        result.space_reclaimed_bytes = vacuum_databases()

    result.finished_at = datetime.now(UTC).isoformat()

    logger.info(
        "Cleanup %s: deleted=%d, archived=%d, space_reclaimed=%s",
        "preview" if dry_run else "complete",
        result.total_deleted,
        result.total_archived,
        _human_bytes(result.space_reclaimed_bytes),
    )

    return result


# ------------------------------------------------------------------
# Storage statistics
# ------------------------------------------------------------------

# Map of database file → list of (table_name, display_label, module)
_DB_TABLE_MAP: dict[str, list[tuple[str, str, str]]] = {
    "actions.db": [
        ("actions", "Actions", "action_queue"),
        ("processing_history", "Processing History", "action_queue"),
    ],
    "eob_matching.db": [
        ("matching_runs", "Matching Runs", "eob_matching"),
        ("eob_records", "EOB Records", "eob_matching"),
        ("bill_records", "Bill Records", "eob_matching"),
        ("matches", "Match Records", "eob_matching"),
    ],
    "statement-tracker.db": [
        ("discovery_runs", "Discovery Runs", "statements"),
        ("providers", "Providers", "statements"),
        ("recommendation_runs", "Recommendation Runs", "statements"),
        ("recommendations", "Recommendations", "statements"),
        ("provider_overrides", "Provider Overrides", "statements"),
    ],
    "alerts.db": [
        ("alerts", "Alerts", "alerts"),
    ],
}


@dataclass
class TableStats:
    """Stats for a single database table."""

    database: str
    table: str
    label: str
    module: str
    row_count: int = 0


@dataclass
class StorageStats:
    """Full storage breakdown across all DI Hub databases."""

    databases: list[dict[str, Any]] = field(default_factory=list)
    tables: list[TableStats] = field(default_factory=list)
    total_size_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_size_bytes": self.total_size_bytes,
            "total_size_human": _human_bytes(self.total_size_bytes),
            "databases": self.databases,
            "tables": [
                {
                    "database": t.database,
                    "table": t.table,
                    "label": t.label,
                    "module": t.module,
                    "row_count": t.row_count,
                }
                for t in self.tables
            ],
        }


def get_storage_stats() -> StorageStats:
    """Gather storage usage across all DI Hub databases and tables."""
    stats = StorageStats()

    for db_file, table_defs in _DB_TABLE_MAP.items():
        db_path = _PROJECT_ROOT / "data" / db_file
        size = _get_db_size(db_path)
        db_info: dict[str, Any] = {
            "name": db_file,
            "size_bytes": size,
            "size_human": _human_bytes(size),
            "exists": db_path.exists(),
        }
        stats.databases.append(db_info)
        stats.total_size_bytes += size

        if not db_path.exists():
            for table_name, label, module in table_defs:
                stats.tables.append(
                    TableStats(
                        database=db_file,
                        table=table_name,
                        label=label,
                        module=module,
                        row_count=0,
                    )
                )
            continue

        try:
            conn = sqlite3.connect(str(db_path))
            for table_name, label, module in table_defs:
                try:
                    row = conn.execute(f"SELECT COUNT(*) FROM [{table_name}]").fetchone()
                    count = row[0] if row else 0
                except Exception:
                    count = 0
                stats.tables.append(
                    TableStats(
                        database=db_file,
                        table=table_name,
                        label=label,
                        module=module,
                        row_count=count,
                    )
                )
            conn.close()
        except Exception:
            logger.exception("Could not read stats from %s", db_file)

    return stats
