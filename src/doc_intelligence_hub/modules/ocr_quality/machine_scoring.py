"""Machine-extraction scoring.

Assesses whether extracted text is coherent and useful for search and
downstream tasks. Dictionary/prose-coherence checks are deliberately
low-weight and never treat valid names, medical terminology, acronyms,
identifiers, or document codes as automatic errors — structural signals
(character plausibility, token/whitespace quality, repetition, preserved
structured entities) carry the bulk of the weight.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from doc_intelligence_hub.modules.ocr_quality.common_words import common_word_hit_ratio
from doc_intelligence_hub.modules.ocr_quality.scoring_config import DEFAULT_CONFIG, ScoringConfig
from doc_intelligence_hub.modules.ocr_quality.scoring_models import (
    ContentShape,
    DownstreamOutcome,
    Reason,
    ScoreComponent,
    Severity,
)

# Structured-entity density (distinct hits / token count) at/above this
# fraction earns full "bonus" credit toward the structured_entities signal.
_ENTITY_DENSITY_SATURATION = 0.02
_TABLE_LINE_RATIO_SATURATION = 0.3
_MIN_PLAUSIBILITY_FOR_STRUCTURE_SIGNALS = 0.5

_REPLACEMENT_CHAR = "\ufffd"

# Table/form "dot leader" or rule-line separator characters (e.g.
# "Account Fee..........$25.00" or a "----" section rule). Long runs of these
# specific characters are expected formatting artifacts in tabular/structured
# layouts, not evidence of OCR corruption the way a run of e.g. random letters
# would be — so they are exempted from the repetition-noise run penalty when
# the document's content shape indicates structured content.
_LEADER_CHARS = frozenset(".-_=*~\u00b7\u2022")

_DATE_RE = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b",
    re.IGNORECASE,
)
_CURRENCY_RE = re.compile(r"[$€£]\s?\d[\d,]*\.\d{2}\b|\b\d[\d,]*\.\d{2}\s?(?:USD|EUR|GBP)\b")
_CODE_RE = re.compile(r"\b(?:[A-Z]\d{2}\.?[0-9A-Z]{0,4}|\d{5})\b")
_IDENTIFIER_RE = re.compile(
    r"\b(?=[A-Za-z0-9-]{6,20}\b)(?=[^ ]*\d)(?=[^ ]*[A-Za-z])[A-Za-z0-9-]+\b"
)

_LABEL_VALUE_RE = re.compile(r"^[\w \-/()#]{1,40}:\s*\S")
_MULTI_SPACE_COLUMNS_RE = re.compile(r"(\t| {2,})\S+(\t| {2,})\S+")

_SIGNAL_NAMES = (
    "char_script_plausibility",
    "token_whitespace_quality",
    "repetition_noise",
    "prose_coherence",
    "structured_entities",
    "table_structure",
    "engine_confidence",
    "downstream_evidence",
)


def score_machine(
    *,
    text_content: str | None,
    confidence_data: list[float] | None = None,
    downstream_outcomes: list[DownstreamOutcome] | None = None,
    content_shape: ContentShape | None = None,
    config: ScoringConfig | None = None,
) -> ScoreComponent:
    """Score machine-extraction quality/utility of extracted text.

    ``text_content`` semantics:

    - ``None`` — no text was available to look at at all; all text-derived
      signals are unavailable.
    - ``""`` (empty string) — text was looked for and there is none; this is
      a strong, computable negative signal (not "unavailable").

    ``content_shape`` is the document's :class:`~...scoring_models.ContentShape`
    from :func:`~...profiling.build_document_profile`, when available. It is
    used to dampen ``char_script_plausibility`` and ``repetition_noise`` for
    legitimately table/form-like (or code-heavy/mixed-classified structured)
    content, where those two signals are prone to false positives — see
    ``ScoringConfig.structured_content_shapes``/``structured_content_signal_multiplier``.
    """
    cfg = config or DEFAULT_CONFIG
    reasons: list[Reason] = []
    unavailable: list[str] = []
    signals: dict[str, float | None] = dict.fromkeys(_SIGNAL_NAMES)
    is_structured_shape = (
        content_shape is not None and content_shape in cfg.structured_content_shapes
    )

    if text_content is None:
        unavailable.extend(_SIGNAL_NAMES[:6])
        reasons.append(
            Reason(
                code="machine.no_input",
                message="No extracted text was provided; machine quality cannot be assessed.",
                severity=Severity.INFO,
                component="machine",
            )
        )
    elif text_content.strip() == "":
        for name in _SIGNAL_NAMES[:6]:
            signals[name] = 0.0
        reasons.append(
            Reason(
                code="machine.empty_text",
                message="No extractable text was found in the document.",
                severity=Severity.BLOCKING,
                component="machine",
            )
        )
    else:
        tokens = text_content.split()
        lines = [line.strip() for line in text_content.splitlines() if line.strip()]
        char_plausibility = _char_script_plausibility(text_content)
        signals["char_script_plausibility"] = char_plausibility
        signals["token_whitespace_quality"] = _token_whitespace_quality(tokens)
        signals["repetition_noise"] = _repetition_noise(
            text_content, tokens, dampen_structural_noise=is_structured_shape
        )
        signals["prose_coherence"] = _prose_coherence(tokens, cfg)

        # Structured-entity/table detection is meaningless on text that is
        # mostly implausible characters to begin with — leave it unavailable
        # rather than letting a neutral "no entities found" baseline prop up
        # a score that should be driven by the character-plausibility signal.
        if char_plausibility >= _MIN_PLAUSIBILITY_FOR_STRUCTURE_SIGNALS:
            signals["structured_entities"] = _structured_entities(text_content, tokens)
            signals["table_structure"] = _table_structure(lines)

    if confidence_data:
        signals["engine_confidence"] = sum(confidence_data) / len(confidence_data)
    else:
        unavailable.append("engine_confidence")

    if downstream_outcomes:
        signals["downstream_evidence"] = _downstream_evidence(downstream_outcomes, cfg)
    else:
        unavailable.append("downstream_evidence")

    for name in _SIGNAL_NAMES:
        if signals[name] is None and name not in unavailable:
            unavailable.append(name)

    weights = cfg.machine_weights.model_dump()
    if is_structured_shape:
        multiplier = cfg.structured_content_signal_multiplier
        weights["char_script_plausibility"] *= multiplier
        weights["repetition_noise"] *= multiplier

    weighted_total = 0.0
    weight_sum = 0.0
    for name in _SIGNAL_NAMES:
        value = signals[name]
        if value is None:
            continue
        weighted_total += value * 100.0 * weights[name]
        weight_sum += weights[name]

    score = (weighted_total / weight_sum) if weight_sum > 0 else None

    _append_signal_reasons(signals, reasons)
    if unavailable:
        reasons.append(
            Reason(
                code="machine.partial_signals",
                message=f"Unavailable machine signals: {', '.join(unavailable)}.",
                severity=Severity.INFO,
                component="machine",
            )
        )

    return ScoreComponent(
        score=round(score, 2) if score is not None else None,
        signals=signals,
        reasons=reasons,
        unavailable_signals=unavailable,
    )


def _char_script_plausibility(text: str) -> float:
    total = len(text)
    if total == 0:
        return 0.0
    bad = 0
    for ch in text:
        if ch == _REPLACEMENT_CHAR:
            bad += 1
            continue
        if ch in "\n\r\t":
            continue
        category = unicodedata.category(ch)
        # Cc = control, Cs = surrogate, Co = private use, Cn = unassigned.
        if category in ("Cc", "Cs", "Co", "Cn"):
            bad += 1
    return max(0.0, 1.0 - (bad / total))


def _token_whitespace_quality(tokens: list[str]) -> float | None:
    if not tokens:
        return None
    alpha_tokens = [t for t in tokens if any(c.isalpha() for c in t)]
    if not alpha_tokens:
        # No word-like tokens at all (e.g. control-character noise) — this
        # signal has nothing meaningful to assess, not a clean pass.
        return None
    single_char = sum(1 for t in alpha_tokens if len(t) == 1 and t.lower() not in ("a", "i"))
    overlong = sum(1 for t in alpha_tokens if len(t) > 25)
    penalty = (single_char + overlong) / len(alpha_tokens)
    return max(0.0, 1.0 - penalty)


def _repetition_noise(
    text: str, tokens: list[str], *, dampen_structural_noise: bool = False
) -> float | None:
    if not tokens:
        return None
    # Long runs of a single repeated character (e.g. "-----------", "xxxxxx").
    run_penalty = 0.0
    for match in re.finditer(r"(.)\1{7,}", text):
        if dampen_structural_noise and match.group(1) in _LEADER_CHARS:
            # Dot-leader/rule-line table formatting, not corruption.
            continue
        run_penalty += len(match.group(0))
    run_penalty = min(run_penalty / max(len(text), 1), 1.0)

    alnum_tokens = [t.lower() for t in tokens if t.isalnum()]
    token_penalty = 0.0
    if len(tokens) >= 10 and alnum_tokens:
        counts = Counter(alnum_tokens)
        # A single token dominating the stream (e.g. "xx xx xx ...").
        if dampen_structural_noise:
            # Purely-numeric tokens (row counts, cents, account/routing
            # digits) legitimately dominate tabular data — that is
            # structurally different from a dominant non-numeric/gibberish
            # token stream, so they are not eligible to drive the dominance
            # penalty. Vocabulary diversity below is left unaffected: a
            # numeric-heavy table is still expected to have moderate
            # diversity, so that check still applies as-is.
            dominance_candidates = [c for tok, c in counts.items() if not tok.isdigit()]
            dominant_count = max(dominance_candidates, default=0)
        else:
            dominant_count = counts.most_common(1)[0][1]
        dominance = dominant_count / len(tokens)
        dominance_penalty = min(1.0, max(0.0, dominance - 0.15) / 0.35)
        # Low overall vocabulary diversity (many different repeated tokens,
        # not just one) is an equally strong noise indicator.
        unique_ratio = len(counts) / len(tokens)
        diversity_penalty = min(1.0, max(0.0, 0.5 - unique_ratio) / 0.35)
        token_penalty = max(dominance_penalty, diversity_penalty)

    return max(0.0, 1.0 - max(run_penalty, token_penalty))


def _prose_coherence(tokens: list[str], config: ScoringConfig) -> float | None:
    alpha_tokens = [t.strip(".,;:!?\"'()") for t in tokens]
    alpha_tokens = [t for t in alpha_tokens if t.isalpha()]
    if len(alpha_tokens) < config.common_word_min_hits:
        return None
    ratio = common_word_hit_ratio(alpha_tokens)
    return ratio


def _structured_entities(text: str, tokens: list[str]) -> float | None:
    if not tokens:
        return None
    hits = 0
    hits += len(_DATE_RE.findall(text))
    hits += len(_CURRENCY_RE.findall(text))
    hits += len(_CODE_RE.findall(text))
    hits += len(_IDENTIFIER_RE.findall(text))
    density = hits / len(tokens)
    bonus = min(density / _ENTITY_DENSITY_SATURATION, 1.0)
    # Neutral baseline of 0.5: absence of structured entities is expected
    # and unremarkable in plain prose documents, so it must not be scored
    # the same as corrupted/garbled text.
    return min(1.0, 0.5 + 0.5 * bonus)


def _table_structure(lines: list[str]) -> float | None:
    if not lines:
        return None
    hits = sum(
        1 for line in lines if _LABEL_VALUE_RE.match(line) or _MULTI_SPACE_COLUMNS_RE.search(line)
    )
    ratio = hits / len(lines)
    bonus = min(ratio / _TABLE_LINE_RATIO_SATURATION, 1.0)
    return min(1.0, 0.5 + 0.5 * bonus)


def _downstream_evidence(outcomes: list[DownstreamOutcome], config: ScoringConfig) -> float:
    total = len(outcomes)
    failures = sum(1 for o in outcomes if not o.success)
    successes = total - failures
    failure_ratio = failures / total
    success_ratio = successes / total
    penalty = min(failure_ratio * 100.0, config.max_downstream_penalty) / 100.0
    bonus = min(success_ratio * 100.0, config.max_downstream_bonus) / 100.0
    return max(0.0, min(1.0, 0.5 - penalty + bonus))


_REASON_MESSAGES: dict[str, tuple[str, Severity]] = {
    "char_script_plausibility": (
        "A significant portion of characters are unassigned, control, or replacement "
        "characters, suggesting encoding or OCR corruption.",
        Severity.WARNING,
    ),
    "token_whitespace_quality": (
        "Many tokens look like broken words (single characters or missing spaces).",
        Severity.WARNING,
    ),
    "repetition_noise": (
        "Repeated character runs or dominant repeated tokens suggest OCR noise.",
        Severity.WARNING,
    ),
    "engine_confidence": (
        "Reported OCR engine confidence is low.",
        Severity.WARNING,
    ),
    "downstream_evidence": (
        "Downstream extraction outcomes suggest this document's text is unreliable.",
        Severity.WARNING,
    ),
}
_LOW_SIGNAL_THRESHOLD = 0.6


def _append_signal_reasons(signals: dict[str, float | None], reasons: list[Reason]) -> None:
    for name, value in signals.items():
        if value is None or value >= _LOW_SIGNAL_THRESHOLD or name not in _REASON_MESSAGES:
            continue
        message, severity = _REASON_MESSAGES[name]
        reasons.append(
            Reason(
                code=f"machine.{name}",
                message=message,
                severity=severity,
                component="machine",
                value=round(value, 3),
            )
        )
