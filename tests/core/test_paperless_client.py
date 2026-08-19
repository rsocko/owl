"""Tests for the shared Paperless-ngx client."""

import httpx
import pytest

from doc_intelligence_hub.core.paperless import PaperlessClient, load_fixture
from doc_intelligence_hub.core.resilience import PaperlessError


def _mock_transport():
    """Create a mock transport for testing."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/api/documents/" and request.url.params.get("page_size") == "1":
            return httpx.Response(200, json={"count": 42, "results": [], "next": None})
        if request.url.path == "/api/documents/":
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "next": None,
                    "results": [
                        {
                            "id": 1,
                            "title": "Doc A",
                            "correspondent": 10,
                            "tags": [1, 2],
                            "created_date": "2026-01-01",
                        },
                        {
                            "id": 2,
                            "title": "Doc B",
                            "correspondent": 20,
                            "tags": [3],
                            "created_date": "2026-02-01",
                        },
                    ],
                },
            )
        if request.url.path == "/api/correspondents/":
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "results": [{"id": 10, "name": "Acme"}, {"id": 20, "name": "Globex"}],
                    "next": None,
                },
            )
        if request.url.path == "/api/tags/":
            return httpx.Response(
                200,
                json={
                    "count": 3,
                    "results": [
                        {"id": 1, "name": "bills"},
                        {"id": 2, "name": "monthly"},
                        {"id": 3, "name": "annual"},
                    ],
                    "next": None,
                },
            )
        if request.url.path == "/api/document_types/":
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "results": [{"id": 1, "name": "Statement"}, {"id": 2, "name": "Invoice"}],
                    "next": None,
                },
            )
        if request.url.path == "/api/documents/1/":
            return httpx.Response(
                200, json={"id": 1, "title": "Doc A", "content": "Hello world", "custom_fields": []}
            )
        if request.url.path == "/api/custom_fields/":
            return httpx.Response(
                200, json={"results": [{"id": 1, "name": "Status", "data_type": "string"}]}
            )
        if request.url.path == "/api/custom_fields/7/" and request.method == "PATCH":
            return httpx.Response(
                200,
                json={"id": 7, "name": "Action Status", "extra_data": {"select_options": []}},
            )
        if request.url.path == "/api/saved_views/":
            return httpx.Response(200, json={"results": [{"id": 1, "name": "Inbox"}]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def client(monkeypatch):
    """Create a PaperlessClient with mocked transport."""
    transport = _mock_transport()

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    return PaperlessClient(base_url="https://paperless.test", token="test-token")


@pytest.mark.asyncio
async def test_health_check(client):
    result = await client.health_check()
    assert result["status"] == "ok"
    assert result["documents"] == 42
    assert result["correspondents"] == 2
    assert result["tags"] == 3


@pytest.mark.asyncio
async def test_list_documents(client):
    docs = await client.list_documents()
    assert len(docs) == 2
    assert docs[0]["title"] == "Doc A"
    assert docs[1]["title"] == "Doc B"


@pytest.mark.asyncio
async def test_get_document(client):
    doc = await client.get_document(1)
    assert doc["title"] == "Doc A"


@pytest.mark.asyncio
async def test_get_document_content(client):
    content = await client.get_document_content(1)
    assert content == "Hello world"


@pytest.mark.asyncio
async def test_fetch_all_metadata(client):
    correspondents, tags, document_types = await client.fetch_all_metadata()
    assert correspondents == {10: "Acme", 20: "Globex"}
    assert tags == {1: "bills", 2: "monthly", 3: "annual"}
    assert document_types == {1: "Statement", 2: "Invoice"}


@pytest.mark.asyncio
async def test_list_custom_fields(client):
    fields = await client.list_custom_fields()
    assert len(fields) == 1
    assert fields[0]["name"] == "Status"


@pytest.mark.asyncio
async def test_update_custom_field(client):
    updated = await client.update_custom_field(
        7,
        {"extra_data": {"select_options": []}},
    )

    assert updated["id"] == 7
    assert updated["name"] == "Action Status"


@pytest.mark.asyncio
async def test_list_saved_views(client):
    views = await client.list_saved_views()
    assert len(views) == 1
    assert views[0]["name"] == "Inbox"


def test_load_fixture(tmp_path):
    fixture = tmp_path / "docs.json"
    fixture.write_text('[{"id": 1, "title": "Test"}]', encoding="utf-8")
    result = load_fixture(str(fixture))
    assert result == [{"id": 1, "title": "Test"}]


@pytest.mark.asyncio
async def test_string_and_int_id_handling(monkeypatch):
    """Ensure metadata IDs are normalized to int regardless of API response type."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/correspondents/":
            return httpx.Response(
                200, json={"count": 1, "results": [{"id": "5", "name": "StringID"}], "next": None}
            )
        if request.url.path == "/api/tags/":
            return httpx.Response(
                200, json={"count": 1, "results": [{"id": "9", "name": "string-tag"}], "next": None}
            )
        if request.url.path == "/api/document_types/":
            return httpx.Response(200, json={"count": 0, "results": [], "next": None})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    client = PaperlessClient(base_url="https://test.local", token="t")
    correspondents, tags, document_types = await client.fetch_all_metadata()

    # Keys should be int even when API returns string IDs
    assert correspondents[5] == "StringID"
    assert tags[9] == "string-tag"


