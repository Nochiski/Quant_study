"""`TargetTapeStrategy` 어댑터 경계 — 판정은 `evaluate_tape`에 위임한다.

bar 없는 종목 처리(거래정지·기준가 세션)를 포함한 tape 판정 규칙 자체는 `tests/test_tape.py`가
단일 정본으로 고정한다. 여기서는 어댑터가 커널에 그대로 넘기는지, 그리고 어댑터만 얹는
`target_tape:<signal iso>` reason 접두어와 `idle_reason`이 맞는지만 본다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

import pytest

from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.tape import evaluate_tape
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import OpenOrderSnapshot
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import Bar, MarketSnapshot, PriceWindow
from backtest_engine.types.requirements import HistoryRequest
from backtest_engine.types.tape import DeclarativeTapeStrategy
from strategy_workbench.adapters.outbound.backtest_engine._adapter import (
    TargetTapeStrategy,
    _columnar_feed,
    _instrument,
)
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.backtest_run.facade.ports import MarketBarRecord
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.portfolio.facade.construction import (
    CandidateSide,
    TargetFrame,
    TargetPosition,
    TargetTape,
)
from tests.conftest import make_bar, make_instrument

SIGNAL, EXECUTION = date(2018, 4, 27), date(2018, 4, 30)
ENVIRONMENT = RunEnvironment(start=SIGNAL, end=EXECUTION, universe_id="krx.common-stock")


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
    spec = StrategyDesignService(InMemoryStrategyRepository(), new_id=lambda: "x").template()
    frame = TargetFrame(signal_as_of=SIGNAL, execution_on=EXECUTION, targets=targets, candidates=())
    tape = TargetTape(data_snapshot_id="snap", strategy_hash="h", tape_hash="t", frames=(frame,))
    return TargetTapeStrategy(
        spec,
        tape,
        BacktestEnginePortfolioAdapter(),
        environment=ENVIRONMENT,
    )


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


def test_adapter_strategy_opts_into_the_declarative_tape_path() -> None:
    """기반 클래스 선언 한 줄이 빠지면 결과는 같고 콜백 경로로만 내려가 테스트가 안 깨진다 —
    tape 경로 진입은 명시 상속이므로 여기서 고정한다."""
    assert issubclass(TargetTapeStrategy, DeclarativeTapeStrategy)


def _bar_row(session: date, security_id: str, close: float) -> MarketBarRecord:
    return MarketBarRecord(
        session=session,
        security_id=security_id,
        open=close - 1.0,
        high=close + 1.0,
        low=close - 2.0,
        close=close,
        volume=1_000,
    )


def _reference_feed(rows: tuple[MarketBarRecord, ...]) -> DataFeed:
    """이 PR 이전 어댑터가 하던 것: 행마다 `Bar`를 만들어 `DataFeed(bars)`에 넣는다."""
    return DataFeed(
        tuple(
            Bar(
                ts=datetime.combine(row.session, time(15, 30)),
                instrument=_instrument(row.security_id),
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
            )
            for row in rows
        )
    )


def test_columnar_feed_matches_the_bar_built_feed() -> None:
    """dataset 행에서 바로 만든 feed가 `Bar`를 거쳐 만든 feed와 같은 스냅샷·순서를 낸다.

    행이 종목 기준으로 묶여 와도(세션 연속이 아니어도) 세션 묶음과 세션 안 순서가 같아야
    한다 — 순서가 갈리면 두 코어의 결과 테이블 종목 조회표가 갈린다.
    """
    rows = (
        _bar_row(SIGNAL, "005930", 100.0),
        _bar_row(EXECUTION, "005930", 101.0),
        _bar_row(SIGNAL, "000660", 50.0),
        _bar_row(EXECUTION, "000660", 51.0),
    )
    columnar = _columnar_feed(rows)
    reference = _reference_feed(rows)

    assert columnar.sessions == reference.sessions
    assert list(columnar.snapshots()) == list(reference.snapshots())
    assert columnar.columns().instruments == reference.columns().instruments


def test_columnar_feed_keeps_the_bar_price_validation() -> None:
    """검증은 어댑터가 아니라 feed가 한다 — 메시지도 `Bar` 경로와 같아야 한다."""
    broken = (_bar_row(SIGNAL, "005930", 100.0), _bar_row(EXECUTION, "005930", 0.5))
    with pytest.raises(ValueError) as columnar:
        _columnar_feed(broken)
    with pytest.raises(ValueError) as reference:
        _reference_feed(broken)
    assert str(columnar.value) == str(reference.value)
