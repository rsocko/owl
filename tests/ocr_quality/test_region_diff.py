"""Unit tests for the box-level diffing heuristic (region_diff.py)."""

from __future__ import annotations

from doc_intelligence_hub.modules.ocr_quality.region_diff import (
    DiffWord,
    diff_word_boxes,
    diff_word_boxes_from_dicts,
)

PAGE_WIDTH = 600.0
PAGE_HEIGHT = 800.0


def word(
    text: str, x0: float, top: float, x1: float | None = None, bottom: float | None = None
) -> DiffWord:
    return DiffWord(
        text=text,
        x0=x0,
        top=top,
        x1=x1 if x1 is not None else x0 + 40.0,
        bottom=bottom if bottom is not None else top + 12.0,
    )


class TestIdenticalPages:
    def test_identical_word_lists_produce_no_diff(self):
        words = [word("Hello", 10, 50), word("World", 60, 50)]
        result = diff_word_boxes(words, words, page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT)
        assert result.removed_from_b == []
        assert result.added_in_b == []
        assert result.shifted == []

    def test_empty_both_sides_produces_no_diff(self):
        result = diff_word_boxes([], [], page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT)
        assert result.removed_from_b == []
        assert result.added_in_b == []
        assert result.shifted == []


class TestAddedAndRemoved:
    def test_word_only_in_a_is_removed_from_b(self):
        words_a = [word("Hello", 10, 50), word("World", 60, 50)]
        words_b = [word("Hello", 10, 50)]
        result = diff_word_boxes(words_a, words_b, page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT)
        assert result.removed_from_b == [1]
        assert result.added_in_b == []
        assert result.shifted == []

    def test_word_only_in_b_is_added_in_b(self):
        words_a = [word("Hello", 10, 50)]
        words_b = [word("Hello", 10, 50), word("World", 60, 50)]
        result = diff_word_boxes(words_a, words_b, page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT)
        assert result.removed_from_b == []
        assert result.added_in_b == [1]
        assert result.shifted == []

    def test_all_different_words_are_all_added_and_removed(self):
        words_a = [word("Woof", 10, 50), word("Bark", 60, 50)]
        words_b = [word("Fizz", 300, 700), word("Buzz", 400, 700)]
        result = diff_word_boxes(words_a, words_b, page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT)
        assert result.removed_from_b == [0, 1]
        assert result.added_in_b == [0, 1]
        assert result.shifted == []

    def test_empty_a_means_every_b_word_is_added(self):
        words_b = [word("Hello", 10, 50), word("World", 60, 50)]
        result = diff_word_boxes([], words_b, page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT)
        assert result.added_in_b == [0, 1]
        assert result.removed_from_b == []


class TestShifted:
    def test_same_word_moved_far_is_shifted(self):
        words_a = [word("Hello", 10, 50)]
        words_b = [word("Hello", 10, 400)]  # moved ~350pt down, well beyond threshold
        result = diff_word_boxes(words_a, words_b, page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT)
        assert result.removed_from_b == []
        assert result.added_in_b == []
        assert len(result.shifted) == 1
        assert result.shifted[0].index_a == 0
        assert result.shifted[0].index_b == 0
        assert result.shifted[0].distance > 0

    def test_same_word_moved_slightly_is_not_shifted(self):
        words_a = [word("Hello", 10, 50)]
        words_b = [word("Hello", 11, 50.5)]  # sub-point jitter
        result = diff_word_boxes(words_a, words_b, page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT)
        assert result.shifted == []
        assert result.removed_from_b == []
        assert result.added_in_b == []


class TestMixedPage:
    def test_page_with_added_removed_and_shifted_words(self):
        words_a = [
            word("Alpha", 10, 50),  # will be removed (no match in B)
            word("Beta", 60, 50),  # matches exactly
            word("Gamma", 110, 50),  # shifted far in B
        ]
        words_b = [
            word("Beta", 60, 50),
            word("Gamma", 110, 500),  # shifted
            word("Delta", 300, 700),  # added, no match in A
        ]
        result = diff_word_boxes(words_a, words_b, page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT)
        assert result.removed_from_b == [0]
        assert result.added_in_b == [2]
        assert len(result.shifted) == 1
        assert result.shifted[0].index_a == 2
        assert result.shifted[0].index_b == 1


class TestFromDicts:
    def test_accepts_raw_region_word_dicts(self):
        words_a = [{"text": "Hello", "x0": 10, "top": 50, "x1": 50, "bottom": 62}]
        words_b = [{"text": "Hello", "x0": 10, "top": 50, "x1": 50, "bottom": 62}]
        result = diff_word_boxes_from_dicts(
            words_a, words_b, page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT
        )
        assert result.removed_from_b == []
        assert result.added_in_b == []

    def test_to_dict_shape(self):
        words_a = [{"text": "Hello", "x0": 10, "top": 50, "x1": 50, "bottom": 62}]
        words_b = [{"text": "World", "x0": 300, "top": 700, "x1": 340, "bottom": 712}]
        result = diff_word_boxes_from_dicts(
            words_a, words_b, page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT
        )
        payload = result.to_dict()
        assert payload["removed_from_b"] == [0]
        assert payload["added_in_b"] == [0]
        assert payload["shifted"] == []
