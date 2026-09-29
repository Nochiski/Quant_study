"""검증 랩 V2-02: `BacktestDataPort` 의 워밍업 bar·거래대금 계약을 두 어댑터가 같게 답한다.

참여 기준 `adv20` 은 측정 구간 앞 20세션 거래대금으로 첫 세션들의 평균을 채운다. mock 으로
green 인 실행이 실데이터에서만 다르게 돌지 않도록, 두 어댑터가 (1) 워밍업을 요청하지 않으면 워밍업
bar 가 없고 (2) 요청하면 start 앞 거래일의 bar 만 따로 답하며 측정 구간 bar·경고는 그대로이고
(3) 모든 bar 에 원화 거래대금을 싣는지 대조한다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestDataPort,
    BacktestDataQuery,
)

HISTORY = 3
# (어댑터, 측정 구간, 종목). duckdb 픽스처의 000660 은 01-10 이 거래정지(기준가 행)라 워밍업에서도
# 빠져야 하고, 그 행이 측정 구간 경고 건수에 섞이면 안 된다.
CASES = [
    pytest.param("mock", date(2024, 1, 8), date(2024, 1, 12), ("sec-005930-1",), id="mock"),
    pytest.param(
        "equity_duckdb",
        date(2024, 1, 11),
        date(2024, 1, 12),
        ("005930:1", "000660:1"),
        id="equity_duckdb",
    ),
]


@pytest.fixture(scope="session")
def equity_duckdb_adapter(tmp_path_factory: pytest.TempPathFactory) -> BacktestDataPort:
    pytest.importorskip("duckdb", reason="backend optional extra `equity` (uv sync --extra equity)")
    from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (
        EquityDuckdbAdapter,
    )
    from tests.equity_fixture import build_workbench_root

    root: Path = tmp_path_factory.mktemp("backtest-data-contract") / "equity"
    return EquityDuckdbAdapter(build_workbench_root(root))


def _adapter(request: pytest.FixtureRequest, name: str) -> BacktestDataPort:
    if name == "mock":
        return MockEquityDataAdapter.demo()
    return request.getfixturevalue("equity_duckdb_adapter")


@pytest.mark.parametrize(("name", "start", "end", "security_ids"), CASES)
def test_warmup_bars_are_answered_apart_and_leave_the_window_unchanged(
    request: pytest.FixtureRequest,
    name: str,
    start: date,
    end: date,
    security_ids: tuple[str, ...],
) -> None:
    adapter = _adapter(request, name)
    query = BacktestDataQuery(start, end, security_ids, security_ids[0])

    plain = adapter.load_backtest_dataset(query)
    warmed = adapter.load_backtest_dataset(replace(query, history_sessions_before_start=HISTORY))

    assert plain.history_bars == ()
    assert (warmed.bars, warmed.warnings, warmed.invalid_bars) == (
        plain.bars,
        plain.warnings,
        plain.invalid_bars,
    )
    sessions = sorted({bar.session for bar in warmed.history_bars})
    assert len(sessions) == HISTORY and sessions[-1] < start
    assert {bar.security_id for bar in warmed.history_bars} == set(security_ids)
    assert all(
        bar.trading_value is not None and bar.trading_value > 0
        for bar in (*warmed.history_bars, *warmed.bars)
    )
