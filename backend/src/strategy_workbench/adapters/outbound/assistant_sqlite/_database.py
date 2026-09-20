"""두 어시스턴트 저장소가 함께 쓰는 SQLite 파일 하나 (설계 spec D5).

프로파일 저장소와 세션 저장소는 같은 파일을 본다. 그래서 경로가 아니라 이 객체를 주입한다.
경로를 각자 받게 하면 `:memory:` 모드에서 두 저장소가 서로 다른 DB를 열어, 세션이 참조하는
프로파일이 없는 조합이 테스트에서만 성립한다.

연결·PRAGMA·트랜잭션·예외 감싸기를 여기 한 곳이 소유한다. 저장소는 SQL과 행 ↔ 값 타입 변환만
한다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import ClassVar

from ._errors import AssistantStorageError
from ._schema import SCHEMA_VERSION, migrate_schema


class AssistantDatabase:
    """어시스턴트 DB 파일 한 개. 경로가 `None`이면 프로세스 안에서만 사는 in-memory DB다."""

    SCHEMA_VERSION: ClassVar[int] = SCHEMA_VERSION

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        busy_timeout_seconds: float = 5.0,
    ) -> None:
        if busy_timeout_seconds <= 0:
            raise ValueError(
                f"busy_timeout_seconds must be positive — got={busy_timeout_seconds!r}"
            )
        self._busy_timeout_seconds = busy_timeout_seconds
        self._busy_timeout_ms = max(1, round(busy_timeout_seconds * 1000))
        self._lock = RLock()
        self._closed = False
        self._memory_connection: sqlite3.Connection | None = None
        self._path: Path | None
        if path is None:
            self._path = None
            self._memory_connection = self._new_connection(":memory:")
            self._prepare(self._memory_connection)
        else:
            candidate = Path(path).expanduser()
            if candidate.exists() and candidate.is_dir():
                raise ValueError(f"assistant database path is a directory — path={candidate}")
            candidate.parent.mkdir(parents=True, exist_ok=True)
            self._path = candidate.resolve()
            with closing(self._new_connection(str(self._path))) as connection:
                self._prepare(connection)

    @property
    def database_path(self) -> Path | None:
        return self._path

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._memory_connection is not None:
                self._memory_connection.close()
                self._memory_connection = None

    def __enter__(self) -> AssistantDatabase:
        self._ensure_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        """트랜잭션 하나. 성공하면 commit, 예외면 rollback한다.

        포트가 약속한 예외(`LookupError` 계열)와 이미 감싼 저장 오류는 그대로 올리고, 나머지
        SQLite 오류만 `AssistantStorageError`로 바꾼다. 호출부가 sqlite3 예외 타입을 알지 않게
        하려는 것이다.
        """
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                try:
                    yield connection
                except Exception:
                    if connection.in_transaction:
                        connection.rollback()
                    raise
                else:
                    connection.commit()
        except (LookupError, AssistantStorageError):
            raise
        except sqlite3.DatabaseError as error:
            raise AssistantStorageError(
                f"SQLite assistant storage operation failed — write={write} {error}"
            ) from error

    def _prepare(self, connection: sqlite3.Connection) -> None:
        try:
            migrate_schema(connection)
        except AssistantStorageError:
            raise
        except sqlite3.DatabaseError as error:
            raise AssistantStorageError(
                f"could not initialise the SQLite assistant database — path={self._path} {error}"
            ) from error

    def _new_connection(self, database: str) -> sqlite3.Connection:
        connection = sqlite3.connect(
            database,
            timeout=self._busy_timeout_seconds,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._ensure_open()
            if self._memory_connection is not None:
                yield self._memory_connection
                return
            if self._path is None:  # pragma: no cover - 생성자 불변식
                raise AssistantStorageError(
                    "assistant database has neither a path nor a memory connection"
                )
            with closing(self._new_connection(str(self._path))) as connection:
                yield connection

    def _ensure_open(self) -> None:
        if self._closed:
            raise AssistantStorageError("SQLite assistant database is closed")

    def __del__(self) -> None:  # pragma: no cover - 인터프리터 정리용 방어
        with suppress(Exception):
            self.close()


def datetime_text(value: datetime, *, field: str) -> str:
    """인지형(aware) datetime을 UTC 저장 문자열로 바꾼다.

    거부하는 것은 naive datetime뿐이다. 그걸 그대로 적으면 재기동 뒤 읽을 때 로컬 시간대가 섞여
    이벤트 이력의 순서가 말이 되지 않는 조합이 생긴다. 반대로 `Asia/Seoul`처럼 offset이 0이 아닌
    aware 값은 손실 없이 UTC로 옮길 수 있으므로 받는다 — offset 0만 받으면 호출자가 저장 형식에
    맞춰 미리 변환해야 하고, 그 변환을 잊은 곳이 조용히 터진다.

    (`strategy_sqlite/_values.py`와 같은 규칙이지만, 어댑터 노드끼리는 서로 import하지 않으므로
    각자 갖는다.)
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware datetime — got={value!r}")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


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
