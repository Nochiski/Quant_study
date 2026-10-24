"""보유·매매 투영을 손으로 만든 작은 원장과 비교한다.

시나리오(세션 1/2·3·4·5·8·9):
- 2일: A 10주·B 5주 신규.
- 3일: A 5주 추가, B 2주 일부 매도(청산 거래 +30).
- 4일: A 체결 없이 분할로 15 → 30주, B 남은 3주 전량 매도(청산 거래 −10).
- 5일: A 체결 없이 장부에서 빠짐, D 4주 신규.
- 8일: B 2주 다시 진입, D 10주 매도해 숏 6주로 방향 전환(청산 거래 +8).
- 9일: 체결 없음.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.holdings import (
    HoldingChange,
    HoldingPeriod,
    SecurityName,
    SecurityTradesStatus,
    SessionHoldingsStatus,
    project_holdings_overview,
    project_security_trades,
    project_session_holdings,
    result_security_ids,
)
from strategy_workbench.domain.backtest.facade.runs import (
    BacktestRunResult,
    BacktestRunSpec,
    BacktestSeries,
    RawArtifactBundle,
    RawCost,
    RawFill,
    RawPosition,
    RawSnapshot,
    RawTrade,
    RunManifest,
    StrategyProvenance,
    StrategySourceKind,
)


def _day(day: int) -> date:
    return date(2024, 1, day)


def _position(day: int, security_id: str, quantity: int, value: float, pnl: float = 0.0):
    return RawPosition(
        session=_day(day),
        security_id=security_id,
        quantity=str(quantity),
        average_price=abs(value / quantity) - pnl / quantity,
        market_price=abs(value / quantity),
        market_value=value,
        unrealized_pnl=pnl,
    )


def _fill(day: int, security_id: str, side: str, quantity: int, price: float) -> RawFill:
    return RawFill(
        fill_id=f"f-{day}-{security_id}-{side}",
        order_id=f"o-{day}-{security_id}",
        session=_day(day),
        security_id=security_id,
        side=side,
        quantity=str(quantity),
        price=price,
        fee=1.0,
        slippage_per_share=0.0,
        sell_tax=0.5 if side == "sell" else 0.0,
    )


def _trade(opened: int, closed: int, security_id: str, pnl: float) -> RawTrade:
    return RawTrade(security_id, _day(opened), _day(closed), "long", "1", 1.0, 1.0, pnl, 0.0, 0.0)


SNAPSHOTS = (
    RawSnapshot(_day(2), 0.0, 2000.0, 1.0, 1.0),
    RawSnapshot(_day(3), 100.0, 2200.0, 1.0, 1.0),
    RawSnapshot(_day(4), 700.0, 2300.0, 1.0, 1.0),
    RawSnapshot(_day(5), 1900.0, 2300.0, 1.0, 1.0),
    RawSnapshot(_day(8), 2000.0, 0.0, 1.0, 1.0),
    RawSnapshot(_day(9), 2000.0, 2100.0, 1.0, 1.0),
)
POSITIONS = (
    _position(2, "A", 10, 1000.0),
    _position(2, "B", 5, 1000.0),
    _position(3, "A", 15, 1650.0, pnl=150.0),
    _position(3, "B", 3, 450.0, pnl=-150.0),
    _position(4, "A", 30, 1600.0),
    _position(5, "D", 4, 400.0),
    _position(8, "B", 2, 300.0),
    _position(8, "D", -6, -600.0),
    _position(9, "B", 2, 320.0, pnl=20.0),
    _position(9, "D", -6, -580.0, pnl=20.0),
)
FILLS = (
    _fill(2, "A", "buy", 10, 100.0),
    _fill(2, "B", "buy", 5, 200.0),
    _fill(3, "A", "buy", 5, 110.0),
    _fill(3, "B", "sell", 2, 150.0),
    _fill(4, "B", "sell", 3, 160.0),
    _fill(5, "D", "buy", 4, 100.0),
    _fill(8, "B", "buy", 2, 150.0),
    _fill(8, "D", "sell", 10, 100.0),
)
TRADES = (_trade(2, 3, "B", 30.0), _trade(2, 4, "B", -10.0), _trade(5, 8, "D", 8.0))
NAMES = {"A": SecurityName("000A", "에이"), "B": SecurityName("000B", "비")}


def _result() -> BacktestRunResult:
    created = datetime(2024, 1, 10, tzinfo=UTC)
    manifest = RunManifest(
        run_id="holdings-run",
        created_at=created,
        completed_at=created,
        engine_version="engine",
        run_fingerprint="fingerprint",
        run_spec=BacktestRunSpec(initial_cash=2000.0),
        strategy_hash="strategy-hash",
        data_snapshot_id="snapshot",
        target_tape_hash="tape",
        metric_registry_version="registry",
        environment=RunEnvironment(start=_day(2), end=_day(9), universe_id="test"),
        environment_hash="environment-hash",
        strategy_provenance=StrategyProvenance(
            StrategySourceKind.SAVED_REVISION, "strategy-hash", "1.2", "strategy", 1
        ),
        warnings=(),
        factor_evaluation_version=None,
    )
    costs = (
        RawCost(_day(3), "sell_tax", "B", 0.5),
        RawCost(_day(3), "fee", "B", 1.0),
    )
    return BacktestRunResult(
        manifest,
        (),
        (),
        BacktestSeries((), (), (), ()),
        RawArtifactBundle(SNAPSHOTS, POSITIONS, (), FILLS, costs, TRADES),
    )


def _session(day: int):
    report = project_session_holdings(_result(), _day(day), NAMES)
    assert report.status is SessionHoldingsStatus.AVAILABLE
    assert report.holdings is not None
    return report.holdings


def test_change_follows_net_fills_not_quantity_moves_from_corporate_actions() -> None:
    added = {row.security.security_id: row for row in _session(3).holdings}
    assert (added["A"].change, added["A"].filled_quantity) == (HoldingChange.ADDED, "5")
    assert (added["B"].change, added["B"].filled_quantity) == (HoldingChange.REDUCED, "-2")

    split_day = _session(4)
    assert [
        (row.security.security_id, row.change, row.filled_quantity) for row in split_day.holdings
    ] == [("A", HoldingChange.HELD, None)]
    assert [
        (item.security.security_id, item.change, item.realized_pnl) for item in split_day.closed
    ] == [("B", HoldingChange.SOLD, -10.0)]

    delisted = _session(5)
    assert [
        (item.security.security_id, item.change, item.realized_pnl) for item in delisted.closed
    ] == [("A", HoldingChange.REMOVED, None)]
    assert [row.change for row in delisted.holdings] == [HoldingChange.OPENED]


def test_reentry_and_direction_flip_are_new_holdings() -> None:
    changes = {row.security.security_id: row.change for row in _session(8).holdings}
    assert changes == {"B": HoldingChange.OPENED, "D": HoldingChange.OPENED}


def test_weights_divide_by_that_session_equity_and_sort_by_size() -> None:
    first = _session(2)
    assert [(row.security.security_id, row.weight) for row in first.holdings] == [
        ("A", 0.5),
        ("B", 0.5),
    ]
    assert first.cash_weight == 0.0
    third = _session(3)
    assert [row.security.security_id for row in third.holdings] == ["A", "B"]
    assert third.holdings[1].unrealized_return == -150.0 / 600.0
    # 자산이 0 이면 비중을 지어내지 않는다.
    flat = _session(8)
    assert {row.weight for row in flat.holdings} == {None}
    assert flat.cash_weight is None


def test_session_totals_read_fills_and_sell_tax_cost_records() -> None:
    totals = _session(3).totals
    assert (totals.buy_count, totals.buy_notional) == (1, 550.0)
    assert (totals.sell_count, totals.sell_notional) == (1, 300.0)
    assert (totals.fees, totals.taxes) == (2.0, 0.5)


def test_unrecorded_session_is_reported_not_guessed() -> None:
    report = project_session_holdings(_result(), _day(6), NAMES)
    assert report.status is SessionHoldingsStatus.SESSION_NOT_RECORDED
    assert report.holdings is None
    assert report.detail is not None and "session=2024-01-06" in report.detail


def test_overview_lists_trade_sessions_and_holding_periods_with_gaps() -> None:
    overview = project_holdings_overview(_result(), NAMES)
    assert [item.session for item in overview.trade_sessions] == [
        _day(2),
        _day(3),
        _day(4),
        _day(5),
        _day(8),
    ]
    by_session = {item.session: item for item in overview.trade_sessions}
    assert (by_session[_day(5)].opened_count, by_session[_day(5)].closed_count) == (1, 1)
    assert (by_session[_day(8)].opened_count, by_session[_day(8)].fill_count) == (2, 2)

    securities = {item.security.security_id: item for item in overview.securities}
    assert [item.security.security_id for item in overview.securities] == ["A", "B", "D"]
    assert securities["B"].holding_periods == (
        HoldingPeriod(_day(2), _day(3), open=False),
        HoldingPeriod(_day(8), _day(9), open=True),
    )
    assert securities["B"].realized_pnl == 20.0
    assert securities["B"].final_quantity == "2"
    assert securities["A"].final_quantity is None
    assert (overview.fill_count, overview.final_holding_count) == (8, 2)
    assert securities["D"].security.name is None
    assert securities["A"].security.name == "에이"


def test_security_trades_group_fills_by_session_with_realized_pnl_only_when_closed() -> None:
    report = project_security_trades(_result(), "B", NAMES)
    assert report.status is SecurityTradesStatus.AVAILABLE
    assert [
        (item.session, item.change, item.quantity_after, item.realized_pnl, len(item.fills))
        for item in report.sessions
    ] == [
        (_day(2), HoldingChange.OPENED, "5", None, 1),
        (_day(3), HoldingChange.REDUCED, "3", 30.0, 1),
        (_day(4), HoldingChange.SOLD, None, -10.0, 1),
        (_day(8), HoldingChange.OPENED, "2", None, 1),
    ]
    assert report.sessions[1].fills[0].notional == 300.0

    missing = project_security_trades(_result(), "Z", NAMES)
    assert missing.status is SecurityTradesStatus.SECURITY_NOT_TRADED
    assert missing.sessions == ()


def test_result_security_ids_cover_positions_and_fills() -> None:
    assert result_security_ids(_result()) == {"A", "B", "D"}
