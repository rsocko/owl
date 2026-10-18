from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from doc_intelligence_hub.modules.triage.relationship_discovery import (
    MAX_CANDIDATES_PER_DOCUMENT,
    PAPERLESS_PAGE_SIZE,
    build_relationship_candidate_pairs,
    discover_paperless_documents,
)


def test_candidate_generation_scales_without_all_pairs():
    documents = {
        document_id: {
            "document_id": document_id,
            "provider": f"Provider {document_id}",
            "invoice_number": f"INV-{document_id}",
            "amount": 100,
        }
        for document_id in range(1, 8001)
    }
    documents[8000]["provider"] = documents[7999]["provider"]
    documents[8000]["invoice_number"] = documents[7999]["invoice_number"]

    pairs, groups, exclusions = build_relationship_candidate_pairs(documents)

    assert pairs == [(7999, 8000)]
    assert groups == 1
    assert exclusions == {}
    assert len(pairs) < len(documents)


def test_dense_bucket_remains_linear_bounded():
    documents = {
        document_id: {
            "provider": "Utility",
            "invoice_number": "SHARED",
            "document_date": f"2026-01-{(document_id % 28) + 1:02d}",
        }
        for document_id in range(1, 8001)
    }

    pairs, groups, _ = build_relationship_candidate_pairs(documents)

    assert groups == 1
    assert len(pairs) <= len(documents) * MAX_CANDIDATES_PER_DOCUMENT


def test_candidate_generation_deduplicates_pairs_across_evidence_keys():
    documents = {
        1: {
            "provider": "Utility",
            "invoice_number": "INV-1",
            "account_identifier": "1234",
            "amount": 100,
        },
        2: {
            "provider": "Utility",
            "invoice_number": "INV-1",
            "account_identifier": "1234",
            "amount": 101,
        },
    }

    pairs, groups, _ = build_relationship_candidate_pairs(documents)

    assert pairs == [(1, 2)]
    assert groups == 2


def test_candidate_generation_reports_bounded_exclusion_reasons():
    documents = {
        1: {"title": "No provider"},
        2: {"provider": "Utility", "title": "No obligation identity"},
    }

    pairs, groups, exclusions = build_relationship_candidate_pairs(documents)

    assert pairs == []
    assert groups == 0
    assert exclusions == {
        "missing_provider": 1,
        "missing_obligation_identity": 1,
    }


def test_explicit_references_create_cross_provider_candidate_pair():
    documents = {
        1: {"provider": "Builder", "explicit_references": ["PO-91827"]},
        2: {"provider": "Supplier", "explicit_references": ["PO-91827"]},
    }

    pairs, groups, exclusions = build_relationship_candidate_pairs(documents)

    assert pairs == [(1, 2)]
    assert groups == 1
    assert exclusions == {}


def test_ambiguous_explicit_reference_fails_closed():
    documents = {
        document_id: {
            "provider": f"Provider {document_id}",
            "explicit_references": ["CASE-91827"],
        }
        for document_id in range(1, 5)
    }

    pairs, groups, exclusions = build_relationship_candidate_pairs(documents)

    assert pairs == []
    assert groups == 0
    assert exclusions == {"ambiguous_explicit_reference": 4}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ({"kind": "tag", "tags": ["Bills"]}, {"tags": ["Bills"]}),
        ({"kind": "saved_view", "saved_view_id": 7}, {"saved_view": 7}),
        (
            {
                "kind": "date_range",
                "created_after": "2026-01-01",
                "created_before": "2026-02-01",
            },
            {"created_after": "2026-01-01", "created_before": "2026-02-01"},
        ),
        ({"kind": "all"}, {}),
    ],
)
async def test_discovery_selects_scope_and_bounded_pagination(scope, expected):
    client = AsyncMock()
    client.list_documents.return_value = []
    client.fetch_all_metadata.return_value = ({}, {}, {})
    client.list_custom_fields.return_value = []

    _, discovery = await discover_paperless_documents(
        client,
        scope=scope,
        action_queue_source={},
    )

    kwargs = client.list_documents.await_args.kwargs
    assert kwargs["page_size"] == PAPERLESS_PAGE_SIZE
    assert {key: kwargs[key] for key in expected} == expected
    assert discovery["scope"]["kind"] == scope["kind"]


@pytest.mark.asyncio
async def test_action_queue_scope_reuses_configured_saved_view():
    client = AsyncMock()
    client.list_documents.return_value = []
    client.fetch_all_metadata.return_value = ({}, {}, {})
    client.list_custom_fields.return_value = []

    await discover_paperless_documents(
        client,
        scope={"kind": "action_queue"},
        action_queue_source={
            "scan_mode": "saved_view",
            "saved_view_id": 12,
            "monitor_tags": ["Ignored"],
            "document_limit": 500,
        },
    )

    assert client.list_documents.await_args.kwargs["saved_view"] == 12
    assert client.list_documents.await_args.kwargs["limit"] == 500


@pytest.mark.asyncio
async def test_action_queue_scope_rejects_empty_tag_configuration():
    client = AsyncMock()

    with pytest.raises(ValueError, match="no monitored tags"):
        await discover_paperless_documents(
            client,
            scope={"kind": "action_queue"},
            action_queue_source={
                "scan_mode": "tags",
                "monitor_tags": [],
            },
        )

    client.list_documents.assert_not_awaited()


@pytest.mark.asyncio
async def test_content_is_fetched_only_for_indexed_candidate_documents():
    client = AsyncMock()
    client.list_documents.return_value = [
        {
            "id": 1,
            "title": "Original",
            "correspondent": 10,
            "custom_fields": [{"field": 2, "value": "INV-1"}],
        },
        {
            "id": 2,
            "title": "Notice",
            "correspondent": 10,
            "custom_fields": [{"field": 2, "value": "INV-1"}],
        },
        {"id": 3, "title": "Unrelated", "correspondent": 11, "custom_fields": []},
    ]
    client.fetch_all_metadata.return_value = ({10: "Utility", 11: "Other"}, {}, {})
    client.list_custom_fields.return_value = [{"id": 2, "name": "Invoice Number"}]
    client.get_document_content.side_effect = ["Original bill", "Second notice"]

    documents, _ = await discover_paperless_documents(
        client,
        scope={"kind": "all"},
        action_queue_source={},
    )

    assert client.get_document_content.await_count == 2
    assert [call.args[0] for call in client.get_document_content.await_args_list] == [1, 2]
    assert documents[1]["content"] == "Second notice"
