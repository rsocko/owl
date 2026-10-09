from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from typing import Literal
from urllib.parse import quote

import httpx
from pydantic import ConfigDict, Field, model_validator

from doc_intelligence_hub.modules.statements.correspondent_models import PolicyModel
from doc_intelligence_hub.modules.statements.database import Database

PayeeReviewStatus = Literal["unreviewed", "reviewed", "inactive"]
DocumentProductionDecision = Literal["unknown", "documents_expected", "no_documents_expected"]


class PayeeObservationWindow(PolicyModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_by_alias=True,
        validate_by_name=False,
    )
    first_observed_on: date = Field(
        validation_alias="firstObservedOn", serialization_alias="firstObservedOn"
    )
    last_observed_on: date = Field(
        validation_alias="lastObservedOn", serialization_alias="lastObservedOn"
    )


class PayeeIntervalEvidence(PolicyModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_by_alias=True,
        validate_by_name=False,
    )
    sample_count: int = Field(
        ge=1, validation_alias="sampleCount", serialization_alias="sampleCount"
    )
    median_days: float = Field(
        ge=0, validation_alias="medianDays", serialization_alias="medianDays"
    )
    minimum_days: float = Field(
        ge=0, validation_alias="minimumDays", serialization_alias="minimumDays"
    )
    maximum_days: float = Field(
        ge=0, validation_alias="maximumDays", serialization_alias="maximumDays"
    )


class PayeePatternProvenance(PolicyModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_by_alias=True,
        validate_by_name=False,
    )
    transaction_history: Literal[True] = Field(
        validation_alias="transactionHistory", serialization_alias="transactionHistory"
    )
    monarch_recurring: bool = Field(
        validation_alias="monarchRecurring", serialization_alias="monarchRecurring"
    )


class MonarchConfirmedRecurring(PolicyModel):
    active: bool
    cadence: str = Field(min_length=1, max_length=100)


class PayeePatternV1(PolicyModel):
    """Exact privacy-bounded Tyrion PayeePatternProjectionV1 item."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_by_alias=True,
        validate_by_name=False,
    )

    payee_ref: str = Field(
        min_length=1,
        max_length=200,
        validation_alias="payeeRef",
        serialization_alias="payeeRef",
    )
    display_name: str = Field(
        min_length=1,
        max_length=200,
        validation_alias="displayName",
        serialization_alias="displayName",
    )
    activity: Literal["active", "inactive", "unknown"]
    classification: Literal[
        "recurring-fixed",
        "recurring-variable",
        "regular",
        "infrequent",
        "single-observation",
        "unknown",
    ]
    observation_count: int = Field(
        ge=1,
        validation_alias="observationCount",
        serialization_alias="observationCount",
    )
    observation_window: PayeeObservationWindow = Field(
        validation_alias="observationWindow",
        serialization_alias="observationWindow",
    )
    interval_evidence: PayeeIntervalEvidence | None = Field(
        validation_alias="intervalEvidence",
        serialization_alias="intervalEvidence",
    )
    confidence: float = Field(ge=0, le=1)
    basis: list[str] = Field(min_length=1, max_length=20)
    provenance: PayeePatternProvenance
    monarch_confirmed_recurring: MonarchConfirmedRecurring | None = Field(
        validation_alias="monarchConfirmedRecurring",
        serialization_alias="monarchConfirmedRecurring",
    )

    @model_validator(mode="after")
    def normalize_basis(self) -> PayeePatternV1:
        self.basis = sorted(set(self.basis))
        if any(
            not value or len(value) > 64 or not value.replace("_", "").isalnum()
            for value in self.basis
        ):
            raise ValueError("basis must contain short identifier values")
        return self


class PayeePatternProjectionV1(PolicyModel):
    """Exact Tyrion read-only contract; deliberately excludes raw transactions."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_by_alias=True,
        validate_by_name=False,
    )

    contract_version: Literal["1"] = Field(
        validation_alias="contractVersion",
        serialization_alias="contractVersion",
    )
    connector_ref: str = Field(
        min_length=1,
        max_length=200,
        validation_alias="connectorRef",
        serialization_alias="connectorRef",
    )
    source_generation: str = Field(
        min_length=1,
        max_length=200,
        validation_alias="sourceGeneration",
        serialization_alias="sourceGeneration",
    )
    source_as_of: datetime = Field(
        validation_alias="sourceAsOf",
        serialization_alias="sourceAsOf",
    )
    completeness: Literal["complete", "partial"]
    payees: list[PayeePatternV1] = Field(max_length=6000)

    @model_validator(mode="after")
    def validate_unique_payees(self) -> PayeePatternProjectionV1:
        refs = [payee.payee_ref for payee in self.payees]
        if len(refs) != len(set(refs)):
            raise ValueError("candidates must contain unique payeeRef values")
        return self


