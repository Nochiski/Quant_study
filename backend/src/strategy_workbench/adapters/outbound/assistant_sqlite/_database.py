"""두 어시스턴트 저장소가 함께 쓰는 SQLite 파일 하나 (설계 spec D5).

프로파일 저장소와 세션 저장소는 같은 파일을 본다. 그래서 경로가 아니라 이 객체를 주입한다.
경로를 각자 받게 하면 `:memory:` 모드에서 두 저장소가 서로 다른 DB를 열어, 세션이 참조하는
프로파일이 없는 조합이 테스트에서만 성립한다.

연결·PRAGMA·트랜잭션·예외 감싸기는 공용 `SqliteDatabase`가 소유한다. 저장소는 SQL과 행 ↔ 값
타입 변환만 한다.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from strategy_workbench.adapters.outbound.sqlite_store.facade.database import SqliteDatabase

from ._errors import AssistantStorageError
from ._schema import SCHEMA_VERSION, migrate_schema


class AssistantDatabase(SqliteDatabase):
    """어시스턴트 DB 파일 한 개. 경로가 `None`이면 프로세스 안에서만 사는 in-memory DB다."""

    SCHEMA_VERSION: ClassVar[int] = SCHEMA_VERSION

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        busy_timeout_seconds: float = 5.0,
    ) -> None:
        super().__init__(
            path,
            label="assistant database",
            error=AssistantStorageError,
            prepare=migrate_schema,
            busy_timeout_seconds=busy_timeout_seconds,
        )


def datetime_value(raw: object, *, field: str) -> datetime:
    if not isinstance(raw, str):
        raise AssistantStorageError(f"stored {field} is not text — type={type(raw).__name__}")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise AssistantStorageError(f"stored {field} is not an ISO-8601 datetime") from error
    if parsed.tzinfo is None:
        raise AssistantStorageError(f"stored {field} has no time zone")
    return parsed.astimezone(UTC)


def text_value(row: sqlite3.Row, key: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        raise AssistantStorageError(f"stored {key} is not text — type={type(value).__name__}")
    return value


def optional_text_value(row: sqlite3.Row, key: str) -> str | None:
    value = row[key]
    if value is not None and not isinstance(value, str):
        raise AssistantStorageError(
            f"stored {key} is neither text nor null — type={type(value).__name__}"
        )
    return value


def int_value(row: sqlite3.Row, key: str) -> int:
    value = row[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise AssistantStorageError(f"stored {key} is not an integer — type={type(value).__name__}")
    return value


def optional_int_value(row: sqlite3.Row, key: str) -> int | None:
    value = row[key]
    if value is None:
        return None
    return int_value(row, key)
