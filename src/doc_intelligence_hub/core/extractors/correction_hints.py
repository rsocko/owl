"""Pure text helpers for correspondent-keyed correction hints (issue #171).

These functions have no Paperless or database dependency, so they stay easy to unit
test in isolation. Callers (``modules/action_queue/fallback_analyzer.py``,
``core/extractors/account_numbers.py``, and the metadata correction API) are
responsible for resolving correspondents and reading/writing stored hints.
"""

from __future__ import annotations

import re
from typing import TypeVar

_DEFAULT_ANCHOR_WINDOW_CHARS = 60
_DEFAULT_ANCHOR_MAX_WORDS = 6
_DEFAULT_MAX_ANCHOR_DISTANCE = 250
_LINE_BREAK_PATTERN = re.compile(r"[\r\n]+")

T = TypeVar("T")


def derive_label_anchor(
    source_text: str | None,
    value: str | None,
    *,
    window: int = _DEFAULT_ANCHOR_WINDOW_CHARS,
    max_words: int = _DEFAULT_ANCHOR_MAX_WORDS,
) -> str | None:
    """Return the short label text immediately preceding ``value`` in ``source_text``.

    e.g. for ``source_text="...\\nTotal Due: $142.50\\n..."`` and ``value="$142.50"``,
    this returns ``"Total Due:"``. Only the text on the same line as ``value`` (within
    the last ``window`` characters) is considered, so unrelated preceding lines (like a
    document header a few lines up) don't leak into the anchor. Returns ``None`` when
    ``value`` isn't found in ``source_text`` (nothing to anchor to) or either input is
    empty.
    """
    if not source_text or not value:
        return None
    idx = source_text.find(value)
    if idx == -1:
        return None
    start = max(0, idx - window)
    preceding = source_text[start:idx]

    lines = [line for line in _LINE_BREAK_PATTERN.split(preceding) if line.strip()]
    last_line = lines[-1] if lines else ""

    anchor = " ".join(last_line.split())  # collapse internal whitespace
    anchor = anchor.lstrip(" :\t-")  # drop stray leading punctuation/whitespace only
    if not anchor:
        return None
    words = anchor.split(" ")
    anchor = " ".join(words[-max_words:])
    return anchor or None


def pick_nearest_to_anchor(
    text: str,
    candidates: list[tuple[int, T]],
    anchor: str | None,
    *,
    max_distance: int = _DEFAULT_MAX_ANCHOR_DISTANCE,
) -> T | None:
    """Return whichever candidate sits closest to ``anchor``'s occurrence in ``text``.

    ``candidates`` is a list of ``(position, candidate)`` pairs — e.g. a regex match's
    start offset paired with its parsed value. Returns ``None`` (the caller should fall
    back to its naive heuristic) when:
      - there's no anchor or no candidates,
      - the anchor text isn't found anywhere in ``text``, or
      - the nearest candidate is farther than ``max_distance`` characters away, which
        likely means it isn't actually related to the anchor.
    """
    if not candidates or not anchor:
        return None
    anchor = anchor.strip()
    if not anchor:
        return None

    idx = text.find(anchor)
    if idx == -1:
        idx = text.lower().find(anchor.lower())
    if idx == -1:
        return None
    anchor_end = idx + len(anchor)

    best_candidate: T | None = None
    best_distance: int | None = None
    for position, candidate in candidates:
        distance = abs(position - anchor_end)
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_candidate = candidate

    if best_candidate is not None and best_distance is not None and best_distance <= max_distance:
        return best_candidate
    return None
