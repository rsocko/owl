"""Configuration for the private Tyrion document-delivery boundary."""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/monarch_delivery.db"
    tyrion_authority: Literal["http://tyrion-operations-ui:3000"] = (
        "http://tyrion-operations-ui:3000"
    )
    tyrion_api_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "tyrion_api_token",
            "OWL_TYRION_API_TOKEN",
        ),
    )
    identity_secret: SecretStr | None = None
    write_enabled: bool = False
    sent_tag: str | None = None
    deployment_id: str = Field(default="default", min_length=1, max_length=100)
    submit_timeout_seconds: float = Field(default=100.0, gt=90, le=300)
    reconcile_timeout_seconds: float = Field(default=60.0, gt=45, le=300)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "env_prefix": "OWL_MONARCH_DELIVERY_",
    }

settings = Settings()
