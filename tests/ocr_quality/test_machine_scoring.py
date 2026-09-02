"""Tests for machine-extraction scoring."""

from __future__ import annotations

from doc_intelligence_hub.modules.ocr_quality.config import DEFAULT_CONFIG
from doc_intelligence_hub.modules.ocr_quality.machine_scoring import score_machine
from doc_intelligence_hub.modules.ocr_quality.models import DownstreamOutcome


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


def pytest_approx(value: float, tol: float = 1e-6):
    """Small local helper to avoid importing pytest.approx repeatedly."""
    import pytest

    return pytest.approx(value, abs=tol)
