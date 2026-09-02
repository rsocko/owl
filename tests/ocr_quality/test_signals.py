from __future__ import annotations

from doc_intelligence_hub.modules.ocr_quality.models import ReasonCode
from doc_intelligence_hub.modules.ocr_quality.signals import compute_text_signals


class TestComputeTextSignals:
    def test_empty_content_returns_zeroed_signals(self):
        signals = compute_text_signals(None)
        assert signals.content_length == 0
        assert signals.word_count == 0
        assert signals.preliminary_score == 0
        assert signals.reason_codes == (ReasonCode.EMPTY_CONTENT,)

    def test_empty_string_is_treated_as_empty(self):
        signals = compute_text_signals("")
        assert signals.reason_codes == (ReasonCode.EMPTY_CONTENT,)

    def test_healthy_digital_text_scores_high(self):
        text = (
            "This is a normal, well-formed invoice document with plenty of "
            "distinct English words describing charges, dates, and totals "
            "for the billing period in question, spanning several sentences."
        )
        signals = compute_text_signals(text)
        assert signals.word_count > 20
        assert signals.preliminary_score >= 70
        assert signals.reason_codes == (ReasonCode.OK,)

    def test_very_short_document_is_penalized(self):
        signals = compute_text_signals("Hi")
        assert signals.word_count == 1
        assert signals.preliminary_score < 100

    def test_repetitive_garbage_scores_low(self):
        text = "??? " * 200
        signals = compute_text_signals(text)
        assert signals.repetition_ratio > 0.5
        assert signals.preliminary_score < 50

    def test_heavy_non_ascii_reduces_score(self):
        text = "\u00e9\u00e8\u00ea\u00eb" * 100 + " word " * 30
        signals = compute_text_signals(text)
        assert signals.non_ascii_ratio > 0.25

    def test_table_shape_hint_detected(self):
        text = "Name    Age    City\nAlice   30     Boston\nBob     40     Denver\n"
        signals = compute_text_signals(text)
        assert signals.table_shape_hint is True

    def test_code_shape_hint_detected(self):
        text = "def foo():\n    return {1: 2}\nclass Bar:\n    pass\n"
        signals = compute_text_signals(text)
        assert signals.code_shape_hint is True

    def test_score_is_bounded_0_to_100(self):
        for text in ("", "a", "normal text here", "a" * 10000, "\u00e9" * 5000):
            signals = compute_text_signals(text)
            assert 0 <= signals.preliminary_score <= 100

    def test_no_raw_text_is_retained_on_signals_object(self):
        signals = compute_text_signals("some private patient content")
        # The dataclass must only expose derived numeric/categorical fields.
        field_names = {f for f in signals.__dataclass_fields__}
        assert "content" not in field_names
        assert "text" not in field_names
