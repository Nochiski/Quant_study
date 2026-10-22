"""키움 어댑터 전용 참조 원장.

키움은 우리 의도 ID와 S05 revision을 모른다. 이 파일이 의도 ↔ 주문번호, 전송 직전 기록, 마지막
관측값(revision 포함), 정지 래치를 소유한다. S05 실행 원장과 다른 파일이며 서로의 행을 읽지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from strategy_workbench.adapters.outbound.sqlite_store.facade.database import SqliteDatabase
from strategy_workbench.adapters.outbound.sqlite_store.facade.schema import (
    SchemaContract,
    ensure_schema,
)
from strategy_workbench.application.offline_execution.facade.execution import BrokerError
from strategy_workbench.domain.order_execution.facade.orders import Intent, OrderFact, OrderState

_OBJECTS = (
    (
        "table",
        "kiwoom_meta",
        "CREATE TABLE kiwoom_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID",
    ),
    (
        "table",
        "kiwoom_orders",
        "CREATE TABLE kiwoom_orders (intent_id TEXT PRIMARY KEY, intent TEXT NOT NULL, "
        "order_date TEXT NOT NULL, sent_at TEXT NOT NULL, "
        "status TEXT NOT NULL CHECK (status IN ('pending', 'bound', 'answered', 'refused')), "
        "ord_no TEXT, note TEXT NOT NULL, fact TEXT, "
        "finalized INTEGER NOT NULL CHECK (finalized IN (0, 1)), "
        "CHECK ((status = 'bound') = (ord_no IS NOT NULL))) WITHOUT ROWID",
    ),
    (
        "index",
        "kiwoom_orders_ord_no",
        "CREATE UNIQUE INDEX kiwoom_orders_ord_no ON kiwoom_orders (order_date, ord_no) "
        "WHERE ord_no IS NOT NULL",
    ),
)


ACCOUNT_TAIL_DIGITS = 4


class KiwoomReferenceError(BrokerError):
    """참조 원장을 읽거나 쓰지 못했다. 전송 전이면 보내지 않는다."""


class ReferenceStatus(StrEnum):
    PENDING = "pending"  # 전송 직전 기록만 있고 주문번호가 없다
    BOUND = "bound"  # 키움 주문번호에 묶였다
    ANSWERED = "answered"  # 키움이 오류 코드로 답했다(주문번호 없음)
    REFUSED = "refused"  # 어댑터가 보내지 않고 거부로 확정했다


@dataclass(frozen=True)
class ReferenceOrder:
    intent: Intent
    order_date: str  # KST YYYYMMDD
    sent_at: str  # KST HH:MM:SS
    status: ReferenceStatus
    ord_no: str | None
    note: str
    fact: OrderFact | None
    finalized: bool


def _prepare(connection: sqlite3.Connection) -> None:
    ensure_schema(
        connection,
        SchemaContract(
            label="kiwoom_mock_reference",
            application_id=0x4B574D4B,
            version=1,
            objects=_OBJECTS,
            error=KiwoomReferenceError,
        ),
    )


class KiwoomReference:
    """명시적 경로만 받는다. S05 executor 안에서 한 실행자만 쓴다는 전제다."""

    def __init__(self, path: str | Path) -> None:
        self._database = SqliteDatabase(
            path, label="kiwoom_mock_reference", error=KiwoomReferenceError, prepare=_prepare
        )

    def close(self) -> None:
        self._database.close()

    def bind_account(self, account_number: str) -> bool:
        """처음이면 계좌번호 끝 4자리를 저장한다. 다른 계좌의 원장이면 거짓이다.

        예전 판은 계좌번호 전체의 SHA-256 을 남겼다. 10자리 숫자라 바로 복원되므로(2026-10-22 리뷰
        #593) 같은 계좌로 열면 끝 4자리로 바꿔 적고 해시는 지운다. 계좌번호 전체는 저장하지 않는다.
        """
        tail = account_number[-ACCOUNT_TAIL_DIGITS:]
        with self._database.transaction(write=True) as connection:
            stored = dict(
                connection.execute(
                    "SELECT key, value FROM kiwoom_meta "
                    "WHERE key IN ('account_tail', 'account_sha256')"
                ).fetchall()
            )
            if "account_tail" in stored:
                return stored["account_tail"] == tail
            if "account_sha256" in stored:
                if stored["account_sha256"] != hashlib.sha256(account_number.encode()).hexdigest():
                    return False
                connection.execute("DELETE FROM kiwoom_meta WHERE key = 'account_sha256'")
            connection.execute("INSERT INTO kiwoom_meta VALUES ('account_tail', ?)", (tail,))
            return True

    def latch(self) -> str | None:
        with self._database.transaction(write=False) as connection:
            row = connection.execute("SELECT value FROM kiwoom_meta WHERE key = 'latch'").fetchone()
            return None if row is None else str(row[0])

    def set_latch(self, detail: str) -> None:
        """처음 걸린 사유를 남긴다. 이미 걸려 있으면 바꾸지 않는다."""
        with self._database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO kiwoom_meta VALUES ('latch', ?) ON CONFLICT(key) DO NOTHING",
                (detail,),
            )

    def clear_latch(self) -> None:
        with self._database.transaction(write=True) as connection:
            connection.execute("DELETE FROM kiwoom_meta WHERE key = 'latch'")

    def orders(self) -> tuple[ReferenceOrder, ...]:
        with self._database.transaction(write=False) as connection:
            return tuple(
                _decode(row)
                for row in connection.execute(
                    "SELECT * FROM kiwoom_orders ORDER BY order_date, sent_at, intent_id"
                )
            )

    def get(self, intent_id: str) -> ReferenceOrder | None:
        with self._database.transaction(write=False) as connection:
            row = connection.execute(
                "SELECT * FROM kiwoom_orders WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            return None if row is None else _decode(row)

    def record_pending(self, intent: Intent, order_date: str, sent_at: str) -> None:
        """전송 직전 기록. 같은 의도가 이미 있으면 거부해 두 번 보내지 않는다."""
        self._insert(intent, order_date, sent_at, ReferenceStatus.PENDING, "", None)

    def record_refused(
        self, intent: Intent, order_date: str, sent_at: str, note: str, fact: OrderFact
    ) -> None:
        self._insert(intent, order_date, sent_at, ReferenceStatus.REFUSED, note, fact)

    def mark_answered(self, intent_id: str, note: str) -> None:
        with self._database.transaction(write=True) as connection:
            updated = connection.execute(
                "UPDATE kiwoom_orders SET status = 'answered', note = ? "
                "WHERE intent_id = ? AND status = 'pending'",
                (note, intent_id),
            ).rowcount
            if updated != 1:
                raise KiwoomReferenceError(f"no pending order to mark — intent_id={intent_id}")

    def bind(self, intent_id: str, ord_no: str, fact: OrderFact | None) -> None:
        with self._database.transaction(write=True) as connection:
            updated = connection.execute(
                "UPDATE kiwoom_orders SET status = 'bound', ord_no = ?, fact = ? "
                "WHERE intent_id = ? AND status IN ('pending', 'answered')",
                (ord_no, None if fact is None else _encode_fact(fact), intent_id),
            ).rowcount
            if updated != 1:
                raise KiwoomReferenceError(f"no unbound order to bind — intent_id={intent_id}")

    def observe(self, intent_id: str, fact: OrderFact, *, finalized: bool) -> None:
        with self._database.transaction(write=True) as connection:
            updated = connection.execute(
                "UPDATE kiwoom_orders SET fact = ?, finalized = ? WHERE intent_id = ?",
                (_encode_fact(fact), int(finalized), intent_id),
            ).rowcount
            if updated != 1:
                raise KiwoomReferenceError(f"observation for unknown order — intent_id={intent_id}")

    def _insert(
        self,
        intent: Intent,
        order_date: str,
        sent_at: str,
        status: ReferenceStatus,
        note: str,
        fact: OrderFact | None,
    ) -> None:
        with self._database.transaction(write=True) as connection:
            if connection.execute(
                "SELECT 1 FROM kiwoom_orders WHERE intent_id = ?", (intent.intent_id,)
            ).fetchone():
                raise KiwoomReferenceError(
                    f"intent already recorded — refusing a second send intent_id={intent.intent_id}"
                )
            connection.execute(
                "INSERT INTO kiwoom_orders VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                (
                    intent.intent_id,
                    json.dumps(asdict(intent)),
                    order_date,
                    sent_at,
                    status.value,
                    note,
                    None if fact is None else _encode_fact(fact),
                    int(fact is not None and fact.state.terminal),
                ),
            )


def _encode_fact(fact: OrderFact) -> str:
    return json.dumps(asdict(fact))


def _decode(row: sqlite3.Row) -> ReferenceOrder:
    try:
        fact = None
        if row["fact"] is not None:
            data = json.loads(row["fact"])
            fact = OrderFact(
                intent=Intent(**data["intent"]),
                state=OrderState(data["state"]),
                revision=data["revision"],
                filled_quantity=data["filled_quantity"],
                filled_value_krw=data["filled_value_krw"],
                fees_krw=data["fees_krw"],
            )
        return ReferenceOrder(
            intent=Intent(**json.loads(row["intent"])),
            order_date=row["order_date"],
            sent_at=row["sent_at"],
            status=ReferenceStatus(row["status"]),
            ord_no=row["ord_no"],
            note=row["note"],
            fact=fact,
            finalized=bool(row["finalized"]),
        )
    except (ValueError, TypeError, KeyError) as error:
        raise KiwoomReferenceError(
            f"invalid stored kiwoom reference row — intent_id={row['intent_id']}: {error}"
        ) from error
