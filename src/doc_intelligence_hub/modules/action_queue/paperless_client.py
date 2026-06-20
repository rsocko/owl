"""Paperless-NGX API client for Action Queue — delegates to shared client."""

from typing import Optional

from doc_intelligence_hub.core.paperless import PaperlessClient
from .config import settings


def _get_client() -> PaperlessClient:
    """Create a PaperlessClient from action queue settings."""
    return PaperlessClient(
        base_url=settings.paperless_url,
        token=settings.paperless_token,
    )


class ActionQueuePaperlessClient:
    """Action Queue specific wrapper around shared PaperlessClient.

    Preserves the same public API as the original for backward compatibility.
    """

    def __init__(self):
        self._client = _get_client()

    async def get_documents_by_tags(self, tag_names: list[str]) -> list[dict]:
        """Fetch all documents that have any of the specified tags."""
        # Resolve tag names to IDs, then fetch by each (union)
        all_tags = await self._client.list_tags()
        name_to_id = {t["name"]: t["id"] for t in all_tags}
        tag_ids = [name_to_id[n] for n in tag_names if n in name_to_id]
        if not tag_ids:
            return []
        return await self._client.list_documents_by_tag_ids(tag_ids)

    async def get_documents_by_saved_view(self, view_id: int) -> list[dict]:
        """Fetch all documents matching a Paperless saved view."""
        return await self._client.list_documents(saved_view=view_id)

    async def get_documents_by_query(
        self,
        query: Optional[str] = None,
        tags: Optional[list[str]] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        added_after: Optional[str] = None,
        added_before: Optional[str] = None,
        correspondent: Optional[str] = None,
        document_type: Optional[str] = None,
    ) -> list[dict]:
        """Flexible document query using Paperless API filter parameters."""
        return await self._client.list_documents(
            query=query,
            tags=tags,
            created_after=created_after,
            created_before=created_before,
            added_after=added_after,
            added_before=added_before,
            correspondent=correspondent,
            document_type=document_type,
        )

    async def list_saved_views(self) -> list[dict]:
        """List all saved views in Paperless."""
        return await self._client.list_saved_views()

    async def get_document_content(self, document_id: int) -> str:
        """Get the OCR/text content of a document."""
        return await self._client.get_document_content(document_id)

    async def get_document(self, document_id: int) -> dict:
        """Get full document metadata."""
        return await self._client.get_document(document_id)

    async def update_custom_fields(self, document_id: int, custom_fields: list[dict]) -> None:
        """Update custom field values on a document (merge, not replace)."""
        await self._client.update_custom_fields(document_id, custom_fields)

    async def get_custom_fields(self) -> list[dict]:
        """List all defined custom fields."""
        return await self._client.list_custom_fields()

    async def create_custom_field(self, field_def: dict) -> dict:
        """Create a new custom field definition."""
        return await self._client.create_custom_field(field_def)

    async def health_check(self) -> bool:
        """Verify connectivity to Paperless-NGX."""
        try:
            result = await self._client.health_check()
            return result.get("status") == "ok"
        except Exception:
            return False


# Keep backward compatibility — the module-level PaperlessClient name
PaperlessClient = ActionQueuePaperlessClient

