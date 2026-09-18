"""`TargetTapeStrategy` 어댑터 경계 — 판정은 `evaluate_tape`에 위임한다.

bar 없는 종목 처리(거래정지·기준가 세션)를 포함한 tape 판정 규칙 자체는 `tests/test_tape.py`가
단일 정본으로 고정한다. 여기서는 어댑터가 커널에 그대로 넘기는지, 그리고 어댑터만 얹는
`target_tape:<signal iso>` reason 접두어와 `idle_reason`이 맞는지만 본다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from backtest_engine.engine.tape import evaluate_tape
from backtest_engine.types.decision import StrategyDecision
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


def test_adapter_delegates_to_evaluate_tape_and_only_adds_its_reasons() -> None:
    ts = datetime.combine(SIGNAL, time(15, 30))
    instrument = make_instrument("000660:1")
    snapshot = MarketSnapshot(ts=ts, bars=(make_bar(ts, instrument, 100.0, 101.0),))
    # 보유 종목 하나는 bar 가 없다 — 커널을 거쳤는지 결정 내용으로 드러난다.
    strategy = _strategy(_target("000660:1", 0.4), _target("005930:1", 0.4))
    ctx = _Context(now=ts, positions={"005930:1": Decimal(12)})

    frames = strategy.tape_frames()
    assert [frame.reason for frame in frames.values()] == [f"target_tape:{SIGNAL.isoformat()}"]
    assert strategy.on_event(ctx, snapshot) == evaluate_tape(
        frames, strategy.idle_reason, ctx, snapshot
    )

    # 프레임이 없는 세션은 어댑터의 idle_reason 으로 NoAction 이 된다.
    idle_ts = datetime.combine(date(2018, 5, 2), time(15, 30))
    idle = strategy.on_event(
        _Context(now=idle_ts, positions={}),
        MarketSnapshot(ts=idle_ts, bars=(make_bar(idle_ts, instrument, 100.0, 101.0),)),
    )
    assert idle == StrategyDecision.no_action(idle_ts, strategy.idle_reason)
