"""완료된 실행의 보유 종목·매매 기록을 화면이 읽는 모양으로 묶는 순수 투영.

입력은 엔진 원장 그대로다: 세션 끝 계좌 기록(`snapshots`)과 그 세션의 보유(`positions`, 수량
0 은 행이 없다), 체결(`fills`), 청산 거래(`trades`), 비용(`costs`). 금액·손익은 원장 값을 옮기거나
같은 세션 값끼리 나누기만 하고 엔진 규칙을 다시 계산하지 않는다.

매매 구분은 그 세션 그 종목의 체결 순수량(매수 − 매도)과 앞 세션·이 세션 끝 보유로 정한다. 체결
없이 수량이 바뀐 세션(분할·병합)은 매매가 아니라 `held` 이고, 체결 없이 장부에서 빠진 종목은
`removed` 다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from ._models import BacktestRunResult, RawFill, RawPosition, RawTrade, RunCostKind


@dataclass(frozen=True)
class SecurityName:
    """종목 디렉터리가 답한 표시 이름. 디렉터리가 모르는 종목은 이름이 없다."""

    ticker: str
    name: str


@dataclass(frozen=True)
class SecurityLabel:
    security_id: str
    ticker: str | None
    name: str | None


class HoldingChange(StrEnum):
    """한 세션에서 한 종목의 보유가 어떻게 바뀌었나."""

    OPENED = "opened"  # 앞 세션에 없었거나 방향이 바뀌어 새로 잡은 보유
    ADDED = "added"  # 같은 방향으로 체결해 수량을 늘림
    REDUCED = "reduced"  # 반대 방향으로 체결해 수량을 줄임(남은 보유 있음)
    HELD = "held"  # 그 세션 체결 순수량이 없음
    SOLD = "sold"  # 체결로 보유가 0이 됨
    REMOVED = "removed"  # 체결 없이 장부에서 빠짐(상장폐지 정산 등)


@dataclass(frozen=True)
class HoldingRow:
    security: SecurityLabel
    quantity: str
    market_value: float
    # 그 세션 끝 평가 자산 대비. 자산이 0 이하면 비중을 정의하지 않는다.
    weight: float | None
    # 원장 평가손익 ÷ (|수량| × 평균단가). 평균단가가 0 이하면 없다.
    unrealized_return: float | None
    change: HoldingChange
    # 그 세션 체결 순수량(부호 있음). 체결이 없으면 None.
    filled_quantity: str | None


@dataclass(frozen=True)
class ClosedHolding:
    security: SecurityLabel
    change: HoldingChange  # SOLD 또는 REMOVED
    previous_quantity: str
    # 그 세션에 청산된 거래(`trades.closed_on`)의 비용 후 손익 합. 청산 거래가 없으면 None.
    realized_pnl: float | None


@dataclass(frozen=True)
class SessionTradeTotals:
    buy_count: int
    buy_notional: float
    sell_count: int
    sell_notional: float
    fees: float
    # 그 세션 매도 거래세 비용 기록의 합. 지표 `total_taxes` 와 같은 원천이다.
    taxes: float


@dataclass(frozen=True)
class SessionHoldings:
    session: date
    equity: float
    cash: float
    cash_weight: float | None
    holdings: tuple[HoldingRow, ...]
    closed: tuple[ClosedHolding, ...]
    totals: SessionTradeTotals


class SessionHoldingsStatus(StrEnum):
    AVAILABLE = "available"
    SESSION_NOT_RECORDED = "session_not_recorded"


@dataclass(frozen=True)
class SessionHoldingsReport:
    run_id: str
    session: date
    status: SessionHoldingsStatus
    holdings: SessionHoldings | None
    detail: str | None

    def __post_init__(self) -> None:
        available = self.status is SessionHoldingsStatus.AVAILABLE
        if available != (self.holdings is not None) or available == bool(self.detail):
            raise ValueError(
                "세션 보유 보고서의 상태·내용·사유 계약을 위반했습니다: "
                f"run_id={self.run_id} session={self.session} status={self.status!r} "
                f"has_holdings={self.holdings is not None} detail={self.detail!r}"
            )


@dataclass(frozen=True)
class TradeSessionSummary:
    session: date
    holding_count: int
    opened_count: int
    closed_count: int
    fill_count: int


@dataclass(frozen=True)
class HoldingPeriod:
    """계좌 기록 세션에서 끊김 없이 들고 있던 구간(양끝 포함)."""

    start: date
    end: date
    open: bool  # 마지막 계좌 기록까지 들고 있었다


@dataclass(frozen=True)
class SecurityTradeSummary:
    security: SecurityLabel
    holding_periods: tuple[HoldingPeriod, ...]
    fill_count: int
    realized_pnl: float
    final_quantity: str | None
    final_unrealized_return: float | None


@dataclass(frozen=True)
class HoldingsOverview:
    run_id: str
    first_session: date | None
    last_session: date | None
    fill_count: int
    final_holding_count: int
    trade_sessions: tuple[TradeSessionSummary, ...]
    securities: tuple[SecurityTradeSummary, ...]


@dataclass(frozen=True)
class SecurityFillRow:
    fill_id: str
    side: str
    quantity: str
    price: float
    notional: float
    fee: float
    sell_tax: float | None


@dataclass(frozen=True)
class SecurityTradeSession:
    session: date
    change: HoldingChange
    quantity_after: str | None
    fills: tuple[SecurityFillRow, ...]
    realized_pnl: float | None


class SecurityTradesStatus(StrEnum):
    AVAILABLE = "available"
    SECURITY_NOT_TRADED = "security_not_traded"


@dataclass(frozen=True)
class SecurityTradesReport:
    run_id: str
    security: SecurityLabel
    status: SecurityTradesStatus
    sessions: tuple[SecurityTradeSession, ...]
    detail: str | None

    def __post_init__(self) -> None:
        available = self.status is SecurityTradesStatus.AVAILABLE
        if available == bool(self.detail) or (not available and self.sessions):
            raise ValueError(
                "종목 매매 보고서의 상태·내용·사유 계약을 위반했습니다: "
                f"run_id={self.run_id} security_id={self.security.security_id} "
                f"status={self.status!r} sessions={len(self.sessions)} detail={self.detail!r}"
            )


def result_security_ids(result: BacktestRunResult) -> frozenset[str]:
    """보유·체결에 한 번이라도 나온 종목. 이름을 찾을 대상이다."""
    artifacts = result.artifacts
    return frozenset(
        [item.security_id for item in artifacts.positions]
        + [item.security_id for item in artifacts.fills]
    )


class _Ledger:
    """원장을 세션·종목으로 한 번 색인한다. 세션 순서는 계좌 기록 순서다."""

    def __init__(self, result: BacktestRunResult, names: Mapping[str, SecurityName]) -> None:
        artifacts = result.artifacts
        self.run_id = result.manifest.run_id
        self.snapshots = {item.session: item for item in artifacts.snapshots}
        self.sessions = tuple(item.session for item in artifacts.snapshots)
        self.index = {session: index for index, session in enumerate(self.sessions)}
        self.positions: dict[date, dict[str, RawPosition]] = defaultdict(dict)
        for position in artifacts.positions:
            if position.session not in self.index:
                raise ValueError(
                    "보유 기록의 세션에 계좌 기록이 없습니다: "
                    f"run_id={self.run_id} session={position.session} "
                    f"security_id={position.security_id}"
                )
            self.positions[position.session][position.security_id] = position
        self.fills: dict[date, dict[str, list[RawFill]]] = defaultdict(lambda: defaultdict(list))
        for fill in artifacts.fills:
            self.fills[fill.session][fill.security_id].append(fill)
        self.realized: dict[tuple[date, str], float] = defaultdict(float)
        self.closing: set[tuple[date, str]] = set()
        for trade in artifacts.trades:
            self._add_trade(trade)
        self.taxes: dict[date, float] = defaultdict(float)
        for cost in artifacts.costs:
            if cost.kind == RunCostKind.SELL_TAX:
                self.taxes[cost.session] += cost.amount
        self.names = names
        self.fill_count = len(artifacts.fills)

    def _add_trade(self, trade: RawTrade) -> None:
        key = (trade.closed_on, trade.security_id)
        self.realized[key] += trade.pnl
        self.closing.add(key)

    def label(self, security_id: str) -> SecurityLabel:
        known = self.names.get(security_id)
        return SecurityLabel(
            security_id=security_id,
            ticker=None if known is None else known.ticker,
            name=None if known is None else known.name,
        )

    def previous(self, session: date) -> dict[str, RawPosition]:
        index = self.index[session]
        return {} if index == 0 else self.positions.get(self.sessions[index - 1], {})

    def realized_pnl(self, session: date, security_id: str) -> float | None:
        key = (session, security_id)
        return self.realized[key] if key in self.closing else None

    def change(self, session: date, security_id: str) -> HoldingChange:
        before = self.previous(session).get(security_id)
        after = self.positions.get(session, {}).get(security_id)
        fills = self.fills.get(session, {}).get(security_id, [])
        if after is None:
            if before is None:
                # 같은 세션에 사서 다 판 경우. 보유 표에는 나오지 않고 종목 기록에만 나온다.
                return HoldingChange.SOLD if fills else HoldingChange.HELD
            return HoldingChange.SOLD if fills else HoldingChange.REMOVED
        after_quantity = Decimal(after.quantity)
        if before is None or (Decimal(before.quantity) > 0) != (after_quantity > 0):
            return HoldingChange.OPENED
        net = _net_filled(fills)
        if net is None or net == 0:
            return HoldingChange.HELD
        return HoldingChange.ADDED if (net > 0) == (after_quantity > 0) else HoldingChange.REDUCED


def _net_filled(fills: Collection[RawFill]) -> Decimal | None:
    if not fills:
        return None
    return sum(
        (Decimal(fill.quantity) * (1 if fill.side == "buy" else -1) for fill in fills),
        Decimal(0),
    )


def _quantity_text(quantity: Decimal) -> str:
    # trades 와 같은 표기(지수 표기 금지, #135).
    return format(quantity.normalize(), "f") if quantity != 0 else "0"


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator > 0 else None


def project_session_holdings(
    result: BacktestRunResult, session: date, names: Mapping[str, SecurityName]
) -> SessionHoldingsReport:
    """한 세션 끝 보유와 그 세션의 매매. 계좌 기록이 없는 날은 사유로 답한다."""
    ledger = _Ledger(result, names)
    if session not in ledger.index:
        return SessionHoldingsReport(
            run_id=ledger.run_id,
            session=session,
            status=SessionHoldingsStatus.SESSION_NOT_RECORDED,
            holdings=None,
            detail=(
                "그날은 이 실행의 계좌 기록이 없습니다(거래일이 아니거나 실행 기간 밖). "
                f"session={session} first={ledger.sessions[0] if ledger.sessions else None} "
                f"last={ledger.sessions[-1] if ledger.sessions else None}"
            ),
        )
    return SessionHoldingsReport(
        run_id=ledger.run_id,
        session=session,
        status=SessionHoldingsStatus.AVAILABLE,
        holdings=_session_holdings(ledger, session),
        detail=None,
    )


def _session_holdings(ledger: _Ledger, session: date) -> SessionHoldings:
    snapshot = ledger.snapshots[session]
    current = ledger.positions.get(session, {})
    fills = ledger.fills.get(session, {})
    rows = [
        HoldingRow(
            security=ledger.label(security_id),
            quantity=position.quantity,
            market_value=position.market_value,
            weight=_ratio(position.market_value, snapshot.equity),
            unrealized_return=_ratio(
                position.unrealized_pnl, abs(float(position.quantity)) * position.average_price
            ),
            change=ledger.change(session, security_id),
            filled_quantity=(
                None
                if (net := _net_filled(fills.get(security_id, []))) is None
                else _quantity_text(net)
            ),
        )
        for security_id, position in current.items()
    ]
    rows.sort(key=lambda row: (-abs(row.market_value), row.security.security_id))
    closed = tuple(
        ClosedHolding(
            security=ledger.label(security_id),
            change=ledger.change(session, security_id),
            previous_quantity=position.quantity,
            realized_pnl=ledger.realized_pnl(session, security_id),
        )
        for security_id, position in sorted(ledger.previous(session).items())
        if security_id not in current
    )
    all_fills = [fill for items in fills.values() for fill in items]
    buys = [fill for fill in all_fills if fill.side == "buy"]
    sells = [fill for fill in all_fills if fill.side != "buy"]
    return SessionHoldings(
        session=session,
        equity=snapshot.equity,
        cash=snapshot.cash,
        cash_weight=_ratio(snapshot.cash, snapshot.equity),
        holdings=tuple(rows),
        closed=closed,
        totals=SessionTradeTotals(
            buy_count=len(buys),
            buy_notional=sum(float(fill.quantity) * fill.price for fill in buys),
            sell_count=len(sells),
            sell_notional=sum(float(fill.quantity) * fill.price for fill in sells),
            fees=sum(fill.fee for fill in all_fills),
            taxes=ledger.taxes.get(session, 0.0),
        ),
    )


def project_holdings_overview(
    result: BacktestRunResult, names: Mapping[str, SecurityName]
) -> HoldingsOverview:
    """매매한 세션 목록과 종목별 보유 구간·손익 요약."""
    ledger = _Ledger(result, names)
    trade_sessions = tuple(
        TradeSessionSummary(
            session=session,
            holding_count=len(ledger.positions.get(session, {})),
            opened_count=sum(
                1
                for security_id in ledger.positions.get(session, {})
                if ledger.change(session, security_id) is HoldingChange.OPENED
            ),
            closed_count=sum(
                1
                for security_id in ledger.previous(session)
                if security_id not in ledger.positions.get(session, {})
            ),
            fill_count=sum(len(items) for items in ledger.fills[session].values()),
        )
        for session in ledger.sessions
        if session in ledger.fills
    )
    held: dict[str, list[int]] = defaultdict(list)
    for session, positions in ledger.positions.items():
        for security_id in positions:
            held[security_id].append(ledger.index[session])
    fill_counts: dict[str, int] = defaultdict(int)
    first_seen: dict[str, date] = {}
    for fill in result.artifacts.fills:
        fill_counts[fill.security_id] += 1
        first_seen.setdefault(fill.security_id, fill.session)
    realized: dict[str, float] = defaultdict(float)
    for trade in result.artifacts.trades:
        realized[trade.security_id] += trade.pnl
    last = ledger.sessions[-1] if ledger.sessions else None
    final = ledger.positions.get(last, {}) if last is not None else {}
    security_ids = set(held) | set(fill_counts)
    securities = tuple(
        SecurityTradeSummary(
            security=ledger.label(security_id),
            holding_periods=_periods(sorted(held.get(security_id, [])), ledger.sessions),
            fill_count=fill_counts.get(security_id, 0),
            realized_pnl=realized.get(security_id, 0.0),
            final_quantity=final[security_id].quantity if security_id in final else None,
            final_unrealized_return=(
                _ratio(
                    final[security_id].unrealized_pnl,
                    abs(float(final[security_id].quantity)) * final[security_id].average_price,
                )
                if security_id in final
                else None
            ),
        )
        for security_id in sorted(
            security_ids,
            key=lambda item: (
                first_seen.get(item) or ledger.sessions[min(held[item])],
                item,
            ),
        )
    )
    return HoldingsOverview(
        run_id=ledger.run_id,
        first_session=ledger.sessions[0] if ledger.sessions else None,
        last_session=last,
        fill_count=ledger.fill_count,
        final_holding_count=len(final),
        trade_sessions=trade_sessions,
        securities=securities,
    )


def _periods(indices: list[int], sessions: tuple[date, ...]) -> tuple[HoldingPeriod, ...]:
    periods: list[HoldingPeriod] = []
    start = 0
    for position in range(1, len(indices) + 1):
        if position == len(indices) or indices[position] != indices[position - 1] + 1:
            end = indices[position - 1]
            periods.append(
                HoldingPeriod(
                    start=sessions[indices[start]],
                    end=sessions[end],
                    open=end == len(sessions) - 1,
                )
            )
            start = position
    return tuple(periods)


def project_security_trades(
    result: BacktestRunResult, security_id: str, names: Mapping[str, SecurityName]
) -> SecurityTradesReport:
    """한 종목의 매매한 세션별 체결과 그 세션 매매 구분·실현 손익."""
    ledger = _Ledger(result, names)
    sessions = tuple(
        SecurityTradeSession(
            session=session,
            change=ledger.change(session, security_id),
            quantity_after=(
                position.quantity
                if (position := ledger.positions.get(session, {}).get(security_id)) is not None
                else None
            ),
            fills=tuple(
                SecurityFillRow(
                    fill_id=fill.fill_id,
                    side=fill.side,
                    quantity=fill.quantity,
                    price=fill.price,
                    notional=float(fill.quantity) * fill.price,
                    fee=fill.fee,
                    sell_tax=fill.sell_tax,
                )
                for fill in ledger.fills[session][security_id]
            ),
            realized_pnl=ledger.realized_pnl(session, security_id),
        )
        for session in ledger.sessions
        if security_id in ledger.fills.get(session, {})
    )
    if not sessions:
        return SecurityTradesReport(
            run_id=ledger.run_id,
            security=ledger.label(security_id),
            status=SecurityTradesStatus.SECURITY_NOT_TRADED,
            sessions=(),
            detail=f"이 실행에서 체결된 적 없는 종목입니다. security_id={security_id}",
        )
    return SecurityTradesReport(
        run_id=ledger.run_id,
        security=ledger.label(security_id),
        status=SecurityTradesStatus.AVAILABLE,
        sessions=sessions,
        detail=None,
    )
