"""Tests for ScoringConfig validation and version wiring."""

from __future__ import annotations

import pytest

from doc_intelligence_hub.modules.ocr_quality.config import (
    DEFAULT_CONFIG,
    MachineWeights,
    OverlayWeights,
    ScoringConfig,
    StatusThresholds,
    load_config,
    scorer_version,
)


def test_default_config_is_valid() -> None:
    assert DEFAULT_CONFIG.config_version == "default-1"
    assert sum(DEFAULT_CONFIG.overlay_weights.model_dump().values()) > 0
    assert sum(DEFAULT_CONFIG.machine_weights.model_dump().values()) > 0


def test_overlay_weights_must_sum_positive() -> None:
    with pytest.raises(ValueError):
        OverlayWeights(
            searchable_text=0,
            page_coverage=0,
            bounds_sanity=0,
            duplicate_overlap=0,
            alignment=0,
            reading_order=0,
            page_integrity=0,
        )


def test_machine_weights_must_sum_positive() -> None:
    with pytest.raises(ValueError):
        MachineWeights(
            char_script_plausibility=0,
            token_whitespace_quality=0,
            repetition_noise=0,
            prose_coherence=0,
            structured_entities=0,
            table_structure=0,
            engine_confidence=0,
            downstream_evidence=0,
        )


def test_negative_weight_rejected() -> None:
    with pytest.raises(ValueError):
        OverlayWeights(searchable_text=-1)


@pytest.mark.parametrize(
    "good_min,uncertain_min,review_recommended_min",
    [
        (50.0, 60.0, 35.0),  # good_min must be > uncertain_min
        (80.0, 35.0, 60.0),  # uncertain_min must be > review_recommended_min
        (80.0, 60.0, -1.0),  # must be >= 0
    ],
)
def test_status_thresholds_must_be_ordered(
    good_min: float, uncertain_min: float, review_recommended_min: float
) -> None:
    with pytest.raises(ValueError):
        StatusThresholds(
            good_min=good_min,
            uncertain_min=uncertain_min,
            review_recommended_min=review_recommended_min,
        )


def test_scorer_version_combines_code_and_config_version() -> None:
    config_a = ScoringConfig(config_version="a")
    config_b = ScoringConfig(config_version="b")
    assert scorer_version(config_a) != scorer_version(config_b)
    assert scorer_version(config_a).endswith("/a")
    assert scorer_version(config_a).split("/")[0] == scorer_version(config_b).split("/")[0]


def test_load_config_falls_back_to_defaults_when_no_file(tmp_path) -> None:
    missing = tmp_path / "does-not-exist.yaml"
    config = load_config(missing)
    assert config.config_version == DEFAULT_CONFIG.config_version


def test_load_config_merges_partial_override(tmp_path) -> None:
    override_path = tmp_path / "override.yaml"
    override_path.write_text(
        "config_version: 'tuned-1'\nmachine_weights:\n  engine_confidence: 50.0\n"
    )
    config = load_config(override_path)
    assert config.config_version == "tuned-1"
    assert config.machine_weights.engine_confidence == 50.0
    # Unspecified fields still fall back to defaults.
    assert config.machine_weights.char_script_plausibility == (
        DEFAULT_CONFIG.machine_weights.char_script_plausibility
    )
    assert config.overlay_weights == DEFAULT_CONFIG.overlay_weights
