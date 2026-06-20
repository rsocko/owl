"""Tests for the shared Paperless-ngx client."""

import pytest
import httpx

from doc_intelligence_hub.core.paperless import PaperlessClient, load_fixture


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
                        {"id": 1, "title": "Doc A", "correspondent": 10, "tags": [1, 2], "created_date": "2026-01-01"},
                        {"id": 2, "title": "Doc B", "correspondent": 20, "tags": [3], "created_date": "2026-02-01"},
                    ],
                },
            )
        if request.url.path == "/api/correspondents/":
            return httpx.Response(
                200,
                json={"count": 2, "results": [{"id": 10, "name": "Acme"}, {"id": 20, "name": "Globex"}], "next": None},
            )
        if request.url.path == "/api/tags/":
            return httpx.Response(
                200,
                json={
                    "count": 3,
                    "results": [{"id": 1, "name": "bills"}, {"id": 2, "name": "monthly"}, {"id": 3, "name": "annual"}],
                    "next": None,
                },
            )
        if request.url.path == "/api/documents/1/":
            return httpx.Response(200, json={"id": 1, "title": "Doc A", "content": "Hello world", "custom_fields": []})
        if request.url.path == "/api/custom_fields/":
            return httpx.Response(200, json={"results": [{"id": 1, "name": "Status", "data_type": "string"}]})
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
    correspondents, tags = await client.fetch_all_metadata()
    assert correspondents == {10: "Acme", 20: "Globex"}
    assert tags == {1: "bills", 2: "monthly", 3: "annual"}


@pytest.mark.asyncio
async def test_list_custom_fields(client):
    fields = await client.list_custom_fields()
    assert len(fields) == 1
    assert fields[0]["name"] == "Status"


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
            return httpx.Response(200, json={"count": 1, "results": [{"id": "5", "name": "StringID"}], "next": None})
        if request.url.path == "/api/tags/":
            return httpx.Response(200, json={"count": 1, "results": [{"id": "9", "name": "string-tag"}], "next": None})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    client = PaperlessClient(base_url="https://test.local", token="t")
    correspondents, tags = await client.fetch_all_metadata()

    # Keys should be int even when API returns string IDs
    assert correspondents[5] == "StringID"
    assert tags[9] == "string-tag"
