"""Scoring configuration and versioning.

Weights and thresholds are validated configuration, not hardcoded constants,
and every produced assessment records the scorer/configuration version that
produced it (see ``scorer_version()``). A YAML override file
(``config/ocr-quality-scoring.yaml``) may supply a tuned configuration; if
absent or partially specified, built-in defaults fill the rest — mirroring
the builtin+YAML merge pattern used by
``doc_intelligence_hub.modules.analysis.rule_registry``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

# Bumped when scoring *logic* changes (independent of tunable config values).
SCORER_CODE_VERSION = "ocr-quality-scorer-1"

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "ocr-quality-scoring.yaml"


class OverlayWeights(BaseModel):
    """Relative weights for overlay/readability signals.

    Only signals that were actually computable are renormalized and applied;
    an unavailable signal's weight is simply excluded, never given a
    favorable default value.
    """

    searchable_text: float = Field(default=20.0, ge=0.0)
    page_coverage: float = Field(default=15.0, ge=0.0)
    bounds_sanity: float = Field(default=10.0, ge=0.0)
    duplicate_overlap: float = Field(default=15.0, ge=0.0)
    alignment: float = Field(default=15.0, ge=0.0)
    reading_order: float = Field(default=15.0, ge=0.0)
    page_integrity: float = Field(default=10.0, ge=0.0)

    @model_validator(mode="after")
    def _non_zero_total(self) -> OverlayWeights:
        if sum(self.model_dump().values()) <= 0:
            raise ValueError("Overlay weights must sum to a positive value.")
        return self


class MachineWeights(BaseModel):
    """Relative weights for machine-extraction signals.

    ``prose_coherence`` (which includes the low-weight dictionary check) is
    intentionally modest relative to structural signals so that valid names,
    medical terms, acronyms, identifiers, and codes are not treated as
    automatic errors.
    """

    char_script_plausibility: float = Field(default=20.0, ge=0.0)
    token_whitespace_quality: float = Field(default=15.0, ge=0.0)
    repetition_noise: float = Field(default=15.0, ge=0.0)
    prose_coherence: float = Field(default=10.0, ge=0.0)
    structured_entities: float = Field(default=15.0, ge=0.0)
    table_structure: float = Field(default=10.0, ge=0.0)
    engine_confidence: float = Field(default=10.0, ge=0.0)
    downstream_evidence: float = Field(default=5.0, ge=0.0)

    @model_validator(mode="after")
    def _non_zero_total(self) -> MachineWeights:
        if sum(self.model_dump().values()) <= 0:
            raise ValueError("Machine weights must sum to a positive value.")
        return self


class StatusThresholds(BaseModel):
    """Score bands (0-100) used to derive :class:`AssessmentStatus`.

    ``good_min`` > ``uncertain_min`` > ``review_recommended_min`` must hold.
    Below ``review_recommended_min`` is treated as high risk. Blocking
    reasons (e.g. missing pages, no usable content at all) can force a worse
    status regardless of the numeric score.
    """

    good_min: float = Field(default=80.0, ge=0.0, le=100.0)
    uncertain_min: float = Field(default=60.0, ge=0.0, le=100.0)
    review_recommended_min: float = Field(default=35.0, ge=0.0, le=100.0)

    @model_validator(mode="after")
    def _ordered(self) -> StatusThresholds:
        if not (self.good_min > self.uncertain_min > self.review_recommended_min >= 0.0):
            raise ValueError(
                "Status thresholds must satisfy "
                "good_min > uncertain_min > review_recommended_min >= 0."
            )
        return self


class ScoringConfig(BaseModel):
    """Full tunable configuration for one scorer run.

    ``config_version`` is caller-controlled (e.g. bumped after calibration)
    and is combined with :data:`SCORER_CODE_VERSION` to form the
    ``scorer_version`` recorded on every assessment.
    """

    config_version: str = Field(default="default-1")
    overlay_weights: OverlayWeights = Field(default_factory=OverlayWeights)
    machine_weights: MachineWeights = Field(default_factory=MachineWeights)
    status_thresholds: StatusThresholds = Field(default_factory=StatusThresholds)
    short_document_char_threshold: int = Field(
        default=200,
        ge=0,
        description="Documents with fewer extracted characters are flagged 'short' in the "
        "profile for context, but are not automatically penalized for it.",
    )
    common_word_min_hits: int = Field(
        default=3,
        ge=0,
        description="Minimum prose-like tokens required before the low-weight dictionary "
        "coherence signal is considered available.",
    )
    max_downstream_penalty: float = Field(
        default=15.0,
        ge=0.0,
        le=100.0,
        description="Cap on how much cumulative downstream-extraction failure evidence can "
        "reduce the machine score.",
    )
    max_downstream_bonus: float = Field(
        default=5.0,
        ge=0.0,
        le=100.0,
        description="Cap on how much downstream-extraction success evidence can add to the "
        "machine score. Deliberately small: success does not prove the whole document correct.",
    )


DEFAULT_CONFIG = ScoringConfig()


def scorer_version(config: ScoringConfig) -> str:
    """Compose the recorded ``scorer_version`` string for an assessment."""
    return f"{SCORER_CODE_VERSION}/{config.config_version}"


def load_config(path: str | Path | None = None) -> ScoringConfig:
    """Load scoring configuration, merging an optional YAML override file.

    Falls back entirely to :data:`DEFAULT_CONFIG` if no override file exists.
    Partial YAML overrides (e.g. only ``machine_weights``) are merged over
    the defaults rather than requiring a full config to be specified.
    """
    yaml_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    if not yaml_path.exists():
        return ScoringConfig()

    try:
        with open(yaml_path) as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("Failed to load OCR scoring config from %s: %s", yaml_path, exc)
        return ScoringConfig()

    return ScoringConfig(**data)
