from __future__ import annotations

from urllib.parse import quote

import httpx

from doc_intelligence_hub.modules.statements.correspondent_models import (
    DocumentExpectationSignalsV1,
)


class DocumentExpectationSignalsClient:
    """Pull the bounded, policy-safe Tyrion projection by opaque generation."""

    def __init__(
        self,
        base_url: str,
        *,
        api_token: str | None = None,
        verify_ssl: bool = True,
        timeout_seconds: int = 30,
    ) -> None:
        headers = {"Authorization": "Bearer " + api_token} if api_token else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            verify=verify_ssl,
            timeout=timeout_seconds,
        )

    async def fetch(
        self,
        source_generation: str,
    ) -> DocumentExpectationSignalsV1:
        generation = quote(source_generation, safe="")
        response = await self._client.get(
            f"/api/connector/v1/document-expectation-signals/{generation}",
        )
        response.raise_for_status()
        snapshot = DocumentExpectationSignalsV1.model_validate(response.json())
        if snapshot.source_generation != source_generation:
            raise ValueError("External signal response sourceGeneration did not match the request")
        return snapshot

    async def close(self) -> None:
        await self._client.aclose()
