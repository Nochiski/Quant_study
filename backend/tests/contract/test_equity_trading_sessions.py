"""검증 랩 V3-05: `EquityDataPort.trading_sessions` 가 양끝을 포함한 거래 세션만 답한다.

워크포워드 엠바고가 이 세션을 세어 학습 측정 끝을 당긴다. mock 은 평일 달력, duckdb 는 원장
캘린더(휴장일이 빠진다)다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)


def test_mock_sessions_are_weekdays_between_both_ends() -> None:
    adapter = MockEquityDataAdapter.demo()

    assert adapter.trading_sessions(date(2024, 1, 5), date(2024, 1, 9)) == (
        date(2024, 1, 5),
        date(2024, 1, 8),
        date(2024, 1, 9),
    )
    assert adapter.trading_sessions(date(2024, 1, 6), date(2024, 1, 7)) == ()


def test_duckdb_sessions_follow_the_ledger_calendar(tmp_path: Path) -> None:
    pytest.importorskip("duckdb", reason="backend optional extra `equity` (uv sync --extra equity)")
    from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (
        EquityDuckdbAdapter,
    )
    from tests.equity_fixture import build_workbench_root

    adapter = EquityDuckdbAdapter(build_workbench_root(tmp_path / "equity"))

    # 2024-01-01 은 휴장일이라 캘린더에 없다.
    assert adapter.trading_sessions(date(2023, 12, 29), date(2024, 1, 3)) == (
        date(2023, 12, 29),
        date(2024, 1, 2),
        date(2024, 1, 3),
    )
    assert adapter.trading_sessions(date(2024, 1, 13), date(2024, 1, 14)) == ()
