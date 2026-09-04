"""Tests for core.extractors.correction_hints — pure text helpers for issue #171."""

from __future__ import annotations

from doc_intelligence_hub.core.extractors.correction_hints import (
    derive_label_anchor,
    pick_nearest_to_anchor,
)


class TestDeriveLabelAnchor:
    def test_returns_text_immediately_preceding_value(self):
        text = "Statement Summary\nTotal Due: $142.50\nThank you."
        assert derive_label_anchor(text, "$142.50") == "Total Due:"

    def test_trims_to_max_words(self):
        text = (
            "Some long preamble that should be trimmed to just the last few words Total Due: $50.00"
        )
        anchor = derive_label_anchor(text, "$50.00", max_words=3)
        assert anchor == "words Total Due:"
        assert len(anchor.split(" ")) <= 3

    def test_value_not_found_returns_none(self):
        assert derive_label_anchor("Total Due: $142.50", "$999.99") is None

    def test_empty_inputs_return_none(self):
        assert derive_label_anchor("", "$1.00") is None
        assert derive_label_anchor("Total Due: $1.00", "") is None
        assert derive_label_anchor(None, "$1.00") is None
        assert derive_label_anchor("Total Due: $1.00", None) is None

    def test_collapses_whitespace_and_newlines(self):
        text = "Account Summary\n\n   Balance   Due:\n$99.00"
        assert derive_label_anchor(text, "$99.00") == "Balance Due:"

    def test_window_limits_how_far_back_it_looks(self):
        text = "X" * 100 + "Total Due: $10.00"
        anchor = derive_label_anchor(text, "$10.00", window=5)
        # Only the last 5 chars before the value are considered — no "Total Due:" label.
        assert anchor != "Total Due:"


class TestPickNearestToAnchor:
    def test_prefers_candidate_closest_to_anchor(self):
        text = "Subtotal: $10.00 ... Total Due: $142.50 ... Tax: $5.00"
        candidates = [
            (text.index("$10.00"), 10.00),
            (text.index("$142.50"), 142.50),
            (text.index("$5.00"), 5.00),
        ]
        result = pick_nearest_to_anchor(text, candidates, "Total Due:")
        assert result == 142.50

    def test_case_insensitive_anchor_match(self):
        text = "total due: $75.00 and Balance: $5.00"
        candidates = [(text.index("$75.00"), 75.00), (text.index("$5.00"), 5.00)]
        result = pick_nearest_to_anchor(text, candidates, "TOTAL DUE:")
        assert result == 75.00

    def test_anchor_not_found_returns_none(self):
        text = "Amount: $10.00"
        candidates = [(text.index("$10.00"), 10.00)]
        assert pick_nearest_to_anchor(text, candidates, "Total Due:") is None

    def test_no_candidates_returns_none(self):
        assert pick_nearest_to_anchor("Total Due: $10.00", [], "Total Due:") is None

    def test_no_anchor_returns_none(self):
        candidates = [(0, 10.00)]
        assert pick_nearest_to_anchor("Total Due: $10.00", candidates, None) is None
        assert pick_nearest_to_anchor("Total Due: $10.00", candidates, "") is None

    def test_nearest_candidate_too_far_returns_none(self):
        text = "Total Due:" + (" " * 300) + "$10.00"
        candidates = [(text.index("$10.00"), 10.00)]
        assert pick_nearest_to_anchor(text, candidates, "Total Due:", max_distance=250) is None

    def test_candidate_within_max_distance_is_returned(self):
        text = "Total Due:" + (" " * 10) + "$10.00"
        candidates = [(text.index("$10.00"), 10.00)]
        assert pick_nearest_to_anchor(text, candidates, "Total Due:", max_distance=250) == 10.00
