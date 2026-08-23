from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from doc_intelligence_hub.modules.statements.correspondent_models import (
    AcquisitionSourceCreate,
    Cadence,
    DocumentExpectationCreate,
    ExpectationEvidence,
    MetadataPolicy,
    TitleConvention,
    paperless_deployment_identity,
)


def test_deployment_identity_is_stable_and_not_the_url() -> None:
    first = paperless_deployment_identity("HTTPS://Paperless.Example.test:443/archive/")
    second = paperless_deployment_identity("https://paperless.example.test:443/archive")

    assert first == second
    assert first.startswith("paperless:")
    assert "example" not in first


def test_title_convention_renders_deterministically() -> None:
    convention = TitleConvention(
        template="{correspondent} - {series} - {kind} - {period}",
        date_basis="period",
        example="Chase - Checking 1234 - Statement - 2026-07",
    )

    rendered = convention.render(
        {
            "correspondent": "Chase",
            "series": "Checking 1234",
            "kind": "Statement",
            "period": "2026-07",
        }
    )

    assert rendered == "Chase - Checking 1234 - Statement - 2026-07"


def test_title_convention_rejects_missing_required_fields() -> None:
    convention = TitleConvention(
        template="{series} - {document_date}",
        date_basis="document_date",
        example="Invoice - 2026-08-22",
    )

    with pytest.raises(ValueError, match="document_date"):
        convention.render({"series": "Invoice"})

    assert (
        convention.render({"series": "Invoice", "document_date": date(2026, 8, 22)})
        == "Invoice - 2026-08-22"
    )


def test_metadata_any_of_requires_a_child_tag() -> None:
    policy = MetadataPolicy(any_of=[11, 12], none_of=[99])

    assert not policy.tags_satisfy([10])
    assert policy.tags_satisfy([10, 11])
    assert not policy.tags_satisfy([11, 99])


@pytest.mark.parametrize(
    ("status", "mode"),
    [
        ("suggested", "recurring"),
        ("dismissed", "recurring"),
        ("retired", "recurring"),
        ("confirmed", "irregular"),
        ("confirmed", "not_expected"),
    ],
)
def test_non_alerting_expectation_states_are_ineligible(status: str, mode: str) -> None:
    expectation = DocumentExpectationCreate(
        kind="invoice",
        expectation_mode=mode,
        status=status,
        cadence=Cadence(frequency="monthly") if mode == "recurring" else None,
        evidence=ExpectationEvidence(source="user"),
    )

    assert not expectation.can_emit_missing_alert()


def test_confirmed_recurring_expectation_requires_cadence() -> None:
    with pytest.raises(ValidationError, match="require cadence"):
        DocumentExpectationCreate(
            kind="invoice",
            expectation_mode="recurring",
            status="confirmed",
            evidence=ExpectationEvidence(source="user"),
        )


def test_acquisition_source_rejects_sensitive_url_components() -> None:
    with pytest.raises(ValidationError, match="without credentials"):
        AcquisitionSourceCreate(
            channel="portal_manual",
            delivery_mode="pull",
            portal_url="https://user:secret@example.test/statements?account=123",
        )
