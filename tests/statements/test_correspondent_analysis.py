from datetime import UTC, date, datetime

from doc_intelligence_hub.modules.statements.correspondent_analysis import (
    analyze_correspondent_policy,
)
from doc_intelligence_hub.modules.statements.models import DocumentRecord


def _document(
    document_id: int,
    title: str,
    created: date,
    *,
    document_type_id: int = 3,
    document_type: str = "Statement",
    tag_ids: list[int] | None = None,
    tags: list[str] | None = None,
) -> DocumentRecord:
    return DocumentRecord(
        id=document_id,
        title=title,
        correspondent_id=42,
        correspondent_name="Example Bank",
        document_type_id=document_type_id,
        document_type=document_type,
        created=created,
        tag_ids=tag_ids or [7],
        tags=tags or ["Finance"],
    )


def test_analyzes_monthly_series_with_explainable_policy() -> None:
    documents = [
        _document(
            month,
            f"Example Bank Checking Statement {date(2026, month, 1):%B %Y}",
            date(2026, month, 3),
        )
        for month in range(1, 5)
    ]

    result = analyze_correspondent_policy(
        42,
        "Example Bank",
        documents,
        [
            {
                "id": "checking",
                "name": "Checking",
                "correspondent_id": 42,
                "correspondent_name": "Example Bank",
            }
        ],
        analyzed_at=datetime(2026, 5, 1, tzinfo=UTC),
    )

    assert result.observed_summary.document_count == 4
    assert result.observed_summary.candidate_series_count == 1
    suggestion = result.suggestions[0]
    assert suggestion.expectation_mode == "recurring"
    assert suggestion.cadence is not None
    assert suggestion.cadence.frequency == "monthly"
    assert suggestion.statement_series_id == "checking"
    assert suggestion.evidence.sample_size == 4
    assert suggestion.evidence.reason_codes == [
        "existing_statement_series",
        "monthly_cadence",
        "paperless_history",
    ]
    assert suggestion.title.convention is not None
    assert suggestion.title.coverage == 1
    assert len(suggestion.title.examples) == 3
    assert suggestion.metadata.policy.all_of == [7]
    assert suggestion.metadata.policy.required_document_type_id == 3
    assert suggestion.acquisition.channel == "unknown"
    assert suggestion.acquisition.reason_codes == ["ingestion_source_unavailable"]


def test_keeps_two_observations_unknown_instead_of_forcing_cadence() -> None:
    result = analyze_correspondent_policy(
        42,
        "Example Bank",
        [
            _document(1, "Example Bank Notice January 2026", date(2026, 1, 10)),
            _document(2, "Example Bank Notice May 2026", date(2026, 5, 10)),
        ],
        [],
    )

    suggestion = result.suggestions[0]
    assert suggestion.expectation_mode == "unknown"
    assert suggestion.cadence is None
    assert suggestion.evidence.reason_codes == [
        "insufficient_cadence_evidence",
        "paperless_history",
    ]


def test_keeps_single_observation_unknown() -> None:
    result = analyze_correspondent_policy(
        42,
        "Example Bank",
        [_document(1, "Example Bank Notice January 2026", date(2026, 1, 10))],
        [],
    )

    assert result.suggestions[0].expectation_mode == "unknown"
    assert result.suggestions[0].evidence.confidence == 0.15


def test_separates_numeric_account_candidates_without_exposing_identifiers() -> None:
    documents = []
    document_id = 1
    for account in (1234, 5678):
        for month in range(1, 4):
            documents.append(
                _document(
                    document_id,
                    f"Checking {account} Statement 2026-{month:02d}",
                    date(2026, month, 3),
                )
            )
            document_id += 1

    result = analyze_correspondent_policy(42, "Example Bank", documents, [])

    assert len(result.suggestions) == 2
    assert {item.series_discriminator for item in result.suggestions} == {
        "Checking (Candidate 1)",
        "Checking (Candidate 2)",
    }
    serialized = result.model_dump_json()
    assert "1234" not in serialized
    assert "5678" not in serialized


