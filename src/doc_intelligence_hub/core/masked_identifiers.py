"""Deterministic validation helpers for masked identifiers."""

from __future__ import annotations


def masked_identifier_suffix(value: str) -> str | None:
    """Return the 2-8 character ASCII suffix when the prefix is fully masked."""
    for suffix_length in range(2, 9):
        if len(value) <= suffix_length:
            continue
        prefix = value[:-suffix_length]
        suffix = value[-suffix_length:]
        if all(char in "*Xx.-" or char.isspace() for char in prefix) and all(
            char.isascii() and char.isalnum() for char in suffix
        ):
            return suffix
    return None
