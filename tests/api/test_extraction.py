from unittest.mock import AsyncMock

import pytest

from doc_intelligence_hub.api.routers import extraction
from doc_intelligence_hub.core.extractors.account_numbers import (
    ExtractionResult,
    extract_account_numbers,
)


@pytest.mark.asyncio
async def test_extract_text_returns_only_server_masked_account_context() -> None:
    secret = "MEMBER123456"

    result = await extraction.extract_from_text(
        extraction.ExtractTextRequest(text=f"Member ID: {secret}")
    )

    assert result["identifier_class"] == "member"
    assert result["account_identifier_display"] == "member ending 3456"
    assert secret not in str(result)
    assert "matches" not in result


@pytest.mark.asyncio
async def test_multiple_candidates_route_to_privacy_safe_review(monkeypatch) -> None:
    secret_one = "MEMBER123456"
    secret_two = "POLICY987654"
    result = ExtractionResult(
        document_id=100,
        pattern_matches=extract_account_numbers(
            f"Member ID: {secret_one}\nPolicy Number: {secret_two}"
        ),
        raw_text_length=50,
        success=True,
    )
    queued: dict = {}

    async def stub_extract(document_id, client):
        return result

    def capture_queue(**kwargs):
        queued.update(kwargs)
        return kwargs

    monkeypatch.setattr(extraction, "make_paperless_client", lambda request: AsyncMock())
    monkeypatch.setattr(extraction, "extract_from_document", stub_extract)
    monkeypatch.setattr(extraction, "create_queue_item", capture_queue)

    response = await extraction.extract_single(
        extraction.ExtractRequest(document_id=100, write_to_paperless=True),
        object(),
    )

    assert response["requires_review"] is True
    assert response["candidate_count"] == 2
    assert queued["item_type"] == "metadata_quality_review"
    assert secret_one not in str(response)
    assert secret_two not in str(response)
    assert secret_one not in str(queued)
    assert secret_two not in str(queued)
