"""F6 전 구간 재시험(F6_FULL.md). 대상·구간·판정은 그 문서가 정본이다.

    python -m search.full
"""

from __future__ import annotations

import json
from datetime import date

import numpy as np

from search.alt import AltFeatures
from search.family import E0, F1, F2, F3, F4
from search.panel import INDEX_NAMES, RESEARCH, build_panel, load_panel
from search.run import BENCH, bench_series, cagr_of, evaluate, run_one, save
from search.strategies import SignalBuilder, month_ends
from sim.metrics import holm

PANEL_F = RESEARCH / "panel_full.npz"
LOAD, FIRST = date(2010, 1, 4), date(2011, 1, 31)
IDS = ("C01", "C05", "C06", "C07", "C09", "C11", "C12", "C13", "E1", "E5", "E6", "E8",
       "G1", "G2", "G3", "G4", "G5", "H1", "H5", "H6")
CUTS = (date(2015, 12, 30), date(2019, 12, 30))
MIN_EFFECT, ALPHA = 0.02, 0.05


def segments(sessions: tuple[date, ...], equity: np.ndarray, bench: np.ndarray) -> list[float]:
    """세 구간 CAGR 초과. 경계는 각 구간 끝날 이전 마지막 세션."""
    idx = [0] + [max(k for k, d in enumerate(sessions) if d <= c) for c in CUTS] + [len(sessions) - 1]
    return [cagr_of(equity, sessions, a, b) - cagr_of(bench, sessions, a, b) for a, b in zip(idx, idx[1:])]


def main() -> None:
    from strategy_workbench.domain.analytics._base_rate import base_rate

    if not PANEL_F.exists():
        build_panel(LOAD, None, PANEL_F, INDEX_NAMES)
    panel = load_panel(PANEL_F)
    end = panel.market.sessions[-1]
    days = month_ends(panel, FIRST, end)
    builder = SignalBuilder(panel, days, AltFeatures(panel))
    by_id = {c.cid: c for c in (*F1, *F2, *F3, *F4, E0)}
    rows: dict[str, dict] = {}
    for cid in (*IDS, "E0"):
        r = run_one(by_id[cid], panel, builder, days, end, start=FIRST)
        save(r, RESEARCH / "results" / "full" / f"{cid}.json")
        row = {"label": by_id[cid].label, **evaluate(r, panel, base_rate)}
        row["segments"] = segments(r.sessions, r.equity, bench_series(panel, r.sessions, BENCH))
        rows[cid] = row
        seg = " / ".join(f"{x*100:+.1f}" for x in row["segments"])
        print(f"{cid} {row['label']:22s} CAGR {row['cagr']*100:+6.2f}% 초과 {row['excess']*100:+6.2f}%p t {row['t']:+5.2f} "
              f"구간 {seg} MDD {row['mdd']*100:6.1f}%", flush=True)
    sig = holm({cid: rows[cid]["p"] for cid in IDS}, ALPHA)
    passed = []
    for cid in IDS:
        row = rows[cid]
        row["pass"] = bool(sig[cid] and row["t"] > 0 and row["excess"] >= MIN_EFFECT and all(x > 0 for x in row["segments"]))
        if row["pass"]:
            passed.append(cid)
    print("코스피200 전 구간 CAGR", f"{rows['E0']['bench_cagr']*100:+.2f}%", "| 전 구간 후보:", passed or "없음")
    (RESEARCH / "judge" / "F6_full.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
