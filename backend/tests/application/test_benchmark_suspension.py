"""이슈 #226: 벤치마크 종목이 거래정지된 세션에도 벤치마크 곡선이 이어진다.

정지 세션에는 벤치마크 종목의 bar가 없다. 예전에는 그 세션의 벤치마크 값이 비어
`benchmark_return`·`excess_return`이 통째로 "사용 불가"가 됐다. 엔진은 정지 종목을 마지막
평가 가격(직전 종가)으로 계속 평가하므로(`Portfolio.mark`는 bar가 있는 종목만 갱신한다),
벤치마크도 같은 규칙으로 직전 값을 이어 쓰고 그 세션 수를 결과 경고로 알린다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

import pytest

from backtest_engine import BacktestEngine, RunConfig
from backtest_engine.types.actions import (
    ActionKind,
    ExecutionPolicy,
    SetPortfolioTarget,
    TargetScope,
    WeightTarget,
)
from backtest_engine.types.decision import StrategyDecision
from backtest_engine.types.events import CorporateActionEvent, CorporateActionType
from backtest_engine.types.instruments import InstrumentId
from backtest_engine.types.market import MarketSnapshot
from backtest_engine.types.requirements import EventKind, EverySession, StrategyRequirements
from strategy_workbench.adapters.outbound.backtest_engine._adapter import (
    _benchmark_series,
    _benchmark_warnings,
    _columnar_feed,
    _instrument,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestDataPort,
    BacktestDataQuery,
    BacktestDataset,
    CorporateActionRecord,
    UniverseMembershipRecord,
)
from strategy_workbench.domain.backtest.facade.runs import BacktestRunResult, DataWarning
from tests.application.test_benchmark_corporate_actions import (
    BENCHMARK,
    _action,
    _bar,
    _dataset,
    _metric,
    _run,
)

SUSPENDED = (date(2024, 1, 12), date(2024, 1, 15))
SPLIT_SESSION = date(2024, 1, 15)  # 정지 중 사건 — 다음 bar(2024-01-16)에 정산된다
SPLIT_RATIO = 2.0
CARRY_CODE = "benchmark.suspended_sessions_carried"


class _SuspendedBenchmark:
    """벤치마크 종목의 `SUSPENDED` 세션 bar를 지운다. `split=True`면 정지 중 2:1 분할도 싣는다."""

    def __init__(self, inner: BacktestDataPort, *, split: bool = False) -> None:
        self._inner = inner
        self._split = split

    def load_backtest_dataset(self, query: BacktestDataQuery) -> BacktestDataset:
        dataset = self._inner.load_backtest_dataset(query)
        bars = []
        for bar in dataset.bars:
            if bar.security_id != BENCHMARK:
                bars.append(bar)
            elif bar.session in SUSPENDED:
                continue
            elif self._split and bar.session >= SPLIT_SESSION:
                bars.append(
                    replace(
                        bar,
                        open=bar.open / SPLIT_RATIO,
                        high=bar.high / SPLIT_RATIO,
                        low=bar.low / SPLIT_RATIO,
                        close=bar.close / SPLIT_RATIO,
                        volume=int(bar.volume * SPLIT_RATIO),
                    )
                )
            else:
                bars.append(bar)
        actions = dataset.corporate_actions
        if self._split:
            actions = (
                *actions,
                CorporateActionRecord(
                    session=SPLIT_SESSION,
                    security_id=BENCHMARK,
                    action_type="split",
                    ratio=repr(SPLIT_RATIO),
                    detail="test-split-226",
                ),
            )
        return replace(dataset, bars=tuple(bars), corporate_actions=actions)


@pytest.mark.parametrize("split", [False, True], ids=["suspension", "split-during-suspension"])
def test_benchmark_metrics_survive_a_benchmark_suspension(tmp_path: Path, split: bool) -> None:
    plain = _run(MockEquityDataAdapter.demo(), tmp_path / "plain", "plain-run")
    suspended = _run(
        _SuspendedBenchmark(MockEquityDataAdapter.demo(), split=split),
        tmp_path / "suspended",
        "suspended-run",
    )

    curve = {item.session: item.benchmark_equity for item in suspended.series.equity}
    plain_curve = {item.session: item.benchmark_equity for item in plain.series.equity}
    # 정지 세션도 엔진 세션이다(다른 종목은 거래한다). 값은 직전 세션 값을 그대로 잇는다
    assert set(SUSPENDED) <= set(curve)
    previous = date(2024, 1, 11)
    for session in SUSPENDED:
        assert curve[session] == pytest.approx(plain_curve[previous], rel=1e-12), session
    # 정지가 끝나면 원래 곡선으로 돌아온다
    assert curve[date(2024, 1, 16)] == pytest.approx(plain_curve[date(2024, 1, 16)], rel=1e-12)

    assert _metric(suspended, "benchmark_return") == pytest.approx(
        _metric(plain, "benchmark_return"), rel=1e-12
    )
    assert _metric(suspended, "excess_return") == pytest.approx(
        _metric(suspended, "total_return") - _metric(suspended, "benchmark_return")
    )

    warnings = [item for item in suspended.manifest.warnings if item.code == CARRY_CODE]
    assert len(warnings) == 1
    assert "carried_sessions=2" in warnings[0].message
    assert BENCHMARK in warnings[0].message
    assert not [item for item in plain.manifest.warnings if item.code == CARRY_CODE]


D1, D2, D3, D4, D5 = (date(2021, 5, day) for day in (17, 18, 20, 21, 24))


def test_suspended_sessions_carry_the_previous_adjusted_value() -> None:
    dataset = _dataset((_bar(D1, 100.0), _bar(D4, 120.0)), ())

    values, carried = _benchmark_series(dataset, 1_000.0, (D1, D2, D3, D4))

    assert values == {D1: 1_000.0, D2: 1_000.0, D3: 1_000.0, D4: pytest.approx(1_200.0)}
    assert carried == (D2, D3)


def test_event_during_suspension_waits_for_the_next_bar_like_the_engine() -> None:
    """50:1 병합이 정지 중(D3)에 일어나면 정지 동안은 병합 전 가치를 쓴다.

    재개 bar부터 병합 후 가치를 쓴다.
    """
    dataset = _dataset(
        (_bar(D1, 339.0), _bar(D2, 339.0), _bar(D5, 14_650.0)),
        (_action(D3, "reverse_split", 0.02),),
    )

    values, carried = _benchmark_series(dataset, 100.0, (D1, D2, D3, D4, D5))

    assert values[D3] == values[D4] == pytest.approx(100.0)
    assert values[D5] == pytest.approx(100.0 * 293.0 / 339.0)
    assert carried == (D3, D4)


def test_sessions_before_the_first_benchmark_bar_stay_empty() -> None:
    """상장 전 세션은 살 수 없었다. 이어 쓸 직전 값이 없으므로 비워 둔다."""
    dataset = _dataset((_bar(D3, 100.0), _bar(D4, 110.0)), ())

    values, carried = _benchmark_series(dataset, 1_000.0, (D1, D2, D3, D4))

    assert values == {D3: 1_000.0, D4: pytest.approx(1_100.0)}
    assert carried == ()


def test_sessions_after_the_last_benchmark_bar_carry_the_last_value() -> None:
    """상장폐지 뒤에도 엔진은 포지션을 마지막 평가 가격에 동결한다. 벤치마크도 같다."""
    dataset = _dataset((_bar(D1, 100.0), _bar(D2, 90.0)), ())

    values, carried = _benchmark_series(dataset, 1_000.0, (D1, D2, D3))

    assert values[D3] == pytest.approx(900.0)
    assert carried == (D3,)


def test_no_benchmark_means_no_values_and_no_carry() -> None:
    dataset = replace(_dataset((_bar(D1, 100.0),), ()), benchmark_security_id=None)

    assert _benchmark_series(dataset, 1_000.0, (D1, D2)) == ({}, ())


# 이슈 #229: 이어 쓴 세션을 거래정지와 상장 종료 뒤 동결로 나누고, 창 시작이 첫 bar보다 앞서
# 벤치마크 지표가 사용 불가가 되는 이유를 경고로 알린다.
DELIST_CODE = "benchmark.delisted_sessions_frozen"
LEADING_CODE = "benchmark.no_bar_at_start"


class _BenchmarkGaps:
    """벤치마크 종목의 bar를 `dropped` 세션에서 지우고, 멤버십 `last_session`을 바꿀 수 있다."""

    def __init__(
        self,
        inner: BacktestDataPort,
        *,
        dropped: Callable[[date], bool],
        last_session: date | None = None,
    ) -> None:
        self._inner = inner
        self._dropped = dropped
        self._last_session = last_session

    def load_backtest_dataset(self, query: BacktestDataQuery) -> BacktestDataset:
        dataset = self._inner.load_backtest_dataset(query)
        bars = tuple(
            bar
            for bar in dataset.bars
            if bar.security_id != BENCHMARK or not self._dropped(bar.session)
        )
        memberships = tuple(
            replace(item, last_session=self._last_session)
            if item.security_id == BENCHMARK and self._last_session is not None
            else item
            for item in dataset.memberships
        )
        return replace(dataset, bars=bars, memberships=memberships)


def _warning(result: BacktestRunResult, code: str) -> DataWarning:
    matches = [item for item in result.manifest.warnings if item.code == code]
    assert len(matches) == 1, [item.code for item in result.manifest.warnings]
    return matches[0]


def test_frozen_sessions_after_delisting_are_reported_apart_from_suspensions(
    tmp_path: Path,
) -> None:
    # 01-12·01-15 정지, 01-16이 마지막 상장일, 01-17~01-19는 상장 종료 뒤다.
    last_listed = date(2024, 1, 16)
    result = _run(
        _BenchmarkGaps(
            MockEquityDataAdapter.demo(),
            dropped=lambda session: session in SUSPENDED or session > last_listed,
            last_session=last_listed,
        ),
        tmp_path,
        "delisted-run",
    )

    suspended = _warning(result, CARRY_CODE)
    assert "carried_sessions=2 " in suspended.message
    assert "2024-01-12, 2024-01-15" in suspended.message
    assert "2024-01-17" not in suspended.message
    frozen = _warning(result, DELIST_CODE)
    assert BENCHMARK in frozen.message
    assert "frozen_sessions=3 " in frozen.message
    assert "last_session=2024-01-16" in frozen.message
    assert "2024-01-17, 2024-01-18, 2024-01-19" in frozen.message
    # 값 규칙은 그대로다: 상장 종료 뒤에도 마지막 값을 이어 써 지표가 살아 있다.
    assert _metric(result, "benchmark_return") is not None


def test_suspension_between_the_last_bar_and_delisting_counts_as_suspension() -> None:
    dataset = replace(
        _dataset((_bar(D1, 100.0), _bar(D2, 90.0)), ()),
        memberships=(UniverseMembershipRecord(BENCHMARK, D1, D4),),
    )
    sessions = (D1, D2, D3, D4, D5)
    values, carried = _benchmark_series(dataset, 1_000.0, sessions)

    warnings = {
        item.code: item.message for item in _benchmark_warnings(dataset, sessions, values, carried)
    }

    # D3·D4는 상장 기간 안의 정지, D5만 상장 종료(D4) 뒤다.
    assert "carried_sessions=2 " in warnings[CARRY_CODE]
    assert "frozen_sessions=1 " in warnings[DELIST_CODE]
    assert "last_session=2021-05-21" in warnings[DELIST_CODE]


def test_window_starting_inside_a_suspension_explains_the_unavailable_benchmark(
    tmp_path: Path,
) -> None:
    result = _run(
        _BenchmarkGaps(
            MockEquityDataAdapter.demo(),
            dropped=lambda session: session <= date(2024, 1, 9),
        ),
        tmp_path,
        "leading-run",
    )

    benchmark_return = next(
        item
        for item in result.metrics
        if item.metric_id == "benchmark_return" and item.scope == "full"
    )
    assert benchmark_return.value is None
    leading = _warning(result, LEADING_CODE)
    assert BENCHMARK in leading.message
    assert "leading_sessions=2 " in leading.message
    assert "first_bar=2024-01-10" in leading.message
    assert "benchmark_return" in leading.message
    # 상장은 창 시작부터라(멤버십 first_session) 이유는 거래정지다.
    assert "거래정지" in leading.message
    assert not [item for item in result.manifest.warnings if item.code == CARRY_CODE]


def test_benchmark_listed_after_the_window_start_is_reported_as_not_yet_listed() -> None:
    dataset = replace(
        _dataset((_bar(D3, 100.0), _bar(D4, 110.0)), ()),
        memberships=(UniverseMembershipRecord(BENCHMARK, D3, D5),),
    )
    sessions = (D1, D2, D3, D4)
    values, carried = _benchmark_series(dataset, 1_000.0, sessions)

    warnings = {
        item.code: item.message for item in _benchmark_warnings(dataset, sessions, values, carried)
    }

    assert set(warnings) == {LEADING_CODE}
    assert "leading_sessions=2 " in warnings[LEADING_CODE]
    assert "상장 전" in warnings[LEADING_CODE]


def test_benchmark_without_any_bar_in_the_window_is_reported() -> None:
    dataset = _dataset((_bar(D1, 100.0, security_id="other"),), ())
    sessions = (D1, D2)
    values, carried = _benchmark_series(dataset, 1_000.0, sessions)

    warnings = {
        item.code: item.message for item in _benchmark_warnings(dataset, sessions, values, carried)
    }

    assert set(warnings) == {LEADING_CODE}
    assert "leading_sessions=2 " in warnings[LEADING_CODE]
    assert "first_bar=없음" in warnings[LEADING_CODE]


def test_fully_valued_benchmark_raises_no_warning() -> None:
    dataset = _dataset((_bar(D1, 100.0), _bar(D2, 101.0)), ())
    sessions = (D1, D2)
    values, carried = _benchmark_series(dataset, 1_000.0, sessions)

    assert _benchmark_warnings(dataset, sessions, values, carried) == ()


class _BuyAndHold:
    """첫 bar에서 `instrument`를 한 번 사서 끝까지 들고 있는 엔진 전략."""

    def __init__(self, instrument: InstrumentId) -> None:
        self._instrument = instrument
        self._done = False

    def requirements(self) -> StrategyRequirements:
        return StrategyRequirements(
            histories=(),
            schedule=EverySession(),
            events=frozenset({EventKind.MARKET}),
            actions=frozenset({ActionKind.NO_ACTION, ActionKind.SET_PORTFOLIO_TARGET}),
            features=frozenset(),
        )

    def on_event(self, ctx, event):
        if not isinstance(event, MarketSnapshot) or self._done or not event.has(self._instrument):
            return StrategyDecision.no_action(ctx.now, "hold")
        self._done = True
        return StrategyDecision.of(
            ctx.now,
            SetPortfolioTarget(
                targets=(WeightTarget(self._instrument, 0.95),),
                scope=TargetScope.PATCH,
                execution=ExecutionPolicy.market_next_open(),
            ),
            reason="buy",
        )


@pytest.mark.parametrize("core", ["python", "rust"])
def test_benchmark_curve_follows_the_engine_holder_through_suspension_split_and_delisting(
    core: str,
) -> None:
    """이슈 #229(DEFECT-228-04): 벤치마크 곡선은 엔진이 같은 종목 보유자를 평가하는 규칙을 따른다.

    정지(01-12·01-15), 정지 중 2:1 분할(01-15, 재개 bar 01-16에 정산), 상장 종료(01-17 이후 bar
    없음)를 모두 담은 mock 데이터로 엔진에 buy-and-hold를 돌린다. 수수료·슬리피지 없이 한 번 산 뒤
    현금은 그대로이므로 보유자 가치(`equity - cash`)의 상대 경로가 벤치마크 곡선의 상대 경로와
    같아야 한다. 엔진의 정지 종목 평가·사건 정산 시점이 바뀌면 이 테스트가 먼저 깨진다.
    """
    last_listed = date(2024, 1, 16)
    port = _BenchmarkGaps(
        _SuspendedBenchmark(MockEquityDataAdapter.demo(), split=True),
        dropped=lambda session: session > last_listed,
        last_session=last_listed,
    )
    dataset = port.load_backtest_dataset(
        BacktestDataQuery(
            start=date(2024, 1, 8),
            end=date(2024, 1, 19),
            security_ids=("sec-005930-1",),
            benchmark_security_id=BENCHMARK,
        )
    )
    instrument = _instrument(BENCHMARK)
    result = BacktestEngine(
        RunConfig(run_id=f"buy-and-hold-{core}", initial_cash=1e9, fee_bps=0.0), core=core
    ).run(
        _BuyAndHold(instrument),
        _columnar_feed(dataset.bars),
        corporate_actions=tuple(
            CorporateActionEvent(
                ts=datetime.combine(item.session, time(15, 30)),
                instrument=_instrument(item.security_id),
                action_type=CorporateActionType(item.action_type),
                ratio=Decimal(item.ratio),
                detail=item.detail,
            )
            for item in dataset.corporate_actions
        ),
    )
    fill_ts = next(item.ts for item in result.fills if item.instrument == instrument)
    snapshots = [item for item in result.snapshots if item.ts >= fill_ts]
    cash = snapshots[0].cash
    holder = {item.ts.date(): item.equity - cash for item in snapshots}
    sessions = tuple(sorted({bar.session for bar in dataset.bars}))
    values, carried = _benchmark_series(dataset, 1_000.0, sessions)

    start = min(holder)
    # 정지·분할·상장 종료 세션이 모두 비교 구간 안에 있어야 이 테스트가 뜻을 가진다.
    assert set(carried) == {*SUSPENDED, date(2024, 1, 17), date(2024, 1, 18), date(2024, 1, 19)}
    assert start < min(carried)
    for session, held in holder.items():
        assert held / holder[start] == pytest.approx(values[session] / values[start], rel=1e-9), (
            session
        )
