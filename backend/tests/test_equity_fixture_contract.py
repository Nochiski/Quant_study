"""손 픽스처도 실제 S23 산출 규칙의 시점·구간·잠정판 계약을 따른다."""

from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("duckdb")

from tests.equity_fixture import (
    WB_EVENING_SESSION,
    WB_SPANS,
    WB_SPLIT_DATE,
    build_workbench_root,
    price_adj_table,
    span_table,
    wb_close,
    write_equity_table,
)


def test_상장구간_없는_가격과_계수도_원장_span0에서_함께_접힌다(tmp_path: Path) -> None:
    root = build_workbench_root(tmp_path / "equity")
    write_equity_table(
        root, "security_span", span_table([row for row in WB_SPANS if row[0] != "000660"])
    )
    rows = price_adj_table(root).to_pylist()
    row = next(
        item for item in rows if item["ticker"] == "000660" and item["date"] == WB_SPLIT_DATE
    )
    assert row["cum_share_factor"] == 2
    assert row["adj_close"] == wb_close("000660", WB_SPLIT_DATE) * 2


def test_수정주가_산출도_원천의_잠정판_표식을_전달한다(tmp_path: Path) -> None:
    root = build_workbench_root(tmp_path / "equity", evening_session=WB_EVENING_SESSION)
    rows = price_adj_table(root).to_pylist()
    row = next(
        item for item in rows if item["ticker"] == "005930" and item["date"] == WB_EVENING_SESSION
    )
    assert row["basis"] == "evening"
    assert row["corp_action_pending"] is True
    confirmed = next(
        item for item in rows if item["ticker"] == "005930" and item["date"] == date(2024, 1, 12)
    )
    assert confirmed["basis"] == "krx"
    assert confirmed["corp_action_pending"] is False
