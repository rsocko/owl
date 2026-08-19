"""Tests for core.extractors.account_numbers — regex extraction and best-pick logic."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from doc_intelligence_hub.core.extractors.account_numbers import (
    ExtractionResult,
    evaluate_account_identifiers,
    extract_account_numbers,
    write_account_to_paperless,
)
from doc_intelligence_hub.core.paperless import AccountIdentifierClass


class TestExtractAccountNumbers:
    """Tests for the regex-based extraction pipeline."""

    def test_account_last4(self):
        text = "Account #****4321"
        matches = extract_account_numbers(text)
        assert len(matches) == 1
        assert matches[0]["normalized"] == "4321"
        assert matches[0]["pattern"] == "account_last4"

    def test_account_number_full(self):
        text = "Account Number: ABC123456"
        matches = extract_account_numbers(text)
        assert any(m["normalized"] == "ABC123456" for m in matches)

    def test_ending_in(self):
        text = "Card ending in 9876"
        matches = extract_account_numbers(text)
        assert any(
            m["normalized"] == "9876" and m["pattern"] == "payment_card_ending" for m in matches
        )

    def test_last4_variant(self):
        text = "last 4: 5555"
        matches = extract_account_numbers(text)
        assert any(m["normalized"] == "5555" for m in matches)

    def test_last4_in_variant(self):
        text = "last 4 in 5555"
        matches = extract_account_numbers(text)
        assert any(m["normalized"] == "5555" for m in matches)

    def test_card_number(self):
        text = "Card #****1234"
        matches = extract_account_numbers(text)
        assert any(m["normalized"] == "1234" for m in matches)

    def test_masked_number(self):
        text = "XXXX 7890"
        matches = extract_account_numbers(text)
        assert any(m["normalized"] == "7890" for m in matches)

    def test_member_id(self):
        text = "Member ID: WXY1234567"
        matches = extract_account_numbers(text)
        assert any(m["normalized"] == "WXY1234567" for m in matches)

    def test_policy_number(self):
        text = "Policy # POL9988776"
        matches = extract_account_numbers(text)
        assert any(m["normalized"] == "POL9988776" for m in matches)

    def test_claim_number(self):
        text = "Claim No. CLM-2024-0012"
        matches = extract_account_numbers(text)
        assert any("CLM-2024-0012" in m["normalized"] for m in matches)

    def test_deduplicates_same_value(self):
        text = "Account #****4321\nYour acct number: ****4321"
        matches = extract_account_numbers(text)
        values = [m["normalized"] for m in matches]
        assert values.count("4321") == 1

    def test_empty_text_returns_empty(self):
        assert extract_account_numbers("") == []
        assert extract_account_numbers("   ") == []
        assert extract_account_numbers(None) == []

    def test_no_matches(self):
        text = "Thank you for your payment. Please keep this for your records."
        assert extract_account_numbers(text) == []

    def test_multiple_different_numbers(self):
        text = "Account #****4321\nMember ID: ABC9999999\nClaim No. CLM-2024-0001"
        matches = extract_account_numbers(text)
        assert len(matches) >= 3


class TestAccountIdentifierPolicy:
    def test_provider_account_can_project_exact_to_paperless(self):
        decision = evaluate_account_identifiers(
            extract_account_numbers("Account Number: SAMPLE123456")
        )
        assert decision.requires_review is False
        assert decision.projection is not None
        assert decision.projection.paperless_value == "SAMPLE123456"
        assert decision.projection.display_value == "ending 3456"

    def test_bank_account_is_masked_before_projection(self):
        decision = evaluate_account_identifiers(
            extract_account_numbers("Bank Account Number: 123456789")
        )
        assert decision.projection is not None
        assert decision.projection.identifier_class is AccountIdentifierClass.BANK_ACCOUNT
        assert decision.projection.paperless_value == "bank account ending 6789"

    def test_unlabeled_suffix_requires_review(self):
        decision = evaluate_account_identifiers(extract_account_numbers("ending in 9876"))
        assert decision.requires_review is True
        assert decision.projection is not None
        assert decision.projection.identifier_class is AccountIdentifierClass.AMBIGUOUS
        assert decision.projection.paperless_value is None

    def test_multiple_candidates_require_review(self):
        decision = evaluate_account_identifiers(
            extract_account_numbers("Member ID: MEM123456\nPolicy Number: POL998877")
        )
        assert decision.requires_review is True
        assert decision.candidate_count == 2
        assert decision.candidate is None

    def test_claim_and_invoice_are_never_account_fallbacks(self):
        decision = evaluate_account_identifiers(
            extract_account_numbers("Claim Number: CLM-1234\nInvoice Number: INV-5678")
        )
        assert decision.candidate_count == 0
        assert decision.projection is None
        assert decision.requires_review is False


class TestExtractionResult:
    """Tests for the ExtractionResult dataclass."""

    def test_defaults(self):
        r = ExtractionResult(document_id=1)
        assert r.document_id == 1
        assert r.pattern_matches == []
        assert r.success is False
        assert r.error is None


@pytest.mark.asyncio
async def test_write_account_uses_canonical_numeric_field_id() -> None:
    client = AsyncMock()
    client.list_custom_fields.return_value = [
        {"id": 42, "name": "Account Identifier", "data_type": "string"},
        {"id": 7, "name": "di_account_id", "data_type": "string"},
    ]

    assert await write_account_to_paperless(100, "ending 4321", client) is True

    client.update_custom_fields.assert_awaited_once_with(
        100,
        [{"field": 42, "value": "ending 4321"}],
    )


@pytest.mark.asyncio
async def test_write_account_rejects_unmasked_value() -> None:
    client = AsyncMock()
    client.list_custom_fields.return_value = [
        {"id": 42, "name": "Account Identifier", "data_type": "string"}
    ]

    assert await write_account_to_paperless(100, "SAMPLE123456789", client) is False
    client.update_custom_fields.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_classified_provider_account_allows_exact_paperless_value() -> None:
    client = AsyncMock()
    client.list_custom_fields.return_value = [
        {"id": 42, "name": "Account Identifier", "data_type": "string"}
    ]

    assert await write_account_to_paperless(
        100,
        "SAMPLE123456789",
        client,
        identifier_class=AccountIdentifierClass.PROVIDER_ACCOUNT,
        confidence=0.99,
    )
    client.update_custom_fields.assert_awaited_once_with(
        100,
        [{"field": 42, "value": "SAMPLE123456789"}],
    )
