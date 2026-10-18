from __future__ import annotations

import pytest

from doc_intelligence_hub.api.app import HubSettings
from doc_intelligence_hub.modules.monarch_delivery.config import Settings as DeliverySettings


@pytest.mark.parametrize("settings_type", [HubSettings, DeliverySettings])
def test_tyrion_clients_use_canonical_token(monkeypatch, settings_type):
    monkeypatch.setenv("OWL_TYRION_API_TOKEN", "canonical-token")

    settings = settings_type(_env_file=None)

    assert settings.tyrion_api_token is not None
    assert settings.tyrion_api_token.get_secret_value() == "canonical-token"
