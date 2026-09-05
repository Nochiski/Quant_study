from __future__ import annotations

import argparse
from pathlib import Path
from shutil import copyfile

from quant_study.data import download_ohlcv, save_raw_ohlcv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download daily KRX OHLCV data through pykrx.")
    parser.add_argument("--tickers", nargs="+", default=["005930", "000660", "005380"])
    parser.add_argument("--start", default="20200101")
    parser.add_argument("--end", default="20251231")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--web-dir", type=Path, default=Path("public/market_data"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for ticker in args.tickers:
        result = download_ohlcv(ticker=ticker, start=args.start, end=args.end)
        if not result.ok:
            raise RuntimeError(result.detail)
        if result.frame is None:
            raise RuntimeError(f"download succeeded without frame: ticker={ticker}")
        output_path = save_raw_ohlcv(result.frame, ticker=ticker, raw_dir=args.raw_dir)
        web_path = save_raw_ohlcv(result.frame, ticker=ticker, raw_dir=args.web_dir)
        copyfile(web_path, args.web_dir / "latest_ohlcv.csv")
        print(f"saved raw OHLCV: ticker={ticker} rows={len(result.frame)} path={output_path}")
        print(f"saved web OHLCV: ticker={ticker} rows={len(result.frame)} path={web_path}")


if __name__ == "__main__":
    main()
