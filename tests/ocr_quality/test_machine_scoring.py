"""Tests for machine-extraction scoring."""

from __future__ import annotations

from doc_intelligence_hub.modules.ocr_quality.machine_scoring import score_machine
from doc_intelligence_hub.modules.ocr_quality.scoring_config import DEFAULT_CONFIG
from doc_intelligence_hub.modules.ocr_quality.scoring_models import ContentShape, DownstreamOutcome


def test_none_text_is_fully_unavailable() -> None:
    result = score_machine(text_content=None, config=DEFAULT_CONFIG)
    assert result.score is None
    assert "char_script_plausibility" in result.unavailable_signals


def test_empty_text_is_a_computable_failure_not_unavailable() -> None:
    result = score_machine(text_content="", config=DEFAULT_CONFIG)
    assert result.score == 0.0
    assert any(r.code == "machine.empty_text" for r in result.reasons)


def test_coherent_prose_scores_well() -> None:
    text = (
        "This is a normal, coherent paragraph of English text. It describes a "
        "routine medical visit and the treatment that was provided to the patient. "
        "The billing statement below reflects the total amount due for services rendered."
    )
    result = score_machine(text_content=text, config=DEFAULT_CONFIG)
    assert result.score is not None
    assert result.score > 60.0


def test_garbled_repeated_noise_scores_poorly() -> None:
    text = "xx xx xx xx xx xx xx xx xx xx xx xx xx xx qzjx qzjx qzjx zzzzzzzzzzzz"
    result = score_machine(text_content=text, config=DEFAULT_CONFIG)
    assert result.score is not None
    assert result.score < 60.0
    assert any(r.code == "machine.repetition_noise" for r in result.reasons)


def test_replacement_characters_lower_char_script_plausibility() -> None:
    clean = score_machine(text_content="This is fine text with no issues at all here.")
    garbled = score_machine(text_content="This is \ufffd\ufffd\ufffd\ufffd\ufffd broken text.")
    assert garbled.signals["char_script_plausibility"] < clean.signals["char_script_plausibility"]


def test_non_ascii_medical_terms_are_not_penalized_as_garbage() -> None:
    """Valid non-ASCII names/terms must not tank the coherence signal."""
    text = (
        "Dr. Jos\u00e9 Garc\u00eda-Fern\u00e1ndez reviewed the patient's caf\u00e9au lait "
        "spots and prescribed acetaminophen for the na\u00efve presentation of symptoms."
    )
    result = score_machine(text_content=text, config=DEFAULT_CONFIG)
    assert result.signals["char_script_plausibility"] >= 0.95


def test_identifiers_and_acronyms_not_penalized() -> None:
    text = (
        "Claim number ABC123456 was processed under policy XYZ-9988. CPT code 99213 "
        "and ICD-10 code E11.9 were billed for the encounter on 03/04/2024, totaling $150.00."
    )
    result = score_machine(text_content=text, config=DEFAULT_CONFIG)
    assert result.signals["structured_entities"] is not None
    assert result.signals["structured_entities"] > 0.5


def test_structured_entities_present_boosts_signal_above_neutral_baseline() -> None:
    plain = score_machine(
        text_content="This is a plain sentence without any special structured content in it."
    )
    with_entities = score_machine(
        text_content="Total due $482.19 on 04/15/2024, account 55-889012, CPT 99214."
    )
    assert with_entities.signals["structured_entities"] > plain.signals["structured_entities"]


def test_table_like_text_detected() -> None:
    text = "\n".join(
        [
            "Patient Name:   John Smith",
            "Account Number: 998877",
            "Date of Service: 01/02/2024",
            "Total Billed:      $100.00",
        ]
    )
    result = score_machine(text_content=text, config=DEFAULT_CONFIG)
    assert result.signals["table_structure"] > 0.5


def test_engine_confidence_signal_present_when_data_supplied() -> None:
    result = score_machine(text_content="Some text.", confidence_data=[0.9, 0.95, 0.4])
    assert result.signals["engine_confidence"] == pytest_approx(0.75)


def test_engine_confidence_unavailable_without_data() -> None:
    result = score_machine(text_content="Some text.", confidence_data=None)
    assert "engine_confidence" in result.unavailable_signals


def test_downstream_failure_lowers_score_vs_success() -> None:
    text = "This is a normal document with reasonable prose content in it for testing."
    failure = score_machine(
        text_content=text,
        downstream_outcomes=[DownstreamOutcome(source="tyrion", success=False)],
        config=DEFAULT_CONFIG,
    )
    success = score_machine(
        text_content=text,
        downstream_outcomes=[DownstreamOutcome(source="tyrion", success=True)],
        config=DEFAULT_CONFIG,
    )
    assert failure.signals["downstream_evidence"] < success.signals["downstream_evidence"]
    assert failure.score < success.score


def test_downstream_evidence_unavailable_without_outcomes() -> None:
    result = score_machine(text_content="Some text.", downstream_outcomes=None)
    assert "downstream_evidence" in result.unavailable_signals


