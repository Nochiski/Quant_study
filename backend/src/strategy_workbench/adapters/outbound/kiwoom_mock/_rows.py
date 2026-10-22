"""키움 조회 행을 S05 누적 사실로 옮기는 해석.

주문의 상태와 체결수량은 `kt00007`, 체결금액과 비용은 `ka10170` 당일매매일지의 종목별 하루 합계에서
읽는다. 2026-10-21 모의 실측에서 여러 번 나눠 체결된 주문의 `kt00007` 체결단가는 평균을 원 단위로
반올림한 값이었고, `ka10076` 수수료는 체결 건마다 10원 미만을 버린 합이라 현금 차감과 달랐다.
해석과 맞지 않는 응답은 추정하지 않고 `ContractViolation`으로 멈춘다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from strategy_workbench.domain.order_execution.facade.orders import Intent, OrderFact, OrderState

NO_ORIGINAL = "0000000"
# 우리는 trde_tp 0(보통 = 지정가)만 보낸다. 명세 예시는 "지정가"·"보통"이고, 2026-10-20 모의 실측의
# kt00007은 "보통가"였다.
_LIMIT_TYPES = frozenset({"지정가", "보통", "보통가"})
# REST로 낸 주문의 통신구분. 명세는 "REST API"이고 모의 실측은 "모의투자"였다. 모의에서 HTS 주문도
# "모의투자"로 남는지는 미확인이라, 같은 내용의 HTS 주문이 있으면 후보 2건으로 정지한다.
_ORDER_CHANNELS = frozenset({"REST API", "모의투자"})
UNTRACKED_PREFIX = "kiwoom-untracked-"


class ContractViolation(Exception):
    """키움 응답이 이 어댑터의 해석과 맞지 않는다. 어댑터는 정지 래치를 건다."""


class JournalNotReady(Exception):
    """당일매매일지와 주문 조회의 체결수량이 아직 맞지 않는다. 다음 조회에서 다시 본다."""


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
class DayTrade:
    """`ka10170` 당일매매일지의 한 종목. 하루 합계가 원 단위로 정확하다.

    2026-10-21 모의 실측에서 매수금액과 수수료가 현금(`d2_entra`) 차감과 일치했다. 수수료와
    세금(`cmsn_alm_tax`)은 매수·매도를 합친 값이다.
    """

    symbol: str
    buy_quantity: int
    buy_value_krw: int
    sell_quantity: int
    sell_value_krw: int
    costs_krw: int

    @classmethod
    def parse(cls, row: Mapping[str, object]) -> DayTrade:
        return cls(
            symbol=symbol(text(row, "stk_cd")),
            buy_quantity=number(row, "buy_qty"),
            buy_value_krw=number(row, "buy_amt"),
            sell_quantity=number(row, "sell_qty"),
            sell_value_krw=number(row, "sell_amt"),
            costs_krw=number(row, "cmsn_alm_tax"),
        )


def day_trades(rows: list[DayTrade]) -> dict[str, DayTrade]:
    """같은 종목이 여러 줄이면 합친다."""
    totals: dict[str, DayTrade] = {}
    for row in rows:
        seen = totals.get(row.symbol)
        totals[row.symbol] = (
            row
            if seen is None
            else DayTrade(
                row.symbol,
                seen.buy_quantity + row.buy_quantity,
                seen.buy_value_krw + row.buy_value_krw,
                seen.sell_quantity + row.sell_quantity,
                seen.sell_value_krw + row.sell_value_krw,
                seen.costs_krw + row.costs_krw,
            )
        )
    return totals


def same_order(row: OrderRow, intent: Intent) -> bool:
    """의도와 내용(종목·방향·수량·지정가)이 같은 신규 주문 행. 정정·취소 행은 아니다."""
    return (
        row.original == NO_ORIGINAL
        and row.channel in _ORDER_CHANNELS
        and row.symbol == intent.symbol
        and row.side == intent.side
        and row.quantity == intent.quantity
        and row.price_krw == intent.limit_price_krw
        and row.trade_type in _LIMIT_TYPES
    )


def is_candidate(row: OrderRow, intent: Intent, sent_at: datetime, margin: timedelta) -> bool:
    """응답을 잃은 의도의 자동 재식별 후보. 주문 시각이 전송 시각 ± 여유 안이어야 한다. 뒤쪽
    경계가 없으면 한참 뒤에 낸 같은 내용의 수동 주문도 묶였다(2026-10-22 리뷰 #593). 이미 다른
    의도에 묶인 번호는 호출자가 뺀다."""
    earliest, latest = sent_at - margin, sent_at + margin
    earliest_text = (
        "00:00:00" if earliest.date() < sent_at.date() else earliest.strftime("%H:%M:%S")
    )
    latest_text = "23:59:59" if latest.date() > sent_at.date() else latest.strftime("%H:%M:%S")
    return same_order(row, intent) and earliest_text <= row.time <= latest_text


@dataclass(frozen=True)
class Observation:
    state: OrderState
    filled_quantity: int


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
    # 체결단가는 반올림한 평균이라 금액에 쓰지 않는다. 금액은 split_day_amounts가 정한다.
    return Observation(state, filled)


@dataclass(frozen=True)
class Participant:
    """종목 하나의 하루 합계를 나눠 받을 오늘 주문."""

    key: str
    side: str
    filled_quantity: int


def split_day_amounts(
    day: DayTrade,
    settled: list[OrderFact],
    pending: list[Participant],
    untracked: list[Participant],
) -> dict[str, tuple[int, int]]:
    """하루 합계에서 확정된 주문 몫을 빼고 남은 체결금액·비용을 진행 중인 주문 한 건에 준다.

    확정된 주문은 마지막 관측 값을 그대로 둔다. 체결이 있는 진행 중 주문이 둘 이상이거나 이 어댑터가
    보내지 않은 주문의 체결이 섞이면 나눌 근거가 없어 멈춘다. 반환은 진행 중 주문마다
    (금액, 비용)이다.
    """
    identity = f"symbol={day.symbol}"
    journal = {
        "buy": (day.buy_quantity, day.buy_value_krw),
        "sell": (day.sell_quantity, day.sell_value_krw),
    }
    known_value = {"buy": 0, "sell": 0}
    quantity = {"buy": 0, "sell": 0}
    known_costs = 0
    for fact in settled:
        known_value[fact.intent.side] += fact.filled_value_krw
        quantity[fact.intent.side] += fact.filled_quantity
        known_costs += fact.fees_krw
    for participant in (*pending, *untracked):
        quantity[participant.side] += participant.filled_quantity
    for side, (journal_quantity, _) in journal.items():
        if journal_quantity != quantity[side]:
            raise JournalNotReady(
                f"kiwoom day journal and orders disagree — {identity} side={side} "
                f"journal_quantity={journal_quantity} order_quantity={quantity[side]}"
            )
    amounts = {participant.key: (0, 0) for participant in pending}
    filled = [participant for participant in pending if participant.filled_quantity]
    if any(participant.filled_quantity for participant in untracked):
        if filled:
            raise ContractViolation(
                f"fills of an order this adapter did not send share the symbol — {identity}"
            )
        return amounts
    if len(filled) > 1:
        raise ContractViolation(
            f"several open orders with fills share the symbol — {identity} "
            f"intents={[participant.key for participant in filled]}"
        )
    rest = {side: value - known_value[side] for side, (_, value) in journal.items()}
    rest_costs = day.costs_krw - known_costs
    if filled:
        owner = filled[0]
        other = "sell" if owner.side == "buy" else "buy"
        if rest[owner.side] <= 0 or rest[other] or rest_costs < 0:
            raise ContractViolation(
                f"day journal leaves no valid remainder — {identity} intent={owner.key} "
                f"rest={rest} rest_costs={rest_costs}"
            )
        amounts[owner.key] = (rest[owner.side], rest_costs)
    elif rest["buy"] or rest["sell"] or rest_costs:
        raise ContractViolation(
            f"day journal differs from settled orders — {identity} rest={rest} "
            f"rest_costs={rest_costs}"
        )
    return amounts


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


def holdings_of(rows: list[dict[str, object]]) -> tuple[tuple[str, int], ...]:
    """`kt00018` 보유. 같은 종목이 신용구분별로 여러 줄일 수 있어 합친다."""
    totals: dict[str, int] = {}
    for row in rows:
        quantity = number(row, "rmnd_qty")
        if quantity:
            code = symbol(text(row, "stk_cd"))
            totals[code] = totals.get(code, 0) + quantity
    return tuple(sorted(totals.items()))


def cash_of(body: Mapping[str, object]) -> int:
    """`kt00001` 현금. D+2 추정예수금(`d2_entra`)을 S05 현금으로 본다 — 2026-10-20 모의 실측에서
    체결 즉시 움직인 칸이다(`entr`는 결제 전이라 그대로였다)."""
    return number(body, "d2_entra")
