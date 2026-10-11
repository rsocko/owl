"""OWL-owned idempotency, recovery, and reconciliation for Monarch delivery."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Protocol

import httpx

from . import database
from .models import (
    DeliveryState,
    GatewayResult,
    GatewayState,
    MonarchDeliveryResult,
    SourceDocumentReadError,
)

ByteStreamFactory = Callable[
    [], AbstractAsyncContextManager[tuple[AsyncIterator[bytes], str]]
]
AcknowledgementHook = Callable[[], Awaitable[None]]


class DeliveryGateway(Protocol):
    async def submit(
        self,
        *,
        idempotency_key: str,
        canonical_document_ref: str,
        source_ref: str,
        expected_revision: int | None,
        content: AsyncIterator[bytes],
        media_type: str,
    ) -> GatewayResult: ...

    async def reconcile(
        self,
        *,
        idempotency_key: str,
        expected_revision: int | None,
    ) -> GatewayResult: ...


class DeliveryOutcomeUnknownError(RuntimeError):
    """A submission may have reached Tyrion and must be reconciled."""


class MonarchDeliveryService:
    def __init__(
        self,
        gateway: DeliveryGateway,
        *,
        deployment_id: str,
        identity_secret: str,
        write_enabled: bool,
    ) -> None:
        if len(identity_secret) < 16:
            raise ValueError("monarch_delivery_identity_secret_too_short")
        self._gateway = gateway
        self._deployment_id = deployment_id
        self._identity_secret = identity_secret.encode()
        self._write_enabled = write_enabled

    def status(self, document_id: int) -> MonarchDeliveryResult:
        row = self._get_by_document(document_id)
        if row is None:
            return MonarchDeliveryResult(
                delivery_ref=self._delivery_ref(document_id),
                state=DeliveryState.NOT_SUBMITTED,
                attempt_count=0,
                can_send=self._write_enabled,
                can_reconcile=False,
                can_retry=False,
            )
        return self._result(row)

    async def send(
        self,
        document_id: int,
        *,
        content_factory: ByteStreamFactory,
        recovery_origin: bool,
        on_acknowledged: AcknowledgementHook | None = None,
    ) -> MonarchDeliveryResult:
        if not self._write_enabled:
            raise PermissionError("monarch_delivery_write_disabled")
        row = self._ensure_delivery(document_id, recovery_origin=recovery_origin)
        state = DeliveryState(row["state"])
        if state is DeliveryState.BLOCKED_RECOVERY:
            return self._result(row)
        if state in {DeliveryState.ACKNOWLEDGED, DeliveryState.PROCESSING}:
            return await self._sync_display_tag(row, on_acknowledged)
        if state is DeliveryState.UNKNOWN:
            return await self.reconcile(document_id, on_acknowledged=on_acknowledged)
        if state is not DeliveryState.NOT_SUBMITTED:
            return self._result(row)
        return await self._submit(row, content_factory, on_acknowledged)

    async def reconcile(
        self,
        document_id: int,
        *,
        on_acknowledged: AcknowledgementHook | None = None,
    ) -> MonarchDeliveryResult:
        row = self._get_by_document(document_id)
        if row is None:
            return self.status(document_id)
        state = DeliveryState(row["state"])
        if state in {
            DeliveryState.NOT_SUBMITTED,
            DeliveryState.BLOCKED_RECOVERY,
            DeliveryState.FAILED,
        }:
            return self._result(row)
        try:
            gateway_result = await self._gateway.reconcile(
                idempotency_key=row["idempotency_key"],
                expected_revision=self._expected_revision(row),
            )
        except (httpx.HTTPError, ValueError):
            return self._transition(row, DeliveryState.UNKNOWN, "reconcile_outcome_unknown")
        updated = self._apply_gateway_result(row, gateway_result, reconciliation=True)
        return await self._sync_display_tag(updated, on_acknowledged)

    async def retry(
        self,
        document_id: int,
        *,
        content_factory: ByteStreamFactory,
        on_acknowledged: AcknowledgementHook | None = None,
    ) -> MonarchDeliveryResult:
        if not self._write_enabled:
            raise PermissionError("monarch_delivery_write_disabled")
        row = self._get_by_document(document_id)
        if row is None:
            return await self.send(
                document_id,
                content_factory=content_factory,
                recovery_origin=False,
                on_acknowledged=on_acknowledged,
            )
        if DeliveryState(row["state"]) not in {
            DeliveryState.UNKNOWN,
            DeliveryState.RETRYABLE,
        }:
            return self._result(row)

        try:
            reconciled = await self._gateway.reconcile(
                idempotency_key=row["idempotency_key"],
                expected_revision=self._expected_revision(row),
            )
        except (httpx.HTTPError, ValueError):
            return self._result(
                self._transition(row, DeliveryState.UNKNOWN, "reconcile_outcome_unknown")
            )
        if reconciled.state is not GatewayState.NOT_FOUND:
            updated = self._apply_gateway_result(row, reconciled, reconciliation=True)
            return await self._sync_display_tag(updated, on_acknowledged)
        return await self._submit(
            self._clear_revision(row),
            content_factory,
            on_acknowledged,
        )

    async def _submit(
        self,
        row: sqlite3.Row,
        content_factory: ByteStreamFactory,
        on_acknowledged: AcknowledgementHook | None,
    ) -> MonarchDeliveryResult:
        claimed = self._claim(row)
        if claimed is None:
            current = self._get_by_digest(row["document_ref_digest"])
            if current is None:
                raise RuntimeError("monarch_delivery_claim_lost")
            return self._result(current)
        try:
            async with content_factory() as (content, media_type):
                try:
                    gateway_result = await self._gateway.submit(
                        idempotency_key=claimed["idempotency_key"],
                        canonical_document_ref=claimed["document_ref_digest"],
                        source_ref=claimed["source_ref"],
                        expected_revision=claimed["tyrion_revision"],
                        content=content,
                        media_type=media_type,
                    )
                except SourceDocumentReadError:
                    updated = self._transition(
                        claimed,
                        DeliveryState.RETRYABLE,
                        "paperless_download_retryable",
                    )
                    return self._result(updated)
                except (httpx.TimeoutException, httpx.NetworkError):
                    updated = self._transition(
                        claimed, DeliveryState.UNKNOWN, "submit_outcome_unknown"
                    )
                    return self._result(updated)
                except Exception as exc:
                    self._transition(
                        claimed, DeliveryState.UNKNOWN, "submit_outcome_unknown"
                    )
                    raise DeliveryOutcomeUnknownError(
                        "monarch_delivery_outcome_unknown"
                    ) from exc
        except httpx.HTTPError:
            updated = self._transition(
                claimed,
                DeliveryState.RETRYABLE,
                "paperless_download_retryable",
            )
            return self._result(updated)
        updated = self._apply_gateway_result(claimed, gateway_result, reconciliation=False)
        return await self._sync_display_tag(updated, on_acknowledged)

    async def _sync_display_tag(
        self,
        row: sqlite3.Row,
        hook: AcknowledgementHook | None,
    ) -> MonarchDeliveryResult:
        if not bool(row["acknowledged"]) or hook is None:
            return self._result(row)
        try:
            await hook()
        except Exception:
            updated = self._set_display_tag_pending(row, True)
            return self._result(updated)
        updated = self._set_display_tag_pending(row, False)
        return self._result(updated)

    def _ensure_delivery(self, document_id: int, *, recovery_origin: bool) -> sqlite3.Row:
        now = _now()
        digest = self._document_digest(document_id)
        delivery_ref = self._delivery_ref(document_id)
        state = (
            DeliveryState.BLOCKED_RECOVERY
            if recovery_origin
            else DeliveryState.NOT_SUBMITTED
        )
        reason_code = "monarch_recovery_loop_prevented" if recovery_origin else None
        with database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO monarch_document_deliveries (
                    delivery_ref, document_ref_digest, source_ref, idempotency_key, state,
                    reason_code, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    delivery_ref,
                    digest,
                    self._source_ref(document_id),
                    self._idempotency_key(document_id),
                    state.value,
                    reason_code,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM monarch_document_deliveries WHERE document_ref_digest = ?",
                (digest,),
            ).fetchone()
            if (
                row is not None
                and recovery_origin
                and DeliveryState(row["state"]) is not DeliveryState.ACKNOWLEDGED
            ):
                connection.execute(
                    """
                    UPDATE monarch_document_deliveries
                    SET state = ?, reason_code = ?, updated_at = ?
                    WHERE delivery_ref = ?
                    """,
                    (
                        DeliveryState.BLOCKED_RECOVERY.value,
                        "monarch_recovery_loop_prevented",
                        now,
                        row["delivery_ref"],
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM monarch_document_deliveries WHERE delivery_ref = ?",
                    (row["delivery_ref"],),
                ).fetchone()
        if row is None:
            raise RuntimeError("monarch_delivery_not_created")
        return row

    def _claim(self, row: sqlite3.Row) -> sqlite3.Row | None:
        now = _now()
        with database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE monarch_document_deliveries
                SET state = ?, reason_code = NULL, attempt_count = attempt_count + 1,
                    updated_at = ?
                WHERE delivery_ref = ? AND state IN (?, ?, ?)
                """,
                (
                    DeliveryState.SUBMITTING.value,
                    now,
                    row["delivery_ref"],
                    DeliveryState.NOT_SUBMITTED.value,
                    DeliveryState.RETRYABLE.value,
                    DeliveryState.UNKNOWN.value,
                ),
            )
            if cursor.rowcount != 1:
                return None
            return connection.execute(
                "SELECT * FROM monarch_document_deliveries WHERE delivery_ref = ?",
                (row["delivery_ref"],),
            ).fetchone()

    def _apply_gateway_result(
        self,
        row: sqlite3.Row,
        result: GatewayResult,
        *,
        reconciliation: bool,
    ) -> sqlite3.Row:
        state_map = {
            GatewayState.PROCESSING: DeliveryState.PROCESSING,
            GatewayState.UNKNOWN: DeliveryState.UNKNOWN,
            GatewayState.ACKNOWLEDGED: DeliveryState.ACKNOWLEDGED,
            GatewayState.RETRYABLE: DeliveryState.RETRYABLE,
            GatewayState.REJECTED: DeliveryState.FAILED,
            GatewayState.NOT_FOUND: (
                DeliveryState.RETRYABLE if reconciliation else DeliveryState.UNKNOWN
            ),
        }
        reason_code = result.reason_code
        if result.state is GatewayState.NOT_FOUND:
            reason_code = (
                "reconcile_not_found" if reconciliation else "submit_response_not_found"
            )
        acknowledgement_digest = (
            hashlib.sha256(result.acknowledgement_ref.encode()).hexdigest()
            if result.acknowledgement_ref
            else None
        )
        return self._transition(
            row,
            state_map[result.state],
            reason_code,
            acknowledgement_digest=acknowledgement_digest,
            tyrion_revision=result.revision,
            acknowledged=result.acknowledged,
        )

    def _transition(
        self,
        row: sqlite3.Row,
        state: DeliveryState,
        reason_code: str | None,
        *,
        acknowledgement_digest: str | None = None,
        tyrion_revision: int | None = None,
        acknowledged: bool | None = None,
    ) -> sqlite3.Row:
        now = _now()
        event_ref = uuid.uuid4().hex
        with database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE monarch_document_deliveries
                SET state = ?, reason_code = ?, acknowledgement_ref_digest = COALESCE(?, acknowledgement_ref_digest),
                    tyrion_revision = COALESCE(?, tyrion_revision),
                    acknowledged = COALESCE(?, acknowledged),
                    updated_at = ?
                WHERE delivery_ref = ?
                """,
                (
                    state.value,
                    reason_code,
                    acknowledgement_digest,
                    tyrion_revision,
                    int(acknowledged) if acknowledged is not None else None,
                    now,
                    row["delivery_ref"],
                ),
            )
            connection.execute(
                """
                INSERT INTO monarch_delivery_events (
                    event_ref, delivery_ref, event_type, reason_code, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (event_ref, row["delivery_ref"], state.value, reason_code, now),
            )
            updated = connection.execute(
                "SELECT * FROM monarch_document_deliveries WHERE delivery_ref = ?",
                (row["delivery_ref"],),
            ).fetchone()
        if updated is None:
            raise RuntimeError("monarch_delivery_transition_lost")
        return updated

    def _set_display_tag_pending(self, row: sqlite3.Row, pending: bool) -> sqlite3.Row:
        with database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE monarch_document_deliveries
                SET display_tag_pending = ?, updated_at = ?
                WHERE delivery_ref = ?
                """,
                (int(pending), _now(), row["delivery_ref"]),
            )
            updated = connection.execute(
                "SELECT * FROM monarch_document_deliveries WHERE delivery_ref = ?",
                (row["delivery_ref"],),
            ).fetchone()
        if updated is None:
            raise RuntimeError("monarch_delivery_tag_state_lost")
        return updated

    def _clear_revision(self, row: sqlite3.Row) -> sqlite3.Row:
        with database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE monarch_document_deliveries
                SET tyrion_revision = NULL, updated_at = ?
                WHERE delivery_ref = ?
                """,
                (_now(), row["delivery_ref"]),
            )
            updated = connection.execute(
                "SELECT * FROM monarch_document_deliveries WHERE delivery_ref = ?",
                (row["delivery_ref"],),
            ).fetchone()
        if updated is None:
            raise RuntimeError("monarch_delivery_revision_clear_lost")
        return updated

    def _get_by_document(self, document_id: int) -> sqlite3.Row | None:
        return self._get_by_digest(self._document_digest(document_id))

    @staticmethod
    def _get_by_digest(document_digest: str) -> sqlite3.Row | None:
        database.init_db()
        with database.transaction() as connection:
            return connection.execute(
                "SELECT * FROM monarch_document_deliveries WHERE document_ref_digest = ?",
                (document_digest,),
            ).fetchone()

    def _document_digest(self, document_id: int) -> str:
        return self._opaque_identity(f"paperless:{document_id}")

    def _idempotency_key(self, document_id: int) -> str:
        return self._opaque_identity(f"monarch-delivery:v1:{document_id}")

    def _source_ref(self, document_id: int) -> str:
        return self._opaque_identity(f"user-send:v1:{document_id}")

    def _delivery_ref(self, document_id: int) -> str:
        return self._opaque_identity(f"delivery-ref:v1:{document_id}")[:32]

    def _opaque_identity(self, value: str) -> str:
        return hmac.new(
            self._identity_secret,
            f"{self._deployment_id}:{value}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def _result(self, row: sqlite3.Row) -> MonarchDeliveryResult:
        state = DeliveryState(row["state"])
        return MonarchDeliveryResult(
            delivery_ref=row["delivery_ref"],
            state=state,
            attempt_count=row["attempt_count"],
            reason_code=row["reason_code"],
            can_send=self._write_enabled and state is DeliveryState.NOT_SUBMITTED,
            can_reconcile=state in {
                DeliveryState.UNKNOWN,
                DeliveryState.PROCESSING,
                DeliveryState.RETRYABLE,
            },
            can_retry=self._write_enabled
            and state in {DeliveryState.UNKNOWN, DeliveryState.RETRYABLE},
            display_tag_pending=bool(row["display_tag_pending"]),
            acknowledged=bool(row["acknowledged"]),
        )

    @staticmethod
    def _expected_revision(row: sqlite3.Row) -> int | None:
        revision = row["tyrion_revision"]
        if revision is None:
            return None
        return int(revision)


def _now() -> str:
    return datetime.now(UTC).isoformat()
