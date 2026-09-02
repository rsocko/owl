"""A small built-in common-English-word set for a *low-weight* prose signal.

This is deliberately tiny and deliberately not authoritative. Per the OCR
quality design contract, dictionary frequency must never be used to flag
valid medical terminology, names, acronyms, account identifiers, or document
codes as OCR garbage — it is only ever one modest signal contributing to
``prose_coherence`` in ``machine_scoring.py``, alongside structural checks
that are the primary signal.
"""

from __future__ import annotations

COMMON_WORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "and", "or", "but", "if", "of", "to", "in", "on", "at",
        "for", "with", "by", "from", "as", "is", "are", "was", "were", "be",
        "been", "being", "this", "that", "these", "those", "it", "its", "you",
        "your", "we", "our", "they", "their", "he", "she", "his", "her", "not",
        "no", "yes", "will", "would", "can", "could", "should", "may", "might",
        "must", "shall", "have", "has", "had", "do", "does", "did", "please",
        "thank", "date", "amount", "total", "balance", "account", "number",
        "name", "address", "payment", "due", "service", "services",
        "provider", "patient", "insurance", "claim", "statement", "invoice",
        "page", "note", "information", "contact", "questions",
        "call", "visit", "online", "here", "below", "above", "see", "attached",
        "enclosed", "received", "sent", "regarding", "reference", "summary",
        "period", "month", "year", "day", "time", "code", "description",
        "charges", "credit", "debit", "new", "current", "previous", "next",
        "all", "any", "each", "other", "more", "less", "than", "also", "only",
    }
)
"""~120 high-frequency English function/domain words. Intentionally small."""


def common_word_hit_ratio(tokens: list[str]) -> float | None:
    """Fraction of alphabetic tokens that match the common-word set.

    Returns ``None`` if there are no alphabetic tokens to evaluate (signal is
    unavailable rather than defaulted).
    """
    alpha_tokens = [t.lower() for t in tokens if t.isalpha()]
    if not alpha_tokens:
        return None
    hits = sum(1 for t in alpha_tokens if t in COMMON_WORDS)
    return hits / len(alpha_tokens)
