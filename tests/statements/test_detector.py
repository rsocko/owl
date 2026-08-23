from datetime import date
from pathlib import Path

import httpx
import pytest

from doc_intelligence_hub.modules.statements.config import load_config
from doc_intelligence_hub.modules.statements.detector import debug_discovery, discover_providers
from doc_intelligence_hub.modules.statements.models import DocumentRecord
from doc_intelligence_hub.modules.statements.paperless import (
    fetch_paperless_documents,
    load_fixture_documents,
)
from doc_intelligence_hub.modules.statements.paperless import (
    test_paperless_connection as run_paperless_connection_test,
)
from doc_intelligence_hub.modules.statements.service import (
    run_connection_test,
    validate_source_config,
)


def test_discover_monthly_providers() -> None:
    config = load_config("config/config.fixture.yaml")
    documents = load_fixture_documents(config.source.fixture_path)

    result = discover_providers(documents, config.analysis)
    provider_names = [provider.provider_name for provider in result.providers]

    assert result.analyzed_documents == 9
    assert "Chase Visa" in provider_names
    assert "City Utilities" in provider_names
    assert "Personal Notes" not in provider_names


def test_snapshot_fixture_exists() -> None:
    fixture = Path("tests/statements/fixtures/paperless_documents.json")
    assert fixture.exists()


def test_discover_providers_with_mixed_correspondent_documents() -> None:
    config = load_config("config/config.fixture.yaml")
    documents = [
        DocumentRecord(
            id=1,
            title="Chase Statement January 2026",
            correspondent_id=42,
            correspondent_name="Chase Visa",
            created=date(2026, 1, 3),
            tags=["statement"],
        ),
        DocumentRecord(
            id=2,
            title="Chase Statement February 2026",
            correspondent_id=42,
            correspondent_name="Chase Visa",
            created=date(2026, 2, 3),
            tags=["statement"],
        ),
        DocumentRecord(
            id=3,
            title="Chase Statement March 2026",
            correspondent_id=42,
            correspondent_name="Chase Visa",
            created=date(2026, 3, 3),
            tags=["statement"],
        ),
        DocumentRecord(
            id=4,
            title="Chase Rewards Summary",
            correspondent_id=42,
            correspondent_name="Chase Visa",
            created=date(2026, 3, 10),
            tags=["misc"],
        ),
        DocumentRecord(
            id=5,
            title="Chase Fraud Alert",
            correspondent_id=42,
            correspondent_name="Chase Visa",
            created=date(2026, 3, 11),
            tags=["misc"],
        ),
    ]

    result = discover_providers(documents, config.analysis)

    assert len(result.providers) == 1
    assert result.providers[0].provider_name == "Chase Visa"
    assert result.providers[0].statement_name == "Chase Statement"
    assert result.providers[0].normalized_title == "chase statement"
    assert result.providers[0].document_count == 3


def test_discover_providers_falls_back_to_title_when_correspondent_unknown() -> None:
    config = load_config("config/config.fixture.yaml")
    documents = [
        DocumentRecord(
            id=1,
            title="Gas Statement January 2026",
            correspondent_id=15,
            correspondent_name="Unknown",
            created=date(2026, 1, 10),
            tags=["statement"],
        ),
        DocumentRecord(
            id=2,
            title="Gas Statement February 2026",
            correspondent_id=15,
            correspondent_name="Unknown",
            created=date(2026, 2, 10),
            tags=["statement"],
        ),
        DocumentRecord(
            id=3,
            title="Gas Statement March 2026",
            correspondent_id=15,
            correspondent_name="Unknown",
            created=date(2026, 3, 10),
            tags=["statement"],
        ),
    ]

    result = discover_providers(documents, config.analysis)

    assert len(result.providers) == 1
    assert result.providers[0].provider_name == "Gas Statement"