class PayeeCorrespondentMapping(PolicyModel):
    account_candidate_id: str | None = Field(default=None, max_length=200)
    correspondent_id: int = Field(gt=0)


class PayeeReviewAction(PolicyModel):
    document_decision: Literal["documents_expected", "no_documents_expected"]
    mappings: list[PayeeCorrespondentMapping] = Field(default_factory=list, max_length=100)
    expectation_ids: list[str] = Field(default_factory=list, max_length=100)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_decision(self) -> PayeeReviewAction:
        mapping_keys = [
            (item.account_candidate_id, item.correspondent_id) for item in self.mappings
        ]
        if len(mapping_keys) != len(set(mapping_keys)):
            raise ValueError("mappings must be unique")
        self.expectation_ids = list(dict.fromkeys(self.expectation_ids))
        if self.document_decision == "no_documents_expected" and self.expectation_ids:
            raise ValueError("no_documents_expected cannot link document expectations")
        return self


class PayeeReviewHistoryEvent(PolicyModel):
    id: str
    candidate_id: str
    action: Literal["reviewed"]
    previous_state: dict
    new_state: dict
    created_at: str


class PayeeReviewQueueItem(PolicyModel):
    id: str
    active: bool
    display_hint: str
    classification: str
    observation_count: int
    observation_window: PayeeObservationWindow
    interval_evidence: PayeeIntervalEvidence | None
    confidence: float = Field(ge=0, le=1)
    basis: list[str]
    provenance: PayeePatternProvenance
    monarch_confirmed_recurring: MonarchConfirmedRecurring | None
    source_as_of: str
    review_status: PayeeReviewStatus
    document_decision: DocumentProductionDecision
    mappings: list[PayeeCorrespondentMapping]
    expectation_ids: list[str]
    notes: str | None = None
    reviewed_at: str | None = None
    owl_deep_link: str
    source_actions: list[dict[str, str]]


class PayeeSnapshotResult(PolicyModel):
    source_generation: str
    idempotent: bool
    active_candidates: int = Field(ge=0)
    deactivated_candidates: int = Field(ge=0)


class PayeePatternSyncRequest(PolicyModel):
    source_generation: str = Field(min_length=1, max_length=200)


class TyrionPayeePatternsClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_token: str | None = None,
        verify_ssl: bool = True,
        timeout_seconds: int = 30,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            verify=verify_ssl,
            timeout=timeout_seconds,
        )

    async def fetch(
        self, source_generation: str, connector_ref: str
    ) -> PayeePatternProjectionV1:
        generation = quote(source_generation, safe="")
        response = await self._client.get(
            f"/api/connector/v1/payee-patterns/{generation}",
            params={"connectorRef": connector_ref},
        )
        response.raise_for_status()
        projection = PayeePatternProjectionV1.model_validate(response.json())
        if (
            projection.source_generation != source_generation
            or projection.connector_ref != connector_ref
        ):
            raise ValueError("Tyrion payee projection identity did not match the request")
        return projection

    async def close(self) -> None:
        await self._client.aclose()


