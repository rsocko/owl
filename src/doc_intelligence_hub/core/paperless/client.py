"""Paperless-ngx async API client — shared across all Document Intelligence Hub modules.

Provides:
- Document CRUD (list, fetch, update, paginated queries)
- Tag resolution (name ↔ ID)
- Correspondent resolution
- Custom fields (list, create, update)
- Saved views
- Connection health check
- Fixture loading for tests
- Automatic retry with exponential backoff for transient errors
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import httpx

from doc_intelligence_hub.core.resilience import (
    CircuitOpenError,
    PaperlessError,
    get_circuit_breaker,
)

logger = logging.getLogger(__name__)

# HTTP methods that are safe to retry (idempotent)
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE", "OPTIONS"})

# Status codes that indicate transient server issues
_TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


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
        connect_timeout: float = 10.0,
        read_timeout: float | None = None,
        write_timeout: float = 10.0,
        pool_timeout: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        # Per-phase httpx timeouts. `asyncio.wait_for` around the overall
        # fetch is only a backstop — cancelling an asyncio task does not
        # reliably interrupt a blocking socket read/connect inside httpx, so
        # a stalled connect or a server that accepts the connection but never
        # sends a response body could otherwise hang well past the outer
        # timeout. Configuring these directly on the transport means httpx
        # itself raises (and fails fast) long before that backstop ever needs
        # to fire.
        self.connect_timeout = connect_timeout
        self.read_timeout = timeout if read_timeout is None else read_timeout
        self.write_timeout = write_timeout
        self.pool_timeout = pool_timeout
        # Reused across calls so we don't pay a fresh TCP/TLS handshake for
        # every single request (health check, per-tag lookups, per-document
        # content fetches, etc). Created lazily on first use.
        self._client: httpx.AsyncClient | None = None
        # Tag names rarely change within the lifetime of a single client, so
        # cache the name→id mapping instead of re-fetching /api/tags/ on
        # every list_documents()/get_documents_by_tags() call.
        self._tag_name_to_id_cache: dict[str, int] | None = None

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Token {self.token}",
                "Content-Type": "application/json",
            },
            verify=self.verify_ssl,
            timeout=httpx.Timeout(
                connect=self.connect_timeout,
                read=self.read_timeout,
                write=self.write_timeout,
                pool=self.pool_timeout,
            ),
            follow_redirects=True,
        )

    def _get_client(self) -> httpx.AsyncClient:
        """Return the shared AsyncClient, creating it on first use."""
        if self._client is None or self._client.is_closed:
            self._client = self._make_client()
        return self._client

    async def aclose(self) -> None:
        """Close the underlying HTTP client/connection pool, if open."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def __aenter__(self) -> PaperlessClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Resilient HTTP request helper
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        max_attempts: int = 3,
        **kwargs: Any,
    ) -> httpx.Response:
        """Execute an HTTP request with retry logic and circuit breaker.

        Retries on transient errors (timeouts, 429, 5xx) for idempotent
        methods. Non-idempotent methods (POST, PATCH) retry only on
        connection/timeout errors (not on 5xx, to avoid duplicate writes).
        """
        import asyncio

        client = self._get_client()
        breaker = get_circuit_breaker("paperless", failure_threshold=5, recovery_timeout=60.0)
        is_idempotent = method.upper() in _IDEMPOTENT_METHODS

        if not breaker.allow_request():
            raise CircuitOpenError(breaker)

        delay = 1.0
        last_exc: BaseException | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                resp = await client.request(method, path, **kwargs)
                # Don't retry client errors (4xx), only transient server errors
                if resp.status_code in _TRANSIENT_STATUS_CODES:
                    if attempt < max_attempts and (is_idempotent or resp.status_code == 429):
                        logger.warning(
                            "Paperless %s %s returned %d (attempt %d/%d), retrying in %.1fs",
                            method, path, resp.status_code, attempt, max_attempts, delay,
                        )
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, 30.0)
                        continue
                    # Transient error but not retrying — record as failure
                    breaker.record_failure()
                    return resp
                breaker.record_success()
                return resp
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_exc = e
                breaker.record_failure()
                if attempt >= max_attempts:
                    raise PaperlessError(
                        f"{method} {path} failed after {max_attempts} attempts: {e}",
                        status_code=None,
                    ) from e
                logger.warning(
                    "Paperless %s %s failed (attempt %d/%d): %s, retrying in %.1fs",
                    method, path, attempt, max_attempts, e, delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

        # Should not reach here
        raise PaperlessError(f"{method} {path} exhausted retries") from last_exc

    # ------------------------------------------------------------------
    # Health / connectivity
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Verify connectivity and return basic stats."""
        client = self._get_client()
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

    async def check_custom_fields(self) -> dict[str, Any]:
        """Check custom fields endpoint health — useful for diagnosing 500 errors."""
        client = self._get_client()
        resp = await client.get("/api/custom_fields/")
        if resp.status_code == 500:
            # Try to get error details from response body
            try:
                body = resp.text
            except Exception:
                body = "(could not read response body)"
            return {
                "status": "error",
                "http_status": 500,
                "detail": body[:500],
                "fix_hint": (
                    "Paperless custom_fields endpoint returns 500. "
                    "Check Paperless logs: docker logs paperless-ngx --tail 50. "
                    "Common cause: corrupt select field options in DB. "
                    "Fix: delete broken custom fields via Django shell or Paperless admin UI."
                ),
            }
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        return {
            "status": "ok",
            "count": len(results),
            "fields": [
                {"id": f.get("id"), "name": f.get("name"), "data_type": f.get("data_type")}
                for f in results
            ],
        }

    # ------------------------------------------------------------------
    # Documents — read
    # ------------------------------------------------------------------

    async def list_documents(
        self,
        *,
        tags: list[str] | None = None,
        query: str | None = None,
        correspondent: str | None = None,
        document_type: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        added_after: str | None = None,
        added_before: str | None = None,
        saved_view: int | None = None,
        page_size: int = 100,
        limit: int | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> list[dict]:
        """Fetch documents with flexible filtering.

        All filters are applied server-side via Paperless query params
        (``tags__id__in``, ``correspondent__id``, etc) — nothing is fetched
        and then discarded client-side.

        Args:
            page_size: Page size to request from the API (capped by Paperless).
            limit: Stop paginating once this many documents have been
                collected. Pass this whenever the caller only needs a handful
                of documents (e.g. ``limit=5``) so we don't walk thousands of
                pages for a large Paperless instance.

        Returns raw Paperless document dicts (id, title, correspondent, tags, etc).
        """
        client = self._get_client()
        effective_page_size = min(page_size, limit) if limit is not None else page_size
        params: dict[str, Any] = {"page_size": effective_page_size}

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

        return await self._paginate(
            client, "/api/documents/", params, limit=limit, on_progress=on_progress
        )

    async def list_documents_by_tag_ids(
        self, tag_ids: list[int], *, page_size: int = 100, limit: int | None = None
    ) -> list[dict]:
        """Fetch documents matching any of the given tag IDs.

        Issues a single server-side query using ``tags__id__in`` (OR
        semantics) instead of one query per tag ID — Paperless already
        deduplicates documents that match multiple tags within one query.
        """
        if not tag_ids:
            return []
        client = self._get_client()
        # When a limit is given, request exactly that page size (capped by
        # the caller-provided page_size) so _paginate's early-stop check
        # after page 1 is guaranteed to succeed and we never walk into a
        # second page — even when thousands of documents share these tags.
        effective_page_size = min(page_size, limit) if limit is not None else page_size
        params = {
            "tags__id__in": ",".join(str(t) for t in tag_ids),
            "page_size": effective_page_size,
        }
        return await self._paginate(client, "/api/documents/", params, limit=limit)

    async def get_document(self, document_id: int) -> dict:
        """Get full document metadata by ID."""
        resp = await self._request("GET", f"/api/documents/{document_id}/")
        resp.raise_for_status()
        return resp.json()

    async def get_document_content(self, document_id: int) -> str:
        """Get the OCR/text content of a document."""
        resp = await self._request("GET", f"/api/documents/{document_id}/")
        resp.raise_for_status()
        return resp.json().get("content", "")

    async def get_document_thumbnail(self, document_id: int) -> tuple[bytes, str]:
        """Get document thumbnail bytes and content-type."""
        resp = await self._request("GET", f"/api/documents/{document_id}/thumb/")
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type", "image/webp")

    async def get_document_preview(self, document_id: int) -> tuple[bytes, str]:
        """Get document PDF preview bytes and content-type."""
        resp = await self._request("GET", f"/api/documents/{document_id}/preview/")
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type", "application/pdf")

    # ------------------------------------------------------------------
    # Documents — write
    # ------------------------------------------------------------------

    async def update_document(self, document_id: int, data: dict) -> dict:
        """Patch document metadata (title, correspondent, tags, etc)."""
        resp = await self._request("PATCH", f"/api/documents/{document_id}/", json=data)
        resp.raise_for_status()
        return resp.json()

    async def update_custom_fields(self, document_id: int, custom_fields: list[dict]) -> None:
        """Update custom field values on a document (merge, not replace).

        Reads existing fields first to avoid overwriting unrelated ones.
        custom_fields format: [{"field": <field_id>, "value": <value>}, ...]
        """
        resp = await self._request("GET", f"/api/documents/{document_id}/")
        resp.raise_for_status()
        doc = resp.json()
        existing = doc.get("custom_fields", [])

        # Merge: keep existing, update/add ours
        field_map = {f["field"]: f["value"] for f in existing}
        for new_field in custom_fields:
            field_map[new_field["field"]] = new_field["value"]

        merged = [{"field": fid, "value": val} for fid, val in field_map.items()]
        resp = await self._request("PATCH", f"/api/documents/{document_id}/", json={"custom_fields": merged})
        resp.raise_for_status()

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    async def list_tags(self) -> list[dict]:
        """List all tags (paginated, returns full list)."""
        client = self._get_client()
        return await self._paginate(client, "/api/tags/", {"page_size": 500})

    async def resolve_tag_names(self, tag_ids: list[int]) -> dict[int, str]:
        """Resolve tag IDs to names. Returns {id: name} mapping."""
        tags = await self.list_tags()
        return {t["id"]: t["name"] for t in tags if t["id"] in tag_ids}

    async def resolve_tag_ids(self, tag_names: list[str]) -> list[int]:
        """Resolve tag names to IDs (cached — safe to call repeatedly)."""
        client = self._get_client()
        return await self._resolve_tag_ids(client, tag_names)

    async def remove_tags_from_document(self, document_id: int, tag_names: list[str]) -> dict:
        """Remove specific tags from a document by name.

        Fetches the document's current tag list, resolves the names to IDs,
        filters them out, and PATCHes the updated list back.
        Returns the updated document dict, or the original if no change was needed.
        """
        tag_ids_to_remove = await self.resolve_tag_ids(tag_names)
        if not tag_ids_to_remove:
            return await self.get_document(document_id)

        doc = await self.get_document(document_id)
        current_tags: list[int] = doc.get("tags", [])
        remove_set = set(tag_ids_to_remove)
        updated_tags = [t for t in current_tags if t not in remove_set]

        if len(updated_tags) == len(current_tags):
            return doc  # None of the target tags were present

        return await self.update_document(document_id, {"tags": updated_tags})

    # ------------------------------------------------------------------
    # Correspondents
    # ------------------------------------------------------------------

    async def list_correspondents(self) -> list[dict]:
        """List all correspondents (paginated, returns full list)."""
        client = self._get_client()
        return await self._paginate(client, "/api/correspondents/", {"page_size": 100})

    # ------------------------------------------------------------------
    # Custom fields
    # ------------------------------------------------------------------

    async def list_custom_fields(self) -> list[dict]:
        """List all defined custom field definitions."""
        client = self._get_client()
        resp = await client.get("/api/custom_fields/")
        if resp.status_code == 500:
            # Paperless may have internal issues with custom_fields endpoint;
            # try paginated format as fallback
            resp2 = await client.get("/api/custom_fields/?page=1&page_size=100")
            if resp2.status_code == 500:
                raise RuntimeError(
                    "Paperless /api/custom_fields/ returns 500. "
                    "This is likely a Paperless-side issue (DB migration or corrupt field). "
                    "Check Paperless container logs: docker logs paperless-ngx"
                )
            resp = resp2
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "results" in data:
            return data["results"]
        return data

    async def create_custom_field(self, field_def: dict) -> dict:
        """Create a new custom field definition."""
        client = self._get_client()
        resp = await client.post("/api/custom_fields/", json=field_def)
        resp.raise_for_status()
        return resp.json()

    async def update_custom_field(self, field_id: int, changes: dict) -> dict:
        """Patch an existing custom field definition."""
        client = self._get_client()
        resp = await client.patch(f"/api/custom_fields/{field_id}/", json=changes)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Saved views
    # ------------------------------------------------------------------

    async def list_saved_views(self) -> list[dict]:
        """List all saved views."""
        client = self._get_client()
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
        on_progress: ProgressCallback | None = None,
    ) -> tuple[dict[int, str], dict[int, str], dict[int, str]]:
        """Fetch all correspondents, tags, and document types as {id: name} mappings.

        Useful for enriching document records with human-readable names.
        """
        client = self._get_client()
        if on_progress:
            await on_progress(
                "metadata", "Loading correspondents, tags, and document types...", 0, 0
            )

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

        document_types: dict[int, str] = {}
        page = 1
        while True:
            data = await self._safe_list(client, "/api/document_types/", page=page)
            for item in data.get("results", []):
                document_types[int(item["id"])] = item["name"]
            if not data.get("next"):
                break
            page += 1

        return correspondents, tags, document_types

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _paginate(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        params: dict,
        *,
        limit: int | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> list[dict]:
        """Paginate through results for an endpoint, stopping early if limit is reached."""
        start = time.monotonic()
        results: list[dict] = []
        page = 1
        params = {**params}
        params.setdefault("page_size", 100)

        # If a limit is specified and smaller than page_size, reduce page_size to avoid over-fetching
        if limit is not None and limit < params["page_size"]:
            params["page_size"] = limit

        def _log_perf(pages_fetched: int, stopped_early: bool) -> None:
            logger.info(
                "Paperless fetch complete: endpoint=%s docs=%d pages=%d duration=%.2fs "
                "limit=%s stopped_early=%s",
                endpoint,
                len(results),
                pages_fetched,
                time.monotonic() - start,
                limit,
                stopped_early,
            )

        resp = await client.get(endpoint, params={**params, "page": page})
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("results", []))
        total = int(data.get("count", len(results)))

        if on_progress:
            await on_progress("fetching", "Fetching documents...", len(results), total)

        # Stop early if we've gathered enough results
        if limit is not None and len(results) >= limit:
            _log_perf(page, stopped_early=True)
            return results[:limit]

        while data.get("next"):
            page += 1
            resp = await client.get(endpoint, params={**params, "page": page})
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("results", []))
            if on_progress:
                await on_progress(
                    "fetching", "Fetching documents...", min(len(results), total), total
                )
            # Stop early if we've gathered enough results
            if limit is not None and len(results) >= limit:
                _log_perf(page, stopped_early=True)
                return results[:limit]

        _log_perf(page, stopped_early=False)
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
        """Resolve tag names to IDs, using a per-client cache (tags rarely change)."""
        name_to_id = await self._get_tag_name_to_id_map(client)
        return [name_to_id[name] for name in tag_names if name in name_to_id]

    async def _get_tag_name_to_id_map(self, client: httpx.AsyncClient) -> dict[str, int]:
        """Return the cached {name: id} tag mapping, fetching it once per client instance."""
        if self._tag_name_to_id_cache is None:
            resp = await client.get("/api/tags/", params={"page_size": 500})
            resp.raise_for_status()
            data = resp.json()
            tags = data["results"] if isinstance(data, dict) and "results" in data else data
            self._tag_name_to_id_cache = {tag["name"]: tag["id"] for tag in tags}
        return self._tag_name_to_id_cache

    async def _resolve_correspondent_id(self, client: httpx.AsyncClient, name: str) -> int | None:
        """Resolve correspondent name to ID."""
        resp = await client.get("/api/correspondents/", params={"name__icontains": name})
        resp.raise_for_status()
        data = resp.json()
        results = data["results"] if isinstance(data, dict) and "results" in data else data
        return results[0]["id"] if results else None

    async def _resolve_document_type_id(self, client: httpx.AsyncClient, name: str) -> int | None:
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
