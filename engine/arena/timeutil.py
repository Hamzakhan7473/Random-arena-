from __future__ import annotations

from datetime import datetime, timezone


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def floor_hour(value: datetime) -> datetime:
    value = ensure_utc(value)
    return value.replace(minute=0, second=0, microsecond=0)


def as_utc_hour(value: datetime) -> datetime:
    value = ensure_utc(value)
    if (value.minute, value.second, value.microsecond) != (0, 0, 0):
        raise ValueError("timestamp must be an exact UTC hour")
    return value


def format_utc(value: datetime) -> str:
    return as_utc_hour(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return ensure_utc(datetime.fromisoformat(text))
