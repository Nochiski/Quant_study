"""선언형 tape: Python reference 규칙(`evaluate_tape`)과 Rust 네이티브 경로의 패리티.

bar 없는 종목(거래정지·기준가 세션) 처리 규칙과, Rust 경로가 Python 콜백 없이 같은 trace를
남기는지 검사한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import pytest

from backtest_engine import BacktestEngine, RunConfig
from backtest_engine.data.feed import DataFeed
from backtest_engine.engine.core import core_available
from backtest_engine.engine.tape import evaluate_tape
from backtest_engine.errors import UndeclaredActionReturned
from backtest_engine.types.actions import (
    ActionKind,
    ExecutionPolicy,
    QuantityTarget,
    SetPortfolioTarget,
    TargetScope,
    WeightTarget,
)
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import StrategyEvent
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import MarketSnapshot, PriceWindow
from backtest_engine.types.requirements import (
    EventKind,
    EverySession,
    HistoryRequest,
    StrategyRequirements,
)
from backtest_engine.types.strategy import StrategyContext
from backtest_engine.types.tape import DeclarativeTapeStrategy, TapeFrame
from tests.conftest import day, make_bar, make_instrument

RUST_ONLY = pytest.mark.skipif(not core_available("rust"), reason="rust core not built")
SIGNAL = date(2018, 4, 27)
A, B, C = make_instrument("000660:1"), make_instrument("005930:1"), make_instrument("000030:1")
# 작은따옴표가 든 심볼. feed에 없어 미보유 no_bar 경로를 탄다.
QUOTED = make_instrument("00'30")


class _Context:
    def __init__(self, now: datetime, positions: dict[str, Decimal]) -> None:
        self.now = now
        self._positions = positions

    def history(self, request: HistoryRequest) -> PriceWindow:
        raise NotImplementedError("tape strategy declares no history")

    def current_weight(self, instrument: InstrumentId) -> float:
        return 0.0

    def position_qty(self, instrument: InstrumentId) -> Decimal:
        return self._positions.get(instrument.symbol, Decimal(0))

    def cash(self) -> float:
        return 0.0

    def portfolio_value(self) -> float:
        return 0.0

    def universe(self) -> frozenset[InstrumentId]:
        return frozenset()

    def open_orders(self, instrument: InstrumentId | None = None) -> tuple[Any, ...]:
        return ()


def _frame(*targets: tuple[InstrumentId, float]) -> TapeFrame:
    return TapeFrame(
        action=SetPortfolioTarget(
            targets=tuple(WeightTarget(instrument, weight) for instrument, weight in targets),
            scope=TargetScope.REPLACE,
            execution=ExecutionPolicy.market_next_open(),
        ),
        reason=f"target_tape:{SIGNAL.isoformat()}",
    )


def test_tape_frame_rejects_non_weight_targets() -> None:
    with pytest.raises(TypeError, match="must be WeightTarget"):
        TapeFrame(
            action=SetPortfolioTarget(
                targets=(QuantityTarget(A, Decimal(1)),),
                scope=TargetScope.REPLACE,
                execution=ExecutionPolicy.market_next_open(),
            ),
            reason="r",
        )


def test_targets_without_a_bar_are_held_when_owned_and_skipped_otherwise() -> None:
    ts = datetime.combine(SIGNAL, time(15, 30))
    snapshot = MarketSnapshot(ts=ts, bars=(make_bar(ts, A, 100.0, 101.0),))
    frames = {SIGNAL: _frame((A, 0.4), (B, 0.4), (C, 0.2))}

    decision = evaluate_tape(
        frames, "target_tape_idle", _Context(ts, {"005930:1": Decimal(12)}), snapshot
    )

    action = decision.actions[0]
    assert isinstance(action, SetPortfolioTarget)
    assert action.targets == (
        WeightTarget(A, 0.4),
        QuantityTarget(B, Decimal(12)),  # 보유 중 → 수량 유지
    )  # C는 미보유 + bar 없음 → 건너뜀 (예산은 현금에 남는다)
    assert decision.reason == "target_tape:2018-04-27 no_bar=('005930:1', '000030:1')"


def test_frames_with_every_instrument_priced_pass_through_unchanged() -> None:
    ts = datetime.combine(SIGNAL, time(15, 30))
    snapshot = MarketSnapshot(ts=ts, bars=(make_bar(ts, A, 100.0, 101.0),))

    decision = evaluate_tape({SIGNAL: _frame((A, 0.7))}, "idle", _Context(ts, {}), snapshot)

    action = decision.actions[0]
    assert isinstance(action, SetPortfolioTarget)
    assert action.targets == (WeightTarget(A, 0.7),)
    assert decision.reason == "target_tape:2018-04-27"


def test_sessions_without_a_frame_and_non_market_events_are_idle() -> None:
    ts = datetime.combine(SIGNAL, time(15, 30))
    snapshot = MarketSnapshot(ts=ts, bars=(make_bar(ts, A, 100.0, 101.0),))
    idle = evaluate_tape({}, "target_tape_idle", _Context(ts, {}), snapshot)
    assert idle == StrategyDecision.no_action(ts, "target_tape_idle")


# --- Rust 네이티브 경로 패리티 ------------------------------------------------------


class _TapeStrategy(DeclarativeTapeStrategy):
    """`DeclarativeTapeStrategy`를 구현하는 최소 전략. python 경로는 on_event, rust 경로는 tape."""

    @property
    def idle_reason(self) -> str:
        return "tape_idle"

    def __init__(
        self,
        frames: Mapping[date, TapeFrame],
        actions: frozenset[ActionKind] = frozenset(
            {ActionKind.NO_ACTION, ActionKind.SET_PORTFOLIO_TARGET}
        ),
    ) -> None:
        self._frames = frames
        self._actions = actions
        self.callbacks = 0

    def requirements(self) -> StrategyRequirements:
        return StrategyRequirements(
            histories=(),
            schedule=EverySession(),
            events=frozenset({EventKind.MARKET}),
            actions=self._actions,
            features=frozenset(),
        )

    def tape_frames(self) -> Mapping[date, TapeFrame]:
        return self._frames

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        self.callbacks += 1
        return evaluate_tape(self._frames, self.idle_reason, ctx, event)


_X, _Y = make_instrument("005930"), make_instrument("000660")


def _delisting_feed() -> DataFeed:
    """D1·D2 X·Y 거래, D3부터 Y 상폐. D3 프레임은 Y(보유·bar 없음)·미보유 C를 함께 요구한다."""
    return DataFeed(
        (
            make_bar(day(1), _X, 100.0, 100.0),
            make_bar(day(1), _Y, 50.0, 50.0),
            make_bar(day(2), _X, 100.0, 100.0),
            make_bar(day(2), _Y, 50.0, 50.0),
            make_bar(day(3), _X, 110.0, 110.0),
            make_bar(day(4), _X, 105.0, 105.0),
        )
    )


def _delisting_frames() -> dict[date, TapeFrame]:
    def frame(*targets: tuple[InstrumentId, float], reason: str) -> TapeFrame:
        return TapeFrame(
            action=SetPortfolioTarget(
                targets=tuple(WeightTarget(instrument, weight) for instrument, weight in targets),
                scope=TargetScope.REPLACE,
                execution=ExecutionPolicy.market_next_open(),
            ),
            reason=reason,
        )

    return {
        day(1).date(): frame((_X, 0.4), (_Y, 0.4), reason="tape:d1"),
        day(3).date(): frame((_X, 0.4), (_Y, 0.4), (C, 0.1), reason="tape:d3"),
        # 세션이 아닌 날짜의 프레임은 두 경로 모두 무시한다.
        date(2030, 1, 1): frame((_X, 1.0), reason="tape:never"),
    }


def _no_bar_order_frames() -> dict[date, TapeFrame]:
    """D3 프레임의 목표 순서를 "미보유 no_bar(C) → 보유 no_bar(Y) → 정상(X)"으로 둔다."""

    def frame(*targets: tuple[InstrumentId, float], reason: str) -> TapeFrame:
        return TapeFrame(
            action=SetPortfolioTarget(
                targets=tuple(WeightTarget(instrument, weight) for instrument, weight in targets),
                scope=TargetScope.REPLACE,
                execution=ExecutionPolicy.market_next_open(),
            ),
            reason=reason,
        )

    return {
        day(1).date(): frame((_X, 0.4), (_Y, 0.4), reason="tape:d1"),
        day(3).date(): frame((C, 0.1), (_Y, 0.4), (_X, 0.4), reason="tape:d3"),
    }


def _apostrophe_frames() -> dict[date, TapeFrame]:
    """D3 프레임이 작은따옴표가 든 미보유 종목을 요구한다 — 사유에 no_bar로 남는다."""

    def frame(*targets: tuple[InstrumentId, float], reason: str) -> TapeFrame:
        return TapeFrame(
            action=SetPortfolioTarget(
                targets=tuple(WeightTarget(instrument, weight) for instrument, weight in targets),
                scope=TargetScope.REPLACE,
                execution=ExecutionPolicy.market_next_open(),
            ),
            reason=reason,
        )

    return {
        day(1).date(): frame((_X, 0.4), (_Y, 0.4), reason="tape:d1"),
        day(3).date(): frame((_X, 0.4), (QUOTED, 0.1), reason="tape:d3"),
    }


class _LookAlike:
    """`_TapeStrategy`와 이름 네 개가 같지만 `DeclarativeTapeStrategy`를 상속하지 않은 전략.

    구조 일치만으로 tape 경로를 고르면 이 전략의 `on_event()`는 한 번도 불리지 않는다 —
    예외도 경고도 없이 전략 로직이 통째로 건너뛰어진다.
    """

    idle_reason = "look_alike_idle"

    def __init__(self, frames: Mapping[date, TapeFrame]) -> None:
        self._frames = frames
        self.callbacks = 0

    def requirements(self) -> StrategyRequirements:
        return StrategyRequirements(
            histories=(),
            schedule=EverySession(),
            events=frozenset({EventKind.MARKET}),
            actions=frozenset({ActionKind.NO_ACTION, ActionKind.SET_PORTFOLIO_TARGET}),
            features=frozenset(),
        )

    def tape_frames(self) -> Mapping[date, TapeFrame]:
        return self._frames

    def on_event(self, ctx: StrategyContext, event: StrategyEvent) -> StrategyDecision:
        self.callbacks += 1
        return evaluate_tape(self._frames, self.idle_reason, ctx, event)


def test_declarative_tape_requires_explicit_inheritance() -> None:
    assert isinstance(_TapeStrategy({}), DeclarativeTapeStrategy)
    assert not isinstance(_LookAlike({}), DeclarativeTapeStrategy)
    assert not isinstance(object(), DeclarativeTapeStrategy)


@RUST_ONLY
def test_look_alike_strategy_keeps_the_callback_path() -> None:
    """상속하지 않은 전략은 이름이 다 맞아도 콜백 경로로 실행된다."""
    strategy = _LookAlike(_delisting_frames())
    engine = BacktestEngine(RunConfig(run_id="look-alike", initial_cash=100_000.0), core="rust")
    engine.run(strategy, _delisting_feed())

    assert strategy.callbacks == 4
    assert engine.event_store.decision_tape != ()


@RUST_ONLY
def test_rust_tape_path_matches_python_trace_without_callbacks() -> None:
    engines: dict[str, BacktestEngine] = {}
    strategies: dict[str, _TapeStrategy] = {}
    results = {}
    for core in ("python", "rust"):
        # C는 feed에 한 번도 나오지 않는 종목 — 두 경로 모두 미보유 no_bar로 건너뛰어야 한다.
        strategy = _TapeStrategy(_delisting_frames())
        engine = BacktestEngine(RunConfig(run_id="tape", initial_cash=100_000.0), core=core)
        results[core] = engine.run(strategy, _delisting_feed())
        engines[core] = engine
        strategies[core] = strategy

    assert results["python"] == results["rust"]
    python_store, rust_store = engines["python"].event_store, engines["rust"].event_store
    assert python_store.trace_bytes() == rust_store.trace_bytes()
    assert strategies["python"].callbacks == 4
    assert strategies["rust"].callbacks == 0
    assert rust_store.decision_tape == ()
    reasons = [record.decision.reason for record in rust_store.decisions()]
    assert reasons == [
        "tape:d1",
        "tape_idle",
        "tape:d3 no_bar=('000660', '000030:1')",
        "tape_idle",
    ]
    # D3: Y는 보유 중이라 수량 고정 → 청산 주문이 나오지 않고 X만 리밸런싱된다.
    assert [order.instrument.symbol for order in results["rust"].orders] == [
        "005930",
        "000660",
        "005930",
    ]


@RUST_ONLY
def test_no_bar_reason_quotes_symbols_containing_an_apostrophe() -> None:
    """작은따옴표가 든 심볼도 python `repr(tuple)` 표기 그대로 사유에 남는다.

    사유 조립을 Rust가 흉내 내던 시절에는 따옴표를 무조건 작은따옴표로 감싸 `('00'30',)`
    같은 깨진 문자열이 나왔다. 포맷 정본이 `engine/tape.no_bar_reason` 한 곳이 되어 두 코어가
    같은 bytes를 남기는지 고정한다.
    """
    traces: dict[str, bytes] = {}
    reasons: dict[str, list[str | None]] = {}
    for core in ("python", "rust"):
        engine = BacktestEngine(RunConfig(run_id="tape", initial_cash=100_000.0), core=core)
        engine.run(_TapeStrategy(_apostrophe_frames()), _delisting_feed())
        traces[core] = engine.event_store.trace_bytes()
        reasons[core] = [record.decision.reason for record in engine.event_store.decisions()]

    assert traces["python"] == traces["rust"]
    assert reasons["python"] == reasons["rust"]
    assert reasons["rust"] == [
        "tape:d1",
        "tape_idle",
        '''tape:d3 no_bar=("00'30",)''',
        "tape_idle",
    ]


@RUST_ONLY
def test_tape_routing_error_raises_the_same_engine_exception_in_both_cores() -> None:
    """tape 경로 라우팅 오류도 콜백 경로와 같은 엔진 예외로 올라온다 (문자열 인코딩 아님)."""
    frames = {day(1).date(): _delisting_frames()[day(1).date()]}
    failures: list[tuple[type[BaseException], str, bytes]] = []
    for core in ("python", "rust"):
        # 프레임은 SET_PORTFOLIO_TARGET을 내는데 requirements()는 NO_ACTION만 선언했다.
        strategy = _TapeStrategy(frames, frozenset({ActionKind.NO_ACTION}))
        engine = BacktestEngine(
            RunConfig(run_id="tape-route-error", initial_cash=100_000.0), core=core
        )
        with pytest.raises(UndeclaredActionReturned) as caught:
            engine.run(strategy, _delisting_feed())
        failures.append(
            (
                type(caught.value),
                str(caught.value).partition(" — ")[0],
                engine.event_store.trace_bytes(),
            )
        )
    assert failures[0] == failures[1]
    assert failures[0][1] == "action kind was not declared in requirements()"


@RUST_ONLY
def test_rust_tape_path_makes_one_drive_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from collections import Counter

    from backtest_engine.engine import loop as loop_module

    real_factory = loop_module.make_persistent_runtime
    calls: Counter[str] = Counter()

    class CountingRuntime:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def __getattr__(self, name: str) -> Any:
            attribute = getattr(self.inner, name)
            if not callable(attribute):
                return attribute

            def counted(*args: object, **kwargs: object) -> object:
                calls[name] += 1
                return attribute(*args, **kwargs)

            return counted

    monkeypatch.setattr(
        loop_module,
        "make_persistent_runtime",
        lambda *args, **kwargs: CountingRuntime(real_factory(*args, **kwargs)),
    )
    frames = {day(1).date(): _delisting_frames()[day(1).date()]}
    engine = BacktestEngine(RunConfig(run_id="tape-ffi", initial_cash=100_000.0), core="rust")
    result = engine.run(_TapeStrategy(frames), _delisting_feed())
    assert calls["drive"] == 1
    assert calls["submit_decision"] == 0
    assert calls["load_target_tape"] == 1
    assert len(result.orders) == 2
    assert len(engine.event_store.decisions()) == 4


@RUST_ONLY
def test_no_bar_reason_keeps_target_order_regardless_of_holdings() -> None:
    """GAP-3: 미보유 no_bar가 보유 no_bar보다 앞에 와도 reason 순서가 두 경로에서 같다.

    Rust는 피드 등록부에 없는 종목을 먼저 걸러내고 보유 여부는 그 다음에 본다 — 두 분기가
    같은 `no_bar` 리스트에 순서대로 쌓이는지 고정한다.
    """
    outcomes = {}
    for core in ("python", "rust"):
        engine = BacktestEngine(
            RunConfig(run_id="tape-no-bar-order", initial_cash=100_000.0), core=core
        )
        result = engine.run(_TapeStrategy(_no_bar_order_frames()), _delisting_feed())
        outcomes[core] = (
            result,
            engine.event_store.trace_bytes(),
            [record.decision.reason for record in engine.event_store.decisions()],
        )
    assert outcomes["python"][2] == [
        "tape:d1",
        "tape_idle",
        "tape:d3 no_bar=('000030:1', '000660')",
        "tape_idle",
    ]
    assert outcomes["python"] == outcomes["rust"]


@RUST_ONLY
def test_rust_tape_applies_a_frame_to_every_session_on_that_date() -> None:
    """DEFECT-201: 같은 날짜에 세션이 둘(일중)이면 Python처럼 두 세션 모두 프레임을 받는다."""

    def session(day_of_month: int, hour: int) -> datetime:
        return datetime(2026, 8, day_of_month, hour, 30)

    bars = tuple(
        make_bar(ts, instrument, 100.0, 100.0)
        for ts in (session(1, 9), session(1, 15), session(2, 9), session(2, 15))
        for instrument in (_X, _Y)
    )
    frames = {
        date(2026, 8, 1): TapeFrame(
            action=SetPortfolioTarget(
                targets=(WeightTarget(_X, 0.5), WeightTarget(_Y, 0.3)),
                scope=TargetScope.REPLACE,
                execution=ExecutionPolicy.market_next_open(),
            ),
            reason="tape:d1",
        )
    }
    outcomes = {}
    for core in ("python", "rust"):
        engine = BacktestEngine(RunConfig(run_id="intraday", initial_cash=100_000.0), core=core)
        result = engine.run(_TapeStrategy(frames), DataFeed(bars))
        outcomes[core] = (
            result,
            engine.event_store.trace_bytes(),
            [record.decision.reason for record in engine.event_store.decisions()],
        )
    assert outcomes["python"][2] == ["tape:d1", "tape:d1", "tape_idle", "tape_idle"]
    assert outcomes["python"] == outcomes["rust"]


@RUST_ONLY
def test_tape_order_materialization_reads_each_kind_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEFECT-202: tape 경로의 orders 최초 조회가 결정마다 배치를 다시 훑지 않는다."""
    from backtest_engine.engine.store import PersistentEventStore, RecordKind

    class CountingRuntime:
        """`drain_payloads` 호출만 세는 얇은 프록시. 나머지는 실제 runtime에 위임한다."""

        def __init__(self, inner: Any) -> None:
            self.inner = inner
            self.kinds: list[int] = []

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

        def drain_payloads(self, kind: int, limit: int) -> Any:
            self.kinds.append(kind)
            return self.inner.drain_payloads(kind, limit)

    frames = {
        day(n).date(): TapeFrame(
            action=SetPortfolioTarget(
                targets=(WeightTarget(_X, 0.1 * n),),
                scope=TargetScope.REPLACE,
                execution=ExecutionPolicy.market_next_open(),
            ),
            reason=f"tape:d{n}",
        )
        for n in (1, 2, 3)
    }
    bars = tuple(make_bar(day(n), _X, 100.0, 100.0) for n in (1, 2, 3, 4))
    engine = BacktestEngine(RunConfig(run_id="index-once", initial_cash=100_000.0), core="rust")
    result = engine.run(_TapeStrategy(frames), DataFeed(bars))
    store = engine.event_store
    assert isinstance(store, PersistentEventStore)
    counting = CountingRuntime(store._runtime)
    monkeypatch.setattr(store, "_runtime", counting)

    orders = result.orders
    assert len(orders) == 3
    # 주문 수와 무관하게 kind당 한 번이다 — ORDER는 결정 복원을 위해 DECISION을 먼저 읽는다.
    assert counting.kinds == [RecordKind.DECISION.code, RecordKind.ORDER.code]
    assert [order.decision_id for order in orders] == ["D-000001", "D-000002", "D-000003"]

    # DECISION은 이미 공개 객체가 됐으므로 다시 읽지 않는다.
    decisions = {record.decision_id: record.decision for record in store.decisions()}
    assert set(decisions) == {f"D-{n:06d}" for n in range(1, 5)}
    assert [decisions[f"D-{n:06d}"].reason for n in (1, 2, 3)] == ["tape:d1", "tape:d2", "tape:d3"]
    assert counting.kinds == [RecordKind.DECISION.code, RecordKind.ORDER.code]

    # 다른 kind 조회는 그 kind만 추가로 읽는다.
    assert result.fills is not None
    assert counting.kinds == [
        RecordKind.DECISION.code,
        RecordKind.ORDER.code,
        RecordKind.FILL.code,
    ]


