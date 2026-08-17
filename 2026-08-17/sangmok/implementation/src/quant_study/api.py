from __future__ import annotations

import math
from datetime import date

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

from quant_study.backtest import RebalanceFrequency, StrategyKind
from quant_study.data import download_ohlcv, load_raw_ohlcv, save_raw_ohlcv
from quant_study.zipline_service import PROJECT_ROOT, ZiplineBacktestConfig, run_zipline_backtest


class DownloadRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    start: str = Field(min_length=8, max_length=10)
    end: str = Field(min_length=8, max_length=10)


class PriceBar(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class PriceDataResponse(BaseModel):
    ticker: str
    rows: int
    prices: list[PriceBar]


class DownloadResponse(PriceDataResponse):
    raw_path: str
    web_path: str
    latest_path: str


class BacktestRequest(BaseModel):
    ticker: str = Field(pattern=r"^\d{6}$")
    start: str
    end: str
    strategy: StrategyKind
    short_window: int = Field(ge=1, le=250)
    long_window: int = Field(ge=2, le=500)
    rebalance: RebalanceFrequency
    allocation: float = Field(ge=0, le=1)
    capital: float = Field(gt=0)
    fee_bps: float = Field(ge=0, le=1_000)
    slippage_bps: float = Field(ge=0, le=1_000)

    @model_validator(mode="after")
    def validate_period_and_windows(self) -> BacktestRequest:
        try:
            start_date = date.fromisoformat(self.start)
            end_date = date.fromisoformat(self.end)
        except ValueError as exc:
            raise ValueError("start와 end는 YYYY-MM-DD 형식이어야 합니다.") from exc
        if start_date > end_date:
            raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
        if self.short_window >= self.long_window:
            raise ValueError("단기 MA는 장기 MA보다 작아야 합니다.")
        return self


class BacktestPoint(BaseModel):
    date: str
    portfolio_value: float
    price: float | None
    invested: float
    benchmark_value: float | None
    benchmark_period_return: float | None
    algorithm_period_return: float | None
    max_drawdown: float | None
    sharpe: float | None
    sortino: float | None
    algo_volatility: float | None


class BacktestResponse(BaseModel):
    engine: str
    ticker: str
    strategy: StrategyKind
    rows: int
    points: list[BacktestPoint]
    result_path: str


app = FastAPI(title="Quant Study Local API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:4173",
        "http://127.0.0.1:4321",
        "http://localhost:4173",
        "http://localhost:4321",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def serialize_prices(frame) -> list[PriceBar]:
    return [
        PriceBar(
            date=index.date().isoformat(),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=int(row["volume"]),
        )
        for index, row in frame.iterrows()
    ]


def finite_float(value) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def serialize_performance(
    frame: pd.DataFrame,
    capital: float,
) -> list[BacktestPoint]:
    points: list[BacktestPoint] = []
    for timestamp, row in frame.iterrows():
        benchmark_return = finite_float(row.get("benchmark_period_return"))
        actual_exposure = finite_float(row.get("gross_leverage"))
        points.append(
            BacktestPoint(
                date=pd.Timestamp(timestamp).date().isoformat(),
                portfolio_value=float(row["portfolio_value"]),
                price=finite_float(row.get("price")),
                invested=(
                    actual_exposure
                    if actual_exposure is not None
                    else finite_float(row.get("invested")) or 0.0
                ),
                benchmark_value=(
                    capital * (1 + benchmark_return)
                    if benchmark_return is not None
                    else None
                ),
                benchmark_period_return=benchmark_return,
                algorithm_period_return=finite_float(row.get("algorithm_period_return")),
                max_drawdown=finite_float(row.get("max_drawdown")),
                sharpe=finite_float(row.get("sharpe")),
                sortino=finite_float(row.get("sortino")),
                algo_volatility=finite_float(row.get("algo_volatility")),
            )
        )
    return points


def add_buy_hold_benchmark(frame: pd.DataFrame) -> pd.DataFrame:
    enriched = frame.copy()
    benchmark = enriched.get("benchmark_period_return")
    if benchmark is not None and benchmark.notna().any():
        return enriched

    price_column = enriched.get("price")
    if price_column is None:
        enriched["benchmark_period_return"] = float("nan")
        return enriched

    prices = pd.to_numeric(price_column, errors="coerce")
    first_price = prices.dropna().iloc[0] if prices.notna().any() else None
    if first_price is None or first_price == 0:
        enriched["benchmark_period_return"] = float("nan")
    else:
        enriched["benchmark_period_return"] = prices / first_price - 1
    return enriched


@app.get("/api/prices/latest", response_model=PriceDataResponse)
def latest_prices(ticker: str = "005930") -> PriceDataResponse:
    latest_path = PROJECT_ROOT / "public/market_data/latest_ohlcv.csv"
    if not latest_path.exists():
        raise HTTPException(status_code=404, detail="저장된 최신 가격 데이터가 없습니다.")

    frame = load_raw_ohlcv(latest_path)
    return PriceDataResponse(ticker=ticker, rows=len(frame), prices=serialize_prices(frame))


@app.post("/api/download", response_model=DownloadResponse)
def download_prices(request: DownloadRequest) -> DownloadResponse:
    ticker = request.ticker.strip()
    result = download_ohlcv(ticker=ticker, start=request.start, end=request.end)
    if not result.ok or result.frame is None:
        raise HTTPException(status_code=502, detail=result.detail)

    raw_path = save_raw_ohlcv(result.frame, ticker=ticker, raw_dir=PROJECT_ROOT / "data/raw")
    web_path = save_raw_ohlcv(
        result.frame,
        ticker=ticker,
        raw_dir=PROJECT_ROOT / "public/market_data",
    )
    latest_path = PROJECT_ROOT / "public/market_data/latest_ohlcv.csv"
    latest_path.write_text(web_path.read_text(encoding="utf-8"), encoding="utf-8")

    return DownloadResponse(
        ticker=ticker,
        rows=len(result.frame),
        prices=serialize_prices(result.frame),
        raw_path=str(raw_path),
        web_path=str(web_path),
        latest_path=str(latest_path),
    )


@app.post("/api/backtests", response_model=BacktestResponse)
def run_backtest(request: BacktestRequest) -> BacktestResponse:
    config = ZiplineBacktestConfig(
        ticker=request.ticker,
        start=request.start,
        end=request.end,
        strategy=request.strategy,
        short_window_days=request.short_window,
        long_window_days=request.long_window,
        rebalance=request.rebalance,
        allocation=request.allocation,
        capital_base_krw=request.capital,
        fee_bps=request.fee_bps,
        slippage_bps=request.slippage_bps,
    )
    try:
        performance = run_zipline_backtest(config)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Zipline 실행 실패: {exc}") from exc

    performance = add_buy_hold_benchmark(performance)
    result_path = PROJECT_ROOT / "public/backtests/latest_performance.csv"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    performance.to_csv(result_path)
    return BacktestResponse(
        engine="zipline-reloaded",
        ticker=request.ticker,
        strategy=request.strategy,
        rows=len(performance),
        points=serialize_performance(performance, request.capital),
        result_path=str(result_path),
    )