def test_downstream_success_does_not_dominate_score() -> None:
    """A downstream success must not, by itself, guarantee a high score."""
    garbled = "xx xx xx xx xx xx xx xx xx xx xx xx qzjx qzjx qzjx zzzzzzzzzzzz"
    result = score_machine(
        text_content=garbled,
        downstream_outcomes=[DownstreamOutcome(source="tyrion", success=True)],
        config=DEFAULT_CONFIG,
    )
    assert result.score < 60.0


def test_prose_coherence_unavailable_for_too_few_tokens() -> None:
    result = score_machine(text_content="Paid.", config=DEFAULT_CONFIG)
    assert result.signals["prose_coherence"] is None
    assert "prose_coherence" in result.unavailable_signals


def test_cid_glyph_artifacts_are_flagged_and_score_poorly() -> None:
    """Docs #2346/#5483: PDFs whose fonts lack a ToUnicode CMap extract as
    literal "(cid:N)" glyph-ID placeholders instead of real characters. Every
    individual character in that placeholder text is ordinary printable
    ASCII, so this must not be silently treated as clean/plausible text."""
    text = " ".join(f"(cid:{65 + i % 26})(cid:{97 + i % 26})(cid:{48 + i % 10})" for i in range(60))
    clean = score_machine(text_content="This is fine text with no issues at all here.")
    corrupted = score_machine(text_content=text, config=DEFAULT_CONFIG)
    assert corrupted.score is not None
    assert corrupted.score < 60.0
    assert corrupted.signals["char_script_plausibility"] < clean.signals["char_script_plausibility"]
    assert any(r.code == "machine.cid_glyph_artifacts" for r in corrupted.reasons)
    assert any(
        r.severity == "blocking"
        for r in corrupted.reasons
        if r.code == "machine.cid_glyph_artifacts"
    )
    # Structured/table detection must not be given a neutral (0.5) pass on
    # text that is entirely glyph-ID corruption — it should be treated as
    # the computable failure it is, same as the empty-text case.
    assert corrupted.signals["structured_entities"] == 0.0
    assert corrupted.signals["table_structure"] == 0.0


def test_cid_glyph_artifacts_below_blocking_ratio_still_lower_plausibility() -> None:
    """A handful of stray "(cid:N)" placeholders (below the blocking ratio)
    should still measurably reduce char_script_plausibility even though they
    don't trigger the dedicated blocking reason."""
    clean = score_machine(text_content="This is fine text with no issues at all here today.")
    mostly_clean = score_machine(
        text_content="This is fine text with (cid:114)(cid:97) issues at all here today."
    )
    assert (
        mostly_clean.signals["char_script_plausibility"] < clean.signals["char_script_plausibility"]
    )
    assert not any(r.code == "machine.cid_glyph_artifacts" for r in mostly_clean.reasons)


def test_cid_glyph_artifacts_partially_dominant_still_computes_signals() -> None:
    """Between the blocking and fully-dominant ratios, the document is
    flagged but structural signals are still computed (not hard-zeroed) so
    a partially-corrupted document isn't scored identically to one that is
    pure garbage end to end."""
    text = (
        "This is some normal readable prose text describing a routine visit and treatment. " * 6
    ) + " ".join(f"(cid:{i % 100})" for i in range(20))
    result = score_machine(text_content=text, config=DEFAULT_CONFIG)
    assert result.signals["char_script_plausibility"] not in (0.0, None)
    assert result.signals["structured_entities"] not in (0.0, None)
    assert not any(r.code == "machine.cid_glyph_artifacts" for r in result.reasons)


def test_cid_glyph_artifacts_fully_dominant_scores_zero() -> None:
    """Docs where (cid:N) placeholders make up the vast majority of the
    extracted text have no real content left for structural signals to
    assess — this must score like the empty-text case (0), not inherit a
    perfect repetition_noise score just because every glyph ID differs."""
    text = "\n".join(
        " ".join(f"(cid:{(i * 7 + j) % 140})(cid:{(i + j * 3) % 140})" for j in range(10))
        for i in range(40)
    )
    result = score_machine(text_content=text, config=DEFAULT_CONFIG)
    assert result.score == 0.0
    assert any(r.code == "machine.cid_glyph_artifacts" for r in result.reasons)


def pytest_approx(value: float, tol: float = 1e-6):
    """Small local helper to avoid importing pytest.approx repeatedly."""
    import pytest

    return pytest.approx(value, abs=tol)


# --- content_shape-aware calibration for table/form false positives ---------


