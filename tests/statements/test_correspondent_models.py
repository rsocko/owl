from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from doc_intelligence_hub.modules.statements.correspondent_models import (
    AcquisitionSourceCreate,
    AvailabilityWindow,
    Cadence,
    CalendarRule,
    DocumentExpectationCreate,
    EscalationPolicy,
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
        document_ids=[1] if status == "confirmed" and mode != "not_expected" else [],
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
            document_ids=[1],
            expectation_mode="recurring",
            status="confirmed",
            evidence=ExpectationEvidence(source="user"),
        )


def test_confirmed_non_statement_expectation_requires_document_membership() -> None:
    with pytest.raises(ValidationError, match="durable document membership"):
        DocumentExpectationCreate(
            kind="invoice",
            expectation_mode="irregular",
            status="confirmed",
            evidence=ExpectationEvidence(source="user"),
        )


def test_user_can_define_important_annual_tax_form_from_one_example() -> None:
    expectation = DocumentExpectationCreate(
        kind="record",
        document_ids=[101],
        series_discriminator="Annual tax form",
        expectation_mode="periodic",
        status="confirmed",
        cadence=Cadence(frequency="annual"),
        importance="important",
        monitoring_mode="deadline",
        calendar_rule=CalendarRule(
            rule_type="tax_year",
            expected_month=2,
            tax_year_offset=-1,
        ),
        availability_window=AvailabilityWindow(earliest_days=0, latest_days=28),
        grace_period_days=7,
        escalation_policy=EscalationPolicy(
            reminder_days_before_due=[7, 1],
            overdue_reminder_interval_days=7,
            max_overdue_reminders=3,
        ),
        evidence=ExpectationEvidence(source="user", sample_size=1),
    )

    assert expectation.importance == "important"
    assert expectation.calendar_rule.expected_month == 2
    assert expectation.can_emit_missing_alert()


@pytest.mark.parametrize(
    ("occurrence_status", "eligible"),
    [
        ("pending", True),
        ("overdue", True),
        ("cancelled", False),
        ("fulfilled", False),
    ],
)
def test_one_off_occurrence_alert_lifecycle(
    occurrence_status: str, eligible: bool
) -> None:
    fulfilled_at = (
        datetime(2026, 10, 10, tzinfo=timezone.utc)
        if occurrence_status == "fulfilled"
        else None
    )
    expectation = DocumentExpectationCreate(
        kind="record",
        document_ids=[101],
        expectation_mode="one_off",
        status="confirmed",
        monitoring_mode="deadline",
        calendar_rule=CalendarRule(
            rule_type="one_off",
            occurrence_id="tax-notice-2026",
            due_start=date(2026, 10, 1),
            due_end=date(2026, 10, 31),
            occurrence_status=occurrence_status,
            fulfilled_at=fulfilled_at,
        ),
        evidence=ExpectationEvidence(source="user"),
    )

    assert expectation.can_emit_missing_alert() is eligible


def test_importance_does_not_make_suggestion_alertable() -> None:
    expectation = DocumentExpectationCreate(
        kind="record",
        document_ids=[101],
        expectation_mode="periodic",
        status="suggested",
        cadence=Cadence(frequency="annual"),
        importance="critical",
        monitoring_mode="deadline",
        calendar_rule=CalendarRule(rule_type="annual_month", expected_month=2),
        evidence=ExpectationEvidence(source="user"),
    )

    assert not expectation.can_emit_missing_alert()


def test_irregular_policy_rejects_monitoring() -> None:
    with pytest.raises(ValidationError, match="cannot enable monitoring"):
        DocumentExpectationCreate(
            kind="record",
            document_ids=[101],
            expectation_mode="irregular",
            status="confirmed",
            monitoring_mode="presence",
            evidence=ExpectationEvidence(source="user"),
        )


def test_acquisition_source_rejects_sensitive_url_components() -> None:
    with pytest.raises(ValidationError, match="without credentials"):
        AcquisitionSourceCreate(
            channel="portal_manual",
            delivery_mode="pull",
            portal_url="https://user:secret@example.test/statements?account=123",
        )
