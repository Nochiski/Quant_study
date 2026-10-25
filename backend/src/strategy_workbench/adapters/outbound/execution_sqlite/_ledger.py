"""전용 SQLite 의도 원장. 주문 전송 권한은 커밋된 UNKNOWN 전이 한 번뿐이다."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from threading import Lock

from strategy_workbench.adapters.outbound.sqlite_store.facade.database import SqliteDatabase
from strategy_workbench.adapters.outbound.sqlite_store.facade.schema import (
    SchemaContract,
    ensure_schema,
)
from strategy_workbench.application.offline_execution.facade.execution import LedgerError
from strategy_workbench.domain.order_execution.facade.orders import (
    Account,
    EventDisposition,
    Intent,
    OrderFact,
    OrderState,
    classify_event,
)

_OBJECTS = (
    (
        "table",
        "execution_meta",
        "CREATE TABLE execution_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID",
    ),
    (
        "table",
        "order_intents",
        "CREATE TABLE order_intents (intent_id TEXT PRIMARY KEY, fact TEXT NOT NULL) WITHOUT ROWID",
    ),
    (
        "table",
        "order_events",
        "CREATE TABLE order_events (intent_id TEXT NOT NULL, revision INTEGER NOT NULL, "
        "fact TEXT NOT NULL, PRIMARY KEY (intent_id, revision)) WITHOUT ROWID",
    ),
)


def _prepare(connection: sqlite3.Connection) -> None:
    ensure_schema(
        connection,
        SchemaContract(
            label="offline_execution",
            application_id=0x4D484558,
            version=1,
            objects=_OBJECTS,
            error=LedgerError,
        ),
    )


def _decode(value: str) -> OrderFact:
    try:
        return _decode_fact(value)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise LedgerError(
            f"invalid stored order fact — expected=valid cumulative order: {error}"
        ) from error


def _decode_fact(value: str) -> OrderFact:
    data = json.loads(value)
    return OrderFact(
        intent=Intent(**data["intent"]),
        state=OrderState(data["state"]),
        revision=data["revision"],
        filled_quantity=data["filled_quantity"],
        filled_value_krw=data["filled_value_krw"],
        fees_krw=data["fees_krw"],
    )


class ExecutionSqliteLedger:
    """명시적 경로만 받는다. 프로세스 간 executor 잠금은 종료 시 OS가 해제한다."""

    def __init__(self, path: str | Path) -> None:
        self._database = SqliteDatabase(
            path, label="offline_execution", error=LedgerError, prepare=_prepare
        )
        self._executor_path = Path(path).expanduser().resolve().with_suffix(".executor.lock")
        self._lock = Lock()

    def close(self) -> None:
        self._database.close()

    @contextmanager
    def executor(self) -> Iterator[None]:
        if not self._lock.acquire(blocking=False):
            raise LedgerError("offline executor busy — scope=ledger instance")
        try:
            with self._executor_path.open("a") as handle:
                try:
                    _file_lock(handle.fileno(), acquire=True)
                except BlockingIOError as error:
                    raise LedgerError("offline executor busy — scope=database file") from error
                try:
                    yield
                finally:
                    _file_lock(handle.fileno(), acquire=False)
        except OSError as error:
            raise LedgerError(
                f"offline executor lock failed — path={self._executor_path} {error}"
            ) from error
        finally:
            self._lock.release()

    def initialize(self, baseline: Account) -> None:
        with self._database.transaction(write=True) as connection:
            if connection.execute(
                "SELECT 1 FROM execution_meta WHERE key = ?", ("baseline",)
            ).fetchone():
                raise LedgerError("baseline already initialized — refusing account reset")
            if connection.execute("SELECT 1 FROM order_intents").fetchone():
                raise LedgerError(
                    "cannot initialize baseline — existing intent ledger is non-empty"
                )
            connection.execute(
                "INSERT INTO execution_meta VALUES (?, ?)",
                ("baseline", json.dumps(asdict(baseline))),
            )

    def baseline(self) -> Account:
        with self._database.transaction(write=False) as connection:
            row = connection.execute(
                "SELECT value FROM execution_meta WHERE key = ?", ("baseline",)
            ).fetchone()
            if row is None:
                raise LedgerError("baseline missing — initialize before offline execution")
            try:
                data = json.loads(row[0])
                return Account(
                    data["cash_krw"],
                    tuple((k, v) for k, v in data["holdings"]),
                    data["reserved_cash_krw"],
                )
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                raise LedgerError(
                    f"invalid stored account baseline — expected=valid Account: {error}"
                ) from error

    def orders(self) -> tuple[OrderFact, ...]:
        with self._database.transaction(write=False) as connection:
            return tuple(
                _decode(row[0])
                for row in connection.execute("SELECT fact FROM order_intents ORDER BY intent_id")
            )

    def save_intent(self, intent: Intent) -> OrderFact:
        fact = OrderFact(intent, OrderState.READY)
        with self._database.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT fact FROM order_intents WHERE intent_id = ?", (intent.intent_id,)
            ).fetchone()
            if row is not None:
                existing = _decode(row[0])
                if existing.intent != intent:
                    raise LedgerError(f"intent payload conflict — intent_id={intent.intent_id}")
                return existing
            connection.execute(
                "INSERT INTO order_intents VALUES (?, ?)",
                (intent.intent_id, json.dumps(asdict(fact))),
            )
        return fact

    def claim(self, intent_id: str) -> bool:
        with self._database.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT fact FROM order_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if row is None:
                raise LedgerError(f"claim missing intent — intent_id={intent_id}")
            fact = _decode(row[0])
            if fact.state != OrderState.READY:
                return False
            claimed = OrderFact(fact.intent, OrderState.UNKNOWN)
            connection.execute(
                "UPDATE order_intents SET fact = ? WHERE intent_id = ?",
                (json.dumps(asdict(claimed)), intent_id),
            )
            return True

    def apply(self, fact: OrderFact) -> None:
        with self._database.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT fact FROM order_intents WHERE intent_id = ?", (fact.intent.intent_id,)
            ).fetchone()
            if row is None:
                raise LedgerError(f"event for missing intent — intent_id={fact.intent.intent_id}")
            previous = _decode(row[0])
            if previous.intent != fact.intent:
                raise LedgerError(f"event payload conflict — intent_id={fact.intent.intent_id}")
            seen = connection.execute(
                "SELECT fact FROM order_events WHERE intent_id = ? AND revision = ?",
                (fact.intent.intent_id, fact.revision),
            ).fetchone()
            if seen is not None:
                if _decode(seen[0]) != fact:
                    raise LedgerError(
                        f"conflicting same-revision event — intent_id={fact.intent.intent_id} "
                        f"revision={fact.revision}"
                    )
                return
            if fact == previous:
                return
            decision = classify_event(previous, fact)
            if decision.disposition == EventDisposition.CONFLICT:
                raise LedgerError(decision.detail)
            connection.execute(
                "INSERT INTO order_events VALUES (?, ?, ?)",
                (fact.intent.intent_id, fact.revision, json.dumps(asdict(fact))),
            )
            if decision.disposition == EventDisposition.IGNORE:
                return
            connection.execute(
                "UPDATE order_intents SET fact = ? WHERE intent_id = ?",
                (json.dumps(asdict(fact)), fact.intent.intent_id),
            )

    def set_halted(self, halted: bool) -> None:
        with self._database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO execution_meta VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("halted", json.dumps(halted)),
            )

    def halted(self) -> bool:
        with self._database.transaction(write=False) as connection:
            row = connection.execute(
                "SELECT value FROM execution_meta WHERE key = ?", ("halted",)
            ).fetchone()
            if row is None:
                return False
            try:
                value = json.loads(row[0])
            except (ValueError, TypeError) as error:
                raise LedgerError(f"invalid stored halt flag — expected=bool: {error}") from error
            if type(value) is not bool:
                raise LedgerError(f"invalid stored halt flag — expected=bool got={value!r}")
            return value


def _file_lock(descriptor: int, *, acquire: bool) -> None:
    """지원 OS에서만 잠금을 획득하며 잠금 없는 실행으로 폴백하지 않는다."""
    import os

    if os.name == "posix":
        import fcntl

        operation = fcntl.LOCK_EX | fcntl.LOCK_NB if acquire else fcntl.LOCK_UN
        fcntl.flock(descriptor, operation)
    elif os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK if acquire else msvcrt.LK_UNLCK, 1)
    else:
        raise LedgerError(f"offline executor locking unsupported — os.name={os.name}")
