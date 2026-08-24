"""커스텀 backtest_engine과 Zipline을 같은 데이터·전략·비용으로 실행해 대조한다.

사용법 (implementation 디렉터리에서):
    uv run python scripts/compare_engines.py

결과: 콘솔 리포트 + 리포 루트 tests/manual/에 JSON·HTML 리포트 생성.
tolerance 초과 시 exit code 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backtest_engine.data.csv_loader import OhlcPolicy, load_bars_csv
from zipline.utils.calendar_utils import get_calendar as zipline_get_calendar

from quant_study.compare_report import ScenarioComparison, render_html, to_json_payload
from quant_study.data import load_raw_ohlcv
from quant_study.engine_compare import (
    CompareParams,
    CompareScenario,
    make_compare_instrument,
    run_engine_side,
    run_zipline_side,
)
from quant_study.engine_diff import diff_equity_curves, format_report
from quant_study.zipline_service import WEB_BUNDLE, ZIPLINE_RUN_LOCK, prepare_web_bundle

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parents[2]
DEFAULT_CSV = PROJECT_ROOT.parent / "result/html/app/market_data/005930.csv"
DEFAULT_OUT_DIR = REPO_ROOT / "tests/manual"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare custom engine vs Zipline.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--ticker", default="005930")
    parser.add_argument("--short-window", type=int, default=20)
    parser.add_argument("--long-window", type=int, default=60)
    parser.add_argument("--allocation", type=float, default=0.7)
    parser.add_argument("--capital-base", type=float, default=10_000_000.0)
    parser.add_argument("--fee-bps", type=float, default=15.0)
    parser.add_argument("--rel-tol", type=float, default=1e-6)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    params = CompareParams(
        ticker=args.ticker,
        short_window_days=args.short_window,
        long_window_days=args.long_window,
        allocation=args.allocation,
        capital_base_krw=args.capital_base,
        fee_bps=args.fee_bps,
    )

    loaded = load_bars_csv(
        args.csv, make_compare_instrument(args.ticker), ohlc_policy=OhlcPolicy.CLAMP
    )
    if not loaded.ok:
        print(f"failed to load bars: status={loaded.status.value} detail={loaded.detail}")
        return 1
    if loaded.repaired_rows:
        print(f"[data prep] clamped OHLC on {loaded.repaired_rows} rows")
    if loaded.dropped_rows:
        print(f"[data prep] dropped {loaded.dropped_rows} halt rows (non-positive price)")
    frame = load_raw_ohlcv(args.csv)

    # Zipline이 실제로 쓰는 XKRX 캘린더에 없는 거래일은 ingest에서 버려져
    # 두 엔진이 서로 다른 종가 시계열을 보게 된다. 같은 캘린더로 교집합만 공급한다.
    calendar = zipline_get_calendar("XKRX")
    session_dates = {
        ts.date()
        for ts in calendar.sessions_in_range(frame.index.min(), frame.index.max())
    }
    csv_only = [ts for ts in frame.index if ts.date() not in session_dates]
    if csv_only:
        print(f"[data prep] dropped {len(csv_only)} CSV-only sessions not in XKRX calendar")
        frame = frame.loc[[ts for ts in frame.index if ts.date() in session_dates]]
    bars = tuple(bar for bar in loaded.bars if bar.ts.date() in session_dates)

    comparisons: list[ScenarioComparison] = []
    with ZIPLINE_RUN_LOCK:
        environ = prepare_web_bundle(args.ticker, frame)
        for scenario in (CompareScenario.BUY_HOLD, CompareScenario.GOLDEN_CROSS):
            engine_curve = run_engine_side(bars, scenario, params)
            zipline_curve = run_zipline_side(frame, scenario, params, WEB_BUNDLE, environ)
            report = diff_equity_curves(engine_curve, zipline_curve, rel_tol=args.rel_tol)
            comparisons.append(
                ScenarioComparison(
                    scenario=scenario.value,
                    engine_curve=engine_curve,
                    zipline_curve=zipline_curve,
                    report=report,
                )
            )
            print(format_report(scenario.value, report))

    params_dict: dict[str, object] = {
        "ticker": params.ticker,
        "csv": str(args.csv.relative_to(REPO_ROOT)),
        "sessions": len(bars),
        "csv_only_sessions_dropped": len(csv_only),
        "short_window_days": params.short_window_days,
        "long_window_days": params.long_window_days,
        "allocation": params.allocation,
        "capital_base_krw": params.capital_base_krw,
        "fee_bps": params.fee_bps,
        "rel_tol": args.rel_tol,
        "clamped_rows": loaded.repaired_rows,
        "halt_rows_dropped": loaded.dropped_rows,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / "compare_results.json"
    html_path = args.out_dir / "compare_report.html"
    json_path.write_text(
        json.dumps(to_json_payload(comparisons, params_dict), ensure_ascii=False, indent=2)
    )
    html_path.write_text(render_html(comparisons, params_dict))
    print(f"saved JSON report: {json_path}")
    print(f"saved HTML report: {html_path}")

    return 0 if all(comparison.report.ok for comparison in comparisons) else 1


if __name__ == "__main__":
    sys.exit(main())
