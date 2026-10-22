"""S05 `BrokerPort`의 키움 모의 구현.

- 기본은 보내지 않는다(`transmit=False`). 보내지 않은 의도는 거부로 확정해 기록한다.
- 주문은 KRX 지정가(`trde_tp` 0)만 보낸다. 전송 직전 기록을 먼저 커밋하고, 실패하면 보내지 않는다.
- 주문번호는 접수일 뿐이다. 체결·거부·취소는 조회로 확정한다.
- 응답을 잃은 의도는 당일 주문에서 같은 내용의 후보가 정확히 1건일 때만 다시 묶는다. 자동으로 다시
  보내지 않는다.
- 해석이 맞지 않는 응답은 정지 래치를 건다. 사람이 `clear_latch`로 풀기 전까지 모든 조회가 멈춘다.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import NoReturn

from strategy_workbench.adapters.outbound.kiwoom_mock._http import (
    KST,
    MOCK_MIN_INTERVAL_SECONDS,
    KiwoomAnsweredError,
    KiwoomBlock,
    KiwoomBlockedError,
    KiwoomClient,
    KiwoomCredentials,
    KiwoomNoResponseError,
    KiwoomTransport,
    UrllibTransport,
)
from strategy_workbench.adapters.outbound.kiwoom_mock._reference import (
    KiwoomReference,
    ReferenceOrder,
    ReferenceStatus,
)
from strategy_workbench.adapters.outbound.kiwoom_mock._rows import (
    NO_ORIGINAL,
    UNTRACKED_PREFIX,
    ContractViolation,
    DayTrade,
    JournalNotReady,
    Observation,
    OrderRow,
    Participant,
    day_trades,
    is_candidate,
    number,
    observe,
    order_number,
    same_order,
    split_day_amounts,
    symbol,
    text,
    untracked_fact,
)
from strategy_workbench.application.offline_execution.facade.execution import BrokerError
from strategy_workbench.domain.order_execution.facade.orders import (
    Account,
    EventDisposition,
    Intent,
    OrderFact,
    OrderState,
    Snapshot,
    classify_event,
)

logger = logging.getLogger(__name__)

_ACCOUNT_PATH = "/api/dostk/acnt"
_SYMBOL = re.compile(r"[0-9A-Z]{6}")
# 재식별 후보의 주문 시각 하한 여유. 키움과 PC 시각 차이는 ③에서 재고 정한다.
DEFAULT_REMATCH_MARGIN = timedelta(seconds=60)


@dataclass(frozen=True)
class _Reads:
    orders: list[OrderRow]
    day_trades: dict[str, DayTrade]
    holdings: tuple[tuple[str, int], ...]
    cash_krw: int


class KiwoomMockBroker:
    def __init__(
        self,
        credentials: KiwoomCredentials,
        reference_path: str | Path,
        *,
        transport: KiwoomTransport | None = None,
        transmit: bool = False,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        min_interval_seconds: float = MOCK_MIN_INTERVAL_SECONDS,
        rematch_margin: timedelta = DEFAULT_REMATCH_MARGIN,
        max_snapshot_reads: int = 3,
    ) -> None:
        if max_snapshot_reads < 1:
            raise ValueError(f"max_snapshot_reads must be positive — got={max_snapshot_reads}")
        self._now = now or (lambda: datetime.now(KST))
        self._client = KiwoomClient(
            credentials,
            transport or UrllibTransport(),
            now=self._now,
            monotonic=monotonic,
            sleep=sleep,
            min_interval_seconds=min_interval_seconds,
        )
        self._account_sha256 = hashlib.sha256(credentials.account_number.encode()).hexdigest()
        self._reference = KiwoomReference(reference_path)
        self._transmit = transmit
        self._rematch_margin = rematch_margin
        self._max_snapshot_reads = max_snapshot_reads

    def close(self) -> None:
        self._reference.close()

    @property
    def client(self) -> KiwoomClient:
        """같은 토큰과 호출 간격을 함께 쓰려는 이 노드 안 조회용(S07 호가). 주문하지 않는다."""
        return self._client

    def latch(self) -> str | None:
        """걸려 있는 정지 래치의 사유. 없으면 None이다."""
        return self._reference.latch()

    def clear_latch(self) -> None:
        """사람이 원인을 확인한 뒤에만 부른다."""
        self._reference.clear_latch()

    def raw_reads(self) -> dict[str, object]:
        """감사·복기용 조회 원본: 당일 주문(`kt00007`)·당일매매일지(`ka10170`)·잔고(`kt00018`)·
        예수금(`kt00001`). 계좌번호 칸이 있는 TR은 부르지 않는다. 해석하지 않고 그대로 낸다."""
        self._ready()
        cash = self._client.call("kt00001", _ACCOUNT_PATH, {"qry_tp": "3"}).body
        return {
            "taken_at": self._kst().isoformat(timespec="seconds"),
            "kt00007": self._order_rows(""),
            "ka10170": self._client.rows(
                "ka10170",
                _ACCOUNT_PATH,
                {"base_dt": "", "ottks_tp": "2", "ch_crd_tp": "0"},
                "tdy_trde_diary",
            ),
            "kt00018": self._client.rows(
                "kt00018",
                _ACCOUNT_PATH,
                {"qry_tp": "1", "dmst_stex_tp": "KRX"},
                "acnt_evlt_remn_indv_tot",
            ),
            "kt00001": {
                key: value
                for key, value in cash.items()
                if key not in ("return_code", "return_msg")
            },
        }

    # --- BrokerPort -------------------------------------------------------------------------

    def snapshot(self) -> Snapshot:
        self._ready()
        today = self._kst().strftime("%Y%m%d")
        reads = self._stable_reads()
        try:
            facts = self._facts(today, reads)
            tracked = [f for f in facts if not f.intent.intent_id.startswith(UNTRACKED_PREFIX)]
            account = Account(
                reads.cash_krw, reads.holdings, sum(f.reserved_cash_krw for f in tracked)
            )
            return Snapshot(account, tuple(facts))
        except JournalNotReady as error:
            raise BrokerError(str(error)) from error
        except (ContractViolation, ValueError) as error:
            self._violation(f"snapshot — {error}")

    def submit(self, intent: Intent) -> OrderFact:
        now = self._kst()
        order_date, sent_at = now.strftime("%Y%m%d"), now.strftime("%H:%M:%S")
        refusal = self._refusal(intent, order_date)
        if refusal is None:
            try:
                self._ready()
            except KiwoomBlockedError as error:
                if error.reason is KiwoomBlock.ACCOUNT_MISMATCH:
                    raise  # 남의 계좌 원장이거나 키가 다른 계좌다. 이 원장에 아무것도 쓰지 않는다.
                refusal = f"connection not ready — {error}"
            except BrokerError as error:
                refusal = f"connection not ready — {error}"
        elif not self._reference.bind_account(self._account_sha256):
            raise KiwoomBlockedError(
                KiwoomBlock.ACCOUNT_MISMATCH, "reference ledger belongs to another account"
            )
        if refusal is not None:
            # 보내지 않은 것이 확실하므로 결과를 모름으로 두지 않고 거부로 확정한다.
            fact = OrderFact(intent, OrderState.REJECTED, revision=1)
            self._reference.record_refused(intent, order_date, sent_at, refusal, fact)
            logger.warning(
                "kiwoom order not sent — intent_id=%s reason=%s", intent.intent_id, refusal
            )
            return fact
        self._reference.record_pending(intent, order_date, sent_at)
        api_id = "kt10000" if intent.side == "buy" else "kt10001"
        logger.info(
            "kiwoom order send — intent_id=%s api_id=%s symbol=%s quantity=%d price_krw=%d",
            intent.intent_id,
            api_id,
            intent.symbol,
            intent.quantity,
            intent.limit_price_krw,
        )
        try:
            body = self._client.order(
                api_id,
                {
                    "dmst_stex_tp": "KRX",
                    "stk_cd": intent.symbol,
                    "ord_qty": str(intent.quantity),
                    "ord_uv": str(intent.limit_price_krw),
                    "trde_tp": "0",
                    "cond_uv": "",
                },
            )
        except (KiwoomAnsweredError, KiwoomBlockedError) as error:
            self._reference.mark_answered(intent.intent_id, str(error))
            raise
        try:
            ord_no = order_number(text(body, "ord_no"))
        except ContractViolation as error:
            raise KiwoomNoResponseError(
                f"kiwoom accepted without a usable order number — intent_id={intent.intent_id}"
            ) from error
        fact = OrderFact(intent, OrderState.OPEN, revision=1)
        self._reference.bind(intent.intent_id, ord_no, fact)
        return fact

    def cancel(self, intent_id: str) -> OrderFact:
        record = self._reference.get(intent_id)
        if record is None or record.ord_no is None or record.fact is None:
            raise BrokerError(f"cancel requires a bound kiwoom order — intent_id={intent_id}")
        if record.fact.state.terminal:
            return record.fact
        if not self._transmit:
            raise BrokerError(f"transmit disabled — cancel not sent intent_id={intent_id}")
        if record.order_date != self._kst().strftime("%Y%m%d"):
            raise BrokerError(
                f"cancel not sent for a previous-day order — intent_id={intent_id} "
                f"order_date={record.order_date}"
            )
        self._ready()
        self._client.order(
            "kt10003",
            {
                "dmst_stex_tp": "KRX",
                "orig_ord_no": record.ord_no,
                "stk_cd": record.intent.symbol,
                "cncl_qty": "0",
            },
        )
        fact = next((f for f in self.snapshot().orders if f.intent.intent_id == intent_id), None)
        if fact is not None and fact.state.terminal:
            return fact
        raise BrokerError(
            f"cancel sent but not yet confirmed — intent_id={intent_id} "
            f"state={None if fact is None else fact.state}"
        )

    # --- 사람이 푸는 일(2026-10-22 리뷰 #593) --------------------------------------------------

    def unresolved(self) -> tuple[UnresolvedReference, ...]:
        """응답을 잃어 주문번호에 묶이지 않은 의도와, 그 주문일 키움 주문 중 내용이 같은 번호.

        사람이 고를 후보라 자동 재식별의 시각 창은 보지 않는다. 다른 의도에 묶인 번호는 뺀다.
        래치가 걸려 있어도 읽는다(주문은 내지 않는다).
        """
        records = [record for record in self._reference.orders() if _unresolved(record)]
        if not records:
            return ()
        self._session()
        bound = {(r.order_date, r.ord_no) for r in self._reference.orders() if r.ord_no is not None}
        today = self._kst().strftime("%Y%m%d")
        rows: dict[str, list[OrderRow]] = {}
        found: list[UnresolvedReference] = []
        for record in records:
            if record.order_date not in rows:
                query = "" if record.order_date == today else record.order_date
                rows[record.order_date] = [OrderRow.parse(row) for row in self._order_rows(query)]
            candidates = tuple(
                row.ord_no
                for row in rows[record.order_date]
                if (record.order_date, row.ord_no) not in bound and same_order(row, record.intent)
            )
            found.append(UnresolvedReference(record, candidates))
        return tuple(found)

    def bind_unresolved(self, intent_id: str, ord_no: str) -> None:
        """사람이 고른 주문번호에 묶는다. 그 번호가 지금 후보일 때만 묶는다."""
        target = self._unresolved_one(intent_id)
        if ord_no not in target.candidates:
            raise BrokerError(
                f"order number is not a candidate — intent_id={intent_id} ord_no={ord_no} "
                f"candidates={list(target.candidates)}"
            )
        self._reference.bind(intent_id, ord_no, None)
        logger.warning("kiwoom order bound by hand — intent_id=%s ord_no=%s", intent_id, ord_no)

    def reject_unresolved(self, intent_id: str) -> None:
        """그 주문일 키움에 내용이 같은 주문이 하나도 없을 때만 거부(보내지 않음)로 확정한다."""
        target = self._unresolved_one(intent_id)
        if target.candidates:
            raise BrokerError(
                f"kiwoom has orders with the same content — not rejected intent_id={intent_id} "
                f"candidates={list(target.candidates)}"
            )
        fact = OrderFact(target.record.intent, OrderState.REJECTED, revision=1)
        self._reference.observe(intent_id, fact, finalized=True)
        logger.warning("kiwoom order rejected by hand — intent_id=%s", intent_id)

    def _unresolved_one(self, intent_id: str) -> UnresolvedReference:
        found = next((item for item in self.unresolved() if item.intent_id == intent_id), None)
        if found is None:
            raise BrokerError(f"no unresolved order with this intent — intent_id={intent_id}")
        return found

    # --- 내부 --------------------------------------------------------------------------------

    def _kst(self) -> datetime:
        return self._now().astimezone(KST)

    def _ready(self) -> None:
        latched = self._reference.latch()
        if latched is not None:
            raise KiwoomBlockedError(KiwoomBlock.LATCHED, latched)
        self._session()

    def _session(self) -> None:
        """연결(토큰·계좌 대조)과 참조 원장의 계좌만 확인한다. 래치는 보지 않는다."""
        self._client.ensure_session()
        if not self._reference.bind_account(self._account_sha256):
            raise KiwoomBlockedError(
                KiwoomBlock.ACCOUNT_MISMATCH, "reference ledger belongs to another account"
            )

    def _refusal(self, intent: Intent, order_date: str) -> str | None:
        if not self._transmit:
            return "transmit disabled (dry-run default)"
        if not _SYMBOL.fullmatch(intent.symbol):
            return f"symbol is not a 6-character KRX code — symbol={intent.symbol!r}"
        # 한 종목에 끝나지 않은 주문은 하나만 둔다. 둘이 함께 체결되면 당일매매일지의 종목 합계를
        # 주문별로 나눌 수 없어 래치가 걸린다(_rows.split_day_amounts). 보내기 전에 거부로 확정한다.
        unfinished = sorted(
            record.intent.intent_id
            for record in self._reference.orders()
            if record.order_date == order_date
            and record.intent.symbol == intent.symbol
            and not record.finalized
            and record.status is not ReferenceStatus.REFUSED
        )
        if unfinished:
            return (
                f"unfinished order on the same symbol — symbol={intent.symbol} "
                f"unfinished={unfinished}"
            )
        return None

    def _violation(self, detail: str) -> NoReturn:
        self._reference.set_latch(detail)
        logger.error("kiwoom mock latched — %s", detail)
        raise KiwoomBlockedError(KiwoomBlock.LATCHED, detail)

    def _order_rows(self, order_date: str) -> list[dict[str, object]]:
        # 당일은 빈 날짜(명세 예시)로, 지난 날은 주문일자로 읽는다.
        return self._client.rows(
            "kt00007",
            _ACCOUNT_PATH,
            {
                "ord_dt": order_date,
                "qry_tp": "1",
                "stk_bond_tp": "0",
                "sell_tp": "0",
                "stk_cd": "",
                "fr_ord_no": "",
                "dmst_stex_tp": "%",
            },
            "acnt_ord_cntr_prps_dtl",
        )

    def _stable_reads(self) -> _Reads:
        """주문 → 당일매매일지 → 보유 → 현금 → 주문. 두 번 읽은 주문이 같을 때만 쓴다."""
        for _ in range(self._max_snapshot_reads):
            first = self._order_rows("")
            journal = self._client.rows(
                "ka10170",
                _ACCOUNT_PATH,
                {"base_dt": "", "ottks_tp": "2", "ch_crd_tp": "0"},
                "tdy_trde_diary",
            )
            holdings = self._client.rows(
                "kt00018",
                _ACCOUNT_PATH,
                {"qry_tp": "1", "dmst_stex_tp": "KRX"},
                "acnt_evlt_remn_indv_tot",
            )
            cash = self._client.call("kt00001", _ACCOUNT_PATH, {"qry_tp": "3"}).body
            second = self._order_rows("")
            if first == second:
                try:
                    return _Reads(
                        [OrderRow.parse(row) for row in second],
                        day_trades([DayTrade.parse(row) for row in journal]),
                        _holdings(holdings),
                        # D+2 추정예수금을 S05 현금으로 본다(③에서 확인).
                        number(cash, "d2_entra"),
                    )
                except ContractViolation as error:
                    self._violation(f"unreadable kiwoom rows — {error}")
        raise BrokerError(
            f"kiwoom orders kept changing during snapshot — reads={self._max_snapshot_reads}"
        )

    def _facts(self, today: str, reads: _Reads) -> list[OrderFact]:
        records = self._reference.orders()
        rows_by_date = {today: reads.orders}
        for order_date in sorted({r.order_date for r in records if not r.finalized} - {today}):
            rows_by_date[order_date] = [OrderRow.parse(row) for row in self._order_rows(order_date)]
        bound = {(r.order_date, r.ord_no) for r in records if r.ord_no is not None}
        resolved: list[ReferenceOrder] = []
        for record in records:
            if record.fact is None and record.status in (
                ReferenceStatus.PENDING,
                ReferenceStatus.ANSWERED,
            ):
                # 거부로 확정한 뒤에는 같은 내용의 주문이 나중에 보여도 묶지 않는다.
                record = self._rematch(record, rows_by_date[record.order_date], bound)
                if record.ord_no is not None:
                    bound.add((record.order_date, record.ord_no))
            resolved.append(record)
        seen = {
            record.intent.intent_id: self._seen(record, today, rows_by_date.get(record.order_date))
            for record in resolved
        }
        untracked = [
            row
            for row in reads.orders
            if row.original == NO_ORIGINAL and (today, row.ord_no) not in bound
        ]
        amounts = self._day_amounts(today, resolved, seen, untracked, reads.day_trades)
        facts: list[OrderFact] = []
        for record in resolved:
            fact = self._fact(record, today, seen[record.intent.intent_id], amounts)
            if fact is not None:
                facts.append(fact)
        facts.extend(untracked_fact(today, row) for row in untracked)
        return facts

    def _rematch(
        self, record: ReferenceOrder, rows: list[OrderRow], bound: set[tuple[str, str]]
    ) -> ReferenceOrder:
        sent_at = datetime.strptime(f"{record.order_date} {record.sent_at}", "%Y%m%d %H:%M:%S")
        candidates = [
            row
            for row in rows
            if (record.order_date, row.ord_no) not in bound
            and is_candidate(row, record.intent, sent_at, self._rematch_margin)
        ]
        if len(candidates) == 1:
            self._reference.bind(record.intent.intent_id, candidates[0].ord_no, None)
            logger.warning(
                "kiwoom order rematched — intent_id=%s ord_no=%s",
                record.intent.intent_id,
                candidates[0].ord_no,
            )
            return ReferenceOrder(
                record.intent,
                record.order_date,
                record.sent_at,
                ReferenceStatus.BOUND,
                candidates[0].ord_no,
                record.note,
                None,
                False,
            )
        if not candidates and record.status == ReferenceStatus.ANSWERED:
            # 키움이 오류로 답했고 같은 주문도 없다. 처리되지 않은 것으로 확정한다.
            fact = OrderFact(record.intent, OrderState.REJECTED, revision=1)
            self._reference.observe(record.intent.intent_id, fact, finalized=True)
            return ReferenceOrder(
                record.intent,
                record.order_date,
                record.sent_at,
                record.status,
                None,
                record.note,
                fact,
                True,
            )
        # 0건(응답 없음)이나 2건 이상이면 묶지 않는다. 사실이 없으므로 S05 대사가 멈춘다.
        logger.warning(
            "kiwoom order unresolved — intent_id=%s status=%s candidates=%s",
            record.intent.intent_id,
            record.status,
            [row.ord_no for row in candidates],
        )
        return record

    def _seen(
        self, record: ReferenceOrder, today: str, rows: list[OrderRow] | None
    ) -> Observation | None:
        """새로 관측할 주문의 상태·체결수량. 마지막 사실을 그대로 둘 주문은 None이다."""
        if record.ord_no is None or record.finalized or rows is None:
            return None
        identity = f"intent_id={record.intent.intent_id} ord_no={record.ord_no}"
        closed_day = record.order_date < today
        main = next(
            (row for row in rows if row.ord_no == record.ord_no and row.original == NO_ORIGINAL),
            None,
        )
        if main is None:
            if closed_day or record.fact is None:
                raise ContractViolation(f"bound kiwoom order is missing from orders — {identity}")
            # 접수 직후 조회에 아직 안 보일 수 있다. 마지막 관측을 유지한다.
            return None
        related = [row for row in rows if row.original == record.ord_no]
        return observe(record.intent, main, related, closed_day=closed_day)

    def _day_amounts(
        self,
        today: str,
        records: list[ReferenceOrder],
        seen: dict[str, Observation | None],
        untracked: list[OrderRow],
        journal: dict[str, DayTrade],
    ) -> dict[str, tuple[int, int]]:
        """오늘 주문을 종목별로 묶어 당일매매일지 합계를 나눈다."""
        settled: dict[str, list[OrderFact]] = {}
        pending: dict[str, list[Participant]] = {}
        outside: dict[str, list[Participant]] = {}
        for record in records:
            if record.order_date != today:
                continue
            code = record.intent.symbol
            observation = seen[record.intent.intent_id]
            if observation is not None:
                pending.setdefault(code, []).append(
                    Participant(
                        record.intent.intent_id, record.intent.side, observation.filled_quantity
                    )
                )
            elif record.fact is not None and record.fact.filled_quantity:
                settled.setdefault(code, []).append(record.fact)
        for row in untracked:
            outside.setdefault(row.symbol, []).append(
                Participant(row.ord_no, row.side or "buy", row.filled_quantity)
            )
        amounts: dict[str, tuple[int, int]] = {}
        for code in sorted({*settled, *pending, *outside, *journal}):
            amounts.update(
                split_day_amounts(
                    journal.get(code, DayTrade(code, 0, 0, 0, 0, 0)),
                    settled.get(code, []),
                    pending.get(code, []),
                    outside.get(code, []),
                )
            )
        return amounts

    def _fact(
        self,
        record: ReferenceOrder,
        today: str,
        seen: Observation | None,
        amounts: dict[str, tuple[int, int]],
    ) -> OrderFact | None:
        if seen is None:
            return record.fact
        identity = f"intent_id={record.intent.intent_id} ord_no={record.ord_no}"
        previous = record.fact
        if record.order_date < today:
            # 지난 날의 합계는 다시 읽지 않는다. 마지막 관측 뒤 체결이 늘었으면 멈춘다.
            if seen.filled_quantity != (0 if previous is None else previous.filled_quantity):
                raise ContractViolation(
                    f"fills after the last same-day observation have unknown amounts — {identity}"
                )
            value, fees = (
                (0, 0)
                if previous is None
                else (
                    previous.filled_value_krw,
                    previous.fees_krw,
                )
            )
        else:
            value, fees = amounts[record.intent.intent_id]
        if previous is not None and (
            previous.state,
            previous.filled_quantity,
            previous.filled_value_krw,
            previous.fees_krw,
        ) == (seen.state, seen.filled_quantity, value, fees):
            fact = previous
        else:
            fact = OrderFact(
                record.intent,
                seen.state,
                1 if previous is None else previous.revision + 1,
                seen.filled_quantity,
                value,
                fees,
            )
            if previous is not None:
                decision = classify_event(previous, fact)
                if decision.disposition != EventDisposition.APPLY:
                    raise ContractViolation(f"{decision.detail} — {identity}")
        if fact is not previous or (fact.state.terminal and not record.finalized):
            self._reference.observe(record.intent.intent_id, fact, finalized=fact.state.terminal)
        return fact


def _holdings(rows: list[dict[str, object]]) -> tuple[tuple[str, int], ...]:
    """같은 종목이 신용구분별로 여러 줄일 수 있어 합친다."""
    totals: dict[str, int] = {}
    for row in rows:
        quantity = number(row, "rmnd_qty")
        if quantity:
            code = symbol(text(row, "stk_cd"))
            totals[code] = totals.get(code, 0) + quantity
    return tuple(sorted(totals.items()))


@dataclass(frozen=True)
class UnresolvedReference:
    """응답을 잃어 주문번호에 묶이지 않은 의도 하나와, 사람이 고를 수 있는 키움 주문번호."""

    record: ReferenceOrder
    candidates: tuple[str, ...]

    @property
    def intent_id(self) -> str:
        return self.record.intent.intent_id


def _unresolved(record: ReferenceOrder) -> bool:
    return record.fact is None and record.status in (
        ReferenceStatus.PENDING,
        ReferenceStatus.ANSWERED,
    )

