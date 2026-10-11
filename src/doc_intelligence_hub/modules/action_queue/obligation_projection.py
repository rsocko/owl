"""One-way Paperless projection of OWL obligation membership."""

from __future__ import annotations

import logging
from typing import Any, Iterable

from sqlalchemy.orm import Session

from doc_intelligence_hub.core.paperless import (
    MetadataFieldKey,
    PaperlessClient,
    PaperlessMetadataResolver,
    build_metadata_update,
)

from .database import ObligationDocument

logger = logging.getLogger(__name__)

_PROJECTION_FIELDS = (
    MetadataFieldKey.OBLIGATION_RELATED_DOCUMENTS,
    MetadataFieldKey.OBLIGATION_ID,
    MetadataFieldKey.OBLIGATION_ROLE,
)


def obligation_projection_document_ids(
    db: Session,
    obligation_ids: Iterable[str] | None = None,
) -> set[int]:
    """Return every current or stale document that needs an obligation projection."""
    query = db.query(ObligationDocument.document_id)
    if obligation_ids is not None:
        ids = set(obligation_ids)
        if not ids:
            return set()
        query = query.filter(ObligationDocument.obligation_id.in_(ids))
    return {int(row[0]) for row in query.distinct().all()}


def _projection_updates(db: Session, document_id: int, schema: Any) -> list[dict[str, Any]]:
    memberships = (
        db.query(ObligationDocument)
        .filter_by(document_id=document_id, excluded=False)
        .order_by(ObligationDocument.created_at.desc(), ObligationDocument.id.desc())
        .all()
    )
    if len(memberships) > 1:
        obligation_ids = ", ".join(sorted({row.obligation_id for row in memberships}))
        raise RuntimeError(
            f"Document {document_id} has multiple active obligations: {obligation_ids}"
        )

    if not memberships:
        related_ids: list[int] = []
        obligation_id: str | None = None
        role: str | None = None
    else:
        membership = memberships[0]
        related_ids = [
            int(row.document_id)
            for row in db.query(ObligationDocument)
            .filter_by(obligation_id=membership.obligation_id, excluded=False)
            .order_by(ObligationDocument.document_id.asc())
            .all()
            if row.document_id != document_id
        ]
        obligation_id = membership.obligation_id
        role = membership.role

    return [
        build_metadata_update(
            MetadataFieldKey.OBLIGATION_RELATED_DOCUMENTS,
            related_ids,
            schema,
        ),
        build_metadata_update(MetadataFieldKey.OBLIGATION_ID, obligation_id, schema),
        build_metadata_update(MetadataFieldKey.OBLIGATION_ROLE, role, schema),
    ]


async def project_obligations_to_paperless(
    db: Session,
    client: PaperlessClient | None,
    *,
    document_ids: Iterable[int] | None = None,
    write_enabled: bool = True,
) -> dict[str, Any]:
    """Rebuild owned obligation fields without changing unrelated Paperless metadata."""
    targets = (
        sorted({int(document_id) for document_id in document_ids})
        if document_ids is not None
        else sorted(obligation_projection_document_ids(db))
    )
    if not write_enabled:
        return {
            "synced": False,
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "documents": targets,
            "errors": [],
            "error": "Paperless writes are disabled",
        }
    if client is None:
        raise ValueError("A Paperless client is required when writes are enabled")
    if not targets:
        return {
            "synced": True,
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "documents": [],
            "errors": [],
            "error": None,
        }

    try:
        schema = await PaperlessMetadataResolver(client).ensure(_PROJECTION_FIELDS)
    except Exception:
        logger.exception("Failed to resolve Paperless obligation projection fields")
        return {
            "synced": False,
            "attempted": len(targets),
            "succeeded": 0,
            "failed": len(targets),
            "documents": targets,
            "errors": [
                {
                    "document_id": document_id,
                    "code": "paperless_schema_unavailable",
                    "message": "Paperless obligation fields are unavailable or incompatible.",
                }
                for document_id in targets
            ],
            "error_code": "paperless_schema_unavailable",
            "error": "Paperless obligation fields are unavailable or incompatible.",
        }

    succeeded = 0
    errors: list[dict[str, Any]] = []
    for document_id in targets:
        try:
            updates = _projection_updates(db, document_id, schema)
            await client.update_custom_fields_verified(document_id, updates)
            succeeded += 1
        except Exception:
            logger.exception(
                "Failed to update Paperless obligation fields for document %s",
                document_id,
            )
            errors.append(
                {
                    "document_id": document_id,
                    "code": "paperless_obligation_projection_failed",
                    "message": "Paperless obligation fields could not be updated.",
                }
            )

    failed = len(errors)
    return {
        "synced": failed == 0,
        "attempted": len(targets),
        "succeeded": succeeded,
        "failed": failed,
        "documents": targets,
        "errors": errors,
        "error_code": None if not errors else "paperless_obligation_projection_failed",
        "error": None if not errors else "One or more Paperless documents could not be updated",
    }
