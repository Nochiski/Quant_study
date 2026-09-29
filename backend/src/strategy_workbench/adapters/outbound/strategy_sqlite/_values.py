from __future__ import annotations

import sqlite3


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
