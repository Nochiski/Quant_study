"""J6 홀드아웃 1회 시험(HOLDOUT_J6.md). 사양·구간·판정은 그 문서가 정본이고 결과를 보고 바꾸지 않는다.

    python -m search.holdout
"""

from __future__ import annotations

import json
from datetime import date

import numpy as np

from search.alt import AltFeatures
from search.family import F5
from search.panel import RESEARCH, Panel, build_panel, load_panel
from search.run import BENCH, _tax, bench_series, evaluate, marks, save
from search.strategies import SignalBuilder, month_ends, stock_variant
from sim.engine import simulate
from sim.metrics import newey_west_t

PANEL_H = RESEARCH / "panel_holdout.npz"
LOAD_H, FIRST_H, LAST_H = date(2016, 10, 1), date(2017, 3, 31), date(2019, 12, 30)
NAMES_H = (("KOSPI", "코스피 200"), ("KOSPI", "코스피"), ("KOSDAQ", "코스닥"), ("KOSDAQ", "코스닥 150"))
BENCH_J6, MIN_EFFECT, MIN_T = "코스닥 150", 0.02, 2.0


def monthly_excess(sessions, equity, bench) -> np.ndarray:
    mk = marks(sessions)
    return np.array([(equity[b] / equity[a]) - (bench[b] / bench[a]) for a, b in zip(mk, mk[1:])])


def main() -> None:
    from strategy_workbench.domain.analytics._base_rate import base_rate

    if not PANEL_H.exists():
        build_panel(LOAD_H, LAST_H, PANEL_H, NAMES_H)
    panel: Panel = load_panel(PANEL_H)
    j6 = next(c for c in F5 if c.cid == "J6")
    days = month_ends(panel, FIRST_H, LAST_H)
    builder = SignalBuilder(panel, days, AltFeatures(panel))
    signals = {panel.market.sessions[i]: builder.signal(j6, i) for i in days}
    r = simulate(stock_variant(j6), panel.market, signals, start=FIRST_H, end=LAST_H, tax_schedule=_tax())
    save(r, RESEARCH / "results" / "holdout" / "J6.json")
    row = evaluate(r, panel, base_rate, BENCH_J6)
    ex_h = monthly_excess(r.sessions, r.equity, bench_series(panel, r.sessions, BENCH_J6))
    dev = json.loads((RESEARCH / "results" / "F5" / "J6.json").read_text())
    main_panel = load_panel()
    dev_s = tuple(date.fromisoformat(d) for d in dev["sessions"])
    ex_d = monthly_excess(dev_s, np.array(dev["equity"]), bench_series(main_panel, dev_s, BENCH_J6))
    t_all, p_all, lag = newey_west_t(np.concatenate([ex_h, ex_d]))
    c1, c2 = row["excess"] >= MIN_EFFECT, t_all >= MIN_T
    verdict = "확인" if (c1 and c2) else ("방향 일치, 통계 약함" if c1 else "기각")
    out = {**row, "pooled_t": t_all, "pooled_p": p_all, "pooled_months": int(len(ex_h) + len(ex_d)),
           "holdout_months": int(len(ex_h)), "criterion1": bool(c1), "criterion2": bool(c2), "verdict": verdict,
           "start": r.sessions[0].isoformat(), "end": r.sessions[-1].isoformat(), "bench_k200": BENCH}
    path = RESEARCH / "judge" / "holdout_J6.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    print(f"홀드아웃 {out['start']}~{out['end']} {len(ex_h)}개월 | CAGR {row['cagr']*100:+.2f}% 코스닥150 {row['bench_cagr']*100:+.2f}% "
          f"초과 {row['excess']*100:+.2f}%p t {row['t']:+.2f} | 합친 t {t_all:+.2f}({out['pooled_months']}개월) | 판정 {verdict}")
    print("연도(전략/코스닥150)", {y: (round(a * 100, 1), round(b * 100, 1)) for y, (a, b) in row["years"].items()})
    print(f"MDD {row['mdd']*100:.1f}% (코스닥150 {row['bench_mdd']*100:.1f}%) 코스피200 대비 {row['excess_k200']*100:+.2f}%p 회전 {row['turnover']:.1f}")


if __name__ == "__main__":
    main()
