"""Protected restart and audit storage for metadata migrations."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path
from typing import Protocol

from .models import ProtectedRecord, to_json_safe


class MigrationStateStore(Protocol):
    def start_run(
        self,
        run_id: str,
        *,
        registry_digest: str,
        config_digest: str,
        instance_digest: str,
        mode: str,
    ) -> None: ...

    def validate_resume(
        self,
        run_id: str,
        *,
        registry_digest: str,
        config_digest: str,
        instance_digest: str,
        mode: str,
    ) -> str | None: ...

    def has_success(self, run_id: str, idempotency_key: str) -> bool: ...

    def record_and_checkpoint(
        self, run_id: str, record: ProtectedRecord, next_cursor: str | None
    ) -> None: ...

    def checkpoint(self, run_id: str, cursor: str | None) -> None: ...

    def sanitized_counts(self, run_id: str) -> tuple[dict[str, int], dict[str, dict[str, int]]]: ...

    def finish_run(self, run_id: str, completion_state: str) -> None: ...

    def cleanup(self, *, finished_before: str) -> int: ...


class SQLiteMigrationStateStore:
    """SQLite state with atomic audit-result and checkpoint commits."""

    def __init__(
        self,
        path: str | Path,
        *,
        allow_unverified_windows_permissions: bool = False,
    ):
        self.path = Path(path)
        if os.name == "nt" and not allow_unverified_windows_permissions:
            raise PermissionError(
                "Windows ACL protection cannot be verified; use a protected runtime "
                "state implementation or an explicitly pre-verified path"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError as exc:
            self.connection.close()
            raise PermissionError("Unable to protect migration state database") from exc
        if os.name != "nt" and stat.S_IMODE(self.path.stat().st_mode) & 0o077:
            self.connection.close()
            raise PermissionError("Migration state database must be owner-readable only")
        self.connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS migration_runs (
                    run_id TEXT PRIMARY KEY,
                    registry_digest TEXT NOT NULL,
                    config_digest TEXT NOT NULL,
                    instance_digest TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    cursor TEXT,
                    completion_state TEXT NOT NULL DEFAULT 'running',
                    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS migration_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    document_id INTEGER NOT NULL,
                    stable_key TEXT NOT NULL,
                    action TEXT NOT NULL,
                    result TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    protected_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS ix_migration_results_active
                    ON migration_results (run_id, document_id, stable_key, active);
                """
            )

    def start_run(
        self,
        run_id: str,
        *,
        registry_digest: str,
        config_digest: str,
        instance_digest: str,
        mode: str,
    ) -> None:
        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO migration_runs
                        (run_id, registry_digest, config_digest, instance_digest, mode)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (run_id, registry_digest, config_digest, instance_digest, mode),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"Migration run {run_id} already exists; use resume or a new run ID"
            ) from exc

    def validate_resume(
        self,
        run_id: str,
        *,
        registry_digest: str,
        config_digest: str,
        instance_digest: str,
        mode: str,
    ) -> str | None:
        row = self.connection.execute(
            "SELECT * FROM migration_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown migration run {run_id}")
        if row["completion_state"] == "completed":
            raise ValueError(f"Migration run {run_id} already completed")
        expected = (registry_digest, config_digest, instance_digest, mode)
        actual = (
            row["registry_digest"],
            row["config_digest"],
            row["instance_digest"],
            row["mode"],
        )
        if actual != expected:
            raise ValueError("Resume refused: registry, configuration, instance, or mode changed")
        return row["cursor"]

    def has_success(self, run_id: str, idempotency_key: str) -> bool:
        row = self.connection.execute(
            """
            SELECT result FROM migration_results
            WHERE run_id = ? AND idempotency_key = ? AND active = 1
            """,
            (run_id, idempotency_key),
        ).fetchone()
        return row is not None and row["result"] in {"applied", "reconciled", "skipped"}

    def record_and_checkpoint(
        self, run_id: str, record: ProtectedRecord, next_cursor: str | None
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE migration_results SET active = 0
                WHERE run_id = ? AND document_id = ? AND stable_key = ? AND active = 1
                """,
                (run_id, record.document_id, record.stable_key),
            )
            self.connection.execute(
                """
                INSERT INTO migration_results
                    (run_id, idempotency_key, document_id, stable_key, action,
                     result, reason_code, protected_json, recorded_at, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    run_id,
                    record.idempotency_key,
                    record.document_id,
                    record.stable_key,
                    record.action.value,
                    record.result.value,
                    record.reason_code.value,
                    json.dumps(to_json_safe(record), sort_keys=True),
                    record.recorded_at,
                ),
            )
            self.connection.execute(
                "UPDATE migration_runs SET cursor = ? WHERE run_id = ?",
                (next_cursor, run_id),
            )

    def finish_run(self, run_id: str, completion_state: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE migration_runs
                SET completion_state = ?, finished_at = CURRENT_TIMESTAMP
                WHERE run_id = ?
                """,
                (completion_state, run_id),
            )

    def checkpoint(self, run_id: str, cursor: str | None) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE migration_runs SET cursor = ? WHERE run_id = ?",
                (cursor, run_id),
            )

    def sanitized_counts(self, run_id: str) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
        totals: dict[str, int] = {}
        grouped: dict[str, dict[str, int]] = {}
        rows = self.connection.execute(
            """
            SELECT stable_key, result, reason_code, COUNT(*) AS item_count
            FROM migration_results
            WHERE run_id = ? AND active = 1
            GROUP BY stable_key, result, reason_code
            """,
            (run_id,),
        )
        for row in rows:
            count = int(row["item_count"])
            totals[row["result"]] = totals.get(row["result"], 0) + count
            key_counts = grouped.setdefault(row["stable_key"], {})
            key_counts[row["result"]] = key_counts.get(row["result"], 0) + count
            reason_key = f"reason:{row['reason_code']}"
            key_counts[reason_key] = key_counts.get(reason_key, 0) + count
        return totals, grouped

    def cleanup(self, *, finished_before: str) -> int:
        with self.connection:
            run_ids = [
                row["run_id"]
                for row in self.connection.execute(
                    """
                    SELECT run_id FROM migration_runs
                    WHERE finished_at IS NOT NULL AND finished_at < ?
                    """,
                    (finished_before,),
                )
            ]
            for run_id in run_ids:
                self.connection.execute("DELETE FROM migration_results WHERE run_id = ?", (run_id,))
                self.connection.execute("DELETE FROM migration_runs WHERE run_id = ?", (run_id,))
        return len(run_ids)
