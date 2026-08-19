from doc_intelligence_hub.api.document_summary import (
    DocumentSummaryContext,
    build_document_summary,
)


def test_normalizes_document_summary_fields() -> None:
    summary = build_document_summary(
        {
            "id": 42,
            "title": "  August statement ",
            "provider_name": "Example Bank",
            "doc_type": "statement",
            "statement_date": "2026-08-01",
            "tags": [{"name": "finance"}, "review", "review"],
        }
    )

    assert summary == {
        "document_id": 42,
        "title": "August statement",
        "correspondent": "Example Bank",
        "document_type": "statement",
        "document_date": "2026-08-01",
        "tags": ["finance", "review"],
    }


def test_omits_account_outside_named_review_context() -> None:
    summary = build_document_summary(
        {"document_id": 7, "account_identifier": "EXACT-123456789"}
    )

    assert "account_identifier_display" not in summary
    assert "EXACT-123456789" not in str(summary)


def test_masks_account_in_named_review_context() -> None:
    summary = build_document_summary(
        {"document_id": 7, "account_identifier": "EXACT-123456789"},
        context=DocumentSummaryContext.ACCOUNT_REVIEW,
    )

    assert summary["account_identifier_display"] != "EXACT-123456789"
    assert summary["account_identifier_display"].endswith("6789")


def test_missing_optional_fields_are_omitted() -> None:
    assert build_document_summary({"document_id": "abc"}) == {
        "document_id": "abc",
        "tags": [],
    }
