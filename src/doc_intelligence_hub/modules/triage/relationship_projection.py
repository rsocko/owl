"""Rebuild the non-authoritative Paperless projection of OWL relationships."""

from __future__ import annotations

import logging
from typing import Any

from doc_intelligence_hub.core.paperless import (
    MetadataFieldKey,
    PaperlessClient,
    PaperlessMetadataResolver,
    build_metadata_update,
)
from doc_intelligence_hub.modules.action_queue.database import (
    ObligationDocument,
)
from doc_intelligence_hub.modules.action_queue.database import (
    get_session as get_action_queue_session,
)
from doc_intelligence_hub.modules.action_queue.database import (
    init_db as init_action_queue_db,
)
from doc_intelligence_hub.modules.action_queue.obligation_projection import (
    obligation_projection_document_ids,
    project_obligations_to_paperless,
)
from doc_intelligence_hub.modules.triage.relationships import list_document_relationships

logger = logging.getLogger(__name__)

_PROJECTION_FIELDS = (
    MetadataFieldKey.RELATED_DOCUMENTS,
    MetadataFieldKey.RELATIONSHIP_SUMMARY,
)


def _projection_values(document_id: int) -> tuple[list[int], str]:
    relationships = list_document_relationships(document_id)
    related_ids: list[int] = []
    summaries: list[str] = []
    for relationship in relationships:
        if relationship["source_document_id"] == document_id:
            other_id = relationship["target_document_id"]
            summaries.append(f"{relationship['relationship_type']} #{other_id}")
        else:
            other_id = relationship["source_document_id"]
            summaries.append(f"#{other_id} {relationship['relationship_type']} this")
        related_ids.append(other_id)
    return sorted(set(related_ids)), "; ".join(summaries)


async def project_relationships_to_paperless(
    client: PaperlessClient,
    document_ids: set[int],
) -> dict[str, Any]:
    """Rebuild relationship custom fields for the specified Paperless documents."""
    targets = sorted(document_ids)
    try:
        schema = await PaperlessMetadataResolver(client).ensure(_PROJECTION_FIELDS)
    except Exception:
        logger.exception("Failed to resolve Paperless relationship projection fields")
        return {
            "synced": False,
            "status": "failed",
            "attempted": len(targets),
            "succeeded": 0,
            "failed": len(targets),
            "documents": targets,
            "failed_documents": targets,
            "errors": [],
            "error_code": "paperless_schema_unavailable",
            "error": "Paperless relationship fields are unavailable or incompatible.",
            "obligation_projection": None,
        }
    if any(not schema.field(key).is_compatible for key in _PROJECTION_FIELDS):
        logger.error("Paperless relationship projection fields are incompatible")
        return {
            "synced": False,
            "status": "failed",
            "attempted": len(targets),
            "succeeded": 0,
            "failed": len(targets),
            "documents": targets,
            "failed_documents": targets,
            "errors": [],
            "error_code": "paperless_schema_incompatible",
            "error": "Paperless relationship fields are unavailable or incompatible.",
            "obligation_projection": None,
        }

    succeeded_documents: set[int] = set()
    errors: list[dict[str, Any]] = []
    for document_id in targets:
        try:
            related_ids, summary = _projection_values(document_id)
            updates = [
                build_metadata_update(
                    MetadataFieldKey.RELATED_DOCUMENTS,
                    ", ".join(str(related_id) for related_id in related_ids),
                    schema,
                ),
                build_metadata_update(MetadataFieldKey.RELATIONSHIP_SUMMARY, summary, schema),
            ]
            await client.update_custom_fields_verified(document_id, updates)
            succeeded_documents.add(document_id)
        except Exception:
            logger.exception(
                "Failed to update Paperless relationship fields for document %s",
                document_id,
            )
            errors.append(
                {
                    "document_id": document_id,
                    "code": "paperless_document_update_failed",
                    "message": "Paperless relationship fields could not be updated.",
                }
            )

    init_action_queue_db()
    db = get_action_queue_session()
    try:
        obligation_ids = {
            row[0]
            for row in db.query(ObligationDocument.obligation_id)
            .filter(
                ObligationDocument.document_id.in_(document_ids),
                ObligationDocument.excluded.is_(False),
            )
            .distinct()
            .all()
        }
        obligation_document_ids = document_ids | obligation_projection_document_ids(
            db,
            obligation_ids,
        )
        obligation_projection = await project_obligations_to_paperless(
            db,
            client,
            document_ids=obligation_document_ids,
        )
        failed_obligation_documents = {
            int(error["document_id"])
            for error in obligation_projection["errors"]
            if error.get("document_id") is not None
        }
        affected_obligation_documents: set[int] = set()
        if failed_obligation_documents:
            failed_obligation_ids = {
                row[0]
                for row in db.query(ObligationDocument.obligation_id)
                .filter(ObligationDocument.document_id.in_(failed_obligation_documents))
                .distinct()
                .all()
            }
            affected_obligation_documents = obligation_projection_document_ids(
                db,
                failed_obligation_ids,
            )
    finally:
        db.close()

    if obligation_projection["failed"]:
        errors.append(
            {
                "document_id": None,
                "code": "paperless_obligation_projection_failed",
                "message": "Paperless obligation fields could not be updated.",
            }
        )

    direct_failed_documents = set(targets) - succeeded_documents
    failed_documents = direct_failed_documents | affected_obligation_documents
    failed = len(failed_documents)
    projected_documents = set(obligation_document_ids)
    if not failed and obligation_projection["failed"] == 0:
        status = "synced"
        error_code = None
        error = None
    else:
        status = "partial" if succeeded_documents else "failed"
        error_code = (
            "paperless_document_update_failed"
            if direct_failed_documents
            else "paperless_obligation_projection_failed"
        )
        error = (
            "One or more Paperless documents could not be updated."
            if direct_failed_documents
            else "Paperless obligation fields could not be updated."
        )
    return {
        "synced": status == "synced",
        "status": status,
        "attempted": len(projected_documents),
        "succeeded": len(projected_documents - failed_documents),
        "failed": failed,
        "documents": sorted(obligation_document_ids),
        "failed_documents": sorted(failed_documents),
        "errors": errors,
        "error_code": error_code,
        "error": error,
        "obligation_projection": obligation_projection,
    }
