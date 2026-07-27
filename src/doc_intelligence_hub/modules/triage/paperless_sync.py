"""Paperless tag sync worker — applies tags from CorrectionEvents to Paperless documents.

When orphan documents are resolved (self-pay, already-paid, not-medical), the resolution
payload includes a ``paperless_tags`` list. This module reads those events and applies
the tags to the corresponding Paperless documents via the REST API.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from doc_intelligence_hub.modules.triage.database import (
    CorrectionEvent,
    get_session,
)

logger = logging.getLogger(__name__)


def _get_paperless_client():
    """Return (base_url, token) for the Paperless instance, or (None, None) if unconfigured."""
    from doc_intelligence_hub.modules.statements.config import load_config, resolve_api_token

    config = None
    with contextlib.suppress(Exception):
        config = load_config()

    base_url = None
    token = None
    if config:
        base_url = config.source.paperless_url
        token = resolve_api_token(config)

    return base_url, token


def sync_correction_to_paperless(event_id: str) -> dict[str, Any]:
    """Sync a single CorrectionEvent's tags to Paperless.

    Returns a dict with ``synced``, ``tags_applied``, and ``error`` keys.
    """
    session = get_session()
    try:
        event = session.query(CorrectionEvent).filter(CorrectionEvent.id == event_id).first()
        if not event:
            return {"synced": False, "error": f"CorrectionEvent {event_id} not found"}

        if event.paperless_synced:
            return {"synced": True, "tags_applied": [], "error": None, "already_synced": True}

        if event.undone:
            return {"synced": False, "error": "Event has been undone, skipping sync"}

        # Parse payload
        try:
            payload = json.loads(event.payload_json) if event.payload_json else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}

        tags_to_apply = payload.get("paperless_tags", [])
        if not tags_to_apply:
            # No tags requested — mark as synced (nothing to do)
            event.paperless_synced = 1
            event.paperless_synced_at = datetime.now(UTC)
            session.commit()
            return {"synced": True, "tags_applied": [], "error": None}

        document_id = event.target_id
        if not document_id:
            return {"synced": False, "error": "No target_id (document ID) on event"}

        # Try to parse document_id as int for Paperless API
        try:
            doc_id_int = int(document_id)
        except (ValueError, TypeError):
            return {"synced": False, "error": f"target_id '{document_id}' is not a valid integer document ID"}

        base_url, token = _get_paperless_client()
        if not base_url or not token:
            logger.warning("Paperless not configured, skipping tag sync for event %s", event_id)
            return {"synced": False, "error": "Paperless not configured"}

        # Apply tags via async Paperless client
        applied_tags: list[str] = []
        error_msg: str | None = None

        async def _apply_tags() -> None:
            nonlocal applied_tags, error_msg
            from doc_intelligence_hub.core.paperless import PaperlessClient

            client = PaperlessClient(base_url=base_url, token=token)
            try:
                # Get existing tags from Paperless
                all_tags = await client.list_tags()
                tag_name_to_id: dict[str, int] = {
                    t["name"].lower(): t["id"] for t in all_tags if "name" in t and "id" in t
                }

                resolved_tag_ids: list[int] = []
                for tag_name in tags_to_apply:
                    existing_id = tag_name_to_id.get(tag_name.lower())
                    if existing_id is not None:
                        resolved_tag_ids.append(existing_id)
                    else:
                        # Create the tag
                        http_client = client._get_client()
                        resp = await http_client.post("/api/tags/", json={"name": tag_name})
                        resp.raise_for_status()
                        new_tag_id = resp.json()["id"]
                        resolved_tag_ids.append(new_tag_id)
                        tag_name_to_id[tag_name.lower()] = new_tag_id

                # Get current document tags and merge
                doc = await client.get_document(doc_id_int)
                current_tags: list[int] = doc.get("tags", [])
                merged_tags = list(set(current_tags + resolved_tag_ids))

                if merged_tags != current_tags:
                    await client.update_document(doc_id_int, {"tags": merged_tags})

                applied_tags = tags_to_apply
            finally:
                await client.aclose()

        try:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_apply_tags())
            except RuntimeError:
                asyncio.run(_apply_tags())
        except Exception as exc:
            error_msg = str(exc)
            logger.warning("Failed to sync tags for event %s: %s", event_id, exc)

        if error_msg:
            return {"synced": False, "tags_applied": [], "error": error_msg}

        # Mark as synced
        event.paperless_synced = 1
        event.paperless_synced_at = datetime.now(UTC)
        session.commit()

        logger.info("Synced tags %s to doc %s for event %s", tags_to_apply, document_id, event_id)
        return {"synced": True, "tags_applied": applied_tags, "error": None}

    except Exception as exc:
        logger.exception("Unexpected error syncing event %s", event_id)
        return {"synced": False, "tags_applied": [], "error": str(exc)}
    finally:
        session.close()


def sync_all_pending() -> dict[str, Any]:
    """Find all unsynced, non-undone CorrectionEvents with paperless_tags and sync them.

    Returns summary with counts of synced, skipped, and failed events.
    """
    session = get_session()
    try:
        pending_events = (
            session.query(CorrectionEvent)
            .filter(CorrectionEvent.paperless_synced == 0, CorrectionEvent.undone == 0)
            .all()
        )
        event_ids = [e.id for e in pending_events]
    finally:
        session.close()

    synced = 0
    skipped = 0
    failed = 0
    errors: list[dict[str, str]] = []

    for eid in event_ids:
        result = sync_correction_to_paperless(eid)
        if result.get("synced"):
            if result.get("already_synced"):
                skipped += 1
            else:
                synced += 1
        elif result.get("error") and "No target_id" not in result["error"] and "not a valid integer" not in result["error"]:
            failed += 1
            errors.append({"event_id": eid, "error": result["error"]})
        else:
            skipped += 1

    return {
        "total": len(event_ids),
        "synced": synced,
        "skipped": skipped,
        "failed": failed,
        "errors": errors,
    }
