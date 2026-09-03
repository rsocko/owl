"""Tests for the shared Paperless-ngx client."""

import json

import httpx
import pytest

from doc_intelligence_hub.core.paperless import PaperlessClient, load_fixture
from doc_intelligence_hub.core.resilience import (
    PaperlessError,
    reset_circuit_breakers,
)


@pytest.fixture(autouse=True)
def isolate_circuit_breakers():
    reset_circuit_breakers()
    yield
    reset_circuit_breakers()


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
        if request.url.path == "/api/documents/1/suggestions/":
            return httpx.Response(200, json={"correspondents": [20, 10], "tags": [3]})
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
async def test_resolve_tag_ids_is_case_insensitive(client):
    assert await client.resolve_tag_ids(["BILLS", "Monthly"]) == [1, 2]


@pytest.mark.asyncio
async def test_list_documents_filters_by_correspondent_id_without_name_lookup(monkeypatch):
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        return httpx.Response(200, json={"count": 0, "next": None, "results": []})

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    paperless = PaperlessClient(base_url="https://paperless.test", token="test-token")

    await paperless.list_documents(correspondent_id=42)

    assert len(requests_seen) == 1
    assert requests_seen[0].url.path == "/api/documents/"
    assert requests_seen[0].url.params["correspondent__id"] == "42"


@pytest.mark.asyncio
async def test_get_document(client):
    doc = await client.get_document(1)
    assert doc["title"] == "Doc A"


@pytest.mark.asyncio
async def test_get_document_suggestions(client):
    suggestions = await client.get_document_suggestions(1)
    assert suggestions["correspondents"] == [20, 10]


@pytest.mark.asyncio
async def test_resolve_correspondent_id_requires_exact_name(client):
    assert await client.resolve_correspondent_id(" acme ") == 10
    assert await client.resolve_correspondent_id("Ac") is None


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


