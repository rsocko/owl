"""Incremental Paperless discovery with restart-safe cursors and bounded backfill."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from dateutil.parser import isoparse
from sqlalchemy.orm import Session

from doc_intelligence_hub.core.datetime_utils import (
    normalize_utc_datetime,
    serialize_utc_datetime,
)

from .config import settings
from .database import DiscoveredDocumentState, DiscoveryCursor
from .fingerprint import document_source_fingerprint
from .models import InvalidationReason
from .service import AnalysisFreshnessService

AnalysisCallback = Callable[[int], Awaitable[dict[str, Any] | None]]
SessionFactory = Callable[[], Session]


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return normalize_utc_datetime(isoparse(value))


def _stream_state(now: datetime) -> dict[str, Any]:
    return {
        "high_water_at": serialize_utc_datetime(now),
        "high_water_id": 0,
        "scan_floor": None,
        "scan_cursor": None,
        "scan_max_at": None,
        "scan_max_id": 0,
    }


def _cursor_state(now: datetime) -> dict[str, Any]:
    return {
        "added": _stream_state(now),
        "modified": _stream_state(now),
    }


class IncrementalDiscoveryService:
    def __init__(
        self,
        session_factory: SessionFactory | None = None,
        freshness_service: AnalysisFreshnessService | None = None,
    ) -> None:
        from .database import get_session as default_session_factory

        self._session_factory = session_factory or default_session_factory
        self._freshness = freshness_service or AnalysisFreshnessService(session_factory)

    async def reconcile(
        self,
        *,
        client: Any,
        analyze: AnalysisCallback,
        cursor_name: str = "paperless-analysis",
        page_size: int | None = None,
        overlap_seconds: int | None = None,
        governed_custom_field_ids: set[int] | None = None,
        ignored_tag_ids: set[int] | None = None,
        dry_run: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        effective_now = normalize_utc_datetime(now or datetime.now(UTC))
        effective_page_size = page_size or settings.discovery_page_size
        effective_overlap = (
            settings.discovery_overlap_seconds
            if overlap_seconds is None
            else overlap_seconds
        )
        if effective_page_size < 1 or effective_page_size > 500:
            raise ValueError("page_size must be between 1 and 500")
        if effective_overlap < 0 or effective_overlap > 86400:
            raise ValueError("overlap_seconds must be between 0 and 86400")

        cursor = self._get_cursor(cursor_name)
        if cursor is None:
            if not dry_run:
                self._create_cursor(cursor_name, effective_now)
            return {
                "cursor": cursor_name,
                "bootstrapped": True,
                "processed": 0,
                "unchanged": 0,
                "failed": 0,
                "pending": 0,
                "documents": [],
                "webhook_reconciliation": "blocked_on_issue_81",
            }

        result = {
            "cursor": cursor_name,
            "bootstrapped": False,
            "processed": 0,
            "unchanged": 0,
            "failed": 0,
            "documents": [],
            "webhook_reconciliation": "blocked_on_issue_81",
        }
        selected_ids = governed_custom_field_ids or set()
        ignored_tags = ignored_tag_ids or set()

        pending_ids = self._pending_document_ids(limit=effective_page_size)
        for document_id in pending_ids:
            document = await client.get_document(document_id)
            await self._process_document(
                document=document,
                analyze=analyze,
                governed_custom_field_ids=selected_ids,
                ignored_tag_ids=ignored_tags,
                dry_run=dry_run,
                result=result,
                forced_reason="retry_pending",
            )

        for stream_name in ("added", "modified"):
            await self._scan_stream(
                client=client,
                analyze=analyze,
                cursor_name=cursor_name,
                stream_name=stream_name,
                page_size=effective_page_size,
                overlap_seconds=effective_overlap,
                governed_custom_field_ids=selected_ids,
                ignored_tag_ids=ignored_tags,
                dry_run=dry_run,
                result=result,
            )

        result["pending"] = self._pending_count()
        return result

    async def backfill(
        self,
        *,
        client: Any,
        analyze: AnalysisCallback,
        document_ids: list[int] | None = None,
        added_after: str | None = None,
        added_before: str | None = None,
        id_after: int | None = None,
        id_before: int | None = None,
        saved_view_id: int | None = None,
        limit: int | None = None,
        governed_custom_field_ids: set[int] | None = None,
        ignored_tag_ids: set[int] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        selectors = [
            bool(document_ids),
            bool(added_after or added_before),
            id_after is not None or id_before is not None,
            saved_view_id is not None,
        ]
        if sum(selectors) != 1:
            raise ValueError(
                "Select exactly one bounded backfill scope: document_ids, added date range, "
                "ID range, or saved_view_id"
            )
        if id_after is not None and id_before is not None and id_after > id_before:
            raise ValueError("id_after cannot be greater than id_before")
        effective_limit = min(limit or settings.max_backfill_batch, settings.max_backfill_batch)

        if document_ids:
            unique_ids = list(dict.fromkeys(document_ids))
            if len(unique_ids) > effective_limit:
                raise ValueError(f"Backfill exceeds the configured limit of {effective_limit}")
            documents = [await client.get_document(document_id) for document_id in unique_ids]
            scope = "document_ids"
        elif saved_view_id is not None:
            documents = await client.list_documents(
                saved_view=saved_view_id,
                limit=effective_limit,
            )
            scope = "saved_view"
        else:
            documents = await client.list_documents(
                added_after=added_after,
                added_before=added_before,
                id_after=id_after,
                id_before=id_before,
                ordering="id",
                limit=effective_limit,
            )
            scope = "added_range" if added_after or added_before else "id_range"

        result = {
            "scope": scope,
            "processed": 0,
            "unchanged": 0,
            "failed": 0,
            "documents": [],
        }
        for document in documents:
            await self._process_document(
                document=document,
                analyze=analyze,
                governed_custom_field_ids=governed_custom_field_ids or set(),
                ignored_tag_ids=ignored_tag_ids or set(),
                dry_run=dry_run,
                result=result,
                forced_reason="explicit_backfill",
            )
        result["pending"] = self._pending_count()
        return result

    async def _scan_stream(
        self,
        *,
        client: Any,
        analyze: AnalysisCallback,
        cursor_name: str,
        stream_name: str,
        page_size: int,
        overlap_seconds: int,
        governed_custom_field_ids: set[int],
        ignored_tag_ids: set[int],
        dry_run: bool,
        result: dict[str, Any],
    ) -> None:
        cursor = self._get_cursor(cursor_name)
        if cursor is None:
            raise RuntimeError(f"Discovery cursor {cursor_name!r} disappeared")
        state = dict(cursor.state)
        stream = dict(state[stream_name])
        high_water = _parse_timestamp(stream["high_water_at"])
        if high_water is None:
            raise RuntimeError(f"Discovery cursor {cursor_name!r} has invalid state")

        if stream.get("scan_floor") is None:
            stream["scan_floor"] = serialize_utc_datetime(
                high_water - timedelta(seconds=overlap_seconds)
            )
            stream["scan_max_at"] = stream["high_water_at"]
            stream["scan_max_id"] = stream["high_water_id"]

        page = await client.list_document_page(
            page_size=page_size,
            cursor=stream.get("scan_cursor"),
            scope_params={f"{stream_name}__gte": stream["scan_floor"]},
            ordering=f"{stream_name},id",
        )

        for document in page.results:
            await self._process_document(
                document=document,
                analyze=analyze,
                governed_custom_field_ids=governed_custom_field_ids,
                ignored_tag_ids=ignored_tag_ids,
                dry_run=dry_run,
                result=result,
            )
            timestamp = _parse_timestamp(document.get(stream_name))
            document_id = int(document["id"])
            current_max = _parse_timestamp(stream.get("scan_max_at"))
            current_tuple = (current_max or datetime.min, int(stream.get("scan_max_id") or 0))
            candidate_tuple = (timestamp or datetime.min, document_id)
            if candidate_tuple > current_tuple:
                stream["scan_max_at"] = serialize_utc_datetime(timestamp)
                stream["scan_max_id"] = document_id

        if page.next_cursor is None:
            stream["high_water_at"] = stream["scan_max_at"]
            stream["high_water_id"] = stream["scan_max_id"]
            stream["scan_floor"] = None
            stream["scan_cursor"] = None
            stream["scan_max_at"] = None
            stream["scan_max_id"] = 0
        else:
            stream["scan_cursor"] = page.next_cursor

        if not dry_run:
            state[stream_name] = stream
            self._save_cursor(cursor_name, state)

    async def _process_document(
        self,
        *,
        document: dict[str, Any],
        analyze: AnalysisCallback,
        governed_custom_field_ids: set[int],
        ignored_tag_ids: set[int],
        dry_run: bool,
        result: dict[str, Any],
        forced_reason: str | None = None,
    ) -> None:
        document_id = int(document["id"])
        source_fingerprint, content_identity, metadata_fields = document_source_fingerprint(
            document,
            governed_custom_field_ids=governed_custom_field_ids,
            ignored_tag_ids=ignored_tag_ids,
        )
        state = self._get_document_state(document_id)
        reason = forced_reason
        if reason is None:
            if state is None or state.accepted_source_fingerprint is None:
                reason = "new_document"
            elif state.accepted_source_fingerprint != source_fingerprint:
                reason = "material_source_changed"
            elif state.pending_source_fingerprint == source_fingerprint:
                reason = state.pending_reason or "retry_pending"
            else:
                self._touch_document_state(document)
                result["unchanged"] += 1
                return

        result["documents"].append({"document_id": document_id, "reason": reason})
        if dry_run:
            result["processed"] += 1
            return

        self._mark_pending(document, source_fingerprint, reason)
        self._freshness.record_invalidation(
            document_id=document_id,
            accepted_checksum=content_identity,
            metadata_fields=metadata_fields,
            reason=InvalidationReason.VERSION_CHANGED,
            triggered_by=f"paperless-discovery:{reason}",
        )
        try:
            await analyze(document_id)
        except Exception as exc:
            self._record_failure(document_id, type(exc).__name__)
            result["failed"] += 1
            return

        self._accept_document(document, source_fingerprint)
        result["processed"] += 1

    def _get_cursor(self, name: str) -> DiscoveryCursor | None:
        db = self._session_factory()
        try:
            return db.get(DiscoveryCursor, name)
        finally:
            db.close()

    def _create_cursor(self, name: str, now: datetime) -> None:
        db = self._session_factory()
        try:
            db.add(DiscoveryCursor(name=name, state=_cursor_state(now)))
            db.commit()
        finally:
            db.close()

    def _save_cursor(self, name: str, state: dict[str, Any]) -> None:
        db = self._session_factory()
        try:
            cursor = db.get(DiscoveryCursor, name)
            if cursor is None:
                raise RuntimeError(f"Discovery cursor {name!r} disappeared")
            cursor.state = state
            cursor.updated_at = datetime.utcnow()
            db.commit()
        finally:
            db.close()

    def _get_document_state(self, document_id: int) -> DiscoveredDocumentState | None:
        db = self._session_factory()
        try:
            return db.get(DiscoveredDocumentState, document_id)
        finally:
            db.close()

    def _touch_document_state(self, document: dict[str, Any]) -> None:
        db = self._session_factory()
        try:
            row = db.get(DiscoveredDocumentState, int(document["id"]))
            if row is not None:
                row.last_seen_at = datetime.utcnow()
                row.source_added_at = _parse_timestamp(document.get("added"))
                row.source_modified_at = _parse_timestamp(document.get("modified"))
                db.commit()
        finally:
            db.close()

    def _mark_pending(self, document: dict[str, Any], fingerprint: str, reason: str) -> None:
        db = self._session_factory()
        try:
            document_id = int(document["id"])
            row = db.get(DiscoveredDocumentState, document_id)
            if row is None:
                row = DiscoveredDocumentState(document_id=document_id)
                db.add(row)
            row.pending_source_fingerprint = fingerprint
            row.pending_reason = reason
            row.source_added_at = _parse_timestamp(document.get("added"))
            row.source_modified_at = _parse_timestamp(document.get("modified"))
            row.last_seen_at = datetime.utcnow()
            row.last_error_code = None
            db.commit()
        finally:
            db.close()

    def _accept_document(self, document: dict[str, Any], fingerprint: str) -> None:
        db = self._session_factory()
        try:
            row = db.get(DiscoveredDocumentState, int(document["id"]))
            if row is None:
                raise RuntimeError("Pending discovery state disappeared before acknowledgement")
            row.accepted_source_fingerprint = fingerprint
            row.pending_source_fingerprint = None
            row.pending_reason = None
            row.last_error_code = None
            row.retry_count = 0
            row.last_analyzed_at = datetime.utcnow()
            row.last_seen_at = datetime.utcnow()
            db.commit()
        finally:
            db.close()

    def _record_failure(self, document_id: int, error_code: str) -> None:
        db = self._session_factory()
        try:
            row = db.get(DiscoveredDocumentState, document_id)
            if row is None:
                raise RuntimeError("Pending discovery state disappeared before failure recording")
            row.retry_count += 1
            row.last_error_code = error_code
            db.commit()
        finally:
            db.close()

    def _pending_document_ids(self, *, limit: int) -> list[int]:
        db = self._session_factory()
        try:
            rows = (
                db.query(DiscoveredDocumentState.document_id)
                .filter(DiscoveredDocumentState.pending_source_fingerprint.is_not(None))
                .order_by(DiscoveredDocumentState.last_seen_at, DiscoveredDocumentState.document_id)
                .limit(limit)
                .all()
            )
            return [row[0] for row in rows]
        finally:
            db.close()

    def _pending_count(self) -> int:
        db = self._session_factory()
        try:
            return (
                db.query(DiscoveredDocumentState)
                .filter(DiscoveredDocumentState.pending_source_fingerprint.is_not(None))
                .count()
            )
        finally:
            db.close()
