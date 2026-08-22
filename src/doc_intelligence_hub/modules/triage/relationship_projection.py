"""Rebuild the non-authoritative Paperless projection of OWL relationships."""

from __future__ import annotations

from typing import Any

from doc_intelligence_hub.core.paperless import (
    MetadataFieldKey,
    PaperlessClient,
    PaperlessMetadataResolver,
    build_metadata_update,
)
from doc_intelligence_hub.modules.statements.config import load_config, resolve_api_token
from doc_intelligence_hub.modules.triage.relationships import list_document_relationships

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


async def project_relationships_to_paperless(document_ids: set[int]) -> dict[str, Any]:
    """Rebuild relationship custom fields for the specified Paperless documents."""
    config = load_config()
    token = resolve_api_token(config)
    if not config.source.paperless_url or not token:
        return {
            "synced": False,
            "documents": sorted(document_ids),
            "error": "Paperless is not configured",
        }

    client = PaperlessClient(base_url=config.source.paperless_url, token=token)
    try:
        schema = await PaperlessMetadataResolver(client).ensure(_PROJECTION_FIELDS)
        for document_id in sorted(document_ids):
            related_ids, summary = _projection_values(document_id)
            updates = [
                build_metadata_update(
                    MetadataFieldKey.RELATED_DOCUMENTS,
                    ", ".join(str(related_id) for related_id in related_ids),
                    schema,
                ),
                build_metadata_update(MetadataFieldKey.RELATIONSHIP_SUMMARY, summary, schema),
            ]
            await client.update_custom_fields(document_id, updates)
    finally:
        await client.aclose()

    return {"synced": True, "documents": sorted(document_ids), "error": None}
