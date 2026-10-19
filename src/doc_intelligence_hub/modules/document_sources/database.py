from __future__ import annotations

import json
import sqlite3
import uuid
from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from doc_intelligence_hub.modules.document_sources.models import (
    CoverageOccurrence,
    CoverageOccurrenceDetail,
    DocumentExpectation,
    DocumentExpectationCreate,
    DocumentExpectationUpdate,
    DocumentSeries,
    DocumentSeriesCreate,
    DocumentSeriesUpdate,
    DocumentSource,
    DocumentSourceUpdate,
    ExternalSignalRelationship,
    ExternalSignalRelationshipUpdate,
    SourceReopenRequest,
    SourceSeriesAnalysisResult,
)

SOURCE_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS document_source_schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS document_sources (
    deployment_id TEXT NOT NULL,
    correspondent_id INTEGER NOT NULL,
    current_name TEXT NOT NULL,
    review_state TEXT NOT NULL CHECK (review_state IN ('needs_review', 'resolved')),
    disposition TEXT CHECK (disposition IN (
        'expectations_configured', 'no_series_expected',
        'waiting_for_history', 'ignored_retired'
    )),
    lifecycle TEXT NOT NULL DEFAULT 'active'
        CHECK (lifecycle IN ('active', 'orphaned', 'retired')),
    reopen_reasons_json TEXT NOT NULL DEFAULT '["new_source"]',
    waiting_revisit_date TEXT,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT,
    last_evidence_at TEXT,
    last_reviewed_at TEXT,
    orphaned_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (deployment_id, correspondent_id)
);

CREATE INDEX IF NOT EXISTS idx_document_sources_queue
    ON document_sources(deployment_id, review_state, disposition, lifecycle);

