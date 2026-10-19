"""Shared optimistic-write helpers for Paperless metadata updates."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any

_INITIATOR_PATTERN = re.compile(r"[^a-zA-Z0-9_.:-]+")


@dataclass(frozen=True)
class WriteConflict:
    field: str
    label: str
    loaded_value: Any
    paperless_value: Any
    owl_value: Any

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalized(value: Any) -> str:
    if isinstance(value, list) and all(
        item is None or isinstance(item, (bool, int, float, str)) for item in value
    ):
        value = sorted(value, key=lambda item: (type(item).__name__, str(item)))
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def values_equal(left: Any, right: Any) -> bool:
    return _normalized(left) == _normalized(right)


def build_write_conflicts(
    *,
    loaded: dict[str, Any],
    current: dict[str, Any],
    proposed: dict[str, Any],
    labels: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return fields changed by both Paperless and OWL since the document was loaded."""
    labels = labels or {}
    conflicts = []
    for field, owl_value in proposed.items():
        loaded_value = loaded.get(field)
        paperless_value = current.get(field)
        if values_equal(paperless_value, loaded_value) or values_equal(paperless_value, owl_value):
            continue
        conflicts.append(
            WriteConflict(
                field=field,
                label=labels.get(field, field.replace("_", " ").title()),
                loaded_value=loaded_value,
                paperless_value=paperless_value,
                owl_value=owl_value,
            ).as_dict()
        )
    return conflicts


def is_idempotent_replay(current: dict[str, Any], proposed: dict[str, Any]) -> bool:
    return bool(proposed) and all(
        values_equal(current.get(field), value) for field, value in proposed.items()
    )


def sanitize_initiator(value: str | None, *, fallback: str) -> str:
    candidate = _INITIATOR_PATTERN.sub("-", (value or fallback).strip())[:64].strip("-")
    return candidate or fallback


def audit_paperless_write(
    logger: logging.Logger,
    *,
    initiator: str,
    surface: str,
    fields: list[str],
    outcome: str,
) -> None:
    """Audit write provenance without logging document IDs or field values."""
    logger.info(
        "Paperless write initiator=%s surface=%s field_count=%d fields=%s outcome=%s",
        initiator,
        surface,
        len(fields),
        ",".join(sorted(fields)),
        outcome,
    )
