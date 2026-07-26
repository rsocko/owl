---
title: Testing Guide
sidebar_label: Testing
sidebar_position: 2
---

# Testing Guide

The Document Intelligence Hub uses **pytest** with **pytest-asyncio** for testing. Tests are organized to mirror the source structure.

## Running Tests

```bash
# Run the full test suite
pytest

# Run tests for a specific module
pytest tests/eob_matching/
pytest tests/statements/
pytest tests/api/

# Run a single test file
pytest tests/test_stats_api.py

# Run with verbose output
pytest -v

# Run tests matching a keyword
pytest -k "test_health"

# Run with coverage report
pytest --cov=doc_intelligence_hub --cov-report=term-missing
```

:::tip
During development, use `pytest -x` to stop on the first failure — it saves time when iterating on a fix.
:::

## Test Structure

Tests mirror the `src/` layout for easy discovery:

```
tests/
├── __init__.py
├── _category_.json
├── test_stats_api.py          # Top-level API integration tests
├── action_queue/              # Action queue module tests
├── analysis/                  # Analysis engine tests
├── api/                       # API router tests
├── core/                      # Core utility tests
├── eob_matching/              # EOB matching tests
├── statements/                # Statement tracker tests
└── triage/                    # Triage queue tests
```

Each module test directory typically contains:
- `test_service.py` — Business logic unit tests
- `test_router.py` — API endpoint tests (using httpx AsyncClient)
- `test_database.py` — Database operation tests
- `conftest.py` — Module-specific fixtures

## Configuration

Test configuration lives in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

This ensures:
- `src/` is on the Python path so imports work without installation
- `pytest` automatically discovers tests under `tests/`

## Fixtures

### Common Fixtures

Create shared fixtures in `tests/conftest.py` or module-level `conftest.py` files:

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.fixture
def mock_paperless_client():
    """Mock Paperless-ngx API client for unit tests."""
    with patch("doc_intelligence_hub.core.paperless.client.PaperlessClient") as mock:
        client = AsyncMock()
        client.get_documents.return_value = []
        client.get_document.return_value = {"id": 1, "title": "Test Doc"}
        mock.return_value = client
        yield client

@pytest.fixture
def sample_document():
    """A minimal document fixture for testing."""
    return {
        "id": 42,
        "title": "Medical Bill - January 2024",
        "correspondent": "Hospital ABC",
        "document_type": "bill",
        "tags": [1, 5, 12],
        "content": "Patient: John Doe\nAmount: $150.00",
    }
```

### Test Database

For modules that use SQLite (alerts, triage, analysis), use an in-memory database:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

@pytest.fixture
def test_db():
    """In-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    # Create tables
    from doc_intelligence_hub.core.alerts import Base
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    yield Session()
```

## Writing Tests

### Testing API Routers

Use `httpx.AsyncClient` with FastAPI's test utilities:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from doc_intelligence_hub.api.app import create_app, HubSettings

@pytest.fixture
def app():
    """Create a test app instance."""
    settings = HubSettings(
        paperless_url="http://mock-paperless:8000",
        paperless_api_token="test-token",
    )
    return create_app(settings)

@pytest.fixture
async def client(app):
    """Async HTTP client for testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

@pytest.mark.asyncio
async def test_health_endpoint(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "modules" in data
```

### Testing Services

Unit test business logic by mocking external dependencies:

```python
import pytest
from unittest.mock import AsyncMock, patch

from doc_intelligence_hub.modules.eob_matching.service import classify_document

@pytest.mark.asyncio
async def test_classify_document_as_eob():
    mock_llm = AsyncMock()
    mock_llm.chat.return_value = {"classification": "eob", "confidence": 0.95}

    with patch("doc_intelligence_hub.modules.eob_matching.service.get_llm_client", return_value=mock_llm):
        result = await classify_document(document_id=42, content="Explanation of Benefits...")
        assert result.classification == "eob"
        assert result.confidence >= 0.9
```

### Testing Database Operations

```python
import pytest
from doc_intelligence_hub.modules.triage.database import (
    add_to_queue,
    get_pending_items,
    resolve_item,
)

def test_triage_queue_lifecycle(test_db):
    # Add an item to the queue
    item = add_to_queue(db=test_db, document_id=1, reason="Low confidence match")
    assert item.status == "pending"

    # Retrieve pending items
    pending = get_pending_items(db=test_db)
    assert len(pending) == 1

    # Resolve the item
    resolve_item(db=test_db, item_id=item.id, decision="approved")
    pending = get_pending_items(db=test_db)
    assert len(pending) == 0
```

:::warning
Always use isolated test databases (`:memory:` or temp files) — never run tests against a real Paperless instance unless you're explicitly running integration tests.
:::

## Coverage

Check test coverage to identify untested code:

```bash
# Terminal coverage report
pytest --cov=doc_intelligence_hub --cov-report=term-missing

# HTML coverage report (opens in browser)
pytest --cov=doc_intelligence_hub --cov-report=html
open htmlcov/index.html
```

### Coverage by Module

| Module | Key Areas to Test |
|--------|------------------|
| `api/routers/` | Request validation, response shapes, error codes |
| `modules/statements/` | Recommendation logic, provider matching |
| `modules/eob_matching/` | Classification, extraction, bill matching |
| `modules/action_queue/` | Pipeline stages, retry logic |
| `modules/analysis/` | Rule evaluation, insight generation |
| `modules/triage/` | Queue lifecycle, decision recording |
| `core/` | LLM client, Paperless client, alert system |

:::info
Focus coverage on business logic and edge cases. Trivial getters and framework boilerplate don't need dedicated tests.
:::

## Integration Tests

For tests that need real external services (Paperless-ngx, LLM gateway), use Docker Compose:

### Running Integration Tests

```bash
# Start Paperless and the hub for integration testing
docker compose up -d hub

# Run integration tests (marked with @pytest.mark.integration)
pytest -m integration --paperless-url=http://localhost:8071

# Tear down after testing
docker compose down
```

### Marking Integration Tests

```python
import pytest

@pytest.mark.integration
@pytest.mark.asyncio
async def test_paperless_document_fetch(live_paperless_client):
    """Requires a running Paperless instance with test documents."""
    docs = await live_paperless_client.get_documents(limit=5)
    assert len(docs) > 0
    assert all("id" in doc for doc in docs)
```

### Test Docker Compose Profile

For CI or isolated integration testing, use a dedicated test profile:

```bash
# Start services with test configuration
docker compose --profile test up -d

# Run the integration suite
pytest -m integration

# Clean up
docker compose --profile test down -v
```

:::tip
Integration tests are slow and require infrastructure. Run them selectively during development and let CI handle the full integration suite on PRs.
:::

## Best Practices

1. **Name tests descriptively** — `test_classify_document_returns_eob_for_valid_content` over `test_classify`
2. **One assertion per concept** — test one behavior per test function
3. **Use fixtures** for shared setup — avoid repeating mock configuration
4. **Mark slow tests** — use `@pytest.mark.slow` for tests that take >1s
5. **Test error cases** — verify proper error responses for invalid input
6. **Keep tests independent** — no test should depend on another test's side effects

---

## Related

- **[Development Guide](./index.md)** � Project structure, running locally, CI/CD
- **[Architecture](../architecture/)** � How modules are organized and interact
