"""F7 기업 이벤트 시험(F7_EVENTS.md).

    python -m search.events
"""

from __future__ import annotations

import json
from datetime import date

from search.alt import EventFeatures
from search.family import E0, F7
from search.full import PANEL_F
from search.panel import RESEARCH, load_panel
from search.run import BENCH, bench_series, cagr_of, evaluate, run_one, save
from search.strategies import SignalBuilder, month_ends
from sim.metrics import holm

FIRST, CUT, MIN_EFFECT, ALPHA = date(2016, 1, 29), date(2019, 12, 30), 0.02, 0.05


def main() -> None:
    from strategy_workbench.domain.analytics._base_rate import base_rate

    panel = load_panel(PANEL_F)
    end = panel.market.sessions[-1]
    days = month_ends(panel, FIRST, end)
    builder = SignalBuilder(panel, days, EventFeatures(panel))
    rows: dict[str, dict] = {}
    for c in (*F7, E0):
        r = run_one(c, panel, builder, days, end, start=FIRST)
        save(r, RESEARCH / "results" / "F7" / f"{c.cid}.json")
        row = {"label": c.label, **evaluate(r, panel, base_rate)}
        s, eq, b = r.sessions, r.equity, bench_series(panel, r.sessions, BENCH)
        k = max(i for i, d in enumerate(s) if d <= CUT)
        row["segments"] = [cagr_of(eq, s, 0, k) - cagr_of(b, s, 0, k), cagr_of(eq, s, k, len(s) - 1) - cagr_of(b, s, k, len(s) - 1)]
        rows[c.cid] = row
        print(f"{c.cid} {c.label:22s} CAGR {row['cagr']*100:+6.2f}% 초과 {row['excess']*100:+6.2f}%p t {row['t']:+5.2f} TE {row['te']*100:.1f}% "
              f"구간 {row['segments'][0]*100:+.1f} / {row['segments'][1]*100:+.1f} MDD {row['mdd']*100:6.1f}%", flush=True)
    sig = holm({c.cid: rows[c.cid]["p"] for c in F7}, ALPHA)
    for c in F7:
        row = rows[c.cid]
        row["pass"] = bool(sig[c.cid] and row["t"] > 0 and row["excess"] >= MIN_EFFECT and all(x > 0 for x in row["segments"]))
    print("코스피200", f"{rows['E0']['bench_cagr']*100:+.2f}%", "| 전 구간 후보:", [c.cid for c in F7 if rows[c.cid]["pass"]] or "없음")
    (RESEARCH / "judge" / "F7_events.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
