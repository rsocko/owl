from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from doc_intelligence_hub.modules.triage.reference_discovery import (
    ExplicitReference,
    extract_explicit_references,
    extract_labeled_references,
    extract_title_references,
    resolve_reference,
)


def test_labeled_extraction_keeps_short_contextual_invoice_number() -> None:
    references = extract_labeled_references(
        "Payment applies to invoice 4826, dated 2026-02-01."
    )

    assert [(reference.kind, reference.value) for reference in references] == [
        ("invoice", "4826")
    ]


def test_title_extraction_rejects_bare_short_number_and_year() -> None:
    references = extract_title_references("Statement 4826 for 2026 and account ACCT-91827")

    assert [reference.value for reference in references] == ["ACCT-91827"]


@pytest.mark.asyncio
async def test_llm_augments_unusual_explicit_reference_wording() -> None:
    with patch(
        "doc_intelligence_hub.core.llm.chat_json",
        new=AsyncMock(
            return_value={"references": [{"kind": "case", "value": "CV-2026-1042"}]}
        ),
    ) as chat_json:
        references = await extract_explicit_references(
            "This notice concerns the case identified in our prior correspondence as CV-2026-1042."
        )

    assert references == [
        ExplicitReference(kind="case", value="CV-2026-1042", source="llm")
    ]
    chat_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolver_requires_whole_reference_and_excludes_source_document() -> None:
    client = AsyncMock()
    client.search_documents_by_content.return_value = [
        {"id": 1, "title": "Source", "content": "Invoice INV-4826"},
        {"id": 2, "title": "Receipt", "content": "Paid invoice INV-4826."},
        {"id": 3, "title": "Other", "content": "Different INV-48260 value"},
    ]

    result = await resolve_reference(
        client,
        ExplicitReference(kind="invoice", value="INV-4826", source="content"),
        exclude_document_id=1,
    )

    assert result.status == "resolved"
    assert result.document_ids == (2,)
    client.get_document_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolver_fails_closed_for_ambiguous_reference() -> None:
    client = AsyncMock()
    client.search_documents_by_content.return_value = [
        {"id": document_id, "content": "Case CV-2026-1042"}
        for document_id in range(2, 6)
    ]

    result = await resolve_reference(
        client,
        ExplicitReference(kind="case", value="CV-2026-1042", source="content"),
        exclude_document_id=1,
    )

    assert result.status == "ambiguous"
    assert result.document_ids == ()


@pytest.mark.asyncio
async def test_resolver_stops_when_substring_scan_limit_is_exceeded() -> None:
    client = AsyncMock()
    client.search_documents_by_content.return_value = [
        {"id": document_id} for document_id in range(1, 202)
    ]

    result = await resolve_reference(
        client,
        ExplicitReference(kind="reference", value="REF-12345", source="content"),
        exclude_document_id=999,
    )

    assert result.status == "scan_limit_exceeded"
    client.get_document_content.assert_not_awaited()
