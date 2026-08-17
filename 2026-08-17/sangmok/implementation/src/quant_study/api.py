from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from quant_study.data import download_ohlcv, load_raw_ohlcv, save_raw_ohlcv


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


@app.get("/api/prices/latest", response_model=PriceDataResponse)
def latest_prices(ticker: str = "005930") -> PriceDataResponse:
    latest_path = Path("public/market_data/latest_ohlcv.csv")
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

    raw_path = save_raw_ohlcv(result.frame, ticker=ticker, raw_dir=Path("data/raw"))
    web_path = save_raw_ohlcv(result.frame, ticker=ticker, raw_dir=Path("public/market_data"))
    latest_path = Path("public/market_data/latest_ohlcv.csv")
    latest_path.write_text(web_path.read_text(encoding="utf-8"), encoding="utf-8")

    return DownloadResponse(
        ticker=ticker,
        rows=len(result.frame),
        prices=serialize_prices(result.frame),
        raw_path=str(raw_path),
        web_path=str(web_path),
        latest_path=str(latest_path),
    )
