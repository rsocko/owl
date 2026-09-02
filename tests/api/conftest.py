"""Shared fixtures for API integration tests.

All external dependencies (Paperless, LLM) are mocked so tests run
without any network access.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from doc_intelligence_hub.api.app import HubSettings, create_app
from doc_intelligence_hub.core.alerts import (
    Alert,
)
from doc_intelligence_hub.core.alerts import (
    configure as alerts_configure,
)
from doc_intelligence_hub.core.alerts import (
    get_session as get_alerts_session,
)
from doc_intelligence_hub.core.alerts import (
    init_db as alerts_init_db,
)
from doc_intelligence_hub.modules.action_queue.config import settings as aq_settings
from doc_intelligence_hub.modules.action_queue.database import (
    Action,
)
from doc_intelligence_hub.modules.action_queue.database import (
    get_session as get_aq_session,
)
from doc_intelligence_hub.modules.action_queue.database import (
    init_db as aq_init_db,
)
from doc_intelligence_hub.modules.eob_matching.database import (
    BillRecord,
    EOBRecord,
    MatchingRun,
    MatchRecord,
)
from doc_intelligence_hub.modules.eob_matching.database import (
    configure as eob_configure,
)
from doc_intelligence_hub.modules.eob_matching.database import (
    get_session as get_eob_session,
)
from doc_intelligence_hub.modules.eob_matching.database import (
    init_db as eob_init_db,
)
from doc_intelligence_hub.modules.triage.database import (
    configure as triage_configure,
)
from doc_intelligence_hub.modules.triage.database import (
    init_db as triage_init_db,
)

# ---------------------------------------------------------------------------
# Mock Paperless client
# ---------------------------------------------------------------------------


def _make_mock_paperless() -> AsyncMock:
    """Build a mock PaperlessClient that returns realistic stub data."""
    mock = AsyncMock()
    mock.base_url = "http://paperless.test"
    mock.token = "test-token"
    mock.health_check.return_value = {"status": "ok", "documents": 42}
    mock.list_tags.return_value = [
        {"id": 1, "name": "Inbox", "colour": "#1f6feb"},
        {"id": 2, "name": "Medical", "colour": "#2da44e"},
    ]
    mock.list_correspondents.return_value = [
        {"id": 1, "name": "UnitedHealth"},
        {"id": 2, "name": "Aetna"},
    ]
    mock.list_saved_views.return_value = [
        {"id": 1, "name": "Inbox View"},
        {"id": 2, "name": "Todo View"},
    ]
    mock.fetch_all_metadata.return_value = (
        {1: "UnitedHealth", 2: "Aetna"},
        {1: "Inbox", 2: "Medical"},
        {1: "Statement", 2: "Bill", 3: "Letter"},
    )
    mock.list_documents.return_value = []
    mock.list_custom_fields.return_value = []
    mock.get_document.return_value = {
        "id": 1,
        "title": "Test Document",
        "correspondent": 1,
        "tags": [1],
        "created": "2026-01-01",
        "added": "2026-01-01",
    }
    mock.get_document_suggestions.return_value = {"correspondents": [2]}
    mock.get_document_content.return_value = "Sample document content for testing."
    mock.get_document_thumbnail.return_value = (b"\x89PNG", "image/png")
    mock.get_document_preview.return_value = (b"%PDF-1.4", "application/pdf")
    mock.check_custom_fields.return_value = {"status": "ok", "fields": []}
    return mock


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_paperless():
    """Provide a mock PaperlessClient and patch make_paperless_client everywhere it's imported."""
    mock = _make_mock_paperless()
    # Patch at source AND at every router module that imports it
    targets = [
        "doc_intelligence_hub.api.routers.make_paperless_client",
        "doc_intelligence_hub.api.routers.system.make_paperless_client",
        "doc_intelligence_hub.api.routers.statements.make_paperless_client",
        "doc_intelligence_hub.api.routers.eob.make_paperless_client",
        "doc_intelligence_hub.api.routers.action_queue.make_paperless_client",
        "doc_intelligence_hub.api.routers.document_views.make_paperless_client",
        "doc_intelligence_hub.api.routers.ocr_quality.make_paperless_client",
        "doc_intelligence_hub.api.routers.ocr_quality_candidates.make_paperless_client",
        # "doc_intelligence_hub.api.routers.stats.make_paperless_client",  # stats doesn't import this
    ]
    patches = [patch(t, return_value=mock) for t in targets]
    for p in patches:
        p.start()
    yield mock
    for p in patches:
        p.stop()