@RUST_ONLY
def test_columnar_feed_builds_no_market_snapshot_for_tape_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """열로 적재한 feed는 tape 실행과 결과 테이블 조회 내내 `MarketSnapshot`을 안 만든다.

    tape 경로는 전략 콜백이 없고 워크벤치는 `result_tables()`만 읽는다 — 세션마다 스냅샷
    객체를 만들면 아무도 읽지 않는 객체를 세션 수만큼 만드는 셈이다. MARKET 레코드를 실제로
    조회하는 순간에만 만들어지는지도 같이 고정한다.
    """
    from backtest_engine.data import feed as feed_module
    from backtest_engine.types.market import Bar

    built: list[datetime] = []
    real_snapshot = feed_module.MarketSnapshot

    def counting(*, ts: datetime, bars: tuple[Bar, ...]) -> MarketSnapshot:
        built.append(ts)
        return real_snapshot(ts=ts, bars=bars)

    monkeypatch.setattr(feed_module, "MarketSnapshot", counting)

    sessions = [day(n) for n in (1, 2, 3)]
    feed = DataFeed.from_columns(
        sessions=sessions,
        instruments=[_X, _Y],
        offsets=[0, 2, 4, 5],
        instrument_ids=[0, 1, 0, 1, 0],
        opens=[100.0, 50.0, 100.0, 50.0, 110.0],
        highs=[100.0, 50.0, 100.0, 50.0, 110.0],
        lows=[100.0, 50.0, 100.0, 50.0, 110.0],
        closes=[100.0, 50.0, 100.0, 50.0, 110.0],
        volumes=[1_000, 1_000, 1_000, 1_000, 1_000],
    )
    frames = {
        day(1).date(): TapeFrame(
            action=SetPortfolioTarget(
                targets=(WeightTarget(_X, 0.5),),
                scope=TargetScope.REPLACE,
                execution=ExecutionPolicy.market_next_open(),
            ),
            reason="tape:d1",
        )
    }
    engine = BacktestEngine(RunConfig(run_id="tape-columnar", initial_cash=100_000.0), core="rust")
    engine.run(_TapeStrategy(frames), feed)
    tables = engine.event_store.result_tables()

    assert built == []
    assert tables.sessions == tuple(sessions)
    assert tables.instruments == (_X, _Y)

    market_sessions = [
        payload.ts
        for record in engine.event_store.records
        if isinstance(payload := record.payload, MarketSnapshot)
    ]
    assert market_sessions == sessions
    assert built == sessions
