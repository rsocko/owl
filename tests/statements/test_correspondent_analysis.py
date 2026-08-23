from __future__ import annotations

from doc_intelligence_hub.modules.statements.correspondent_analysis import (
    analyze_correspondent_policy,
)
from doc_intelligence_hub.modules.statements.correspondent_models import (
    CorrespondentProfile,
    DocumentExpectation,
    ExpectationEvidence,
    MetadataPolicy,
    TitleConvention,
)


def _document(
    document_id: int,
    title: str,
    created: str,
    *,
    tags: list[int] | None = None,
    source: str | None = None,
    mail_rule_id: int | None = None,
) -> dict:
    document = {
        "id": document_id,
        "title": title,
        "created": created,
        "added": f"{created}T08:00:00Z",
        "tags": tags or [],
        "document_type": 3,
    }
    if source is not None:
        document["ingestion_source"] = source
    if mail_rule_id is not None:
        document["mail_rule_id"] = mail_rule_id
    return document


def _series(
    series_id: str,
    name: str,
    document_ids: list[int],
    dates: list[str],
    *,
    frequency: str = "monthly",
) -> tuple[dict, list[dict]]:
    return (
        {
            "id": series_id,
            "name": name,
            "correspondent_id": 42,
            "correspondent_name": "Example Bank",
            "frequency": frequency,
            "manually_curated": True,
        },
        [
            {
                "series_id": series_id,
                "document_id": str(document_id),
                "statement_date": created,
                "period_label": created[:7],
            }
            for document_id, created in zip(document_ids, dates, strict=True)
        ],
    )


def _analyze(
    documents: list[dict],
    series: list[dict],
    memberships: dict[str, list[dict]],
    *,
    expectations: list[DocumentExpectation] | None = None,
):
    return analyze_correspondent_policy(
        profile=CorrespondentProfile(correspondent_id=42, current_name="Example Bank"),
        raw_documents=documents,
        tag_names={7: "Financial", 11: "PET:Quinn", 12: "PET:Avery"},
        document_type_names={3: "Statement"},
        series=series,
        series_documents=memberships,
        series_overrides={item["id"]: [{"override_type": "rename"}] for item in series},
        expectations=expectations or [],
        acquisition_sources=[],
    )


def test_analysis_keeps_multiple_series_and_explains_title_and_metadata() -> None:
    checking_dates = ["2026-01-03", "2026-02-03", "2026-03-03", "2026-04-03"]
    savings_dates = ["2026-01-15", "2026-02-15", "2026-03-15"]
    checking, checking_members = _series("checking", "Checking 1234", [1, 2, 3, 4], checking_dates)
    savings, savings_members = _series("savings", "Savings 9876", [5, 6, 7], savings_dates)
    documents = [
        _document(
            document_id,
            (
                f"Checking 1234 - Statement - {created[:7]}"
                if document_id != 4
                else "April checking statement"
            ),
            created,
            tags=[7, 11 if document_id % 2 else 12],
            mail_rule_id=9,
        )
        for document_id, created in zip([1, 2, 3, 4], checking_dates, strict=True)
    ]
    documents.extend(
        _document(
            document_id,
            f"Savings 9876 - Statement - {created[:7]}",
            created,
            tags=[7],
            mail_rule_id=9,
        )
        for document_id, created in zip([5, 6, 7], savings_dates, strict=True)
    )

    result = _analyze(
        documents,
        [checking, savings],
        {"checking": checking_members, "savings": savings_members},
    )

    assert [item.statement_series_id for item in result.suggestions] == ["checking", "savings"]
    checking_result = result.suggestions[0]
    assert checking_result.expectation_mode == "recurring"
    assert checking_result.cadence.frequency == "monthly"
    assert checking_result.title.convention.template == "{series} - {kind} - {period}"
    assert checking_result.title.coverage == 0.75
    assert checking_result.title.exception_document_ids == [4]
    assert len(checking_result.title.examples) == 3
    assert checking_result.metadata.policy.all_of == [7]
    assert checking_result.metadata.policy.any_of == [11, 12]
    assert checking_result.metadata.required_tag_families[0].family == "PET"
    assert checking_result.metadata.policy.required_document_type_id == 3
    assert checking_result.acquisition.channel == "paperless_mail"
    assert checking_result.acquisition.reason_codes == ["mail_rule_evidence"]