class PayeeReviewService:
    def __init__(self, database: Database, deployment_id: str) -> None:
        self.database = database
        self.deployment_id = deployment_id

    def replace_snapshot(self, snapshot: PayeePatternProjectionV1) -> PayeeSnapshotResult:
        conn = self.database.connect()
        conn.execute("BEGIN IMMEDIATE")
        processed = conn.execute(
            """SELECT 1 FROM tyrion_payee_generations
               WHERE deployment_id = ? AND connector_ref = ? AND source_generation = ?""",
            (self.deployment_id, snapshot.connector_ref, snapshot.source_generation),
        ).fetchone()
        if processed:
            active = conn.execute(
                """SELECT COUNT(*) FROM tyrion_payee_candidates
                   WHERE deployment_id = ? AND connector_ref = ? AND active = 1""",
                (self.deployment_id, snapshot.connector_ref),
            ).fetchone()[0]
            conn.commit()
            return PayeeSnapshotResult(
                source_generation=snapshot.source_generation,
                idempotent=True,
                active_candidates=active,
                deactivated_candidates=0,
            )

        current_source = conn.execute(
            """SELECT source_as_of FROM tyrion_payee_sources
               WHERE deployment_id = ? AND connector_ref = ?""",
            (self.deployment_id, snapshot.connector_ref),
        ).fetchone()
        if current_source and snapshot.source_as_of < datetime.fromisoformat(
            current_source["source_as_of"]
        ):
            with conn:
                conn.execute(
                    """INSERT INTO tyrion_payee_generations (
                           deployment_id, connector_ref, source_generation
                       ) VALUES (?, ?, ?)""",
                    (
                        self.deployment_id,
                        snapshot.connector_ref,
                        snapshot.source_generation,
                    ),
                )
            active = conn.execute(
                """SELECT COUNT(*) FROM tyrion_payee_candidates
                   WHERE deployment_id = ? AND connector_ref = ? AND active = 1""",
                (self.deployment_id, snapshot.connector_ref),
            ).fetchone()[0]
            return PayeeSnapshotResult(
                source_generation=snapshot.source_generation,
                idempotent=True,
                active_candidates=active,
                deactivated_candidates=0,
            )

        existing_active = {
            row["payee_ref"]
            for row in conn.execute(
                """SELECT payee_ref FROM tyrion_payee_candidates
                   WHERE deployment_id = ? AND connector_ref = ? AND active = 1""",
                (self.deployment_id, snapshot.connector_ref),
            ).fetchall()
        }
        incoming_refs = {payee.payee_ref for payee in snapshot.payees}
        deactivated = existing_active & {
            payee.payee_ref for payee in snapshot.payees if payee.activity == "inactive"
        }
        if snapshot.completeness == "complete":
            deactivated.update(existing_active - incoming_refs)

        with conn:
            if deactivated:
                placeholders = ",".join("?" for _ in deactivated)
                conn.execute(
                    f"""UPDATE tyrion_payee_candidates
                        SET active = 0, source_generation = ?, source_as_of = ?,
                            updated_at = datetime('now')
                        WHERE deployment_id = ? AND connector_ref = ?
                          AND payee_ref IN ({placeholders})""",
                    (
                        snapshot.source_generation,
                        snapshot.source_as_of.isoformat(),
                        self.deployment_id,
                        snapshot.connector_ref,
                        *sorted(deactivated),
                    ),
                )
            for candidate in snapshot.payees:
                candidate_id = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    (
                        f"owl:{self.deployment_id}:tyrion-payee:"
                        f"{snapshot.connector_ref}:{candidate.payee_ref}"
                    ),
                ).hex
                conn.execute(
                    """INSERT INTO tyrion_payee_candidates (
                           id, deployment_id, connector_ref, payee_ref, source_generation,
                           source_as_of, active, display_hint, classification,
                           observation_count, observation_window_json,
                           interval_evidence_json, confidence, basis_json,
                           provenance_json, monarch_confirmed_recurring_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(deployment_id, connector_ref, payee_ref) DO UPDATE SET
                           source_generation = excluded.source_generation,
                           source_as_of = excluded.source_as_of,
                           active = excluded.active,
                           display_hint = excluded.display_hint,
                           classification = excluded.classification,
                           observation_count = excluded.observation_count,
                           observation_window_json = excluded.observation_window_json,
                           interval_evidence_json = excluded.interval_evidence_json,
                           confidence = excluded.confidence,
                           basis_json = excluded.basis_json,
                           provenance_json = excluded.provenance_json,
                           monarch_confirmed_recurring_json =
                               excluded.monarch_confirmed_recurring_json,
                           updated_at = datetime('now')""",
                    (
                        candidate_id,
                        self.deployment_id,
                        snapshot.connector_ref,
                        candidate.payee_ref,
                        snapshot.source_generation,
                        snapshot.source_as_of.isoformat(),
                        int(candidate.activity == "active"),
                        candidate.display_name,
                        candidate.classification,
                        candidate.observation_count,
                        candidate.observation_window.model_dump_json(by_alias=True),
                        (
                            candidate.interval_evidence.model_dump_json(by_alias=True)
                            if candidate.interval_evidence
                            else None
                        ),
                        candidate.confidence,
                        json.dumps(candidate.basis),
                        candidate.provenance.model_dump_json(by_alias=True),
                        (
                            candidate.monarch_confirmed_recurring.model_dump_json(by_alias=True)
                            if candidate.monarch_confirmed_recurring
                            else None
                        ),
                    ),
                )
            conn.execute(
                """INSERT INTO tyrion_payee_generations (
                       deployment_id, connector_ref, source_generation
                   ) VALUES (?, ?, ?)""",
                (self.deployment_id, snapshot.connector_ref, snapshot.source_generation),
            )
            conn.execute(
                """INSERT INTO tyrion_payee_sources (
                       deployment_id, connector_ref, source_generation, source_as_of
                   ) VALUES (?, ?, ?, ?)
                   ON CONFLICT(deployment_id, connector_ref) DO UPDATE SET
                       source_generation = excluded.source_generation,
                       source_as_of = excluded.source_as_of,
                       updated_at = datetime('now')""",
                (
                    self.deployment_id,
                    snapshot.connector_ref,
                    snapshot.source_generation,
                    snapshot.source_as_of.isoformat(),
                ),
            )

        active = conn.execute(
            """SELECT COUNT(*) FROM tyrion_payee_candidates
               WHERE deployment_id = ? AND connector_ref = ? AND active = 1""",
            (self.deployment_id, snapshot.connector_ref),
        ).fetchone()[0]
        return PayeeSnapshotResult(
            source_generation=snapshot.source_generation,
            idempotent=False,
            active_candidates=active,
            deactivated_candidates=len(deactivated),
        )

    def list_queue(
        self,
        *,
        status: Literal["all", "unreviewed", "reviewed", "inactive"] = "all",
        limit: int = 100,
        offset: int = 0,
    ) -> list[PayeeReviewQueueItem]:
        conn = self.database.connect()
        sql = "SELECT * FROM tyrion_payee_candidates WHERE deployment_id = ?"
        params: list[object] = [self.deployment_id]
        if status == "inactive":
            sql += " AND active = 0"
        elif status == "unreviewed":
            sql += " AND active = 1 AND document_decision = 'unknown'"
        elif status == "reviewed":
            sql += " AND active = 1 AND document_decision != 'unknown'"
        sql += " ORDER BY active DESC, document_decision, display_hint, id LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(sql, params).fetchall()
        return [self._row_to_item(conn, row) for row in rows]

    def review(self, candidate_id: str, action: PayeeReviewAction) -> PayeeReviewQueueItem:
        conn = self.database.connect()
        conn.execute("BEGIN IMMEDIATE")
        candidate = conn.execute(
            """SELECT * FROM tyrion_payee_candidates
               WHERE deployment_id = ? AND id = ?""",
            (self.deployment_id, candidate_id),
        ).fetchone()
        if candidate is None:
            conn.rollback()
            raise KeyError("payee_candidate_not_found")
        if any(
            mapping.account_candidate_id is not None
            and conn.execute(
                """SELECT 1 FROM external_document_candidates
                   WHERE deployment_id = ? AND id = ?
                     AND kind = 'accountStatementCandidate'""",
                (self.deployment_id, mapping.account_candidate_id),
            ).fetchone()
            is None
            for mapping in action.mappings
        ):
            conn.rollback()
            raise ValueError("account_candidate_id must reference an OWL V1 account candidate")
        correspondent_ids = {mapping.correspondent_id for mapping in action.mappings}
        if correspondent_ids:
            placeholders = ",".join("?" for _ in correspondent_ids)
            found = {
                row["correspondent_id"]
                for row in conn.execute(
                    f"""SELECT correspondent_id FROM correspondent_profiles
                        WHERE deployment_id = ? AND correspondent_id IN ({placeholders})""",
                    (self.deployment_id, *sorted(correspondent_ids)),
                ).fetchall()
            }
            if found != correspondent_ids:
                conn.rollback()
                raise ValueError("mapping correspondent_id is not a current OWL profile")
        if action.expectation_ids:
            placeholders = ",".join("?" for _ in action.expectation_ids)
            found_expectations = {
                row["id"]
                for row in conn.execute(
                    f"""SELECT id FROM document_expectations
                        WHERE deployment_id = ? AND id IN ({placeholders})""",
                    (self.deployment_id, *action.expectation_ids),
                ).fetchall()
            }
            if found_expectations != set(action.expectation_ids):
                conn.rollback()
                raise ValueError("expectation_ids must reference current OWL expectations")

        previous = self._state_dict(conn, candidate)
        now = datetime.now(UTC).isoformat(timespec="microseconds")
        with conn:
            conn.execute(
                """UPDATE tyrion_payee_candidates
                   SET document_decision = ?, notes = ?, reviewed_at = ?,
                       updated_at = datetime('now')
                   WHERE deployment_id = ? AND id = ?""",
                (
                    action.document_decision,
                    action.notes,
                    now,
                    self.deployment_id,
                    candidate_id,
                ),
            )
            conn.execute(
                """DELETE FROM tyrion_payee_mappings
                   WHERE deployment_id = ? AND candidate_id = ?""",
                (self.deployment_id, candidate_id),
            )
            conn.executemany(
                """INSERT INTO tyrion_payee_mappings (
                       deployment_id, candidate_id, account_candidate_id, correspondent_id
                   ) VALUES (?, ?, ?, ?)""",
                [
                    (
                        self.deployment_id,
                        candidate_id,
                        mapping.account_candidate_id,
                        mapping.correspondent_id,
                    )
                    for mapping in action.mappings
                ],
            )
            conn.execute(
                """DELETE FROM tyrion_payee_expectations
                   WHERE deployment_id = ? AND candidate_id = ?""",
                (self.deployment_id, candidate_id),
            )
            conn.executemany(
                """INSERT INTO tyrion_payee_expectations (
                       deployment_id, candidate_id, expectation_id, sort_order
                   ) VALUES (?, ?, ?, ?)""",
                [
                    (self.deployment_id, candidate_id, expectation_id, index)
                    for index, expectation_id in enumerate(action.expectation_ids)
                ],
            )
            updated = conn.execute(
                """SELECT * FROM tyrion_payee_candidates
                   WHERE deployment_id = ? AND id = ?""",
                (self.deployment_id, candidate_id),
            ).fetchone()
            new_state = self._state_dict(conn, updated)
            conn.execute(
                """INSERT INTO tyrion_payee_review_events (
                       id, deployment_id, candidate_id, action,
                       previous_state_json, new_state_json, created_at
                   ) VALUES (?, ?, ?, 'reviewed', ?, ?, ?)""",
                (
                    uuid.uuid4().hex,
                    self.deployment_id,
                    candidate_id,
                    json.dumps(previous, sort_keys=True),
                    json.dumps(new_state, sort_keys=True),
                    now,
                ),
            )
        return self._row_to_item(conn, updated)

    def list_history(self, candidate_id: str) -> list[PayeeReviewHistoryEvent]:
        rows = self.database.connect().execute(
            """SELECT * FROM tyrion_payee_review_events
               WHERE deployment_id = ? AND candidate_id = ?
               ORDER BY created_at, id""",
            (self.deployment_id, candidate_id),
        ).fetchall()
        return [
            PayeeReviewHistoryEvent(
                id=row["id"],
                candidate_id=row["candidate_id"],
                action=row["action"],
                previous_state=json.loads(row["previous_state_json"]),
                new_state=json.loads(row["new_state_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def close(self) -> None:
        self.database.close()

    def _state_dict(self, conn, row) -> dict:
        mappings = [
            {
                "account_candidate_id": item["account_candidate_id"],
                "correspondent_id": item["correspondent_id"],
            }
            for item in conn.execute(
                """SELECT account_candidate_id, correspondent_id FROM tyrion_payee_mappings
                   WHERE deployment_id = ? AND candidate_id = ?
                   ORDER BY account_candidate_id, correspondent_id""",
                (self.deployment_id, row["id"]),
            ).fetchall()
        ]
        expectation_ids = [
            item["expectation_id"]
            for item in conn.execute(
                """SELECT expectation_id FROM tyrion_payee_expectations
                   WHERE deployment_id = ? AND candidate_id = ?
                   ORDER BY sort_order, expectation_id""",
                (self.deployment_id, row["id"]),
            ).fetchall()
        ]
        return {
            "document_decision": row["document_decision"],
            "mappings": mappings,
            "expectation_ids": expectation_ids,
            "notes": row["notes"],
        }

    def _row_to_item(self, conn, row) -> PayeeReviewQueueItem:
        state = self._state_dict(conn, row)
        review_status: PayeeReviewStatus = (
            "inactive"
            if not row["active"]
            else "unreviewed"
            if row["document_decision"] == "unknown"
            else "reviewed"
        )
        return PayeeReviewQueueItem(
            id=row["id"],
            active=bool(row["active"]),
            display_hint=row["display_hint"],
            classification=row["classification"],
            observation_count=row["observation_count"],
            observation_window=PayeeObservationWindow.model_validate_json(
                row["observation_window_json"]
            ),
            interval_evidence=(
                PayeeIntervalEvidence.model_validate_json(row["interval_evidence_json"])
                if row["interval_evidence_json"]
                else None
            ),
            confidence=row["confidence"],
            basis=json.loads(row["basis_json"]),
            provenance=PayeePatternProvenance.model_validate_json(row["provenance_json"]),
            monarch_confirmed_recurring=(
                MonarchConfirmedRecurring.model_validate_json(
                    row["monarch_confirmed_recurring_json"]
                )
                if row["monarch_confirmed_recurring_json"]
                else None
            ),
            source_as_of=row["source_as_of"],
            review_status=review_status,
            document_decision=row["document_decision"],
            mappings=[PayeeCorrespondentMapping.model_validate(item) for item in state["mappings"]],
            expectation_ids=state["expectation_ids"],
            notes=row["notes"],
            reviewed_at=row["reviewed_at"],
            owl_deep_link=f"#/correspondents?payeeCandidate={row['id']}",
            source_actions=[
                {
                    "id": "set_mapping",
                    "label": "Set correspondent mapping",
                    "method": "PUT",
                    "url": f"/api/mc/v1/payee-document-reviews/{row['id']}/mapping",
                },
                {
                    "id": "mark_no_documents_expected",
                    "label": "No documents expected",
                    "method": "POST",
                    "url": (
                        f"/api/mc/v1/payee-document-reviews/"
                        f"{row['id']}/no-documents-expected"
                    ),
                },
            ],
        )
