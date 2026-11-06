"""결과 JSON 을 읽어 지표를 내고 사전등록 "판정" 절로 판정한다.

    python -m sim.judge --signal A      # 판정
    python -m sim.judge --signal B      # 확인용: 지표만(판정 안 함)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date

import numpy as np

from sim.configs import (
    ARMS,
    BASELINE,
    DIAGNOSTICS,
    SENSITIVITY,
    V2_ARMS,
    V2_BASELINE,
    V2_DIAGNOSTICS,
    V2_SPLIT_DIAGNOSTICS,
    V3_ARMS,
    Variant,
)
from sim.engine import START_CAPITAL, Result
from sim.metrics import CYCLES, cagr, cagr_between, holm, jaccard_mean, mdd, sharpe, turnover
from sim.run import RESEARCH, load_market

MIN_EFFECT = 0.02  # 판정 1: CAGR 차 +2%p
MIN_T = 2.0  # 판정 2
ALPHA = 0.05
INDICES = ("코스피", "코스닥", "코스피200")  # Extras.index_close 행 순서와 같다


def _load(name: str, signal: str) -> tuple[dict, Result]:
    d = json.loads((RESEARCH / "results" / f"signal_{signal}" / f"{name}.json").read_text())
    r = Result(
        variant=Variant(**d["variant"]),
        sessions=tuple(date.fromisoformat(s) for s in d["sessions"]),
        equity=np.array(d["equity"]),
        traded_krw=d["traded_krw"],
        fees_krw=d["fees_krw"],
        taxes_krw=d["taxes_krw"],
        slippage_krw=d["slippage_krw"],
    )
    r.holdings_by_week = {date.fromisoformat(k): frozenset(v) for k, v in d["holdings_by_week"].items()}
    return d, r


def _index_result(name: str, sessions: tuple[date, ...], level: np.ndarray) -> Result:
    return Result(variant=Variant(name=name, axis="index"), sessions=sessions, equity=level / level[0] * START_CAPITAL)


def monthly_returns(r: Result) -> np.ndarray:
    """월말 장 마감 자산 사이의 수익률. 첫 값은 시작일 → 첫 월말."""
    s = r.sessions
    ends = [i for i in range(len(s)) if i + 1 == len(s) or (s[i + 1].year, s[i + 1].month) != (s[i].year, s[i].month)]
    marks = [0, *ends]
    return np.array([r.equity[b] / r.equity[a] - 1.0 for a, b in zip(marks, marks[1:])])


def paired_t(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """월별 수익률 차(a − b)의 짝지은 t 와 정규 근사 양측 p."""
    diff = a - b
    sd = float(np.std(diff, ddof=1))
    if sd <= 0:
        return float("nan"), float("nan")
    t = float(np.mean(diff)) / (sd / math.sqrt(len(diff)))
    return t, math.erfc(abs(t) / math.sqrt(2))


def summarize(raw: dict | None, r: Result, base_rate, kospi: Result) -> dict:
    out = {
        "name": r.variant.name,
        "cagr": cagr(r),
        "mdd": mdd(r.equity),
        "sharpe": sharpe(r, base_rate),
        "final_krw": float(r.equity[-1]),
        "vs_kospi_cagr": cagr(r) - cagr(kospi),
        "mdd_vs_kospi": mdd(r.equity) - mdd(kospi.equity),
        "cycles": {k: cagr_between(r, a, b) for k, (a, b) in CYCLES.items() if a >= r.sessions[0]},
    }
    if raw is None:
        return out
    rebuys = np.array(raw["rebuys"])
    out |= {
        "turnover": turnover(r),
        "cost_krw": raw["fees_krw"] + raw["taxes_krw"] + raw["slippage_krw"],
        "n_orders": raw["n_orders"],
        "rule_sales": raw["rule_sales"],
        "n_sigma_missing": raw["n_sigma_missing"],
        "n_small_buys": raw["n_small_buys"],
        "rebuys_n": int(len(rebuys)),
        "rebuys_mean": float(rebuys.mean()) if len(rebuys) else float("nan"),
        "rebuys_higher_share": float((rebuys > 0).mean()) if len(rebuys) else float("nan"),
        "cash_mean": float(np.mean(raw["cash_ratio"])),
        "spac_rows_dropped": raw["spac_rows_dropped"],
    }
    return out


def main(argv: list[str]) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--signal", choices=["A", "B"], required=True)
    p.add_argument("--round", type=int, choices=[1, 2, 3], default=1)
    a = p.parse_args(argv)
    # 판별 (기준선, 판정 갈래, 함께 적는 줄). 2차는 1차 기준선·전체 규칙을 비교용으로 함께 싣는다
    plans = {
        1: (BASELINE, ARMS, (*SENSITIVITY, *DIAGNOSTICS)),
        2: (V2_BASELINE, V2_ARMS, (*V2_DIAGNOSTICS, *V2_SPLIT_DIAGNOSTICS, BASELINE, ARMS[-1])),
        3: (V2_BASELINE, V3_ARMS, ()),
    }
    base_v, arms, extra = plans[a.round]
    from strategy_workbench.domain.analytics._base_rate import base_rate

    market, ex = load_market()
    names = [v.name for v in (base_v, *arms)]
    names += [v.name for v in extra if (RESEARCH / "results" / f"signal_{a.signal}" / f"{v.name}.json").exists()]
    loaded = {n: _load(n, a.signal) for n in names}
    sessions = loaded[base_v.name][1].sessions
    pos = market.session_index()
    rows_at = [pos[d] for d in sessions]
    indices = {n: _index_result(n, sessions, ex.index_close[k, rows_at]) for k, n in enumerate(INDICES)}
    kospi = indices["코스피"]
    rows = {name: summarize(raw, r, base_rate, kospi) for name, (raw, r) in loaded.items()}
    for n, r in indices.items():
        rows[n] = summarize(None, r, base_rate, kospi)
    _, base = loaded[base_v.name]
    base_m = monthly_returns(base)
    for name, (_, r) in loaded.items():
        if name == base_v.name:
            continue
        t, pval = paired_t(monthly_returns(r), base_m)
        rows[name] |= {"d_cagr": rows[name]["cagr"] - rows[base_v.name]["cagr"], "t": t, "p": pval, "jaccard": jaccard_mean(r, base)}
    verdict: dict[str, dict] = {}
    if a.signal == "A":
        significant = holm({v.name: rows[v.name]["p"] for v in arms}, ALPHA)
        for v in arms:
            row = rows[v.name]
            ok_effect, ok_t, ok_holm = bool(row["d_cagr"] >= MIN_EFFECT), bool(row["t"] >= MIN_T), significant[v.name]
            verdict[v.name] = {
                "effect": ok_effect,
                "t": ok_t,
                "holm": ok_holm,
                "result": "순효과 있음" if (ok_effect and ok_t and ok_holm) else "순효과 확인 안 됨",
            }
    curves = {name: [round(x / START_CAPITAL, 5) for x in r.equity] for name, (_, r) in loaded.items()}
    for n, r in indices.items():
        curves[n] = [round(x / START_CAPITAL, 5) for x in r.equity]
    out = {
        "signal": a.signal,
        "snapshot_id": market.snapshot_id,
        "start": sessions[0].isoformat(),
        "end": sessions[-1].isoformat(),
        "months": int(len(base_m)),
        "rows": rows,
        "verdict": verdict,
        "sessions": [d.isoformat() for d in sessions],
        "curves": curves,
    }
    path = RESEARCH / "judge" / (f"judge_{a.signal}.json" if a.round == 1 else f"judge_{a.signal}_r{a.round}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"{path}  {sessions[0]} ~ {sessions[-1]}  months={len(base_m)}")
    for name, row in rows.items():
        extra = f"  Δ{row['d_cagr'] * 100:+.2f}%p t={row['t']:+.2f}" if "d_cagr" in row else ""
        print(f"{name:16s} CAGR {row['cagr'] * 100:+6.2f}%  MDD {row['mdd'] * 100:6.1f}%  vs코스피 {row['vs_kospi_cagr'] * 100:+6.2f}%p{extra}")
    for name, v in verdict.items():
        print(f"판정 {name}: {v}")


if __name__ == "__main__":
    main(sys.argv[1:])
