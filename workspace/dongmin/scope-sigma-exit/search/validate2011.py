"""2011~2015 확인 시험(VALIDATE_2011.md). 사양·대상·판정은 그 문서가 정본이고 결과를 보고 바꾸지 않는다.

    python -m search.validate2011
"""

from __future__ import annotations

import json
from datetime import date

import numpy as np

from search.alt import AltFeatures
from search.family import E0, F1, F3, F4, F2
from search.holdout import monthly_excess
from search.panel import INDEX_NAMES, RESEARCH, build_panel, load_panel
from search.run import BENCH, bench_series, evaluate, run_one, save
from search.strategies import SignalBuilder, month_ends
from sim.metrics import holm, newey_west_t

PANEL_V = RESEARCH / "panel_2011.npz"
LOAD_V, FIRST_V, LAST_V = date(2010, 1, 4), date(2011, 1, 31), date(2015, 12, 30)
NAMES_V = tuple(n for n in INDEX_NAMES if n[1] != "코스닥 150")  # 코스닥 150 은 2015-07 시작
CONFIRM = {"C13": "F1", "H5": "F4"}
EXPLORE = ("C01", "C05", "C06", "C07", "C09", "C11", "C12", "E1", "E5", "E6", "E8", "G1", "G2", "G3", "G4", "G5", "H1", "H6")
MIN_EFFECT, ALPHA = 0.02, 0.05


def main() -> None:
    from strategy_workbench.domain.analytics._base_rate import base_rate

    if not PANEL_V.exists():
        build_panel(LOAD_V, LAST_V, PANEL_V, NAMES_V)
    panel = load_panel(PANEL_V)
    days = month_ends(panel, FIRST_V, LAST_V)
    builder = SignalBuilder(panel, days, AltFeatures(panel))
    by_id = {c.cid: c for c in (*F1, *F2, *F3, *F4, E0)}
    main_panel = load_panel()
    rows: dict[str, dict] = {}
    for cid in (*CONFIRM, *EXPLORE, "E0"):
        r = run_one(by_id[cid], panel, builder, days, LAST_V, start=FIRST_V)
        save(r, RESEARCH / "results" / "v2011" / f"{cid}.json")
        row = {"label": by_id[cid].label, **evaluate(r, panel, base_rate)}
        if cid in CONFIRM:
            dev = json.loads((RESEARCH / "results" / CONFIRM[cid] / f"{cid}.json").read_text())
            dev_s = tuple(date.fromisoformat(d) for d in dev["sessions"])
            ex_v = monthly_excess(r.sessions, r.equity, bench_series(panel, r.sessions, BENCH))
            ex_d = monthly_excess(dev_s, np.array(dev["equity"]), bench_series(main_panel, dev_s, BENCH))
            row["pooled_t"], row["pooled_p"], _ = newey_west_t(np.concatenate([ex_v, ex_d]))
            row["pooled_months"] = int(len(ex_v) + len(ex_d))
        rows[cid] = row
        tag = "확인 대상" if cid in CONFIRM else "참고"
        extra = f" 합친 t {row['pooled_t']:+.2f}" if cid in CONFIRM else ""
        print(f"[{tag}] {cid} {row['label']:22s} CAGR {row['cagr']*100:+6.2f}% 코스피200 {row['bench_cagr']*100:+6.2f}% "
              f"초과 {row['excess']*100:+6.2f}%p t {row['t']:+5.2f}{extra} MDD {row['mdd']*100:6.1f}%", flush=True)
    sig = holm({cid: rows[cid]["pooled_p"] for cid in CONFIRM}, ALPHA)
    for cid in CONFIRM:
        row = rows[cid]
        c1 = row["excess"] >= MIN_EFFECT
        c2 = bool(sig[cid] and row["pooled_t"] > 0)
        row["verdict"] = "확인" if (c1 and c2) else ("방향 일치, 통계 약함" if c1 else "기각")
        print(f"판정 {cid}: {row['verdict']} (초과 {row['excess']*100:+.2f}%p, 합친 t {row['pooled_t']:+.2f}, {row['pooled_months']}개월)")
    (RESEARCH / "judge" / "validate_2011.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
