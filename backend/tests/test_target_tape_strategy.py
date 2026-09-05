"""`TargetTapeStrategy` — 프레임 목표 중 이 세션에 bar 가 없는 종목(거래정지·기준가 세션) 처리.

커널은 비중 목표를 그 세션 종가로 수량화하므로(`router._notional_to_delta`) bar 없는 종목의
WeightTarget 은 `InstrumentNotInSnapshot` 으로 run 을 죽인다. equity 피드는 기준가 행을 내지 않으니
(S07·S21) 실제 KRX 데이터에서는 흔한 일이다 — 보유 중이면 수량 유지, 미보유면 건너뛴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from backtest_engine.types.actions import QuantityTarget, SetPortfolioTarget, WeightTarget
from backtest_engine.types.events import OpenOrderSnapshot
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import MarketSnapshot, PriceWindow
from backtest_engine.types.requirements import HistoryRequest
from strategy_workbench.adapters.outbound.backtest_engine._adapter import TargetTapeStrategy
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.portfolio.facade.construction import (
    CandidateSide,
    TargetFrame,
    TargetPosition,
    TargetTape,
)
from tests.conftest import make_bar, make_instrument

SIGNAL, EXECUTION = date(2018, 4, 27), date(2018, 4, 30)


@dataclass(frozen=True)
class _Context:
    now: datetime
    positions: dict[str, Decimal]

    def history(self, request: HistoryRequest) -> PriceWindow:
        raise NotImplementedError("target tape strategy declares no history")

    def current_weight(self, instrument: InstrumentId) -> float:
        return 0.0

    def position_qty(self, instrument: InstrumentId) -> Decimal:
        return self.positions.get(instrument.symbol, Decimal(0))

    def cash(self) -> float:
        return 0.0

    def portfolio_value(self) -> float:
        return 0.0

    def universe(self) -> frozenset[InstrumentId]:
        return frozenset()

    def open_orders(self, instrument: InstrumentId | None = None) -> tuple[OpenOrderSnapshot, ...]:
        return ()


def _target(security_id: str, weight: float) -> TargetPosition:
    return TargetPosition(security_id, weight, 1.0, 1, CandidateSide.LONG)


def _strategy(*targets: TargetPosition) -> TargetTapeStrategy:
    spec = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "x", today=lambda: EXECUTION
    ).template()
    frame = TargetFrame(signal_as_of=SIGNAL, execution_on=EXECUTION, targets=targets, candidates=())
    tape = TargetTape(data_snapshot_id="snap", strategy_hash="h", tape_hash="t", frames=(frame,))
    return TargetTapeStrategy(spec, tape, BacktestEnginePortfolioAdapter())


def test_targets_without_a_bar_are_held_when_owned_and_skipped_otherwise() -> None:
    ts = datetime.combine(SIGNAL, time(15, 30))
    snapshot = MarketSnapshot(
        ts=ts, bars=(make_bar(ts, make_instrument("000660:1"), 100.0, 101.0),)
    )
    strategy = _strategy(
        _target("000660:1", 0.4), _target("005930:1", 0.4), _target("000030:1", 0.2)
    )

    decision = strategy.on_event(
        _Context(now=ts, positions={"005930:1": Decimal(12)}), snapshot
    )

    action = decision.actions[0]
    assert isinstance(action, SetPortfolioTarget)
    assert action.targets == (
        WeightTarget(make_instrument("000660:1"), 0.4),
        QuantityTarget(make_instrument("005930:1"), Decimal(12)),  # 보유 중 → 수량 유지
    )  # 000030:1 은 미보유 + bar 없음 → 건너뜀 (예산은 현금에 남는다)
    assert decision.reason == "target_tape:2018-04-27 no_bar=('005930:1', '000030:1')"


def test_frames_with_every_instrument_priced_pass_through_unchanged() -> None:
    ts = datetime.combine(SIGNAL, time(15, 30))
    instrument = make_instrument("000660:1")
    snapshot = MarketSnapshot(ts=ts, bars=(make_bar(ts, instrument, 100.0, 101.0),))

    decision = _strategy(_target("000660:1", 0.7)).on_event(
        _Context(now=ts, positions={}), snapshot
    )

    action = decision.actions[0]
    assert isinstance(action, SetPortfolioTarget)
    assert action.targets == (WeightTarget(instrument, 0.7),)
    assert decision.reason == "target_tape:2018-04-27"
