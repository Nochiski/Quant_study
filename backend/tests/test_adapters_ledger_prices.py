"""S07 위험 보고의 원장 가격 이력 어댑터. 임시 폴더에 직접 만든 원장 표만 읽는다."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from strategy_workbench.adapters.outbound.ledger_prices.facade.prices import LedgerPriceHistory
from strategy_workbench.application.weekly_rebalance.facade.rebalance import PriceHistoryStatus


def _table(root: Path, table: str, builds: dict[str, dict[int, pa.Table]], current: str) -> None:
    folder = root / table
    for build, years in builds.items():
        for year, rows in years.items():
            part = folder / f"v={build}" / f"year={year}"
            part.mkdir(parents=True)
            pq.write_table(rows, part / "part0.parquet")
            (part / "_meta.json").write_text("{}", encoding="utf-8")
    (folder / "MANIFEST.json").write_text(json.dumps({"current_build": current}), encoding="utf-8")


def _prices(rows: list[tuple[str, date, float | None]]) -> pa.Table:
    return pa.table(
        {
            "ticker": [r[0] for r in rows],
            "date": pa.array([r[1] for r in rows], pa.date32()),
            "adj_close": pa.array([r[2] for r in rows], pa.float64()),
        }
    )


def _index(rows: list[tuple[str, date, str]]) -> pa.Table:
    return pa.table(
        {
            "index_name": [r[0] for r in rows],
            "date": pa.array([r[1] for r in rows], pa.date32()),
            "close_idx": pa.array([Decimal(r[2]) for r in rows], pa.decimal128(10, 2)),
        }
    )


def _ledger(root: Path) -> Path:
    _table(
        root,
        "price_adj_daily",
        {
            "m_old": {2026: _prices([("A", date(2026, 10, 29), 1.0)])},
            "m_new": {
                2019: _prices([("A", date(2019, 12, 30), 9.0)]),
                2020: _prices([("A", date(2020, 1, 2), 10.0), ("B", date(2020, 1, 2), 5.0)]),
                2026: _prices(
                    [
                        ("A", date(2026, 10, 29), 20.0),
                        ("A", date(2026, 10, 30), 21.0),
                        ("B", date(2026, 10, 30), None),
                        ("C", date(2026, 10, 30), 7.0),
                    ]
                ),
            },
        },
        "m_new",
    )
    _table(
        root,
        "index_daily",
        {
            "i_1": {
                2020: _index([("코스피", date(2020, 1, 2), "2175.17")]),
                2026: _index(
                    [
                        ("코스피", date(2026, 10, 30), "5730.74"),
                        ("코스닥", date(2026, 10, 30), "1.00"),
                    ]
                ),
            }
        },
        "i_1",
    )
    return root


def test_reads_only_the_current_build_from_the_holdout_floor(tmp_path: Path) -> None:
    history = LedgerPriceHistory(_ledger(tmp_path)).history(["A", "B", "D"], date(2019, 6, 1))
    assert history.status is PriceHistoryStatus.OK
    # 옛 빌드(1.0)·2019 파티션·빈 값·요청하지 않은 종목은 없다. 없는 종목은 지어내지 않는다
    assert history.closes == {
        "A": {date(2020, 1, 2): 10.0, date(2026, 10, 29): 20.0, date(2026, 10, 30): 21.0},
        "B": {date(2020, 1, 2): 5.0},
    }
    assert history.index == {date(2020, 1, 2): 2175.17, date(2026, 10, 30): 5730.74}
    assert history.source == "price_adj_daily=m_new index_daily=i_1"


def test_since_skips_earlier_years_and_dates(tmp_path: Path) -> None:
    history = LedgerPriceHistory(_ledger(tmp_path)).history(["A"], date(2026, 10, 30))
    assert history.closes == {"A": {date(2026, 10, 30): 21.0}}
    assert list(history.index) == [date(2026, 10, 30)]


def test_missing_manifest_or_build_is_unavailable_without_server_paths(tmp_path: Path) -> None:
    root = _ledger(tmp_path)
    (root / "index_daily" / "MANIFEST.json").write_text(
        json.dumps({"current_build": "i_gone"}), encoding="utf-8"
    )
    history = LedgerPriceHistory(root).history(["A"], date(2026, 1, 1))
    assert history.status is PriceHistoryStatus.UNAVAILABLE
    assert "build=i_gone" in history.detail and str(tmp_path) not in history.detail
    empty = LedgerPriceHistory(tmp_path / "nothing").history(["A"], date(2026, 1, 1))
    assert empty.status is PriceHistoryStatus.UNAVAILABLE
    assert "file=MANIFEST.json" in empty.detail and str(tmp_path) not in empty.detail
