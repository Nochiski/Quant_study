from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

import pandas as pd
from zipline.data import bundles
from zipline.data.bundles.csvdir import csvdir_equities

from quant_study.backtest import RebalanceFrequency, StrategyKind, run_moving_average_backtest
from quant_study.data import download_ohlcv, save_raw_ohlcv, save_zipline_daily_csv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_BUNDLE = "krx-web"
WEB_CSV_ROOT = PROJECT_ROOT / "data/web_zipline_csvdir"
ZIPLINE_ROOT = PROJECT_ROOT / ".zipline"

# Zipline bundle registry와 ingest 디렉터리는 프로세스 전역 상태이므로 직렬화한다.
ZIPLINE_RUN_LOCK = Lock()


@dataclass(frozen=True)
class ZiplineBacktestConfig:
    ticker: str
    start: str
    end: str
    strategy: StrategyKind
    short_window_days: int
    long_window_days: int
    rebalance: RebalanceFrequency
    allocation: float
    capital_base_krw: float
    fee_bps: float
    slippage_bps: float


def warmup_start(start: str, long_window_days: int) -> str:
    start_date = pd.Timestamp(start)
    warmup_days = max(45, long_window_days * 3)
    return (start_date - pd.Timedelta(days=warmup_days)).strftime("%Y%m%d")


def zipline_environ() -> dict[str, str]:
    environ = dict(os.environ)
    environ["ZIPLINE_ROOT"] = str(ZIPLINE_ROOT)
    environ["KRX_CSV_DIR"] = str(WEB_CSV_ROOT)
    return environ


def prepare_web_bundle(ticker: str, frame: pd.DataFrame) -> dict[str, str]:
    daily_dir = WEB_CSV_ROOT / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    for csv_path in daily_dir.glob("*.csv"):
        csv_path.unlink()
    save_zipline_daily_csv(frame, ticker=ticker, csvdir_root=WEB_CSV_ROOT)

    if WEB_BUNDLE in bundles.bundles:
        bundles.unregister(WEB_BUNDLE)
    bundles.register(
        WEB_BUNDLE,
        csvdir_equities(["daily"], str(WEB_CSV_ROOT)),
        calendar_name="XKRX",
    )

    environ = zipline_environ()
    bundles.ingest(WEB_BUNDLE, environ=environ, show_progress=False)
    bundles.clean(WEB_BUNDLE, keep_last=1, environ=environ)
    return environ


def run_zipline_backtest(config: ZiplineBacktestConfig) -> pd.DataFrame:
    with ZIPLINE_RUN_LOCK:
        result = download_ohlcv(
            ticker=config.ticker,
            start=warmup_start(config.start, config.long_window_days),
            end=config.end,
        )
        if not result.ok or result.frame is None:
            raise RuntimeError(result.detail or "pykrx 가격 데이터를 가져오지 못했습니다.")

        save_raw_ohlcv(
            result.frame,
            ticker=config.ticker,
            raw_dir=PROJECT_ROOT / "data/raw",
        )
        environ = prepare_web_bundle(config.ticker, result.frame)
        return run_moving_average_backtest(
            ticker=config.ticker,
            start=config.start,
            end=config.end,
            bundle=WEB_BUNDLE,
            short_window_days=config.short_window_days,
            long_window_days=config.long_window_days,
            capital_base_krw=config.capital_base_krw,
            strategy=config.strategy,
            rebalance=config.rebalance,
            allocation=config.allocation,
            fee_bps=config.fee_bps,
            slippage_bps=config.slippage_bps,
            environ=environ,
        )