CREATE TABLE IF NOT EXISTS document_source_events (
    id TEXT PRIMARY KEY,
    deployment_id TEXT NOT NULL,
    correspondent_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    reason TEXT,
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_series_v2 (
    id TEXT PRIMARY KEY,
    deployment_id TEXT NOT NULL,
    source_correspondent_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    discriminator TEXT,
    membership_revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (deployment_id, source_correspondent_id)
        REFERENCES document_sources(deployment_id, correspondent_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS document_series_members_v2 (
    series_id TEXT NOT NULL REFERENCES document_series_v2(id) ON DELETE CASCADE,
    document_id INTEGER NOT NULL,
    document_date TEXT,
    period_key TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (series_id, document_id)
);

CREATE TABLE IF NOT EXISTS source_expectations (
    id TEXT PRIMARY KEY,
    deployment_id TEXT NOT NULL,
    source_correspondent_id INTEGER NOT NULL,
    series_id TEXT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    expectation_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    cadence_json TEXT,
    importance TEXT NOT NULL,
    monitoring_mode TEXT NOT NULL,
    calendar_rule_json TEXT,
    availability_window_json TEXT,
    grace_period_days INTEGER NOT NULL,
    escalation_policy_json TEXT NOT NULL,
    fulfillment_rule_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    title_convention_json TEXT,
    metadata_policy_json TEXT NOT NULL,
    acquisition_source_id TEXT,
    acquisition_notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (deployment_id, source_correspondent_id)
        REFERENCES document_sources(deployment_id, correspondent_id) ON DELETE RESTRICT,
    FOREIGN KEY (series_id) REFERENCES document_series_v2(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_source_expectations_source
    ON source_expectations(deployment_id, source_correspondent_id, status);

CREATE TABLE IF NOT EXISTS external_signal_relationships (
    deployment_id TEXT NOT NULL,
    signal_id TEXT NOT NULL,
    source_correspondent_id INTEGER,
    resolution TEXT NOT NULL,
    safe_label TEXT,
    revisit_date TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (deployment_id, signal_id),
    FOREIGN KEY (deployment_id, source_correspondent_id)
        REFERENCES document_sources(deployment_id, correspondent_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS external_signal_expectation_links (
    deployment_id TEXT NOT NULL,
    signal_id TEXT NOT NULL,
    expectation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (deployment_id, signal_id, expectation_id),
    FOREIGN KEY (expectation_id) REFERENCES source_expectations(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS coverage_occurrences (
    id TEXT PRIMARY KEY,
    deployment_id TEXT NOT NULL,
    expectation_id TEXT NOT NULL,
    period_key TEXT NOT NULL,
    due_start TEXT NOT NULL,
    due_end TEXT NOT NULL,
    state TEXT NOT NULL,
    fulfilled_document_id INTEGER,
    fulfilled_at TEXT,
    evaluated_at TEXT NOT NULL,
    UNIQUE (deployment_id, expectation_id, period_key),
    FOREIGN KEY (expectation_id) REFERENCES source_expectations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS coverage_occurrence_overrides (
    occurrence_id TEXT PRIMARY KEY REFERENCES coverage_occurrences(id) ON DELETE CASCADE,
    grace_extended_until TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_series_analysis (
    deployment_id TEXT NOT NULL,
    correspondent_id INTEGER NOT NULL,
    signal_id TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    PRIMARY KEY (deployment_id, correspondent_id, signal_id),
    FOREIGN KEY (deployment_id, correspondent_id)
        REFERENCES document_sources(deployment_id, correspondent_id) ON DELETE CASCADE
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SourceDatabase:
    def __init__(self, path: str | Path, deployment_id: str) -> None:
        self.path = str(path)
        self.deployment_id = deployment_id
        self._connection: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = sqlite3.connect(self.path, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.executescript(_SCHEMA)
            expectation_columns = {
                row["name"]
                for row in self._connection.execute(
                    "PRAGMA table_info(source_expectations)"
                ).fetchall()
            }
            if "acquisition_notes" not in expectation_columns:
                self._connection.execute(
                    "ALTER TABLE source_expectations ADD COLUMN acquisition_notes TEXT"
                )
            row = self._connection.execute(
                "SELECT version FROM document_source_schema_version LIMIT 1"
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO document_source_schema_version(version) VALUES (?)",
                    (SOURCE_SCHEMA_VERSION,),
                )
            elif row["version"] != SOURCE_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Document source schema {row['version']} is not supported; "
                    "reset the local policy database"
                )
            self._connection.commit()
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @staticmethod
    def _source(row: sqlite3.Row) -> DocumentSource:
        return DocumentSource(
            correspondent_id=row["correspondent_id"],
            current_name=row["current_name"],
            review_state=row["review_state"],
            disposition=row["disposition"],
            lifecycle=row["lifecycle"],
            reopen_reasons=json.loads(row["reopen_reasons_json"]),
            waiting_revisit_date=row["waiting_revisit_date"],
            aliases=json.loads(row["aliases_json"]),
            notes=row["notes"],
            last_evidence_at=row["last_evidence_at"],
            last_reviewed_at=row["last_reviewed_at"],
            orphaned_at=row["orphaned_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def sync_sources(self, correspondents: list[dict[str, Any]]) -> dict[str, int]:
        conn = self.connect()
        current = {int(item["id"]): str(item["name"]).strip() for item in correspondents}
        if any(item <= 0 or not name for item, name in current.items()):
            raise ValueError("Paperless correspondents require a positive ID and non-empty name")
        rows = conn.execute(
            "SELECT * FROM document_sources WHERE deployment_id = ?",
            (self.deployment_id,),
        ).fetchall()
        existing = {row["correspondent_id"]: row for row in rows}
        created = updated = orphaned = restored = 0
        now = _now()
        with conn:
            for correspondent_id, name in current.items():
                row = existing.get(correspondent_id)
                if row is None:
                    conn.execute(
                        """INSERT INTO document_sources (
                               deployment_id, correspondent_id, current_name,
                               review_state, reopen_reasons_json, created_at, updated_at
                           ) VALUES (?, ?, ?, 'needs_review', '["new_source"]', ?, ?)""",
                        (self.deployment_id, correspondent_id, name, now, now),
                    )
                    created += 1
                    continue
                next_lifecycle = "active" if row["lifecycle"] == "orphaned" else row["lifecycle"]
                reasons = json.loads(row["reopen_reasons_json"])
                if row["lifecycle"] == "orphaned":
                    reasons = list(dict.fromkeys([*reasons, "correspondent_relinked"]))
                    restored += 1
                if row["current_name"] != name or next_lifecycle != row["lifecycle"]:
                    conn.execute(
                        """UPDATE document_sources
                           SET current_name = ?, lifecycle = ?, orphaned_at = NULL,
                               review_state = 'needs_review', reopen_reasons_json = ?,
                               updated_at = ?
                           WHERE deployment_id = ? AND correspondent_id = ?""",
                        (
                            name,
                            next_lifecycle,
                            json.dumps(reasons),
                            now,
                            self.deployment_id,
                            correspondent_id,
                        ),
                    )
                    updated += 1
            for correspondent_id, row in existing.items():
                if correspondent_id not in current and row["lifecycle"] == "active":
                    reasons = list(
                        dict.fromkeys(
                            [*json.loads(row["reopen_reasons_json"]), "correspondent_orphaned"]
                        )
                    )
                    conn.execute(
                        """UPDATE document_sources
                           SET lifecycle = 'orphaned', orphaned_at = ?,
                               review_state = 'needs_review', reopen_reasons_json = ?,
                               updated_at = ?
                           WHERE deployment_id = ? AND correspondent_id = ?""",
                        (now, json.dumps(reasons), now, self.deployment_id, correspondent_id),
                    )
                    orphaned += 1
        return {
            "created": created,
            "updated": updated,
            "orphaned": orphaned,
            "restored": restored,
        }

    def list_sources(
        self,
        *,
        review_state: str | None = None,
        disposition: str | None = None,
        search: str | None = None,
    ) -> list[DocumentSource]:
        conn = self.connect()
        sql = "SELECT * FROM document_sources WHERE deployment_id = ?"
        params: list[Any] = [self.deployment_id]
        if review_state:
            sql += " AND review_state = ?"
            params.append(review_state)
        if disposition:
            sql += " AND disposition = ?"
            params.append(disposition)
        if search:
            sql += " AND (current_name LIKE ? OR aliases_json LIKE ?)"
            term = f"%{search}%"
            params.extend([term, term])
        sql += " ORDER BY current_name COLLATE NOCASE, correspondent_id"
        return [self._source(row) for row in conn.execute(sql, params).fetchall()]

    def get_source(self, correspondent_id: int) -> DocumentSource | None:
        row = (
            self.connect()
            .execute(
                """SELECT * FROM document_sources
               WHERE deployment_id = ? AND correspondent_id = ?""",
                (self.deployment_id, correspondent_id),
            )
            .fetchone()
        )
        return self._source(row) if row else None

    def save_series_analysis(self, result: SourceSeriesAnalysisResult) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO source_series_analysis (
                       deployment_id, correspondent_id, signal_id, result_json, analyzed_at
                   ) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(deployment_id, correspondent_id, signal_id) DO UPDATE SET
                       result_json = excluded.result_json,
                       analyzed_at = excluded.analyzed_at""",
                (
                    self.deployment_id,
                    result.correspondent_id,
                    result.signal_id or "",
                    result.model_dump_json(),
                    result.analyzed_at,
                ),
            )

    def get_series_analysis(
        self, correspondent_id: int, signal_id: str | None = None
    ) -> SourceSeriesAnalysisResult | None:
        row = (
            self.connect()
            .execute(
                """SELECT result_json FROM source_series_analysis
                   WHERE deployment_id = ? AND correspondent_id = ? AND signal_id = ?""",
                (self.deployment_id, correspondent_id, signal_id or ""),
            )
            .fetchone()
        )
        return (
            SourceSeriesAnalysisResult.model_validate_json(row["result_json"])
            if row
            else None
        )

    def clear_series_analysis(self, correspondent_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """DELETE FROM source_series_analysis
                   WHERE deployment_id = ? AND correspondent_id = ?""",
                (self.deployment_id, correspondent_id),
            )

    def update_source(self, correspondent_id: int, update: DocumentSourceUpdate) -> DocumentSource:
        source = self.get_source(correspondent_id)
        if source is None:
            raise KeyError("document_source_not_found")
        if update.disposition == "expectations_configured":
            count = (
                self.connect()
                .execute(
                    """SELECT COUNT(*) FROM source_expectations
                   WHERE deployment_id = ? AND source_correspondent_id = ?
                     AND status = 'confirmed' AND expectation_mode != 'not_expected'""",
                    (self.deployment_id, correspondent_id),
                )
                .fetchone()[0]
            )
            if count == 0:
                raise ValueError(
                    "expectations_configured requires at least one confirmed expectation"
                )
        now = _now()
        aliases = source.aliases if update.aliases is None else update.aliases
        notes = source.notes if "notes" not in update.model_fields_set else update.notes
        with self.connect() as conn:
            conn.execute(
                """UPDATE document_sources
                   SET disposition = ?, review_state = 'resolved',
                       reopen_reasons_json = '[]', waiting_revisit_date = ?,
                       aliases_json = ?, notes = ?, last_reviewed_at = ?, updated_at = ?
                   WHERE deployment_id = ? AND correspondent_id = ?""",
                (
                    update.disposition,
                    update.waiting_revisit_date.isoformat()
                    if update.waiting_revisit_date
                    else None,
                    json.dumps(aliases),
                    notes,
                    now,
                    now,
                    self.deployment_id,
                    correspondent_id,
                ),
            )
            self._event(conn, correspondent_id, "source_disposition_recorded", update.disposition)
        result = self.get_source(correspondent_id)
        assert result is not None
        return result

    def reopen_source(self, correspondent_id: int, request: SourceReopenRequest) -> DocumentSource:
        source = self.get_source(correspondent_id)
        if source is None:
            raise KeyError("document_source_not_found")
        reasons = list(dict.fromkeys([*source.reopen_reasons, request.reason]))
        evidence_at = request.evidence_at.isoformat() if request.evidence_at else _now()
        with self.connect() as conn:
            conn.execute(
                """UPDATE document_sources
                   SET review_state = 'needs_review', reopen_reasons_json = ?,
                       last_evidence_at = ?, updated_at = ?
                   WHERE deployment_id = ? AND correspondent_id = ?""",
                (
                    json.dumps(reasons),
                    evidence_at,
                    _now(),
                    self.deployment_id,
                    correspondent_id,
                ),
            )
            self._event(conn, correspondent_id, "source_reopened", request.reason, request.detail)
        result = self.get_source(correspondent_id)
        assert result is not None
        return result

    def reopen_due_waiting_sources(self, as_of: date) -> int:
        rows = (
            self.connect()
            .execute(
                """SELECT correspondent_id FROM document_sources
               WHERE deployment_id = ? AND lifecycle = 'active'
                 AND disposition = 'waiting_for_history'
                 AND review_state = 'resolved'
                 AND waiting_revisit_date <= ?""",
                (self.deployment_id, as_of.isoformat()),
            )
            .fetchall()
        )
        for row in rows:
            self.reopen_source(
                row["correspondent_id"],
                SourceReopenRequest(reason="waiting_revisit_due"),
            )
        return len(rows)

    def _event(
        self,
        conn: sqlite3.Connection,
        correspondent_id: int,
        event_type: str,
        reason: str | None = None,
        detail: str | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO document_source_events (
                   id, deployment_id, correspondent_id, event_type, reason, detail, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                uuid.uuid4().hex,
                self.deployment_id,
                correspondent_id,
                event_type,
                reason,
                detail,
                _now(),
            ),
        )

    @staticmethod
    def _series(conn: sqlite3.Connection, row: sqlite3.Row) -> DocumentSeries:
        members = conn.execute(
            """SELECT document_id FROM document_series_members_v2
               WHERE series_id = ? ORDER BY document_id""",
            (row["id"],),
        ).fetchall()
        return DocumentSeries(
            id=row["id"],
            source_correspondent_id=row["source_correspondent_id"],
            name=row["name"],
            kind=row["kind"],
            discriminator=row["discriminator"],
            document_ids=[item["document_id"] for item in members],
            membership_revision=row["membership_revision"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_series(self, create: DocumentSeriesCreate) -> DocumentSeries:
        if self.get_source(create.source_correspondent_id) is None:
            raise KeyError("document_source_not_found")
        conn = self.connect()
        series_id = uuid.uuid4().hex
        now = _now()
        with conn:
            conn.execute(
                """INSERT INTO document_series_v2 (
                       id, deployment_id, source_correspondent_id, name, kind,
                       discriminator, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    series_id,
                    self.deployment_id,
                    create.source_correspondent_id,
                    create.name,
                    create.kind,
                    create.discriminator,
                    now,
                    now,
                ),
            )
            self._replace_members(conn, series_id, create.document_ids)
            conn.execute(
                """DELETE FROM source_series_analysis
                   WHERE deployment_id = ? AND correspondent_id = ?""",
                (self.deployment_id, create.source_correspondent_id),
            )
        result = self.get_series(series_id)
        assert result is not None
        return result

    def get_series(self, series_id: str) -> DocumentSeries | None:
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM document_series_v2 WHERE deployment_id = ? AND id = ?",
            (self.deployment_id, series_id),
        ).fetchone()
        return self._series(conn, row) if row else None

    def list_series(self, correspondent_id: int | None = None) -> list[DocumentSeries]:
        conn = self.connect()
        sql = "SELECT * FROM document_series_v2 WHERE deployment_id = ?"
        params: list[Any] = [self.deployment_id]
        if correspondent_id is not None:
            sql += " AND source_correspondent_id = ?"
            params.append(correspondent_id)
        sql += " ORDER BY name COLLATE NOCASE, id"
        return [self._series(conn, row) for row in conn.execute(sql, params).fetchall()]

    def update_series(self, series_id: str, update: DocumentSeriesUpdate) -> DocumentSeries:
        current = self.get_series(series_id)
        if current is None:
            raise KeyError("document_series_not_found")
        name = current.name if update.name is None else update.name
        discriminator = (
            current.discriminator
            if "discriminator" not in update.model_fields_set
            else update.discriminator
        )
        membership_changed = (
            update.document_ids is not None and update.document_ids != current.document_ids
        )
        with self.connect() as conn:
            conn.execute(
                """UPDATE document_series_v2
                   SET name = ?, discriminator = ?,
                       membership_revision = membership_revision + ?,
                       updated_at = ?
                   WHERE deployment_id = ? AND id = ?""",
                (
                    name,
                    discriminator,
                    int(membership_changed),
                    _now(),
                    self.deployment_id,
                    series_id,
                ),
            )
            if update.document_ids is not None:
                self._replace_members(conn, series_id, update.document_ids)
            if membership_changed:
                conn.execute(
                    """DELETE FROM source_series_analysis
                       WHERE deployment_id = ? AND correspondent_id = ?""",
                    (self.deployment_id, current.source_correspondent_id),
                )
                self._event(
                    conn,
                    current.source_correspondent_id,
                    "series_membership_changed",
                    "series_membership_changed",
                    series_id,
                )
        if membership_changed:
            self.reopen_source(
                current.source_correspondent_id,
                SourceReopenRequest(
                    reason="series_membership_changed",
                    detail=f"Series {series_id} membership changed",
                ),
            )
        result = self.get_series(series_id)
        assert result is not None
        return result

    @staticmethod
    def _replace_members(conn: sqlite3.Connection, series_id: str, document_ids: list[int]) -> None:
        conn.execute("DELETE FROM document_series_members_v2 WHERE series_id = ?", (series_id,))
        now = _now()
        conn.executemany(
            """INSERT INTO document_series_members_v2 (
                   series_id, document_id, created_at
               ) VALUES (?, ?, ?)""",
            [(series_id, document_id, now) for document_id in document_ids],
        )

    @staticmethod
    def _expectation(row: sqlite3.Row) -> DocumentExpectation:
        payload = {
            "id": row["id"],
            "source_correspondent_id": row["source_correspondent_id"],
            "series_id": row["series_id"],
            "name": row["name"],
            "kind": row["kind"],
            "expectation_mode": row["expectation_mode"],
            "status": row["status"],
            "cadence": json.loads(row["cadence_json"]) if row["cadence_json"] else None,
            "importance": row["importance"],
            "monitoring_mode": row["monitoring_mode"],
            "calendar_rule": json.loads(row["calendar_rule_json"])
            if row["calendar_rule_json"]
            else None,
            "availability_window": json.loads(row["availability_window_json"])
            if row["availability_window_json"]
            else None,
            "grace_period_days": row["grace_period_days"],
            "escalation_policy": json.loads(row["escalation_policy_json"]),
            "fulfillment_rule": json.loads(row["fulfillment_rule_json"]),
            "evidence": json.loads(row["evidence_json"]),
            "title_convention": json.loads(row["title_convention_json"])
            if row["title_convention_json"]
            else None,
            "metadata_policy": json.loads(row["metadata_policy_json"]),
            "acquisition_source_id": row["acquisition_source_id"],
            "acquisition_notes": row["acquisition_notes"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        return DocumentExpectation.model_validate(payload)

    def create_expectation(self, create: DocumentExpectationCreate) -> DocumentExpectation:
        source = self.get_source(create.source_correspondent_id)
        if source is None:
            raise KeyError("document_source_not_found")
        if create.series_id:
            series = self.get_series(create.series_id)
            if series is None:
                raise KeyError("document_series_not_found")
            if series.source_correspondent_id != create.source_correspondent_id:
                raise ValueError("series belongs to a different document source")
            if series.kind != create.kind:
                raise ValueError("expectation kind must match its reviewed series")
        if create.acquisition_source_id and not self._acquisition_source_exists(
            create.acquisition_source_id
        ):
            raise KeyError("acquisition_source_not_found")
        expectation_id = uuid.uuid4().hex
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO source_expectations (
                       id, deployment_id, source_correspondent_id, series_id, name, kind,
                       expectation_mode, status, cadence_json, importance, monitoring_mode,
                       calendar_rule_json, availability_window_json, grace_period_days,
                       escalation_policy_json, fulfillment_rule_json, evidence_json,
                       title_convention_json, metadata_policy_json, acquisition_source_id,
                       acquisition_notes, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    expectation_id,
                    self.deployment_id,
                    create.source_correspondent_id,
                    create.series_id,
                    create.name,
                    create.kind,
                    create.expectation_mode,
                    create.status,
                    create.cadence.model_dump_json() if create.cadence else None,
                    create.importance,
                    create.monitoring_mode,
                    create.calendar_rule.model_dump_json() if create.calendar_rule else None,
                    create.availability_window.model_dump_json()
                    if create.availability_window
                    else None,
                    create.grace_period_days,
                    create.escalation_policy.model_dump_json(),
                    create.fulfillment_rule.model_dump_json(),
                    create.evidence.model_dump_json(),
                    create.title_convention.model_dump_json() if create.title_convention else None,
                    create.metadata_policy.model_dump_json(),
                    create.acquisition_source_id,
                    create.acquisition_notes,
                    now,
                    now,
                ),
            )
        result = self.get_expectation(expectation_id)
        assert result is not None
        return result

    def get_expectation(self, expectation_id: str) -> DocumentExpectation | None:
        row = (
            self.connect()
            .execute(
                "SELECT * FROM source_expectations WHERE deployment_id = ? AND id = ?",
                (self.deployment_id, expectation_id),
            )
            .fetchone()
        )
        return self._expectation(row) if row else None

    def list_expectations(self, correspondent_id: int | None = None) -> list[DocumentExpectation]:
        sql = "SELECT * FROM source_expectations WHERE deployment_id = ?"
        params: list[Any] = [self.deployment_id]
        if correspondent_id is not None:
            sql += " AND source_correspondent_id = ?"
            params.append(correspondent_id)
        sql += " ORDER BY created_at, id"
        return [self._expectation(row) for row in self.connect().execute(sql, params).fetchall()]

    def update_expectation(
        self, expectation_id: str, update: DocumentExpectationUpdate
    ) -> DocumentExpectation:
        current = self.get_expectation(expectation_id)
        if current is None:
            raise KeyError("document_expectation_not_found")
        data = current.model_dump(exclude={"id", "created_at", "updated_at"})
        data.update(update.model_dump(exclude_unset=True))
        validated = DocumentExpectationCreate.model_validate(data)
        if validated.source_correspondent_id != current.source_correspondent_id:
            raise ValueError("expectation source cannot change")
        if validated.kind != current.kind:
            raise ValueError("expectation kind cannot change")
        series = self.get_series(validated.series_id) if validated.series_id else None
        if series and series.source_correspondent_id != current.source_correspondent_id:
            raise ValueError("series belongs to a different document source")
        if series and series.kind != validated.kind:
            raise ValueError("expectation kind must match its reviewed series")
        if validated.acquisition_source_id and not self._acquisition_source_exists(
            validated.acquisition_source_id
        ):
            raise KeyError("acquisition_source_not_found")
        with self.connect() as conn:
            conn.execute(
                """UPDATE source_expectations SET
                       series_id = ?, name = ?, expectation_mode = ?, status = ?,
                       cadence_json = ?, importance = ?, monitoring_mode = ?,
                       calendar_rule_json = ?, availability_window_json = ?,
                       grace_period_days = ?, escalation_policy_json = ?,
                       fulfillment_rule_json = ?, evidence_json = ?,
                       title_convention_json = ?, metadata_policy_json = ?,
                       acquisition_source_id = ?, acquisition_notes = ?, updated_at = ?
                   WHERE deployment_id = ? AND id = ?""",
                (
                    validated.series_id,
                    validated.name,
                    validated.expectation_mode,
                    validated.status,
                    validated.cadence.model_dump_json() if validated.cadence else None,
                    validated.importance,
                    validated.monitoring_mode,
                    validated.calendar_rule.model_dump_json() if validated.calendar_rule else None,
                    validated.availability_window.model_dump_json()
                    if validated.availability_window
                    else None,
                    validated.grace_period_days,
                    validated.escalation_policy.model_dump_json(),
                    validated.fulfillment_rule.model_dump_json(),
                    validated.evidence.model_dump_json(),
                    validated.title_convention.model_dump_json()
                    if validated.title_convention
                    else None,
                    validated.metadata_policy.model_dump_json(),
                    validated.acquisition_source_id,
                    validated.acquisition_notes,
                    _now(),
                    self.deployment_id,
                    expectation_id,
                ),
            )
        result = self.get_expectation(expectation_id)
        assert result is not None
        return result

    def _acquisition_source_exists(self, source_id: str) -> bool:
        conn = self.connect()
        table = conn.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'table' AND name = 'acquisition_sources'"""
        ).fetchone()
        if table is None:
            return False
        return (
            conn.execute(
                """SELECT 1 FROM acquisition_sources
                   WHERE deployment_id = ? AND id = ?""",
                (self.deployment_id, source_id),
            ).fetchone()
            is not None
        )

    def expectation_is_coverage_eligible(self, expectation_id: str) -> bool:
        expectation = self.get_expectation(expectation_id)
        if expectation is None:
            raise KeyError("document_expectation_not_found")
        source = self.get_source(expectation.source_correspondent_id)
        assert source is not None
        if (
            source.lifecycle != "active"
            or source.disposition != "expectations_configured"
            or expectation.status != "confirmed"
            or expectation.monitoring_mode == "none"
        ):
            return False
        if expectation.expectation_mode in {"recurring", "periodic"}:
            return expectation.cadence is not None
        if expectation.expectation_mode == "one_off":
            return (
                expectation.monitoring_mode == "deadline"
                and expectation.calendar_rule is not None
                and expectation.calendar_rule.rule_type == "one_off"
                and expectation.calendar_rule.occurrence_status in {"pending", "overdue"}
            )
        return False

    def set_external_relationship(
        self, signal_id: str, update: ExternalSignalRelationshipUpdate
    ) -> ExternalSignalRelationship:
        conn = self.connect()
        if update.source_correspondent_id is not None:
            if self.get_source(update.source_correspondent_id) is None:
                raise KeyError("document_source_not_found")
        for expectation_id in update.expectation_ids:
            expectation = self.get_expectation(expectation_id)
            if expectation is None:
                raise KeyError("document_expectation_not_found")
            if expectation.source_correspondent_id != update.source_correspondent_id:
                raise ValueError("all expectation links must belong to the selected source")
        now = _now()
        with conn:
            conn.execute(
                """INSERT INTO external_signal_relationships (
                       deployment_id, signal_id, source_correspondent_id, resolution,
                       safe_label, revisit_date, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (deployment_id, signal_id) DO UPDATE SET
                       source_correspondent_id = excluded.source_correspondent_id,
                       resolution = excluded.resolution,
                       safe_label = excluded.safe_label,
                       revisit_date = excluded.revisit_date,
                       updated_at = excluded.updated_at""",
                (
                    self.deployment_id,
                    signal_id,
                    update.source_correspondent_id,
                    update.resolution,
                    update.safe_label,
                    update.revisit_date.isoformat() if update.revisit_date else None,
                    now,
                ),
            )
            conn.execute(
                """DELETE FROM external_signal_expectation_links
                   WHERE deployment_id = ? AND signal_id = ?""",
                (self.deployment_id, signal_id),
            )
            conn.executemany(
                """INSERT INTO external_signal_expectation_links (
                       deployment_id, signal_id, expectation_id, created_at
                   ) VALUES (?, ?, ?, ?)""",
                [
                    (self.deployment_id, signal_id, expectation_id, now)
                    for expectation_id in update.expectation_ids
                ],
            )
        if update.source_correspondent_id is not None:
            self.reopen_source(
                update.source_correspondent_id,
                SourceReopenRequest(
                    reason="external_relationship_proposed",
                    detail=f"External signal {signal_id} relationship changed",
                ),
            )
        return self.get_external_relationship(signal_id)

    def get_external_relationship(self, signal_id: str) -> ExternalSignalRelationship:
        conn = self.connect()
        row = conn.execute(
            """SELECT * FROM external_signal_relationships
               WHERE deployment_id = ? AND signal_id = ?""",
            (self.deployment_id, signal_id),
        ).fetchone()
        if row is None:
            raise KeyError("external_signal_relationship_not_found")
        links = conn.execute(
            """SELECT expectation_id FROM external_signal_expectation_links
               WHERE deployment_id = ? AND signal_id = ?
               ORDER BY created_at, expectation_id""",
            (self.deployment_id, signal_id),
        ).fetchall()
        return ExternalSignalRelationship(
            signal_id=signal_id,
            source_correspondent_id=row["source_correspondent_id"],
            expectation_ids=[item["expectation_id"] for item in links],
            resolution=row["resolution"],
            safe_label=row["safe_label"],
            revisit_date=row["revisit_date"],
            updated_at=row["updated_at"],
        )

    def evaluate_coverage(
        self, as_of: date, horizon_start: date | None = None
    ) -> list[CoverageOccurrence]:
        horizon_start = horizon_start or date(as_of.year - 1, as_of.month, 1)
        results: list[CoverageOccurrence] = []
        for expectation in self.list_expectations():
            source = self.get_source(expectation.source_correspondent_id)
            assert source is not None
            periods = self._periods(expectation, horizon_start, as_of)
            if not periods and expectation.expectation_mode in {"irregular", "not_expected"}:
                continue
            eligible = self.expectation_is_coverage_eligible(expectation.id)
            member_periods = self._member_periods(expectation.series_id)
            for period_key, due_start, due_end in periods:
                fulfilled = member_periods.get(period_key)
                if fulfilled is None and expectation.series_id is None:
                    fulfilled = self._stored_occurrence_fulfillment(expectation.id, period_key)
                occurrence_id = self._occurrence_id(expectation.id, period_key)
                grace_extended_until = self._grace_extended_until(occurrence_id)
                effective_due_end = max(
                    due_end + timedelta(days=expectation.grace_period_days),
                    grace_extended_until or due_end,
                )
                if not eligible:
                    state = "unmonitored"
                elif expectation.calendar_rule and expectation.calendar_rule.rule_type == "one_off":
                    status = expectation.calendar_rule.occurrence_status
                    if status == "cancelled":
                        state = "cancelled"
                    elif fulfilled:
                        document_date = date.fromisoformat(fulfilled["document_date"])
                        state = "fulfilled_late" if document_date > due_end else "fulfilled"
                    elif status == "fulfilled":
                        state = "fulfilled"
                    elif as_of > effective_due_end:
                        state = "missing"
                    elif as_of >= due_start:
                        state = "due_soon"
                    else:
                        state = "upcoming"
                elif fulfilled:
                    document_date = date.fromisoformat(fulfilled["document_date"])
                    state = "fulfilled_late" if document_date > due_end else "fulfilled"
                elif as_of > effective_due_end:
                    state = "missing"
                elif as_of >= due_start:
                    state = "due_soon"
                else:
                    state = "upcoming"
                occurrence = self._upsert_occurrence(
                    expectation,
                    source.current_name,
                    period_key,
                    due_start,
                    due_end,
                    state,
                    fulfilled,
                    grace_extended_until,
                )
                results.append(occurrence)
        return sorted(results, key=lambda item: (item.due_end, item.expectation_name, item.id))

    def _member_periods(self, series_id: str | None) -> dict[str, sqlite3.Row]:
        if series_id is None:
            return {}
        rows = (
            self.connect()
            .execute(
                """SELECT * FROM document_series_members_v2
               WHERE series_id = ? AND period_key IS NOT NULL""",
                (series_id,),
            )
            .fetchall()
        )
        return {row["period_key"]: row for row in rows}

    def _stored_occurrence_fulfillment(
        self, expectation_id: str, period_key: str
    ) -> sqlite3.Row | None:
        return (
            self.connect()
            .execute(
                """SELECT fulfilled_document_id AS document_id,
                          fulfilled_at AS document_date
                   FROM coverage_occurrences
                   WHERE deployment_id = ? AND expectation_id = ? AND period_key = ?
                     AND fulfilled_document_id IS NOT NULL AND fulfilled_at IS NOT NULL""",
                (self.deployment_id, expectation_id, period_key),
            )
            .fetchone()
        )

    @staticmethod
    def _periods(
        expectation: DocumentExpectation, start: date, end: date
    ) -> list[tuple[str, date, date]]:
        rule = expectation.calendar_rule
        if rule and rule.rule_type == "one_off":
            assert rule.occurrence_id and rule.due_start and rule.due_end
            return [(rule.occurrence_id, rule.due_start, rule.due_end)]
        if rule and rule.rule_type in {"annual_month", "tax_year"}:
            assert rule.expected_month is not None
            periods = []
            for year in range(start.year, end.year + 1):
                month = rule.expected_month
                due_start = date(year, month, 1)
                due_end = date(year, month, monthrange(year, month)[1])
                periods.append((str(year + (rule.tax_year_offset or 0)), due_start, due_end))
            return periods
        cadence = expectation.cadence
        if cadence is None:
            return []
        periods = []
        cursor = date(start.year, start.month, 1)
        step = {"monthly": 1, "quarterly": 3, "annual": 12}[cadence.frequency]
        while cursor <= end:
            day = min(
                cadence.expected_day or monthrange(cursor.year, cursor.month)[1],
                monthrange(cursor.year, cursor.month)[1],
            )
            due_start = date(cursor.year, cursor.month, day) + timedelta(
                days=cadence.availability_delay_days
            )
            due_end = due_start + timedelta(days=cadence.grace_period_days)
            period_key = (
                f"{cursor.year:04d}-{cursor.month:02d}"
                if cadence.frequency == "monthly"
                else (
                    f"{cursor.year:04d}-Q{((cursor.month - 1) // 3) + 1}"
                    if cadence.frequency == "quarterly"
                    else f"{cursor.year:04d}"
                )
            )
            periods.append((period_key, due_start, due_end))
            month_index = cursor.year * 12 + cursor.month - 1 + step
            cursor = date(month_index // 12, month_index % 12 + 1, 1)
        return periods

    def record_series_member_period(
        self, series_id: str, document_id: int, document_date: date, period_key: str
    ) -> None:
        series = self.get_series(series_id)
        if series is None:
            raise KeyError("document_series_not_found")
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO document_series_members_v2 (
                       series_id, document_id, document_date, period_key, created_at
                   ) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (series_id, document_id) DO UPDATE SET
                       document_date = excluded.document_date,
                       period_key = excluded.period_key""",
                (series_id, document_id, document_date.isoformat(), period_key, _now()),
            )

    def get_occurrence_detail(self, occurrence_id: str) -> CoverageOccurrenceDetail:
        row = self._occurrence_row(occurrence_id)
        expectation = self.get_expectation(row["expectation_id"])
        if expectation is None:
            raise KeyError("document_expectation_not_found")
        occurrence = self._coverage_occurrence(row, expectation)
        series = self.get_series(expectation.series_id) if expectation.series_id else None
        return CoverageOccurrenceDetail(
            occurrence=occurrence,
            expectation=expectation,
            series=series,
            recent_document_ids=(
                list(reversed(series.document_ids[-5:]))
                if series
                else [occurrence.fulfilled_document_id]
                if occurrence.fulfilled_document_id
                else []
            ),
        )

    def extend_occurrence_grace(self, occurrence_id: str, extend_until: date) -> CoverageOccurrence:
        row = self._occurrence_row(occurrence_id)
        current = (
            date.fromisoformat(row["grace_extended_until"]) if row["grace_extended_until"] else None
        )
        if current and extend_until < current:
            raise ValueError("grace extension cannot shorten an existing occurrence extension")
        if extend_until < date.fromisoformat(row["due_end"]):
            raise ValueError("grace extension must end on or after the occurrence due date")
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO coverage_occurrence_overrides (
                       occurrence_id, grace_extended_until, updated_at
                   ) VALUES (?, ?, ?)
                   ON CONFLICT (occurrence_id) DO UPDATE SET
                       grace_extended_until = excluded.grace_extended_until,
                       updated_at = excluded.updated_at""",
                (occurrence_id, extend_until.isoformat(), _now()),
            )
        return self._recalculate_occurrence(occurrence_id, date.today())

    def reassign_occurrence_document(
        self,
        occurrence_id: str,
        document_id: int,
        document_date: date,
        *,
        as_of: date | None = None,
    ) -> CoverageOccurrence:
        row = self._occurrence_row(occurrence_id)
        expectation = self.get_expectation(row["expectation_id"])
        if expectation is None:
            raise KeyError("document_expectation_not_found")
        if expectation.series_id is None:
            with self.connect() as conn:
                conn.execute(
                    """UPDATE coverage_occurrences
                       SET fulfilled_document_id = ?, fulfilled_at = ?, evaluated_at = ?
                       WHERE deployment_id = ? AND id = ?""",
                    (
                        document_id,
                        document_date.isoformat(),
                        _now(),
                        self.deployment_id,
                        occurrence_id,
                    ),
                )
            return self._recalculate_occurrence(occurrence_id, as_of or date.today())
        conn = self.connect()
        affected = [
            item["series_id"]
            for item in conn.execute(
                """SELECT DISTINCT series_id FROM document_series_members_v2
                   WHERE document_id = ? AND series_id != ?""",
                (document_id, expectation.series_id),
            ).fetchall()
        ]
        with conn:
            conn.execute(
                "DELETE FROM document_series_members_v2 WHERE document_id = ? AND series_id != ?",
                (document_id, expectation.series_id),
            )
            conn.execute(
                """INSERT INTO document_series_members_v2 (
                       series_id, document_id, document_date, period_key, created_at
                   ) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (series_id, document_id) DO UPDATE SET
                       document_date = excluded.document_date,
                       period_key = excluded.period_key""",
                (
                    expectation.series_id,
                    document_id,
                    document_date.isoformat(),
                    row["period_key"],
                    _now(),
                ),
            )
            for series_id in [*affected, expectation.series_id]:
                conn.execute(
                    """UPDATE document_series_v2
                       SET membership_revision = membership_revision + 1, updated_at = ?
                       WHERE id = ?""",
                    (_now(), series_id),
                )
        return self._recalculate_occurrence(occurrence_id, as_of or date.today())

    def occurrence_assigned_document_ids(self) -> set[int]:
        conn = self.connect()
        rows = conn.execute(
            """SELECT document_id FROM document_series_members_v2 members
               JOIN document_series_v2 series ON series.id = members.series_id
               WHERE series.deployment_id = ?""",
            (self.deployment_id,),
        ).fetchall()
        occurrence_rows = conn.execute(
            """SELECT fulfilled_document_id FROM coverage_occurrences
               WHERE deployment_id = ? AND fulfilled_document_id IS NOT NULL""",
            (self.deployment_id,),
        ).fetchall()
        return {
            *[row["document_id"] for row in rows],
            *[row["fulfilled_document_id"] for row in occurrence_rows],
        }

    def _occurrence_row(self, occurrence_id: str) -> sqlite3.Row:
        row = (
            self.connect()
            .execute(
                """SELECT occurrences.*, overrides.grace_extended_until
               FROM coverage_occurrences occurrences
               LEFT JOIN coverage_occurrence_overrides overrides
                 ON overrides.occurrence_id = occurrences.id
               WHERE occurrences.deployment_id = ? AND occurrences.id = ?""",
                (self.deployment_id, occurrence_id),
            )
            .fetchone()
        )
        if row is None:
            raise KeyError("coverage_occurrence_not_found")
        return row

    def _recalculate_occurrence(self, occurrence_id: str, as_of: date) -> CoverageOccurrence:
        row = self._occurrence_row(occurrence_id)
        expectation = self.get_expectation(row["expectation_id"])
        if expectation is None:
            raise KeyError("document_expectation_not_found")
        source = self.get_source(expectation.source_correspondent_id)
        assert source is not None
        fulfilled = self._member_periods(expectation.series_id).get(row["period_key"])
        if fulfilled is None and expectation.series_id is None:
            fulfilled = self._stored_occurrence_fulfillment(expectation.id, row["period_key"])
        due_start = date.fromisoformat(row["due_start"])
        due_end = date.fromisoformat(row["due_end"])
        grace_extended_until = self._grace_extended_until(occurrence_id)
        effective_due_end = max(
            due_end + timedelta(days=expectation.grace_period_days),
            grace_extended_until or due_end,
        )
        if not self.expectation_is_coverage_eligible(expectation.id):
            state = "unmonitored"
        elif (
            expectation.calendar_rule
            and expectation.calendar_rule.rule_type == "one_off"
            and expectation.calendar_rule.occurrence_status == "cancelled"
        ):
            state = "cancelled"
        elif fulfilled:
            document_date = date.fromisoformat(fulfilled["document_date"])
            state = "fulfilled_late" if document_date > due_end else "fulfilled"
        elif (
            expectation.calendar_rule
            and expectation.calendar_rule.rule_type == "one_off"
            and expectation.calendar_rule.occurrence_status == "fulfilled"
        ):
            state = "fulfilled"
        elif as_of > effective_due_end:
            state = "missing"
        elif as_of >= due_start:
            state = "due_soon"
        else:
            state = "upcoming"
        return self._upsert_occurrence(
            expectation,
            source.current_name,
            row["period_key"],
            due_start,
            due_end,
            state,
            fulfilled,
            grace_extended_until,
        )

    def _grace_extended_until(self, occurrence_id: str) -> date | None:
        row = (
            self.connect()
            .execute(
                """SELECT grace_extended_until FROM coverage_occurrence_overrides
               WHERE occurrence_id = ?""",
                (occurrence_id,),
            )
            .fetchone()
        )
        return date.fromisoformat(row["grace_extended_until"]) if row else None

    def _occurrence_id(self, expectation_id: str, period_key: str) -> str:
        return uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{self.deployment_id}:{expectation_id}:{period_key}",
        ).hex

    def _coverage_occurrence(
        self, row: sqlite3.Row, expectation: DocumentExpectation
    ) -> CoverageOccurrence:
        source = self.get_source(expectation.source_correspondent_id)
        assert source is not None
        return CoverageOccurrence(
            id=row["id"],
            expectation_id=expectation.id,
            source_correspondent_id=expectation.source_correspondent_id,
            source_name=source.current_name,
            expectation_name=expectation.name,
            kind=expectation.kind,
            period_key=row["period_key"],
            due_start=row["due_start"],
            due_end=row["due_end"],
            importance=expectation.importance,
            grace_period_days=expectation.grace_period_days,
            grace_extended_until=row["grace_extended_until"],
            state=row["state"],
            fulfilled_document_id=row["fulfilled_document_id"],
            fulfilled_at=row["fulfilled_at"],
            evaluated_at=row["evaluated_at"],
        )

    def _upsert_occurrence(
        self,
        expectation: DocumentExpectation,
        source_name: str,
        period_key: str,
        due_start: date,
        due_end: date,
        state: str,
        fulfilled: sqlite3.Row | dict[str, Any] | None,
        grace_extended_until: date | None = None,
    ) -> CoverageOccurrence:
        conn = self.connect()
        occurrence_id = self._occurrence_id(expectation.id, period_key)
        evaluated_at = _now()
        fulfilled_document_id = fulfilled["document_id"] if fulfilled else None
        fulfilled_at = fulfilled["document_date"] if fulfilled else None
        with conn:
            conn.execute(
                """INSERT INTO coverage_occurrences (
                       id, deployment_id, expectation_id, period_key, due_start, due_end,
                       state, fulfilled_document_id, fulfilled_at, evaluated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (deployment_id, expectation_id, period_key) DO UPDATE SET
                       due_start = excluded.due_start, due_end = excluded.due_end,
                       state = excluded.state,
                       fulfilled_document_id = excluded.fulfilled_document_id,
                       fulfilled_at = excluded.fulfilled_at,
                       evaluated_at = excluded.evaluated_at""",
                (
                    occurrence_id,
                    self.deployment_id,
                    expectation.id,
                    period_key,
                    due_start.isoformat(),
                    due_end.isoformat(),
                    state,
                    fulfilled_document_id,
                    fulfilled_at,
                    evaluated_at,
                ),
            )
        return CoverageOccurrence(
            id=occurrence_id,
            expectation_id=expectation.id,
            source_correspondent_id=expectation.source_correspondent_id,
            source_name=source_name,
            expectation_name=expectation.name,
            kind=expectation.kind,
            period_key=period_key,
            due_start=due_start,
            due_end=due_end,
            importance=expectation.importance,
            grace_period_days=expectation.grace_period_days,
            grace_extended_until=grace_extended_until,
            state=state,
            fulfilled_document_id=fulfilled_document_id,
            fulfilled_at=fulfilled_at,
            evaluated_at=evaluated_at,
        )
