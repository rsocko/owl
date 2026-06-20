"""End-to-end smoke tests for statement-tracker.

These tests exercise the full pipeline (fetch → discover → recommend)
against a realistic mock Paperless-ngx API with multi-page pagination,
multiple providers, and mixed document types.

An optional live test is gated behind the PAPERLESS_API_TOKEN env var.
"""

from __future__ import annotations

import json
import math
import os
from datetime import date, timedelta
from pathlib import Path

import dotenv
import httpx
import pytest

# Load .env early so module-level skip conditions can see the token
dotenv.load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from doc_intelligence_hub.modules.statements.config import load_config
from doc_intelligence_hub.modules.statements.models import DocumentRecord
from doc_intelligence_hub.modules.statements.service import load_documents, run_connection_test, run_discovery, run_recommendations


# ---------------------------------------------------------------------------
# Realistic mock data: 5 providers, 60+ documents, noise, mixed frequencies
# ---------------------------------------------------------------------------

def _monthly_docs(
    correspondent_id: int,
    correspondent_name: str,
    title_template: str,
    start: date,
    count: int,
    anchor_day: int,
    *,
    id_base: int,
    tags: list[str] | None = None,
    doc_type: str = "statement",
) -> list[dict]:
    """Generate a series of monthly documents."""
    docs = []
    current = date(start.year, start.month, anchor_day)
    for i in range(count):
        month_names = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
        title = title_template.format(month=month_names[current.month - 1], year=current.year)
        docs.append({
            "id": id_base + i,
            "title": title,
            "correspondent": correspondent_id,
            "document_type": doc_type,
            "created_date": current.isoformat(),
            "added": f"{current.isoformat()}T08:00:00Z",
            "tags": tags or [1],
            "original_file_name": f"doc_{id_base + i}.pdf",
        })
        # Advance one month
        month = current.month % 12 + 1
        year = current.year + (1 if current.month == 12 else 0)
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        current = date(year, month, min(anchor_day, last_day))
    return docs


def _quarterly_docs(
    correspondent_id: int,
    title_template: str,
    start_year: int,
    count: int,
    anchor_day: int,
    *,
    id_base: int,
    tags: list[str] | None = None,
) -> list[dict]:
    """Generate a series of quarterly documents."""
    docs = []
    quarters = [(1, "Q1"), (4, "Q2"), (7, "Q3"), (10, "Q4")]
    idx = 0
    year = start_year
    q_idx = 0
    while idx < count:
        month, q_label = quarters[q_idx]
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        day = min(anchor_day, last_day)
        title = title_template.format(quarter=q_label, year=year)
        docs.append({
            "id": id_base + idx,
            "title": title,
            "correspondent": correspondent_id,
            "document_type": "statement",
            "created_date": date(year, month, day).isoformat(),
            "added": f"{date(year, month, day).isoformat()}T08:00:00Z",
            "tags": tags or [1],
            "original_file_name": f"doc_{id_base + idx}.pdf",
        })
        idx += 1
        q_idx += 1
        if q_idx >= 4:
            q_idx = 0
            year += 1
    return docs


def _annual_docs(
    correspondent_id: int,
    title_template: str,
    start_year: int,
    count: int,
    month: int,
    day: int,
    *,
    id_base: int,
    tags: list[str] | None = None,
) -> list[dict]:
    """Generate a series of annual documents."""
    docs = []
    for i in range(count):
        year = start_year + i
        title = title_template.format(year=year)
        docs.append({
            "id": id_base + i,
            "title": title,
            "correspondent": correspondent_id,
            "document_type": "statement",
            "created_date": date(year, month, day).isoformat(),
            "added": f"{date(year, month, day).isoformat()}T08:00:00Z",
            "tags": tags or [1],
            "original_file_name": f"doc_{id_base + i}.pdf",
        })
    return docs


def _noise_docs(*, id_base: int, count: int = 10) -> list[dict]:
    """Generate non-statement noise documents."""
    titles = [
        "Grocery Receipt", "Vacation Photos Index", "Meeting Notes",
        "Tax Return Draft", "Appliance Manual", "Insurance Card",
        "Pet Vaccination Record", "Recipe Collection", "Travel Itinerary",
        "Gift Ideas", "Home Renovation Plan", "Car Maintenance Log",
    ]
    docs = []
    for i in range(count):
        docs.append({
            "id": id_base + i,
            "title": titles[i % len(titles)],
            "correspondent": 99,
            "document_type": "note",
            "created_date": date(2025, 1 + (i % 12), 5).isoformat(),
            "added": f"{date(2025, 1 + (i % 12), 5).isoformat()}T10:00:00Z",
            "tags": [5],
            "original_file_name": f"noise_{id_base + i}.pdf",
        })
    return docs


