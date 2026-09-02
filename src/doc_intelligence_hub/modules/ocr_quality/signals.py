"""Stage-1 pure text/metadata signal computation.

These functions compute fast, deterministic signals from Paperless
``content`` text and document metadata. They intentionally do not implement
the full overlay/machine-extraction scorer from issue #29 — the
``preliminary_score`` here is a coarse quality-risk heuristic used only to
build stratification buckets for the Stage-2 sample. Callers must not persist
or export raw ``content``; only the derived signals below should be stored.
"""

from __future__ import annotations

import re
from collections import Counter

from .models import DocumentSignals, ReasonCode

_WHITESPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[^\s]+")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]")
_TABLE_ROW_HINT_RE = re.compile(r"[|\t]")
_TABLE_COLUMN_RE = re.compile(r" {2,}\S")
_CODE_HINT_RE = re.compile(r"[{}<>;]|def |class |function |SELECT |import ")


def _has_table_shape(text: str) -> bool:
    """Coarse heuristic: pipe/tab-delimited rows, or 2+ lines with 3+ columns."""
    if _TABLE_ROW_HINT_RE.search(text):
        return True
    multi_column_lines = sum(
        1 for line in text.splitlines() if len(_TABLE_COLUMN_RE.findall(line)) >= 2
    )
    return multi_column_lines >= 2


def compute_text_signals(content: str | None) -> DocumentSignals:
    """Compute Stage-1 signals for one document's extracted text.

    Returns a ``DocumentSignals`` with ``reason_codes`` including
    ``EMPTY_CONTENT`` when there is no usable text.
    """
    text = content or ""
    content_length = len(text)

    if content_length == 0:
        return DocumentSignals(
            content_length=0,
            word_count=0,
            non_ascii_ratio=0.0,
            whitespace_ratio=0.0,
            repetition_ratio=0.0,
            avg_token_length=0.0,
            distinct_token_ratio=0.0,
            table_shape_hint=False,
            code_shape_hint=False,
            preliminary_score=0,
            reason_codes=(ReasonCode.EMPTY_CONTENT,),
        )

    tokens = _TOKEN_RE.findall(text)
    word_count = len(tokens)

    non_ascii_count = sum(1 for ch in text if ord(ch) > 127)
    non_ascii_ratio = non_ascii_count / content_length

    whitespace_count = len(_WHITESPACE_RE.findall(text))
    whitespace_ratio = whitespace_count / content_length

    if tokens:
        token_counts = Counter(tokens)
        most_common_count = token_counts.most_common(1)[0][1]
        repetition_ratio = most_common_count / word_count
        distinct_token_ratio = len(token_counts) / word_count
        avg_token_length = sum(len(t) for t in tokens) / word_count
    else:
        repetition_ratio = 0.0
        distinct_token_ratio = 0.0
        avg_token_length = 0.0

    table_shape_hint = _has_table_shape(text)
    code_shape_hint = bool(_CODE_HINT_RE.search(text))

    preliminary_score = _preliminary_score(
        word_count=word_count,
        non_ascii_ratio=non_ascii_ratio,
        repetition_ratio=repetition_ratio,
        distinct_token_ratio=distinct_token_ratio,
        avg_token_length=avg_token_length,
        content_length=content_length,
    )

    return DocumentSignals(
        content_length=content_length,
        word_count=word_count,
        non_ascii_ratio=round(non_ascii_ratio, 4),
        whitespace_ratio=round(whitespace_ratio, 4),
        repetition_ratio=round(repetition_ratio, 4),
        avg_token_length=round(avg_token_length, 2),
        distinct_token_ratio=round(distinct_token_ratio, 4),
        table_shape_hint=table_shape_hint,
        code_shape_hint=code_shape_hint,
        preliminary_score=preliminary_score,
        reason_codes=(ReasonCode.OK,),
    )


def _preliminary_score(
    *,
    word_count: int,
    non_ascii_ratio: float,
    repetition_ratio: float,
    distinct_token_ratio: float,
    avg_token_length: float,
    content_length: int,
) -> int:
    """Coarse 0-100 quality-risk estimate. Higher is healthier-looking.

    This is a stratification aid only — not a claim of OCR accuracy, and not
    a substitute for the #29 overlay/machine-extraction scorer.
    """
    score = 100

    # Very short documents are hard to assess and often extraction failures.
    if word_count == 0:
        return 0
    if word_count < 5:
        score -= 40
    elif word_count < 20:
        score -= 15

    # Heavy non-ASCII noise can indicate garbled OCR (though also legitimate
    # non-English text — this is a coarse heuristic, not a correctness claim).
    if non_ascii_ratio > 0.5:
        score -= 25
    elif non_ascii_ratio > 0.25:
        score -= 10

    # High repetition of a single token (e.g. "??? ??? ???") suggests garbage.
    if repetition_ratio > 0.8:
        score -= 40
    elif repetition_ratio > 0.5:
        score -= 25
    elif repetition_ratio > 0.3:
        score -= 10

    # Very low token diversity across a longer document is also suspicious.
    if word_count >= 20 and distinct_token_ratio < 0.2:
        score -= 15

    # Extreme average token length (too long = numeric/garbage runs, too
    # short = fragmented noise) is a mild signal.
    if avg_token_length > 20 or (0 < avg_token_length < 1.5):
        score -= 10

    # Extremely dense content with almost no whitespace is often noise.
    if content_length > 0 and word_count > 0 and (content_length / word_count) > 60:
        score -= 10

    return max(0, min(100, score))
