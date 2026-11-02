"""`MarketIndexPort`: duckdb 는 원장 `index_daily` 종가를 그대로, mock 은 지수를 지어내지 않는다.

지수는 security 축이 아니라(GAP-09) 백테스트 bar 가 아니고 결과 옆 시장 참고선만 읽는다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)


def test_mock_answers_a_reason_instead_of_inventing_indices() -> None:
    loaded = MockEquityDataAdapter.demo().load_market_index_closes(
        ("코스피",), date(2024, 1, 2), date(2024, 1, 31)
    )
    assert loaded.closes == {}
    assert loaded.unavailable_reason is not None


def test_duckdb_reads_index_closes_in_range_and_reports_a_missing_table(tmp_path: Path) -> None:
    pytest.importorskip("duckdb", reason="backend optional extra `equity` (uv sync --extra equity)")
    import pyarrow as pa

    from strategy_workbench.adapters.outbound.equity_duckdb.facade.provider import (
        EquityDuckdbAdapter,
    )
    from tests.equity_fixture import build_workbench_root, write_equity_table

    without = EquityDuckdbAdapter(
        build_workbench_root(tmp_path / "plain", catalog=False)
    ).load_market_index_closes(("코스피",), date(2024, 1, 2), date(2024, 1, 3))
    assert (without.closes, without.unavailable_reason) == (
        {},
        "원장에 시장 지수 표(index_daily)가 없습니다.",
    )

    root = build_workbench_root(tmp_path / "indexed", catalog=False)
    write_equity_table(
        root,
        "index_daily",
        pa.table(
            {
                "index_name": ["코스피", "코스피", "코스피", "코스닥", "코스피 200"],
                "date": [
                    date(2024, 1, 2),
                    date(2024, 1, 3),
                    date(2024, 1, 4),
                    date(2024, 1, 3),
                    date(2024, 1, 3),
                ],
                "close_idx": [2_600.5, 2_610.0, None, 850.0, 350.0],
            }
        ),
        year_column="date",
    )
    loaded = EquityDuckdbAdapter(root).load_market_index_closes(
        ("코스피", "코스닥"), date(2024, 1, 3), date(2024, 1, 4)
    )
    # 기간 밖(1/2)·종가 없는 행(1/4)·묻지 않은 지수(코스피 200)는 싣지 않는다.
    assert loaded.unavailable_reason is None
    assert loaded.closes == {
        "코스피": {date(2024, 1, 3): 2_610.0},
        "코스닥": {date(2024, 1, 3): 850.0},
    }