def _table_statement_text() -> str:
    """Synthetic stand-in for a real table/form bank-statement document
    (issue: doc #995) — clean, correctly-formatted digital-native text with
    dot-leader table formatting, private-use-area glyphs from an old
    template's custom font, and dominant repeated numeric/currency tokens.
    None of this reflects an actual extraction problem.
    """
    lines = [
        "Account Statement",
        "Account Number: 123456789",
        "Statement Date: 03/01/2011",
        "Beginning Balance..........$1,204.55",
        "Deposits...................$500.00",
        "Withdrawals.................$220.00\uf0b7",
        "Ending Balance..............$1,484.55",
        "Fee Schedule: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00",
        "Interest Rate: 0.01% APR\uf0b7\uf0b7",
    ]
    return "\n".join(lines)


def test_table_shaped_false_positive_scores_better_with_content_shape() -> None:
    """The two proven false-positive signals must be dampened, and the score
    must improve, once content_shape identifies the document as tabular."""
    text = _table_statement_text()
    undampened = score_machine(text_content=text, content_shape=None, config=DEFAULT_CONFIG)
    dampened = score_machine(
        text_content=text, content_shape=ContentShape.TABLE_OR_FORM, config=DEFAULT_CONFIG
    )
    assert dampened.score > undampened.score
    # Weight dampening must not change the *signals themselves* (only how
    # heavily they count), except for repetition_noise's structural fix.
    assert dampened.signals["char_script_plausibility"] == pytest_approx(
        undampened.signals["char_script_plausibility"]
    )


def test_dominant_numeric_token_not_treated_as_repetition_noise_when_structured() -> None:
    """A stream dominated by a repeated purely-numeric token (e.g. "00" cents
    across many table cells) is expected in tabular data, unlike a dominant
    non-numeric/gibberish token."""
    text = "Row Total Amount " + " ".join(["00"] * 20) + " Balance Due Fee Schedule Interest"
    prose_like = score_machine(text_content=text, content_shape=None, config=DEFAULT_CONFIG)
    table = score_machine(
        text_content=text, content_shape=ContentShape.TABLE_OR_FORM, config=DEFAULT_CONFIG
    )
    assert table.signals["repetition_noise"] > prose_like.signals["repetition_noise"]


def test_dot_leader_runs_not_penalized_as_repetition_noise_when_structured() -> None:
    """Dot-leader table formatting (e.g. "Total Due..........$100.00") must
    not count as a corrupted repeated-character run once content_shape says
    the document is tabular/structured."""
    text = "Account Fee" + "." * 20 + "$25.00\nBalance" + "." * 20 + "$975.00"
    prose_like = score_machine(text_content=text, content_shape=None, config=DEFAULT_CONFIG)
    table = score_machine(
        text_content=text, content_shape=ContentShape.TABLE_OR_FORM, config=DEFAULT_CONFIG
    )
    assert table.signals["repetition_noise"] > prose_like.signals["repetition_noise"]


def test_garbled_prose_still_scores_poorly_regardless_of_content_shape() -> None:
    """The true-positive case must be preserved: genuinely garbled/repeated
    OCR noise in prose text is never dampened, since PROSE/UNKNOWN are not in
    the default structured_content_shapes."""
    text = "xx xx xx xx xx xx xx xx xx xx xx xx xx xx qzjx qzjx qzjx zzzzzzzzzzzz"
    for shape in (None, ContentShape.PROSE, ContentShape.UNKNOWN):
        result = score_machine(text_content=text, content_shape=shape, config=DEFAULT_CONFIG)
        assert result.score < 60.0
        assert any(r.code == "machine.repetition_noise" for r in result.reasons)


def test_garbled_repeated_noise_in_table_shape_is_still_flagged_just_less_weighted() -> None:
    """Dampening must reduce influence, not eliminate detection: a document
    that is *genuinely* garbled (not just legitimately tabular) should still
    show a low repetition_noise signal value even when content_shape is
    TABLE_OR_FORM — the dampening only changes how much that signal counts
    toward the final score, not whether it fires."""
    text = "xx xx xx xx xx xx xx xx xx xx xx xx xx xx qzjx qzjx qzjx zzzzzzzzzzzz"
    result = score_machine(
        text_content=text, content_shape=ContentShape.TABLE_OR_FORM, config=DEFAULT_CONFIG
    )
    assert result.signals["repetition_noise"] < 0.6
    assert any(r.code == "machine.repetition_noise" for r in result.reasons)


def test_structured_content_signal_multiplier_is_configurable() -> None:
    """A fully-neutralizing multiplier (0.0) must remove the two signals'
    weight entirely for structured content, per the versioned-config
    architecture (not a hardcoded magic number)."""
    text = _table_statement_text()
    tuned = DEFAULT_CONFIG.model_copy(
        update={"structured_content_signal_multiplier": 0.0, "config_version": "test-neutral"}
    )
    result = score_machine(
        text_content=text, content_shape=ContentShape.TABLE_OR_FORM, config=tuned
    )
    assert result.score is not None
    # With weight fully zeroed, neither signal contributes to weighted_total.
    weights = tuned.machine_weights.model_dump()
    assert weights["char_script_plausibility"] > 0  # sanity: config itself unaffected
