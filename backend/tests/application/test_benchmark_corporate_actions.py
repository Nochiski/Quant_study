"""이슈 #219: 벤치마크 곡선도 확인된 분할·병합에 조정된다.

전략 자산 곡선은 엔진이 사건일에 보유 수량을 조정해 이어진다. 벤치마크 곡선은 따로
`close / first_close`로 계산해 원주가 불연속을 그대로 가졌다. 여기서는 mock 데이터의 벤치마크
종목에 창 중간 2:1 분할을 끼워 넣은 데이터 포트로 두 곡선을 비교한다. 사건을 반영하면 분할한
데이터의 벤치마크 곡선·벤치마크 수익률·초과수익은 분할 없는 데이터와 같아야 한다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.artifact_local.facade.store import LocalArtifactStore
from strategy_workbench.adapters.outbound.backtest_engine._adapter import _benchmark_values
from strategy_workbench.adapters.outbound.backtest_engine.facade.executor import (
    BacktestEngineExecutorAdapter,
)
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestDataPort,
    BacktestDataQuery,
    BacktestDataset,
    CorporateActionRecord,
    MarketBarRecord,
)
from strategy_workbench.application.backtest_run.facade.runs import (
    BacktestRunService,
    BacktestRunSpec,
)
from strategy_workbench.application.portfolio_design.facade.design import PortfolioDesignService
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.analytics.facade.metrics import build_default_metric_registry
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.backtest.facade.runs import BacktestRunResult, ExecutionCore
from strategy_workbench.domain.factor.facade.expression import (
    FactorGraph,
    FieldNode,
    TimeSeriesNode,
    TimeSeriesOperator,
)
from strategy_workbench.domain.strategy.facade.specification import (
    FactorDirection,
    FactorSignal,
    RebalanceFrequency,
    StrategySpec,
)
from tests.backtest_run_wait import wait_for_terminal_run

WINDOW = (date(2024, 1, 8), date(2024, 1, 19))
BENCHMARK = "sec-000660-1"
SPLIT_SESSION = date(2024, 1, 15)
SPLIT_RATIO = 2.0


class _SplitBenchmark:
    """mock 데이터에서 벤치마크 종목만 `SPLIT_SESSION`부터 원주가를 1/2로 낮추고 사건을 싣는다.

    duckdb 어댑터가 내는 모양과 같다: 원주가 bar + `adj_factor` factor_ok 행에서 온 split 사건
    (`ratio` = share_factor).
    """

    def __init__(self, inner: BacktestDataPort) -> None:
        self._inner = inner

    def load_backtest_dataset(self, query: BacktestDataQuery) -> BacktestDataset:
        dataset = self._inner.load_backtest_dataset(query)
        bars = tuple(
            replace(
                bar,
                open=bar.open / SPLIT_RATIO,
                high=bar.high / SPLIT_RATIO,
                low=bar.low / SPLIT_RATIO,
                close=bar.close / SPLIT_RATIO,
                volume=int(bar.volume * SPLIT_RATIO),
            )
            if bar.security_id == BENCHMARK and bar.session >= SPLIT_SESSION
            else bar
            for bar in dataset.bars
        )
        action = CorporateActionRecord(
            session=SPLIT_SESSION,
            security_id=BENCHMARK,
            action_type="split",
            ratio=repr(SPLIT_RATIO),
            detail="test-split-219",
        )
        return replace(
            dataset,
            bars=bars,
            corporate_actions=(*dataset.corporate_actions, action),
        )


# schema 1.2 문서는 기간·유니버스를 담지 않는다(lang2 P2-03). 실행 설정은 run 요청이 싣는다.
_ENVIRONMENT = RunEnvironment(start=WINDOW[0], end=WINDOW[1], universe_id="krx.common-stock")


def _spec() -> StrategySpec:
    template = StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused"
    ).template()
    momentum = FactorSignal(
        factor_id="momentum_3",
        label="3세션 모멘텀",
        direction=FactorDirection.HIGH,
        weight=1.0,
        graph=FactorGraph(
            nodes=(
                FieldNode("close", "price.adj_close", "field"),
                TimeSeriesNode("mom", TimeSeriesOperator.MOMENTUM, "close", 3, "time_series"),
            ),
            output_node_id="mom",
        ),
    )
    return replace(
        template,
        factors=(momentum,),
        portfolio=replace(
            template.portfolio,
            rebalance=RebalanceFrequency.EVERY_N_SESSIONS,
            rebalance_every_n_sessions=1,
        ),
    )


def _run(data: BacktestDataPort, tmp_path: Path, run_id: str) -> BacktestRunResult:
    adapter = MockEquityDataAdapter.demo()
    portfolio = PortfolioDesignService(
        adapter,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=adapter,
        factor_registry_version="test-registry",
    )
    runs = BacktestRunService(
        portfolio,
        InMemoryStrategyRepository(),
        data,
        BacktestEngineExecutorAdapter(build_default_metric_registry()),
        LocalArtifactStore(tmp_path),
        new_id=lambda: run_id,
    )
    runs.start(
        BacktestRunSpec(
            strategy=_spec(),
            core=ExecutionCore.PYTHON,
            benchmark_security_id=BENCHMARK,
            environment=_ENVIRONMENT,
        )
    )
    state = wait_for_terminal_run(runs, run_id)
    assert state.status.value == "completed", state
    return runs.result(run_id)


def _metric(result: BacktestRunResult, metric_id: str) -> float:
    value = next(item.value for item in result.metrics if item.metric_id == metric_id)
    assert value is not None, metric_id
    return value


def test_benchmark_curve_is_continuous_across_a_benchmark_split(tmp_path: Path) -> None:
    plain = _run(MockEquityDataAdapter.demo(), tmp_path / "plain", "plain-run")
    split = _run(_SplitBenchmark(MockEquityDataAdapter.demo()), tmp_path / "split", "split-run")

    plain_curve = {item.session: item.benchmark_equity for item in plain.series.equity}
    split_curve = {item.session: item.benchmark_equity for item in split.series.equity}
    assert SPLIT_SESSION in split_curve
    assert set(split_curve) == set(plain_curve)
    for session, value in split_curve.items():
        assert value == pytest.approx(plain_curve[session], rel=1e-12), session
    assert _metric(split, "benchmark_return") == pytest.approx(
        _metric(plain, "benchmark_return"), rel=1e-12
    )


def test_benchmark_excess_return_ignores_the_split(tmp_path: Path) -> None:
    split = _run(_SplitBenchmark(MockEquityDataAdapter.demo()), tmp_path, "split-run")

    benchmark_return = _metric(split, "benchmark_return")

    # 원주가 곡선이라면 분할 하나로 벤치마크 수익률이 약 −50%가 된다
    assert benchmark_return > -0.2
    assert _metric(split, "excess_return") == pytest.approx(
        _metric(split, "total_return") - benchmark_return
    )


def _bar(session: date, close: float, security_id: str = BENCHMARK) -> MarketBarRecord:
    return MarketBarRecord(
        session=session,
        security_id=security_id,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000,
    )


def _action(session: date, action_type: str, ratio: float) -> CorporateActionRecord:
    return CorporateActionRecord(
        session=session,
        security_id=BENCHMARK,
        action_type=action_type,
        ratio=repr(ratio),
        detail=f"{action_type}@{session}",
    )


def _dataset(
    bars: tuple[MarketBarRecord, ...], actions: tuple[CorporateActionRecord, ...]
) -> BacktestDataset:
    return BacktestDataset(
        data_snapshot_id="unit",
        bars=bars,
        memberships=(),
        corporate_actions=actions,
        benchmark_security_id=BENCHMARK,
    )


D1, D2, D3, D4 = (date(2021, 5, day) for day in (18, 20, 21, 24))


def test_reverse_split_on_the_event_session_keeps_the_holder_value() -> None:
    """50:1 병합(221610 2021-05-21 모양): 원주가 339 → 14,650, 주식 수 × 0.02."""
    dataset = _dataset(
        (_bar(D1, 339.0), _bar(D2, 339.0), _bar(D3, 14_650.0), _bar(D4, 12_800.0)),
        (_action(D3, "reverse_split", 0.02),),
    )

    values = _benchmark_values(dataset, 100.0)

    assert values[D2] == pytest.approx(100.0)
    assert values[D3] == pytest.approx(100.0 * 293.0 / 339.0)
    assert values[D4] == pytest.approx(100.0 * 256.0 / 339.0)


def test_event_without_a_bar_that_day_applies_from_the_next_bar() -> None:
    no_bar_day = date(2021, 5, 19)
    dataset = _dataset(
        (_bar(D1, 1_000.0), _bar(D2, 500.0)),
        (_action(no_bar_day, "split", 2.0),),
    )

    assert _benchmark_values(dataset, 100.0) == {D1: 100.0, D2: pytest.approx(100.0)}


def test_event_on_the_first_bar_does_not_move_the_curve() -> None:
    dataset = _dataset(
        (_bar(D1, 500.0), _bar(D2, 550.0)),
        (_action(D1, "split", 2.0),),
    )

    assert _benchmark_values(dataset, 100.0) == {D1: 100.0, D2: pytest.approx(110.0)}


def test_notify_only_events_and_other_securities_do_not_adjust_the_curve() -> None:
    other = replace(_action(D2, "split", 2.0), security_id="sec-other-1")
    dataset = _dataset(
        (_bar(D1, 1_000.0), _bar(D2, 1_100.0), _bar(D2, 9.0, "sec-other-1")),
        (_action(D2, "share_count_change", 3.0), other),
    )

    assert _benchmark_values(dataset, 100.0) == {D1: 100.0, D2: pytest.approx(110.0)}
