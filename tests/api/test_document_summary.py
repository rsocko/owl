from datetime import date, datetime

import pytest

from doc_intelligence_hub.api.document_summary import (
    DocumentSummaryContext,
    build_document_summary,
)


def test_general_summary_uses_native_document_type_and_omits_sensitive_fields():
    summary = build_document_summary(
        {
            "id": 42,
            "title": "Statement",
            "correspondent_name": "Example Bank",
            "document_type_name": "Financial Statement",
            "doc_type": "guessed-statement",
            "created": date(2026, 10, 1),
            "account_identifier": "123456789",
            "patient_name": "Private Patient",
        }
    )

    assert summary == {
        "document_id": 42,
        "title": "Statement",
        "correspondent": "Example Bank",
        "document_type": "Financial Statement",
        "document_date": "2026-10-01",
        "date_label": "Created",
        "tags": [],
    }


def test_summary_does_not_accept_module_generated_document_type():
    summary = build_document_summary({"document_id": 7, "doc_type": "eob", "type": "bill"})

    assert "document_type" not in summary


def test_account_review_masks_identifier_and_uses_display_only_key():
    summary = build_document_summary(
        {
            "document_id": "9",
            "account_identifier": "ACCT-123456",
            "statement_date": datetime(2026, 9, 30, 12, 0),
        },
        context=DocumentSummaryContext.ACCOUNT_REVIEW,
    )

    assert summary["account_identifier_display"] == "ending 3456"
    assert summary["document_date"] == "2026-09-30"
    assert "account_identifier" not in summary


def test_medical_context_is_required_for_patient_name():
    source = {"document_id": 10, "patient_name": "Pat Example"}

    assert "patient_name" not in build_document_summary(source)
    assert (
        build_document_summary(source, context=DocumentSummaryContext.MEDICAL_REVIEW)[
            "patient_name"
        ]
        == "Pat Example"
    )


def test_document_id_is_required():
    with pytest.raises(ValueError, match="requires a document ID"):
        build_document_summary({"title": "Missing identity"})
