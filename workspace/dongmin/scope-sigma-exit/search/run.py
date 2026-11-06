"""묶음을 돌리고 PROTOCOL.md 로 판정한다.

    python -m search.run F1            # 1단(묶음 판정) → 통과 후보는 2단(롤링·비용 2배)까지
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date

import numpy as np

from search.family import BENCHMARKS, FAMILIES, NEEDS_ALT, REFERENCES, Candidate
from search.panel import RESEARCH, Panel, load_panel
from search.strategies import FIRST_SIGNAL, SignalBuilder, month_ends, run_index, run_stock
from sim.engine import Result
from sim.metrics import cagr, holm, mdd, newey_west_t, sharpe, turnover

MIN_EFFECT, ALPHA = 0.02, 0.05
ROLL_STARTS = [(2020, 1), (2020, 7), (2021, 1), (2021, 7), (2022, 1), (2022, 7), (2023, 1), (2023, 7)]
ROLL_MONTHS, ROLL_MIN_POS, BENCH = 36, 6, "코스피 200"


def _tax():
    from strategy_workbench.domain.backtest._krx_tax import STATUTORY_SELL_TAX_BPS

    (schedule,) = STATUTORY_SELL_TAX_BPS.values()
    return schedule


def marks(sessions: tuple[date, ...]) -> list[int]:
    """첫 세션 + 월말 세션 위치."""
    ends = [i for i in range(len(sessions)) if i + 1 == len(sessions) or (sessions[i + 1].year, sessions[i + 1].month) != (sessions[i].year, sessions[i].month)]
    return sorted({0, *ends})


def bench_series(panel: Panel, sessions: tuple[date, ...], name: str = BENCH) -> np.ndarray:
    pos = panel.market.session_index()
    return np.array([panel.index_close[name][pos[d]] for d in sessions])


def cagr_of(values: np.ndarray, sessions: tuple[date, ...], a: int, b: int) -> float:
    return (values[b] / values[a]) ** (365.25 / (sessions[b] - sessions[a]).days) - 1.0


def evaluate(r: Result, panel: Panel, base_rate, bench_name: str = BENCH) -> dict:
    s, eq = r.sessions, r.equity
    bench = bench_series(panel, s, bench_name)
    k200 = bench_series(panel, s, BENCH)
    kospi = bench_series(panel, s, "코스피")
    mk = marks(s)
    rs = np.array([eq[b] / eq[a] - 1 for a, b in zip(mk, mk[1:])])
    rb = np.array([bench[b] / bench[a] - 1 for a, b in zip(mk, mk[1:])])
    t, p, lag = newey_west_t(rs - rb)
    te = float(np.std(rs - rb, ddof=1) * math.sqrt(12))
    out = {
        "cagr": cagr(r), "bench_cagr": cagr_of(bench, s, 0, len(s) - 1), "kospi_cagr": cagr_of(kospi, s, 0, len(s) - 1),
        "mdd": mdd(eq), "bench_mdd": mdd(bench), "sharpe": sharpe(r, base_rate), "turnover": turnover(r),
        "cost_krw": r.fees_krw + r.taxes_krw + r.slippage_krw, "final_krw": float(eq[-1]),
        "t": t, "p": p, "nw_lag": lag, "te": te, "ir": float(np.mean(rs - rb) * 12 / te) if te > 0 else float("nan"),
        "months": int(len(rs)),
    }
    out["excess"] = out["cagr"] - out["bench_cagr"]
    out["bench"] = bench_name
    out["excess_k200"] = out["cagr"] - cagr_of(k200, s, 0, len(s) - 1)
    out["excess_kospi"] = out["cagr"] - out["kospi_cagr"]
    year = {}
    for y in sorted({d.year for d in s}):
        idx = [k for k, d in enumerate(s) if d.year == y]
        a = max(idx[0] - 1, 0)
        year[y] = (eq[idx[-1]] / eq[a] - 1, bench[idx[-1]] / bench[a] - 1)
    out["years"] = year
    return out


def rolling(r: Result, panel: Panel, bench_name: str = BENCH) -> list[dict]:
    s, eq = r.sessions, r.equity
    bench = bench_series(panel, s, bench_name)
    mk = marks(s)
    by_month = {(s[i].year, s[i].month): i for i in mk[1:]}
    out = []
    for y, m in ROLL_STARTS:
        py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
        a = by_month.get((py, pm), 0)
        ey, em = y + (m - 1 + ROLL_MONTHS - 1) // 12, (m - 1 + ROLL_MONTHS - 1) % 12 + 1
        b = by_month.get((ey, em))
        if b is None:
            continue
        out.append({"start": s[a].isoformat(), "end": s[b].isoformat(),
                    "excess": cagr_of(eq, s, a, b) - cagr_of(bench, s, a, b)})
    return out


def save(r: Result, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "sessions": [d.isoformat() for d in r.sessions], "equity": [float(x) for x in r.equity],
        "traded_krw": r.traded_krw, "fees_krw": r.fees_krw, "taxes_krw": r.taxes_krw, "slippage_krw": r.slippage_krw,
        "n_orders": r.n_orders, "holdings_by_week": {d.isoformat(): sorted(h) for d, h in r.holdings_by_week.items()},
    }))


def run_one(
    c: Candidate, panel: Panel, builder: SignalBuilder, days: list[int], end: date, cost_mult: float = 1.0,
    start: date = FIRST_SIGNAL,
) -> Result:
    if c.kind in ("stock", "stock_ew", "cap", "tilt", "exclude", "core_sat", "avoid"):
        return run_stock(c, panel, builder, days, end, _tax(), cost_mult, start)
    return run_index(c, panel, days, end, cost_mult, start)


def main(argv: list[str]) -> None:
    from strategy_workbench.domain.analytics._base_rate import base_rate

    fam = argv[0]
    bench_name = BENCHMARKS.get(fam, BENCH)
    cands = FAMILIES[fam]
    panel = load_panel()
    end = panel.market.sessions[-1]
    days = month_ends(panel, FIRST_SIGNAL, end)
    alt = None
    if fam in NEEDS_ALT:
        from search.alt import AltFeatures

        alt = AltFeatures(panel)
    builder = SignalBuilder(panel, days, alt)
    out_dir = RESEARCH / "results" / fam
    rows: dict[str, dict] = {}
    results: dict[str, Result] = {}
    ref = REFERENCES.get(fam)
    if ref is not None:
        rr = run_one(ref, panel, builder, days, end)
        save(rr, out_dir / f"{ref.cid}.json")
        ref_row = {"label": ref.label, **evaluate(rr, panel, base_rate, bench_name)}
        print(f"{ref.cid} {ref.label:22s} CAGR {ref_row['cagr']*100:+6.2f}% 코스피200 대비 {ref_row['excess']*100:+6.2f}%p TE {ref_row['te']*100:.1f}%", flush=True)
    for c in cands:
        r = run_one(c, panel, builder, days, end)
        results[c.cid] = r
        save(r, out_dir / f"{c.cid}.json")
        rows[c.cid] = {"label": c.label, **evaluate(r, panel, base_rate, bench_name)}
        x = rows[c.cid]
        if ref is not None:
            x["vs_ref"] = x["cagr"] - ref_row["cagr"]
        print(f"{c.cid} {c.label:22s} CAGR {x['cagr']*100:+6.2f}% {bench_name} 대비 {x['excess']*100:+6.2f}%p t {x['t']:+5.2f} MDD {x['mdd']*100:6.1f}% 회전 {x['turnover']:5.1f}", flush=True)
    sig = holm({cid: rows[cid]["p"] for cid in rows}, ALPHA)  # 참고 줄은 아직 rows 에 없다
    for cid, row in rows.items():
        row["stage1"] = bool(sig[cid] and row["t"] > 0 and row["excess"] >= MIN_EFFECT)
    for c in cands:
        row = rows[c.cid]
        if not row["stage1"]:
            continue
        row["rolling"] = rolling(results[c.cid], panel, bench_name)
        r2 = run_one(c, panel, builder, days, end, cost_mult=2.0)
        row["excess_cost2"] = cagr(r2) - row["bench_cagr"]
        pos = sum(w["excess"] > 0 for w in row["rolling"])
        med = float(np.median([w["excess"] for w in row["rolling"]]))
        row["stage2"] = bool(pos >= ROLL_MIN_POS and med >= MIN_EFFECT and row["excess_cost2"] > 0)
    path = RESEARCH / "judge" / f"{fam}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if ref is not None:
        rows[ref.cid] = ref_row | {"reference": True}
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str))
    print("1단 통과:", [cid for cid, r in rows.items() if r.get("stage1")], " 2단 통과:", [cid for cid, r in rows.items() if r.get("stage2")])


if __name__ == "__main__":
    main(sys.argv[1:])
