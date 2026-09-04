"""Datetime normalization and serialization helpers."""

from __future__ import annotations

from datetime import UTC, datetime


def normalize_utc_datetime(value: datetime) -> datetime:
    """Normalize API timestamps to the UTC-naive convention used by the database."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def serialize_utc_datetime(value: datetime | None) -> str | None:
    """Serialize a database timestamp explicitly as UTC."""
    if value is None:
        return None
    normalized = value if value.tzinfo else value.replace(tzinfo=UTC)
    return normalized.astimezone(UTC).isoformat().replace("+00:00", "Z")
