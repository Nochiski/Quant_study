"""H5 홀드아웃 1회 시험(HOLDOUT_H5.md). 사양·구간·판정은 그 문서가 정본이고 결과를 보고 바꾸지 않는다.

    python -m search.holdout_h5
"""

from __future__ import annotations

import json
from datetime import date

import numpy as np

from search.family import F4
from search.holdout import monthly_excess
from search.panel import RESEARCH, build_panel, load_panel
from search.run import BENCH, bench_series, evaluate, run_one, save
from search.strategies import SignalBuilder, month_ends
from sim.metrics import newey_west_t

PANEL_H5 = RESEARCH / "panel_holdout_h5.npz"
LOAD, FIRST, LAST = date(2014, 12, 1), date(2016, 1, 29), date(2019, 12, 30)
NAMES = (("KOSPI", "코스피 200"), ("KOSPI", "코스피"), ("KOSDAQ", "코스닥"))
MIN_EFFECT, MIN_T = 0.02, 2.0


def _excess(path, panel) -> np.ndarray:
    d = json.loads(path.read_text())
    s = tuple(date.fromisoformat(x) for x in d["sessions"])
    return monthly_excess(s, np.array(d["equity"]), bench_series(panel, s, BENCH))


def main() -> None:
    from strategy_workbench.domain.analytics._base_rate import base_rate

    if not PANEL_H5.exists():
        build_panel(LOAD, LAST, PANEL_H5, NAMES)
    panel = load_panel(PANEL_H5)
    h5 = next(c for c in F4 if c.cid == "H5")
    days = month_ends(panel, FIRST, LAST)
    r = run_one(h5, panel, SignalBuilder(panel, days), days, LAST, start=FIRST)
    save(r, RESEARCH / "results" / "holdout" / "H5.json")
    row = evaluate(r, panel, base_rate)
    ex_h = monthly_excess(r.sessions, r.equity, bench_series(panel, r.sessions, BENCH))
    ex_v = _excess(RESEARCH / "results" / "v2011" / "H5.json", load_panel(RESEARCH / "panel_2011.npz"))
    ex_d = _excess(RESEARCH / "results" / "F4" / "H5.json", load_panel())
    t_all, p_all, _ = newey_west_t(np.concatenate([ex_v, ex_h, ex_d]))
    c1, c2 = row["excess"] >= MIN_EFFECT, t_all >= MIN_T
    verdict = "확인" if (c1 and c2) else ("방향 일치, 통계 약함" if c1 else "기각")
    out = {**row, "pooled_t": t_all, "pooled_p": p_all, "pooled_months": int(len(ex_v) + len(ex_h) + len(ex_d)),
           "holdout_months": int(len(ex_h)), "verdict": verdict, "start": r.sessions[0].isoformat(), "end": r.sessions[-1].isoformat()}
    (RESEARCH / "judge" / "holdout_H5.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    print(f"홀드아웃 {out['start']}~{out['end']} {len(ex_h)}개월 | CAGR {row['cagr']*100:+.2f}% 코스피200 {row['bench_cagr']*100:+.2f}% "
          f"초과 {row['excess']*100:+.2f}%p t {row['t']:+.2f} | 세 구간 합친 t {t_all:+.2f}({out['pooled_months']}개월) | 판정 {verdict}")
    print("연도(전략/코스피200)", {y: (round(a * 100, 1), round(b * 100, 1)) for y, (a, b) in row["years"].items()})
    print(f"MDD {row['mdd']*100:.1f}% (코스피200 {row['bench_mdd']*100:.1f}%) 회전 {row['turnover']:.1f}")


if __name__ == "__main__":
    main()