def _make_saved_view_client(
    monkeypatch,
    definition,
    *,
    detail_status=200,
    detail_content=None,
    document_content=None,
):
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.url.path == "/api/saved_views/7/":
            if detail_content is not None:
                return httpx.Response(detail_status, content=detail_content)
            return httpx.Response(detail_status, json=definition)
        if request.url.path == "/api/documents/":
            if document_content is not None:
                return httpx.Response(200, content=document_content)
            if request.url.params.get("saved_view") == "7":
                return httpx.Response(
                    200,
                    json={
                        "count": 8868,
                        "next": None,
                        "results": [{"id": item} for item in range(8868)],
                    },
                )
            return httpx.Response(
                200,
                json={"count": 2, "next": None, "results": [{"id": 41}, {"id": 42}]},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    return PaperlessClient(base_url="https://paperless.test", token="test-token"), requests_seen


def _nested_custom_field_query(depth):
    query = ["Account Identifier", "exists", False]
    for _ in range(depth - 1):
        query = ["NOT", query]
    return query


@pytest.mark.asyncio
async def test_saved_view_rules_are_translated_before_querying_documents(monkeypatch):
    custom_field_query = json.dumps(
        [
            "OR",
            [
                ["Account Identifier", "exists", False],
                ["Account Identifier", "exact", ""],
                ["Account Identifier", "isnull", True],
            ],
        ]
    )
    definition = {
        "id": 7,
        "name": "Deployment-managed view",
        "filter_rules": [
            {"rule_type": 3, "value": None},
            {"rule_type": 4, "value": "14"},
            {"rule_type": 6, "value": "11,12"},
            {"rule_type": 6, "value": "13"},
            {"rule_type": 7, "value": "false"},
            {"rule_type": 17, "value": "21"},
            {"rule_type": 17, "value": "22"},
            {"rule_type": 20, "value": "added:[-30 day to now]"},
            {"rule_type": 25, "value": None},
            {"rule_type": 26, "value": "31"},
            {"rule_type": 26, "value": "32"},
            {"rule_type": 42, "value": custom_field_query},
        ],
    }
    client, requests_seen = _make_saved_view_client(monkeypatch, definition)

    documents = await client.list_documents(saved_view=7)

    assert [document["id"] for document in documents] == [41, 42]
    document_request = next(
        request for request in requests_seen if request.url.path == "/api/documents/"
    )
    assert "saved_view" not in document_request.url.params
    assert dict(document_request.url.params) == {
        "page_size": "100",
        "correspondent__isnull": "1",
        "document_type__id": "14",
        "is_tagged": "0",
        "query": "added:[-30 day to now]",
        "storage_path__isnull": "1",
        "tags__id__all": "11,12,13",
        "tags__id__none": "21,22",
        "correspondent__id__in": "31,32",
        "custom_field_query": custom_field_query,
        "page": "1",
    }


@pytest.mark.asyncio
async def test_saved_view_count_uses_one_bounded_server_side_query(monkeypatch):
    definition = {
        "id": 7,
        "name": "Missing correspondent",
        "filter_rules": [{"rule_type": 3, "value": None}],
    }
    client, requests_seen = _make_saved_view_client(monkeypatch, definition)

    count = await client.count_documents_for_saved_view(7)

    assert count == 2
    document_requests = [
        request for request in requests_seen if request.url.path == "/api/documents/"
    ]
    assert len(document_requests) == 1
    assert dict(document_requests[0].url.params) == {
        "page": "1",
        "page_size": "1",
        "correspondent__isnull": "1",
    }


@pytest.mark.asyncio
async def test_saved_view_count_normalizes_malformed_definition_json(monkeypatch):
    client, _ = _make_saved_view_client(
        monkeypatch,
        {},
        detail_content=b"{not-json",
    )

    with pytest.raises(PaperlessError, match="returned malformed JSON"):
        await client.count_documents_for_saved_view(7)


@pytest.mark.asyncio
async def test_saved_view_count_normalizes_malformed_document_json(monkeypatch):
    client, _ = _make_saved_view_client(
        monkeypatch,
        {"id": 7, "filter_rules": [{"rule_type": 3, "value": None}]},
        document_content=b"{not-json",
    )

    with pytest.raises(PaperlessError, match="document query returned malformed JSON"):
        await client.count_documents_for_saved_view(7)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        "not-json",
        json.dumps({"field": "Account Identifier"}),
        json.dumps(["XOR", [["Account Identifier", "exists", False]]]),
        json.dumps(["Account Identifier", "regex", "secret-value"]),
        json.dumps([7, "exists", False]),
        json.dumps(["Account Identifier", "exists", "false"]),
        json.dumps(["AND", []]),
        json.dumps(["NOT", [["Account Identifier", "exists", False]]]),
        json.dumps(float("nan")),
    ],
)
async def test_saved_view_invalid_custom_field_queries_fail_closed(monkeypatch, value):
    client, requests_seen = _make_saved_view_client(
        monkeypatch,
        {
            "id": 7,
            "name": "Unsafe custom field query",
            "filter_rules": [{"rule_type": 42, "value": value}],
        },
    )

    with pytest.raises(PaperlessError) as exc_info:
        await client.list_documents(saved_view=7)

    assert "secret-value" not in str(exc_info.value)
    assert not any(request.url.path == "/api/documents/" for request in requests_seen)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        json.dumps(_nested_custom_field_query(10)),
        json.dumps(["AND", [["Account Identifier", "exact", str(index)] for index in range(20)]]),
    ],
)
async def test_saved_view_custom_field_query_accepts_paperless_limits(monkeypatch, value):
    client, requests_seen = _make_saved_view_client(
        monkeypatch,
        {
            "id": 7,
            "name": "Bounded custom field query",
            "filter_rules": [{"rule_type": 42, "value": value}],
        },
    )

    await client.list_documents(saved_view=7)

    document_request = next(
        request for request in requests_seen if request.url.path == "/api/documents/"
    )
    assert document_request.url.params["custom_field_query"] == value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        json.dumps(["AND", [["Account Identifier", "exact", str(index)] for index in range(21)]]),
        json.dumps(_nested_custom_field_query(11)),
    ],
)
async def test_saved_view_oversized_custom_field_queries_fail_closed(monkeypatch, value):
    client, requests_seen = _make_saved_view_client(
        monkeypatch,
        {
            "id": 7,
            "name": "Oversized custom field query",
            "filter_rules": [{"rule_type": 42, "value": value}],
        },
    )

    with pytest.raises(PaperlessError):
        await client.list_documents(saved_view=7)

    assert not any(request.url.path == "/api/documents/" for request in requests_seen)


@pytest.mark.asyncio
async def test_saved_view_duplicate_custom_field_query_fails_closed(monkeypatch):
    value = json.dumps(["Account Identifier", "exists", False])
    client, requests_seen = _make_saved_view_client(
        monkeypatch,
        {
            "id": 7,
            "name": "Duplicate custom field query",
            "filter_rules": [
                {"rule_type": 42, "value": value},
                {"rule_type": 42, "value": value},
            ],
        },
    )

    with pytest.raises(PaperlessError, match="duplicates rule type 42"):
        await client.list_documents(saved_view=7)

    assert not any(request.url.path == "/api/documents/" for request in requests_seen)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filter_rules",
    [
        [],
        [{"rule_type": 999, "value": "1"}],
        [{"rule_type": 6, "value": ""}],
        [{"rule_type": 7, "value": "maybe"}],
        [{"rule_type": 20, "value": ""}],
        [{"rule_type": 3, "value": None}, {"rule_type": 3, "value": "4"}],
        ["not-a-rule"],
    ],
)
async def test_saved_view_malformed_or_unsupported_rules_fail_closed(monkeypatch, filter_rules):
    client, requests_seen = _make_saved_view_client(
        monkeypatch,
        {"id": 7, "name": "Unsafe view", "filter_rules": filter_rules},
    )

    with pytest.raises(PaperlessError):
        await client.list_documents(saved_view=7)

    assert not any(request.url.path == "/api/documents/" for request in requests_seen)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404])