def test_contradictory_cadence_and_acquisition_remain_unknown() -> None:
    dates = ["2026-01-03", "2026-04-03", "2026-05-03"]
    series, memberships = _series("mixed", "Mixed", [1, 2, 3], dates)
    documents = [
        _document(1, "Mixed - Statement - 2026-01", dates[0], mail_rule_id=9),
        _document(2, "Mixed - Statement - 2026-04", dates[1], source="direct_api"),
        _document(3, "Mixed - Statement - 2026-05", dates[2], mail_rule_id=9),
    ]

    result = _analyze(documents, [series], {"mixed": memberships})
    suggestion = result.suggestions[0]

    assert suggestion.expectation_mode == "unknown"
    assert suggestion.cadence is None
    assert suggestion.evidence.reason_codes == [
        "contradictory_cadence_evidence",
        "user_curated_series",
    ]
    assert suggestion.acquisition.channel == "unknown"
    assert suggestion.acquisition.reason_codes == ["contradictory_acquisition_evidence"]


def test_insufficient_one_off_and_irregular_evidence_are_distinguished() -> None:
    result = _analyze(
        [
            _document(1, "Ordinary notice", "2026-01-01"),
            _document(2, "Final closing statement", "2026-02-01"),
            _document(3, "Irregular statement", "2026-01-01"),
            _document(4, "Irregular statement", "2026-03-01"),
            _document(5, "Irregular statement", "2026-07-01"),
        ],
        [],
        {},
    )
    by_documents = {tuple(item.document_ids): item for item in result.suggestions}

    assert by_documents[(1,)].expectation_mode == "unknown"
    assert by_documents[(2,)].expectation_mode == "one_off"
    assert by_documents[(3, 4, 5)].expectation_mode == "irregular"


def test_confirmed_policy_is_reused_and_missing_title_fields_are_findings() -> None:
    dates = ["2026-01-03", "2026-02-03", "2026-03-03"]
    series, memberships = _series("checking", "Checking", [1, 2, 3], dates)
    expectation = DocumentExpectation(
        id="expectation-1",
        correspondent_id=42,
        kind="statement",
        statement_series_id="checking",
        expectation_mode="irregular",
        status="confirmed",
        evidence=ExpectationEvidence(source="user"),
        title_convention=TitleConvention(
            template="{subject} - {kind} - {document_date}",
            date_basis="document_date",
            example="Quinn - Statement - 2026-01-03",
        ),
        metadata_policy=MetadataPolicy(all_of=[7]),
    )
    documents = [
        _document(1, "Quinn - Statement - 2026-01-03", dates[0], tags=[7, 11]),
        _document(2, "Statement - 2026-02-03", dates[1], tags=[7]),
        _document(3, "Quinn - Statement - 2026-03-03", dates[2], tags=[7, 11]),
    ]

    result = _analyze(
        documents,
        [series],
        {"checking": memberships},
        expectations=[expectation],
    )
    suggestion = result.suggestions[0]

    assert suggestion.expectation_mode == "irregular"
    assert suggestion.existing_expectation_id == "expectation-1"
    assert suggestion.evidence.source == "user"
    assert suggestion.title.coverage == 1.0
    assert suggestion.title.missing_required_fields[0].model_dump() == {
        "document_id": 2,
        "missing_fields": ["subject"],
    }
    assert suggestion.metadata.reason_codes == ["user_confirmed_metadata_policy"]


def test_analysis_redacts_sensitive_numbers_and_is_deterministic() -> None:
    dates = ["2026-01-03", "2026-02-03", "2026-03-03"]
    series, memberships = _series("checking", "Account 123456789", [1, 2, 3], dates)
    documents = [
        _document(
            document_id,
            f"Account 123456789 - Statement - {created[:7]}",
            created,
        )
        for document_id, created in zip([1, 2, 3], dates, strict=True)
    ]

    first = _analyze(documents, [series], {"checking": memberships})
    second = _analyze(documents, [series], {"checking": memberships})

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    serialized = first.model_dump_json()
    assert "123456789" not in serialized
    assert first.suggestions[0].series_discriminator == "Account ****"


def test_analysis_redacts_grouped_and_alphanumeric_identifiers_but_preserves_dates() -> None:
    result = _analyze(
        [
            _document(
                1,
                "Account AB123456 and 123-45-6789 - Statement - 2026-01-03",
                "2026-01-03",
            )
        ],
        [],
        {},
    )

    serialized = result.model_dump_json()
    assert "AB123456" not in serialized
    assert "123-45-6789" not in serialized
    assert "2026-01-03" in serialized