def _make_client_with_pages(monkeypatch, num_docs: int, page_size: int = 100):
    """Build a PaperlessClient whose /api/documents/ endpoint is backed by a
    large, paginated result set — used to test limit/early-stop behavior and
    to assert on the query params Paperless actually receives.
    """
    all_docs = [{"id": i, "title": f"Doc {i}", "tags": [1]} for i in range(1, num_docs + 1)]
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.url.path == "/api/tags/":
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "results": [{"id": 1, "name": "Inbox"}, {"id": 2, "name": "Todo"}],
                    "next": None,
                },
            )
        if request.url.path == "/api/documents/":
            page = int(request.url.params.get("page", "1"))
            size = int(request.url.params.get("page_size", str(page_size)))
            start = (page - 1) * size
            end = start + size
            page_docs = all_docs[start:end]
            has_next = end < len(all_docs)
            return httpx.Response(
                200,
                json={
                    "count": len(all_docs),
                    "next": "http://x/next" if has_next else None,
                    "results": page_docs,
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    client = PaperlessClient(base_url="https://paperless.test", token="test-token")
    return client, requests_seen


class TestFetchWithLimit:
    """A `limit` should stop pagination early instead of walking every page."""

    @pytest.mark.asyncio
    async def test_list_documents_stops_at_limit(self, monkeypatch):
        client, requests_seen = _make_client_with_pages(monkeypatch, num_docs=500, page_size=100)

        docs = await client.list_documents(tags=["Inbox"], limit=5)

        assert len(docs) == 5
        # Only /api/tags/ (tag resolution) + a single /api/documents/ page — not
        # 5 pages worth of requests for a 500-document collection.
        doc_requests = [r for r in requests_seen if r.url.path == "/api/documents/"]
        assert len(doc_requests) == 1

    @pytest.mark.asyncio
    async def test_list_documents_by_tag_ids_single_query_with_limit(self, monkeypatch):
        client, requests_seen = _make_client_with_pages(monkeypatch, num_docs=500, page_size=100)

        docs = await client.list_documents_by_tag_ids([1, 2], limit=5)

        assert len(docs) == 5
        doc_requests = [r for r in requests_seen if r.url.path == "/api/documents/"]
        # A single tags__id__in query — not one request per tag ID.
        assert len(doc_requests) == 1
        assert doc_requests[0].url.params.get("tags__id__in") == "1,2"

    @pytest.mark.asyncio
    async def test_list_documents_without_limit_paginates_all_pages(self, monkeypatch):
        client, requests_seen = _make_client_with_pages(monkeypatch, num_docs=250, page_size=100)

        docs = await client.list_documents_by_tag_ids([1], page_size=100)

        assert len(docs) == 250
        doc_requests = [r for r in requests_seen if r.url.path == "/api/documents/"]
        assert len(doc_requests) == 3

    @pytest.mark.asyncio
    async def test_iter_document_pages_uses_restart_cursor_and_stable_ordering(self, monkeypatch):
        client, requests_seen = _make_client_with_pages(monkeypatch, num_docs=250, page_size=100)

        pages = [page async for page in client.iter_document_pages(page_size=100, cursor="2")]

        assert [len(page.results) for page in pages] == [100, 50]
        assert pages[0].next_cursor == "3"
        doc_requests = [r for r in requests_seen if r.url.path == "/api/documents/"]
        assert doc_requests[0].url.params.get("page") == "2"
        assert doc_requests[0].url.params.get("ordering") == "id"

    @pytest.mark.asyncio
    async def test_list_document_page_rejects_invalid_cursor(self, client):
        with pytest.raises(ValueError, match="cursor"):
            await client.list_document_page(cursor="not-a-page")

    @pytest.mark.asyncio
    async def test_list_documents_by_tag_ids_single_page_with_huge_collection(self, monkeypatch):
        """Even with 5000+ documents sharing the requested tags, a small
        `limit` must result in exactly one /api/documents/ request — no
        multi-page walk through the entire tagged collection.
        """
        client, requests_seen = _make_client_with_pages(monkeypatch, num_docs=5000, page_size=100)

        docs = await client.list_documents_by_tag_ids([1, 2], limit=5)

        assert len(docs) == 5
        doc_requests = [r for r in requests_seen if r.url.path == "/api/documents/"]
        assert len(doc_requests) == 1
        # page_size sent to Paperless should be capped to the limit, not the
        # default page_size, so the single page returned is exactly enough.
        assert doc_requests[0].url.params.get("page_size") == "5"
        assert doc_requests[0].url.params.get("page") == "1"


class TestServerSideFiltering:
    """Filters must be sent as Paperless query params, not applied client-side."""

    @pytest.mark.asyncio
    async def test_tags_use_tags_id_in_param(self, monkeypatch):
        client, requests_seen = _make_client_with_pages(monkeypatch, num_docs=3, page_size=100)

        await client.list_documents(tags=["Inbox", "Todo"])

        doc_requests = [r for r in requests_seen if r.url.path == "/api/documents/"]
        assert len(doc_requests) == 1
        assert doc_requests[0].url.params.get("tags__id__in") == "1,2"

    @pytest.mark.asyncio
    async def test_tag_name_to_id_lookup_is_cached(self, monkeypatch):
        client, requests_seen = _make_client_with_pages(monkeypatch, num_docs=3, page_size=100)

        await client.list_documents(tags=["Inbox"])
        await client.list_documents(tags=["Todo"])

        tag_requests = [r for r in requests_seen if r.url.path == "/api/tags/"]
        # Tag names rarely change — the second call should reuse the cached mapping.
        assert len(tag_requests) == 1


class TestHttpxTimeoutConfig:
    """A hung request must be bounded by real httpx-level timeouts, not just
    the outer asyncio.wait_for wrapper (task cancellation doesn't always
    interrupt a blocking socket read promptly).
    """

    @pytest.mark.asyncio
    async def test_make_client_configures_httpx_timeout(self):
        client = PaperlessClient(base_url="https://paperless.test", token="test-token")
        async_client = client._make_client()
        try:
            timeout = async_client.timeout
            assert isinstance(timeout, httpx.Timeout)
            assert timeout.connect == 10.0
            assert timeout.read == 30.0
            assert timeout.write == 10.0
            assert timeout.pool == 10.0
        finally:
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_make_client_honors_custom_timeout_overrides(self):
        client = PaperlessClient(
            base_url="https://paperless.test",
            token="test-token",
            connect_timeout=5.0,
            read_timeout=15.0,
            write_timeout=5.0,
            pool_timeout=5.0,
        )
        async_client = client._make_client()
        try:
            timeout = async_client.timeout
            assert timeout.connect == 5.0
            assert timeout.read == 15.0
            assert timeout.write == 5.0
            assert timeout.pool == 5.0
        finally:
            await async_client.aclose()

    @pytest.mark.asyncio
    async def test_make_client_read_timeout_defaults_to_timeout_param(self):
        client = PaperlessClient(
            base_url="https://paperless.test", token="test-token", timeout=45.0
        )
        async_client = client._make_client()
        try:
            assert async_client.timeout.read == 45.0
        finally:
            await async_client.aclose()


@pytest.mark.asyncio
async def test_list_custom_fields_follows_pagination(monkeypatch):
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        page = int(request.url.params.get("page", "1"))
        if page == 1:
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "results": [{"id": 1, "name": "Field A", "data_type": "string"}],
                    "next": "https://paperless.test/api/custom_fields/?page=2",
                },
            )
        return httpx.Response(
            200,
            json={
                "count": 2,
                "results": [{"id": 2, "name": "Field B", "data_type": "date"}],
                "next": None,
            },
        )

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    client = PaperlessClient(base_url="https://paperless.test", token="test-token")

    fields = await client.list_custom_fields()

    assert [field["id"] for field in fields] == [1, 2]
    assert len(requests_seen) == 2


