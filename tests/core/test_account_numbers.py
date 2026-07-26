"""Tests for core.extractors.account_numbers — regex extraction and best-pick logic."""

from __future__ import annotations


from doc_intelligence_hub.core.extractors.account_numbers import (
    ExtractionResult,
    extract_account_numbers,
    pick_best_account_identifier,
)


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
        assert any(m["normalized"] == "9876" and m["pattern"] == "ending_in" for m in matches)

    def test_last4_variant(self):
        text = "last 4: 5555"
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


class TestPickBestAccountIdentifier:
    """Tests for the best-pick heuristic."""

    def test_prefers_last4(self):
        matches = [
            {"pattern": "member_id", "value": "XYZ123", "normalized": "XYZ123"},
            {"pattern": "account_last4", "value": "4321", "normalized": "4321"},
        ]
        assert pick_best_account_identifier(matches) == "ending 4321"

    def test_prefers_ending_in(self):
        matches = [
            {"pattern": "ending_in", "value": "9876", "normalized": "9876"},
            {"pattern": "policy_number", "value": "POL123", "normalized": "POL123"},
        ]
        assert pick_best_account_identifier(matches) == "ending 9876"

    def test_falls_back_to_first_full(self):
        matches = [
            {"pattern": "member_id", "value": "MEM123456", "normalized": "MEM123456"},
        ]
        assert pick_best_account_identifier(matches) == "MEM123456"

    def test_empty_returns_none(self):
        assert pick_best_account_identifier([]) is None


class TestExtractionResult:
    """Tests for the ExtractionResult dataclass."""

    def test_defaults(self):
        r = ExtractionResult(document_id=1)
        assert r.document_id == 1
        assert r.account_numbers == []
        assert r.success is False
        assert r.error is None
