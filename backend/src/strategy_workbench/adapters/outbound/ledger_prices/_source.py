"""S07 위험 보고의 가격 이력을 원장(KRX 정본) parquet 에서 읽는다(pyarrow 는 이 어댑터 안에만).

    <equity 루트>/price_adj_daily/MANIFEST.json · v=<빌드>/year=<연>/*.parquet  수정주가 `adj_close`
    <equity 루트>/index_daily/MANIFEST.json     · v=<빌드>/year=<연>/*.parquet  지수 `close_idx`

- 표마다 MANIFEST 의 `current_build` 폴더만 연다. 옛 빌드 폴더가 남아 있어 표 폴더 전체를 읽으면
  날짜가 겹친다.
- 2020-01-02(홀드아웃 하한) 앞 연도 파티션은 열지 않는다. 위험 보고의 가장 이른 구간은 2020-01-22다.
- 원장이 가린 조정 공백(`v_adj_close`)은 다시 판정하지 않는다. 표의 값을 그대로 넘기고, 가격제한
  밖 하루 수익률은 보고 계산(`domain.risk`)이 빼고 목록으로 보인다.
- 읽지 못하면 실패 결과(`PriceHistoryStatus.UNAVAILABLE`)다. 문장에 서버 경로를 싣지 않는다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from strategy_workbench.application.weekly_rebalance.facade.rebalance import (
    PriceHistory,
    PriceHistoryStatus,
)

HISTORY_FLOOR = date(2020, 1, 2)
KOSPI_INDEX_NAME = "코스피"
_PRICES, _INDEX = "price_adj_daily", "index_daily"


class _Unreadable(Exception):
    pass


class LedgerPriceHistory:
    def __init__(self, equity_root: Path) -> None:
        self._root = equity_root

    def history(self, symbols: Sequence[str], since: date) -> PriceHistory:
        since = max(since, HISTORY_FLOOR)
        try:
            prices_build, prices_dir = _current(self._root, _PRICES)
            index_build, index_dir = _current(self._root, _INDEX)
            closes = _closes(prices_dir, symbols, since) if symbols else {}
            index = _index(index_dir, since)
        except OSError as error:
            # 파일 입출력 원문에는 서버 경로가 담긴다 — 파일 이름만 싣는다
            name = Path(str(error.filename)).name if error.filename else ""
            return PriceHistory(
                PriceHistoryStatus.UNAVAILABLE,
                detail=f"ledger prices unreadable — file={name} error={error.strerror}",
            )
        except (_Unreadable, ValueError, KeyError, pa.ArrowException) as error:
            return PriceHistory(PriceHistoryStatus.UNAVAILABLE, detail=f"ledger prices — {error}")
        if not index:
            return PriceHistory(
                PriceHistoryStatus.UNAVAILABLE,
                detail=f"no {KOSPI_INDEX_NAME} rows — {_INDEX}={index_build} since={since}",
            )
        return PriceHistory(
            PriceHistoryStatus.OK,
            closes,
            index,
            f"{_PRICES}={prices_build} {_INDEX}={index_build}",
        )


def _current(root: Path, table: str) -> tuple[str, Path]:
    manifest = json.loads((root / table / "MANIFEST.json").read_text(encoding="utf-8"))
    build = manifest.get("current_build") if isinstance(manifest, dict) else None
    if not isinstance(build, str) or not build:
        raise _Unreadable(f"no current_build — table={table}")
    folder = root / table / f"v={build}"
    if not folder.is_dir():
        raise _Unreadable(f"current build folder missing — table={table} build={build}")
    return build, folder


def _years(folder: Path, since: date) -> list[Path]:
    years: list[Path] = []
    for child in sorted(folder.iterdir()):
        name, _, value = child.name.partition("=")
        if child.is_dir() and name == "year" and value.isdigit() and int(value) >= since.year:
            years.append(child)
    return years


def _closes(folder: Path, symbols: Sequence[str], since: date) -> dict[str, dict[date, float]]:
    out: dict[str, dict[date, float]] = {}
    wanted = sorted(set(symbols))
    for year in _years(folder, since):
        table = pq.read_table(
            year, columns=["ticker", "date", "adj_close"], filters=[("ticker", "in", wanted)]
        )
        for ticker, day, close in zip(
            table.column("ticker").to_pylist(),
            table.column("date").to_pylist(),
            table.column("adj_close").to_pylist(),
            strict=True,
        ):
            if close is None or day < since:
                continue
            series = out.setdefault(str(ticker), {})
            if day in series:
                raise _Unreadable(f"duplicate price row — ticker={ticker} date={day}")
            series[day] = float(close)
    return out


def _index(folder: Path, since: date) -> dict[date, float]:
    out: dict[date, float] = {}
    for year in _years(folder, since):
        table = pq.read_table(
            year,
            columns=["index_name", "date", "close_idx"],
            filters=[("index_name", "=", KOSPI_INDEX_NAME)],
        )
        for day, close in zip(
            table.column("date").to_pylist(), table.column("close_idx").to_pylist(), strict=True
        ):
            if close is None or day < since:
                continue
            if day in out:
                raise _Unreadable(f"duplicate index row — index={KOSPI_INDEX_NAME} date={day}")
            out[day] = float(close)  # close_idx 는 DECIMAL 이다
    return out
