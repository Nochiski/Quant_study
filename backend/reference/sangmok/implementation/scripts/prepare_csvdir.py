from __future__ import annotations

import argparse
from pathlib import Path

from quant_study.data import load_raw_ohlcv, save_zipline_daily_csv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert raw pykrx CSV files to Zipline csvdir files."
    )
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--csvdir-root", type=Path, default=Path("data/zipline_csvdir"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw_paths = sorted(args.raw_dir.glob("*.csv"))
    if not raw_paths:
        raise RuntimeError(f"no raw CSV files found: raw_dir={args.raw_dir}")

    for raw_path in raw_paths:
        ticker = raw_path.stem
        frame = load_raw_ohlcv(raw_path)
        output_path = save_zipline_daily_csv(frame, ticker=ticker, csvdir_root=args.csvdir_root)
        print(f"saved zipline csvdir: ticker={ticker} rows={len(frame)} path={output_path}")


if __name__ == "__main__":
    main()