@pytest.fixture()
def mock_llm():
    """Patch LLM-related calls to avoid real network access."""
    mock_settings = MagicMock(base_url="http://llm.test/v1", model="gpt-4o-mini")
    mock_validate = AsyncMock(
        return_value={"available": True, "model": "gpt-4o-mini", "models": ["gpt-4o-mini"]}
    )
    mock_health = AsyncMock(return_value={"status": "ok"})

    # Patch at source and at every module that imports these functions
    patches = [
        patch("doc_intelligence_hub.core.llm.health_check", mock_health),
        patch("doc_intelligence_hub.core.llm.validate_model_availability", mock_validate),
        patch("doc_intelligence_hub.core.llm.get_llm_settings", return_value=mock_settings),
        # Also patch where imported into router modules
        patch("doc_intelligence_hub.api.routers.system.llm_health_check", mock_health),
        patch("doc_intelligence_hub.api.routers.system.validate_model_availability", mock_validate),
        patch(
            "doc_intelligence_hub.api.routers.system.get_llm_settings", return_value=mock_settings
        ),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


@pytest.fixture()
def hub_settings(tmp_path) -> HubSettings:
    """Create HubSettings pointing at a temp config."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("# empty config for testing\n")
    return HubSettings(
        paperless_url="http://paperless.test",
        paperless_browser_url="https://paperless.browser.test",
        paperless_token="test-token",
        statement_tracker_config=str(config_path),
    )


@pytest.fixture()
def app(hub_settings, mock_llm, tmp_path):
    """Create a FastAPI app with mocked startup events and temp databases."""
    # Action queue DB
    aq_db_path = tmp_path / "test_actions.db"
    original_aq_db_url = aq_settings.database_url
    aq_settings.database_url = f"sqlite:///{aq_db_path}"
    aq_init_db()

    # EOB DB
    eob_db_path = tmp_path / "test_eob.db"
    eob_configure(f"sqlite:///{eob_db_path}")
    eob_init_db()

    # Alerts DB
    alerts_db_path = tmp_path / "test_alerts.db"
    alerts_configure(f"sqlite:///{alerts_db_path}")
    alerts_init_db()

    # Triage DB
    triage_db_path = tmp_path / "test_triage.db"
    triage_configure(f"sqlite:///{triage_db_path}")
    triage_init_db()

    application = create_app(hub_settings)

    yield application

    aq_settings.database_url = original_aq_db_url


@pytest.fixture()
def client(app, mock_paperless) -> TestClient:
    """TestClient with all external deps mocked."""
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def seed_actions():
    """Seed the action queue database with sample actions."""
    db = get_aq_session()
    try:
        actions = [
            Action(
                document_id=1,
                document_title="Electric Bill",
                action_type="PAY",
                title="Pay electric bill",
                summary="Monthly electric bill due",
                urgency="CRITICAL",
                status="pending",
                created_at=datetime.now(UTC),
            ),
            Action(
                document_id=2,
                document_title="Insurance Card",
                action_type="FILE",
                title="File insurance card",
                summary="File for records",
                urgency="LOW",
                status="pending",
                created_at=datetime.now(UTC),
            ),
            Action(
                document_id=3,
                document_title="Old Bill",
                action_type="PAY",
                title="Pay old bill",
                summary="Already paid",
                urgency="MEDIUM",
                status="completed",
                completed_at=datetime.now(UTC) - timedelta(days=5),
                created_at=datetime.now(UTC) - timedelta(days=10),
            ),
        ]
        for a in actions:
            db.add(a)
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def seed_eob():
    """Seed the EOB matching database with sample data."""
    db = get_eob_session()
    try:
        run = MatchingRun(
            started_at=datetime(2026, 7, 20, 10, 0, 0, tzinfo=UTC),
            finished_at=datetime(2026, 7, 20, 10, 1, 0, tzinfo=UTC),
            documents_scanned=8,
            eobs_found=3,
            bills_found=2,
            matches_found=2,
            high_confidence=1,
            medium_confidence=1,
            low_confidence=0,
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        db.add(
            EOBRecord(
                document_id=100,
                run_id=run.id,
                title="EOB from UHC",
                provider_name="UnitedHealth",
                total_patient_responsibility=150.00,
            )
        )
        db.add(
            EOBRecord(
                document_id=101,
                run_id=run.id,
                title="EOB from Aetna",
                provider_name="Aetna",
                total_patient_responsibility=75.50,
            )
        )
        db.add(
            BillRecord(
                document_id=200,
                run_id=run.id,
                title="Bill from Dr. Smith",
                provider_name="Dr. Smith",
                patient_name="Jane Doe",
                date_of_service="2026-06-15",
                total_amount=150.00,
                balance_due=150.00,
                invoice_number="INV-001",
            )
        )
        db.add(
            BillRecord(
                document_id=201,
                run_id=run.id,
                title="Bill from City Hospital",
                provider_name="City Hospital",
                patient_name="Jane Doe",
                date_of_service="2026-06-20",
                total_amount=75.50,
                balance_due=75.50,
                invoice_number="INV-002",
            )
        )
        db.add(
            MatchRecord(
                run_id=run.id,
                eob_document_id=100,
                bill_document_id=200,
                score=0.92,
                confidence="HIGH",
                status="confirmed",
                confirmed_at=datetime(2026, 7, 20, 11, 0, 0, tzinfo=UTC),
            )
        )
        db.add(
            MatchRecord(
                run_id=run.id,
                eob_document_id=101,
                bill_document_id=201,
                score=0.65,
                confidence="MEDIUM",
                status="candidate",
            )
        )
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def seed_alerts():
    """Seed the alerts database with sample alerts."""
    db = get_alerts_session()
    try:
        alerts = [
            Alert(
                alert_type="missing_statement",
                severity="high",
                module="statements",
                title="Missing electric bill statement",
                description="Expected Jan 2026 statement not received",
                created_at=datetime.now(UTC) - timedelta(days=2),
            ),
            Alert(
                alert_type="unmatched_eob",
                severity="medium",
                module="eob",
                title="Unmatched EOB from Aetna",
                description="No matching bill found",
                created_at=datetime.now(UTC) - timedelta(days=1),
            ),
            Alert(
                alert_type="urgent_action",
                severity="critical",
                module="action_queue",
                title="Overdue payment",
                description="Electric bill past due",
                created_at=datetime.now(UTC),
            ),
            Alert(
                alert_type="resolved_item",
                severity="low",
                module="statements",
                title="Old resolved alert",
                description="Already handled",
                created_at=datetime.now(UTC) - timedelta(days=10),
                acknowledged_at=datetime.now(UTC) - timedelta(days=9),
                resolved_at=datetime.now(UTC) - timedelta(days=8),
            ),
        ]
        for a in alerts:
            db.add(a)
        db.commit()
    finally:
        db.close()
