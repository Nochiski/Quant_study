"""키움 조회 행을 S05 누적 사실로 옮기는 해석.

키움 필드의 뜻 가운데 ③ 모의 실측 전까지 확정하지 못한 것(체결단가가 평균인지, 수수료 행이 주문당
하나인지, 거부가 어떻게 남는지)은 추정하지 않고 `ContractViolation`으로 멈춘다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from strategy_workbench.domain.order_execution.facade.orders import Intent, OrderFact, OrderState

NO_ORIGINAL = "0000000"
# kt00007은 지정가를 "지정가"로, ka10076·실시간은 "보통"으로 적는다. 우리는 trde_tp 0만 보낸다.
_LIMIT_TYPES = frozenset({"지정가", "보통"})
UNTRACKED_PREFIX = "kiwoom-untracked-"


class ContractViolation(Exception):
    """키움 응답이 이 어댑터의 해석과 맞지 않는다. 어댑터는 정지 래치를 건다."""


class FeesNotReady(Exception):
    """체결은 보이는데 수수료 행이 아직 따라오지 않았다. 다음 조회에서 다시 본다."""


def number(row: Mapping[str, object], key: str) -> int:
    """0으로 채운 부호 포함 숫자 문자열. 빈 값은 0이다."""
    text = "" if row.get(key) is None else str(row.get(key)).strip()
    if not text:
        return 0
    try:
        return int(text)
    except ValueError as error:
        raise ContractViolation(f"non-numeric kiwoom field — key={key} value={text!r}") from error


def text(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    return "" if value is None else str(value).strip()


def order_number(value: str) -> str:
    """주문 응답은 "00024"처럼, 조회는 7자리로 준다. 7자리로 맞춘다."""
    if not value.isdigit():
        raise ContractViolation(f"invalid kiwoom order number — value={value!r}")
    return value.zfill(7)


def symbol(value: str) -> str:
    """조회의 종목번호는 접두어 1자리(A 주식) + 6자리다. 주문에 쓰는 6자리로 맞춘다."""
    return value[1:] if len(value) == 7 and value[0].isalpha() else value


def side(io_name: str) -> str | None:
    if "매수" in io_name:
        return "buy"
    if "매도" in io_name:
        return "sell"
    return None


def clock(value: str) -> str:
    """ "13:05:43"과 "130543"을 HH:MM:SS로 맞춘다."""
    digits = value.replace(":", "")
    if len(digits) != 6 or not digits.isdigit():
        raise ContractViolation(f"invalid kiwoom order time — value={value!r}")
    return f"{digits[:2]}:{digits[2:4]}:{digits[4:]}"


@dataclass(frozen=True)
class OrderRow:
    """`kt00007` 계좌별주문체결내역상세의 한 행."""

    ord_no: str
    original: str
    symbol: str
    side: str | None
    trade_type: str
    channel: str
    accept: str
    amendment: str
    time: str
    quantity: int
    price_krw: int
    confirmed_quantity: int
    filled_quantity: int
    fill_price_krw: int
    remaining_quantity: int

    @classmethod
    def parse(cls, row: Mapping[str, object]) -> OrderRow:
        return cls(
            ord_no=order_number(text(row, "ord_no")),
            original=order_number(text(row, "ori_ord") or NO_ORIGINAL),
            symbol=symbol(text(row, "stk_cd")),
            side=side(text(row, "io_tp_nm")),
            trade_type=text(row, "trde_tp"),
            channel=text(row, "comm_ord_tp"),
            accept=text(row, "acpt_tp"),
            amendment=text(row, "mdfy_cncl"),
            time=clock(text(row, "ord_tm")),
            quantity=number(row, "ord_qty"),
            price_krw=number(row, "ord_uv"),
            confirmed_quantity=number(row, "cnfm_qty"),
            filled_quantity=number(row, "cntr_qty"),
            fill_price_krw=number(row, "cntr_uv"),
            remaining_quantity=number(row, "ord_remnq"),
        )


@dataclass(frozen=True)
class FeeRow:
    """`ka10076` 체결요청의 한 행. 수수료와 세금을 합쳐 S05 비용으로 본다."""

    ord_no: str
    filled_quantity: int
    fees_krw: int

    @classmethod
    def parse(cls, row: Mapping[str, object]) -> FeeRow:
        return cls(
            ord_no=order_number(text(row, "ord_no")),
            filled_quantity=number(row, "cntr_qty"),
            fees_krw=number(row, "tdy_trde_cmsn") + number(row, "tdy_trde_tax"),
        )


def is_candidate(row: OrderRow, intent: Intent, sent_at: datetime, margin: timedelta) -> bool:
    """응답을 잃은 의도의 재식별 후보. 이미 다른 의도에 묶인 번호는 호출자가 뺀다."""
    earliest = sent_at - margin
    earliest_text = (
        "00:00:00" if earliest.date() < sent_at.date() else earliest.strftime("%H:%M:%S")
    )
    return (
        row.original == NO_ORIGINAL
        and "REST" in row.channel.upper()
        and row.symbol == intent.symbol
        and row.side == intent.side
        and row.quantity == intent.quantity
        and row.price_krw == intent.limit_price_krw
        and row.trade_type in _LIMIT_TYPES
        and row.time >= earliest_text
    )


@dataclass(frozen=True)
class Observation:
    state: OrderState
    filled_quantity: int
    filled_value_krw: int


def observe(
    intent: Intent, main: OrderRow, related: list[OrderRow], *, closed_day: bool
) -> Observation:
    """원주문 행과 그 주문을 가리키는 정정·취소 행으로 누적 상태를 정한다."""
    identity = f"intent_id={intent.intent_id} ord_no={main.ord_no}"
    if (
        main.symbol != intent.symbol
        or main.side != intent.side
        or main.quantity != intent.quantity
        or main.price_krw != intent.limit_price_krw
    ):
        raise ContractViolation(f"kiwoom order differs from the recorded intent — {identity}")
    if any("정정" in row.amendment for row in related):
        raise ContractViolation(f"kiwoom order was amended outside this adapter — {identity}")
    filled = main.filled_quantity
    cancelled = sum(row.confirmed_quantity for row in related if "취소" in row.amendment)
    if filled < 0 or cancelled < 0 or filled + cancelled > intent.quantity:
        raise ContractViolation(
            f"kiwoom quantities exceed the order — {identity} filled={filled} cancelled={cancelled}"
        )
    if filled and main.fill_price_krw <= 0:
        raise ContractViolation(f"kiwoom fill has no price — {identity} filled={filled}")
    if "거부" in main.accept:
        if filled or cancelled:
            raise ContractViolation(f"kiwoom rejection with fills — {identity}")
        state = OrderState.REJECTED
    elif filled == intent.quantity:
        state = OrderState.FILLED
    elif cancelled:
        if filled + cancelled != intent.quantity:
            raise ContractViolation(
                f"kiwoom partial cancel leaves an open remainder — {identity} "
                f"filled={filled} cancelled={cancelled}"
            )
        state = OrderState.CANCELLED
    elif closed_day:
        # 정규장 지정가 주문은 그날만 유효하다. 지난 거래일의 잔량은 장 마감으로
        # 소멸했다고 본다(③에서 확인).
        state = OrderState.CANCELLED
    elif main.remaining_quantity == 0:
        raise ContractViolation(
            f"kiwoom order has no remainder, fill or cancel — {identity} accept={main.accept!r}"
        )
    elif filled:
        state = OrderState.PARTIAL
    else:
        state = OrderState.OPEN
    # 체결단가가 평균이라고 본다. 반올림이 있으면 현금 대사가 어긋나 멈춘다(③에서 확인).
    return Observation(state, filled, filled * main.fill_price_krw)


def fees_today(ord_no: str, filled_quantity: int, rows: list[FeeRow]) -> int:
    """주문 하나의 수수료 행은 건별이든 한 줄이든 체결수량 합이 맞아야 한다."""
    if not filled_quantity:
        return 0
    matched = [row for row in rows if row.ord_no == ord_no]
    reported = sum(row.filled_quantity for row in matched)
    if reported < filled_quantity:
        raise FeesNotReady(
            f"kiwoom fee rows lag the fills — ord_no={ord_no} filled={filled_quantity} "
            f"fee_rows_filled={reported}"
        )
    if reported > filled_quantity:
        raise ContractViolation(
            f"kiwoom fee rows exceed the fills — ord_no={ord_no} filled={filled_quantity} "
            f"fee_rows_filled={reported} rows={len(matched)}"
        )
    return sum(row.fees_krw for row in matched)


def untracked_fact(order_date: str, row: OrderRow) -> OrderFact:
    """이 어댑터가 보내지 않은 주문. 대사가 이름으로 잡도록 형식만 맞춘 자리표시 사실이다.

    수량·가격·비용은 S05 값 타입 검사를 통과시키려고 맞춘 값이며 판정에 쓰지 않는다.
    """
    quantity = max(row.quantity, row.filled_quantity, 1)
    filled = row.filled_quantity
    if row.side == "sell":
        limit = row.fill_price_krw if filled else max(row.price_krw, 1)
    else:
        limit = max(row.price_krw, row.fill_price_krw, 1)
    intent = Intent(
        f"{UNTRACKED_PREFIX}{order_date}-{row.ord_no}",
        row.symbol or "unknown",
        row.side or "buy",
        quantity,
        limit,
    )
    if filled == quantity:
        state = OrderState.FILLED
    elif filled:
        state = OrderState.PARTIAL
    else:
        state = OrderState.OPEN
    return OrderFact(intent, state, 1, filled, filled * row.fill_price_krw if filled else 0)