CORRESPONDENTS = {
    10: "Chase Visa",
    20: "National Grid",
    30: "Vanguard",
    40: "State Farm",
    50: "City Water",
    99: "Personal",
}

TAGS = {
    1: "statement",
    2: "bill",
    3: "invoice",
    4: "insurance",
    5: "personal",
}

# Build the full document set
_ALL_DOCS: list[dict] = []
_ALL_DOCS.extend(_monthly_docs(10, "Chase Visa", "Chase Statement {month} {year}", date(2025, 1, 3), 15, 3, id_base=1000, tags=[1]))
_ALL_DOCS.extend(_monthly_docs(20, "National Grid", "National Grid Bill {month} {year}", date(2025, 3, 15), 12, 15, id_base=2000, tags=[2], doc_type="bill"))
_ALL_DOCS.extend(_quarterly_docs(30, "Vanguard Statement {quarter} {year}", 2024, 8, 1, id_base=3000, tags=[1]))
_ALL_DOCS.extend(_annual_docs(40, "State Farm Policy Renewal {year}", 2021, 5, 3, 15, id_base=4000, tags=[4, 1]))
_ALL_DOCS.extend(_monthly_docs(50, "City Water", "City Water Bill {month} {year}", date(2025, 6, 20), 6, 20, id_base=5000, tags=[2], doc_type="bill"))
_ALL_DOCS.extend(_noise_docs(id_base=9000, count=10))

PAGE_SIZE = 25