def test_debug_discovery_reports_rejected_near_misses() -> None:
    config = load_config("config/config.fixture.yaml")
    documents = [
        DocumentRecord(
            id=1,
            title="Electric Bill January 2026",
            correspondent_id=90,
            correspondent_name="Utility Co",
            created=date(2026, 1, 5),
            tags=["bill"],
        ),
        DocumentRecord(
            id=2,
            title="Electric Bill February 2026",
            correspondent_id=90,
            correspondent_name="Utility Co",
            created=date(2026, 2, 5),
            tags=["bill"],
        ),
    ]

    result = debug_discovery(documents, config.analysis, limit=10)

    assert result.analyzed_documents == 2
    assert result.accepted_providers == 0
    assert result.groups[0].status == "rejected"
    assert result.groups[0].reason == "too_few_documents"


def test_discover_monthly_provider_with_missing_months() -> None:
    config = load_config("config/config.fixture.yaml")
    documents = [
        DocumentRecord(
            id=1,
            title="Checking Account Statement January 2024",
            correspondent_id=13,
            correspondent_name="Bank of America",
            created=date(2024, 1, 14),
            tags=["statement"],
        ),
        DocumentRecord(
            id=2,
            title="Checking Account Statement February 2024",
            correspondent_id=13,
            correspondent_name="Bank of America",
            created=date(2024, 2, 14),
            tags=["statement"],
        ),
        DocumentRecord(
            id=3,
            title="Checking Account Statement April 2024",
            correspondent_id=13,
            correspondent_name="Bank of America",
            created=date(2024, 4, 14),
            tags=["statement"],
        ),
        DocumentRecord(
            id=4,
            title="Checking Account Statement May 2024",
            correspondent_id=13,
            correspondent_name="Bank of America",
            created=date(2024, 5, 14),
            tags=["statement"],
        ),
    ]

    result = discover_providers(documents, config.analysis)

    assert len(result.providers) == 1
    assert result.providers[0].provider_name == "Bank of America"
    assert result.providers[0].pattern.frequency == "monthly"


def test_debug_discovery_rejects_sparse_long_running_groups() -> None:
    config = load_config("config/config.fixture.yaml")
    documents = [
        DocumentRecord(
            id=1,
            title="EOB Tracy",
            correspondent_id=11,
            correspondent_name="Unknown",
            created=date(2021, 1, 10),
            tags=["medical"],
            document_type="eob",
        ),
        DocumentRecord(
            id=2,
            title="EOB Tracy",
            correspondent_id=11,
            correspondent_name="Unknown",
            created=date(2021, 6, 10),
            tags=["medical"],
            document_type="eob",
        ),
        DocumentRecord(
            id=3,
            title="EOB Tracy",
            correspondent_id=11,
            correspondent_name="Unknown",
            created=date(2022, 2, 10),
            tags=["medical"],
            document_type="eob",
        ),
        DocumentRecord(
            id=4,
            title="EOB Tracy",
            correspondent_id=11,
            correspondent_name="Unknown",
            created=date(2023, 1, 10),
            tags=["medical"],
            document_type="eob",
        ),
    ]

    result = debug_discovery(documents, config.analysis, limit=10)

    assert result.groups[0].status == "rejected"
    assert result.groups[0].reason == "coverage_not_supported"


def test_discover_providers_with_document_type_mapping_does_not_suppress_keyword_matches() -> None:
    """Regression test: document type mapping should expand matches, never suppress keyword heuristics.

    When a mapping is saved but empty (all types disabled), documents whose
    document_type matches keywords like 'Statement' must still be discovered.
    """
    config = load_config("config/config.fixture.yaml")
    documents = [
        DocumentRecord(
            id=i,
            title=f"Chase Visa {date(2025, month, 15).strftime('%B %Y')}",
            correspondent_id=10,
            correspondent_name="Chase Visa",
            document_type="Statement",
            created=date(2025, month, 15),
            tags=["financial"],  # not in allowed_tags
        )
        for i, month in enumerate(range(1, 7), start=1)
    ]

    # Empty mapping — must NOT block keyword-based document_type matching
    config.analysis.enabled_document_type_names = set()
    result = discover_providers(documents, config.analysis)
    assert len(result.providers) == 1, f"Expected 1 provider, got {len(result.providers)}"
    assert result.providers[0].provider_name == "Chase Visa"

    # Mapping with a custom type should expand discovery to include it
    custom_docs = documents + [
        DocumentRecord(
            id=100 + i,
            title=f"Custom Report {date(2025, month, 15).strftime('%B %Y')}",
            correspondent_id=20,
            correspondent_name="Custom Co",
            document_type="Financial Report",
            created=date(2025, month, 15),
            tags=["financial"],
        )
        for i, month in enumerate(range(1, 7), start=1)
    ]
    config.analysis.enabled_document_type_names = {"Financial Report"}
    result = discover_providers(custom_docs, config.analysis)
    provider_names = {p.provider_name for p in result.providers}
    assert "Chase Visa" in provider_names, "Keyword-matched provider must still be found"
    assert "Custom Co" in provider_names, "Mapping-matched provider must be found"


