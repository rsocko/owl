"""Paperless writeback for triage correction events.

Two kinds of sync are handled:

1. **Tag sync** — When orphan documents are resolved (self-pay, already-paid,
   not-medical), the resolution payload includes a ``paperless_tags`` list.
   Tags are created if missing and applied to the document.

2. **Custom-field sync** — After series split/merge/rename/reassign operations
   update the local SQLite database, the corresponding custom-field changes are
   pushed back to Paperless-ngx so the source of truth stays in sync.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from doc_intelligence_hub.core.paperless import PaperlessClient
from doc_intelligence_hub.modules.triage.database import (
    CorrectionEvent,
)
from doc_intelligence_hub.modules.triage.database import (
    get_session as get_triage_session,
)

logger = logging.getLogger(__name__)

# Custom field names used in Paperless-ngx (matched by name, resolved to ID at runtime)
_SERIES_NAME_FIELD = "Series Name"
_ACCOUNT_ID_FIELD = "Account Identifier"


# ------------------------------------------------------------------
# Tag sync (orphan resolution)
# ------------------------------------------------------------------


def _get_paperless_config():
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
    session = get_triage_session()
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
            return {
                "synced": False,
                "error": f"target_id '{document_id}' is not a valid integer document ID",
            }

        base_url, token = _get_paperless_config()
        if not base_url or not token:
            logger.warning("Paperless not configured, skipping tag sync for event %s", event_id)
            return {"synced": False, "error": "Paperless not configured"}

        # Apply tags via async Paperless client
        applied_tags: list[str] = []
        error_msg: str | None = None

        async def _apply_tags() -> None:
            nonlocal applied_tags, error_msg

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
    session = get_triage_session()
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
        elif (
            result.get("error")
            and "No target_id" not in result["error"]
            and "not a valid integer" not in result["error"]
        ):
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


# ------------------------------------------------------------------
# Custom-field sync (series operations)
# ------------------------------------------------------------------


async def _resolve_field_ids(client: PaperlessClient) -> dict[str, int]:
    """Resolve custom field names to their Paperless IDs.

    Returns a mapping of field name -> field ID. Missing fields are omitted.
    """
    fields = await client.list_custom_fields()
    return {
        f["name"]: f["id"]
        for f in fields
        if f.get("name") in (_SERIES_NAME_FIELD, _ACCOUNT_ID_FIELD)
    }


async def sync_correction_event(
    client: PaperlessClient,
    event_id: str,
    *,
    series_name: str | None = None,
    account_identifier: str | None = None,
    target_series_name: str | None = None,
) -> bool:
    """Sync a single correction event to Paperless-ngx.

    Reads the event from the triage DB, determines what custom fields to
    update based on event_type, pushes changes to Paperless, and marks the
    event as synced.

    Returns True on success, False on failure (logged, never raises).
    """
    session = get_triage_session()
    try:
        event = session.query(CorrectionEvent).filter(CorrectionEvent.id == event_id).first()
        if not event:
            logger.warning("Correction event %s not found for Paperless sync", event_id)
            return False

        if event.paperless_synced:
            return True  # Already synced

        payload = json.loads(event.payload_json) if event.payload_json else {}
        field_ids = await _resolve_field_ids(client)

        if not field_ids:
            logger.info("No Paperless custom fields configured for series sync; skipping")
            return False

        success = await _sync_by_event_type(
            client,
            event.event_type,
            payload,
            field_ids,
            series_name=series_name,
            account_identifier=account_identifier,
            target_series_name=target_series_name,
        )

        if success:
            event.paperless_synced = 1
            event.paperless_synced_at = datetime.now(UTC)
            session.commit()

        return success

    except Exception:
        logger.exception("Failed to sync correction event %s to Paperless", event_id)
        session.rollback()
        return False
    finally:
        session.close()


async def _sync_by_event_type(
    client: PaperlessClient,
    event_type: str,
    payload: dict,
    field_ids: dict[str, int],
    *,
    series_name: str | None = None,
    account_identifier: str | None = None,
    target_series_name: str | None = None,
) -> bool:
    """Dispatch to the correct sync handler based on event type."""
    if event_type == "series_rename":
        return await _sync_rename(client, payload, field_ids, series_name, account_identifier)
    elif event_type == "series_split":
        return await _sync_split(client, payload, field_ids, series_name)
    elif event_type == "series_merge":
        return await _sync_merge(client, payload, field_ids, target_series_name)
    elif event_type == "series_reassign":
        return await _sync_reassign(client, payload, field_ids, target_series_name)
    else:
        logger.debug("Event type '%s' does not require Paperless sync", event_type)
        return True


async def _update_doc_fields(
    client: PaperlessClient,
    document_id: str,
    field_ids: dict[str, int],
    series_name: str | None = None,
    account_identifier: str | None = None,
) -> None:
    """Update the custom fields for a single Paperless document."""
    custom_fields: list[dict] = []
    if series_name is not None and _SERIES_NAME_FIELD in field_ids:
        custom_fields.append({"field": field_ids[_SERIES_NAME_FIELD], "value": series_name})
    if account_identifier is not None and _ACCOUNT_ID_FIELD in field_ids:
        custom_fields.append({"field": field_ids[_ACCOUNT_ID_FIELD], "value": account_identifier})

    if custom_fields:
        await client.update_custom_fields(int(document_id), custom_fields)


async def _sync_rename(
    client: PaperlessClient,
    payload: dict,
    field_ids: dict[str, int],
    series_name: str | None,
    account_identifier: str | None,
) -> bool:
    """Rename: update series name and account identifier on all documents."""
    doc_ids = payload.get("document_ids", [])
    new_name = series_name or payload.get("new_name")
    acct = account_identifier or payload.get("account_identifier")

    for doc_id in doc_ids:
        try:
            await _update_doc_fields(
                client, doc_id, field_ids, series_name=new_name, account_identifier=acct
            )
        except Exception:
            logger.exception("Failed to sync rename for document %s", doc_id)
            return False
    return True


async def _sync_split(
    client: PaperlessClient,
    payload: dict,
    field_ids: dict[str, int],
    new_series_name: str | None,
) -> bool:
    """Split: update moved documents' series name to the new series."""
    doc_ids = payload.get("document_ids", [])
    for doc_id in doc_ids:
        try:
            await _update_doc_fields(client, doc_id, field_ids, series_name=new_series_name)
        except Exception:
            logger.exception("Failed to sync split for document %s", doc_id)
            return False
    return True


async def _sync_merge(
    client: PaperlessClient,
    payload: dict,
    field_ids: dict[str, int],
    target_series_name: str | None,
) -> bool:
    """Merge: update source documents' series name to the target series name."""
    doc_ids = payload.get("document_ids", [])
    for doc_id in doc_ids:
        try:
            await _update_doc_fields(client, doc_id, field_ids, series_name=target_series_name)
        except Exception:
            logger.exception("Failed to sync merge for document %s", doc_id)
            return False
    return True


async def _sync_reassign(
    client: PaperlessClient,
    payload: dict,
    field_ids: dict[str, int],
    target_series_name: str | None,
) -> bool:
    """Reassign: update the moved document's series name."""
    doc_id = payload.get("document_id")
    if not doc_id:
        return True
    try:
        await _update_doc_fields(client, doc_id, field_ids, series_name=target_series_name)
    except Exception:
        logger.exception("Failed to sync reassign for document %s", doc_id)
        return False
    return True