def test_unconfirmed_expectation_is_not_reported_as_user_confirmed() -> None:
    dates = ["2026-01-03", "2026-02-03", "2026-03-03"]
    series, memberships = _series("checking", "Checking", [1, 2, 3], dates)
    dismissed = DocumentExpectation(
        id="dismissed-1",
        correspondent_id=42,
        kind="statement",
        statement_series_id="checking",
        expectation_mode="irregular",
        status="dismissed",
        evidence=ExpectationEvidence(source="user"),
        title_convention=TitleConvention(
            template="{subject} - {kind}",
            date_basis="document_date",
            example="Quinn - Statement",
        ),
        metadata_policy=MetadataPolicy(all_of=[99]),
    )
    documents = [
        _document(
            document_id,
            f"Checking - Statement - {created[:7]}",
            created,
            tags=[7],
        )
        for document_id, created in zip([1, 2, 3], dates, strict=True)
    ]

    result = _analyze(
        documents,
        [series],
        {"checking": memberships},
        expectations=[dismissed],
    )
    suggestion = result.suggestions[0]

    assert suggestion.expectation_mode == "recurring"
    assert suggestion.evidence.source == "paperless"
    assert "user_confirmed_title_convention" not in suggestion.title.reason_codes
    assert suggestion.metadata.policy.all_of == [7]


def test_multiple_tag_families_are_exposed_as_separate_required_rules() -> None:
    series, memberships = _series(
        "visits",
        "Visits",
        [1, 2],
        ["2026-01-03", "2026-02-03"],
    )
    result = analyze_correspondent_policy(
        profile=CorrespondentProfile(correspondent_id=42, current_name="Example Vet"),
        raw_documents=[
            {
                **_document(1, "Invoice", "2026-01-03", tags=[11, 21]),
                "document_type": 4,
            },
            {
                **_document(2, "Invoice", "2026-02-03", tags=[12, 22]),
                "document_type": 4,
            },
        ],
        tag_names={
            11: "PET:Quinn",
            12: "PET:Avery",
            21: "VISIT:Wellness",
            22: "VISIT:Urgent",
        },
        document_type_names={4: "Invoice"},
        series=[series],
        series_documents={"visits": memberships},
        series_overrides={"visits": []},
        expectations=[],
        acquisition_sources=[],
    )
    metadata = result.suggestions[0].metadata

    assert metadata.policy.any_of == []
    assert [family.family for family in metadata.required_tag_families] == ["PET", "VISIT"]
    assert "multiple_tag_families_require_separate_rules" in metadata.reason_codes


def test_discarded_document_type_evidence_does_not_inflate_confidence() -> None:
    result = _analyze(
        [_document(1, "Ordinary notice", "2026-01-01")],
        [],
        {},
    )
    metadata = result.suggestions[0].metadata

    assert metadata.policy.required_document_type_id is None
    assert metadata.confidence == 0.0
    assert metadata.reason_codes == ["insufficient_metadata_consistency"]


def test_universal_child_tag_remains_all_of_instead_of_becoming_any_of() -> None:
    series, memberships = _series(
        "visits",
        "Visits",
        [1, 2],
        ["2026-01-03", "2026-02-03"],
    )
    result = analyze_correspondent_policy(
        profile=CorrespondentProfile(correspondent_id=42, current_name="Example Vet"),
        raw_documents=[
            _document(1, "Invoice", "2026-01-03", tags=[11, 13]),
            _document(2, "Invoice", "2026-02-03", tags=[12, 13]),
        ],
        tag_names={11: "PET:Quinn", 12: "PET:Avery", 13: "PET:Household"},
        document_type_names={3: "Invoice"},
        series=[series],
        series_documents={"visits": memberships},
        series_overrides={"visits": []},
        expectations=[],
        acquisition_sources=[],
    )
    metadata = result.suggestions[0].metadata

    assert metadata.policy.all_of == [13]
    assert metadata.policy.any_of == []
    assert metadata.required_tag_families == []


def test_uncurated_series_with_repeated_account_hints_suggests_distinct_series() -> None:
    dates = ["2026-01-03", "2026-02-03", "2026-01-15", "2026-02-15"]
    series, memberships = _series("combined", "Combined", [1, 2, 3, 4], dates)
    series["manually_curated"] = False
    for membership, account_hint in zip(
        memberships,
        ["Checking 1234", "Checking 1234", "Savings 9876", "Savings 9876"],
        strict=True,
    ):
        membership["account_hint"] = account_hint
    documents = [
        _document(document_id, f"Statement - {created[:7]}", created)
        for document_id, created in zip([1, 2, 3, 4], dates, strict=True)
    ]

    result = _analyze(documents, [series], {"combined": memberships})

    assert [item.series_discriminator for item in result.suggestions] == [
        "Checking 1234",
        "Savings 9876",
    ]
    assert all(item.candidate_series for item in result.suggestions)
    assert all(item.statement_series_id is None for item in result.suggestions)
    assert all(item.source_statement_series_id == "combined" for item in result.suggestions)
    assert all(
        "account_hint_candidate" in item.evidence.reason_codes for item in result.suggestions
    )
