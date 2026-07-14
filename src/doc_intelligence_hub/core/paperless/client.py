"""Paperless-ngx async API client — shared across all Document Intelligence Hub modules.

Provides:
- Document CRUD (list, fetch, update, paginated queries)
- Tag resolution (name ↔ ID)
- Correspondent resolution
- Custom fields (list, create, update)
- Saved views
- Connection health check
- Fixture loading for tests
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

import httpx


class PaperlessClient:
    """Async HTTP client for Paperless-NGX REST API.

    Usage:
        client = PaperlessClient(base_url="https://paperless.example.com", token="abc123")
        docs = await client.list_documents(tags=["Inbox"])
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        verify_ssl: bool = True,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.verify_ssl = verify_ssl
        self.timeout = timeout

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Token {self.token}",
                "Content-Type": "application/json",
            },
            verify=self.verify_ssl,
            timeout=self.timeout,
            follow_redirects=True,
        )

    # ------------------------------------------------------------------
    # Health / connectivity
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Verify connectivity and return basic stats."""
        async with self._make_client() as client:
            api_resp = await client.get("/api/")
            api_resp.raise_for_status()

            doc_resp = await client.get("/api/documents/", params={"page": 1, "page_size": 1})
            doc_resp.raise_for_status()
            doc_data = doc_resp.json()

            corr_data = await self._safe_list(client, "/api/correspondents/")
            tag_data = await self._safe_list(client, "/api/tags/")

        return {
            "status": "ok",
            "base_url": self.base_url,
            "documents": int(doc_data.get("count", 0)),
            "correspondents": int(corr_data.get("count", 0)),
            "tags": int(tag_data.get("count", 0)),
            "correspondents_access": str(corr_data.get("access", "ok")),
            "tags_access": str(tag_data.get("access", "ok")),
        }

    # ------------------------------------------------------------------
    # Documents — read
    # ------------------------------------------------------------------

    async def list_documents(
        self,
        *,
        tags: Optional[list[str]] = None,
        query: Optional[str] = None,
        correspondent: Optional[str] = None,
        document_type: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        added_after: Optional[str] = None,
        added_before: Optional[str] = None,
        saved_view: Optional[int] = None,
        page_size: int = 100,
        on_progress: Optional[ProgressCallback] = None,
    ) -> list[dict]:
        """Fetch documents with flexible filtering.

        Returns raw Paperless document dicts (id, title, correspondent, tags, etc).
        """
        async with self._make_client() as client:
            params: dict[str, Any] = {"page_size": page_size}

            if saved_view is not None:
                params["saved_view"] = saved_view

            if query:
                params["query"] = query
            if created_after:
                params["created__date__gte"] = created_after
            if created_before:
                params["created__date__lte"] = created_before
            if added_after:
                params["added__date__gte"] = added_after
            if added_before:
                params["added__date__lte"] = added_before

            if tags:
                tag_ids = await self._resolve_tag_ids(client, tags)
                if tag_ids:
                    # Use __in for OR logic (docs with ANY of these tags)
                    params["tags__id__in"] = ",".join(str(t) for t in tag_ids)

            if correspondent:
                corr_id = await self._resolve_correspondent_id(client, correspondent)
                if corr_id:
                    params["correspondent__id"] = corr_id

            if document_type:
                type_id = await self._resolve_document_type_id(client, document_type)
                if type_id:
                    params["document_type__id"] = type_id

            return await self._paginate(client, "/api/documents/", params, on_progress=on_progress)

    async def list_documents_by_tag_ids(self, tag_ids: list[int], *, page_size: int = 100) -> list[dict]:
        """Fetch documents matching any of the given tag IDs (union, deduplicated)."""
        async with self._make_client() as client:
            seen: set[int] = set()
            results: list[dict] = []
            for tag_id in tag_ids:
                docs = await self._paginate(client, "/api/documents/", {"tags__id": tag_id, "page_size": page_size})
                for doc in docs:
                    if doc["id"] not in seen:
                        seen.add(doc["id"])
                        results.append(doc)
            return results

    async def get_document(self, document_id: int) -> dict:
        """Get full document metadata by ID."""
        async with self._make_client() as client:
            resp = await client.get(f"/api/documents/{document_id}/")
            resp.raise_for_status()
            return resp.json()

    async def get_document_content(self, document_id: int) -> str:
        """Get the OCR/text content of a document."""
        async with self._make_client() as client:
            resp = await client.get(f"/api/documents/{document_id}/")
            resp.raise_for_status()
            return resp.json().get("content", "")

    async def get_document_thumbnail(self, document_id: int) -> tuple[bytes, str]:
        """Get document thumbnail bytes and content-type."""
        async with self._make_client() as client:
            resp = await client.get(f"/api/documents/{document_id}/thumb/")
            resp.raise_for_status()
            return resp.content, resp.headers.get("content-type", "image/webp")

    async def get_document_preview(self, document_id: int) -> tuple[bytes, str]:
        """Get document PDF preview bytes and content-type."""
        async with self._make_client() as client:
            resp = await client.get(f"/api/documents/{document_id}/preview/")
            resp.raise_for_status()
            return resp.content, resp.headers.get("content-type", "application/pdf")

    # ------------------------------------------------------------------
    # Documents — write
    # ------------------------------------------------------------------

    async def update_document(self, document_id: int, data: dict) -> dict:
        """Patch document metadata (title, correspondent, tags, etc)."""
        async with self._make_client() as client:
            resp = await client.patch(f"/api/documents/{document_id}/", json=data)
            resp.raise_for_status()
            return resp.json()

    async def update_custom_fields(self, document_id: int, custom_fields: list[dict]) -> None:
        """Update custom field values on a document (merge, not replace).

        Reads existing fields first to avoid overwriting unrelated ones.
        custom_fields format: [{"field": <field_id>, "value": <value>}, ...]
        """
        async with self._make_client() as client:
            resp = await client.get(f"/api/documents/{document_id}/")
            resp.raise_for_status()
            doc = resp.json()
            existing = doc.get("custom_fields", [])

            # Merge: keep existing, update/add ours
            field_map = {f["field"]: f["value"] for f in existing}
            for new_field in custom_fields:
                field_map[new_field["field"]] = new_field["value"]

            merged = [{"field": fid, "value": val} for fid, val in field_map.items()]
            resp = await client.patch(f"/api/documents/{document_id}/", json={"custom_fields": merged})
            resp.raise_for_status()

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    async def list_tags(self) -> list[dict]:
        """List all tags (paginated, returns full list)."""
        async with self._make_client() as client:
            return await self._paginate(client, "/api/tags/", {"page_size": 500})

    async def resolve_tag_names(self, tag_ids: list[int]) -> dict[int, str]:
        """Resolve tag IDs to names. Returns {id: name} mapping."""
        tags = await self.list_tags()
        return {t["id"]: t["name"] for t in tags if t["id"] in tag_ids}

    # ------------------------------------------------------------------
    # Correspondents
    # ------------------------------------------------------------------

    async def list_correspondents(self) -> list[dict]:
        """List all correspondents (paginated, returns full list)."""
        async with self._make_client() as client:
            return await self._paginate(client, "/api/correspondents/", {"page_size": 100})

    # ------------------------------------------------------------------
    # Custom fields
    # ------------------------------------------------------------------

    async def list_custom_fields(self) -> list[dict]:
        """List all defined custom field definitions."""
        async with self._make_client() as client:
            resp = await client.get("/api/custom_fields/")
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "results" in data:
                return data["results"]
            return data

    async def create_custom_field(self, field_def: dict) -> dict:
        """Create a new custom field definition."""
        async with self._make_client() as client:
            resp = await client.post("/api/custom_fields/", json=field_def)
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # Saved views
    # ------------------------------------------------------------------

    async def list_saved_views(self) -> list[dict]:
        """List all saved views."""
        async with self._make_client() as client:
            resp = await client.get("/api/saved_views/")
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "results" in data:
                return data["results"]
            return data

    # ------------------------------------------------------------------
    # Metadata helpers (fetch all correspondents + tags for enrichment)
    # ------------------------------------------------------------------

    async def fetch_all_metadata(
        self,
        *,
        on_progress: Optional[ProgressCallback] = None,
    ) -> tuple[dict[int, str], dict[int, str]]:
        """Fetch all correspondents and tags as {id: name} mappings.

        Useful for enriching document records with human-readable names.
        """
        async with self._make_client() as client:
            if on_progress:
                await on_progress("metadata", "Loading correspondents and tags...", 0, 0)

            correspondents: dict[int, str] = {}
            page = 1
            while True:
                data = await self._safe_list(client, "/api/correspondents/", page=page)
                for item in data.get("results", []):
                    correspondents[int(item["id"])] = item["name"]
                if not data.get("next"):
                    break
                page += 1

            tags: dict[int, str] = {}
            page = 1
            while True:
                data = await self._safe_list(client, "/api/tags/", page=page)
                for item in data.get("results", []):
                    tags[int(item["id"])] = item["name"]
                if not data.get("next"):
                    break
                page += 1

        return correspondents, tags

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _paginate(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        params: dict,
        *,
        on_progress: Optional[ProgressCallback] = None,
    ) -> list[dict]:
        """Paginate through all results for an endpoint."""
        results: list[dict] = []
        page = 1
        params = {**params}
        params.setdefault("page_size", 100)

        resp = await client.get(endpoint, params={**params, "page": page})
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("results", []))
        total = int(data.get("count", len(results)))

        if on_progress:
            await on_progress("fetching", "Fetching documents...", len(results), total)

        while data.get("next"):
            page += 1
            resp = await client.get(endpoint, params={**params, "page": page})
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("results", []))
            if on_progress:
                await on_progress("fetching", "Fetching documents...", len(results), total)

        return results

    async def _safe_list(self, client: httpx.AsyncClient, path: str, page: int = 1) -> dict:
        """Fetch a list endpoint, gracefully handling 403/404."""
        resp = await client.get(path, params={"page": page, "page_size": 100})
        if resp.status_code in {403, 404}:
            return {"count": 0, "results": [], "next": None, "access": "restricted"}
        resp.raise_for_status()
        payload = resp.json()
        payload.setdefault("access", "ok")
        return payload

    async def _resolve_tag_ids(self, client: httpx.AsyncClient, tag_names: list[str]) -> list[int]:
        """Resolve tag names to IDs."""
        resp = await client.get("/api/tags/", params={"page_size": 500})
        resp.raise_for_status()
        data = resp.json()
        tags = data["results"] if isinstance(data, dict) and "results" in data else data
        name_to_id = {tag["name"]: tag["id"] for tag in tags}
        return [name_to_id[name] for name in tag_names if name in name_to_id]

    async def _resolve_correspondent_id(self, client: httpx.AsyncClient, name: str) -> Optional[int]:
        """Resolve correspondent name to ID."""
        resp = await client.get("/api/correspondents/", params={"name__icontains": name})
        resp.raise_for_status()
        data = resp.json()
        results = data["results"] if isinstance(data, dict) and "results" in data else data
        return results[0]["id"] if results else None

    async def _resolve_document_type_id(self, client: httpx.AsyncClient, name: str) -> Optional[int]:
        """Resolve document type name to ID."""
        resp = await client.get("/api/document_types/", params={"name__icontains": name})
        resp.raise_for_status()
        data = resp.json()
        results = data["results"] if isinstance(data, dict) and "results" in data else data
        return results[0]["id"] if results else None


# Type alias for progress callbacks
ProgressCallback = Callable[[str, str, int, int], Coroutine[Any, Any, None]]


# ------------------------------------------------------------------
# Fixture loading (for tests and offline development)
# ------------------------------------------------------------------

def load_fixture(fixture_path: str) -> list[dict]:
    """Load documents from a JSON fixture file."""
    with Path(fixture_path).open("r", encoding="utf-8") as handle:
        return json.load(handle)
