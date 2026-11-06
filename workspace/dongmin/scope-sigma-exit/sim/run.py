"""갈래를 돌려 결과 JSON 을 남긴다.

    python -m sim.run extras                      # 지수 시가·종가·소속 시장·스팩 판 만들기(한 번)
    python -m sim.run run --signal A --all        # 기준선 + 갈래 3 + 민감도 2

backend 가상환경에서 `workspace/dongmin/scope-sigma-exit` 를 작업 폴더로 실행한다. 시장 판 캐시·신호·원장
고정본은 10-26 매매 규칙 시험 폴더의 것을 읽기만 한다.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import date
from pathlib import Path

import duckdb
import numpy as np

from sim.configs import ARMS, BASELINE, SENSITIVITY, V2_ARMS, V2_BASELINE, V2_DIAGNOSTICS, V3_ARMS, Variant, by_name
from sim.data import Extras, Market, load_extras
from sim.engine import Signal, simulate
from sim.scoring import rescore

PRIOR = Path(os.path.expanduser("~/quant-data/research/2026-10-26-trading-rules"))
RESEARCH = Path(os.path.expanduser("~/quant-data/research/2026-10-30-scope-sigma-exit"))
LEDGER = PRIOR / "ledger" / "equity"
CACHE = PRIOR / "market_cache.npz"
EXTRAS = RESEARCH / "extras.npz"
SIGNAL_A = PRIOR / "signal_a" / "scores.parquet"
SIGNAL_B = PRIOR / "signal_b" / "score_history.csv"
STUDY_START = date(2020, 1, 2)
SIGNAL_B_START = date(2026, 4, 3)


def build_extras() -> None:
    z = np.load(CACHE, allow_pickle=False)
    sessions = tuple(date.fromisoformat(s) for s in z["sessions"])
    tickers = tuple(str(t) for t in z["tickers"])
    ex = load_extras(LEDGER, sessions, tickers)
    np.savez_compressed(
        EXTRAS,
        snapshot_id=str(z["snapshot_id"]),
        index_open=ex.index_open,
        index_close=ex.index_close,
        kosdaq=ex.kosdaq,
        spac=ex.spac,
    )
    print(
        f"extras {EXTRAS} snapshot={z['snapshot_id']} sessions={len(sessions)} tickers={len(tickers)} "
        f"kosdaq_cells={int(ex.kosdaq.sum())} spac_cells={int(ex.spac.sum())}"
    )


def load_market() -> tuple[Market, Extras]:
    z = np.load(CACHE, allow_pickle=False)
    e = np.load(EXTRAS, allow_pickle=False)
    if str(e["snapshot_id"]) != str(z["snapshot_id"]):
        raise ValueError(f"extras snapshot differs — cache={z['snapshot_id']} extras={e['snapshot_id']}")
    ex = Extras(index_open=e["index_open"], index_close=e["index_close"], kosdaq=e["kosdaq"], spac=e["spac"])
    market = Market(
        snapshot_id=str(z["snapshot_id"]),
        sessions=tuple(date.fromisoformat(s) for s in z["sessions"]),
        tickers=tuple(str(t) for t in z["tickers"]),
        adj_open=z["adj_open"],
        adj_close=z["adj_close"],
        volume=z["volume"],
        adv20=z["adv20"],
        unadjusted_jump=z["unadjusted_jump"],
        last_bar=z["last_bar"],
        index_sessions=tuple(date.fromisoformat(s) for s in z["index_sessions"]),
        kospi=z["kospi"],
        kospi200=z["kospi200"],
        index_open=ex.index_open[:2],
        index_close=ex.index_close[:2],
        kosdaq=ex.kosdaq,
    )
    return market, ex


_B_FACTORS = {"momentum": "momentum_score", "revision": "revision_score", "flow": "flow_score",
              "quality": "quality_score", "valuation": "valuation_score"}


def load_signals(
    which: str, market: Market, spac: np.ndarray, variant: Variant | None = None
) -> tuple[dict[date, Signal], int]:
    """신호일 → 순위표. 그날 스팩인 종목은 뺀다(사전등록 "자료" 절). 반환 둘째 값은 뺀 행 수.

    갈래에 팩터 가중이 있으면(PREREG_v3) 팩터별 점수로 합성 점수·순위를 다시 낸다.
    """
    weights = None if variant is None else (variant.factor_weights if which == "A" else variant.factor_weights_b)
    required = () if variant is None else variant.required_factors
    rows: dict[date, list[tuple[int, str, float]]] = {}
    factors: dict[date, list[tuple[str, dict[str, float | None]]]] = {}
    if which == "A":
        con = duckdb.connect()
        for d, t, s, r, mo, fl, qu, va in con.execute(
            f"select date, ticker, composite, rank, momentum, flow, quality, valuation "
            f"from read_parquet('{SIGNAL_A}') where date >= ?",
            [STUDY_START],
        ).fetchall():
            if weights is None:
                rows.setdefault(d, []).append((int(r), t, float(s)))
            else:
                factors.setdefault(d, []).append((t, {"momentum": mo, "flow": fl, "quality": qu, "valuation": va}))
    elif which == "B":
        with SIGNAL_B.open() as f:
            for rec in csv.DictReader(f):
                d = date.fromisoformat(rec["score_date"])
                if weights is None:
                    if rec["composite_score"] in ("", None):
                        continue
                    rows.setdefault(d, []).append((int(rec["rank"]), rec["stock_code"], float(rec["composite_score"])))
                else:
                    vals = {k: (float(rec[c]) if rec[c] not in ("", None) else None) for k, c in _B_FACTORS.items()}
                    factors.setdefault(d, []).append((rec["stock_code"], vals))
    else:
        raise ValueError(f"unknown signal — {which}")
    if weights is not None:
        for d, fs in factors.items():
            rows[d] = [(k, t, s) for k, (t, s) in enumerate(rescore(fs, weights, required=required), start=1)]
    s_index, t_index = market.session_index(), market.ticker_index()
    dropped = 0
    out: dict[date, Signal] = {}
    for d, v in rows.items():
        i = s_index.get(d)
        kept = []
        for r, t, s in sorted(v):
            j = t_index.get(t)
            if i is not None and j is not None and spac[i, j]:
                dropped += 1
                continue
            kept.append((t, s))
        out[d] = Signal(ranked=tuple(kept))
    return out, dropped


def _tax_schedule():
    from strategy_workbench.domain.backtest._krx_tax import STATUTORY_SELL_TAX_BPS

    (schedule,) = STATUTORY_SELL_TAX_BPS.values()  # 시장은 KRX 하나뿐
    return schedule


def _run_one(args):
    variant, which, out_dir = args
    market, ex = load_market()
    signals, dropped = load_signals(which, market, ex.spac, variant)
    start = STUDY_START if which == "A" else SIGNAL_B_START
    end = min(max(signals), market.sessions[-1])
    tax = _tax_schedule() if variant.sell_tax else ()
    result = simulate(variant, market, signals, start=start, end=end, tax_schedule=tax)
    payload = {
        "variant": asdict(variant),
        "signal": which,
        "snapshot_id": market.snapshot_id,
        "spac_rows_dropped": dropped,
        "sessions": [d.isoformat() for d in result.sessions],
        "equity": [float(x) for x in result.equity],
        "cash_ratio": [float(x) for x in result.cash_ratio],
        "traded_krw": result.traded_krw,
        "fees_krw": result.fees_krw,
        "taxes_krw": result.taxes_krw,
        "slippage_krw": result.slippage_krw,
        "n_orders": result.n_orders,
        "n_unfilled": result.n_unfilled,
        "n_jump_neutralized": result.n_jump_neutralized,
        "n_delisted_sales": result.n_delisted_sales,
        "rule_sales": result.rule_sales,
        "n_sigma_missing": result.n_sigma_missing,
        "n_small_buys": result.n_small_buys,
        "rebuys": result.rebuys,
        "rule_events": [
            [d.isoformat(), t, why, z, w, a.isoformat() if a else None, sg] for d, t, why, z, w, a, sg in result.rule_events
        ],
        "holdings_by_week": {d.isoformat(): sorted(h) for d, h in result.holdings_by_week.items()},
    }
    path = out_dir / f"{variant.name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False))
    return variant.name, payload["equity"][-1]


def main(argv: list[str]) -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("extras")
    r = sub.add_parser("run")
    r.add_argument("--signal", choices=["A", "B"], required=True)
    r.add_argument("--names", nargs="*", default=[])
    r.add_argument("--all", action="store_true")
    r.add_argument("--workers", type=int, default=3)
    r.add_argument("--round", type=int, choices=[1, 2, 3], default=1, help="--all 로 돌릴 사전등록 판")
    a = p.parse_args(argv)
    if a.cmd == "extras":
        build_extras()
        return
    table = by_name()
    rounds = {
        1: [BASELINE.name, *[v.name for v in (*ARMS, *SENSITIVITY)]],
        2: [V2_BASELINE.name, *[v.name for v in (*V2_ARMS, *V2_DIAGNOSTICS)]],
        3: [v.name for v in V3_ARMS],
    }
    names = rounds[a.round] if a.all else a.names
    out_dir = RESEARCH / "results" / f"signal_{a.signal}"
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(table[n], a.signal, out_dir) for n in names]
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        for name, last in pool.map(_run_one, jobs):
            print(f"{name}\t{last:,.0f}")


if __name__ == "__main__":
    main(sys.argv[1:])
