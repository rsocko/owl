"""Paperless-ngx async API client — shared across all Document Intelligence Hub modules.

Provides:
- Document CRUD (list, fetch, update, paginated queries)
- Tag resolution (name ↔ ID)
- Correspondent resolution
- Custom fields (list, create, update)
- Saved views
- Document versions (upload/list/delete/label) — issue #18 slice 2
- Connection health check
- Fixture loading for tests
- Automatic retry with exponential backoff for transient errors
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from doc_intelligence_hub.core.resilience import (
    CircuitOpenError,
    PaperlessError,
    UnsupportedSavedViewError,
    get_circuit_breaker,
)

logger = logging.getLogger(__name__)

# HTTP methods that are safe to retry (idempotent)
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE", "OPTIONS"})

# Status codes that indicate transient server issues
_TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

_SAVED_VIEW_NULLABLE_ID_RULES = {
    3: ("correspondent__id", "correspondent__isnull"),
    4: ("document_type__id", "document_type__isnull"),
    25: ("storage_path__id", "storage_path__isnull"),
}
_SAVED_VIEW_MULTI_ID_RULES = {
    6: "tags__id__all",
    17: "tags__id__none",
    26: "correspondent__id__in",
}
_SAVED_VIEW_BOOLEAN_RULES = {7: "is_tagged"}
_SAVED_VIEW_STRING_RULES = {20: "query"}
_SAVED_VIEW_CUSTOM_FIELD_QUERY_RULE = 42
_CUSTOM_FIELD_QUERY_MAX_DEPTH = 10
_CUSTOM_FIELD_QUERY_MAX_ATOMS = 20
_CUSTOM_FIELD_QUERY_LOGICAL_OPERATORS = frozenset({"and", "or", "not"})
_CUSTOM_FIELD_QUERY_OPERATORS = frozenset(
    {
        "contains",
        "exact",
        "exists",
        "gt",
        "gte",
        "icontains",
        "iendswith",
        "in",
        "isnull",
        "istartswith",
        "lt",
        "lte",
        "range",
    }
)
_CUSTOM_FIELD_QUERY_DATE_COMPONENTS = frozenset(
    {
        "day",
        "iso_week_day",
        "iso_year",
        "month",
        "quarter",
        "week",
        "week_day",
        "year",
    }
)


@dataclass(frozen=True)
class PaperlessPage:
    """One bounded page from a Paperless list endpoint."""

    results: tuple[dict, ...]
    next_cursor: str | None
    total_count: int


def _equivalent_field_value(actual: Any, expected: Any, *, numeric: bool = False) -> bool:
    if actual == expected:
        return True
    if (
        numeric
        and isinstance(actual, (str, int, float))
        and isinstance(expected, (str, int, float))
    ):
        try:
            return Decimal(str(actual)) == Decimal(str(expected))
        except InvalidOperation:
            return False
    return False


def _saved_view_filter_params(filter_rules: Any) -> dict[str, Any]:
    """Translate supported Paperless saved-view rules into document query params."""
    if not isinstance(filter_rules, list) or not filter_rules:
        raise PaperlessError("Saved view has no usable filter rules")

    params: dict[str, Any] = {}
    multi_values: dict[str, list[str]] = {}
    single_rule_types: set[int] = set()
    for index, rule in enumerate(filter_rules):
        if not isinstance(rule, dict):
            raise PaperlessError(f"Saved view rule {index} is malformed")
        rule_type = rule.get("rule_type")
        if isinstance(rule_type, bool) or not isinstance(rule_type, int):
            raise PaperlessError(f"Saved view rule {index} has an invalid rule type")
        value = rule.get("value")

        if rule_type in _SAVED_VIEW_NULLABLE_ID_RULES:
            if rule_type in single_rule_types:
                raise PaperlessError(f"Saved view rule {index} duplicates rule type {rule_type}")
            single_rule_types.add(rule_type)
            filter_param, null_param = _SAVED_VIEW_NULLABLE_ID_RULES[rule_type]
            if value is None:
                target_param, rendered = null_param, 1
            elif isinstance(value, str) and value.strip() == "-1":
                target_param, rendered = null_param, 0
            else:
                target_param = filter_param
                rendered = _saved_view_positive_ids(value, index, allow_multiple=False)[0]
            if target_param in params:
                raise PaperlessError(f"Saved view rule {index} duplicates {target_param}")
            params[target_param] = rendered
            continue

        if rule_type in _SAVED_VIEW_MULTI_ID_RULES:
            target_param = _SAVED_VIEW_MULTI_ID_RULES[rule_type]
            multi_values.setdefault(target_param, []).extend(
                _saved_view_positive_ids(value, index, allow_multiple=True)
            )
            continue

        if rule_type in _SAVED_VIEW_BOOLEAN_RULES:
            target_param = _SAVED_VIEW_BOOLEAN_RULES[rule_type]
            if target_param in params:
                raise PaperlessError(f"Saved view rule {index} duplicates {target_param}")
            if isinstance(value, bool):
                rendered_bool = value
            elif isinstance(value, str) and value.strip().lower() in {"true", "1", "false", "0"}:
                rendered_bool = value.strip().lower() in {"true", "1"}
            else:
                raise PaperlessError(f"Saved view rule {index} has an invalid boolean value")
            params[target_param] = 1 if rendered_bool else 0
            continue

        if rule_type in _SAVED_VIEW_STRING_RULES:
            target_param = _SAVED_VIEW_STRING_RULES[rule_type]
            if target_param in params:
                raise PaperlessError(f"Saved view rule {index} duplicates {target_param}")
            if not isinstance(value, str) or not value.strip():
                raise PaperlessError(f"Saved view rule {index} has an invalid string value")
            params[target_param] = value.strip()
            continue

        if rule_type == _SAVED_VIEW_CUSTOM_FIELD_QUERY_RULE:
            target_param = "custom_field_query"
            if target_param in params:
                raise PaperlessError(f"Saved view rule {index} duplicates rule type {rule_type}")
            params[target_param] = _validated_custom_field_query(value, index)
            continue

        raise PaperlessError(f"Saved view rule type {rule_type} is not supported")

    params.update({key: ",".join(values) for key, values in multi_values.items()})
    return params


def _validated_custom_field_query(value: Any, rule_index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PaperlessError(f"Saved view rule {rule_index} has an invalid custom field query")

    try:
        expression = json.loads(
            value,
            parse_constant=lambda _constant: (_ for _ in ()).throw(ValueError()),
        )
    except (json.JSONDecodeError, ValueError):
        raise PaperlessError(
            f"Saved view rule {rule_index} has an invalid custom field query"
        ) from None

    atom_count = 0

    def validate(node: Any, depth: int) -> None:
        nonlocal atom_count
        if depth > _CUSTOM_FIELD_QUERY_MAX_DEPTH:
            raise PaperlessError(
                f"Saved view rule {rule_index} custom field query exceeds maximum depth"
            )
        if not isinstance(node, list):
            raise PaperlessError(
                f"Saved view rule {rule_index} has an invalid custom field query expression"
            )

        if len(node) == 2:
            logical_operator, operands = node
            if (
                not isinstance(logical_operator, str)
                or logical_operator.lower() not in _CUSTOM_FIELD_QUERY_LOGICAL_OPERATORS
            ):
                raise PaperlessError(
                    f"Saved view rule {rule_index} has an unsupported logical operator"
                )
            if logical_operator.lower() == "not":
                validate(operands, depth + 1)
                return
            if not isinstance(operands, list) or not operands:
                raise PaperlessError(
                    f"Saved view rule {rule_index} has an invalid custom field query expression"
                )
            for operand in operands:
                validate(operand, depth + 1)
            return

        if len(node) != 3:
            raise PaperlessError(
                f"Saved view rule {rule_index} has an invalid custom field query expression"
            )

        field_name, operator_name, operand = node
        if not isinstance(field_name, str) or not field_name:
            raise PaperlessError(
                f"Saved view rule {rule_index} custom field query requires a field name"
            )
        if not isinstance(operator_name, str):
            raise PaperlessError(
                f"Saved view rule {rule_index} has an unsupported custom field operator"
            )

        operator_parts = operator_name.split("__")
        if len(operator_parts) == 1:
            operator = operator_parts[0]
        elif len(operator_parts) == 2 and operator_parts[0] in _CUSTOM_FIELD_QUERY_DATE_COMPONENTS:
            operator = operator_parts[1]
        else:
            raise PaperlessError(
                f"Saved view rule {rule_index} has an unsupported custom field operator"
            )
        if operator not in _CUSTOM_FIELD_QUERY_OPERATORS:
            raise PaperlessError(
                f"Saved view rule {rule_index} has an unsupported custom field operator"
            )

        if operator in {"exists", "isnull"}:
            valid_operand = isinstance(operand, bool)
        elif operator in {"icontains", "iendswith", "istartswith"}:
            valid_operand = isinstance(operand, str)
        elif operator == "in":
            valid_operand = (
                isinstance(operand, list)
                and bool(operand)
                and all(_is_custom_field_query_scalar(item) for item in operand)
            )
        elif operator == "range":
            valid_operand = (
                isinstance(operand, list)
                and len(operand) == 2
                and all(_is_custom_field_query_scalar(item) for item in operand)
            )
        elif operator == "contains":
            valid_operand = isinstance(operand, list) and all(
                isinstance(item, int) and not isinstance(item, bool) for item in operand
            )
        else:
            valid_operand = _is_custom_field_query_scalar(operand)
        if not valid_operand:
            raise PaperlessError(
                f"Saved view rule {rule_index} has an invalid custom field query operand"
            )

        atom_count += 1
        if atom_count > _CUSTOM_FIELD_QUERY_MAX_ATOMS:
            raise PaperlessError(
                f"Saved view rule {rule_index} custom field query has too many conditions"
            )

    validate(expression, 1)
    return value


def _is_custom_field_query_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) and not (
        isinstance(value, float) and not (-float("inf") < value < float("inf"))
    )


def _saved_view_positive_ids(value: Any, rule_index: int, *, allow_multiple: bool) -> list[str]:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise PaperlessError(f"Saved view rule {rule_index} has an invalid ID value")
    parts = str(value).split(",") if allow_multiple else [str(value)]
    rendered: list[str] = []
    for part in parts:
        candidate = part.strip()
        if not candidate.isdigit() or int(candidate) <= 0:
            raise PaperlessError(f"Saved view rule {rule_index} has an invalid ID value")
        rendered.append(str(int(candidate)))
    return rendered


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
                # No static Content-Type here: httpx computes the right one
                # per request from `json=`/`files=`/`data=`, but a fixed
                # client-level header would win over that (httpx applies
                # explicit headers after auto-encoding request content), which
                # would silently corrupt multipart uploads (wrong boundary).
                "Authorization": f"Token {self.token}",
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

        Multipart (``files=``) requests are never retried on a transient
        server response (only on connection/timeout errors before any bytes
        were sent) regardless of ``method`` — re-POSTing a multipart upload
        after Paperless returned e.g. a 503 could otherwise create two
        upload attempts server-side. GET/HEAD/PUT/DELETE/OPTIONS remain
        idempotent-retryable as before.
        """
        import asyncio

        client = self._get_client()
        breaker = get_circuit_breaker("paperless", failure_threshold=5, recovery_timeout=60.0)
        is_multipart = "files" in kwargs
        is_idempotent = method.upper() in _IDEMPOTENT_METHODS and not is_multipart

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
                            method,
                            path,
                            resp.status_code,
                            attempt,
                            max_attempts,
                            delay,
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
                    method,
                    path,
                    attempt,
                    max_attempts,
                    e,
                    delay,
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
        correspondent_id: int | None = None,
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
            if any(
                (
                    tags,
                    query,
                    correspondent,
                    correspondent_id,
                    document_type,
                    created_after,
                    created_before,
                    added_after,
                    added_before,
                )
            ):
                raise PaperlessError("A saved view cannot be combined with explicit filters")
            view = await self.get_saved_view(saved_view)
            params.update(_saved_view_filter_params(view.get("filter_rules")))

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
            if correspondent_id is not None:
                raise PaperlessError("correspondent and correspondent_id cannot be combined")
            corr_id = await self._resolve_correspondent_id(client, correspondent)
            if corr_id:
                params["correspondent__id"] = corr_id
        elif correspondent_id is not None:
            if correspondent_id <= 0:
                raise PaperlessError("correspondent_id must be positive")
            params["correspondent__id"] = correspondent_id

        if document_type:
            type_id = await self._resolve_document_type_id(client, document_type)
            if type_id:
                params["document_type__id"] = type_id

        return await self._paginate(
            client, "/api/documents/", params, limit=limit, on_progress=on_progress
        )

    async def count_documents_for_saved_view(self, view_id: int) -> int:
        """Return Paperless's server-side count for one saved-view definition."""
        view = await self.get_saved_view(view_id)
        try:
            filter_params = _saved_view_filter_params(view.get("filter_rules"))
        except PaperlessError as exc:
            raise UnsupportedSavedViewError(str(exc)) from exc
        params = {
            "page": 1,
            "page_size": 1,
            **filter_params,
        }
        resp = await self._request("GET", "/api/documents/", params=params)
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError as exc:
            raise PaperlessError(
                f"Saved view {view_id} document query returned malformed JSON"
            ) from exc
        count = payload.get("count") if isinstance(payload, dict) else None
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise PaperlessError(f"Saved view {view_id} document query returned an invalid count")
        return count

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

    async def list_document_page(
        self,
        *,
        page_size: int = 100,
        cursor: str | None = None,
        scope_params: dict[str, Any] | None = None,
    ) -> PaperlessPage:
        """Fetch one deterministically ordered document page.

        The cursor is intentionally an opaque string to callers. It currently
        encodes the Paperless page number and can be persisted in protected
        restart state without retaining a deployment URL.

        ``scope_params`` are additional server-side query params (e.g.
        ``{"tags__id__in": "1,2"}``) applied on every page of a scoped,
        resumable scan. Pass the same ``scope_params`` on resume as on the
        original call — callers are responsible for detecting scope changes
        across a resume (e.g. via a digest of these params).
        """
        if page_size < 1:
            raise ValueError("page_size must be at least 1")
        try:
            page = int(cursor) if cursor is not None else 1
        except ValueError as exc:
            raise ValueError("Invalid Paperless pagination cursor") from exc
        if page < 1:
            raise ValueError("Invalid Paperless pagination cursor")

        params: dict[str, Any] = {"page": page, "page_size": page_size, "ordering": "id"}
        if scope_params:
            params.update(scope_params)

        resp = await self._request("GET", "/api/documents/", params=params)
        resp.raise_for_status()
        payload = resp.json()
        results = tuple(payload.get("results", ()))
        next_cursor = str(page + 1) if payload.get("next") else None
        return PaperlessPage(
            results=results,
            next_cursor=next_cursor,
            total_count=int(payload.get("count", len(results))),
        )

    async def iter_document_pages(
        self,
        *,
        page_size: int = 100,
        cursor: str | None = None,
        scope_params: dict[str, Any] | None = None,
    ) -> AsyncIterator[PaperlessPage]:
        """Yield bounded document pages from an optional restart cursor."""
        next_cursor = cursor
        while True:
            page = await self.list_document_page(
                page_size=page_size, cursor=next_cursor, scope_params=scope_params
            )
            yield page
            if page.next_cursor is None:
                return
            next_cursor = page.next_cursor

    async def get_document(self, document_id: int) -> dict:
        """Get full document metadata by ID."""
        resp = await self._request("GET", f"/api/documents/{document_id}/")
        resp.raise_for_status()
        return resp.json()

    async def get_document_suggestions(self, document_id: int) -> dict:
        """Get Paperless classifier suggestions for a document."""
        resp = await self._request("GET", f"/api/documents/{document_id}/suggestions/")
        resp.raise_for_status()
        suggestions = resp.json()
        if not isinstance(suggestions, dict):
            raise PaperlessError(
                f"Paperless suggestions for document {document_id} returned an invalid response"
            )
        return suggestions

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

    async def update_custom_field(self, field_id: int, data: dict) -> dict:
        """Patch a Paperless custom field definition."""
        resp = await self._request("PATCH", f"/api/custom_fields/{field_id}/", json=data)
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
        resp = await self._request(
            "PATCH", f"/api/documents/{document_id}/", json={"custom_fields": merged}
        )
        resp.raise_for_status()

    async def update_custom_fields_verified(
        self,
        document_id: int,
        custom_fields: list[dict],
        *,
        numeric_field_ids: set[int] | None = None,
    ) -> dict:
        """Merge custom fields and verify the requested values by reading them back."""
        numeric_field_ids = numeric_field_ids or set()
        await self.update_custom_fields(document_id, custom_fields)
        verified = await self.get_document(document_id)
        actual = {
            int(item["field"]): item.get("value")
            for item in verified.get("custom_fields", [])
            if isinstance(item, dict) and str(item.get("field", "")).isdigit()
        }
        expected = {int(item["field"]): item.get("value") for item in custom_fields}
        if any(
            not _equivalent_field_value(
                actual.get(field_id), value, numeric=field_id in numeric_field_ids
            )
            for field_id, value in expected.items()
        ):
            raise PaperlessError(
                f"Paperless custom-field verification failed for document {document_id}",
                status_code=None,
            )
        return verified

    # ------------------------------------------------------------------
    # Document versions — issue #18 slice 2 (apply/rollback an OCR candidate)
    #
    # Paperless's own document-version mechanism: a document can be
    # "merged in" as a version of another (root) document. The version
    # with the highest internal ordering is always what preview/download/
    # content resolve to as "latest" — there is no separate "promote"
    # call; rollback works by deleting the newer version(s) so the
    # previous one becomes latest again. Uploading via ``update_version``
    # with the root id set at consumption time means the result is never
    # a separate/duplicate top-level document.
    # ------------------------------------------------------------------

    async def upload_document_version(
        self,
        root_document_id: int,
        filename: str,
        content: bytes,
        *,
        version_label: str | None = None,
    ) -> str:
        """Upload ``content`` as a new version of ``root_document_id``.

        POSTs multipart to ``/api/documents/{root_document_id}/update_version/``.
        Paperless runs this through its normal (async) consume pipeline with
        the root id fixed at consumption time, so the result is attached as
        a version directly — never a separate top-level document. Returns
        the Celery task UUID string; poll it with :meth:`get_task`.
        """
        files = {"document": (filename, content, "application/pdf")}
        data = {"version_label": version_label} if version_label else {}
        resp = await self._request(
            "POST",
            f"/api/documents/{root_document_id}/update_version/",
            files=files,
            data=data,
            max_attempts=1,  # never auto-retry a multipart upload
        )
        resp.raise_for_status()
        task_id = resp.json()
        if not isinstance(task_id, str) or not task_id:
            raise PaperlessError(
                f"Paperless update_version for document {root_document_id} did not "
                f"return a task id: {task_id!r}",
                status_code=None,
            )
        return task_id

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Fetch one Paperless task's status by its Celery task id.

        Returns ``None`` if Paperless has no record of it (yet, or ever).

        Response shape: confirmed against paperless-ngx's current source
        (``documents/views.py::TasksViewSet``) — ``DEFAULT_VERSION`` is
        ``"10"`` and ``TasksViewSet.paginate_queryset`` only returns the
        legacy bare-list response when the request's API version is below
        10. This client never sends an ``Accept`` version override, so it
        always gets the default (10+) behavior: the standard paginated
        envelope ``{"count", "next", "previous", "results": [...]}``. The
        ``isinstance(dict)`` branch below handles that (real, expected) case;
        the plain-list branch is kept only as defense-in-depth for an older
        (<10, unsupported-by-this-client) Paperless deployment.
        """
        resp = await self._request("GET", "/api/tasks/", params={"task_id": task_id})
        resp.raise_for_status()
        results = resp.json()
        if isinstance(results, dict):
            results = results.get("results", [])
        if not results:
            return None
        return results[0]

    async def list_document_versions(self, root_document_id: int) -> list[dict[str, Any]]:
        """Return the root document's own entry plus every merged-in version.

        Each entry has ``id``, ``added``, ``checksum``, ``version_label``,
        and ``is_root`` (from Paperless's ``DocumentVersionInfoSerializer`).
        Order is not guaranteed to reflect recency — callers must not assume
        ``versions[0]`` is latest.
        """
        document = await self.get_document(root_document_id)
        versions = document.get("versions")
        if not versions:
            # Older Paperless without version support, or a document that
            # has never had a version merged in: treat the document itself
            # as its only "version" so callers have a uniform shape.
            return [
                {
                    "id": root_document_id,
                    "added": document.get("added"),
                    "checksum": document.get("checksum"),
                    "version_label": None,
                    "is_root": True,
                }
            ]
        return list(versions)

    async def delete_document_version(self, root_document_id: int, version_id: int) -> dict:
        """Delete one version of ``root_document_id``.

        Paperless rejects deleting the root/original version itself. On
        success Paperless auto-promotes whatever version now has the
        highest ordering to "latest" and returns its id as
        ``current_version_id`` — this is the rollback primitive.
        """
        resp = await self._request(
            "DELETE",
            f"/api/documents/{root_document_id}/versions/{version_id}/",
        )
        resp.raise_for_status()
        return resp.json()

    async def label_document_version(
        self, root_document_id: int, version_id: int, label: str
    ) -> dict:
        """Set a human-readable label on one version, for audit purposes."""
        resp = await self._request(
            "PATCH",
            f"/api/documents/{root_document_id}/versions/{version_id}/",
            json={"version_label": label},
        )
        resp.raise_for_status()
        return resp.json()

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

    async def resolve_correspondent_id(self, name: str) -> int | None:
        """Resolve an exact correspondent name to its Paperless ID."""
        normalized = name.strip().casefold()
        if not normalized:
            return None
        matches = [
            correspondent
            for correspondent in await self.list_correspondents()
            if str(correspondent.get("name", "")).strip().casefold() == normalized
        ]
        if len(matches) != 1:
            return None
        correspondent_id = matches[0].get("id")
        return (
            int(correspondent_id)
            if isinstance(correspondent_id, int) and not isinstance(correspondent_id, bool)
            else None
        )

    # ------------------------------------------------------------------
    # Custom fields
    # ------------------------------------------------------------------

    async def list_custom_fields(self) -> list[dict]:
        """List all custom-field definitions, following paginated responses."""
        page_size = 100
        resp = await self._request(
            "GET", "/api/custom_fields/", params={"page": 1, "page_size": page_size}
        )
        if resp.status_code == 500:
            resp2 = await self._request(
                "GET", "/api/custom_fields/", params={"page": 1, "page_size": page_size}
            )
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
            results = list(data["results"])
            page = 1
            while data.get("next"):
                page += 1
                resp = await self._request(
                    "GET",
                    "/api/custom_fields/",
                    params={"page": page, "page_size": page_size},
                )
                resp.raise_for_status()
                data = resp.json()
                results.extend(data.get("results", []))
            return results
        return data

    async def create_custom_field(self, field_def: dict) -> dict:
        """Create a new custom field definition."""
        client = self._get_client()
        resp = await client.post("/api/custom_fields/", json=field_def)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Saved views
    # ------------------------------------------------------------------

    async def list_saved_views(self) -> list[dict]:
        """List all saved views."""
        client = self._get_client()
        return await self._paginate(client, "/api/saved_views/", {"page_size": 100})

    async def get_saved_view(self, view_id: int) -> dict:
        """Fetch and validate one saved view by its unique Paperless ID."""
        if isinstance(view_id, bool) or not isinstance(view_id, int) or view_id <= 0:
            raise PaperlessError("Saved view ID must be a positive integer")
        resp = await self._request("GET", f"/api/saved_views/{view_id}/")
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as exc:
            raise PaperlessError(f"Saved view {view_id} returned malformed JSON") from exc
        if not isinstance(data, dict):
            raise PaperlessError(f"Saved view {view_id} returned a malformed definition")
        actual_id = data.get("id")
        if isinstance(actual_id, bool) or not isinstance(actual_id, (str, int)):
            raise PaperlessError(f"Saved view {view_id} returned a malformed definition")
        try:
            normalized_id = int(actual_id)
        except ValueError as exc:
            raise PaperlessError(f"Saved view {view_id} returned a malformed definition") from exc
        if normalized_id != view_id:
            raise PaperlessError(f"Saved view {view_id} returned a mismatched definition")
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
        normalized_name_to_id = {
            name.strip().casefold(): tag_id for name, tag_id in name_to_id.items()
        }
        return [
            normalized_name_to_id[normalized]
            for name in tag_names
            if (normalized := name.strip().casefold()) in normalized_name_to_id
        ]

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
