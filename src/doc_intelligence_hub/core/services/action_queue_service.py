"""Action Queue service — encapsulates pipeline execution and action management."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from doc_intelligence_hub.core.resilience import retry_async
from doc_intelligence_hub.core.services.base import BaseService
from doc_intelligence_hub.modules.action_queue.database import Action, get_session, init_db
from doc_intelligence_hub.modules.action_queue.pipeline import get_pipeline_progress, run_pipeline
from doc_intelligence_hub.modules.action_queue.risk_scoring import (
    compute_risk_score,
    recalculate_risk_scores,
)


class ActionQueueService(BaseService):
    """Service layer for the Action Queue module.

    Decouples the API router from direct pipeline/database calls and adds:
    - Structured logging for pipeline runs
    - Retry for pipeline execution
    - Consistent error handling
    """

    service_name = "action_queue"

    @retry_async(max_attempts=2, base_delay=3.0)
    async def run_pipeline(
        self,
        *,
        limit: int | None = None,
        dry_run: bool = True,
        force: bool = False,
    ) -> dict[str, Any]:
        """Run the action queue pipeline with retry support."""
        self.logger.info(
            "Running action queue pipeline (limit=%s, dry_run=%s, force=%s)",
            limit, dry_run, force,
        )
        result = await run_pipeline(limit=limit, dry_run=dry_run, force=force)
        self.logger.info("Pipeline complete: %s", result)
        return result

    def get_pipeline_progress(self) -> dict[str, Any]:
        """Get current pipeline execution progress."""
        return get_pipeline_progress()

    def get_database_counts(self) -> dict[str, int]:
        """Get action counts by status."""
        init_db()
        db = get_session()
        try:
            pending = db.query(Action).filter_by(status="pending").count()
            completed = db.query(Action).filter_by(status="completed").count()
            dismissed = db.query(Action).filter_by(status="dismissed").count()
            return {
                "pending": pending,
                "completed": completed,
                "dismissed": dismissed,
                "total": pending + completed + dismissed,
            }
        finally:
            db.close()

    def list_actions(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List action items with optional status filter."""
        init_db()
        db = get_session()
        try:
            query = db.query(Action)
            if status:
                query = query.filter_by(status=status)
            if status == "pending":
                query = query.order_by(Action.risk_score.desc(), Action.created_at.desc())
            else:
                query = query.order_by(Action.created_at.desc())
            total = query.count()
            actions = query.offset(offset).limit(limit).all()
            return {"actions": actions, "total": total, "limit": limit, "offset": offset}
        finally:
            db.close()

    def update_action(
        self,
        action_id: int,
        *,
        status: str,
        version: int | None = None,
    ) -> Action:
        """Update an action's status with optimistic locking.

        Raises:
            KeyError: Action not found.
            ValueError: Version conflict.
        """
        init_db()
        db = get_session()
        try:
            action = db.query(Action).filter_by(id=action_id).first()
            if not action:
                raise KeyError(f"Action {action_id} not found")

            if version is not None and action.version != version:
                raise ValueError(
                    f"Version conflict on action {action_id}: "
                    f"expected {version}, found {action.version}"
                )

            action.status = status
            action.version = (action.version or 1) + 1
            if status == "completed":
                action.completed_at = datetime.utcnow()
            elif status == "pending":
                action.completed_at = None
                action.risk_score = compute_risk_score(
                    urgency=action.urgency or "LOW",
                    due_date=action.due_date,
                    amount=action.amount,
                    confidence=action.confidence or 0,
                    action_type=action.action_type or "REVIEW",
                )
            db.commit()
            self.logger.info("Action %d updated to status=%s", action_id, status)
            return action
        finally:
            db.close()

    def bulk_update(self, action_ids: list[int], target_status: str) -> int:
        """Apply a status change to multiple actions. Returns count of affected items."""
        init_db()
        db = get_session()
        try:
            actions = db.query(Action).filter(Action.id.in_(action_ids)).all()
            affected = 0
            for action in actions:
                if action.status == target_status:
                    continue
                action.status = target_status
                action.version = (action.version or 1) + 1
                if target_status == "completed":
                    action.completed_at = datetime.utcnow()
                elif target_status == "pending":
                    action.completed_at = None
                    action.risk_score = compute_risk_score(
                        urgency=action.urgency or "LOW",
                        due_date=action.due_date,
                        amount=action.amount,
                        confidence=action.confidence or 0,
                        action_type=action.action_type or "REVIEW",
                    )
                affected += 1
            db.commit()
            self.logger.info(
                "Bulk update: %d actions set to %s", affected, target_status
            )
            return affected
        finally:
            db.close()

    def recalculate_risk_scores(self) -> dict[str, Any]:
        """Recalculate risk scores for all pending actions."""
        self.logger.info("Recalculating risk scores for pending actions")
        return recalculate_risk_scores()

    def emit_alerts(self) -> None:
        """Emit unified alerts for pending actions (best-effort)."""
        try:
            from doc_intelligence_hub.core.alerts import emit_action_queue_alerts

            init_db()
            db = get_session()
            try:
                pending_actions = db.query(Action).filter_by(status="pending").all()
                action_dicts = [
                    {
                        "id": a.id,
                        "title": a.title,
                        "document_title": a.document_title,
                        "urgency": a.urgency,
                        "status": a.status,
                        "due_date": a.due_date.isoformat() if a.due_date else None,
                        "action_type": a.action_type,
                    }
                    for a in pending_actions
                ]
                emit_action_queue_alerts(action_dicts)
            finally:
                db.close()
        except Exception as exc:
            self.logger.debug("Alert emission failed (best-effort): %s", exc)
