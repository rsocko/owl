"""Paperless writeback for triage correction events.

After series split/merge/rename/reassign operations update the local SQLite
database, this module pushes the corresponding custom-field changes back to
Paperless-ngx so the source of truth stays in sync.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from doc_intelligence_hub.core.paperless import PaperlessClient
from doc_intelligence_hub.modules.triage.database import (
    CorrectionEvent,
    get_session as get_triage_session,
)

logger = logging.getLogger(__name__)

# Custom field names used in Paperless-ngx (matched by name, resolved to ID at runtime)
_SERIES_NAME_FIELD = "Series Name"
_ACCOUNT_ID_FIELD = "Account Identifier"


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
            await _update_doc_fields(client, doc_id, field_ids, series_name=new_name, account_identifier=acct)
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
