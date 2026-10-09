"""duckdb 백테스트 dataset 이 무효 OHLC 행(GAP-14)으로 뺀 세션을 종목별로 넘긴다 (이슈 #241 P3-1).

원장 행은 거래(`price_kind='trade'`)인데 OHLC 가 NULL·0 이하이거나 서로 맞지 않으면 bar 로 내지
않는다. 그 세션은 거래정지가 아니라 거래된 날이다. 벤치마크 경고가 둘을 가를 수 있게 어댑터가
세션을 함께 넘긴다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.application.backtest_run.facade.ports import (
    BacktestDataQuery,
    InvalidBarRecord,
)
from tests.equity_fixture import WB_HALT_DATE, build_workbench_root

pytest.importorskip("duckdb", reason="backend optional extra `equity` (uv sync --extra equity)")

from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (  # noqa: E402
    EquityDuckdbAdapter,
)

START = date(2024, 1, 8)
INVALID_SESSION = date(2024, 1, 9)  # open NULL(GAP-14 전형)
INCONSISTENT_SESSION = date(2024, 1, 11)  # high < 종가 — 값은 다 있는데 서로 맞지 않는다


def test_invalid_ohlc_sessions_are_reported_per_security_apart_from_suspensions(
    tmp_path: Path,
) -> None:
    root = build_workbench_root(
        tmp_path / "equity",
        invalid_ohlc=("005930", INVALID_SESSION),
        inconsistent_ohlc=("005930", INCONSISTENT_SESSION),
    )
    dataset = EquityDuckdbAdapter(root).load_backtest_dataset(
        BacktestDataQuery(START, date(2024, 1, 12), ("005930:1", "000660:1"), None)
    )

    # 어댑터의 무효 분기 둘(값 없음·서로 맞지 않음)이 모두 세션을 넘긴다(#256).
    assert dataset.invalid_bars == (
        InvalidBarRecord(INVALID_SESSION, "005930:1"),
        InvalidBarRecord(INCONSISTENT_SESSION, "005930:1"),
    )
    traded = {b.session for b in dataset.bars if b.security_id == "005930:1"}
    assert not traded & {INVALID_SESSION, INCONSISTENT_SESSION}
    # 000660 의 정지일(기준가 행)은 무효 행이 아니다.
    assert WB_HALT_DATE not in {item.session for item in dataset.invalid_bars}
    dropped = {item.code: item.message for item in dataset.warnings}
    assert "dropped=2" in dropped["equity.invalid_ohlc_rows_dropped"]


def test_valid_roots_report_no_invalid_bars(tmp_path: Path) -> None:
    root = build_workbench_root(tmp_path / "equity")
    dataset = EquityDuckdbAdapter(root).load_backtest_dataset(
        BacktestDataQuery(START, date(2024, 1, 12), ("005930:1",), None)
    )

    assert dataset.invalid_bars == ()
