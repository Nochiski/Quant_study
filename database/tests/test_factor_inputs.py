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

기대 상태는 유예 G=5 기준이다 — `built` 픽스처가 유예 경로를 시험하려고 `grace_days=5` 를 명시한다.
기본값은 0(N-14)이라 기본 빌드에서는 B·C 도 lapsed 다(`test_default_grace_is_zero…`).
"""
from __future__ import annotations

import datetime as dt
import inspect
import itertools
import json
import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import duckdb
import pytest
from conftest import _make_stage_tree, allow_skips
from deliver.excel_weekly import _returns
from deliver.view import DayView
from factor_inputs import FactorInputsError, build
from factor_inputs.__main__ import main as cli_main
from model.build import load_inputs
from model.contracts import FI_TABLES, UniverseRule
from model.engines import v4_rank
from stage import manifest


@pytest.fixture(scope="module", autouse=True)
def _k17a_fixture_skips() -> Iterator[None]:
    """K1-7a — 이 모듈의 픽스처가 표본이 작아 못 재는 게이트의 SKIP 만 테스트에서 허용한다
    (운영 허용표 `src/stage/skip_allow.py` 는 그대로다)."""
    with allow_skips(
            ("factor_inputs", "FG4", "no_fixtures",
             "합성 트리에는 운영 골든(fixtures/golden.json) 종목이 없다")):
        yield


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

    def row(t: str, rcept: dt.date, deadline: dt.date, *, corr: bool = False,
            avail: dt.date | None = None) -> dict:
        # `avail` = equity 공개일. 재제출본(J-41 · N-26 4.10)은 원천 rcept_dt 보다 늦다
        a = avail or rcept
        return {"rcept_no": a.strftime("%Y%m%d") + t, "corp_code": corp(t), "rcept_dt": rcept,
                "kind": "half", "is_correction": corr, "legal_deadline": deadline,
                "delay_days": (rcept - deadline).days, "available_date": a}
    return [row(A, y(2026, 8, 14), y(2026, 8, 14)),                 # 기한 당일
            row(B, y(2026, 8, 18), y(2026, 8, 15)),                 # 기한 토 → 월 휴장 → 화 제출
            row(E, y(2026, 8, 20), y(2026, 8, 14)),                 # 지연
            row(E, y(2026, 9, 1), y(2026, 8, 14), corr=True),       # 정정은 원본이 아니다
            row(H, y(2026, 5, 20), y(2026, 5, 15)),                 # 옛 지연 …
            row(H, y(2026, 8, 13), y(2026, 8, 14)),                 # … 최근 건은 기한 안
            # D 뒤에야 공개되는 재제출본(원천 rcept_dt 08-15 는 지연) — filing_late 는 보지 않는다
            row(H, y(2026, 8, 15), y(2026, 8, 14), avail=D + dt.timedelta(days=1))]


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
# ⑤ 가격 전용 계수(equity e1.26.0) — P 는 09-22 계수 행(price_only, r = 1.25)만 있는 종목이다.
# 그날부터 cum_price_only_factor = 1.25 → 수정가 = 원가 × cum_share_factor ÷ 1.25.
# Q 는 가격 축에서 해소된 표식 행(factor_near · price_only_near)만 있는 종목이다(계수 1).
P, Q = EXTRA[0], EXTRA[1]
P_DAY, P_R = dt.date(2026, 9, 22), 1.25


def _price_only(t: str, d: dt.date) -> float:
    return P_R if t == P and d >= P_DAY else 1.0


def _adj() -> list[dict]:
    return [{"ticker": r["ticker"], "date": r["date"],
             "adj_close": float(r["close"]) * ADJ_FACTOR.get(r["ticker"], 1.0)
             / _price_only(r["ticker"], r["date"]),
             "cum_share_factor": ADJ_FACTOR.get(r["ticker"], 1.0),
             "cum_price_only_factor": _price_only(r["ticker"], r["date"]),
             "n_unadjusted_events": 0, "basis": "krx"} for r in _prices()]


def _adj_factor() -> list[dict]:
    # B: 창 안 미해결 사건(09-22) → 09-22 부터 adj_ok False. A: 창 밖 옛 사건(2020) → 영향 없음.
    # E: 창 안 사건 두 날(09-21 에 둘 · 09-23) → True · False · False · True · True 로 뒤집힌다.
    # P·Q: not-ok 행이지만 가격 축에서 해소됐다(price_resolution ≠ 'unresolved') → 뒤집지 않는다.
    def bad(t: str, d: dt.date, eid: str, res: str = "unresolved") -> dict:
        return {"ticker": t, "effective_date": d, "event_id": eid, "apply_date": d,
                "factor_ok": False, "available_date": d, "price_resolution": res}
    return [bad(E, dt.date(2026, 9, 21), "e1"), bad(E, dt.date(2026, 9, 21), "e2"),
            bad(E, dt.date(2026, 9, 23), "e3"),
            {"ticker": B, "effective_date": dt.date(2026, 9, 22), "event_id": "b1",
             "apply_date": dt.date(2026, 9, 22), "factor_ok": False,
             "available_date": dt.date(2026, 9, 22), "price_resolution": "unresolved"},
            {"ticker": A, "effective_date": dt.date(2020, 5, 4), "event_id": "a0",
             "apply_date": dt.date(2020, 5, 4), "factor_ok": False,
             "available_date": dt.date(2020, 5, 4), "price_resolution": "unresolved"},
            {"ticker": A, "effective_date": dt.date(2021, 5, 4), "event_id": "a1",
             "apply_date": dt.date(2021, 5, 4), "factor_ok": True,
             "available_date": dt.date(2021, 5, 4), "price_resolution": "factor"},
            bad(P, P_DAY, "p1", "price_only"), bad(P, P_DAY, "p2", "price_only_dup"),
            bad(Q, dt.date(2026, 9, 21), "q1", "factor_near"),
            bad(Q, dt.date(2026, 9, 23), "q2", "price_only_near")]


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
# 보고서 종류 → 누계 개월(fin_std period_start~period_end — 1분기 3 · 반기 6 · 3분기 9 · 사업 12)
REPORT_MONTHS = {"11013": 3, "11012": 6, "11014": 9, "11011": 12}


def _period_start(period_end: dt.date, report: str) -> dt.date:
    """누계 기간 첫날 — 기말 달에서 개월 수만큼 거슬러 올라간 달의 1일."""
    k = period_end.year * 12 + period_end.month - REPORT_MONTHS[report]
    return dt.date(k // 12, k % 12 + 1, 1)


def _fin_std_row(t: str, period_end: dt.date, report: str, avail: dt.date, *,
                 fs_div: str = "CFS", scale: float = 1.0, **over: object) -> dict:
    row: dict = {
        "corp_code": corp(t), "period_end": period_end, "report_code": report, "fs_div": fs_div,
        "period_start": _period_start(period_end, report),
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
        # A 2024: 1월 중(01-20) 시작 — 달력 달 수라 12개월(G-28 경계)
        _fin_std_row(A, y(2024, 12, 31), "11011", y(2025, 3, 10), scale=0.9,
                     period_start=y(2024, 1, 20)),
        _fin_std_row(A, y(2025, 12, 31), "11011", y(2026, 3, 11)),
        _fin_std_row(A, y(2025, 12, 31), "11011", y(2026, 3, 12), fs_div="OFS", scale=0.2),
        _fin_std_row(A, y(2025, 12, 31), "11011", y(2026, 10, 1), scale=2.0),     # D 뒤 정정
        _fin_std_row(A, y(2025, 3, 31), "11013", y(2025, 5, 15), scale=0.25),
        _fin_std_row(A, y(2025, 6, 30), "11012", y(2025, 8, 14), scale=0.26),
        _fin_std_row(A, y(2025, 9, 30), "11014", y(2025, 11, 14), scale=0.27),
        _fin_std_row(A, y(2026, 3, 31), "11013", y(2026, 5, 15), scale=0.28),
        _fin_std_row(A, y(2026, 6, 30), "11012", y(2026, 8, 14), scale=0.29),
        _fin_std_row(A, y(2026, 9, 30), "11014", y(2026, 11, 14), scale=0.3),      # D 뒤
        # F: 2025-06-15 설립 — 회계기간 6월~12월 = 달력 달 7개(G-28, 월 중간 시작)
        _fin_std_row(F, y(2025, 12, 31), "11011", y(2026, 3, 20), capex_basis="ppe_parts",
                     period_start=y(2025, 6, 15)),
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


def _leak_rows(day: dt.date) -> dict[str, list[dict]]:
    """D 뒤 `day` 날짜의 정보 행 — 재생이 D 뒤 자료를 담은 판을 고정했을 때의 모양(컷오버 PR-4).
    장 마감 판(T = day, D' = D)이 이 행을 읽으면 A·B·E·L 의 컨센서스·재무·배당·속성이 바뀐다.
    달력·가격·수급에는 넣지 않는다(연구 판 D' 의 거래일 축은 D' 에서 끝난다 — MD-SEAM).
    E 의 사업보고서는 D' 이전 공개지만 4Q 파생값(`q4_derived_available_date`)만 `day` 에 선다.
    L 의 공시는 법정기한이 `day`(∈ (D', T])인 원본(D' 접수)과 `day` 에 공개된 지연 원본이다."""
    stamp = day.strftime("%Y%m%d")
    matrix = [{"ticker": A, "fetched_date": day, "target_period": period, "acc_cd": acc,
               "lookback_idx": str(li + 1), "lookback": lb,
               "target_label": f"{period[:4]}/{period[4:]}", "base_date": day,
               "value": mval(A, day, period, acc, lb)}
              for period in ("202612", "202712") for acc in ACC for lb, li in LB.items()]

    def filing(t: str, rcept_no: str, rcept: dt.date, deadline: dt.date,
               avail: dt.date) -> dict:
        return {"rcept_no": rcept_no, "corp_code": corp(t), "rcept_dt": rcept, "kind": "half",
                "is_correction": False, "legal_deadline": deadline,
                "delay_days": (rcept - deadline).days, "available_date": avail}
    return {
        "stg_consensus_annual": [_annual_row(A, day, "202512", "A", 800.0, 600.0),
                                 _annual_row(A, day, "202612", "E", 901.0, 701.0),
                                 _annual_row(B, day, "202612", "E", 950.0, 750.0)],
        "stg_consensus_matrix": matrix,
        "stg_fin_wise": _fin_version(A, day, "cF3002", 50) + _fin_version(A, day, "cF4002", 50),
        "stg_fin_wise_q": _wq_rows(A, day, {"매출액(수익)": [11.0] * 6, "영업이익": [2.0] * 6,
                                            "당기순이익": [1.0] * 6}),
        "fin_std": [_fin_std_row(A, dt.date(2026, 6, 30), "11012", day, scale=5.0),
                    _fin_std_row(E, dt.date(2025, 12, 31), "11011", dt.date(2026, 3, 20),
                                 q4_derived_available_date=day)],
        "dividend_event": [{"reprt_code": "11011", "rcept_no": stamp + "000001",
                            "corp_code": corp(A), "bsns_year": "2025", "stock_knd": "보통주",
                            "stlm_dt": dt.date(2025, 12, 31), "dps_krw": 2_000.0,
                            "available_date": day}],
        # A(신선 — 마지막 신선일 값) · F(none — asof 이하 최신 값) 둘 다 T 행은 읽지 않는다
        "coverage_daily": [{"ticker": A, "date": day, "analyst_count": 99},
                           {"ticker": F, "date": day, "analyst_count": 77}],
        "universe_daily": [{"date": day, "ticker": A, "status": "delisted", "market": "KOSPI",
                            "sec_type": "common", "halt_state": True, "admin_state": True,
                            "adv20_krw": 1.0}],
        "sector_snapshot": [{"ticker": A, "snapshot_date": day, "wics_l1_cd": "G30",
                             "wics_l1_nm": "필수소비재", "wics_l2_cd": "G3010",
                             "wics_l2_nm": "식품", "available_date": day}],
        "audit_opinion": [{"corp_code": corp(A), "bsns_year": "2026", "reprt_code": "11011",
                           "bsns_year_label": "제11기(당기)", "rcept_no": stamp + "000009",
                           "adt_opinion": "의견거절의견", "adt_opinion_class": "의견거절",
                           "available_date": day}],
        "disclosure_version": [filing(A, stamp + A, day, dt.date(2026, 9, 1), day),
                               filing(L, D.strftime("%Y%m%d") + L, D, day, D),
                               filing(L, stamp + L, day, D, day)],
        "adj_factor": [{"ticker": A, "effective_date": dt.date(2026, 9, 21), "event_id": "a_leak",
                        "apply_date": dt.date(2026, 9, 21), "factor_ok": False,
                        "available_date": day, "price_resolution": "unresolved"}],
    }


def make_roots(base: Path, *, eq_build: str = EQ_BUILD,
               drop_fetch_after: dt.date | None = None,
               wise_q: list[dict] | None = None,
               holding: frozenset[str] = frozenset(),
               fin_wise: list[dict] | None = None,
               leak: dt.date | None = None) -> tuple[Path, Path]:
    """(equity_root, stage_root). `drop_fetch_after` 를 주면 그날 뒤 WISE 수집이 없다(수집 정지).
    `wise_q` 를 주면 stg_fin_wise_q 판을 만든다(없으면 선택 원천 'absent' → 분기는 DART).
    `fin_wise` 를 주면 stg_fin_wise 를 그 행으로 바꾼다(기본 `_fin_wise()`).
    `leak` 를 주면 그날 날짜의 정보 행(`_leak_rows`)을 원천에 더한다."""
    eq, st = base / "eq", base / "st"
    equity = {"trading_calendar": _calendar(), "universe_daily": _universe(),
              "security": _security(), "price_daily": _prices(), "price_adj_daily": _adj(),
              "adj_factor": _adj_factor(), "flow_daily": _flows(), "credit_daily": _credit(),
              "sector_snapshot": _sector(), "coverage_daily": _coverage_daily(),
              "fin_std": _fin_std(), "dividend_event": _dividend(), "audit_opinion": _audit(),
              "disclosure_version": _disclosure(), "corp": _corp(holding)}
    stage = {"stg_consensus_annual": _consensus_annual(), "stg_consensus_matrix": _matrix(),
             "stg_fin_wise": _fin_wise() if fin_wise is None else fin_wise}
    if wise_q is not None:
        stage["stg_fin_wise_q"] = wise_q
    if leak is not None:
        for table, extra in _leak_rows(leak).items():
            (equity if table in equity else stage).setdefault(table, []).extend(extra)
    for table, rows in equity.items():
        _make_stage_tree(eq, table, rows, build_id=eq_build)
    for table, rows in stage.items():
        if drop_fetch_after is not None:
            rows = [r for r in rows if r["fetched_date"] <= drop_fetch_after]
        _make_stage_tree(st, table, rows, build_id=ST_BUILD)
    return eq / "stage", st / "stage"


@pytest.fixture(scope="module")
def roots(tmp_path_factory) -> tuple[Path, Path]:
    return make_roots(tmp_path_factory.mktemp("fi_src"))


@pytest.fixture(scope="module")
def built(roots, tmp_path_factory):
    out = tmp_path_factory.mktemp("fi_out") / "factor_inputs"
    res = build(D_S, "morning", out, roots[1], roots[0], grace_days=5, min_eligible=5,
                golden_path=None)
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


def test_default_keep_keeps_the_first_of_four_same_day_builds(roots, tmp_path: Path) -> None:
    """E-01: 기본 keep(인자 생략)은 모델 판과 같은 60(N-17)이라 같은 날 네 번 지어도 첫 판이 8표
    모두 남는다. 3 이던 때는 네 번째 빌드가 첫 판을 지워, 그 판을 `fi_build_id` 로 가리키는 모델
    판의 엑셀 인계(주간·옛 날짜 일일)가 `DeliverError` 로 실패했다."""
    out = tmp_path / "fi"
    runs = [build(D_S, "morning", out, roots[1], roots[0], min_eligible=5, golden_path=None)
            for _ in range(4)]
    assert all(r.ok for r in runs), [(r.build_id, g.name, g.detail) for r in runs
                                     for g in r.gates if g.status.value == "fail"]
    for t in FI_TABLES:
        assert len(manifest.load(out / t / "MANIFEST.json").builds) == 4, t
        assert (out / t / f"v={runs[0].build_id}").is_dir(), t


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


def test_default_grace_is_zero_so_absent_estimates_lapse(roots, tmp_path: Path) -> None:
    """기본 유예 0(N-14) — 최신 수집일(D*)에 추정치가 없으면 나이와 상관없이 lapsed 로 빠진다."""
    assert UniverseRule().coverage_grace_days == 0
    out = tmp_path / "fi"
    res = build(D_S, "morning", out, roots[1], roots[0], min_eligible=1, golden_path=None)
    assert res.ok
    got = {r[0]: r[1:] for r in q(out, "fi_universe",
           "SELECT ticker, coverage_state, coverage_age_days, eligible, exclude_reason FROM t")}
    assert got[B] == ("lapsed", 1, False, "estimates_lapsed")
    assert got[C] == ("lapsed", 5, False, "estimates_lapsed")
    assert got[A][0] == "fresh" and got[E][0] == "fresh"
    assert res.coverage["n_lapsed_dropped"] == 3
    assert "grace" not in res.coverage["counts"]


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


def test_fin_summary_annual_period_months_from_dart_period(built) -> None:
    """G-28(N-25 Q5): 연간 행 기간(개월) = DART fin_std period_start~period_end 의 달력 달 수
    (양끝 달 포함). F 는 2025-06-15 시작 → 7(월 중간 시작도 그 달을 센다), A 2024 는 01-20
    시작 → 12(1월 중 설립이면 12). DART 연간 행이 없는(WISE 만) 기·분기 행은 NULL(모름)."""
    out, _ = built
    sql = ("SELECT ticker, period, period_months FROM t WHERE period_type = 'annual' "
           f"AND ticker IN ('{A}', '{E}', '{F}') ORDER BY ticker, period DESC")
    assert q(out, "fi_fin_summary", sql) == [
        (A, "2025/12", 12), (A, "2024/12", 12), (E, "2025/12", None), (E, "2024/12", None),
        (F, "2025/12", 7)]
    assert q(out, "fi_fin_summary", "SELECT count(*) FROM t WHERE period_type = 'quarter' "
                                    "AND period_months IS NOT NULL") == [(0,)]


def test_fin_summary_annual_revenue_reads_financial_accounts(tmp_path: Path) -> None:
    """금융업 WISE cF3002 는 최상위 매출 계정이 '매출액(수익)' 이 아니다 — 보험 '영업수익'(총액) ·
    은행·증권 '순영업이익'(순액). 연간 매출도 분기(`WISE_Q_REVENUE_*`)와 같은 계정으로 고른다
    (10-07 scope 금융 28종목 2025 매출 빈칸). 둘 이상 있으면 총액 계정이 순서대로('매출액(수익)' →
    '영업수익') 이기고 순액은 마지막이다."""
    x1, x2 = EXTRA[0], EXTRA[1]
    renamed = {B: "영업수익", E: "순영업이익", x1: "영업수익"}
    # 같은 종목에 더 싣는 매출 계정 — 값은 기본 + 오프셋(어느 계정이 골렸는지 값으로 가른다)
    added = {H: (("순영업이익", 50_000.0),), x1: (("순영업이익", 50_000.0),),
             x2: (("영업수익", 30_000.0),)}
    rows = []
    for r in _fin_wise():
        if r["acc_nm"] != "매출액(수익)":
            rows.append(r)
            continue
        t = r["ticker"]
        rows.append(dict(r, acc_nm=renamed[t]) if t in renamed else r)
        for k, (nm, off) in enumerate(added.get(t, ()), start=1):
            rows.append(dict(r, acc_nm=nm, accode=f"29{k:04d}", seq=90 + k,
                             **{f"val_{i}": r[f"val_{i}"] + off for i in range(1, 7)}))
    eq, st = make_roots(tmp_path / "src", fin_wise=rows)
    out = tmp_path / "factor_inputs"
    build(D_S, "morning", out, st, eq, grace_days=5, min_eligible=5, golden_path=None)
    got = {(r[0], r[1]): r[2:] for r in q(
        out, "fi_fin_summary", "SELECT ticker, period, revenue, revenue_basis FROM t "
                               "WHERE period_type = 'annual'")}

    def base(t: str, slot: int = 5) -> float:
        return 10_000.4 * IDX[t] + slot

    # 2025/12 = WISE 5번 칸(기본값 10,000.4 × IDX + 5 — 모두 마지막 판이라 보정값 없음)
    assert got[(A, "2025/12")] == (float(round(base(A))), "gross")
    assert got[(B, "2025/12")] == (float(round(base(B))), "gross")
    assert got[(E, "2025/12")] == (float(round(base(E))), "net")
    assert got[(E, "2024/12")] == (float(round(base(E, 4))), "net")
    # 우선순위: 매출액 + 순영업이익 → 매출액 · 영업수익 + 순영업이익 → 영업수익 ·
    # 매출액 + 영업수익 → 매출액
    assert got[(H, "2025/12")] == (float(round(base(H))), "gross")
    assert got[(x1, "2025/12")] == (float(round(base(x1))), "gross")
    assert got[(x2, "2025/12")] == (float(round(base(x2))), "gross")


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
                            "당기순이익": [90, 95, 100, 105, 110, 115.0]})
          # E: 매출 계정 셋이 다 있으면 총액 순서대로 '매출액(수익)' 이 이긴다(순액은 마지막)
          + _wq_rows(E, f, {"순영업이익": [900, 910, 920, 930, 940, 950.0],
                            "영업수익": [700, 710, 720, 730, 740, 750.0],
                            "매출액(수익)": [300, 310, 320, 330, 340, 350.0],
                            "영업이익": [30, 31, 32, 33, 34, 35.0],
                            "당기순이익": [20, 21, 22, 23, 24, 25.0]}))
    eq, st = make_roots(tmp_path / "src", wise_q=wq, holding=frozenset({B}))
    out = tmp_path / "factor_inputs"
    build(D_S, "morning", out, st, eq, grace_days=5, min_eligible=5, golden_path=None)
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
    e = q(out, "fi_fin_summary", sql.format(E))
    assert {r[5] for r in e} == {"gross"} and [r[1] for r in e] == [340, 330, 320, 310, 300]
    # A 는 DART 분기(fin_std)도 있지만 WISE 분기가 있으면 섞지 않는다. WISE 분기가 없을 때 DART 로 가는
    # 경로는 test_fin_summary_last_five_quarters_from_dart(선택 원천 absent)가 본다.
    assert not any(str(r[4]).startswith("DART:") for r in a)


# ── 배포 묶음 7-2: stage 가 같은 원문을 접은 stg_fin_wise 를 (종목, ep) 단위로 읽는다 ─────────
def _fin_version(t: str, f: dt.date, ep: str, off: float) -> list[dict]:
    """stg_fin_wise 한 판(종목 · 수집일 · ep) — 값 = 기본 × IDX + 칸 번호 + off."""
    rows = []
    for seq, (e, accode, p_accode, nm, base) in enumerate(_FIN_ITEMS):
        if e != ep:
            continue
        row: dict = {"ticker": t, "fetched_date": f, "ep": e, "seq": seq, "accode": accode,
                     "p_accode": p_accode, "acc_nm": nm, "fs_basis": "IFRS연결", "freq": "연간"}
        row.update({f"period_label_{i}": lab for i, lab in enumerate(_labels(t), start=1)})
        row.update({f"val_{i}": base * IDX[t] + i + off for i in range(1, 7)})
        rows.append(row)
    return rows


def _fin_summary_at(roots: tuple[Path, Path], d: dt.date, sql: str) -> list[tuple]:
    """판 빌드 없이 D 의 fi_fin_summary 쿼리만 돌리고 `_fi_fin_summary` 위에서 `sql` 을
    읽는다 — D 마다 다른 게이트(유니버스·가격)를 빼고 재무 스냅샷 선택만 본다.
    유니버스 = security 전부, 유예 G = 5."""
    from factor_inputs import queries
    from factor_inputs.build import EQUITY_SOURCES, STAGE_SOURCES, _resolve
    con = duckdb.connect()
    try:
        for root, tables, stage in ((roots[0], EQUITY_SOURCES, False),
                                    (roots[1], STAGE_SOURCES, True)):
            for name, expr in _resolve(root, tables, stage)[1].items():
                con.execute(f'CREATE TEMP VIEW "{name}" AS SELECT * FROM {expr}')
        p = queries.Params(d=d.isoformat(), fy=f"{d.year}12", price_from=d.isoformat(),
                           flow_from=d.isoformat(), grace_days=5, credit_lag=3)
        con.execute(queries.calendar_sql())
        for cov_sql in queries.coverage_sqls(p):
            con.execute(cov_sql)
        con.execute("CREATE TEMP TABLE _fi_universe AS SELECT ticker FROM security")
        con.execute(queries.fin_summary_sql(p))
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_fin_summary_takes_each_ep_from_its_own_latest_version(tmp_path: Path) -> None:
    """G1(7-2): 접힌 stage 에서 A 의 cF3002(손익) 판은 09-23 뿐이고 cF4002(지표)만 D 에 새 판이다.
    D 의 연간 행은 손익 = 09-23 판(값 +7), 지표 = D 판이어야 한다. 옛 쿼리(종목별 max 한 날짜)는
    D 를 골라 cF3002 행이 없어 매출·영업이익·순이익·매출총이익·fs_basis 가 **조용히** 비었다."""
    rows = [r for r in _fin_wise()
            if not (r["ticker"] == A and r["ep"] == "cF3002" and r["fetched_date"] == D)]
    eq, st = make_roots(tmp_path / "src", fin_wise=rows)
    out = tmp_path / "factor_inputs"
    res = build(D_S, "morning", out, st, eq, grace_days=5, min_eligible=5, golden_path=None)
    assert res.ok, [(g.name, g.detail) for g in res.gates if g.status.value == "fail"]
    got = q(out, "fi_fin_summary", "SELECT period, revenue, op, ni, gross_profit, fs_basis, "
                                   "revenue_basis, eps, per, available_date FROM t "
                                   f"WHERE ticker = '{A}' AND period_type = 'annual' "
                                   "ORDER BY period DESC")
    i = IDX[A]

    def old(base: float, slot: int) -> float:   # 09-23 판 값(fin_val +7), SQL 반올림(.5 올림)
        return float(int(base * i + slot + 7 + 0.5))
    assert got == [
        ("2025/12", old(10_000.4, 5), old(1_000.5, 5), old(800.2, 5), old(3_000.6, 5),
         "IFRS연결", "gross", float(round(1_234.4 * i + 5)), 12.5 * i + 5, D),
        ("2024/12", old(10_000.4, 4), old(1_000.5, 4), old(800.2, 4), old(3_000.6, 4),
         "IFRS연결", "gross", float(round(1_234.4 * i + 4)), 12.5 * i + 4, D)]
    fg1 = next(g for g in res.gates if g.name == "FG1")
    assert fg1.metrics["n_eligible_with_wise_is"] == fg1.metrics["n_eligible_with_wise_fin"]


def test_fin_summary_reads_folded_versions_per_ep_on_each_d(tmp_path: Path) -> None:
    """접힌 stage 를 각 D 에서 읽는다 — A 의 cF3002 는 09-14 A판 · 09-16 B판 · 09-18 A판
    (되돌아온 판은 stage 가 남긴다), 그 사이 날은 행이 없다. cF4002 는 매일 새 판. 손익은
    A·A·B·B·A, 지표는 그날 판, available_date = D. X 는 두 ep 다 09-14 판 하나(그 뒤 접힘) —
    값은 그대로이고 available_date 는 '처음 본 날' 09-14(≤ D)."""
    d = FETCH[:5]
    x = EXTRA[0]
    assert [f.day for f in d] == [14, 15, 16, 17, 18]
    rows = (_fin_version(A, d[0], "cF3002", 0) + _fin_version(A, d[2], "cF3002", 100)
            + _fin_version(A, d[4], "cF3002", 0)
            + [r for f in d for r in _fin_version(A, f, "cF4002", f.day)]
            + _fin_version(x, d[0], "cF3002", 0) + _fin_version(x, d[0], "cF4002", 0))
    roots = make_roots(tmp_path / "src", fin_wise=rows)
    sql = ("SELECT ticker, revenue, ni, eps, available_date FROM _fi_fin_summary WHERE "
           "period = '2025/12' AND period_type = 'annual' AND ticker IN "
           f"('{A}', '{x}') ORDER BY ticker")
    ia, ix = IDX[A], IDX[x]
    for day, is_off in zip(d, (0, 0, 100, 100, 0), strict=True):
        got = _fin_summary_at(roots, day, sql)
        assert got == [
            (A, float(round(10_000.4 * ia + 5 + is_off)), float(round(800.2 * ia + 5 + is_off)),
             float(round(1_234.4 * ia + 5 + day.day)), day),
            (x, float(round(10_000.4 * ix + 5)), float(round(800.2 * ix + 5)),
             float(round(1_234.4 * ix + 5)), d[0])], day


def test_fg1_wise_income_statement_ratio_on_the_full_tree(built) -> None:
    """FG1 손익 비율(7-2 D7-8, 기록형) — 기본 합성 트리에서 손익(op·ni)이 있는 eligible 은
    per·eps 가 있는 eligible 과 같은 9곳(eligible 10 중 C 는 3월 결산이라 연간 행이 없다)."""
    _, res = built
    m = next(g for g in res.gates if g.name == "FG1").metrics
    assert (m["n_eligible_with_wise_fin"], m["n_eligible_with_wise_is"]) == (9, 9)
    assert m["eligible_wise_is_ratio"] == 0.9 and m["fin_coverage_min"] == 0.9


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


def test_price_resolved_rows_keep_adj_ok_and_fold_into_adj_factor(built) -> None:
    """⑤(equity e1.26.0) — 가격 축에서 해소된 not-ok 행(price_only · _dup · _near · factor_near)은
    `adj_ok` 를 뒤집지 않고, `adj_factor` = cum_share ÷ cum_price_only 라 '원가 × 계수 = 수정가' 가
    ⑤ 종목에서도 선다(fi1.3.0). 옛 fi(`NOT factor_ok` · `cum_share_factor`)는 P 를 09-22 부터
    False · 계수 1 로 냈다."""
    out, _ = built
    for t in (P, Q):
        assert q(out, "fi_adj_prices", "SELECT bool_and(adj_ok), count(*) FROM t "
                                       f"WHERE ticker = '{t}'") == [(True, len(PRICE_DAYS))], t
    p = dict(q(out, "fi_adj_prices", f"SELECT date, adj_factor FROM t WHERE ticker = '{P}'"))
    assert p[dt.date(2026, 9, 21)] == 1.0 and p[P_DAY] == p[D] == 1.0 / P_R
    con = duckdb.connect()
    try:
        for t in ("fi_prices", "fi_adj_prices"):
            cur = manifest.load(out / t / "MANIFEST.json").current_build
            con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet("
                        f"'{out / t / f'v={cur}' / 'part0.parquet'}', hive_partitioning=false)")
        got = con.execute(
            "SELECT count(*), count(*) FILTER (WHERE abs(a.adj_factor * p.close - a.adj_close) "
            "> 1e-12 * a.adj_close), (SELECT count(*) FROM fi_adj_prices) "
            "FROM fi_adj_prices a JOIN fi_prices p USING (ticker, date)").fetchone()
    finally:
        con.close()
    assert got is not None and got[0] == got[2] > 0 and got[1] == 0     # A 분할 · P ⑤ 포함 전 행


def test_price_resolved_stock_gets_v4_and_weekly_returns(built) -> None:
    """실제 fi 판을 v4 `_ret`·주간 엑셀 `_returns` 에 그대로 넣는다(배포 묶음 6-3 G1). ⑤ 만 있는
    P 는 09-22 를 넘는 창에서도 값이 나오고(옛 fi: '수정주가미해결'), 가격 축 미해결 B 는 그대로
    결측이다."""
    out, res = built
    lag = len(PRICE_DAYS) - 1                         # 09-18 → 09-28 — 09-22 를 넘는다
    want = (CLOSE[P] + 100 * lag) / P_R / CLOSE[P] - 1.0
    fi, _ = load_inputs(out, res.build_id, D.isoformat(), "morning")
    s = v4_rank._series(fi, {P, B}, D.isoformat())
    assert v4_rank._ret(s[P], lag).raw == pytest.approx(want, rel=1e-12)
    assert v4_rank._ret(s[B], lag) == v4_rank.Val(None, v4_rank.ADJ_UNRESOLVED)
    base = cast(DayView, SimpleNamespace(run=SimpleNamespace(fi_build_id=res.build_id)))
    week = _returns(out, base, PRICE_DAYS[0].isoformat(), D.isoformat())
    assert week[P] == pytest.approx(want * 100.0, rel=1e-12)
    assert week[B] == "결측(수정주가미해결)"


# ── 제한폭 초과 미해결 점프 표식 adj_jump_ok(T-9 · H1-4, fi1.6.0) ─────────────────
def test_within_limit_unresolved_events_leave_adj_jump_ok_true(built) -> None:
    """기본 합성 트리의 미해결 사건(B 09-22 · E 09-21·09-23)은 하루 등락이 1% 안이다 — adj_ok 는
    뒤집혀도 adj_jump_ok 는 전 행 True(scope 모멘텀·20일 변동성을 건드리지 않는다)."""
    out, _ = built
    for t in (B, E):
        assert q(out, "fi_adj_prices", "SELECT bool_and(adj_jump_ok), bool_and(adj_ok) FROM t "
                                       f"WHERE ticker = '{t}'") == [(True, False)], t
    assert q(out, "fi_adj_prices", "SELECT count(*) FROM t WHERE adj_jump_ok IS NULL "
                                   "OR NOT adj_jump_ok") == [(0,)]


JUMP_D, JUMP_D2 = dt.date(2015, 7, 31), dt.date(2015, 8, 4)
# 종목 → (사건 적용일, 점프 날, 배율, price_resolution). 점프 날 종가 = 전날 종가 × 배율(정수 원),
# 그 밖의 날은 전날 + 10원(0.1% 안팎). 세션 = 2015-05-04 ~ 08-14 평일(휴장 없음).
JUMP_CASES: dict[str, tuple[dt.date, dt.date, float, str]] = {
    "B1": (dt.date(2015, 6, 12), dt.date(2015, 6, 12), 1.2, "unresolved"),   # 15% 시절 +20%
    "B2": (dt.date(2015, 6, 15), dt.date(2015, 6, 15), 1.2, "unresolved"),   # 30% 첫날 +20%
    "XB": (dt.date(2015, 6, 17), dt.date(2015, 6, 12), 1.2, "unresolved"),   # 적용일은 30% 시절,
                                                                             # 점프(−3 세션)는 15% 시절
    "LU": (dt.date(2015, 7, 1), dt.date(2015, 7, 1), 1.3, "unresolved"),     # 상한가 +30% 그대로
    "NB6": (dt.date(2015, 7, 1), dt.date(2015, 7, 9), 1.4, "unresolved"),    # 적용일 +6 세션
    "NB7": (dt.date(2015, 7, 1), dt.date(2015, 7, 10), 1.4, "unresolved"),   # 적용일 +7 세션
    "PRE6": (dt.date(2015, 7, 1), dt.date(2015, 6, 23), 0.6, "unresolved"),  # 적용일 −6 세션
    "LATE": (dt.date(2015, 7, 30), dt.date(2015, 8, 3), 0.5, "unresolved"),  # 점프가 D 뒤
    "RES": (dt.date(2015, 7, 1), dt.date(2015, 7, 1), 0.5, "price_only"),    # 가격 축 해소
}


def _jump_sessions() -> list[dt.date]:
    out, day = [], dt.date(2015, 5, 4)
    while day <= dt.date(2015, 8, 14):
        if day.weekday() < 5:
            out.append(day)
        day += dt.timedelta(days=1)
    return out


def _mini_adj_prices(d: dt.date, cases: dict[str, tuple[dt.date, dt.date, float, str]],
                     more_events: tuple[tuple[str, dt.date], ...] = ()) -> list[dict[str, object]]:
    """작은 equity 표(달력·price_adj_daily·adj_factor) 위에서 fi_adj_prices SQL 만 돌린 결과 행
    (계약 열 전부, 종목·날짜순). `cases` 모양은 JUMP_CASES. `more_events` = 같은 종목에 더할 미해결
    사건(종목, 적용일) — 가격은 바꾸지 않는다."""
    from factor_inputs import queries
    sessions = _jump_sessions()
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE trading_calendar (date DATE)")
        con.executemany("INSERT INTO trading_calendar VALUES (?)", [(s,) for s in sessions])
        con.execute("CREATE TABLE price_adj_daily (ticker VARCHAR, date DATE, adj_close DOUBLE, "
                    "cum_share_factor DOUBLE, cum_price_only_factor DOUBLE, basis VARCHAR)")
        con.execute("CREATE TABLE adj_factor (ticker VARCHAR, apply_date DATE, "
                    "available_date DATE, price_resolution VARCHAR)")
        for t, (apply, jump, ratio, res) in cases.items():
            px, rows = 10_000, []
            for i, s in enumerate(sessions):
                px = round(px * ratio) if s == jump else px + (10 if i else 0)
                rows.append((t, s, float(px), 1.0, 1.0, "krx"))
            con.executemany("INSERT INTO price_adj_daily VALUES (?, ?, ?, ?, ?, ?)", rows)
            con.execute("INSERT INTO adj_factor VALUES (?, ?, ?, ?)", [t, apply, apply, res])
        for t, apply in more_events:
            con.execute("INSERT INTO adj_factor VALUES (?, ?, ?, 'unresolved')", [t, apply, apply])
        con.execute("CREATE TABLE _fi_universe AS SELECT DISTINCT ticker FROM price_adj_daily")
        p = queries.Params(d=d.isoformat(), fy=f"{d.year}12",
                           price_from=(d - dt.timedelta(days=queries.PRICE_WINDOW_DAYS)).isoformat(),
                           flow_from=d.isoformat(), grace_days=0, credit_lag=0)
        con.execute(queries.calendar_sql())
        con.execute(queries.adj_prices_sql(p))
        rel = con.execute("SELECT * FROM _fi_adj_prices ORDER BY ticker, date")
        cols = [c[0] for c in rel.description]
        return [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]
    finally:
        con.close()


def _adj_steps_at(d: dt.date) -> dict[str, list[tuple[dt.date, bool, bool]]]:
    """JUMP_CASES 판 → 종목 → [(날짜, adj_ok, adj_jump_ok)] 날짜순."""
    got: dict[str, list[tuple[dt.date, bool, bool]]] = {}
    for r in _mini_adj_prices(d, JUMP_CASES):
        got.setdefault(str(r["ticker"]), []).append(
            (r["date"], bool(r["adj_ok"]), bool(r["adj_jump_ok"])))  # type: ignore[arg-type]
    return got


@pytest.fixture(scope="module")
def jump_steps() -> dict[dt.date, dict[str, list[tuple[dt.date, bool, bool]]]]:
    return {d: _adj_steps_at(d) for d in (JUMP_D, JUMP_D2)}


def _first_false(rows: list[tuple[dt.date, bool, bool]], col: int) -> dt.date | None:
    """col 1 = adj_ok · 2 = adj_jump_ok 가 처음 False 인 날(없으면 None)."""
    return next((r[0] for r in rows if not r[col]), None)


def test_adj_jump_ok_steps_only_at_unresolved_events_with_a_limit_breaking_neighbour(
        jump_steps) -> None:
    """adj_jump_ok 는 adj_ok 와 같은 모양의 계단이되 **점프 행**에서 뒤집힌다 — 점프 행 = 미해결
    사건 적용일 ±6 세션 안에서 |수정수익률|이 그날 가격제한폭을 넘는 행(MAJOR-1: 적용일에서 뒤집으면
    적용일과 점프 사이에서 시작하는 창이 점프를 품고도 빠져나간다). 상한가 그대로(+30%, 부동소수
    0.30000000000000004)는 넘지 않은 것이다. 가격 축에서 해소된 사건은 점프가 있어도 세지 않는다.
    adj_ok 는 그대로(미해결이면 점프 크기와 무관하게 적용일에서 뒤집힌다)."""
    got = jump_steps[JUMP_D]
    assert {t: _first_false(v, 2) for t, v in got.items()} == {
        "B1": dt.date(2015, 6, 12), "B2": None, "XB": dt.date(2015, 6, 12), "LU": None,
        "NB6": dt.date(2015, 7, 9), "NB7": None, "PRE6": dt.date(2015, 6, 23), "LATE": None,
        "RES": None}
    assert {t: _first_false(v, 1) for t, v in got.items()} == {
        t: (None if res != "unresolved" else apply)
        for t, (apply, _, _, res) in JUMP_CASES.items()}
    # 점프 행이 하나라 그 행부터 끝까지 False — 계단 하나
    nb6 = [r[2] for r in got["NB6"]]
    k = nb6.index(False)
    assert all(nb6[:k]) and not any(nb6[k:])


def test_adj_jump_price_limit_switches_on_2015_06_15(jump_steps) -> None:
    """가격제한폭은 수익률 날짜 기준 — 2015-06-12(금) +20% 는 15% 를 넘어 점프, 2015-06-15(월)
    +20% 는 30% 안이라 점프가 아니다. 적용일이 06-17(30% 시절)이어도 인접 점프가 06-12 면 15% 로
    잰다(XB — 적용일 기준으로 고르면 놓친다). 계단은 점프 행 06-12 에서 뒤집힌다."""
    got = jump_steps[JUMP_D]
    assert _first_false(got["B1"], 2) == dt.date(2015, 6, 12)
    assert _first_false(got["B2"], 2) is None and _first_false(got["B2"], 1) == dt.date(2015, 6, 15)
    assert _first_false(got["XB"], 2) == dt.date(2015, 6, 12)


def test_adj_jump_does_not_look_past_d(jump_steps) -> None:
    """적용일 07-30(D−1) 사건의 점프가 08-03(D+1)이면 D 판은 모른다(price_adj_daily 에 D 뒤 행이
    있어도) — 08-04 판에서야 점프 행 08-03 부터 False 다."""
    assert _first_false(jump_steps[JUMP_D]["LATE"], 2) is None
    assert _first_false(jump_steps[JUMP_D2]["LATE"], 2) == dt.date(2015, 8, 3)


def test_jump_row_shared_by_two_unresolved_events_flips_once() -> None:
    """MINOR-A: 같은 종목의 미해결 사건 둘(적용일 07-01 · 07-06)이 같은 점프 행(07-03 +100%)을 이웃
    (±6 세션)으로 공유한다. 점프 행은 (종목, 날짜) 하나로 세므로 계단은 07-03 에서 한 번만 뒤집힌다 —
    사건마다 세면 두 번 뒤집혀 07-03 부터 다시 True 가 되어(짝수) 점프가 통째로 사라진다. adj_ok 는
    사건 축이라 07-01·07-06 두 번 뒤집힌다."""
    rows = _mini_adj_prices(JUMP_D, {"SH": (dt.date(2015, 7, 1), dt.date(2015, 7, 3), 2.0,
                                            "unresolved")},
                            more_events=(("SH", dt.date(2015, 7, 6)),))
    jump_ok = [(r["date"], r["adj_jump_ok"]) for r in rows]
    flips = [d for (_, prev), (d, cur) in itertools.pairwise(jump_ok) if prev != cur]
    assert flips == [dt.date(2015, 7, 3)]
    assert all(ok for d, ok in jump_ok if d < dt.date(2015, 7, 3))
    assert not any(ok for d, ok in jump_ok if d >= dt.date(2015, 7, 3))
    ok = [(r["date"], r["adj_ok"]) for r in rows]
    assert [d for (_, prev), (d, cur) in itertools.pairwise(ok) if prev != cur] == [
        dt.date(2015, 7, 1), dt.date(2015, 7, 6)]


def test_scope_window_starting_between_apply_date_and_a_late_jump_is_masked() -> None:
    """리뷰 재현(MAJOR-1): 적용일 a(07-01) 사건의 실제 점프(+100%)가 a+3 세션이고, D 의 r1m 창(21행)이
    a+2 에서 시작하면 창이 점프를 품는다. 계단을 적용일에서 뒤집으면 창 안 표식이 전부 False(한결같음)
    라 r1m ≈ +100% 가 그대로 남았다(과소 결측 — 조용한 오염). 점프 행에서 뒤집으면 결측이다.
    fi SQL 이 낸 행을 그대로 scope 엔진에 넣는다."""
    from dataclasses import replace

    from model import registry
    from model.contracts import FactorInputs
    from model.engines import ENGINES

    sessions = _jump_sessions()
    a = dt.date(2015, 7, 1)
    k = sessions.index(a)
    d = sessions[k + 2 + 20]                      # r1m 창 = 최근 21행 = [a+2, D]
    adj = _mini_adj_prices(d, {"R1": (a, sessions[k + 3], 2.0, "unresolved")})

    def row(table: str, **v: object) -> dict[str, object]:
        return {c: v.get(c) for c in FI_TABLES[table].column_names}

    prices = [row("fi_prices", ticker=r["ticker"], date=r["date"],
                  close=round(float(r["adj_close"])))   # type: ignore[arg-type]
              for r in adj]
    uni = [row("fi_universe", ticker="R1", date=d, market_cap=5000.0, eligible=True)]
    fi = FactorInputs(d.isoformat(), "morning", "mini",
                      {"fi_prices": prices, "fi_adj_prices": adj, "fi_universe": uni,
                       "fi_flows": [], "fi_consensus": [], "fi_fin_summary": []})
    spec = registry.get("scope@1.0")
    off = replace(spec, params={**spec.params, "adj_jump_missing": False})
    engine = ENGINES["v3_zscore"]
    assert engine.run(off, fi).scores[0]["r1m"] == pytest.approx(1.0, abs=0.05)   # 규칙 없으면 남는다
    res = engine.run(spec, fi)
    assert res.scores[0]["r1m"] is None
    assert res.meta["adj_jump_masked"]["r1m"] == 1                  # type: ignore[index]


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
def test_evening_basis_without_pin_is_refused(roots, tmp_path: Path) -> None:
    """장 마감 판(evening)은 `--builds-from` 으로 직전 거래일 확정판을 고정해야만 짓는다(T-2 ·
    PR-3 가드). 고정 없이 current 판을 읽으면 낡은 판을 조용히 쓰게 된다(P1)."""
    with pytest.raises(FactorInputsError, match="--builds-from"):
        build("20260929", "evening", tmp_path / "fi", roots[1], roots[0])
    assert not (tmp_path / "fi").exists()


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


@pytest.mark.parametrize(("table", "column"), [("price_adj_daily", "cum_price_only_factor"),
                                                ("adj_factor", "price_resolution")])
def test_old_equity_build_without_price_only_columns_refuses(tmp_path: Path, table: str,
                                                            column: str) -> None:
    """fi1.3.0 은 equity e1.26.0 판의 ⑤ 열을 읽는다 — 그 열이 없는 옛 판이면 판을 만들지 않고
    멈춘다(판 섞임이 조용히 지나가지 않게, P1)."""
    eq, st = make_roots(tmp_path)
    rows = {"price_adj_daily": _adj, "adj_factor": _adj_factor}[table]()
    _make_stage_tree(tmp_path / "eq", table, [{k: v for k, v in r.items() if k != column}
                                              for r in rows],
                     build_id="m_20260929T000600_000000Z")
    with pytest.raises(FactorInputsError, match=rf"table={table} .*{column}"):
        build(D_S, "morning", tmp_path / "fi", st, eq, min_eligible=5, golden_path=None)
    assert not (tmp_path / "fi" / "fi_adj_prices" / "MANIFEST.json").exists()
    tmp = tmp_path / "fi" / "_tmp"
    assert not tmp.exists() or not any(tmp.iterdir())


def _set_stage_gates(stage_root: Path, table: str, recorded: list[dict]) -> None:
    """stage 판 `_meta.json` 의 게이트 기록을 바꾼다 — fi 입력 가드가 읽는 곳(K1-7a)."""
    for meta in (stage_root / table).glob("v=*/**/_meta.json"):
        m = json.loads(meta.read_text(encoding="utf-8"))
        m["gates"] = recorded
        meta.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")


def _gate(name: str, status: str, detail: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail, "metrics": {}}


def test_stage_input_skips_inside_the_skip_table_are_used(tmp_path: Path) -> None:
    """K1-7a — WISE stage 판의 정상 SKIP(G4 골든 없음 · G9 교차 소스 없음 · G5 첫 빌드)은 쓴다."""
    eq, st = make_roots(tmp_path)
    _set_stage_gates(st, "stg_fin_wise", [
        _gate("G1", "pass"), _gate("G4", "skip", "no_fixtures"),
        _gate("G5", "skip", "no_baseline"), _gate("G9", "skip", "no_cross_check")])
    res = build(D_S, "morning", tmp_path / "fi", st, eq, min_eligible=5, golden_path=None)
    assert res.ok, [(g.name, g.detail) for g in res.gates if g.status.value == "fail"]


def test_stage_input_skip_outside_the_skip_table_refuses_the_build(tmp_path: Path) -> None:
    """음성 대조 — 교차 원장을 못 붙여 G9 가 건너뛴 판(ledger_unavailable)은 fi 가 쓰지 않는다."""
    eq, st = make_roots(tmp_path)
    _set_stage_gates(st, "stg_consensus_matrix", [
        _gate("G1", "pass"), _gate("G9", "skip", "ledger_unavailable:wise")])
    with pytest.raises(FactorInputsError,
                       match=r"table=stg_consensus_matrix .*G9:ledger_unavailable"):
        build(D_S, "morning", tmp_path / "fi", st, eq, min_eligible=5, golden_path=None)
    assert not (tmp_path / "fi" / "fi_universe" / "MANIFEST.json").exists()


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
    # D-09: 같은 날 성공 기록은 FAIL 재실행이 덮지 않는다(실패는 `_failed/` 에만)
    run = json.loads((out / "_runs" / f"{D_S}_morning.json").read_text(encoding="utf-8"))
    assert run["status"] == "ok" and run["build_id"] == ok.build_id
    assert bad.run_manifest_kept
    latest = json.loads((out / "latest_morning.json").read_text(encoding="utf-8"))
    assert latest["build_id"] == ok.build_id
    assert not (out / "_tmp" / bad.build_id).exists()


@pytest.mark.parametrize("before", [None, b'{"status": "gate_failed"}', b'{"status": "ok", ',
                                    b'["ok"]', b"\xff\xfe"],
                         ids=["absent", "failed", "broken", "not_object", "not_utf8"])
def test_failed_build_writes_the_run_record_unless_the_day_is_ok(roots, tmp_path: Path,
                                                                 before: bytes | None) -> None:
    """D-09 판정의 나머지 갈래 — 같은 날 기록이 없거나 · status ≠ ok 거나 · 내용이 깨졌으면
    (JSON · UTF-8 · 객체가 아님) FAIL 도 `_runs` 를 쓴다."""
    out = tmp_path / "fi"
    run_path = out / "_runs" / f"{D_S}_morning.json"
    if before is not None:
        run_path.parent.mkdir(parents=True)
        run_path.write_bytes(before)
    bad = build(D_S, "morning", out, roots[1], roots[0], min_eligible=999, golden_path=None)
    run = json.loads(run_path.read_text(encoding="utf-8"))
    assert not bad.ok and run["status"] == "gate_failed" and run["build_id"] == bad.build_id
    assert not bad.run_manifest_kept


@pytest.mark.skipif(os.geteuid() == 0, reason="root 는 권한 0 파일도 읽는다")
def test_unreadable_same_day_run_is_kept_and_raises(roots, tmp_path: Path) -> None:
    """D-09: 같은 날 기록이 있는데 못 읽으면(권한 등) 덮지 않고 오류로 낸다 — 성공 기록이
    조용히 gate_failed 로 바뀌지 않는다. 실패 보고서는 그 전에 쓰고 임시 판도 지운다."""
    out = tmp_path / "fi"
    assert build(D_S, "morning", out, roots[1], roots[0], min_eligible=5, golden_path=None).ok
    run_path = out / "_runs" / f"{D_S}_morning.json"
    before = run_path.read_bytes()
    run_path.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            build(D_S, "morning", out, roots[1], roots[0], min_eligible=999, golden_path=None)
    finally:
        run_path.chmod(0o644)
    assert run_path.read_bytes() == before
    assert [json.loads(p.read_text(encoding="utf-8"))["status"]
            for p in (out / "_failed").glob("*.json")] == ["gate_failed"]
    assert not (out / "_tmp").exists() or not any((out / "_tmp").iterdir())


def test_collection_stop_beyond_lag_max_fails_fresh_gate(tmp_path: Path) -> None:
    """WISE 수집이 09-16 에 멈췄다 — D* 가 D 보다 6 거래일 뒤처지면 전 종목이 '신선' 으로 보이는
    조용한 낡음이 된다. FG-fresh 가 막는다."""
    eq, st = make_roots(tmp_path, drop_fetch_after=dt.date(2026, 9, 16))
    res = build(D_S, "morning", tmp_path / "fi", st, eq, min_eligible=1, golden_path=None)
    fresh = next(g for g in res.gates if g.name == "FG-fresh")
    assert fresh.status.value == "fail" and fresh.metrics["collection_lag_over_max"] == 1
    assert fresh.metrics["collection_lag_sessions"] == 6


@pytest.mark.parametrize(("stop", "lag", "passed"), [
    (dt.date(2026, 9, 23), 1, True),     # 09-24·25 휴장 → 09-28 하루 밀림
    (dt.date(2026, 9, 22), 2, False),    # 09-23·09-28 두 거래일 밀림
])
def test_collection_lag_tolerates_exactly_one_session(tmp_path: Path, stop: dt.date, lag: int,
                                                     passed: bool) -> None:
    """WISE 수집 중단 허용치 = 1거래일(N-12) — 유예 값과 따로 판정한다."""
    eq, st = make_roots(tmp_path, drop_fetch_after=stop)
    res = build(D_S, "morning", tmp_path / "fi", st, eq, grace_days=5, min_eligible=1,
                golden_path=None)
    fresh = next(g for g in res.gates if g.name == "FG-fresh")
    assert fresh.metrics["collection_lag_sessions"] == lag
    assert fresh.metrics["collection_lag_max"] == 1
    assert fresh.metrics["collection_lag_over_max"] == int(not passed)
    assert (fresh.status.value == "pass") is passed, fresh.detail


# ── 판 고정 --builds-from (T-2 · 컷오버 PR-3) ─────────────────────────────────
EQ_OLD = "m_20260928T220500_000000Z"           # EQ_BUILD 보다 2시간 앞선 같은 체인 판
EQ_TABLES = ("trading_calendar", "universe_daily", "security", "price_daily", "price_adj_daily",
             "adj_factor", "flow_daily", "credit_daily", "sector_snapshot", "coverage_daily",
             "fin_std", "dividend_event", "audit_opinion", "disclosure_version", "corp")
ST_TABLES = ("stg_consensus_annual", "stg_consensus_matrix", "stg_fin_wise")
HEALTH_OK = {"stage": "ok", "equity": "ok"}


def _history(path: Path, equity_build: str = EQ_BUILD, **override: object) -> Path:
    """`scripts/build_chain.sh` deliver_step 이 쓰는 인계 이력과 같은 모양. fi 가 안 읽는 표
    (`stg_price_daily`)도 실물처럼 함께 싣는다. `override` 는 최상위 키를 바꾼다."""
    payload: dict[str, object] = {
        "date": D_S, "basis": "morning", "stage_snapshot_id": "snap_x",
        "stage_builds": {**{t: ST_BUILD for t in ST_TABLES}, "stg_price_daily": ST_BUILD},
        "equity_builds": {t: equity_build for t in EQ_TABLES},
        "health": HEALTH_OK, **override}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _hashes(run_manifest: Path) -> dict[str, str]:
    run = json.loads(run_manifest.read_text(encoding="utf-8"))
    return {t: v["content_hash"] for t, v in run["tables"].items()}


def test_pinned_build_hashes_equal_the_current_build_on_the_same_builds(roots,
                                                                        tmp_path: Path) -> None:
    """이력이 current 와 같은 판을 가리키면 고정 빌드(CLI)의 8표 해시가 current 빌드와 같다."""
    hist = _history(tmp_path / f"{D_S}_morning.json")
    cur = build(D_S, "morning", tmp_path / "cur", roots[1], roots[0], min_eligible=5,
                golden_path=None)
    assert cur.ok
    rc = cli_main(["build", "--date", D_S, "--basis", "morning", "--root", str(tmp_path / "pin"),
                   "--stage-root", str(roots[1]), "--equity-root", str(roots[0]),
                   "--min-eligible", "5", "--builds-from", str(hist)])
    assert rc == 0
    pin_run = tmp_path / "pin" / "_runs" / f"{D_S}_morning.json"
    assert _hashes(pin_run) == _hashes(cur.run_manifest)
    run = json.loads(pin_run.read_text(encoding="utf-8"))
    assert run["builds_from"] == str(hist.resolve()) and run["builds_from_date"] == D_S
    assert run["equity_builds"] == {t: EQ_BUILD for t in EQ_TABLES}
    # 이력에 없는 선택 원천(WISE 분기)은 그날도 판이 없던 것 — current 와 같이 'absent'
    assert run["stage_builds"] == {**{t: ST_BUILD for t in ST_TABLES}, "stg_fin_wise_q": "absent"}
    cur_run = json.loads(cur.run_manifest.read_text(encoding="utf-8"))
    assert cur_run["builds_from"] is None and cur_run["builds_from_date"] is None


def test_pinned_build_reads_the_history_build_not_the_newer_current(tmp_path: Path) -> None:
    """current 가 더 새 판으로 넘어간 뒤에도 이력의 판을 읽는다 — 결과가 옛 판만 있던 트리의
    current 빌드와 8표 해시까지 같다(최신 판으로 조용히 넘어가지 않는다). 이력 날짜가 --date 보다
    앞서도(장 마감 판이 D' 판을 읽는 모양) 받는다."""
    eq_ref, st_ref = make_roots(tmp_path / "ref", eq_build=EQ_OLD)
    ref = build(D_S, "morning", tmp_path / "fi_ref", st_ref, eq_ref, min_eligible=5,
                golden_path=None)
    eq, st = make_roots(tmp_path / "moved", eq_build=EQ_OLD)
    newer = [{**r, "close": int(str(r["close"])) + 1_000} for r in _prices()]
    _make_stage_tree(tmp_path / "moved" / "eq", "price_daily", newer, build_id=EQ_BUILD)
    hist = _history(tmp_path / "20260923_morning.json", equity_build=EQ_OLD, date="20260923")
    pin = build(D_S, "morning", tmp_path / "fi_pin", st, eq, min_eligible=5, golden_path=None,
                builds_from=hist)
    cur = build(D_S, "morning", tmp_path / "fi_cur", st, eq, min_eligible=5, golden_path=None)
    assert ref.ok and pin.ok and cur.ok
    assert pin.tables["fi_prices"]["inputs"] == {"price_daily": EQ_OLD}
    assert cur.tables["fi_prices"]["inputs"] == {"price_daily": EQ_BUILD}
    assert _hashes(pin.run_manifest) == _hashes(ref.run_manifest)
    pin_run = json.loads(pin.run_manifest.read_text(encoding="utf-8"))
    assert pin_run["builds_from_date"] == "20260923"
    assert _hashes(cur.run_manifest)["fi_prices"] != _hashes(ref.run_manifest)["fi_prices"]


def test_pinned_build_also_refuses_a_stage_skip_outside_the_skip_table(tmp_path: Path) -> None:
    """K1-7a 가드는 `--builds-from` 고정 판에도 걸린다 — 이력이 가리킨 stage 판의 표 밖 SKIP
    (G9 ledger_unavailable)도 current 모드와 같이 거부한다."""
    eq, st = make_roots(tmp_path)
    _set_stage_gates(st, "stg_consensus_matrix", [
        _gate("G1", "pass"), _gate("G9", "skip", "ledger_unavailable:wise")])
    hist = _history(tmp_path / f"{D_S}_morning.json")
    with pytest.raises(FactorInputsError,
                       match=r"table=stg_consensus_matrix .*G9:ledger_unavailable"):
        build(D_S, "morning", tmp_path / "fi", st, eq, min_eligible=5, golden_path=None,
              builds_from=hist)
    assert not (tmp_path / "fi" / "fi_universe" / "MANIFEST.json").exists()


@pytest.mark.parametrize(("case", "want"), [
    ("no_history", ["인계 이력", "없다"]),
    ("health_fail", ["health", "'equity': 'fail'"]),
    ("health_absent", ["health", "{}"]),
    ("gc_removed", ["table=price_daily", "build_id=m_20260920T000500_000000Z", "MANIFEST"]),
    ("gc_removed_optional", ["table=stg_fin_wise_q", f"build_id={ST_BUILD}", "MANIFEST"]),
    ("missing_key", ["table=price_daily", "판이 없다"]),
    ("evening_history", ["basis=evening", "morning"]),
    ("future_history", ["date=20260929", f"--date={D_S}"]),
    ("date_absent", ["date=None", "YYYYMMDD"]),
])
def test_pinned_build_stops_with_rc2_and_names_the_history(roots, tmp_path: Path, capsys,
                                                           case: str, want: list[str]) -> None:
    """판 고정 실패 — 이력 없음 · health 가 ok 아님 · 이력의 판이 GC 로 사라짐 · 이력에 표가 없음 ·
    아침 확정판 이력이 아님 · 이력 날짜가 --date 뒤(미래 판) · 이력 날짜 없음. 어느 경우든 rc 2 로
    멈추고(최신 판으로 대신하지 않는다, P1) 메시지에 이력 파일을 담는다."""
    hist = tmp_path / f"{D_S}_morning.json"
    eq = {t: EQ_BUILD for t in EQ_TABLES}
    st = {t: ST_BUILD for t in ST_TABLES}
    if case == "health_fail":
        _history(hist, health={"stage": "ok", "equity": "fail"})
    elif case == "health_absent":
        _history(hist, health=None)
    elif case == "gc_removed":
        _history(hist, equity_builds={**eq, "price_daily": "m_20260920T000500_000000Z"})
    elif case == "gc_removed_optional":       # 이력엔 WISE 분기 판이 있는데 MANIFEST 에 없다
        _history(hist, stage_builds={**st, "stg_fin_wise_q": ST_BUILD})
    elif case == "missing_key":
        _history(hist, equity_builds={t: b for t, b in eq.items() if t != "price_daily"})
    elif case == "evening_history":
        _history(hist, basis="evening")
    elif case == "future_history":
        _history(hist, date="20260929")
    elif case == "date_absent":
        _history(hist, date=None)
    out = tmp_path / "fi"
    rc = cli_main(["build", "--date", D_S, "--basis", "morning", "--root", str(out),
                   "--stage-root", str(roots[1]), "--equity-root", str(roots[0]),
                   "--min-eligible", "5", "--builds-from", str(hist)])
    err = capsys.readouterr().err
    assert rc == 2, err
    assert str(hist) in err and all(w in err for w in want), err
    assert not any((out / t / "MANIFEST.json").exists() for t in FI_TABLES)


def test_fi_reads_only_morning_or_manual_equity_builds() -> None:
    """fi 는 basis 와 상관없이 아침 확정판(m_)·수동 판(b_) equity 판만 읽는다. 장 마감 판도 직전
    거래일 확정판을 고정해 읽으므로(T-2) 같다 — 저녁 잠정판(e_)은 어느 basis 에도 섞지 않는다.
    고정 없는 장 마감 판은 그 전에 `build` 가 거절한다
    (`test_evening_basis_without_pin_is_refused`)."""
    from factor_inputs.build import _check_basis
    evening_build = "e_20260928T121000_000000Z"
    for basis in ("morning", "evening"):
        _check_basis({"price_daily": EQ_BUILD}, basis)
        _check_basis({"price_daily": "b_manual_0001"}, basis)
        with pytest.raises(FactorInputsError, match="접두어"):
            _check_basis({"price_daily": evening_build}, basis)


# ── 장 마감 판(evening) 세션·유니버스·시점 (컷오버 PR-4 · T-2) ──────────────────────
# 장 마감 판 T = D 다음 거래일. 판은 D 아침 확정판(D' = D)을 `--builds-from` 으로 고정해 읽는다.
T = dt.date(2026, 9, 29)
T_S = "20260929"
# 과거 행만 담는 표 — 장 마감 판이 아침판(D')과 바이트까지 같아야 한다(과거 행 재계산 없음)
INFO_TABLES = ("fi_prices", "fi_adj_prices", "fi_consensus", "fi_consensus_annual",
               "fi_fin_summary")
# filing_late 는 (D', T] 기한 경계에서 두 판이 다르게 정해져 있어 따로 본다
UNI_CARRIED = ("ticker, name, market, sec_type, listed_date, shares, sector_l1, sector_l1_name, "
               "sector_l2, sector_l2_name, has_estimates, coverage_state, coverage_age_days, "
               "n_analysts, adv20, is_admin, is_halted, audit_adverse")


def _cal_dir(base: Path, holidays: set[dt.date] = HOLIDAYS) -> Path:
    """`daily.calendar` 판정 연도 파일(kis_holidays_2026.json) — 합성 트리와 같은 휴장일."""
    d = base / "calendar"
    d.mkdir(parents=True, exist_ok=True)
    (d / "kis_holidays_2026.json").write_text(json.dumps(
        {"year": 2026, "holidays": sorted(h.strftime("%Y%m%d") for h in holidays)}),
        encoding="utf-8")
    return d


@pytest.fixture(scope="module")
def evening(tmp_path_factory) -> SimpleNamespace:
    """T 날짜 정보 행이 섞인 원천(`leak=T`) 위에 아침판(D)과 장 마감 판(T)을 같은 판으로 짓는다.
    T 행(가격·수급)은 아직 없다 — 얹기는 PR-5 라 eligible 0 이고 하한도 0 으로 둔다."""
    base = tmp_path_factory.mktemp("fi_evening")
    eq, st = make_roots(base / "src", leak=T)
    cal = _cal_dir(base)
    # leak 이 WISE 분기(stg_fin_wise_q) 판을 만들므로 인계 이력에도 그 판을 싣는다
    hist = _history(base / f"{D_S}_morning.json",
                    stage_builds={**{t: ST_BUILD for t in ST_TABLES}, "stg_fin_wise_q": ST_BUILD})
    m = build(D_S, "morning", base / "fi_m", st, eq, grace_days=5, min_eligible=5,
              golden_path=None)
    e = build(T_S, "evening", base / "fi_e", st, eq, grace_days=5, min_eligible=0,
              golden_path=None, builds_from=hist, calendar_dir=cal)
    return SimpleNamespace(st=st, m=m, e=e, out_m=base / "fi_m", out_e=base / "fi_e")


def test_evening_build_commits_on_the_pinned_d_prime_build(evening) -> None:
    """`--basis evening` 이 열린다 — 판 id e_, `_runs/<T>_evening.json`, asof = D'. T 행은 아직
    없어(PR-5) 가격 T 행 0 · eligible 0 으로 통과한다."""
    e = evening.e
    assert evening.m.ok
    assert e.ok, [(g.name, g.detail) for g in e.gates if g.status.value == "fail"]
    assert e.build_id.startswith("e_") and (e.basis, e.date) == ("evening", "2026-09-29")
    assert e.run_manifest == evening.out_e / "_runs" / f"{T_S}_evening.json"
    run = json.loads(e.run_manifest.read_text(encoding="utf-8"))
    assert (run["date"], run["asof"], run["builds_from_date"]) == ("2026-09-29", "2026-09-28", D_S)
    latest = json.loads((evening.out_e / "latest_evening.json").read_text(encoding="utf-8"))
    assert latest["build_id"] == e.build_id
    assert all(manifest.load(evening.out_e / t / "MANIFEST.json").current_build == e.build_id
               for t in FI_TABLES)
    by_name = {g.name: g for g in e.gates}
    assert by_name["FG2"].metrics["n_t_price_rows"] == 0
    assert by_name["FG1"].metrics["n_eligible"] == 0
    # 아침판은 asof = D(그대로)
    assert json.loads(evening.m.run_manifest.read_text(encoding="utf-8"))["asof"] == "2026-09-28"


def test_evening_sessions_are_the_research_calendar_plus_t(evening) -> None:
    """세션 = 연구 판 trading_calendar(D' 까지) ∪ {T}. 수급 60 세션 창이 T 에서 끝나고(T 행 자리 —
    지금은 59 세션), 신용잔고는 T 에 실입수되는 행(T 의 3 세션 전)까지 싣는다(아침판 D' 는 한 세션
    앞까지)."""
    sessions = [*SESSIONS, T]
    run = json.loads(evening.e.run_manifest.read_text(encoding="utf-8"))
    assert run["window"]["to"] == "2026-09-29"
    assert run["window"]["flow_from"] == sessions[-60].isoformat()
    flows = q(evening.out_e, "fi_flows", f"SELECT date FROM t WHERE ticker = '{A}' ORDER BY date")
    assert [r[0] for r in flows] == sessions[-60:-1]
    credit = q(evening.out_e, "fi_credit", "SELECT date, available_date FROM t "
                                           f"WHERE ticker = '{A}' ORDER BY date")
    assert credit[-1] == (sessions[-4], T)
    assert q(evening.out_m, "fi_credit", "SELECT max(date), max(available_date) FROM t "
                                         f"WHERE ticker = '{A}'") == [(SESSIONS[-4], D)]
    assert q(evening.out_e, "fi_prices", "SELECT max(date) FROM t") == [(D,)]


def test_evening_universe_carries_the_d_prime_rows(evening) -> None:
    """유니버스 T 행 = universe_daily D' 행 이월. 종목·속성·신선도는 아침판(D')과 같고 다른 것은
    date = T · 시총 기준 't1_shares_x_t_close' · T 종가가 없어 시총 NULL·no_price 뿐이다. 원천의
    T 날짜 행(A 상장폐지·관리·정지, 추정기관 A 99·F 77, WICS G30, 감사 의견거절, 지연 제출)은 읽지
    않는다. filing_late 는 `test_evening_filing_deadline_between_d_prime_and_t_is_not_late`."""
    sql = f"SELECT {UNI_CARRIED} FROM t ORDER BY ticker"
    assert q(evening.out_e, "fi_universe", sql) == q(evening.out_m, "fi_universe", sql)
    assert q(evening.out_e, "fi_universe", "SELECT DISTINCT date, market_cap, mktcap_basis, "
                                           "eligible FROM t") == [
        (T, None, "t1_shares_x_t_close", False)]
    reasons = dict(q(evening.out_e, "fi_universe", "SELECT ticker, exclude_reason FROM t"))
    assert reasons == {**{t: "no_price" for t in LAYER}, G: "sec_type"}
    assert q(evening.out_e, "fi_universe", "SELECT shares, n_analysts, sector_l1, is_admin, "
                                           "is_halted, audit_adverse, filing_late FROM t "
                                           f"WHERE ticker = '{A}'") == [
        (SHARES[A], 10, "G15", False, False, False, False)]


def test_evening_cuts_wise_dart_and_events_at_d_prime(evening) -> None:
    """WISE·DART·재무·기업행위 입력은 fetched/available ≤ D' 로 자른다 — 원천에 T 날짜 행이 있어도
    과거 행 표 5개가 아침판(D')과 바이트까지 같다. T 까지 읽으면 바뀌는 것: A·B 컨센서스(09-29 판) ·
    A 연간 재무(+50 판)·반기 재제출 · A 배당(2,000원) · A 분기(WISE 분기 판으로 넘어감) ·
    E 4Q 파생값 · A adj_ok(09-21 미해결 사건). WISE 신선도 기준일도 D'."""
    path = evening.st / "stg_consensus_annual" / f"v={ST_BUILD}" / "part0.parquet"
    assert duckdb.sql(f"SELECT max(fetched_date) FROM read_parquet('{path}')").fetchone() == (T,)
    for t in INFO_TABLES:
        assert evening.e.tables[t]["content_hash"] == evening.m.tables[t]["content_hash"], t
    assert q(evening.out_e, "fi_consensus", "SELECT ticker, max(fetched_date) FROM t WHERE ticker "
                                            f"IN ('{A}', '{B}') GROUP BY 1 ORDER BY 1") == [
        (A, D), (B, dt.date(2026, 9, 23))]
    assert q(evening.out_e, "fi_fin_summary", "SELECT max(available_date) FROM t") == [(D,)]
    fin = (f"SELECT dps FROM t WHERE ticker = '{A}' AND period = '2025/12' "
           "AND period_type = 'annual'")
    assert q(evening.out_e, "fi_fin_summary", fin) == [(1_446.0,)]
    assert q(evening.out_e, "fi_fin_summary", "SELECT DISTINCT substr(fs_basis, 1, 5) FROM t "
                                              f"WHERE ticker = '{A}' AND period_type = 'quarter'"
                                              ) == [("DART:",)]
    assert q(evening.out_e, "fi_fin_summary", "SELECT revenue, op, ni FROM t "
                                              f"WHERE ticker = '{E}' AND period = '2025/12' "
                                              "AND period_type = 'quarter'") == [
        (None, None, None)]
    fresh = next(g for g in evening.e.gates if g.name == "FG-fresh")
    assert (fresh.metrics["last_collection_date"], fresh.metrics["collection_expected_date"],
            fresh.metrics["collection_lag_sessions"]) == ("2026-09-28", "2026-09-28", 0)


def test_evening_filing_deadline_between_d_prime_and_t_is_not_late(evening) -> None:
    """filing_late 경계 — L 의 원본은 D' 에 접수했고 법정기한이 T(∈ (D', T])다. 장 마감 판은 세션
    축에 T 가 있어 실효 기한 = T, 접수일 D' ≤ T 라 false 다(다음 날 연구 판 T 와 같은 판정). 아침판
    D' 는 기한 뒤 세션이 달력에 없어 NULL 이다. T 에 공개된 L 의 지연 원본(기한 D', 접수 T)은 D' 로
    잘려 보이지 않는다 — 보이면 true 가 된다. 다른 종목은 두 판이 같다."""
    sql = "SELECT ticker, filing_late FROM t ORDER BY ticker"
    e = dict(q(evening.out_e, "fi_universe", sql))
    m = dict(q(evening.out_m, "fi_universe", sql))
    assert (m[L], e[L]) == (None, False)
    assert {t: v for t, v in e.items() if t != L} == {t: v for t, v in m.items() if t != L}


@pytest.mark.parametrize(("stop", "lag", "passed"), [
    (None, 0, True),                     # 정상 — 전날 저녁(D') 수집
    (dt.date(2026, 9, 23), 1, True),     # D' 수집 하나 빠짐 — 허용치 1(N-12)
    (dt.date(2026, 9, 22), 2, False),    # 두 거래일 빠짐 → FAIL
])
def test_evening_wise_lag_counts_to_the_expected_collection_day(tmp_path: Path,
                                                                stop: dt.date | None, lag: int,
                                                                passed: bool) -> None:
    """N-12 허용치 1거래일을 예상 수집일 D' 기준으로 잰다. T 기준으로 재면 정상 상태가 lag 1 이
    되어, 하루만 빠져도(09-23 정지) lag 2 로 미발송된다."""
    eq, st = make_roots(tmp_path / "src", drop_fetch_after=stop)
    res = build(T_S, "evening", tmp_path / "fi", st, eq, grace_days=5, min_eligible=0,
                golden_path=None, builds_from=_history(tmp_path / f"{D_S}_morning.json"),
                calendar_dir=_cal_dir(tmp_path))
    fresh = next(g for g in res.gates if g.name == "FG-fresh")
    assert fresh.metrics["collection_lag_sessions"] == lag
    assert fresh.metrics["collection_expected_date"] == "2026-09-28"
    assert (fresh.status.value == "pass") is passed, fresh.detail


@pytest.mark.parametrize(("case", "date", "want"), [
    ("t_holiday", T_S, ["T=2026-09-29", "거래일이 아니다"]),
    ("d_prime_behind", "20260930", ["MD-SEAM", "D'=2026-09-29", "마지막=2026-09-28"]),
    ("t_already_in_build", D_S, ["MD-SEAM", "D'=2026-09-23", "마지막=2026-09-28"]),
    ("calendar_missing", T_S, ["T=2026-09-29", "판정 불가"]),
    ("year_missing", T_S, ["T=2026-09-29", "판정 불가", "does not cover 2026"]),
])
def test_evening_session_mismatch_stops_with_rc2(roots, tmp_path: Path, capsys, case: str,
                                                 date: str, want: list[str]) -> None:
    """(a) T 가 daily.calendar 거래일 · (b) T 의 직전 거래일 = 연구 판 trading_calendar 마지막
    (MD-SEAM). 어긋나면 판을 만들지 않고 rc 2 로 멈추며 사유를 남긴다 — T 가 휴장일 · 연구 판이
    D' 까지 오지 않음(T 가 하루 뒤) · 연구 판이 이미 T 를 담음 · 판정 달력 없음 · T 의 연도 파일
    없음(`KeyError` — 주말만 거르는 폴백으로 넘어가지 않는다)."""
    if case == "calendar_missing":
        cal = tmp_path / "no_calendar"
        cal.mkdir()
    elif case == "year_missing":
        cal = tmp_path / "calendar_2025"
        cal.mkdir()
        (cal / "kis_holidays_2025.json").write_text(
            json.dumps({"year": 2025, "holidays": ["20250101"]}), encoding="utf-8")
    else:
        cal = _cal_dir(tmp_path, HOLIDAYS | {T} if case == "t_holiday" else HOLIDAYS)
    out = tmp_path / "fi"
    rc = cli_main(["build", "--date", date, "--basis", "evening", "--root", str(out),
                   "--stage-root", str(roots[1]), "--equity-root", str(roots[0]),
                   "--min-eligible", "0", "--calendar-dir", str(cal),
                   "--builds-from", str(_history(tmp_path / f"{D_S}_morning.json"))])
    err = capsys.readouterr().err
    assert rc == 2, err
    assert all(w in err for w in want), err
    assert not any((out / t / "MANIFEST.json").exists() for t in FI_TABLES)
    assert not (out / "_tmp").exists() or not any((out / "_tmp").iterdir())


def test_evening_refuses_the_research_root(roots, tmp_path: Path, capsys, monkeypatch) -> None:
    """T-3: 장 마감 판을 연구 fi 루트(`<QL_HOME>/data/factor_inputs` = CLI `--root` 기본값)에 지으면
    keep 이 하루 2판씩 소모돼 모델 판이 가리키는 fi 판이 GC 된다 — rc 2 로 거절하고 아무것도
    쓰지 않는다. 경로 표기가 달라도(심볼릭 링크) 실제 경로로 본다."""
    ql = tmp_path / "ql"
    (ql / "data").mkdir(parents=True)
    monkeypatch.setenv("QL_HOME", str(ql))
    hist = _history(tmp_path / f"{D_S}_morning.json")
    cal = _cal_dir(tmp_path)
    rc = cli_main(["build", "--date", T_S, "--basis", "evening", "--stage-root", str(roots[1]),
                   "--equity-root", str(roots[0]), "--min-eligible", "0",
                   "--calendar-dir", str(cal), "--builds-from", str(hist)])
    err = capsys.readouterr().err
    assert rc == 2, err
    assert "T-3" in err and "data/model_db/factor_inputs" in err, err
    alias = tmp_path / "alias"
    alias.symlink_to(ql / "data")
    with pytest.raises(FactorInputsError, match="T-3"):
        build(T_S, "evening", alias / "factor_inputs", roots[1], roots[0], min_eligible=0,
              golden_path=None, builds_from=hist, calendar_dir=cal)
    assert not (ql / "data" / "factor_inputs").exists()


def test_evening_market_cap_is_d_prime_shares_times_t_close(roots) -> None:
    """시총 자리(B-24): T 행을 얹는 쪽(PR-5)이 `_t_prices` 에 T 종가를 채우면 market_cap =
    round(D' 주식수 × T 종가 / 1e8) · 't1_shares_x_t_close' 이고 T 종가가 있는 종목만 no_price 를
    벗는다. D' 주식수나 T 종가가 없으면 시총은 NULL(P1). PR-4 의 `_t_prices` 는 빈 표다."""
    from factor_inputs import queries
    from factor_inputs.build import EQUITY_SOURCES, STAGE_SOURCES, _resolve
    con = duckdb.connect()
    try:
        for root, tables, stage in ((roots[0], EQUITY_SOURCES, False),
                                    (roots[1], STAGE_SOURCES, True)):
            for name, expr in _resolve(root, tables, stage)[1].items():
                con.execute(f'CREATE TEMP VIEW "{name}" AS SELECT * FROM {expr}')
        p = queries.Params(d=T.isoformat(), fy="202612", price_from="2025-03-28",
                           flow_from=SESSIONS[-59].isoformat(), grace_days=5, credit_lag=3,
                           basis="evening", asof=D.isoformat())
        con.execute(queries.calendar_sql(T.isoformat()))
        for sql in queries.coverage_sqls(p):
            con.execute(sql)
        sqls = dict(queries.table_sqls(p, UniverseRule(coverage_grace_days=5)))
        con.execute(sqls["_t_prices"])
        assert con.execute("SELECT count(*) FROM _t_prices").fetchone() == (0,)
        con.execute("INSERT INTO _t_prices (ticker, date, close, price_source) VALUES "
                    f"('{A}', DATE '{T}', 123456, 'postclose'), "
                    f"('{K}', DATE '{T}', 7000, 'postclose')")
        con.execute(sqls["fi_universe"])
        got = {r[0]: r[1:] for r in con.execute(
            "SELECT ticker, shares, market_cap, mktcap_basis, eligible, exclude_reason "
            "FROM _fi_universe").fetchall()}
    finally:
        con.close()
    assert got[A] == (SHARES[A], float(round(SHARES[A] * 123_456 / 1e8)), "t1_shares_x_t_close",
                      True, None)
    assert got[K] == (None, None, "t1_shares_x_t_close", True, None)   # D' KRX 행 없음
    assert got[B] == (SHARES[B], None, "t1_shares_x_t_close", False, "no_price")


def test_cli_return_codes(roots, tmp_path: Path, capsys) -> None:
    base = ["build", "--date", D_S, "--root", str(tmp_path / "fi"), "--stage-root",
            str(roots[1]), "--equity-root", str(roots[0])]
    assert cli_main(base + ["--basis", "morning", "--min-eligible", "5"]) == 0
    assert "factor_inputs ok" in capsys.readouterr().out
    assert cli_main(base + ["--basis", "morning", "--min-eligible", "999"]) == 1
    assert "같은 날 성공 기록" in capsys.readouterr().err        # D-09: _runs 는 첫 판 그대로
    assert cli_main(base + ["--basis", "evening"]) == 2


def test_cli_keep_default_is_the_build_default() -> None:
    """E-01: fi 를 짓는 운영 스크립트가 없어(수동 실행) CLI `--keep` 기본값이 곧 운영 보관 수다."""
    from factor_inputs.__main__ import _parser
    from factor_inputs.build import KEEP_DEFAULT
    args = _parser().parse_args(["build", "--date", D_S, "--basis", "morning"])
    assert args.keep == KEEP_DEFAULT == 60
    assert inspect.signature(build).parameters["keep"].default == KEEP_DEFAULT


def test_fi_keep_default_is_at_least_the_model_keep() -> None:
    """E-01: 두 층의 keep 관계는 테스트에서만 고정한다 — 운영 코드에서 model 을 import 하면
    순환 import 다(model.build 가 factor_inputs.build 를 import 한다)."""
    from factor_inputs.build import KEEP_DEFAULT
    from model.build import KEEP_DEFAULT as MODEL_KEEP
    assert KEEP_DEFAULT >= MODEL_KEEP   # fi 판이 모델 판보다 먼저 지워지지 않게(E-01)


def test_flow_and_unit_constants_match_compat() -> None:
    """수급 주체 대응·단위 상수는 compat `units` 와 같아야 한다(두 층이 같은 재료를 본다)."""
    from compat import units
    from factor_inputs import queries
    assert queries.FLOW_SOURCE == units.FLOW_SUBJECTS
    assert (queries.KRW_PER_MN, queries.KRW_PER_EOK) == (units.KRW_PER_MN, units.KRW_PER_EOK)
    from model.contracts import FLOW_SUBJECTS
    assert tuple(d for d, _ in queries.FLOW_SOURCE) == FLOW_SUBJECTS
