from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pandas as pd
from pykrx import stock

RAW_COLUMNS = {
    "시가": "open",
    "고가": "high",
    "저가": "low",
    "종가": "close",
    "거래량": "volume",
}
ZIPLINE_COLUMNS = ["date", "open", "high", "low", "close", "volume", "dividend", "split"]


class LoadStatus(Enum):
    OK = "ok"
    NO_DATA = "no_data"
    SOURCE_ERROR = "source_error"


@dataclass(frozen=True, eq=False)
class PriceLoadResult:
    frame: pd.DataFrame | None
    status: LoadStatus
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is LoadStatus.OK


def normalize_yyyymmdd(value: str) -> str:
    compact_value = value.replace("-", "")
    if len(compact_value) != 8 or not compact_value.isdigit():
        raise ValueError(f"date must be YYYYMMDD or YYYY-MM-DD: value={value!r}")
    return compact_value


def download_ohlcv(ticker: str, start: str, end: str) -> PriceLoadResult:
    start_yyyymmdd = normalize_yyyymmdd(start)
    end_yyyymmdd = normalize_yyyymmdd(end)

    try:
        frame = stock.get_market_ohlcv_by_date(start_yyyymmdd, end_yyyymmdd, ticker)
    except Exception as exc:  # noqa: BLE001
        return PriceLoadResult(
            frame=None,
            status=LoadStatus.SOURCE_ERROR,
            detail=(
                "pykrx OHLCV request failed: "
                f"ticker={ticker} start={start_yyyymmdd} end={end_yyyymmdd} error={exc!r}"
            ),
        )

    if frame.empty:
        return PriceLoadResult(
            frame=None,
            status=LoadStatus.NO_DATA,
            detail=(
                "no pykrx rows returned: "
                f"ticker={ticker} start={start_yyyymmdd} end={end_yyyymmdd}"
            ),
        )

    normalized = frame.rename(columns=RAW_COLUMNS).loc[:, list(RAW_COLUMNS.values())]
    normalized.index = pd.to_datetime(normalized.index)
    normalized.index.name = "date"
    normalized = normalized.sort_index()
    return PriceLoadResult(frame=normalized, status=LoadStatus.OK)


def save_raw_ohlcv(frame: pd.DataFrame, ticker: str, raw_dir: Path) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    output_path = raw_dir / f"{ticker}.csv"
    frame.to_csv(output_path, index=True)
    return output_path


def load_raw_ohlcv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["date"])
    frame = frame.set_index("date").sort_index()
    missing_columns = set(RAW_COLUMNS.values()) - set(frame.columns)
    if missing_columns:
        raise ValueError(
            f"raw OHLCV columns missing: path={path} missing={sorted(missing_columns)}"
        )
    return frame.loc[:, list(RAW_COLUMNS.values())]


def to_zipline_daily_csv(frame: pd.DataFrame) -> pd.DataFrame:
    missing_columns = set(RAW_COLUMNS.values()) - set(frame.columns)
    if missing_columns:
        raise ValueError(f"cannot build zipline csvdir frame: missing={sorted(missing_columns)}")

    csv_frame = frame.loc[:, list(RAW_COLUMNS.values())].copy()
    csv_frame.index = pd.to_datetime(csv_frame.index)
    csv_frame = csv_frame.sort_index()
    csv_frame["dividend"] = 0.0
    csv_frame["split"] = 1.0
    csv_frame = csv_frame.reset_index()
    csv_frame["date"] = pd.to_datetime(csv_frame["date"]).dt.strftime("%Y-%m-%d")
    return csv_frame.loc[:, ZIPLINE_COLUMNS]


def save_zipline_daily_csv(frame: pd.DataFrame, ticker: str, csvdir_root: Path) -> Path:
    daily_dir = csvdir_root / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    output_path = daily_dir / f"{ticker}.csv"
    to_zipline_daily_csv(frame).to_csv(output_path, index=False)
    return output_path