@pytest.mark.asyncio
async def test_update_custom_fields_verified_detects_readback_mismatch(monkeypatch):
    get_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_count
        if request.url.path == "/api/documents/7/" and request.method == "GET":
            get_count += 1
            value = "before" if get_count == 1 else "unexpected"
            return httpx.Response(
                200,
                json={"id": 7, "custom_fields": [{"field": 2, "value": value}]},
            )
        if request.url.path == "/api/documents/7/" and request.method == "PATCH":
            return httpx.Response(200, json={"id": 7})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    client = PaperlessClient(base_url="https://paperless.test", token="test-token")

    with pytest.raises(PaperlessError, match="verification failed"):
        await client.update_custom_fields_verified(7, [{"field": 2, "value": "expected"}])


@pytest.mark.asyncio
async def test_update_custom_fields_verified_normalizes_numeric_readback(monkeypatch):
    get_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_count
        if request.url.path == "/api/documents/8/" and request.method == "GET":
            get_count += 1
            value = "0" if get_count == 1 else "12.50"
            return httpx.Response(
                200,
                json={"id": 8, "custom_fields": [{"field": 3, "value": value}]},
            )
        if request.url.path == "/api/documents/8/" and request.method == "PATCH":
            return httpx.Response(200, json={"id": 8})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    client = PaperlessClient(base_url="https://paperless.test", token="test-token")

    verified = await client.update_custom_fields_verified(
        8, [{"field": 3, "value": 12.5}], numeric_field_ids={3}
    )
    assert verified["custom_fields"][0]["value"] == "12.50"


