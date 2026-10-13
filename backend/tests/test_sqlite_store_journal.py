"""공용 SQLite 연결의 저널 모드 — 읽기와 쓰기가 서로 막지 않는다(#328)."""

import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.sqlite_store.facade.database import SqliteDatabase


class _StorageError(RuntimeError):
    pass


def _create(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE TABLE IF NOT EXISTS item (value INTEGER)")


def _journal_mode(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        return str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()


def test_file_database_uses_write_ahead_log_after_preparing(tmp_path: Path) -> None:
    path = tmp_path / "store.sqlite3"
    SqliteDatabase(path, label="test store", error=_StorageError, prepare=_create)
    assert _journal_mode(path) == "wal"


def test_open_read_transaction_does_not_block_a_write_commit(tmp_path: Path) -> None:
    # 전략·초안 저장소처럼 같은 파일을 두 인스턴스가 연다(인스턴스 잠금은 서로 공유하지 않는다).
    # rollback 저널에서는 한쪽의 열린 읽기가 다른 쪽 commit의 배타 잠금을 막아 busy timeout 뒤
    # `database is locked`가 났다(#328). WAL에서는 읽기와 쓰기가 서로 기다리지 않는다.
    path = tmp_path / "store.sqlite3"

    def open_store() -> SqliteDatabase:
        return SqliteDatabase(
            path, label="test store", error=_StorageError, prepare=_create, busy_timeout_seconds=0.2
        )

    reading, database = open_store(), open_store()
    written = threading.Event()
    failures: list[BaseException] = []

    def write() -> None:
        try:
            with database.transaction(write=True) as connection:
                connection.execute("INSERT INTO item VALUES (1)")
            written.set()
        except BaseException as error:  # noqa: BLE001 — 스레드 밖으로 실패를 옮긴다
            failures.append(error)

    with reading.transaction(write=False) as reader:
        assert reader.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 0
        writer = threading.Thread(target=write)
        writer.start()
        writer.join(timeout=5)
        assert not failures, failures
        assert written.is_set()
        # 읽기 트랜잭션은 시작 시점의 스냅샷을 그대로 본다.
        assert reader.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 0
    with database.transaction(write=False) as connection:
        assert connection.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 1


def test_a_refused_file_keeps_its_journal_mode(tmp_path: Path) -> None:
    # 모드는 파일에 남는다 — 준비(claim)가 거부한 남의 파일은 바꾸지 않는다.
    path = tmp_path / "foreign.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE foreign_table (x INTEGER)")

    def refuse(_: sqlite3.Connection) -> None:
        raise sqlite3.DatabaseError("foreign file")

    with pytest.raises(_StorageError):
        SqliteDatabase(path, label="test store", error=_StorageError, prepare=refuse)
    assert _journal_mode(path) == "delete"
