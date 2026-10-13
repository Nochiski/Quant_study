"""SQLite 파일 하나의 연결·트랜잭션 수명.

연결·PRAGMA·트랜잭션·예외 감싸기를 여기 한 곳이 소유한다. 저장소는 SQL과 행 ↔ 값 타입 변환만
한다. 경로가 `None`이면 프로세스 안에서만 사는 in-memory DB이고, 두 모드가 같은 스키마와 질의를
탄다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager, suppress
from pathlib import Path
from threading import RLock
from typing import Self


class SqliteDatabase:
    """`prepare`는 연결마다가 아니라 열 때 한 번 돈다(스키마 claim·판본 대조·무결성 검사).

    `label`은 메시지에 쓰는 이름이고 `error`는 그 어댑터의 저장 오류 타입이다. 호출부가 sqlite3 예외
    타입을 알지 않게 SQLite 오류만 `error`로 바꾸고, 포트가 약속한 예외는 그대로 올린다.
    """

    def __init__(
        self,
        path: str | Path | None,
        *,
        label: str,
        error: type[Exception],
        prepare: Callable[[sqlite3.Connection], None],
        busy_timeout_seconds: float = 5.0,
    ) -> None:
        if busy_timeout_seconds <= 0:
            raise ValueError(
                f"busy_timeout_seconds must be positive — label={label} "
                f"got={busy_timeout_seconds!r}"
            )
        self._label = label
        self._error = error
        self._busy_timeout_seconds = busy_timeout_seconds
        self._busy_timeout_ms = max(1, round(busy_timeout_seconds * 1000))
        self._lock = RLock()
        self._closed = False
        self._memory_connection: sqlite3.Connection | None = None
        self._path: Path | None
        if path is None:
            self._path = None
            self._memory_connection = self._new_connection(":memory:")
            self._prepare(self._memory_connection, prepare)
        else:
            candidate = Path(path).expanduser()
            if candidate.exists() and candidate.is_dir():
                raise ValueError(f"SQLite {label} path is a directory — path={candidate}")
            candidate.parent.mkdir(parents=True, exist_ok=True)
            self._path = candidate.resolve()
            with closing(self._new_connection(str(self._path))) as connection:
                self._prepare(connection, prepare)

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

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        """트랜잭션 하나. 성공하면 commit, 예외면 rollback한다."""
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
        except sqlite3.DatabaseError as error:
            raise self._error(
                f"SQLite {self._label} operation failed — write={write} {error}"
            ) from error

    def _prepare(
        self, connection: sqlite3.Connection, prepare: Callable[[sqlite3.Connection], None]
    ) -> None:
        try:
            prepare(connection)
        except sqlite3.DatabaseError as error:
            raise self._error(
                f"could not initialise the SQLite {self._label} — path={self._path} {error}"
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
            if self._closed:
                raise self._error(f"SQLite {self._label} is closed")
            if self._memory_connection is not None:
                yield self._memory_connection
                return
            if self._path is None:  # pragma: no cover - 생성자 불변식
                raise self._error(
                    f"SQLite {self._label} has neither a path nor a memory connection"
                )
            with closing(self._new_connection(str(self._path))) as connection:
                yield connection

    def __del__(self) -> None:  # pragma: no cover - 인터프리터 정리용 방어
        with suppress(Exception):
            self.close()