@pytest.mark.asyncio
async def test_update_custom_fields_verified_requires_exact_text_readback(monkeypatch):
    get_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_count
        if request.url.path == "/api/documents/9/" and request.method == "GET":
            get_count += 1
            value = "old" if get_count == 1 else "123"
            return httpx.Response(
                200,
                json={"id": 9, "custom_fields": [{"field": 4, "value": value}]},
            )
        if request.url.path == "/api/documents/9/" and request.method == "PATCH":
            return httpx.Response(200, json={"id": 9})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    client = PaperlessClient(base_url="https://paperless.test", token="test-token")

    with pytest.raises(PaperlessError, match="verification failed"):
        await client.update_custom_fields_verified(9, [{"field": 4, "value": "00123"}])


class TestFetchPerformanceLogging:
    """_paginate should log fetch duration/page/doc-count metrics."""

    @pytest.mark.asyncio
    async def test_paginate_logs_metrics(self, monkeypatch):
        client, _ = _make_client_with_pages(monkeypatch, num_docs=10, page_size=100)

        # Attach a handler directly to the client module's logger rather than
        # relying on caplog/root propagation — `configure_logging()` (invoked
        # by the FastAPI app elsewhere in the suite) sets
        # `doc_intelligence_hub.propagate = False`, which would otherwise
        # prevent caplog's root-attached handler from ever seeing these records.
        import logging as _logging

        records: list[_logging.LogRecord] = []

        class _ListHandler(_logging.Handler):
            def emit(self, record):
                records.append(record)

        target_logger = _logging.getLogger("doc_intelligence_hub.core.paperless.client")
        handler = _ListHandler(level=_logging.INFO)
        target_logger.addHandler(handler)
        previous_level = target_logger.level
        target_logger.setLevel(_logging.INFO)
        try:
            await client.list_documents(tags=["Inbox"], limit=5)
        finally:
            target_logger.removeHandler(handler)
            target_logger.setLevel(previous_level)

        messages = [r.getMessage() for r in records]
        assert any("Paperless fetch complete" in m for m in messages)
        assert any("stopped_early=True" in m for m in messages)
