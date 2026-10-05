"""T2.2b factor_inputs 층 + T2.11 신선도 유예 — 합성 equity/stage 트리 왕복.

`make_stage_tree` 규약(conftest)으로 원천 판을 만들고 `factor_inputs.build` 가 8표를 굽는지,
신선도 상태·유예·게이트·판 기록이 맞는지 본다. 값은 **리터럴**로 고정한다.

합성 트리 요약 (D = 2026-09-28 월, 거래일 = 06-01~09-28 평일 − 06-03·08-17·09-24·09-25)
  추정치 수집일(fetched) : 09-14·15·16·17·18·21·22·23·28 (D* = 09-28)
  종목          sec_type/시장     신선(당해 12월기 E op·ni)          기대 상태
  100010 A      common KOSPI      전 수집일                           fresh 0
  100020 B      common KOSDAQ     ~09-23 (09-28 행은 op NULL)         grace 1
  100030 C      common KOSPI      ~09-17 (이후 행 없음) · 3월 결산     grace 5
  100040 Dd     common KOSPI      ~09-16 (이후 A 행만)                lapsed 6
  100050 E      common KOSPI      09-14·15 → 끊김 → 09-28 복귀        fresh 0
  100060 F      common KOSPI      없음(DART 재무만)                   none
  100070 G      preferred KOSPI   전 수집일                           fresh (sec_type 제외)
  100080 H      spac KOSDAQ       전 수집일                           fresh (eligible)
  100100 K      common KOSPI      전 수집일, D 가격 없음              fresh (no_price 제외)
  100110 L      common KOSPI      차기(202712) 추정만                 none
  100090 I      etf               — 층에 싣지 않는다
  100099 J      common, delisted  — 층에 싣지 않는다
  100120~160    common KOSPI      전 수집일(보통 종목 5 — eligible 을 10 으로 채운다)
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import duckdb
import pytest
from conftest import _make_stage_tree
from factor_inputs import FactorInputsError, build
from factor_inputs.__main__ import main as cli_main
from model.contracts import FI_TABLES
from stage import manifest

D = dt.date(2026, 9, 28)
D_S = "20260928"
EQ_BUILD = "m_20260929T000500_000000Z"
ST_BUILD = "m_20260928T231000_000000Z"
HOLIDAYS = {dt.date(2026, 6, 3), dt.date(2026, 8, 17), dt.date(2026, 9, 24),
            dt.date(2026, 9, 25)}


def _sessions() -> list[dt.date]:
    out, d = [], dt.date(2026, 6, 1)
    while d <= D:
        if d.weekday() < 5 and d not in HOLIDAYS:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


SESSIONS = _sessions()
FETCH = [s for s in SESSIONS if dt.date(2026, 9, 14) <= s <= D]
A, B, C, DD, E, F, G, H, K, L = ("100010", "100020", "100030", "100040", "100050", "100060",
                                 "100070", "100080", "100100", "100110")
ETF, DELISTED = "100090", "100099"
EXTRA = ("100120", "100130", "100140", "100150", "100160")
LAYER = (A, B, C, DD, E, F, G, H, K, L, *EXTRA)
ELIGIBLE = {A, B, C, E, H, *EXTRA}
FRESHLY_COVERED = (A, B, C, DD, E, G, H, K, *EXTRA)       # 한 번이라도 신선했던 종목

# 종목 → (sec_type, market, 신선 수집일 목록)
SPEC: dict[str, tuple[str, str]] = {
    A: ("common", "KOSPI"), B: ("common", "KOSDAQ"), C: ("common", "KOSPI"),
    DD: ("common", "KOSPI"), E: ("common", "KOSPI"), F: ("common", "KOSPI"),
    G: ("preferred", "KOSPI"), H: ("spac", "KOSDAQ"), K: ("common", "KOSPI"),
    L: ("common", "KOSPI"), ETF: ("etf", "KOSPI"), DELISTED: ("common", "KOSPI"),
    **{t: ("common", "KOSPI") for t in EXTRA}}
FRESH_DAYS: dict[str, list[dt.date]] = {
    A: FETCH, B: [f for f in FETCH if f <= dt.date(2026, 9, 23)],
    C: [f for f in FETCH if f <= dt.date(2026, 9, 17)],
    DD: [f for f in FETCH if f <= dt.date(2026, 9, 16)],
    E: [dt.date(2026, 9, 14), dt.date(2026, 9, 15), D], G: FETCH, H: FETCH, K: FETCH,
    **{t: FETCH for t in EXTRA}}
IDX = {t: i for i, t in enumerate(LAYER + (ETF, DELISTED), start=1)}
CLOSE = {t: 10_000 * IDX[t] for t in IDX}
SHARES = {t: 1_000_000 + 12_345 * IDX[t] for t in IDX}


def corp(t: str) -> str:
    return "C" + t          # 우선주 G 는 A 와 같은 법인이다(아래 security)


# ── 원천 행 ──────────────────────────────────────────────────────────────────
def _calendar() -> list[dict]:
    return [{"date": s, "prev_td": SESSIONS[i - 1] if i else None,
             "next_td": SESSIONS[i + 1] if i + 1 < len(SESSIONS) else None}
            for i, s in enumerate(SESSIONS)]


# D-13 적격성 재료(universe_daily) — 종목 → (halt_state, admin_state, adv20_krw)
ELIG_SRC: dict[str, tuple[bool | None, bool | None, float | None]] = {
    A: (False, False, 2_512_345_678.0), K: (True, False, 1_000_000_000.0),
    DD: (False, True, 3_000_000_000.0), C: (False, False, None), E: (False, None, 900_000_000.0)}


def _universe() -> list[dict]:
    return [{"date": D, "ticker": t, "status": "delisted" if t == DELISTED else "listed",
             "market": SPEC[t][1], "sec_type": SPEC[t][0],
             "halt_state": ELIG_SRC.get(t, (False, False, 5e9))[0],
             "admin_state": ELIG_SRC.get(t, (False, False, 5e9))[1],
             "adv20_krw": ELIG_SRC.get(t, (False, False, 5e9))[2]} for t in SPEC]


def _audit() -> list[dict]:
    """한 사업보고서에 당기·전기·전전기 3행 — 당기만 봐야 한다."""
    y = dt.date

    def row(t: str, year: str, label: str, cls: str | None, avail: dt.date) -> dict:
        return {"corp_code": corp(t), "bsns_year": year, "reprt_code": "11011",
                "bsns_year_label": label, "rcept_no": avail.strftime("%Y%m%d") + "000009",
                "adt_opinion": None if cls is None else f"{cls}의견", "adt_opinion_class": cls,
                "available_date": avail}
    return [row(A, "2025", "제10기(당기)", "적정", y(2026, 3, 11)),
            row(A, "2025", "제9기(전기)", "한정", y(2026, 3, 11)),
            row(A, "2025", "제8기(전전기)", "의견거절", y(2026, 3, 11)),
            row(B, "2025", "제5기(당기)", "의견거절", y(2026, 3, 20)),
            row(E, "2025", "", "한정", y(2026, 3, 20)),                 # 라벨 빈 행도 당기 후보
            row(F, "2024", "제3기(당기)", "부적정", y(2025, 3, 20)),
            row(F, "2025", "-", None, y(2026, 3, 20)),              # 의견 원문 없음 → 건너뛴다
            row(F, "2025", "제4기(당기)", "적정", y(2026, 10, 1)),      # D 뒤 — 안 보인다
            row(H, "2025", "제2기(당기)", "other", y(2026, 3, 20))]


def _disclosure() -> list[dict]:
    y = dt.date

    def row(t: str, rcept: dt.date, deadline: dt.date, *, corr: bool = False) -> dict:
        return {"rcept_no": rcept.strftime("%Y%m%d") + t, "corp_code": corp(t), "rcept_dt": rcept,
                "kind": "half", "is_correction": corr, "legal_deadline": deadline,
                "delay_days": (rcept - deadline).days, "available_date": rcept}
    return [row(A, y(2026, 8, 14), y(2026, 8, 14)),                 # 기한 당일
            row(B, y(2026, 8, 18), y(2026, 8, 15)),                 # 기한 토 → 월 휴장 → 화 제출
            row(E, y(2026, 8, 20), y(2026, 8, 14)),                 # 지연
            row(E, y(2026, 9, 1), y(2026, 8, 14), corr=True),       # 정정은 원본이 아니다
            row(H, y(2026, 5, 20), y(2026, 5, 15)),                 # 옛 지연 …
            row(H, y(2026, 8, 13), y(2026, 8, 14))]                 # … 최근 건은 기한 안


def _security() -> list[dict]:
    return [{"ticker": t, "corp_code": corp(A) if t == G else corp(t), "name_current": f"종목{t}",
             "name_abbrv_current": None if t == B else f"약{t}",
             "sec_type": SPEC[t][0], "list_date": dt.date(2015, 1, 2)} for t in SPEC]


PRICE_DAYS = [s for s in SESSIONS if s >= dt.date(2026, 9, 18)]
OLD_IN = dt.date(2025, 4, 1)          # D − 550 = 2025-03-27 → 창 안
OLD_OUT = dt.date(2025, 1, 2)         # 창 밖


def _price_row(t: str, d: dt.date, close: int) -> dict:
    vol = 1_000 * IDX[t]
    return {"ticker": t, "date": d, "open": close - 10, "high": close + 50, "low": close - 50,
            "close": close, "volume_shr": vol, "value_krw": close * vol,
            "mktcap_krw": close * SHARES[t], "shares_out": SHARES[t], "basis": "krx"}


def _prices() -> list[dict]:
    rows = []
    for t in SPEC:
        days = [d for d in PRICE_DAYS if d != D] if t == K else PRICE_DAYS
        rows += [_price_row(t, d, CLOSE[t] + 100 * i) for i, d in enumerate(days)]
    rows += [_price_row(A, OLD_OUT, 5_000), _price_row(A, OLD_IN, 6_000)]
    # KRX 시총이 규칙값과 다른 종목 하나(기록형 n_krx_mktcap_diff) — H 의 D 행
    for r in rows:
        if r["ticker"] == H and r["date"] == D:
            r["mktcap_krw"] += 99_000_000
    return rows


ADJ_FACTOR = {A: 2.0}


def _adj() -> list[dict]:
    return [{"ticker": r["ticker"], "date": r["date"],
             "adj_close": float(r["close"]) * ADJ_FACTOR.get(r["ticker"], 1.0),
             "cum_share_factor": ADJ_FACTOR.get(r["ticker"], 1.0),
             "n_unadjusted_events": 0, "basis": "krx"} for r in _prices()]


def _adj_factor() -> list[dict]:
    # B: 창 안 미해결 사건(09-22) → 09-22 부터 adj_ok False. A: 창 밖 옛 사건(2020) → 영향 없음.
    # E: 창 안 사건 두 날(09-21 에 둘 · 09-23) → True · False · False · True · True 로 뒤집힌다.
    def bad(t: str, d: dt.date, eid: str) -> dict:
        return {"ticker": t, "effective_date": d, "event_id": eid, "apply_date": d,
                "factor_ok": False, "available_date": d}
    return [bad(E, dt.date(2026, 9, 21), "e1"), bad(E, dt.date(2026, 9, 21), "e2"),
            bad(E, dt.date(2026, 9, 23), "e3"),
            {"ticker": B, "effective_date": dt.date(2026, 9, 22), "event_id": "b1",
             "apply_date": dt.date(2026, 9, 22), "factor_ok": False,
             "available_date": dt.date(2026, 9, 22)},
            {"ticker": A, "effective_date": dt.date(2020, 5, 4), "event_id": "a0",
             "apply_date": dt.date(2020, 5, 4), "factor_ok": False,
             "available_date": dt.date(2020, 5, 4)},
            {"ticker": A, "effective_date": dt.date(2021, 5, 4), "event_id": "a1",
             "apply_date": dt.date(2021, 5, 4), "factor_ok": True,
             "available_date": dt.date(2021, 5, 4)}]


_FLOW_COLS = ("ind_invsr_krw", "frgnr_invsr_krw", "orgn_krw", "fnnc_invt_krw", "insrnc_krw",
              "invtrt_krw", "etc_fnnc_krw", "bank_krw", "penfnd_etc_krw", "samo_fund_krw",
              "natn_krw", "etc_corp_krw", "natfor_krw")


def _flow(t: str, d: dt.date, src: str | None, base: int | None) -> dict:
    row: dict = {"date": d, "ticker": t, "src": src}
    for i, c in enumerate(_FLOW_COLS):
        row[c] = None if base is None else base + i * 1_000_000
    return row


def _flows() -> list[dict]:
    rows = [_flow(A, s, "kiwoom", 1_000_000 * (i + 1)) for i, s in enumerate(SESSIONS)]
    rows[-1]["frgnr_invsr_krw"] = 1_500_000          # 1.5 백만원 → 반올림 2
    rows += [_flow(B, D, "kis", 7_000_000_000), _flow(B, D, "kiwoom", 3_000_000_000),
             # 09-23: 키움 칸이 전 주체 NULL 이면 KIS 가 남는다(compat 과 같은 선택)
             _flow(B, dt.date(2026, 9, 23), "kiwoom", None),
             _flow(B, dt.date(2026, 9, 23), "kis", 5_000_000_000),
             _flow(C, D, None, None),                                  # 미측정 칸 → 행 없음
             _flow(ETF, D, "kiwoom", 1_000_000)]
    return rows


def _credit() -> list[dict]:
    rows = [{"date": s, "ticker": A, "whol_loan_rmnd_stcn_shr": 10_000 + i,
             "whol_loan_rmnd_rate_pct": 0.5} for i, s in enumerate(SESSIONS)]
    rows.append({"date": dt.date(2026, 9, 21), "ticker": B, "whol_loan_rmnd_stcn_shr": None,
                 "whol_loan_rmnd_rate_pct": None})
    return rows


def _sector() -> list[dict]:
    rows = [{"ticker": A, "snapshot_date": dt.date(2026, 9, 11), "wics_l1_cd": "G10",
             "wics_l1_nm": "에너지", "wics_l2_cd": "G1010", "wics_l2_nm": "에너지",
             "available_date": dt.date(2026, 9, 11)},
            {"ticker": A, "snapshot_date": dt.date(2026, 10, 2), "wics_l1_cd": "G20",
             "wics_l1_nm": "산업재", "wics_l2_cd": "G2010", "wics_l2_nm": "자본재",
             "available_date": dt.date(2026, 10, 2)}]
    for t in LAYER:
        rows.append({"ticker": t, "snapshot_date": dt.date(2026, 9, 18), "wics_l1_cd": "G15",
                     "wics_l1_nm": "소재", "wics_l2_cd": "G1510", "wics_l2_nm": "소재",
                     "available_date": dt.date(2026, 9, 18)})
    return rows


def _coverage_daily() -> list[dict]:
    rows = []
    for t in (A, B, F):
        for f in FETCH:
            n = {A: 10, B: 7 if f <= dt.date(2026, 9, 23) else 3, F: 2}[t]
            rows.append({"ticker": t, "date": f, "analyst_count": n})
    return rows


def _annual_row(t: str, f: dt.date, period: str, kind: str, op: float | None,
                ni: float | None) -> dict:
    return {"ticker": t, "fetched_date": f, "period_label": f"{period[:4]}.{period[4:]}({kind})",
            "period": period, "period_kind": kind, "fs_basis": "IFRS연결", "revenue": 9_000.0,
            "yoy_pct": 1.0, "op": op, "ni": ni, "eps": 100.0, "bps": 1_000.0, "per": 10.0,
            "pbr": 1.0, "roe_pct": 10.0, "ev_ebitda": 5.0}


def _consensus_annual() -> list[dict]:
    rows = []
    for f in FETCH:
        for t in (*FRESHLY_COVERED, L):
            fresh = f in FRESH_DAYS.get(t, [])
            if t == L:
                rows.append(_annual_row(t, f, "202712", "E", 10.0, 5.0))
            elif fresh:
                rows += [_annual_row(t, f, "202512", "A", 800.0, 600.0),
                         _annual_row(t, f, "202612", "E", 900.0, 700.0)]
            elif t == B:
                rows.append(_annual_row(t, f, "202612", "E", None, 700.0))   # op 소멸
            elif t == DD:
                rows.append(_annual_row(t, f, "202512", "A", 800.0, 600.0))  # 추정 소멸
    return rows


ACC = {"121000": 1, "121500": 2, "122710": 3, "312000": 4, "314000": 5, "382000": 6,
       "382400": 7, "211500": 8, "610100": 9}
LB = {"current": 0, "1w": 1, "1m": 2, "3m": 3, "1y": 4}


def mval(t: str, f: dt.date, period: str, acc: str, lb: str) -> float:
    """합성 매트릭스 값 — 종목·수집일·결산기·계정·lookback 이 전부 다른 값."""
    return (IDX[t] * 100_000 + (1_000 if period == "202712" else 0) + ACC[acc] * 100
            + LB[lb] * 10 + f.day / 100)


def _matrix() -> list[dict]:
    rows = []
    for f in FETCH:
        for t in FRESHLY_COVERED:
            if f not in FRESH_DAYS.get(t, []) and not (t == B and f == D):
                continue
            for period in ("202612", "202712"):
                for acc in ACC:
                    for lb, li in LB.items():
                        if t == H and lb == "3m":
                            continue          # H: 3m 셀이 통째로 없다 → 행은 값 NULL 로 선다
                        rows.append({
                            "ticker": t, "fetched_date": f, "target_period": period,
                            "acc_cd": acc, "lookback_idx": str(li + 1), "lookback": lb,
                            "target_label": f"{period[:4]}/{period[4:]}",
                            "base_date": f - dt.timedelta(days=1),
                            "value": mval(t, f, period, acc, lb)})
    return rows


_FIN_ITEMS: list[tuple[str, str, str | None, str, float]] = [
    ("cF3002", "200000", None, "매출액(수익)", 10_000.4),
    ("cF3002", "200810", None, "매출총이익", 3_000.6),
    ("cF3002", "201370", None, "영업이익", 1_000.5),
    ("cF3002", "203170", None, "당기순이익", 800.2),
    ("cF4002", "312000", None, "EPS", 1_234.4),
    ("cF4002", "314000", None, "BPS", 20_000.0),
    ("cF4002", "382100", None, "PER", 12.5),
    ("cF4002", "382500", None, "PBR", 1.25),
    ("cF4002", "331000", None, "EV/EBITDA", 6.5),
    ("cF4002", "431800", None, "현금배당수익률", 2.5),
    ("cF4002", "701250", None, "보통주수정기말발행주식수(자사주차감)<당기>", 1_000_000.0),
]


def _labels(t: str) -> list[str]:
    mm = "03" if t == C else "12"
    return [f"{y}/{mm}(IFRS연결)" for y in range(2021, 2026)] + [f"2026/{mm}(E)(IFRS연결)"]


def fin_val(t: str, slot: int, base: float, f: dt.date) -> float:
    return base * IDX[t] + slot + (0.0 if f == D or f == FIN_LAST.get(t) else 7.0)


FIN_LAST = {B: dt.date(2026, 9, 23), C: dt.date(2026, 9, 17), DD: dt.date(2026, 9, 16)}


def _fin_wise() -> list[dict]:
    rows = []
    plan = {A: [dt.date(2026, 9, 23), D], B: [dt.date(2026, 9, 23)], C: [dt.date(2026, 9, 17)],
            DD: [dt.date(2026, 9, 16)], E: [D], F: [D], H: [D], **{t: [D] for t in EXTRA}}
    for t, days in plan.items():
        for f in days:
            for seq, (ep, accode, p_accode, nm, base) in enumerate(_FIN_ITEMS):
                row: dict = {"ticker": t, "fetched_date": f, "ep": ep, "seq": seq,
                             "accode": accode, "p_accode": p_accode, "acc_nm": nm,
                             "fs_basis": "IFRS연결", "freq": "연간"}
                for i, lab in enumerate(_labels(t), start=1):
                    row[f"period_label_{i}"] = lab
                for i in range(1, 7):
                    row[f"val_{i}"] = fin_val(t, i, base, f)
                rows.append(row)
    return rows


TRIL = 1_000_000_000_000.0     # 1조 원


def _fin_std_row(t: str, period_end: dt.date, report: str, avail: dt.date, *,
                 fs_div: str = "CFS", scale: float = 1.0, **over: object) -> dict:
    row: dict = {
        "corp_code": corp(t), "period_end": period_end, "report_code": report, "fs_div": fs_div,
        "vintage_kind": "api_restated", "rcept_no": avail.strftime("%Y%m%d") + "000001",
        "available_date": avail, "capex_basis": "standard",
        "total_asset": 500 * TRIL * scale, "total_liab": 100 * TRIL * scale,
        "total_equity": 400 * TRIL * scale, "net_income": 40 * TRIL * scale,
        "cf_operating_ytd": 60 * TRIL * scale, "capex_ytd": -30 * TRIL * scale,
        "gross_profit": 50 * TRIL * scale, "revenue": 70 * TRIL * scale,
        "op_profit": 10 * TRIL * scale,
        "revenue_q4_derived": 80 * TRIL * scale, "op_profit_q4_derived": 11 * TRIL * scale,
        "net_income_q4_derived": 9 * TRIL * scale, "gross_profit_q4_derived": 20 * TRIL * scale,
        "q4_derived_available_date": avail,
        "cf_operating_q": 15 * TRIL * scale, "capex_q": -7 * TRIL * scale,
        "cf_q_available_date": avail}
    row.update(over)
    return row


def _fin_std() -> list[dict]:
    y = dt.date
    return [
        _fin_std_row(A, y(2024, 12, 31), "11011", y(2025, 3, 10), scale=0.9),
        _fin_std_row(A, y(2025, 12, 31), "11011", y(2026, 3, 11)),
        _fin_std_row(A, y(2025, 12, 31), "11011", y(2026, 3, 12), fs_div="OFS", scale=0.2),
        _fin_std_row(A, y(2025, 12, 31), "11011", y(2026, 10, 1), scale=2.0),     # D 뒤 정정
        _fin_std_row(A, y(2025, 3, 31), "11013", y(2025, 5, 15), scale=0.25),
        _fin_std_row(A, y(2025, 6, 30), "11012", y(2025, 8, 14), scale=0.26),
        _fin_std_row(A, y(2025, 9, 30), "11014", y(2025, 11, 14), scale=0.27),
        _fin_std_row(A, y(2026, 3, 31), "11013", y(2026, 5, 15), scale=0.28),
        _fin_std_row(A, y(2026, 6, 30), "11012", y(2026, 8, 14), scale=0.29),
        _fin_std_row(A, y(2026, 9, 30), "11014", y(2026, 11, 14), scale=0.3),      # D 뒤
        _fin_std_row(F, y(2025, 12, 31), "11011", y(2026, 3, 20), capex_basis="ppe_parts"),
        _fin_std_row(C, y(2025, 3, 31), "11011", y(2025, 6, 20)),                  # 3월 결산
    ]


def _dividend() -> list[dict]:
    y = dt.date
    base = {"reprt_code": "11011", "rcept_no": "20260311000001"}
    return [
        {**base, "corp_code": corp(A), "bsns_year": "2025", "stock_knd": "보통주",
         "stlm_dt": y(2025, 12, 31), "dps_krw": 1_446.0, "available_date": y(2026, 3, 11)},
        {**base, "corp_code": corp(A), "bsns_year": "2025", "stock_knd": "우선주",
         "stlm_dt": y(2025, 12, 31), "dps_krw": 1_447.0, "available_date": y(2026, 3, 11)},
        {**base, "corp_code": corp(A), "bsns_year": "2024", "stock_knd": "보통주식",
         "stlm_dt": y(2024, 12, 31), "dps_krw": 1_444.0, "available_date": y(2025, 3, 10)},
        # D 뒤 공시 — 보이면 안 된다
        {**base, "corp_code": corp(F), "bsns_year": "2025", "stock_knd": "보통주",
         "stlm_dt": y(2025, 12, 31), "dps_krw": 500.0, "available_date": y(2026, 10, 5)},
        # E: 종류를 나누지 않은 법인 축 '-' 행에만 값 → 보통주 행(값 NULL)보다 '-' 값이 이긴다
        {**base, "corp_code": corp(E), "bsns_year": "2025", "stock_knd": "보통주",
         "stlm_dt": y(2025, 12, 31), "dps_krw": None, "available_date": y(2026, 3, 11)},
        {**base, "corp_code": corp(E), "bsns_year": "2025", "stock_knd": "-",
         "stlm_dt": y(2025, 12, 31), "dps_krw": 300.0, "available_date": y(2026, 3, 11)},
        # A: '-' 행이 있어도 보통주 값이 먼저다
        {**base, "corp_code": corp(A), "bsns_year": "2025", "stock_knd": "-",
         "stlm_dt": y(2025, 12, 31), "dps_krw": 9_999.0, "available_date": y(2026, 3, 11)},
    ]


def _corp(holding: frozenset[str] = frozenset()) -> list[dict]:
    """equity corp — 지주사 판정 재료(10-01 T-Q4: KSIC 64992). `holding` 종목만 지주회사 업종."""
    return [{"corp_code": corp(t), "corp_name": f"법인{t}", "fiscal_month": 12,
             "fiscal_month_basis": "company", "induty_code": "64992" if t in holding else "26",
             "induty_class": None} for t in (*LAYER, ETF, DELISTED)]


# WISE 분기 손익(stg_fin_wise_q Q:IS) 기간 6칸 — 시프트업 실측 모양(별도 3분기 → 연결 2분기 + 추정 1칸)
WQ_LABELS = (("202506", False, "IFRS별도"), ("202509", False, "IFRS별도"), ("202512", False, "IFRS별도"),
             ("202603", False, "IFRS연결"), ("202606", False, "IFRS연결"), ("202609", True, "IFRS연결"))


def _wq_rows(t: str, fetched: dt.date, accounts: dict[str, list[float]],
             labels: tuple[tuple[str, bool, str], ...] = WQ_LABELS) -> list[dict]:
    rows = []
    for seq, (nm, vals) in enumerate(accounts.items()):
        r: dict[str, object] = {"ticker": t, "fetched_date": fetched, "pkey": "Q:IS", "seq": seq,
                                "accode": f"{200000 + seq}", "acc_nm": nm, "p_accode": None}
        for i, ((per, est, basis), v) in enumerate(zip(labels, vals, strict=True), start=1):
            r.update({f"period_{i}": per, f"is_est_{i}": est, f"basis_{i}": basis, f"val_{i}": v})
        rows.append(r)
    return rows


def make_roots(base: Path, *, eq_build: str = EQ_BUILD,
               drop_fetch_after: dt.date | None = None,
               wise_q: list[dict] | None = None,
               holding: frozenset[str] = frozenset()) -> tuple[Path, Path]:
    """(equity_root, stage_root). `drop_fetch_after` 를 주면 그날 뒤 WISE 수집이 없다(수집 정지).
    `wise_q` 를 주면 stg_fin_wise_q 판을 만든다(없으면 선택 원천 'absent' → 분기는 DART)."""
    eq, st = base / "eq", base / "st"
    equity = {"trading_calendar": _calendar(), "universe_daily": _universe(),
              "security": _security(), "price_daily": _prices(), "price_adj_daily": _adj(),
              "adj_factor": _adj_factor(), "flow_daily": _flows(), "credit_daily": _credit(),
              "sector_snapshot": _sector(), "coverage_daily": _coverage_daily(),
              "fin_std": _fin_std(), "dividend_event": _dividend(), "audit_opinion": _audit(),
              "disclosure_version": _disclosure(), "corp": _corp(holding)}
    for table, rows in equity.items():
        _make_stage_tree(eq, table, rows, build_id=eq_build)
    stage = {"stg_consensus_annual": _consensus_annual(), "stg_consensus_matrix": _matrix(),
             "stg_fin_wise": _fin_wise()}
    for table, rows in stage.items():
        if drop_fetch_after is not None:
            rows = [r for r in rows if r["fetched_date"] <= drop_fetch_after]
        _make_stage_tree(st, table, rows, build_id=ST_BUILD)
    if wise_q is not None:
        _make_stage_tree(st, "stg_fin_wise_q", wise_q, build_id=ST_BUILD)
    return eq / "stage", st / "stage"


@pytest.fixture(scope="module")
def roots(tmp_path_factory) -> tuple[Path, Path]:
    return make_roots(tmp_path_factory.mktemp("fi_src"))


@pytest.fixture(scope="module")
def built(roots, tmp_path_factory):
    out = tmp_path_factory.mktemp("fi_out") / "factor_inputs"
    res = build(D_S, "morning", out, roots[1], roots[0], min_eligible=5, golden_path=None)
    return out, res


def q(out: Path, table: str, sql: str) -> list[tuple]:
    """커밋된 판(`current_build`)을 `t` 로 읽는다."""
    m = manifest.load(out / table / "MANIFEST.json")
    path = out / table / f"v={m.current_build}" / "part0.parquet"
    con = duckdb.connect()
    try:
        con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{path}', "
                    "hive_partitioning=false)")
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ── 판 · 계약 ────────────────────────────────────────────────────────────────
def test_build_commits_every_contract_table_under_one_build_id(built) -> None:
    out, res = built
    assert res.ok, [(g.name, g.status.value, g.detail) for g in res.gates]
    assert res.build_id.startswith("m_")
    ids = {t: manifest.load(out / t / "MANIFEST.json").current_build for t in FI_TABLES}
    assert set(ids.values()) == {res.build_id}
    latest = json.loads((out / "latest_morning.json").read_text(encoding="utf-8"))
    run = json.loads((out / "_runs" / f"{D_S}_morning.json").read_text(encoding="utf-8"))
    assert latest == run
    assert run["status"] == "ok" and run["build_id"] == res.build_id and run["date"] == "2026-09-28"
    assert run["equity_builds"]["price_daily"] == EQ_BUILD
    assert run["stage_builds"]["stg_consensus_matrix"] == ST_BUILD
    assert run["tables"]["fi_fin_summary"]["inputs"]["fin_std"] == EQ_BUILD
    assert run["tables"]["fi_consensus"]["inputs"]["stg_consensus_matrix"] == ST_BUILD
    assert any(g["column"] == "obs_date" for g in run["gaps"])
    rec = manifest.load(out / "fi_prices" / "MANIFEST.json").builds[-1]
    assert rec.basis == "morning" and rec.inputs == {"price_daily": EQ_BUILD}
    assert not (out / "_tmp" / res.build_id).exists()


def test_schema_equals_contract_and_gates_pass(built) -> None:
    out, res = built
    for name, t in FI_TABLES.items():
        m = manifest.load(out / name / "MANIFEST.json")
        con = duckdb.connect()
        got = [(r[0], r[1]) for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{out / name / f'v={m.current_build}'}"
            "/part0.parquet', hive_partitioning=false)").fetchall()]
        con.close()
        assert got == [(c.name, c.dtype) for c in t.columns], name
    status = {g.name: g.status.value for g in res.gates}
    assert status == {"FG0": "pass", "FG1": "pass", "FG2": "pass", "FG3": "pass",
                      "FG4": "skip", "FG-fresh": "pass"}


# ── T2.11 신선도 · 유니버스 ────────────────────────────────────────────────────
def test_coverage_states_ages_and_has_estimates(built) -> None:
    out, _ = built
    got = {r[0]: r[1:] for r in q(out, "fi_universe",
           "SELECT ticker, coverage_state, coverage_age_days, has_estimates FROM t")
           if r[0] not in EXTRA}
    assert got == {
        A: ("fresh", 0, True), B: ("grace", 1, True), C: ("grace", 5, True),
        DD: ("lapsed", 6, False), E: ("fresh", 0, True), F: ("none", None, False),
        G: ("fresh", 0, True), H: ("fresh", 0, True), K: ("fresh", 0, True),
        L: ("none", None, False)}


def test_eligible_and_exclude_reasons(built) -> None:
    out, res = built
    got = {r[0]: (r[1], r[2]) for r in q(out, "fi_universe",
           "SELECT ticker, eligible, exclude_reason FROM t")}
    assert {t for t, (ok, _) in got.items() if ok} == ELIGIBLE
    assert got[DD] == (False, "estimates_lapsed")
    assert got[F] == (False, "estimates_none") and got[L] == (False, "estimates_none")
    assert got[G] == (False, "sec_type")
    assert got[K] == (False, "no_price")
    assert ETF not in got and DELISTED not in got
    run = json.loads(res.run_manifest.read_text(encoding="utf-8"))
    assert run["n_lapsed_dropped"] == 1
    assert run["coverage"]["counts"] == {"fresh": 10, "grace": 2, "lapsed": 1, "none": 2}
    assert run["coverage"]["last_collection_date"] == "2026-09-28"
    assert run["coverage"]["collection_lag_sessions"] == 0


def test_grace_days_parameter_moves_the_boundary(roots, tmp_path: Path) -> None:
    """G=4 이면 나이 5 인 C 가 lapsed 로 떨어진다(유예 경계는 파라미터)."""
    out = tmp_path / "fi"
    res = build(D_S, "morning", out, roots[1], roots[0], grace_days=4, min_eligible=1,
                golden_path=None)
    assert res.ok
    got = dict(q(out, "fi_universe", "SELECT ticker, coverage_state FROM t"))
    assert got[C] == "lapsed" and got[B] == "grace"
    assert res.coverage["n_lapsed_dropped"] == 2


def test_universe_attributes_market_cap_and_sector(built) -> None:
    out, _ = built
    row = q(out, "fi_universe", "SELECT name, market, sec_type, listed_date, shares, market_cap, "
                                f"mktcap_basis, sector_l1, sector_l1_name, n_analysts, date "
                                f"FROM t WHERE ticker = '{A}'")[0]
    close_d = CLOSE[A] + 100 * (len(PRICE_DAYS) - 1)
    assert row == ("약100010", "KOSPI", "common", dt.date(2015, 1, 2), SHARES[A],
                   float(round(SHARES[A] * close_d / 1e8)), "krx", "G15", "소재", 10, D)
    # 이름은 약명(시장 호칭) — 약명이 없는 B 는 정식명으로 돌아간다
    assert q(out, "fi_universe", f"SELECT name FROM t WHERE ticker = '{B}'") == [(f"종목{B}",)]
    # 유예 종목 B 의 추정기관 수는 마지막 신선일(09-23) 값 7 — 소멸 뒤 값 3 이 아니다
    assert q(out, "fi_universe", f"SELECT n_analysts FROM t WHERE ticker = '{B}'") == [(7,)]
    assert q(out, "fi_universe", f"SELECT n_analysts FROM t WHERE ticker = '{F}'") == [(2,)]


def test_eligibility_inputs_are_attributes_not_rules(built) -> None:
    """D-13 재료 5열은 속성으로만 싣고 eligible(기본 규칙)은 쓰지 않는다."""
    out, _ = built
    got = {r[0]: r[1:] for r in q(out, "fi_universe",
                                  "SELECT ticker, adv20, is_admin, is_halted, audit_adverse, "
                                  "filing_late, eligible FROM t")}
    assert got[A] == (25.12345678, False, False, False, False, True)   # 당기 적정(전기 한정 무시)
    assert got[B][3:] == (True, False, True)       # 의견거절이어도 eligible · 휴장 뒤 첫 거래일
    assert got[C][0] is None and got[C][5] is True  # adv20 창 미달 → NULL
    # E: 관리 판정 불가 · 빈 라벨도 당기 후보 · 지연 제출
    assert got[E][1] is None and got[E][3:5] == (True, True)
    assert got[DD][1] is True                       # 관리종목(여전히 사유는 estimates_lapsed)
    assert got[K][2] is True                        # 매매정지
    assert got[F][3] is True                        # 2025 는 의견 없음·D 뒤 정정 → 2024 부적정
    assert got[H][3:5] == (None, False)             # 분류 other → 모름 · 최근 건은 기한 안
    assert got[L][3:5] == (None, None)              # 감사·공시 없음


# ── 컨센서스 ────────────────────────────────────────────────────────────────
def test_consensus_uses_last_fresh_snapshot_and_all_horizons(built) -> None:
    out, _ = built
    rows = q(out, "fi_consensus", "SELECT ticker, target_period, horizon, fetched_date FROM t")
    tickers = {r[0] for r in rows}
    assert tickers == {A, B, C, E, G, H, K, *EXTRA}  # lapsed·none 은 싣지 않는다
    fetched = {r[0]: r[3] for r in rows}
    assert fetched[B] == dt.date(2026, 9, 23) and fetched[C] == dt.date(2026, 9, 17)
    assert fetched[A] == D and fetched[E] == D
    keys = {(r[1], r[2]) for r in rows if r[0] == A}
    assert keys == {(p, h) for p in ("2026/12", "2027/12") for h in ("cur", "1w", "1m", "3m")}
    # H 는 3m 셀이 통째로 없어도 3m 행이 값 NULL 로 선다(compat 규약)
    assert q(out, "fi_consensus", f"SELECT op, ni FROM t WHERE ticker = '{H}' "
                                  "AND target_period = '2026/12' AND horizon = '3m'") == [
        (None, None)]


def test_consensus_values_units_and_obs_date(built) -> None:
    out, _ = built
    cur = q(out, "fi_consensus", "SELECT op, ni, eps, bps, per, obs_date, n_analysts FROM t "
                                 f"WHERE ticker = '{B}' AND target_period = '2026/12' "
                                 "AND horizon = 'cur'")[0]
    f = dt.date(2026, 9, 23)
    assert cur == (mval(B, f, "202612", "121500", "current"),
                   mval(B, f, "202612", "122710", "current"),
                   float(round(mval(B, f, "202612", "312000", "current"))),
                   float(round(mval(B, f, "202612", "314000", "current"))),
                   mval(B, f, "202612", "382000", "current"), dt.date(2026, 9, 22), 7)
    wk = q(out, "fi_consensus", "SELECT op, obs_date FROM t "
                                f"WHERE ticker = '{B}' AND target_period = '2026/12' "
                                "AND horizon = '1w'")[0]
    assert wk == (mval(B, f, "202612", "121500", "1w"), None)


def test_consensus_annual_is_v2_source_latest_snapshot(built) -> None:
    """v2 전용 fi_consensus_annual — 종목별 최신 판(≤ D) 의 전년·당해·차년 12월기, E·A 둘 다."""
    out, _ = built
    got = {(r[0], r[1], r[2]): r[3:] for r in q(
        out, "fi_consensus_annual", "SELECT ticker, period, data_type, op, ni, eps, per, "
                                    "fetched_date FROM t")}
    assert got[(A, "2025/12", "A")] == (800.0, 600.0, 100.0, 10.0, D)
    assert got[(A, "2026/12", "E")] == (900.0, 700.0, 100.0, 10.0, D)
    # B 의 최신 판(09-28)은 op 가 NULL 인 추정 행 하나 — 유예와 무관하게 그 판 그대로(계약)
    assert got[(B, "2026/12", "E")] == (None, 700.0, 100.0, 10.0, D)
    assert (B, "2025/12", "A") not in got
    assert got[(L, "2027/12", "E")][0] == 10.0
    # C 는 09-17 뒤 수집이 없다 → 09-17 판
    assert got[(C, "2026/12", "E")][4] == dt.date(2026, 9, 17)
    assert {t for t, _, _ in got} <= set(LAYER)


# ── 재무 요약 ────────────────────────────────────────────────────────────────
def test_fin_summary_annual_two_periods_with_dart_quality(built) -> None:
    out, _ = built
    rows = q(out, "fi_fin_summary", "SELECT period, revenue, op, ni, eps, bps, per, pbr, roe, "
                                    "roa, debt_ratio, fcf, capex, dividend_yield, dps, shares, "
                                    "ev_ebitda, gross_profit, total_assets, fs_basis, "
                                    "capex_basis, available_date, op_margin, ni_margin, yoy "
                                    f"FROM t WHERE ticker = '{A}' AND period_type = 'annual' "
                                    "ORDER BY period DESC")
    assert [r[0] for r in rows] == ["2025/12", "2024/12"]
    r = rows[0]
    i = IDX[A]
    assert r[1:8] == (float(round(10_000.4 * i + 5)), float(round(1_000.5 * i + 5)),
                      float(round(800.2 * i + 5)), float(round(1_234.4 * i + 5)),
                      float(round(20_000.0 * i + 5)), 12.5 * i + 5, 1.25 * i + 5)
    # DART 2025/12 CFS(3-11) — OFS·D 뒤 정정 판본이 아니다
    assert r[8:13] == (10.0, 8.0, 25.0, 300_000.0, 300_000.0)
    assert r[13:17] == (2.5 * i + 5, 1_446.0, round(1_000_000.0 * i + 5), 6.5 * i + 5)
    assert r[17:22] == (float(round(3_000.6 * i + 5)), 5_000_000.0, "IFRS연결", "standard", D)
    assert r[22:] == (None, None, None)
    assert rows[1][14] == 1_444.0                    # 2024 '보통주식' 표기도 보통주다
    # E 는 보통주 행에 값이 없고 법인 축 '-' 행에만 있다 → '-' 값(무배당 0 으로 떨어지지 않게)
    assert q(out, "fi_fin_summary", f"SELECT dps FROM t WHERE ticker = '{E}' "
                                    "AND period = '2025/12' AND period_type = 'annual'") == [
        (300.0,)]


def test_fin_summary_wise_only_for_fresh_or_grace(built) -> None:
    out, _ = built
    # lapsed Dd 의 옛 WISE 재무(09-16)는 싣지 않는다. none F 는 DART 만 남는다.
    assert q(out, "fi_fin_summary", f"SELECT count(*) FROM t WHERE ticker = '{DD}'") == [(0,)]
    f_rows = q(out, "fi_fin_summary", "SELECT period, period_type, per, total_assets, fs_basis, "
                                      f"capex_basis, dps FROM t WHERE ticker = '{F}' "
                                      "AND period_type = 'annual'")
    assert f_rows == [("2025/12", "annual", None, 5_000_000.0, "DART:CFS", "ppe_parts", None)]
    # 유예 B 는 마지막 판(09-23) 값 — 09-23 이 B 의 최신 판이라 보정값 없음
    b = q(out, "fi_fin_summary", "SELECT per FROM t WHERE ticker = "
                                 f"'{B}' AND period = '2025/12' AND period_type = 'annual'")
    assert b == [(12.5 * IDX[B] + 5,)]


def test_fin_summary_non_december_fye_follows_compat_rule(built) -> None:
    """DQ-11 v3 미러 — 3월 결산 C 의 연간 확정치는 annual 이 아니다(싣지 않는다)."""
    out, _ = built
    assert q(out, "fi_fin_summary", f"SELECT count(*) FROM t WHERE ticker = '{C}' "
                                    "AND period_type = 'annual'") == [(0,)]


def test_fin_summary_last_five_quarters_from_dart(built) -> None:
    out, _ = built
    rows = q(out, "fi_fin_summary", "SELECT period, revenue, op, ni, gross_profit, total_assets, "
                                    "debt_ratio, fcf, capex, per, dps, fs_basis, available_date "
                                    f"FROM t WHERE ticker = '{A}' AND period_type = 'quarter' "
                                    "ORDER BY period DESC")
    assert [r[0] for r in rows] == ["2026/06", "2026/03", "2025/12", "2025/09", "2025/06"]
    q4 = rows[2]                                     # 4Q = 사업보고서 − 1~3Q 파생
    assert q4[1:5] == (800_000.0, 110_000.0, 90_000.0, 200_000.0)
    assert rows[0][1:3] == (round(70 * 0.29 * 1e4), round(10 * 0.29 * 1e4))
    assert rows[0][5:7] == (round(500 * 0.29 * 1e4), 25.0)
    assert all(r[7] is None and r[8] is None and r[9] is None and r[10] is None for r in rows)
    assert rows[0][11] == "DART:CFS" and rows[0][12] == dt.date(2026, 8, 14)


def test_fin_summary_quarters_switch_to_wise_with_basis_holding_and_revenue_kind(
        tmp_path: Path) -> None:
    """10-01 T-Q4: WISE 분기가 있는 종목은 분기 행을 WISE 에서(연결/별도 이름표 보존), 지주사의 연결 아닌
    분기는 값을 비우고 표식, 금융 순영업이익은 revenue_basis 'net'."""
    f = FETCH[-1]
    wq = (_wq_rows(A, f, {"매출액(수익)": [1124, 755, 644, 473, 557, 522.6],
                          "영업이익": [600, 400, 300, 200, 250, 260.0],
                          "영업이익(발표기준)": [682, 495, 374, 215, 281, 262.6],
                          "당기순이익": [513, 546, 482, 378, 417, 298.0]})
          + _wq_rows(B, f, {"매출액(수익)": [100, 110, 120, 130, 140, 150.0],
                            "영업이익(발표기준)": [10, 11, 12, 13, 14, 15.0],
                            "당기순이익": [5, 6, 7, 8, 9, 10.0]})
          + _wq_rows(C, f, {"순영업이익": [200, 210, 220, 230, 240, 250.0],
                            "영업이익": [120, 130, 140, 150, 160, 170.0],
                            "당기순이익": [90, 95, 100, 105, 110, 115.0]}))
    eq, st = make_roots(tmp_path / "src", wise_q=wq, holding=frozenset({B}))
    out = tmp_path / "factor_inputs"
    build(D_S, "morning", out, st, eq, min_eligible=5, golden_path=None)
    sql = ("SELECT period, revenue, op, ni, fs_basis, revenue_basis FROM t WHERE ticker = '{}' "
           "AND period_type = 'quarter' ORDER BY period DESC")
    a = q(out, "fi_fin_summary", sql.format(A))
    assert [r[0] for r in a] == ["2026/06", "2026/03", "2025/12", "2025/09", "2025/06"]
    assert [r[1:4] for r in a[:3]] == [(557, 281, 417), (473, 215, 378), (644, 374, 482)]  # 발표기준
    assert [r[4] for r in a] == ["WISE:IFRS연결", "WISE:IFRS연결", "WISE:IFRS별도",
                                 "WISE:IFRS별도", "WISE:IFRS별도"]
    assert {r[5] for r in a} == {"gross"}
    b = {r[0]: r for r in q(out, "fi_fin_summary", sql.format(B))}
    assert b["2025/12"][1:4] == (None, None, None)                  # 지주사 · 별도 → 비움
    assert b["2025/12"][4] == "WISE:IFRS별도|지주사제외"
    assert b["2026/06"][1:5] == (140, 14, 9, "WISE:IFRS연결")
    c = q(out, "fi_fin_summary", sql.format(C))
    assert {r[5] for r in c} == {"net"} and c[0][1:3] == (240, 160)
    # A 는 DART 분기(fin_std)도 있지만 WISE 분기가 있으면 섞지 않는다. WISE 분기가 없을 때 DART 로 가는
    # 경로는 test_fin_summary_last_five_quarters_from_dart(선택 원천 absent)가 본다.
    assert not any(str(r[4]).startswith("DART:") for r in a)


# ── 가격 · 수정주가 · 수급 · 신용 ─────────────────────────────────────────────
def test_prices_window_units_and_universe(built) -> None:
    out, _ = built
    dates = [r[0] for r in q(out, "fi_prices", f"SELECT date FROM t WHERE ticker = '{A}' "
                                               "ORDER BY date")]
    assert dates[0] == OLD_IN and OLD_OUT not in dates and dates[-1] == D
    close_d = CLOSE[A] + 100 * (len(PRICE_DAYS) - 1)
    assert q(out, "fi_prices", "SELECT close, volume, amount, price_source FROM t "
                               f"WHERE ticker = '{A}' AND date = DATE '{D}'") == [
        (close_d, 1_000 * IDX[A], close_d * 1_000 * IDX[A], "krx")]
    tickers = {r[0] for r in q(out, "fi_prices", "SELECT DISTINCT ticker FROM t")}
    assert tickers == set(LAYER)                     # ETF·폐지 종목은 없다


def test_adj_ok_is_a_step_at_each_in_window_unresolved_event(built) -> None:
    out, _ = built
    got = dict(q(out, "fi_adj_prices", f"SELECT date, adj_ok FROM t WHERE ticker = '{B}'"))
    assert got[dt.date(2026, 9, 21)] is True and got[dt.date(2026, 9, 22)] is False
    assert got[D] is False
    e = [r[0] for r in q(out, "fi_adj_prices", f"SELECT adj_ok FROM t WHERE ticker = '{E}' "
                                                "ORDER BY date")]
    # 09-18 · 09-21(사건 둘 = 한 번) · 09-22 · 09-23(사건) · 09-28
    assert e == [True, False, False, True, True]
    a = q(out, "fi_adj_prices", "SELECT bool_and(adj_ok), max(adj_factor), max(adj_close) "
                                f"FROM t WHERE ticker = '{A}'")[0]
    assert a[0] is True and a[1] == 2.0            # 창 밖 옛 사건은 표시하지 않는다


def test_flows_sixty_sessions_units_and_source_pick(built) -> None:
    out, _ = built
    a = q(out, "fi_flows", f"SELECT date, individual, foreign_investor FROM t WHERE ticker = '{A}' "
                           "ORDER BY date")
    assert [r[0] for r in a] == SESSIONS[-60:]
    n = len(SESSIONS)
    assert a[-1][1:] == (float(n), 2.0)              # 원 → 백만원, 1.5 → 2(반올림)
    b = dict(q(out, "fi_flows", f"SELECT date, individual FROM t WHERE ticker = '{B}'"))
    assert b == {D: 3_000.0, dt.date(2026, 9, 23): 5_000.0}      # 키움 우선 · 빈 키움 칸은 KIS
    assert q(out, "fi_flows", f"SELECT count(*) FROM t WHERE ticker = '{C}'") == [(0,)]


def test_credit_waits_for_the_arrival_lag(built) -> None:
    out, _ = built
    rows = q(out, "fi_credit", "SELECT date, credit_balance, credit_ratio, available_date FROM t "
                               f"WHERE ticker = '{A}' ORDER BY date")
    last = rows[-1]
    assert last[0] == SESSIONS[-4] and last[3] == D   # 실입수 T+3 세션
    assert rows[0][0] == SESSIONS[-60]
    assert last[1] == 10_000 + len(SESSIONS) - 4 and last[2] == 0.5
    assert q(out, "fi_credit", f"SELECT count(*) FROM t WHERE ticker = '{B}'") == [(0,)]


# ── 실패 경로 ────────────────────────────────────────────────────────────────
def test_evening_basis_is_not_implemented(roots, tmp_path: Path) -> None:
    with pytest.raises(FactorInputsError, match="W1-a"):
        build(D_S, "evening", tmp_path / "fi", roots[1], roots[0])


def test_unknown_basis_and_bad_date_refuse(roots, tmp_path: Path) -> None:
    with pytest.raises(FactorInputsError):
        build(D_S, "noon", tmp_path / "fi", roots[1], roots[0])
    with pytest.raises(FactorInputsError, match="YYYYMMDD"):
        build("2026-09-28", "morning", tmp_path / "fi", roots[1], roots[0])


def test_non_session_date_refuses(roots, tmp_path: Path) -> None:
    with pytest.raises(FactorInputsError, match="거래일"):
        build("20260925", "morning", tmp_path / "fi", roots[1], roots[0])


def test_evening_equity_build_refused_for_morning(tmp_path: Path) -> None:
    eq, st = make_roots(tmp_path, eq_build="e_20260928T121000_000000Z")
    with pytest.raises(FactorInputsError, match="접두어"):
        build(D_S, "morning", tmp_path / "fi", st, eq)


def test_gate_failure_commits_nothing(roots, tmp_path: Path) -> None:
    out = tmp_path / "fi"
    ok = build(D_S, "morning", out, roots[1], roots[0], min_eligible=5, golden_path=None)
    assert ok.ok
    bad = build(D_S, "morning", out, roots[1], roots[0], min_eligible=999, golden_path=None)
    assert not bad.ok and bad.status == "gate_failed"
    assert {g.name for g in bad.gates if g.status.value == "fail"} == {"FG1"}
    for t in FI_TABLES:                              # 포인터는 첫 판 그대로
        assert manifest.load(out / t / "MANIFEST.json").current_build == ok.build_id
        assert not (out / t / f"v={bad.build_id}").exists()
    assert bad.failed_report is not None and bad.failed_report.exists()
    run = json.loads((out / "_runs" / f"{D_S}_morning.json").read_text(encoding="utf-8"))
    assert run["status"] == "gate_failed" and run["build_id"] == bad.build_id
    latest = json.loads((out / "latest_morning.json").read_text(encoding="utf-8"))
    assert latest["build_id"] == ok.build_id
    assert not (out / "_tmp" / bad.build_id).exists()


def test_collection_stop_beyond_grace_fails_fresh_gate(tmp_path: Path) -> None:
    """WISE 수집이 09-16 에 멈췄다 — D* 가 D 보다 6 거래일 뒤처지면 전 종목이 '신선' 으로 보이는
    조용한 낡음이 된다. FG-fresh 가 막는다."""
    eq, st = make_roots(tmp_path, drop_fetch_after=dt.date(2026, 9, 16))
    res = build(D_S, "morning", tmp_path / "fi", st, eq, min_eligible=1, golden_path=None)
    fresh = next(g for g in res.gates if g.name == "FG-fresh")
    assert fresh.status.value == "fail" and fresh.metrics["collection_lag_over_grace"] == 1
    assert fresh.metrics["collection_lag_sessions"] == 6


def test_cli_return_codes(roots, tmp_path: Path, capsys) -> None:
    base = ["build", "--date", D_S, "--root", str(tmp_path / "fi"), "--stage-root",
            str(roots[1]), "--equity-root", str(roots[0])]
    assert cli_main(base + ["--basis", "morning", "--min-eligible", "5"]) == 0
    assert "factor_inputs ok" in capsys.readouterr().out
    assert cli_main(base + ["--basis", "morning", "--min-eligible", "999"]) == 1
    assert cli_main(base + ["--basis", "evening"]) == 2


def test_flow_and_unit_constants_match_compat() -> None:
    """수급 주체 대응·단위 상수는 compat `units` 와 같아야 한다(두 층이 같은 재료를 본다)."""
    from compat import units
    from factor_inputs import queries
    assert queries.FLOW_SOURCE == units.FLOW_SUBJECTS
    assert (queries.KRW_PER_MN, queries.KRW_PER_EOK) == (units.KRW_PER_MN, units.KRW_PER_EOK)
    from model.contracts import FLOW_SUBJECTS
    assert tuple(d for d, _ in queries.FLOW_SOURCE) == FLOW_SUBJECTS