def test_prefers_existing_masked_identifiers_over_title_grouping() -> None:
    documents = [
        _document(
            document_id,
            f"Checking ABC{account} Statement 2026-{month:02d}",
            date(2026, month, 3),
        ).model_copy(update={"account_identifier": f"ending {account}"})
        for document_id, account, month in (
            (1, 1234, 1),
            (2, 1234, 2),
            (3, 5678, 1),
            (4, 5678, 2),
        )
    ]

    result = analyze_correspondent_policy(42, "Example Bank", documents, [])

    assert len(result.suggestions) == 2
    assert {item.series_discriminator for item in result.suggestions} == {
        "Checking (Candidate 1)",
        "Checking (Candidate 2)",
    }
    serialized = result.model_dump_json()
    assert "1234" not in serialized
    assert "5678" not in serialized
    assert "ABC" not in serialized


def test_does_not_split_series_on_unique_document_numbers() -> None:
    documents = [
        _document(
            month,
            f"Utility Invoice {9000 + month} 2026-{month:02d}",
            date(2026, month, 3),
            document_type_id=4,
            document_type="Invoice",
        )
        for month in range(1, 4)
    ]

    result = analyze_correspondent_policy(42, "Utility Co", documents, [])

    assert len(result.suggestions) == 1
    assert result.suggestions[0].expectation_mode == "recurring"


def test_suggests_any_of_policy_for_consistent_tag_family() -> None:
    documents = [
        _document(
            month,
            f"West Street Vet Invoice {date(2026, month, 1):%B %Y}",
            date(2026, month, 15),
            document_type_id=8,
            document_type="Invoice",
            tag_ids=[10, animal_tag],
            tags=["Veterinary", animal_name],
        )
        for month, animal_tag, animal_name in (
            (1, 21, "DOG:Quinn"),
            (2, 22, "DOG:Avery"),
            (3, 21, "DOG:Quinn"),
        )
    ]

    result = analyze_correspondent_policy(42, "West Street Vet", documents, [])

    metadata = result.suggestions[0].metadata
    assert metadata.policy.all_of == [10]
    assert metadata.policy.any_of == [21, 22]
    assert metadata.tag_names == {
        10: "Veterinary",
        21: "DOG:Quinn",
        22: "DOG:Avery",
    }
    assert "tag_family_present_on_all_documents" in metadata.reason_codes


def test_curated_series_membership_overrides_title_grouping() -> None:
    documents = [
        _document(1, "Old Checking Label January 2026", date(2026, 1, 3)),
        _document(2, "Renamed Account February 2026", date(2026, 2, 3)),
        _document(3, "Checking 1234 March 2026", date(2026, 3, 3)),
    ]

    result = analyze_correspondent_policy(
        42,
        "Example Bank",
        documents,
        [
            {
                "id": "checking",
                "name": "Household Checking",
                "correspondent_id": 42,
                "correspondent_name": "Example Bank",
                "document_ids": ["1", "2", "3"],
            }
        ],
    )

    assert len(result.suggestions) == 1
    assert result.suggestions[0].statement_series_id == "checking"
    assert result.suggestions[0].series_discriminator == "Household Checking"


def test_curated_series_membership_overrides_ambiguous_document_kind() -> None:
    documents = [
        _document(
            month,
            f"Account Summary 2026-{month:02d}",
            date(2026, month, 3),
            document_type_id=9,
            document_type="Financial Document",
            tags=["Finance"],
        )
        for month in range(1, 4)
    ]

    result = analyze_correspondent_policy(
        42,
        "Example Bank",
        documents,
        [
            {
                "id": "checking",
                "name": "Household Checking",
                "correspondent_id": 42,
                "correspondent_name": "Example Bank",
                "document_ids": ["1", "2", "3"],
            }
        ],
    )

    assert len(result.suggestions) == 1
    assert result.suggestions[0].kind == "statement"
    assert result.suggestions[0].statement_series_id == "checking"
