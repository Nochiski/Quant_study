"""SQLite 파일 하나의 연결·트랜잭션 수명.

연결·PRAGMA·트랜잭션·예외 감싸기를 여기 한 곳이 소유한다. 저장소는 SQL과 행 ↔ 값 타입 변환만
한다. 경로가 `None`이면 프로세스 안에서만 사는 in-memory DB이고, 두 모드가 같은 스키마와 질의를
탄다.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager, suppress
from pathlib import Path
from threading import RLock
from typing import Self

logger = logging.getLogger(__name__)


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
                self._enable_write_ahead_log(connection)

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

    def _enable_write_ahead_log(self, connection: sqlite3.Connection) -> None:
        """claim·판본 대조가 끝난 우리 파일에만 WAL을 켠다(#328).

        rollback 저널에서는 쓰기 commit이 읽기를 기다리고 읽기가 commit을 기다려, 동시 요청이
        몰리면 busy timeout을 넘겨 `database is locked`가 난다. WAL에서는 읽기와 쓰기가 서로
        막지 않는다. 모드는 파일에 남으므로 남의 파일을 바꾸지 않게 준비 뒤에만 건다.
        `synchronous`는 기본(FULL)을 유지해 내구성을 낮추지 않는다. WAL을 쓸 수 없는 파일
        시스템이면 지금처럼 rollback 저널로 둔다.
        """
        try:
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        except sqlite3.DatabaseError as error:
            raise self._error(
                f"could not configure the SQLite {self._label} journal — path={self._path} {error}"
            ) from error
        if str(mode).lower() != "wal":
            logger.warning(
                "SQLite %s stays in %s journal mode — path=%s", self._label, mode, self._path
            )

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
