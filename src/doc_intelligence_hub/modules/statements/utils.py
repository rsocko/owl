from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

MONTH_PATTERN = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)\b",
    re.IGNORECASE,
)


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "provider"


def normalize_title(title: str) -> str:
    text = title.lower()
    text = re.sub(r"\d{4}[-/]\d{2}([-/]\d{2})?", " ", text)
    text = re.sub(r"\bq[1-4]\b", " ", text)
    text = MONTH_PATTERN.sub(" ", text)
    text = re.sub(r"\b\d+\b", " ", text)
    text = re.sub(r"[^a-z]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_last_day_of_month(value: date) -> bool:
    return value.day == calendar.monthrange(value.year, value.month)[1]


def is_last_business_day(value: date) -> bool:
    if value.weekday() >= 5:
        return False
    next_day = value + timedelta(days=1)
    while next_day.month == value.month:
        if next_day.weekday() < 5:
            return False
        next_day += timedelta(days=1)
    return True


def add_months(value: date, months: int) -> date:
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(value.day, last_day)
    return date(year, month, day)


def month_end(value: date) -> date:
    return date(value.year, value.month, calendar.monthrange(value.year, value.month)[1])


def last_business_day(value: date) -> date:
    cursor = month_end(value)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor
