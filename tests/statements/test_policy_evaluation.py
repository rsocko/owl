from datetime import date

import pytest

from doc_intelligence_hub.modules.statements.correspondent_models import (
    Cadence,
    DocumentExpectation,
    ExpectationEvidence,
    MetadataPolicy,
    TitleConvention,
)
from doc_intelligence_hub.modules.statements.models import DocumentRecord
from doc_intelligence_hub.modules.statements.policy_evaluation import evaluate_expectation_policy


def _expectation(**overrides) -> DocumentExpectation:
    values = {
        "id": "expectation-1",
        "correspondent_id": 42,
        "kind": "statement",
        "statement_series_id": "checking",
        "series_discriminator": "Checking",
        "expectation_mode": "recurring",
        "status": "confirmed",
        "cadence": Cadence(frequency="monthly"),
        "evidence": ExpectationEvidence(source="user"),
        "title_convention": TitleConvention(
            template="{series} - {kind} - {period}",
            date_basis="period",
            example="Checking - Statement - 2026-07",
        ),
        "metadata_policy": MetadataPolicy(
            all_of=[7],
            any_of=[11, 12],
            none_of=[99],
            required_document_type_id=3,
        ),
    }
    values.update(overrides)
    return DocumentExpectation(**values)


def _document(**overrides) -> DocumentRecord:
    values = {
        "id": 101,
        "title": "Old title",
        "correspondent_id": 42,
        "correspondent_name": "Example Bank",
        "document_type_id": 4,
        "document_type": "Invoice",
        "created": date(2026, 7, 3),
        "tag_ids": [10, 99],
        "tags": ["DOG", "Forbidden"],
    }
    values.update(overrides)
    return DocumentRecord(**values)


def test_preview_is_exact_stable_and_does_not_treat_parent_as_any_of_child() -> None:
    expectation = _expectation()
    document = _document()
    kwargs = {
        "tag_names": {7: "Finance", 10: "DOG", 11: "DOG:Quinn", 12: "DOG:Avery", 99: "Old"},
        "document_type_names": {3: "Statement", 4: "Invoice"},
        "series_documents": {101: {"period_label": "2026-07"}},
    }

    first = evaluate_expectation_policy(expectation, "Example Bank", [document], **kwargs)
    second = evaluate_expectation_policy(expectation, "Example Bank", [document], **kwargs)

    assert first == second
    finding = first.findings[0]
    assert finding.violations == [
        "missing_all_of",
        "missing_any_of",
        "forbidden_tags",
        "wrong_document_type",
        "title_mismatch",
    ]
    assert finding.unresolved_violations == ["missing_any_of"]
    assert finding.operation.expected.tag_ids == [10, 99]
    assert finding.proposed.tag_ids == [7, 10]
    assert finding.operation.patch.model_dump() == {
        "title": "Checking - Statement - 2026-07",
        "tags": [7, 10],
        "document_type": 3,
    }


def test_subject_template_requires_one_exact_configured_child_tag() -> None:
    expectation = _expectation(
        kind="invoice",
        statement_series_id=None,
        document_ids=[101],
        cadence=None,
        expectation_mode="irregular",
        title_convention=TitleConvention(
            template="{correspondent} - {subject} - {document_date}",
            date_basis="document_date",
            example="West St. Vet - Quinn - 2026-07-03",
        ),
    )
    document = _document(
        title="West St. Vet invoice",
        correspondent_name="West St. Vet",
        document_type_id=3,
        tag_ids=[10],
        tags=["DOG"],
    )

    preview = evaluate_expectation_policy(
        expectation,
        "West St. Vet",
        [document],
        tag_names={10: "DOG", 11: "DOG:Quinn", 12: "DOG:Avery"},
        document_type_names={3: "Invoice"},
    )

    finding = preview.findings[0]
    assert finding.missing_title_fields == ["subject"]
    assert finding.operation.patch.model_dump() == {"tags": [7, 10]}
    assert "title_missing_fields" in finding.unresolved_violations


def test_overlong_render_is_reported_without_a_title_patch() -> None:
    expectation = _expectation(
        series_discriminator="X" * 120,
        title_convention=TitleConvention(
            template="{series} - {document_date}",
            date_basis="document_date",
            example="Short example",
        ),
        metadata_policy=MetadataPolicy(),
    )
    preview = evaluate_expectation_policy(
        expectation,
        "Example Bank",
        [_document(document_type_id=3, tag_ids=[])],
        tag_names={},
        document_type_names={3: "Statement"},
    )

    finding = preview.findings[0]
    assert finding.violations == ["title_too_long"]
    assert finding.operation.patch.model_dump() == {}


def test_single_any_of_option_is_an_exact_repair() -> None:
    expectation = _expectation(
        title_convention=None,
        metadata_policy=MetadataPolicy(any_of=[11]),
    )
    preview = evaluate_expectation_policy(
        expectation,
        "Example Bank",
        [_document(document_type_id=3, tag_ids=[10])],
        tag_names={10: "DOG", 11: "DOG:Quinn"},
        document_type_names={3: "Statement"},
    )

    finding = preview.findings[0]
    assert finding.violations == ["missing_any_of"]
    assert finding.unresolved_violations == []
    assert finding.operation.patch.model_dump() == {"tags": [10, 11]}


def test_not_expected_policy_cannot_be_evaluated() -> None:
    expectation = _expectation(
        kind="invoice",
        statement_series_id=None,
        document_ids=[101],
        cadence=None,
        expectation_mode="not_expected",
    )

    with pytest.raises(ValueError, match="Not-expected expectations cannot be evaluated"):
        evaluate_expectation_policy(
            expectation,
            "Example Bank",
            [_document()],
            tag_names={},
            document_type_names={},
        )


def test_series_period_uses_statement_date_with_canonical_quarter_format() -> None:
    expectation = _expectation(
        cadence=Cadence(frequency="quarterly"),
        title_convention=TitleConvention(
            template="{series} - {period}",
            date_basis="period",
            example="Checking - 2026-Q4",
        ),
        metadata_policy=MetadataPolicy(),
    )
    preview = evaluate_expectation_policy(
        expectation,
        "Example Bank",
        [_document(title="Old", document_type_id=3, created=date(2027, 1, 5), tag_ids=[])],
        tag_names={},
        document_type_names={3: "Statement"},
        series_documents={101: {"statement_date": "2026-12-31", "period_label": None}},
    )

    assert preview.findings[0].operation.patch.model_dump() == {"title": "Checking - 2026-Q4"}
