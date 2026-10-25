"""청산 거래(`RawArtifactBundle.trades`) 수량 직렬화 — issue #135 회귀 방지.

`_closed_trades`는 fill 수량을 float으로 계산해 `Decimal(str(700.0)).normalize()` → `7E+2`가 됐고
`str()`이 지수 표기를 냈다. 같은 번들의 fills·positions는 "700"이라 한 응답 안에서 표현이 갈렸다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from backtest_engine.types.actions import ExecutionPolicy, QuantityTarget, SetPositionTarget
from backtest_engine.types.instruments import AssetClass, InstrumentId
from backtest_engine.types.result_tables import FillTotals, ResultTables
from strategy_workbench.adapters.outbound.backtest_engine._adapter import _artifacts
from tests.rust_core_guard import RUST_CORE
from tests.test_corporate_action_engine import INSTRUMENT, bars_with_split, buy, run, split

_SAMSUNG = InstrumentId(venue="KRX", symbol="005930", asset_class=AssetClass.EQUITY, currency="KRW")


def _tables(
    *fills: tuple[str, str, int, int, str, int, float, float, float, int, float],
    costs: tuple[tuple[int, str, int | None, float], ...] = (),
) -> ResultTables:
    sessions = tuple(datetime(2026, 1, day, tzinfo=UTC) for day in (5, 6, 7))
    return ResultTables(
        sessions=sessions,
        instruments=(_SAMSUNG,),
        snapshots=(),
        positions=(),
        orders=(),
        fills=fills,
        costs=costs,
        roundings=(),
        fill_totals=FillTotals(traded_notional=0.0, total_fees=0.0, total_slippage_cost=0.0),
    )


def _quantities(tables: ResultTables) -> list[str]:
    artifacts, _ = _artifacts(tables, actions=())
    return [trade.quantity for trade in artifacts.trades]


def test_closed_trade_quantity_is_a_plain_integer_string_like_fills() -> None:
    # 700주 매수 → 전량 청산: 예전에는 "7E+2".
    tables = _tables(
        ("f1", "o1", 0, 0, "buy", 700, 100.0, 0.0, 0.0, 10_000, 0.0),
        ("f2", "o2", 1, 0, "sell", 700, 110.0, 0.0, 0.0, 10_000, 0.0),
    )
    assert _quantities(tables) == ["700"]


def test_closed_trade_quantity_keeps_partial_exits_and_round_numbers_plain() -> None:
    # 1000주 중 100주 → 900주 순서로 청산: 둘 다 정수 문자열이고 10의 거듭제곱도 지수 표기가 아니다.
    tables = _tables(
        ("f1", "o1", 0, 0, "buy", 1000, 100.0, 0.0, 0.0, 10_000, 0.0),
        ("f2", "o2", 1, 0, "sell", 100, 110.0, 0.0, 0.0, 10_000, 0.0),
        ("f3", "o3", 2, 0, "sell", 900, 120.0, 0.0, 0.0, 10_000, 0.0),
    )
    assert _quantities(tables) == ["100", "900"]
    for quantity in _quantities(tables):
        assert quantity.isdigit(), quantity


def test_closed_trade_costs_include_the_sell_tax_recorded_after_each_sell() -> None:
    """매도 거래세(V2-01)는 체결의 `fee` 가 아니라 비용 기록이다. 거래 단위 손익은 그 세금까지 뺀다.

    100주 @100 매수 → 같은 세션에 40주 @110, 60주 @120 매도. 세금은 체결 순서대로 8.8, 14.4.
    """
    tables = _tables(
        ("f1", "o1", 0, 0, "buy", 100, 100.0, 0.0, 0.0, 10_000, 0.0),
        ("f2", "o2", 1, 0, "sell", 40, 110.0, 1.0, 0.0, 10_000, 8.8),
        ("f3", "o3", 1, 0, "sell", 60, 120.0, 1.0, 0.0, 10_000, 14.4),
        costs=((1, "sell_tax", 0, 8.8), (1, "sell_tax", 0, 14.4), (2, "short_borrow", 0, 5.0)),
    )
    artifacts, _ = _artifacts(tables, actions=())
    trades = artifacts.trades

    assert [(trade.fees, trade.pnl) for trade in trades] == [
        (pytest.approx(9.8), pytest.approx(400.0 - 9.8)),
        (pytest.approx(15.4), pytest.approx(1200.0 - 15.4)),
    ]


@pytest.mark.parametrize(
    "core", ["python", pytest.param("rust", marks=RUST_CORE)], ids=["python", "rust"]
)
def test_trades_after_a_real_engine_split_match_the_cash_actually_gained(core: str) -> None:
    """분할 뒤 매도의 실현 손익이 실제 현금 증감과 같다(두 코어).

    7주를 100원에 사고(D2), 1.5배 분할로 10.5주 → 10주 + 0.5주는 시가 60원 현금 정산(D3), 전량을
    D4 시가 70원에 판다. 현금은 10,000 → 10,030 이다. 분할을 모르던 재구성은 7주를 70원에 닫은
    −210 과 숏 3주를 남겼다.
    """
    sell_all = SetPositionTarget(
        target=QuantityTarget(INSTRUMENT, Decimal(0)),
        execution=ExecutionPolicy.market_next_open(),
    )
    engine, _, _ = run(
        bars_with_split(60.0, 70.0), (split("1.5"),), script=(buy(7), None, sell_all), core=core
    )
    # Rust 저장소는 읽은 레코드를 놓으므로 실행 어댑터와 같은 순서(결과 테이블 → 적용 기록)로
    # 읽는다.
    artifacts, outcomes = _artifacts(
        engine.event_store.result_tables(),
        actions=engine.event_store.corporate_actions_applied(),
    )
    last = artifacts.snapshots[-1]
    assert not [item for item in artifacts.positions if item.session == last.session]

    assert sum(trade.pnl for trade in artifacts.trades) == pytest.approx(last.cash - 10_000.0)
    assert {trade.side for trade in artifacts.trades} == {"long"}
    assert [
        (item.ratio, item.old_quantity, item.new_quantity, item.settled_quantity)
        for item in artifacts.corporate_actions or ()
    ] == [("1.5", "7", "10", "0.5")]
    assert len(outcomes) == len(artifacts.trades)


@pytest.mark.parametrize(
    "core", ["python", pytest.param("rust", marks=RUST_CORE)], ids=["python", "rust"]
)
@pytest.mark.parametrize(
    ("held", "new_quantity", "settled"), [(3, "1", "0"), (7, "2", "0.333333333")]
)
def test_repeating_decimal_reverse_split_settles_exactly_what_the_engine_settled(
    core: str, held: int, new_quantity: str, settled: str
) -> None:
    """원장 1:3 병합 비율(float 문자열 0.3333…3). 단주는 엔진의 1e-9 반올림 규칙으로 정한다.

    3주는 정확히 1주라 단주 거래가 없어야 한다 — 비율을 다시 곱하면 0.000…1주 가짜 거래가 생겼다.
    """
    sell_all = SetPositionTarget(
        target=QuantityTarget(INSTRUMENT, Decimal(0)),
        execution=ExecutionPolicy.market_next_open(),
    )
    engine, _, _ = run(
        bars_with_split(60.0, 70.0),
        (split("0.3333333333333333"),),
        script=(buy(held), None, sell_all),
        core=core,
    )
    artifacts, _ = _artifacts(
        engine.event_store.result_tables(),
        actions=engine.event_store.corporate_actions_applied(),
    )

    (action,) = artifacts.corporate_actions or ()
    assert (action.new_quantity, action.settled_quantity) == (new_quantity, settled)
    # 단주가 없으면 청산 거래는 남은 수량을 판 한 건뿐이다.
    expected = 1 if settled == "0" else 2
    assert len(artifacts.trades) == expected
    assert artifacts.trades[-1].quantity == new_quantity
