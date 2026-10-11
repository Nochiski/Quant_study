"""Flow 시트 — 모델 유니버스 투자자별 순매수 · 외국인 지분율(DECISIONS §7 #11, 확정 샘플 30).

원천: equity `flow_daily`(키움 — kis 는 08-14 정지). 기준일 = 키움 수급 최신일.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb
from openpyxl import Workbook

from .extra_common import (
    BIGS,
    F_CNT,
    F_FL,
    F_PCT,
    F_PP,
    F_WON,
    Calendar,
    Universe,
    base_text,
    krx_calendar,
    latest,
    md,
    span,
)
from .qpack import XRefs, XSheet
from .sheet_market import INV
from .sources import Sources, lit, num, num0, text

NAME = "Flow"
TYPES = [("외국인", ["frgnr_invsr_krw", "natfor_krw"]), ("기관계", ["orgn_krw"]),
         ("연기금", ["penfnd_etc_krw"]), ("투신", ["invtrt_krw"]), ("사모", ["samo_fund_krw"]),
         ("금융투자", ["fnnc_invt_krw"]), ("개인", ["ind_invsr_krw"])]
WIDTHS = {"A": 7.5, "B": 18, "C": 14} | dict.fromkeys("DEFGHIJKLMNOPQ", 8)


@dataclass
class FlowData:
    cal: Calendar
    tk_flow: dict[str, dict[str, dict[str, float]]]     # 코드 → 창 → 열 → 합(원)
    fw: dict[str, dict[str, float | None]]              # 코드 → now · m1 외국인 지분율(%)


def load(con: duckdb.DuckDBPyConnection, src: Sources, upto: date, uni: Universe) -> FlowData:
    fl = src.equity("flow_daily")
    day = latest(con, fl, "date", upto, "src = 'kiwoom' AND frgnr_invsr_krw IS NOT NULL",
                 "키움 투자자별 매매")
    cal = krx_calendar(con, src, day)
    win = cal.win
    con.execute("CREATE OR REPLACE TEMP TABLE uc (ticker VARCHAR)")
    con.executemany("INSERT INTO uc VALUES (?)", [(c,) for c in uni])
    con.execute("CREATE OR REPLACE TEMP TABLE w (k VARCHAR, d0 DATE)")
    con.executemany("INSERT INTO w VALUES (?, ?)", [(k, win[k]) for k in ("1W", "1M")])
    sums = ", ".join(f"sum({c}) AS {c}" for c in INV)
    tk: dict[str, dict[str, dict[str, float]]] = {}
    for r in con.execute(f"""
            SELECT f.ticker, w.k, {sums} FROM {fl} f JOIN uc USING (ticker) JOIN w ON f.date > w.d0
            WHERE f.src = 'kiwoom' AND f.date <= {lit(day)} GROUP BY 1, 2""").fetchall():
        tk.setdefault(text(r[0]), {})[text(r[1])] = dict(zip(INV, map(num0, r[2:]), strict=True))
    fw: dict[str, dict[str, float | None]] = {}
    for t, dd, pct in con.execute(f"""
            SELECT f.ticker, f.date, f.foreign_wght_pct FROM {fl} f JOIN uc USING (ticker)
            WHERE f.src = 'kiwoom' AND f.date IN ({lit(day)}, {lit(win['1M'])})
              AND f.foreign_wght_pct IS NOT NULL""").fetchall():
        fw.setdefault(text(t), {"now": None, "m1": None})["now" if dd == day else "m1"] = num(pct)
    return FlowData(cal, tk, fw)


def write(wb: Workbook, f: FlowData, uni: Universe, refs: XRefs, d: date,
          *, index: int | None = None) -> list[tuple[str, str, str]]:
    cal, day = f.cal, f.cal.day
    win = cal.win
    xs = XSheet(wb, NAME, WIDTHS, refs, index=index)
    xs.texts({"A1": "정렬: 블록마다(대분류 순 · 1M 순매수 금액 순)",
              "A2": "Flow: 투자자별 순매수 · 외국인 지분율",
              "A3": f"자료: 키움 투자자별 매매 · 외국인 보유 · 모델 유니버스 {len(uni)}종목",
              "A4": base_text(day, d), "F4": "출처: quant-ledger 원장 · 시험 양식",
              "A5": f"1W: {span(win['1W'], day)} · 1M: {span(win['1M'], day)} · 금액 억원"})
    put, header, close, cs = xs.put, xs.header, xs.close, xs.cs

    def fsum(code: str, w: str, cols: list[str]) -> float:
        return sum(f.tk_flow.get(code, {}).get(w, {}).get(c, 0.0) for c in cols) / 1e8
    r0 = 6
    header(r0, [("A", "대분류별 투자자 순매수(억원): 모델 유니버스"), ("C", "종목"), ("D", "1W"),
                ("K", "1M")], ["", "대분류", "종목 수", *(t for t, _ in TYPES), *(t for t, _ in TYPES)],
           ab=True)
    r = r0 + 2
    for big in [*BIGS, "전체"]:
        codes = [c for c, u in uni.items() if big == "전체" or u.big == big]
        xs.lab(r, big, bold=(big == "전체"))
        put(r, 3, len(codes), F_CNT)
        for base, w in ((4, "1W"), (11, "1M")):
            for j, (_, cols) in enumerate(TYPES):
                put(r, base + j, sum(fsum(c, w, cols) for c in codes), F_FL)
        r += 1
    f1_end = r - 1
    close(f1_end, 17)
    cs(f"D{r0 + 2}:J{f1_end - 1}")
    cs(f"K{r0 + 2}:Q{f1_end - 1}")

    def fwchg(c: str) -> float | None:
        x = f.fw.get(c, {})
        now, m1 = x.get("now"), x.get("m1")
        return now - m1 if now is not None and m1 is not None else None
    fl_end = f1_end
    for nm, cols in [TYPES[0], TYPES[1], TYPES[2], TYPES[6], ("외국인 지분율", TYPES[0][1])]:
        t0 = fl_end + 2
        who = "외국인" if nm == "외국인 지분율" else nm
        title = ("외국인 지분율 변화: 1M 상승 상위 15 · 하락 상위 15" if nm == "외국인 지분율"
                 else f"{nm} 순매수: 1M 상위 15 · 하위 15")
        header(t0, [("A", title), ("C", "분류"), ("E", "규모"), ("F", f"{who} 순매수(억원)"),
                    ("I", "수익률"), ("J", "외국인 지분율")],
               ["코드", "이름", "대분류", "종합\n순위", "시총\n(억원)", "1W", "1M", "1M ÷ 시총\n(%)",
                "1M\n(%)", "지분율\n(%)", "1M 변화\n(%p)"])

        keys = {c: k for c in uni
                if (k := fwchg(c) if nm == "외국인 지분율" else fsum(c, "1M", cols)) is not None}
        vals = list(keys)
        top = sorted(vals, key=lambda c: -keys[c])[:15]
        bot = sorted(vals, key=lambda c: keys[c])[:15]
        r = t0 + 2
        for grp in (top, bot):
            for c in grp:
                u = uni[c]
                m1 = fsum(c, "1M", cols)
                xs.stock(r, c, u.name, u.big, u.rank, [
                    (u.mcap, F_WON, False), (fsum(c, "1W", cols), F_FL, False), (m1, F_FL, False),
                    (m1 / u.mcap * 100 if u.mcap else None, "0.00;-0.00", False),
                    (u.r1m, F_PCT, False), (f.fw.get(c, {}).get("now"), "0.00", False),
                    (fwchg(c), F_PP, False)])
                r += 1
            if grp is top:
                close(r - 1, 11)
        fl_end = r - 1
        close(fl_end, 11)
        cs(f"F{t0 + 2}:H{fl_end}")
        cs(f"I{t0 + 2}:I{fl_end}")
        cs(f"K{t0 + 2}:K{fl_end}")
    xs.page(f"A1:Q{fl_end}")
    return [("Flow", "대분류별 투자자 순매수",
             "키움 투자자별 매매를 모델 유니버스 종목 대분류로 더함(억원). 외국인 = 외국인 + 기타외국인, "
             "기관계 = 금융투자 · 투신 · 사모 · 연기금 · 보험 · 은행 · 기타금융 · 국가"),
            ("Flow", "투자자별 1M 상위 · 하위 15",
             f"1M 순매수 금액 순. 1M ÷ 시총 = 1M 순매수 ÷ {md(d)} 시총, 1M 수익률 = Raw 시트 모멘텀 1M, "
             f"외국인 지분율 = 키움 외국인 보유 비중({md(day)} · 1M 변화 %p)")]


__all__ = ["NAME", "FlowData", "load", "write"]
