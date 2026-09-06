from __future__ import annotations

import sqlite3
from datetime import UTC, datetime


def required_text(row: sqlite3.Row, key: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    return value


def optional_text(row: sqlite3.Row, key: str) -> str | None:
    value = row[key]
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{key} must be text or null")
    return value


def required_int(row: sqlite3.Row, key: str) -> int:
    value = row[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def datetime_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"datetime must be timezone-aware UTC — got={value!r}")
    return value.astimezone(UTC).isoformat(timespec="microseconds")