async def test_saved_view_permission_or_missing_errors_fail_closed(monkeypatch, status):
    client, requests_seen = _make_saved_view_client(monkeypatch, {}, detail_status=status)

    with pytest.raises(httpx.HTTPStatusError):
        await client.list_documents(saved_view=7)

    assert not any(request.url.path == "/api/documents/" for request in requests_seen)


@pytest.mark.asyncio
async def test_saved_view_mismatched_id_fails_closed(monkeypatch):
    client, requests_seen = _make_saved_view_client(
        monkeypatch,
        {"id": 8, "name": "Wrong view", "filter_rules": [{"rule_type": 7, "value": "true"}]},
    )

    with pytest.raises(PaperlessError, match="mismatched"):
        await client.list_documents(saved_view=7)

    assert not any(request.url.path == "/api/documents/" for request in requests_seen)


@pytest.mark.asyncio
async def test_saved_view_nullable_not_null_value(monkeypatch):
    client, requests_seen = _make_saved_view_client(
        monkeypatch,
        {"id": 7, "name": "Has correspondent", "filter_rules": [{"rule_type": 3, "value": "-1"}]},
    )

    await client.list_documents(saved_view=7)

    document_request = next(
        request for request in requests_seen if request.url.path == "/api/documents/"
    )
    assert document_request.url.params.get("correspondent__isnull") == "0"


@pytest.mark.asyncio
async def test_saved_view_cannot_be_combined_with_explicit_filters(monkeypatch):
    client, requests_seen = _make_saved_view_client(
        monkeypatch,
        {"id": 7, "name": "View", "filter_rules": [{"rule_type": 7, "value": "true"}]},
    )

    with pytest.raises(PaperlessError, match="cannot be combined"):
        await client.list_documents(saved_view=7, correspondent="Acme")

    assert requests_seen == []


@pytest.mark.asyncio
async def test_list_saved_views_follows_pagination(monkeypatch):
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        page = request.url.params.get("page")
        if page == "1":
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "next": "https://paperless.test/api/saved_views/?page=2",
                    "results": [{"id": 1, "name": "First"}],
                },
            )
        return httpx.Response(
            200,
            json={"count": 2, "next": None, "results": [{"id": 2, "name": "Second"}]},
        )

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    client = PaperlessClient(base_url="https://paperless.test", token="test-token")

    views = await client.list_saved_views()

    assert [view["id"] for view in views] == [1, 2]
    assert len(requests_seen) == 2


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


@pytest.mark.asyncio
async def test_get_task_parses_real_paperless_response_shape(monkeypatch):
    """``GET /api/tasks/?task_id=`` always returns the paginated envelope.

    Fixture reproduces the exact shape confirmed live against a real
    Paperless-ngx instance: ``{"count", "next", "previous",
    "results": [...]}`` (never a bare top-level list), with lowercase
    ``status``/``status_display`` fields.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tasks/"
        assert request.url.params.get("task_id") == "11111111-1111-1111-1111-111111111111"
        return httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [
                    {
                        "id": 7,
                        "task_id": "11111111-1111-1111-1111-111111111111",
                        "task_type": "auto_task",
                        "status": "success",
                        "status_display": "Success",
                        "date_created": "2026-01-01T00:00:00Z",
                        "date_started": "2026-01-01T00:00:01Z",
                        "date_done": "2026-01-01T00:00:02Z",
                        "result_data": None,
                        "related_document_ids": [42],
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    client = PaperlessClient(base_url="https://test.local", token="t")

    task = await client.get_task("11111111-1111-1111-1111-111111111111")

    assert task is not None
    assert task["status"] == "success"
    assert task["related_document_ids"] == [42]


@pytest.mark.asyncio
async def test_get_task_returns_none_when_no_results(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 0, "next": None, "previous": None, "results": []})

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    client = PaperlessClient(base_url="https://test.local", token="t")

    assert await client.get_task("unknown-task-id") is None


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
