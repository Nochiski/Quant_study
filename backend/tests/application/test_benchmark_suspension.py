"""이슈 #226: 벤치마크 종목이 거래정지된 세션에도 벤치마크 곡선이 이어진다.

정지 세션에는 벤치마크 종목의 bar가 없다. 예전에는 그 세션의 벤치마크 값이 비어
`benchmark_return`·`excess_return`이 통째로 "사용 불가"가 됐다. 엔진은 정지 종목을 마지막
평가 가격(직전 종가)으로 계속 평가하므로(`Portfolio.mark`는 bar가 있는 종목만 갱신한다),
벤치마크도 같은 규칙으로 직전 값을 이어 쓰고 그 세션 수를 결과 경고로 알린다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.backtest_engine._adapter import _benchmark_series
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestDataPort,
    BacktestDataQuery,
    BacktestDataset,
    CorporateActionRecord,
)
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
