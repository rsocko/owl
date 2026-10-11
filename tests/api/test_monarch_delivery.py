from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from pydantic import SecretStr

from doc_intelligence_hub.api.routers import monarch_delivery
from doc_intelligence_hub.modules.monarch_delivery import database
from doc_intelligence_hub.modules.monarch_delivery.config import settings
from doc_intelligence_hub.modules.monarch_delivery.models import (
    GatewayResult,
    GatewayState,
)


class FakePaperless:
    def __init__(self) -> None:
        self.tagged: list[tuple[int, list[str]]] = []

    async def get_document(self, document_id: int):
        return {"id": document_id}

    @asynccontextmanager
    async def stream_document_download(self, document_id: int):
        async def chunks():
            yield b"%PDF fixture"

        yield chunks(), "application/pdf"

    async def add_tags_to_document(self, document_id: int, tags: list[str]):
        self.tagged.append((document_id, tags))
        return {"id": document_id, "tags": tags}

    async def aclose(self):
        return None


class FakeTyrion:
    def __init__(self) -> None:
        self.submit_calls = 0
        self.reconcile_calls = 0

    async def submit(self, **kwargs):
        self.submit_calls += 1
        return GatewayResult(
            state=GatewayState.ACKNOWLEDGED,
            acknowledgement_ref="opaque",
            revision=1,
            acknowledged=True,
        )

    async def reconcile(self, **kwargs):
        self.reconcile_calls += 1
        return GatewayResult(
            state=GatewayState.ACKNOWLEDGED,
            acknowledgement_ref="opaque",
            revision=2,
            acknowledged=True,
        )

    async def aclose(self):
        return None


@pytest.fixture
def delivery_api(monkeypatch, tmp_path):
    original = {
        "database_url": settings.database_url,
        "identity_secret": settings.identity_secret,
        "tyrion_api_token": settings.tyrion_api_token,
        "write_enabled": settings.write_enabled,
        "sent_tag": settings.sent_tag,
    }
    settings.database_url = f"sqlite:///{tmp_path / 'delivery.db'}"
    settings.identity_secret = SecretStr("stable-test-secret")
    settings.tyrion_api_token = SecretStr("bridge-token")
    settings.write_enabled = True
    settings.sent_tag = "Sent to Monarch"
    database.init_db()

    paperless = FakePaperless()
    tyrion = FakeTyrion()
    monkeypatch.setattr(monarch_delivery, "make_paperless_client", lambda *args, **kwargs: paperless)
    monkeypatch.setattr(monarch_delivery, "_gateway", lambda **kwargs: tyrion)
    yield paperless, tyrion

    for key, value in original.items():
        setattr(settings, key, value)


def test_status_and_confirmed_send(client, delivery_api):
    paperless, tyrion = delivery_api

    status = client.get("/api/documents/42/monarch-delivery")
    assert status.status_code == 200
    assert status.json()["state"] == "not_submitted"
    assert status.json()["can_send"] is True

    rejected = client.post(
        "/api/documents/42/monarch-delivery",
        json={"confirmed": False},
    )
    assert rejected.status_code == 422
    assert tyrion.submit_calls == 0

    sent = client.post(
        "/api/documents/42/monarch-delivery",
        json={"confirmed": True},
    )
    assert sent.status_code == 200
    assert sent.json()["state"] == "acknowledged"
    assert sent.json()["acknowledged"] is True
    assert tyrion.submit_calls == 1
    assert paperless.tagged == [(42, ["Sent to Monarch"])]

    repeated = client.post(
        "/api/documents/42/monarch-delivery",
        json={"confirmed": True},
    )
    assert repeated.status_code == 200
    assert tyrion.submit_calls == 1


def test_write_gate_denies_send_but_status_remains_visible(client, delivery_api):
    settings.write_enabled = False

    status = client.get("/api/documents/7/monarch-delivery")
    assert status.status_code == 200
    assert status.json()["can_send"] is False

    sent = client.post(
        "/api/documents/7/monarch-delivery",
        json={"confirmed": True},
    )
    assert sent.status_code == 503
    assert sent.json()["error"]["code"] == "monarch_delivery_write_disabled"