def _build_mock_handler():
    """Build an httpx mock handler simulating a real Paperless-ngx API."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rstrip("/")
        params = dict(request.url.params)

        if path == "/api":
            return httpx.Response(200, json={"status": "ok"})

        if path == "/api/correspondents":
            page = int(params.get("page", 1))
            items = list(CORRESPONDENTS.items())
            page_size = int(params.get("page_size", 100))
            start = (page - 1) * page_size
            end = start + page_size
            results = [{"id": cid, "name": name} for cid, name in items[start:end]]
            has_next = end < len(items)
            return httpx.Response(200, json={
                "count": len(items),
                "results": results,
                "next": f"http://test/api/correspondents/?page={page + 1}" if has_next else None,
            })

        if path == "/api/tags":
            items = list(TAGS.items())
            results = [{"id": tid, "name": name} for tid, name in items]
            return httpx.Response(200, json={
                "count": len(items),
                "results": results,
                "next": None,
            })

        if path == "/api/documents":
            page = int(params.get("page", 1))
            page_size = int(params.get("page_size", PAGE_SIZE))
            total = len(_ALL_DOCS)
            start = (page - 1) * page_size
            end = min(start + page_size, total)
            results = _ALL_DOCS[start:end]
            total_pages = math.ceil(total / page_size)
            has_next = page < total_pages
            return httpx.Response(200, json={
                "count": total,
                "results": results,
                "next": f"http://test/api/documents/?page={page + 1}" if has_next else None,
            })

        return httpx.Response(404)

    return handler


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------

_OriginalAsyncClient = httpx.AsyncClient


def _mock_client_class():
    """Create a MockAsyncClient that injects the mock transport."""
    transport = httpx.MockTransport(_build_mock_handler())

    class MockAsyncClient(_OriginalAsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    return MockAsyncClient


@pytest.mark.asyncio
async def test_smoke_fetch_documents_with_pagination():
    """Verify that multi-page document fetching works correctly."""
    from doc_intelligence_hub.modules.statements.paperless import fetch_paperless_documents

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_class())

    try:
        documents = await fetch_paperless_documents("http://test", "test-token")
    finally:
        monkeypatch.undo()

    assert len(documents) == len(_ALL_DOCS)
    # Verify correspondents were resolved
    chase_docs = [d for d in documents if d.correspondent_name == "Chase Visa"]
    assert len(chase_docs) == 15
    # Verify tags were resolved
    statement_tagged = [d for d in documents if "statement" in d.tags]
    assert len(statement_tagged) > 0


@pytest.mark.asyncio
async def test_smoke_connection_test_with_mock_paperless(tmp_path, monkeypatch):
    """Verify connection test works against mock Paperless API."""
    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_class())
    monkeypatch.setenv("PAPERLESS_API_TOKEN", "test-token")

    result = await run_connection_test("config/config.paperless.example.yaml")

    assert result["status"] == "ok"
    assert result["mode"] == "paperless"
    assert result["documents"] == len(_ALL_DOCS)
    assert result["correspondents"] > 0
    assert result["tags"] > 0


@pytest.mark.asyncio
async def test_smoke_discovery_pipeline_finds_all_provider_types(tmp_path, monkeypatch):
    """End-to-end: fetch → discover providers across monthly/quarterly/annual."""
    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_class())
    monkeypatch.setenv("PAPERLESS_API_TOKEN", "test-token")

    # Write snapshot to tmp to avoid polluting data/
    config = load_config("config/config.paperless.example.yaml")
    snapshot_path = str(tmp_path / "snapshot.json")
    config.runtime.snapshot_path = snapshot_path

    documents = await load_documents(config)
    from doc_intelligence_hub.modules.statements.detector import discover_providers
    result = discover_providers(documents, config.analysis)

    provider_names = {p.provider_name for p in result.providers}
    frequencies = {p.pattern.frequency for p in result.providers}

    # Should find monthly providers
    assert "Chase Visa" in provider_names
    assert "National Grid" in provider_names
    assert "monthly" in frequencies

    # Noise should be excluded
    assert "Personal" not in provider_names

    # Total analyzed should match all docs
    assert result.analyzed_documents == len(_ALL_DOCS)


@pytest.mark.asyncio
async def test_smoke_recommendations_pipeline_end_to_end(tmp_path, monkeypatch):
    """End-to-end: fetch → discover → recommend missing statements."""
    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_class())
    monkeypatch.setenv("PAPERLESS_API_TOKEN", "test-token")

    config = load_config("config/config.paperless.example.yaml")
    config.runtime.snapshot_path = str(tmp_path / "snapshot.json")

    documents = await load_documents(config)
    from doc_intelligence_hub.modules.statements.detector import discover_providers
    from doc_intelligence_hub.modules.statements.recommendations import build_recommendations

    discovery = discover_providers(documents, config.analysis)
    # Check as of a date well past the latest documents
    recommendations = build_recommendations(
        discovery.providers,
        date(2026, 6, 15),
        max_inactive_cycles=config.analysis.max_inactive_cycles_for_recommendations,
    )

    assert len(recommendations.recommendations) > 0
    # All recommendations should be for known providers
    known_providers = {p.provider_name for p in discovery.providers}
    for rec in recommendations.recommendations:
        assert rec.provider_name in known_providers
    # Should have overdue or missing items
    statuses = {r.status for r in recommendations.recommendations}
    assert statuses & {"missing", "overdue"}


@pytest.mark.asyncio
async def test_smoke_api_discovery_endpoint_with_mock(tmp_path, monkeypatch):
    """Verify the FastAPI discovery endpoint works end-to-end."""
    from fastapi.testclient import TestClient
    from doc_intelligence_hub.modules.statements.api import create_app

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_class())
    monkeypatch.setenv("PAPERLESS_API_TOKEN", "test-token")

    # Patch snapshot path to tmp
    original_load = load_config.__wrapped__ if hasattr(load_config, "__wrapped__") else None

    from doc_intelligence_hub.modules.statements import config as config_mod
    _original_load = config_mod.load_config

    def patched_load(path):
        cfg = _original_load(path)
        cfg.runtime.snapshot_path = str(tmp_path / "snapshot.json")
        return cfg

    monkeypatch.setattr(config_mod, "load_config", patched_load)
    monkeypatch.setattr("doc_intelligence_hub.modules.statements.service.load_config", patched_load)

    client = TestClient(create_app("config/config.paperless.example.yaml"))

    response = client.post("/api/discovery/run")
    assert response.status_code == 200
    payload = response.json()
    assert payload["analyzed_documents"] == len(_ALL_DOCS)
    assert len(payload["providers"]) > 0


@pytest.mark.asyncio
async def test_smoke_api_recommendations_endpoint_with_mock(tmp_path, monkeypatch):
    """Verify the FastAPI recommendations endpoint works end-to-end."""
    from fastapi.testclient import TestClient
    from doc_intelligence_hub.modules.statements.api import create_app
    from doc_intelligence_hub.modules.statements import config as config_mod

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_class())
    monkeypatch.setenv("PAPERLESS_API_TOKEN", "test-token")

    _original_load = config_mod.load_config

    def patched_load(path):
        cfg = _original_load(path)
        cfg.runtime.snapshot_path = str(tmp_path / "snapshot.json")
        return cfg

    monkeypatch.setattr(config_mod, "load_config", patched_load)
    monkeypatch.setattr("doc_intelligence_hub.modules.statements.service.load_config", patched_load)

    client = TestClient(create_app("config/config.paperless.example.yaml"))

    response = client.post("/api/recommendations/run?as_of=2026-06-15")
    assert response.status_code == 200
    payload = response.json()
    assert payload["as_of"] == "2026-06-15"
    assert len(payload["recommendations"]) > 0


@pytest.mark.asyncio
async def test_smoke_snapshot_written_on_discovery(tmp_path, monkeypatch):
    """Verify that a JSON snapshot is written after discovery."""
    monkeypatch.setattr(httpx, "AsyncClient", _mock_client_class())
    monkeypatch.setenv("PAPERLESS_API_TOKEN", "test-token")

    from doc_intelligence_hub.modules.statements import config as config_mod
    _original_load = config_mod.load_config

    snapshot_file = tmp_path / "test_snapshot.json"

    def patched_load(path):
        cfg = _original_load(path)
        cfg.runtime.snapshot_path = str(snapshot_file)
        return cfg

    monkeypatch.setattr(config_mod, "load_config", patched_load)
    monkeypatch.setattr("doc_intelligence_hub.modules.statements.service.load_config", patched_load)

    from doc_intelligence_hub.modules.statements.service import run_discovery
    result = await run_discovery("config/config.paperless.example.yaml")

    assert snapshot_file.exists()
    snapshot = json.loads(snapshot_file.read_text())
    assert snapshot["analyzed_documents"] == len(_ALL_DOCS)
    assert len(snapshot["providers"]) == len(result.providers)


# ---------------------------------------------------------------------------
# Optional live Paperless smoke test (requires PAPERLESS_API_TOKEN env var)
# ---------------------------------------------------------------------------

LIVE_TOKEN = os.environ.get("PAPERLESS_API_TOKEN")


@pytest.mark.asyncio
@pytest.mark.skipif(not LIVE_TOKEN, reason="PAPERLESS_API_TOKEN not set; skipping live smoke test")
async def test_live_paperless_connection():
    """Smoke test against a real Paperless-ngx instance."""
    result = await run_connection_test("config/config.paperless.example.yaml")

    assert result["status"] == "ok"
    assert result["mode"] == "paperless"
    assert result["documents"] > 0
    print(f"\n  Live Paperless: {result['documents']} documents, "
          f"{result['correspondents']} correspondents, "
          f"{result['tags']} tags")


@pytest.mark.asyncio
@pytest.mark.skipif(not LIVE_TOKEN, reason="PAPERLESS_API_TOKEN not set; skipping live smoke test")
async def test_live_paperless_discovery(tmp_path):
    """Discover providers against a real Paperless-ngx instance."""
    from doc_intelligence_hub.modules.statements import config as config_mod
    _original_load = config_mod.load_config

    def patched_load(path):
        cfg = _original_load(path)
        cfg.runtime.snapshot_path = str(tmp_path / "live_snapshot.json")
        return cfg

    import doc_intelligence_hub.modules.statements.service as svc_mod
    original_svc_load = svc_mod.load_config
    svc_mod.load_config = patched_load
    config_mod.load_config = patched_load

    try:
        result = await run_discovery("config/config.paperless.example.yaml")
    finally:
        svc_mod.load_config = original_svc_load
        config_mod.load_config = _original_load

    assert result.analyzed_documents > 0
    print(f"\n  Live discovery: {result.analyzed_documents} documents, "
          f"{len(result.providers)} providers found")
    for p in result.providers:
        print(f"    - {p.provider_name}: {p.pattern.frequency}, "
              f"confidence={p.pattern.confidence}, docs={p.document_count}")
