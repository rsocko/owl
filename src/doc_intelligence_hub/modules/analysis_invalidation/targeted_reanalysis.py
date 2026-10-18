"""Durable, debounced targeted reanalysis requests."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from .database import TargetedReanalysisRequest, init_db
from .database import get_session as default_session_factory

logger = logging.getLogger(__name__)

AnalyzeCallback = Callable[[int, str], Awaitable[dict[str, Any]]]
_background_tasks: set[asyncio.Task[None]] = set()


def _finish_background_task(task: asyncio.Task[None]) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exception = task.exception()
    if exception is not None:
        logger.error(
            "Debounced targeted reanalysis task failed",
            exc_info=(type(exception), exception, exception.__traceback__),
        )


class TargetedReanalysisService:
    def __init__(self, session_factory=default_session_factory):
        self._session_factory = session_factory

    def schedule(
        self,
        *,
        document_id: int,
        source_fingerprint: str,
        source_modified: str | None,
        reason: str,
        analyzer_version: str,
        debounce_seconds: float,
    ) -> dict[str, Any]:
        init_db()
        requested_at = datetime.utcnow()
        due_at = requested_at + timedelta(seconds=debounce_seconds)
        db = self._session_factory()
        try:
            request = db.get(TargetedReanalysisRequest, document_id)
            if (
                request is not None
                and request.source_fingerprint == source_fingerprint
                and request.status in {"pending", "running", "completed"}
                and reason != "manual_reanalysis"
            ):
                return self._serialize(request, coalesced=True)
            if request is None:
                request = TargetedReanalysisRequest(
                    document_id=document_id,
                    generation=1,
                    source_fingerprint=source_fingerprint,
                    source_modified=source_modified,
                    reason=reason,
                    status="pending",
                    requested_at=requested_at,
                    due_at=due_at,
                    analyzer_version=analyzer_version,
                )
                db.add(request)
            else:
                request.generation += 1
                request.source_fingerprint = source_fingerprint
                request.source_modified = source_modified
                request.reason = reason
                request.status = "pending"
                request.requested_at = requested_at
                request.due_at = due_at
                request.started_at = None
                request.completed_at = None
                request.analyzer_version = analyzer_version
                request.last_error_code = None
            db.commit()
            db.refresh(request)
            return self._serialize(request, coalesced=False)
        finally:
            db.close()

    def get(self, document_id: int) -> dict[str, Any] | None:
        init_db()
        db = self._session_factory()
        try:
            request = db.get(TargetedReanalysisRequest, document_id)
            return self._serialize(request) if request is not None else None
        finally:
            db.close()

    def list_due(self, limit: int = 25) -> list[dict[str, Any]]:
        init_db()
        db = self._session_factory()
        try:
            requests = (
                db.query(TargetedReanalysisRequest)
                .filter(
                    TargetedReanalysisRequest.status == "pending",
                    TargetedReanalysisRequest.due_at <= datetime.utcnow(),
                )
                .order_by(TargetedReanalysisRequest.due_at.asc())
                .limit(limit)
                .all()
            )
            return [self._serialize(request) for request in requests]
        finally:
            db.close()

    async def run_if_current(
        self,
        *,
        document_id: int,
        generation: int,
        analyze: AnalyzeCallback,
    ) -> dict[str, Any]:
        init_db()
        db = self._session_factory()
        try:
            request = db.get(TargetedReanalysisRequest, document_id)
            if request is None or request.generation != generation:
                return {"status": "superseded", "document_id": document_id}
            if request.status == "completed":
                return self._serialize(request, coalesced=True)
            if request.due_at > datetime.utcnow():
                return {"status": "not_due", "document_id": document_id}
            request.status = "running"
            request.started_at = datetime.utcnow()
            request.last_error_code = None
            source_fingerprint = request.source_fingerprint
            db.commit()
        finally:
            db.close()

        try:
            result = await analyze(document_id, source_fingerprint)
        except Exception as exc:
            logger.exception("Targeted reanalysis failed for document %s", document_id)
            db = self._session_factory()
            try:
                request = db.get(TargetedReanalysisRequest, document_id)
                if request is not None and request.generation == generation:
                    request.status = "failed"
                    request.completed_at = datetime.utcnow()
                    request.last_error_code = type(exc).__name__
                    db.commit()
                    return self._serialize(request)
                return {"status": "superseded", "document_id": document_id}
            finally:
                db.close()

        db = self._session_factory()
        try:
            request = db.get(TargetedReanalysisRequest, document_id)
            if request is None or request.generation != generation:
                return {"status": "superseded", "document_id": document_id, "result": result}
            request.status = "completed"
            request.completed_at = datetime.utcnow()
            request.last_error_code = None
            db.commit()
            return {**self._serialize(request), "result": result}
        finally:
            db.close()

    @staticmethod
    def _serialize(
        request: TargetedReanalysisRequest,
        *,
        coalesced: bool | None = None,
    ) -> dict[str, Any]:
        payload = {
            "document_id": request.document_id,
            "generation": request.generation,
            "source_fingerprint": request.source_fingerprint,
            "source_modified": request.source_modified,
            "reason": request.reason,
            "status": request.status,
            "requested_at": request.requested_at.isoformat() if request.requested_at else None,
            "due_at": request.due_at.isoformat() if request.due_at else None,
            "started_at": request.started_at.isoformat() if request.started_at else None,
            "completed_at": request.completed_at.isoformat() if request.completed_at else None,
            "analyzer_version": request.analyzer_version,
            "last_error_code": request.last_error_code,
        }
        if coalesced is not None:
            payload["coalesced"] = coalesced
        return payload


def spawn_debounced_reanalysis(
    *,
    service: TargetedReanalysisService,
    document_id: int,
    generation: int,
    delay_seconds: float,
    analyze: AnalyzeCallback,
) -> None:
    async def run() -> None:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        await service.run_if_current(
            document_id=document_id,
            generation=generation,
            analyze=analyze,
        )

    task = asyncio.create_task(run())
    _background_tasks.add(task)
    task.add_done_callback(_finish_background_task)