def test_validate_source_config_requires_token_for_paperless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PAPERLESS_API_TOKEN", raising=False)
    config = load_config("config/config.paperless.example.yaml")

    with pytest.raises(ValueError, match="PAPERLESS_API_TOKEN"):
        validate_source_config(config)


@pytest.mark.asyncio
async def test_connection_test_supports_fixture_mode() -> None:
    result = await run_connection_test("config/config.fixture.yaml")

    assert result["status"] == "ok"
    assert result["mode"] == "fixture"
    assert result["documents"] == 9


@pytest.mark.asyncio
async def test_paperless_connection_allows_restricted_metadata_endpoints() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/api/documents/":
            return httpx.Response(200, json={"count": 3, "results": [], "next": None})
        if request.url.path in {"/api/correspondents/", "/api/tags/"}:
            return httpx.Response(403, json={"detail": "forbidden"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    try:
        result = await run_paperless_connection_test("https://paperless.example", "token")
    finally:
        monkeypatch.undo()

    assert result["status"] == "ok"
    assert result["documents"] == 3
    assert result["correspondents_access"] == "restricted"
    assert result["tags_access"] == "restricted"


@pytest.mark.asyncio
async def test_fetch_paperless_documents_allows_restricted_metadata_endpoints() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/correspondents/":
            return httpx.Response(403, json={"detail": "forbidden"})
        if request.url.path == "/api/tags/":
            return httpx.Response(403, json={"detail": "forbidden"})
        if request.url.path == "/api/documents/":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "next": None,
                    "results": [
                        {
                            "id": 1,
                            "title": "Statement April 2026",
                            "correspondent": 42,
                            "document_type": 10,
                            "created_date": "2026-04-03",
                            "added": "2026-04-04T08:00:00Z",
                            "tags": [7],
                            "original_file_name": "statement.pdf",
                        }
                    ],
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    try:
        documents = await fetch_paperless_documents("https://paperless.example", "token")
    finally:
        monkeypatch.undo()

    assert len(documents) == 1
    assert documents[0].correspondent_name == "Unknown"
    assert documents[0].tags == ["7"]


@pytest.mark.asyncio
async def test_fetch_paperless_documents_matches_string_and_integer_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/correspondents/":
            return httpx.Response(
                200,
                json={"count": 1, "results": [{"id": "15", "name": "National Grid"}], "next": None},
            )
        if request.url.path == "/api/tags/":
            return httpx.Response(
                200, json={"count": 1, "results": [{"id": "7", "name": "statement"}], "next": None}
            )
        if request.url.path == "/api/documents/":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "next": None,
                    "results": [
                        {
                            "id": 1,
                            "title": "Statement April 2026",
                            "correspondent": 15,
                            "document_type": 10,
                            "created_date": "2026-04-03",
                            "added": "2026-04-04T08:00:00Z",
                            "tags": [7],
                            "original_file_name": "statement.pdf",
                        }
                    ],
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    try:
        documents = await fetch_paperless_documents("https://paperless.example", "token")
    finally:
        monkeypatch.undo()

    assert len(documents) == 1
    assert documents[0].correspondent_name == "National Grid"
    assert documents[0].tags == ["statement"]
