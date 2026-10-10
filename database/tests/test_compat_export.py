"""T1.2 — exporter 왕복 (플랜 `2026-09-24-v3-merge.md` §5 T1.2 1 + 1차 그림자 실행 리뷰 R1~R10).

합성 equity 판 + 합성 stage 판을 `make_stage_tree` 규약(루트만 다르게)으로 만들고 `compat.export`
가 v3 `quant.db` 표를 채우는지 본다. 단위 환산은 **리터럴**로 고정한다(억원·백만원).

픽스처 열 구성은 **서버 실물과 같다**(09-23 실측) — 커밋된 equity 판에는 `reject_reason` 열이
없다(거부 행은 `_reject/` 서브디렉토리로 빠진다). 합성 입력이 실물보다 열이 적으면 SQL 이
로컬에서만 통과한다.

합성 입력 요약 (as_of = 2026-09-23, equity 판 `m_…`, stage 판 `b_…`)
  universe_daily  filler 2,100(common·KOSPI) + 005930·000660(common) + 123450(spac) +
                  제외되어야 할 7종(preferred·etf·reit·foreign·dr·fund·KONEX)
  price_daily     005930·000660 × 09-21·09-22·09-23 (basis 'krx') + 000270 09-23 ('evening')
  flow_daily      005930·000660 × 09-21~09-23 (kiwoom) + 전 주체 NULL 셀 1개(미측정)
  matrix          fetched 09-22·09-23 두 판 × target_period 202612(+ 과거 202512)
  annual          005930 만 당해(202612) 추정 — `--model-universe estimates` 판정축
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from pathlib import Path

import pytest
from compat import CompatEmptyError, CompatError, CompatSchemaError, export, quant_db
from compat.__main__ import main as cli_main
from conftest import _make_stage_tree
from daily import calendar as daily_calendar

AS_OF = "20260923"
D21, D22, D23 = dt.date(2026, 9, 21), dt.date(2026, 9, 22), dt.date(2026, 9, 23)
SESSIONS = (D21, D22, D23)

# 판 id — equity 는 아침 확정판(`m_`), stage 는 수동(`b_`). R5·R9 가드가 이 접두·시각을 읽는다.
EQ_BUILD = "m_20260924T035911_573147Z"
EQ_BUILD_OLD = "m_20260924T015911_000000Z"      # 2시간 앞선 같은 체인 판(--builds-from 용)
ST_BUILD = "b_20260902T164532_554736Z"

# 단위 검산용 리터럴 — 005930 09-23 행
VALUE_KRW = 1_234_000_000          # 거래대금 원      → v3 amount 1,234 백만원
MKTCAP_KRW = 12_345_678_900_000    # 005930 시총 원   → v3 market_cap 123,457 억원
MKTCAP_KRW_2 = 9_876_543_210_000   # 000660 시총 원   → v3 market_cap 98,765 억원
FRGN_KRW = -3_456_000_000          # 외국인 순매수 원 → v3 foreign_investor -3,456 백만원

REAL = ("005930", "000660")
SPAC = "123450"
N_FILLER = 2_100                   # MIN_STOCK_COUNT(2,000) 을 넘겨야 stocks 가 선다
N_STOCKS = N_FILLER + len(REAL) + 1

_FLOW_SRC = ("ind_invsr_krw", "frgnr_invsr_krw", "orgn_krw", "fnnc_invt_krw", "insrnc_krw",
             "invtrt_krw", "etc_fnnc_krw", "bank_krw", "penfnd_etc_krw", "samo_fund_krw",
             "natn_krw", "etc_corp_krw", "natfor_krw")

_FIN_LABELS = ["2021/12(IFRS연결)", "2022/12(IFRS연결)", "2023/12(IFRS연결)",
               "2024/12(IFRS연결)", "2025/12(IFRS연결)", "2026/12(E)(IFRS연결)"]

FIN_SLICE = sorted(Path(__file__).resolve().parent.glob(
    "fixtures/stage_slice/stg_fin_wise/v=*/year=*/*.parquet"))


# ── 합성 행 (열 구성은 서버 실물과 같다) ─────────────────────────────────────
def _price_row(ticker: str, d: dt.date, close: int, *, basis: str = "krx",
               ohl: bool = True, value: int | None = 0, mktcap: int | None = 0) -> dict:
    """`price_daily` 실물 18열."""
    return {
        "ticker": ticker, "date": d,
        "open": (close - 500) if ohl else None,
        "high": (close + 700) if ohl else None,
        "low": (close - 900) if ohl else None,
        "close": close, "volume_shr": 1_000_000, "value_krw": value, "mktcap_krw": mktcap,
        "shares_out": 5_969_782_550, "change_krw": 100, "base_price_krw": close - 100,
        "par_value_krw": 100, "price_kind": "trade", "basis": basis,
        "corp_action_pending": False, "available_date": d, "available_basis": "default"}


def _price_rows(*, bad_volume: bool = False) -> list[dict]:
    rows: list[dict] = []
    for i, ticker in enumerate(REAL):
        for j, d in enumerate(SESSIONS):
            close = 70_000 + i * 1000 + j * 100
            rows.append(_price_row(ticker, d, close, value=VALUE_KRW + j,
                                   mktcap=(MKTCAP_KRW if i == 0 else MKTCAP_KRW_2) + j))
    if bad_volume:
        # R7 — v3 NOT NULL 을 못 채우는 krx 행. O/H/L 공란은 종가로 채우므로(QL-F) 거래량을 비운다.
        rows[0]["volume_shr"] = None
    # 저녁 잠정 T 행 — KRX 기본정보가 없어 OHL·거래대금·시총이 전부 NULL (GAP-1/D-8)
    rows.append(_price_row("000270", D23, 55_000, basis="evening", ohl=False,
                           value=None, mktcap=None))
    return rows


def _adj_rows(price_rows: list[dict] | None = None) -> list[dict]:
    """`price_adj_daily` 실물 18열(e1.26.0 — ⑤ 가격 전용 계수 열 포함). compat 은 이 표를 읽지 않는다(QL-E T-40 —
    v3 adj_close 는 KRX 기준가 사슬). 판 모양을 실물과 같게 두려고 짓는다."""
    src = [r for r in (price_rows if price_rows is not None else _price_rows())
           if r["basis"] == "krx"]
    out: list[dict] = []
    for r in src:
        close = float(r["close"])
        out.append({
            "ticker": r["ticker"], "date": r["date"], "adj_open": close * 0.9,
            "adj_high": close * 0.9, "adj_low": close * 0.9, "adj_close": close * 0.9,
            "adj_volume_shr": 1_000_000.0, "cum_price_factor": 1.0, "cum_share_factor": 0.9,
            "cum_price_only_factor": 1.0, "n_factors_applied": 1, "n_price_only_applied": 0,
            "n_unadjusted_events": 0, "n_price_unresolved_events": 0, "available_date": r["date"],
            "available_basis": "derived", "basis": "krx", "corp_action_pending": False})
    return out


def _flow_row(ticker: str, d: dt.date, offset: int = 0) -> dict:
    """`flow_daily` 실물 23열 — 측정 셀 하나. 주체마다 +1,000,000원씩 어긋난다."""
    row: dict = {"date": d, "ticker": ticker, "src": "kiwoom"}
    for k, col in enumerate(_FLOW_SRC):
        row[col] = FRGN_KRW + (offset + k) * 1_000_000
    row.update(foreign_wght_pct=50.0, foreign_limit_exh_pct=50.0,
               foreign_poss_shr=1_000, pension_net_buy_kiwoom_krw=1_000_000,
               fill_kind="measured", available_date=d, available_basis="default")
    return row


def _flow_rows() -> list[dict]:
    """`flow_daily` 실물 23열."""
    rows = [_flow_row(ticker, d, i + j)
            for i, ticker in enumerate(REAL) for j, d in enumerate(SESSIONS)]
    # 미측정 셀 — 전 주체 NULL. v3 는 수집한 행만 가지므로 내보내지 않는다.
    empty: dict = {"date": D22, "ticker": "000270", "src": None}
    empty.update(dict.fromkeys(_FLOW_SRC))
    empty.update(foreign_wght_pct=None, foreign_limit_exh_pct=None, foreign_poss_shr=None,
                 pension_net_buy_kiwoom_krw=None, fill_kind="not_collected",
                 available_date=D22, available_basis="default")
    rows.append(empty)
    return rows


def _uni_row(ticker: str, d: dt.date, market: str, sec_type: str) -> dict:
    """`universe_daily` 실물 23열."""
    return {"date": d, "ticker": ticker, "status": "listed", "market": market,
            "sec_type": sec_type, "halt_state": None, "admin_state": None,
            "admin_state_basis": None, "liquidation_window": False, "signal_halt": False,
            "signal_halt_release": False, "signal_admin": False, "signal_liquidation": False,
            "signal_delist": False, "admin_flag": False, "mktcap_krw": 1.0, "adv20_krw": 1.0,
            "adv20_rank_pct": 0.5, "no_trade_reason": None, "listing_age_days": 100,
            "no_trade_run": 0, "available_date": d, "available_basis": "default"}


# D-11 — v3 `stocks` 에 들지 **않아야** 하는 종류(09-23 실측: 우리가 236 을 더 넣고 있었다)
EXCLUDED = (("005935", "KOSPI", "preferred"), ("069500", "KOSPI", "etf"),
            ("330590", "KOSPI", "reit"), ("900140", "KOSDAQ", "foreign"),
            ("900110", "KOSDAQ", "dr"), ("287300", "KOSPI", "fund"),
            ("300000", "KONEX", "common"))       # KONEX 는 v3 market 어휘 밖


def _filler_tickers(n: int) -> list[str]:
    return [f"1{i:05d}" for i in range(n)]


def _universe_rows(fillers: list[str]) -> list[dict]:
    rows = [_uni_row(t, d, "KOSPI", "common") for t in REAL for d in SESSIONS]
    rows.append(_uni_row(SPAC, D23, "KOSDAQ", "spac"))
    rows += [_uni_row(t, D23, m, s) for t, m, s in EXCLUDED]
    rows += [_uni_row(t, D23, "KOSPI", "common") for t in fillers]
    return rows


def _sec_row(ticker: str, name: str, list_date: dt.date, delist: dt.date | None = None,
             formal: str | None = None) -> dict:
    """`security` 실물 14열. name = 약명(시장 호칭), formal = 정식 종목명(기본 name + '보통주')."""
    return {"ticker": ticker, "corp_code": f"{ticker}00", "isin": "KR" + ticker * 2,
            "name_current": formal or f"{name}보통주", "name_abbrv_current": name,
            "sec_type": "common", "list_date": list_date,
            "list_date_basis": "measured", "delist_date_krx": delist,
            "delist_date_kis": None, "delist_conflict": False, "delist_date": delist,
            "delist_date_basis": "derived" if delist else "unknown"}


def _security_rows(fillers: list[str]) -> list[dict]:
    rows = [_sec_row("005930", "삼성전자", dt.date(1975, 6, 11)),
            _sec_row("000660", "SK하이닉스", dt.date(1996, 12, 26), formal="에스케이하이닉스보통주"),
            _sec_row(SPAC, "스팩1호", dt.date(2024, 3, 1)),
            _sec_row("900000", "폐지종목", dt.date(2010, 1, 4), dt.date(2026, 5, 1))]
    rows += [_sec_row(t, f"제외{t}", dt.date(2020, 1, 2)) for t, _, _ in EXCLUDED]
    rows += [_sec_row(t, f"종목{t}", dt.date(2015, 1, 2)) for t in fillers]
    return rows


def _master_row(ticker: str, d: dt.date, up_name: str) -> dict:
    """stage `stg_master_daily`(키움 ka10099 일별 마스터) 규칙 열 + 불리언 파생 3열."""
    return {"date": d, "ticker": ticker, "mrkt_tp": "0", "name": f"이름{ticker}",
            "list_shrs": 1_000_000, "audit_info": "정상", "reg_date": dt.date(2000, 1, 4),
            "last_price": 70_000, "state": "증거금20%", "market_code": "0",
            "market_name": "거래소", "up_name": up_name, "up_size_name": "대형주",
            "company_class_name": "", "order_warning": "0", "nxt_enable": "Y", "kind": "A",
            "is_admin_issue": False, "is_trade_halt": False, "is_liquidation": False}


# QL-B(T-19) — v3 `stocks.sector` 는 키움 ka10099 `upName`(KRX 업종명) 그대로다(v3
# `clients/kiwoom/client.py` get_stock_list → `daily_pipeline._fetch_kiwoom_stocks`, 공란은
# `strip() or None`). 값은 v3 실물 어휘('전기/전자'·'기계/장비').
def _master_rows() -> list[dict]:
    return [
        _master_row("005930", D22, "전기/전자"),
        _master_row("005930", dt.date(2026, 9, 24), "화학"),       # D 뒤 스냅샷 — 보이면 안 된다
        _master_row("000660", dt.date(2026, 9, 1), "기계/장비"),   # 옛 스냅샷 — 최신이 이긴다
        _master_row("000660", D23, "전기/전자"),
        _master_row(SPAC, D23, ""),                                # 업종 공란 → v3 는 NULL
    ]


def _matrix_rows() -> list[dict]:
    """(fetched_date, target_period, acc_cd, lookback) 좌표. op 121500 · ni 122710."""
    rows: list[dict] = []
    plan = [
        (D22, D21, "202612", "2026/12", 1000.0, 2000.0),
        (D23, D22, "202612", "2026/12", 1100.0, 2100.0),
        (D23, D22, "202512", "2025/12", 99.0, 88.0),      # 과거 기 — 선택되면 안 된다
    ]
    for fetched, base, period, label, op_cur, ni_cur in plan:
        for acc, cur in (("121500", op_cur), ("122710", ni_cur)):
            for idx, (lb, delta) in enumerate(
                    (("current", 0.0), ("1w", -100.0), ("1m", -200.0), ("3m", -300.0),
                     ("1y", -400.0)), start=1):
                rows.append({
                    "ticker": "005930", "fetched_date": fetched, "target_period": period,
                    "acc_cd": acc, "lookback_idx": str(idx), "lookback": lb,
                    "target_label": label, "base_date": base, "value": cur + delta})
    return rows


def _annual_row(ticker: str, period: str, kind: str, *, op: float | None,
                ni: float | None) -> dict:
    return {"ticker": ticker, "fetched_date": D23,
            "period_label": f"{period[:4]}.{period[4:]}({kind})", "period": period,
            "period_kind": kind, "fs_basis": "IFRS연결", "revenue": 3_000_000.0,
            "yoy_pct": 10.5, "op": op, "ni": ni, "eps": 6563.0, "bps": 60_000.0,
            "per": 18.27, "pbr": 1.3, "roe_pct": 11.1, "ev_ebitda": 7.53}


def _annual_rows() -> list[dict]:
    # 005930 만 당해(202612) 추정 op·ni 가 둘 다 있다 → estimates 유니버스에 남는 유일한 종목.
    # 000660 은 op 만 있고 ni 가 없어 탈락한다(둘 다 필요).
    return [_annual_row("005930", "202512", "A", op=500_000.0, ni=400_000.0),
            _annual_row("005930", "202612", "E", op=500_000.0, ni=400_000.0),
            _annual_row("000660", "202612", "E", op=100_000.0, ni=None)]


# 기간 슬롯 6개 값을 전부 다르게 둔다 — 슬롯 취합이 어긋나면 테스트가 잡는다.
_FIN_SPEC: list[tuple[str, str, str | None, str, list[float]]] = [
    ("cF3002", "200000", None, "매출액(수익)",
     [2_796_047.99, 3_022_313.6, 2_589_354.94, 3_008_709.03, 3_336_059.38, 7_397_267.52]),
    ("cF3002", "200810", None, "매출총이익",
     [900_001.0, 900_002.0, 900_003.0, 900_004.0, 1_000_000.0, 900_006.0]),
    ("cF3002", "201370", None, "영업이익",
     [516_331.0, 516_332.0, 516_333.0, 516_334.0, 516_339.0, 516_336.0]),
    ("cF3002", "203170", None, "당기순이익",
     [399_071.0, 399_072.0, 399_073.0, 399_074.0, 399_075.0, 399_076.0]),
    ("cF4002", "312000", None, "EPS",
     [5777.37, 5000.0, 4000.0, 5500.0, 6563.57, 48_338.64]),
    ("cF4002", "312000", "382100", "EPS<당기>", [1.0] * 6),     # 중첩 — 쓰지 않는다
    ("cF4002", "314000", None, "BPS",
     [60_001.0, 60_002.0, 60_003.0, 60_004.0, 60_000.0, 60_006.0]),
    ("cF4002", "382100", None, "PER", [13.55, 14.0, 15.0, 16.0, 18.27, 5.38]),
    ("cF4002", "382500", None, "PBR", [1.11, 1.12, 1.13, 1.14, 1.1, 1.16]),
    ("cF4002", "331000", None, "EV/EBITDA", [4.81, 4.82, 4.83, 4.84, 4.89, 4.86]),
    ("cF4002", "431800", None, "현금배당수익률", [1.81, 1.82, 1.83, 1.84, 1.84, 1.86]),
    ("cF4002", "701250", None, "보통주수정기말발행주식수(자사주차감)<당기>",
     [5_969_782_551.0, 5_969_782_552.0, 5_969_782_553.0, 5_969_782_554.0,
      5_969_782_550.0, 5_969_782_556.0]),
]


def _fin_wise_rows() -> list[dict]:
    rows: list[dict] = []
    for ep, accode, p_accode, acc_nm, vals in _FIN_SPEC:
        row: dict = {"ticker": "005930", "fetched_date": D23, "ep": ep, "accode": accode,
                     "p_accode": p_accode, "acc_nm": acc_nm, "fs_basis": "IFRS연결"}
        for i, label in enumerate(_FIN_LABELS, start=1):
            row[f"period_label_{i}"] = label
        for i, v in enumerate(vals, start=1):
            row[f"val_{i}"] = v
        rows.append(row)
    return rows


# ── 트리 ─────────────────────────────────────────────────────────────────────
# ── DART fin_std (T1.5 · D-10) ──────────────────────────────────────────────
# 열 이름·단위는 `src/equity/sql/fin_std.sql` 실물 그대로(금액 전부 원).
# 005930 2025/12: 자산 500조 · 부채 100조 · 자본 400조 · 순이익 40조 · 영업CF 60조 ·
#   capex −30조(유출 부호) → roa 8.0% · debt_ratio 25.0% · roe 10.0% ·
#   total_assets 5,000,000 억원 · capex 300,000 억원 · fcf 300,000 억원
FIN_STD_BASE = {"total_asset": 500_000_000_000_000.0, "total_liab": 100_000_000_000_000.0,
                "total_equity": 400_000_000_000_000.0, "net_income": 40_000_000_000_000.0,
                "cf_operating_ytd": 60_000_000_000_000.0,
                "capex_ytd": -30_000_000_000_000.0,
                "gross_profit": 50_000_000_000_000.0}


def _fin_std_row(ticker: str, period_end: dt.date, available: dt.date, rcept: str,
                 fs_div: str = "CFS", **over: float) -> dict:
    row = {"corp_code": f"{ticker}00", "period_end": period_end, "report_code": "11011",
           "fs_div": fs_div, "vintage_kind": "api_restated", "bsns_year": str(period_end.year),
           "rcept_no": rcept, "rcept_dt": available, "period_start": dt.date(2025, 1, 1),
           "period_end_basis": "measured", "currency": "KRW",
           "revenue": 3_000_000_000_000_000.0, "op_profit": 50_000_000_000_000.0,
           "available_date": available, "available_basis": "derived"}
    row.update(FIN_STD_BASE)
    row.update(over)
    return row


def _fin_std_rows() -> list[dict]:
    return [
        _fin_std_row("005930", dt.date(2025, 12, 31), dt.date(2026, 3, 11), "20260311000001"),
        # 별도(OFS) 판본 — 연결(CFS) 이 있으므로 선택되면 안 된다
        _fin_std_row("005930", dt.date(2025, 12, 31), dt.date(2026, 3, 12), "20260312000001",
                     fs_div="OFS", total_asset=111_000_000_000_000.0),
        # D 뒤에 접수된 정정 판본 — PIT 상 보이면 안 된다
        _fin_std_row("005930", dt.date(2025, 12, 31), dt.date(2026, 9, 30), "20260930000001",
                     total_asset=999_000_000_000_000.0),
    ]


def _make_roots(base: Path, *, fillers: list[str] | None = None,
                price_rows: list[dict] | None = None, adj_rows: list[dict] | None = None,
                eq_build: str = EQ_BUILD,
                fin_wise_rows: list[dict] | None = None,
                flow_rows: list[dict] | None = None,
                universe_rows: list[dict] | None = None) -> tuple[Path, Path]:
    eq, st = base / "eq", base / "st"
    f = _filler_tickers(N_FILLER) if fillers is None else fillers
    equity = {
        "price_daily": (price_rows if price_rows is not None else _price_rows(), eq_build),
        "price_adj_daily": (adj_rows if adj_rows is not None else _adj_rows(), eq_build),
        "universe_daily": (_universe_rows(f) if universe_rows is None else universe_rows,
                           eq_build),
        "security": (_security_rows(f), eq_build),
        "flow_daily": (flow_rows if flow_rows is not None else _flow_rows(), eq_build),
        "fin_std": (_fin_std_rows(), eq_build),
    }
    for table, (rows, build) in equity.items():
        _make_stage_tree(eq, table, rows, build_id=build)
    for table, rows in (("stg_consensus_matrix", _matrix_rows()),
                        ("stg_consensus_annual", _annual_rows()),
                        ("stg_fin_wise", _fin_wise_rows() if fin_wise_rows is None
                         else fin_wise_rows)):
        _make_stage_tree(st, table, rows, build_id=ST_BUILD)
    _make_stage_tree(st, "stg_master_daily", _master_rows(), partition_class="date_axis",
                     build_id=ST_BUILD)
    return eq / "stage", st / "stage"


@pytest.fixture(scope="module")
def roots(tmp_path_factory) -> tuple[Path, Path]:
    """(equity_root, stage_root) — 같은 판 규약, 루트만 다르다. 읽기만 하므로 모듈에서 공유."""
    return _make_roots(tmp_path_factory.mktemp("base"))


def _run(roots: tuple[Path, Path], target: Path, *, date: str = AS_OF, **kw):
    kw.setdefault("full", True)
    kw.setdefault("basis", "morning")
    return export(equity_root=roots[0], stage_root=roots[1], date=date, target=target, **kw)


def _rows(target: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(str(target))
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ── (a) 표별 upsert 결과·단위 ────────────────────────────────────────────────
def test_daily_prices_columns_rows_and_units(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["daily_prices"])
    assert res.tables["daily_prices"].n_rows == 6          # krx 2종목 × 3세션
    got = _rows(target, "SELECT stock_code, trade_date, open, high, low, close, volume, "
                        "amount, adj_close FROM daily_prices "
                        "WHERE stock_code='005930' AND trade_date='2026-09-23'")
    # adj_close = 종가 — 창 안 최신 행이고 창 안에 기준가 단계가 없다(v3 기준 K 사슬, QL-E. 전방 조정이면 63,180)
    assert got == [("005930", "2026-09-23", 69_700, 70_900, 69_300, 70_200, 1_000_000,
                    1234, 70_200.0)]
    assert res.n_adj_close_null == 0


def test_evening_rows_are_excluded_and_counted(roots, tmp_path: Path) -> None:
    """GAP-1/D-8 — v3 daily_prices 는 open/high/low NOT NULL 이라 저녁 T 행을 못 받는다."""
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["daily_prices"])
    assert res.n_evening_rows_skipped == 1
    assert _rows(target, "SELECT count(*) FROM daily_prices WHERE stock_code='000270'") == [(0,)]
    assert _rows(target, "SELECT n_evening_rows_skipped FROM _compat_meta") == [(1,)]


def test_stocks_snapshot_and_market_cap_in_eok(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["stocks"])
    assert res.tables["stocks"].n_rows == N_STOCKS
    got = _rows(target, "SELECT stock_code, stock_name, market, sector, market_cap, "
                        "listed_date, is_active, delisted_date FROM stocks "
                        "WHERE stock_code IN ('005930','000660') ORDER BY stock_code")
    assert got == [
        ("000660", "SK하이닉스", "KOSPI", "전기/전자", 98_765, "1996-12-26", 1, None),
        ("005930", "삼성전자", "KOSPI", "전기/전자", 123_457, "1975-06-11", 1, None),
    ]


def test_stocks_sector_is_krx_industry_from_kiwoom_master(roots, tmp_path: Path) -> None:
    """QL-B(T-19) — `sector` 는 v3 와 같은 원천(키움 ka10099 `upName` = KRX 업종명)이다.

    as-of D 이하 최신 마스터 스냅샷 하나를 쓴다 — D 뒤 스냅샷은 안 보이고(005930 '화학'),
    옛 스냅샷은 최신에 진다(000660 '기계/장비'). 공란·마스터 행 없음은 NULL(v3 `strip() or None`).
    """
    target = tmp_path / "quant.db"
    _run(roots, target, tables=["stocks"])
    got = dict(_rows(target, "SELECT stock_code, sector FROM stocks "
                             f"WHERE stock_code IN ('005930', '000660', '{SPAC}')"))
    assert got == {"005930": "전기/전자", "000660": "전기/전자", SPAC: None}
    # 마스터 행이 없는 filler 2,100 종목은 NULL — WICS 등 다른 분류로 메우지 않는다.
    assert _rows(target, "SELECT count(*) FROM stocks WHERE sector IS NOT NULL") == [(2,)]


def test_stocks_mirrors_v3_universe_common_and_spac_only(roots, tmp_path: Path) -> None:
    """D-11 — v3 `stocks` 는 KOSPI·KOSDAQ 의 common·spac 뿐이다(09-23 실측)."""
    target = tmp_path / "quant.db"
    _run(roots, target, tables=["stocks"])
    present = {r[0] for r in _rows(target, "SELECT stock_code FROM stocks")}
    assert SPAC in present                                  # spac 은 v3 에도 있다
    for ticker, _, _ in EXCLUDED:                           # preferred·etf·reit·foreign·dr·
        assert ticker not in present, ticker                # fund·KONEX 는 없다
    assert _rows(target, "SELECT count(DISTINCT market) FROM stocks") == [(2,)]


def test_model_universe_estimates_nulls_market_cap(roots, tmp_path: Path) -> None:
    """사용자 결정 09-24 — 당해 추정치(op·ni)가 없는 종목은 `market_cap` 이 NULL 이다.

    v3 엔진은 `market_cap >= min_market_cap` 으로만 유니버스를 자르므로(engine.py:71-78)
    행을 지우지 않고도 유니버스가 좁아진다. 이름·시장 열은 그대로라 후단 조인은 산다.
    """
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["stocks"], model_universe="estimates")
    assert res.tables["stocks"].n_rows == N_STOCKS          # 행 수는 그대로
    assert res.n_universe_with_estimates == 1               # 005930 만 op·ni 가 다 있다
    with_cap = _rows(target, "SELECT stock_code FROM stocks WHERE market_cap IS NOT NULL")
    assert with_cap == [("005930",)]
    # 000660 은 당해 추정 ni 가 없어 빠지지만 이름·시장은 남는다.
    assert _rows(target, "SELECT stock_name, market, market_cap FROM stocks "
                         "WHERE stock_code='000660'") == [("SK하이닉스", "KOSPI", None)]
    assert _rows(target, "SELECT model_universe, n_universe_with_estimates "
                         "FROM _compat_meta") == [("estimates", 1)]


def test_model_universe_all_keeps_market_cap(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["stocks"])
    assert res.n_universe_with_estimates is None
    assert _rows(target, "SELECT count(*) FROM stocks WHERE market_cap IS NOT NULL") == [(2,)]
    assert _rows(target, "SELECT model_universe FROM _compat_meta") == [("all",)]


# ── QL-B(T-19) — 제자리 반영의 시총은 전 종목(all) 고정 ──────────────────────────
# v3 quant.db 제자리 반영 뒤에는 v3 스코어링이 꺼지고(T-16) 시총은 뉴스 preview 상위 100 ·
# naver_ir 상위 600 · 엑셀 · unitelegram 이 읽는다. `estimates` 로 NULL 을 넣으면 그 소비자들이
# 대부분의 종목을 잃으므로 그림자(별도 파일) 전용으로만 남긴다.
def test_in_place_refuses_estimates_before_writing(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    with pytest.raises(CompatError, match="그림자 전용"):
        _run(roots, target, tables=["stocks"], in_place=True, model_universe="estimates")
    assert not target.exists()                              # 대상 파일을 열기 전에 멈춘다


def test_in_place_keeps_market_cap_for_all_stocks(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["stocks"], in_place=True)
    assert (res.model_universe, res.n_universe_with_estimates) == ("all", None)
    assert _rows(target, "SELECT count(*) FROM stocks WHERE market_cap IS NOT NULL") == [(2,)]


def test_investor_flows_in_million_krw(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["investor_detail_flows"])
    assert res.tables["investor_detail_flows"].n_rows == 6     # 미측정 셀 1개는 빠진다
    got = _rows(target, "SELECT individual, foreign_investor, institution_total, "
                        "etc_corporation FROM investor_detail_flows "
                        "WHERE stock_code='005930' AND trade_date='2026-09-21'")
    # ind = -3,456,000,000원 → -3,456 백만원. 주체마다 +1,000,000원(=+1 백만원)씩 어긋난다.
    assert got == [(-3456, -3455, -3454, -3445)]


# ── QL-A — 가격·수급도 v3 `stocks` 집합(D-11)만 ──────────────────────────────
# v3 는 `stocks` 에 든 종목만 가격·수급을 모은다. 09-28 그림자에서 compat `daily_prices` 3,817행 중
# `stocks` 에 있는 것은 2,490행이었다 — ETF·우선주 등이 섞여 위키 동일가중 수익률·가설 입력이 바뀐다.
# 여기서는 09-23 하루에 스팩 1 + 제외 7종(우선주·ETF·리츠·외국주·DR·펀드·KONEX) 행을 더 얹는다.
@pytest.fixture(scope="module")
def excluded_roots(tmp_path_factory) -> tuple[Path, Path]:
    extra = [SPAC] + [t for t, _, _ in EXCLUDED]
    prices = _price_rows() + [_price_row(t, D23, 10_000, value=VALUE_KRW, mktcap=MKTCAP_KRW)
                              for t in extra]
    flows = _flow_rows() + [_flow_row(t, D23) for t in extra]
    return _make_roots(tmp_path_factory.mktemp("excluded"), price_rows=prices,
                       adj_rows=_adj_rows(price_rows=prices), flow_rows=flows)


def test_daily_prices_keep_only_v3_stock_universe(excluded_roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    res = _run(excluded_roots, target, tables=["daily_prices"])
    assert {r[0] for r in _rows(target, "SELECT DISTINCT stock_code FROM daily_prices")} == \
        {*REAL, SPAC}
    assert res.tables["daily_prices"].n_rows == 7           # 보통주 2 × 3세션 + 스팩 1


# QL-A2 — 거래정지일 참고가 행(`price_kind='reference'`)도 v3 처럼 싣는다. KRX 는 그날 O/H/L 을 '0' 으로
# 주고 stage 가 NULL 로 두므로(결측은 결측) v3 NOT NULL 에 걸려 조용히 빠지고 있었다(10-01~08 재생에서
# v3 에만 있는 102~104행). v3 는 그날을 open=high=low=close=참고가 · volume 0 · amount 0 으로 싣는다
# (로컬 v3 사본 2026-07~08 정지 행 전부 같은 모양, 예: 000300 4200·4200·4200·4200·0·0).
def test_daily_prices_keeps_halted_reference_row_like_v3(tmp_path: Path) -> None:
    halted_ticker = _filler_tickers(1)[0]                   # D23 universe 행이 있는 보통주
    halted = _price_row(halted_ticker, D23, 4_200, ohl=False, value=0)
    halted.update(volume_shr=0, price_kind="reference")
    prices = _price_rows() + [halted]
    roots2 = _make_roots(tmp_path / "h", price_rows=prices, adj_rows=_adj_rows(price_rows=prices))
    target = tmp_path / "quant.db"
    res = _run(roots2, target, tables=["daily_prices"])
    assert (res.tables["daily_prices"].n_rows, res.tables["daily_prices"].n_skipped) == (7, 0)
    assert _rows(target, "SELECT open, high, low, close, volume, amount FROM daily_prices "
                         f"WHERE stock_code='{halted_ticker}'") == [(4200, 4200, 4200, 4200, 0, 0)]


def test_daily_prices_fills_blank_ohl_on_trade_row_without_regular_session(tmp_path: Path) -> None:
    """정규장 체결이 없던 날(price_kind='trade') KRX 가 O/H/L 을 공란으로 준 행 — v3 처럼 종가로 채운다.

    서버 `v3_post --full`(730일) 게이트가 실측으로 잡은 모양: 145210 2025-03-21 close 1,126 · 거래량
    1,015 · O/H/L 공란. v3 10-08 사본의 같은 행은 open=high=low=close=1126 이다(QL-F 리뷰). 정지 참고가
    행만 채우던 규칙(QL-A2)으로는 v3 NOT NULL 에 걸려 빠졌다.
    """
    ticker = _filler_tickers(2)[1]                          # D23 universe 행이 있는 보통주
    blank = _price_row(ticker, D23, 1_126, ohl=False, value=1_142_890)
    blank.update(volume_shr=1_015)                          # price_kind 는 기본 'trade'
    prices = _price_rows() + [blank]
    roots2 = _make_roots(tmp_path / "b", price_rows=prices, adj_rows=_adj_rows(price_rows=prices))
    target = tmp_path / "quant.db"
    res = _run(roots2, target, tables=["daily_prices"])
    assert (res.tables["daily_prices"].n_rows, res.tables["daily_prices"].n_skipped) == (7, 0)
    assert _rows(target, "SELECT open, high, low, close, volume, amount FROM daily_prices "
                         f"WHERE stock_code='{ticker}'") == [(1126, 1126, 1126, 1126, 1015, 1)]


def test_daily_prices_counts_rows_written_on_date(roots, tmp_path: Path) -> None:
    """신선도(T-31 ③) — 이번 실행이 **쓴** trade_date = D 행 수를 남긴다.

    대상에 D 행이 이미 있어도(제자리 반영의 스테이징은 본 파일 사본이다) 이번에 안 썼으면 0 이다.
    """
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["daily_prices"])
    assert res.tables["daily_prices"].metrics == {"n_on_date": 2}     # 005930·000660 의 09-23
    meta = json.loads(_rows(target, "SELECT tables FROM _compat_meta")[0][0])
    assert meta["daily_prices"]["metrics"] == {"n_on_date": 2}
    # 대상에만 있는 09-23 행(본 파일 사본의 옛 행)은 세지 않는다 — 날짜 단위 교체로 지워지고 이번에
    # 쓴 2행만 남는다. 판에 D 행이 아예 없는 실행은 쓰기 전에 멈춘다(QL-D MAJOR-1 — n_on_date 0 으로
    # 끝나지 않는다, test_compat_evening_t.test_morning_on_a_holiday_refuses_and_leaves_tables)
    con = sqlite3.connect(str(target))
    con.execute("INSERT INTO daily_prices VALUES ('999999','2026-09-23',1,1,1,1,1,1,1.0)")
    con.commit()
    con.close()
    res2 = _run(roots, target, tables=["daily_prices"])
    assert res2.tables["daily_prices"].metrics == {"n_on_date": 2}
    assert _rows(target, "SELECT count(*) FROM daily_prices WHERE trade_date='2026-09-23'") == \
        [(2,)]


def test_investor_flows_keep_only_v3_stock_universe(excluded_roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    res = _run(excluded_roots, target, tables=["investor_detail_flows"])
    assert {r[0] for r in _rows(target,
                                "SELECT DISTINCT stock_code FROM investor_detail_flows")} == \
        {*REAL, SPAC}
    assert res.tables["investor_detail_flows"].n_rows == 7


def test_consensus_revision_daily_and_compare(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    _run(roots, target, tables=["consensus_revision_daily", "consensus_revision_compare"])
    assert _rows(target, "SELECT stock_code, base_date, target_period, op, ni, collected_date "
                         "FROM consensus_revision_daily") == [
        ("005930", "2026-09-22", "2026/12", 1100.0, 2100.0, "2026-09-23")]
    assert _rows(target, "SELECT target_period, op_1w, op_1m, op_3m, op_1y "
                         "FROM consensus_revision_compare") == [
        ("2026/12", 1000.0, 900.0, 800.0, 700.0)]


def test_consensus_annual_period_and_data_type(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    _run(roots, target, tables=["consensus_annual"])
    assert _rows(target, "SELECT period, period_type, data_type, op, ni "
                         "FROM consensus_annual WHERE stock_code='005930' "
                         "ORDER BY period") == [
        ("2025/12", "annual", "actual", 500_000.0, 400_000.0),
        ("2026/12", "annual", "estimate", 500_000.0, 400_000.0)]


def test_financial_summary_scoring_window(roots, tmp_path: Path) -> None:
    """v3 quality·valuation 창(`period_type='annual' AND data_type IS NULL`)이 실제로 찬다."""
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["financial_summary"])
    assert res.tables["financial_summary"].n_rows == 6          # 기간 슬롯 6개
    got = _rows(target, "SELECT period, revenue, op, ni, eps, bps, per, pbr, ev_ebitda, "
                        "dividend_yield, shares, gross_profit, accounting_standard "
                        "FROM financial_summary WHERE period_type='annual' "
                        "AND data_type IS NULL ORDER BY period DESC LIMIT 1")
    assert got == [("2025/12", 3_336_059, 516_339, 399_075, 6564, 60_000, 18.27, 1.1, 4.89,
                    1.84, 5_969_782_550, 1_000_000, "IFRS연결")]
    # 직전 기도 슬롯이 어긋나지 않았는지 — 6슬롯 값이 전부 다르므로 밀리면 잡힌다.
    assert _rows(target, "SELECT revenue, op, gross_profit, bps, pbr FROM financial_summary "
                         "WHERE period='2024/12'") == [(3_008_709, 516_334, 900_004,
                                                        60_004, 1.14)]
    # (E) 슬롯은 data_type='estimate' 로 갈라져 scoring 창에 들지 않는다.
    assert _rows(target, "SELECT count(*) FROM financial_summary "
                         "WHERE data_type='estimate'") == [(1,)]


def test_financial_summary_null_columns_stay_null(roots, tmp_path: Path) -> None:
    """두 원천 어디에도 재료가 없는 열은 0 이 아니라 NULL 이다(결측은 결측)."""
    from compat.mappings import BY_TABLE
    target = tmp_path / "quant.db"
    _run(roots, target, tables=["financial_summary"])
    nulls = BY_TABLE["financial_summary"].null_columns
    assert set(nulls) == {"op_margin", "ni_margin", "yoy"}     # T1.5 뒤 남은 셋
    cols = ", ".join(nulls)
    for row in _rows(target, f"SELECT {cols} FROM financial_summary"):
        assert all(v is None for v in row)


# ── T1.5 / D-10 — DART fin_std 가 quality 입력을 채운다 ──────────────────────
def test_financial_summary_dart_quality_inputs(roots, tmp_path: Path) -> None:
    """roa·debt_ratio·roe(%) · total_assets·capex·fcf(억원) 가 산식·단위대로 찬다."""
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["financial_summary"])
    assert _rows(target, "SELECT roa, debt_ratio, roe, total_assets, capex, fcf "
                         "FROM financial_summary WHERE stock_code='005930' "
                         "AND period='2025/12'") == [
        (8.0, 25.0, 10.0, 5_000_000, 300_000, 300_000)]
    # 원천별 채움 수 — WISE 6기 전부 · DART 는 2025/12 한 기
    assert res.tables["financial_summary"].metrics == {"n_from_wise": 6, "n_from_dart": 1}
    meta = json.loads(_rows(target, "SELECT tables FROM _compat_meta")[0][0])
    assert meta["financial_summary"]["metrics"] == {"n_from_wise": 6, "n_from_dart": 1}


def test_financial_summary_dart_is_point_in_time(roots, tmp_path: Path) -> None:
    """D 뒤에 접수된 정정 판본(자산 999조)은 보이지 않고, 연결(CFS)이 별도(OFS)보다 앞선다."""
    target = tmp_path / "quant.db"
    _run(roots, target, tables=["financial_summary"])
    got = _rows(target, "SELECT total_assets FROM financial_summary "
                        "WHERE stock_code='005930' AND period='2025/12'")
    assert got == [(5_000_000,)]           # 999조(9,990,000)도 111조(1,110,000)도 아니다


def test_financial_summary_wise_gross_profit_wins(roots, tmp_path: Path) -> None:
    """gross_profit 은 WISE 값 우선(v3 원천에 가깝다) — DART 50조(500,000 억원)가 아니다."""
    target = tmp_path / "quant.db"
    _run(roots, target, tables=["financial_summary"])
    assert _rows(target, "SELECT gross_profit FROM financial_summary "
                         "WHERE stock_code='005930' AND period='2025/12'") == [(1_000_000,)]


def test_financial_summary_takes_each_ep_from_its_own_latest_version(tmp_path: Path) -> None:
    """G1(배포 묶음 7-2): stage 가 같은 원문을 접으면 cF3002(손익) 판은 09-22 뿐이고 cF4002(지표)만
    09-23 에 새 판일 수 있다. 09-23 행은 손익 = 09-22 판(값 +1,000), 지표 = 09-23 판이어야 한다.
    옛 쿼리(종목별 max 한 날짜)는 09-23 을 골라 revenue·op·ni·회계기준이 조용히 비었다."""
    base = _fin_wise_rows()
    rows = ([dict(r, fetched_date=D22, **{f"val_{i}": r[f"val_{i}"] + 1_000 for i in range(1, 7)})
             for r in base if r["ep"] == "cF3002"]
            + [dict(r, fetched_date=D22, **{f"val_{i}": r[f"val_{i}"] * 2 for i in range(1, 7)})
               for r in base if r["ep"] == "cF4002"]
            + [r for r in base if r["ep"] == "cF4002"])
    roots = _make_roots(tmp_path / "src", fin_wise_rows=rows)
    sql = ("SELECT period, revenue, op, ni, gross_profit, eps, per, accounting_standard "
           "FROM financial_summary WHERE period_type='annual' AND data_type IS NULL "
           "ORDER BY period DESC LIMIT 1")
    target = tmp_path / "quant.db"
    _run(roots, target, tables=["financial_summary"])
    assert _rows(target, sql) == [("2025/12", 3_337_059, 517_339, 400_075, 1_001_000, 6564,
                                   18.27, "IFRS연결")]
    # 09-22 기준이면 두 ep 다 09-22 판 — 지표도 09-22 값(×2)
    old = tmp_path / "quant_0922.db"
    _run(roots, old, tables=["financial_summary"], consensus_asof="20260922")
    assert _rows(old, sql) == [("2025/12", 3_337_059, 517_339, 400_075, 1_001_000, 13_127,
                                36.54, "IFRS연결")]


# ── R1 — accode 충돌(금융업)은 계정명으로 가른다. 실물 절단본으로 본다 ───────
@pytest.mark.skipif(not FIN_SLICE, reason="stage_slice stg_fin_wise 픽스처 없음")
def _financial_summary_on_real_slice() -> tuple[list[str], list[tuple]]:
    """절단본 stg_fin_wise 만으로 financial_summary SQL 을 돌린다(컬럼명, 행)."""
    import duckdb
    from compat.mappings import BY_TABLE
    globs = ", ".join(repr(str(p)) for p in FIN_SLICE)
    expr = f"read_parquet([{globs}], hive_partitioning=true, union_by_name=true)"
    # 절단본에는 equity 판이 없다 — DART 쪽은 빈 관계로 두고 WISE 갈래만 본다(R1 의 범위).
    empty_fin = ("(SELECT NULL::VARCHAR AS corp_code, NULL::DATE AS period_end, "
                 "NULL::VARCHAR AS report_code, NULL::VARCHAR AS fs_div, "
                 "NULL::VARCHAR AS rcept_no, NULL::DATE AS available_date, "
                 "NULL::DOUBLE AS total_asset, NULL::DOUBLE AS total_liab, "
                 "NULL::DOUBLE AS total_equity, NULL::DOUBLE AS net_income, "
                 "NULL::DOUBLE AS cf_operating_ytd, NULL::DOUBLE AS capex_ytd, "
                 "NULL::DOUBLE AS gross_profit WHERE false)")
    empty_sec = "(SELECT NULL::VARCHAR AS ticker, NULL::VARCHAR AS corp_code WHERE false)"
    sql = BY_TABLE["financial_summary"].sql.format(
        stg_fin_wise=expr, fin_std=empty_fin, security=empty_sec,
        consensus_asof="2026-09-03", date="2026-09-03")
    con = duckdb.connect()
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in (cur.description or [])]
        rows = cur.fetchall()
    finally:
        con.close()
    assert cols == list(BY_TABLE["financial_summary"].columns)
    return cols, rows


def test_financial_summary_acc_nm_whitelist_on_real_slice() -> None:
    """003540(대신증권)의 cF3002 200000 은 '순이자이익' 이다 — revenue 로 새면 안 된다."""
    cols, rows = _financial_summary_on_real_slice()
    i_code, i_rev = cols.index("stock_code"), cols.index("revenue")
    by_code: dict[str, set] = {}
    for r in rows:
        by_code.setdefault(r[i_code], set()).add(r[i_rev])
    assert by_code["003540"] == {None}, "금융업 '순이자이익' 이 revenue 로 새고 있다"
    assert any(v is not None for v in by_code["005930"])


def test_financial_summary_financial_template_ni_op_by_account_name() -> None:
    """DQ-6: 증권 템플릿은 당기순이익을 203730, 영업이익을 202820 에 싣는다(제조업 203170·201370).
    accode 고정이면 003540 의 ni·op 가 NULL — 계정명+최상위로 골라 2025/12 연결 1,867·3,014 억이 나와야 한다."""
    cols, rows = _financial_summary_on_real_slice()
    i = {c: cols.index(c) for c in ("stock_code", "period", "ni", "op", "revenue")}
    r = {row[i["period"]]: row for row in rows if row[i["stock_code"]] == "003540"}
    assert "2025/12" in r, sorted(r)
    assert r["2025/12"][i["ni"]] == 1867 and r["2025/12"][i["op"]] == 3014
    assert r["2025/12"][i["revenue"]] is None            # R1 그대로: '순이자이익' 은 매출이 아니다
    s = {row[i["period"]]: row for row in rows if row[i["stock_code"]] == "005930"}
    assert s["2025/12"][i["ni"]] is not None and s["2025/12"][i["op"]] is not None


# ── (b) 멱등 ─────────────────────────────────────────────────────────────────
def test_second_run_is_idempotent(roots, model_root, tmp_path: Path) -> None:
    # 표를 고르지 않으면 원천이 있는 표 전부 — 점수 두 표(QL-C)도 들어간다.
    target = tmp_path / "quant.db"
    first = _run(roots, target, model_root=model_root)
    assert {"score_history", "score_history_v2"} <= set(first.tables)
    before = {t: _rows(target, f"SELECT * FROM {t} ORDER BY 1, 2") for t in first.tables}
    second = _run(roots, target, model_root=model_root)
    assert {t: r.n_rows for t, r in first.tables.items()} == \
           {t: r.n_rows for t, r in second.tables.items()}
    for table, rows in before.items():
        now = _rows(target, f"SELECT * FROM {table} ORDER BY 1, 2")
        if table == "stocks":
            # `updated_at`(마지막 열)만 새 export 시각으로 바뀐다 — 나머지 값은 그대로다.
            assert [r[:-1] for r in now] == [r[:-1] for r in rows]
        else:
            assert now == rows


# ── (c) 0행 ──────────────────────────────────────────────────────────────────
def test_empty_result_raises(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    with pytest.raises(CompatEmptyError, match="daily_prices"):
        _run(roots, target, date="20200101", tables=["daily_prices"])


# ── (d) _compat_meta ─────────────────────────────────────────────────────────
def test_compat_meta_records_builds_and_window(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["daily_prices", "consensus_revision_daily"],
               window_days=730, consensus_asof="20260922")
    row = _rows(target, 'SELECT date, basis, equity_builds, stage_builds, tables, "window", '
                        "consensus_asof, status, failed_table FROM _compat_meta")[0]
    assert row[0] == "2026-09-23"
    assert row[1] == "morning"
    assert "price_daily" in row[2] and "stg_consensus_matrix" in row[3]
    assert '"daily_prices"' in row[4] and '"n_rows": 6' in row[4]
    assert json.loads(row[5]) == {"days": 730, "full": True, "from_date": "2024-09-23",
                                  "to_date": "2026-09-23"}
    assert row[6] == "2026-09-22"
    assert (row[7], row[8]) == ("ok", None)
    assert res.equity_builds["price_daily"] == EQ_BUILD


# ── (e) --consensus-asof ─────────────────────────────────────────────────────
def test_consensus_asof_shifts_matrix_snapshot(roots, tmp_path: Path) -> None:
    """G-M2: v3 09-23 점수의 리비전 입력이 09-22 자료였으므로 as-of 를 따로 줄 수 있어야 한다."""
    target = tmp_path / "quant.db"
    _run(roots, target, tables=["consensus_revision_daily"], consensus_asof="20260922")
    assert _rows(target, "SELECT base_date, op, ni, collected_date "
                         "FROM consensus_revision_daily") == [
        ("2026-09-21", 1000.0, 2000.0, "2026-09-22")]


# ── (f) 스키마 불일치 ────────────────────────────────────────────────────────
def test_existing_schema_mismatch_raises(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    con = sqlite3.connect(str(target))
    con.execute("CREATE TABLE stocks (stock_code TEXT PRIMARY KEY, whatever TEXT)")
    con.commit()
    con.close()
    with pytest.raises(CompatSchemaError, match="stocks"):
        _run(roots, target, tables=["stocks"])
    # 스키마가 다르면 쓰지 않는다 — `_compat_meta` 도 만들지 않는다.
    assert _rows(target, "SELECT count(*) FROM sqlite_master "
                         "WHERE name='_compat_meta'") == [(0,)]


def test_real_v3_stocks_column_order_is_accepted(roots, tmp_path: Path) -> None:
    """실물 v3 `stocks` 는 `delisted_date` 가 마이그레이션 ALTER 로 맨 뒤에 붙어 `updated_at` 뒤에 있다
    (로컬 v3 사본 quant.db 실측 — 선언 DDL 은 CREATE 순서). 열 집합·타입이 같으면 받아들이고 열 이름으로
    넣는다(QL-F: v3 사본 스테이징에 `--in-place` 를 돌리면 순서 비교 때문에 ②에서 멈췄다)."""
    target = tmp_path / "quant.db"
    con = sqlite3.connect(str(target))
    con.execute("""CREATE TABLE stocks (
        stock_code TEXT(6) PRIMARY KEY, stock_name TEXT NOT NULL,
        market TEXT NOT NULL CHECK(market IN ('KOSPI', 'KOSDAQ')), sector TEXT,
        market_cap INTEGER, listed_date TEXT, is_active INTEGER DEFAULT 1,
        updated_at TEXT DEFAULT (datetime('now')), delisted_date TEXT)""")
    con.commit()
    con.close()
    res = _run(roots, target, tables=["stocks"], in_place=True)
    assert res.tables["stocks"].n_rows == N_STOCKS
    assert _rows(target, "SELECT updated_at, delisted_date, listed_date FROM stocks "
                         "WHERE stock_code='005930'") == [(res.exported_at, None, "1975-06-11")]


# ── R2 — 이번 판에 없는 종목은 is_active=0 ───────────────────────────────────
def test_missing_stocks_are_marked_inactive(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    _run(roots, target, tables=["stocks"])
    dropped = _filler_tickers(N_FILLER)[:50]
    smaller = _make_roots(tmp_path / "b", fillers=_filler_tickers(N_FILLER)[50:])
    res = _run(smaller, target, tables=["stocks"])
    assert res.tables["stocks"].n_rows == N_STOCKS - 50
    where = ", ".join(repr(t) for t in dropped)
    assert _rows(target, f"SELECT is_active, count(*) FROM stocks "
                         f"WHERE stock_code IN ({where}) GROUP BY 1") == [(0, 50)]
    assert _rows(target, "SELECT count(*) FROM stocks") == [(N_STOCKS,)]


def test_stocks_below_minimum_refuses(tmp_path: Path) -> None:
    """유니버스 산출이 깨진 판으로 덮으면 멀쩡한 종목이 대거 폐지로 표시된다."""
    small = _make_roots(tmp_path / "s", fillers=_filler_tickers(10))
    with pytest.raises(CompatError, match="stocks 종목 수"):
        _run(small, tmp_path / "quant.db", tables=["stocks"])


def test_stocks_market_vocabulary_guard() -> None:
    """R4 — SQL 필터를 지나 v3 어휘 밖 값이 오면 쓰기 전에 멈춘다(어느 티커인지 메시지에)."""
    from compat.quant_db import MIN_STOCK_COUNT, _stocks_rows

    class _Cur:
        def fetchall(self):
            return [(f"00000{i % 10}", "이름", "KOSPI") for i in range(MIN_STOCK_COUNT)] + \
                   [("300000", "코넥스", "KONEX")]

    with pytest.raises(CompatError, match="300000"):
        _stocks_rows(_Cur(), ["stock_code", "stock_name", "market"])  # type: ignore[arg-type]


# ── R5 — 판 접두와 --basis ───────────────────────────────────────────────────
def test_basis_mismatch_refuses(tmp_path: Path) -> None:
    # 저녁 판(`e_`)을 아침 확정으로 내보내지 않는다. 반대 방향(장 마감 판이 D' 아침 판 `m_` 을
    # 읽는 것)은 컷오버 T-2 의 정상 경로라 R5 가 막지 않고 이음매 검사가 본다(QL-D,
    # test_compat_evening_t).
    evening = _make_roots(tmp_path / "ev", eq_build="e_20260923T122000_000000Z")
    with pytest.raises(CompatError, match="--basis"):
        _run(evening, tmp_path / "quant.db", basis="morning", tables=["daily_prices"])


# ── R7 — 건너뛴 행 비율 ──────────────────────────────────────────────────────
def test_skip_ratio_over_limit_refuses(tmp_path: Path) -> None:
    bad = _make_roots(tmp_path / "bad", price_rows=_price_rows(bad_volume=True))
    with pytest.raises(CompatError, match="건너뛴 행 비율"):
        _run(bad, tmp_path / "quant.db", tables=["daily_prices"])


def _calendar(base: Path, holidays: tuple[str, ...] = ()) -> Path:
    """판정 달력 폴더 — 2026 주말 + `holidays`(test_compat_evening_t `_calendar` 와 같은 방식)."""
    d = base / "calendar"
    d.mkdir(parents=True, exist_ok=True)
    days = (dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(365))
    hol = [x.strftime("%Y%m%d") for x in days if x.weekday() >= 5] + list(holidays)
    (d / "kis_holidays_2026.json").write_text(json.dumps({"year": 2026, "holidays": hol}),
                                              encoding="utf-8")
    return d


# ── R10 — 증분 가드 ──────────────────────────────────────────────────────────
# 증분 창 시작일을 판정 달력으로 세므로(K1-9d) 가드까지 가려면 달력이 있어야 한다.
def test_incremental_on_fresh_target_refuses(roots, tmp_path: Path) -> None:
    with pytest.raises(CompatError, match="daily_prices 가 없다"):
        _run(roots, tmp_path / "quant.db", tables=["daily_prices"], full=False,
             calendar_dir=_calendar(tmp_path))


def test_incremental_on_shallow_target_refuses(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    _run(roots, target, tables=["daily_prices"])
    with pytest.raises(CompatError, match="세션 중앙값"):
        _run(roots, target, tables=["daily_prices"], full=False,
             calendar_dir=_calendar(tmp_path))


# ── R3 — --window-days 는 --full 전용 ────────────────────────────────────────
def test_window_days_without_full_refuses(roots, tmp_path: Path) -> None:
    with pytest.raises(CompatError, match="--window-days"):
        _run(roots, tmp_path / "quant.db", tables=["daily_prices"], full=False,
             window_days=550)


# ── K1-9d · T-14 — 증분 창 = 08:10 KRX 재수집 창(daily.calendar) ────────────
# 창은 as_of 이하 마지막 거래일 L 과 그 앞 10거래일(L 포함 11세션)이다. 14달력일 창은 평시엔 같은
# 11세션이지만 설·추석처럼 평일 휴장이 끼면 7~8세션으로 줄어, 아침 재수집으로 고친 앞쪽 날이
# `--full` 전까지 v3 에 반영되지 않고 T-41 덮어쓰기 재현·자가 복구 창도 좁아진다.
_LONG_BREAK = ("20260916", "20260917", "20260918")                 # 합성 사흘 연휴(수~금)
_CHUSEOK_2026 = ("20260924", "20260925", "20261005", "20261009")   # 추석·개천절 대체·한글날


def test_incremental_window_spans_krx_refetch_across_holidays(roots, tmp_path: Path) -> None:
    """09-23 과 그 앞 10거래일(11세션)은 연휴를 건너 09-04 부터다 — 14달력일(09-09~)이면
    8세션뿐. 창은 `_compat_meta.window` 에 그대로 남는다(days 는 달력일 폭)."""
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["investor_detail_flows"], full=False,
               calendar_dir=_calendar(tmp_path, _LONG_BREAK))
    want = {"days": 19, "full": False, "from_date": "2026-09-04", "to_date": "2026-09-23"}
    assert res.window == want
    assert json.loads(_rows(target, 'SELECT "window" FROM _compat_meta')[0][0]) == want
    assert res.tables["investor_detail_flows"].n_rows == 6


@pytest.mark.parametrize("as_of", [dt.date(2026, 10, 8),     # 거래일
                                   dt.date(2026, 10, 9),     # 한글날 휴장 → 직전 거래일 10-08 부터
                                   dt.date(2026, 10, 11)])   # 일요일
def test_incremental_from_counts_back_from_last_trading_day(tmp_path: Path,
                                                            as_of: dt.date) -> None:
    """2026 추석·개천절 대체공휴일 뒤 — 10-08 과 그 앞 10거래일이면 09-21 부터다(14달력일 09-24~
    는 8세션)."""
    cal = _calendar(tmp_path, _CHUSEOK_2026)
    assert quant_db._incremental_from(as_of, cal) == dt.date(2026, 9, 21)


def test_incremental_window_starts_at_daily_build_krx_refetch(tmp_path: Path) -> None:
    """P4 계약 — 증분 창 시작 = 08:10 `daily_build.sh` krx_step 재수집 시작
    (`prev_trading_day(D, n=N)`). 셸의 N 이 바뀌면 compat 창도 따라가야 한다."""
    sh = (Path(__file__).resolve().parents[1] / "scripts" / "daily_build.sh").read_text(
        encoding="utf-8")
    step = sh[sh.index("krx_step() {"):sh.index("for attempt in")]
    (n,) = re.findall(r"prev_trading_day\(.*, n=(\d+)\)", step)
    cal_dir = _calendar(tmp_path, _CHUSEOK_2026)
    d = dt.date(2026, 10, 8)
    want = daily_calendar.load(cal_dir).prev_trading_day(d, n=int(n))
    assert quant_db._incremental_from(d, cal_dir) == want


@pytest.mark.parametrize("date, cal", [("20260923", "nocal"),        # 달력 폴더 없음
                                       ("20270105", "cal2026")])     # 그 해 판정 파일 없음
def test_incremental_without_calendar_refuses_before_writing(roots, tmp_path: Path, date: str,
                                                             cal: str) -> None:
    """K1-9 ⑦ — 달력을 못 읽으면 영업일을 가정하지 않고 대상 파일을 열기 전에 멈춘다."""
    target = tmp_path / "quant.db"
    cal_dir = _calendar(tmp_path) if cal == "cal2026" else tmp_path / cal
    with pytest.raises(CompatError, match="증분 창 시작일을 셀 수 없다"):
        _run(roots, target, date=date, tables=["investor_detail_flows"], full=False,
             calendar_dir=cal_dir)
    assert not target.exists()


def test_full_window_stays_730_calendar_days(roots, tmp_path: Path) -> None:
    """`--full` 창은 그대로 730달력일이다 — 제자리 반영이 아니면 달력을 읽지 않는다."""
    res = _run(roots, tmp_path / "quant.db", tables=["investor_detail_flows"],
               calendar_dir=tmp_path / "nocal")
    assert res.window == {"days": 730, "full": True, "from_date": "2024-09-23",
                          "to_date": "2026-09-23"}


# ── T-46 — 제자리 --full 창은 대상 v3 표의 이력 시작보다 앞으로 가지 않는다 ─────────────
# 창이 v3 이력 시작 앞이면 v3 소비자 표에 없던 앞 기간 행이 생긴다(서버 10-08 사본 `--full` 재생). 그래서
# 대상에 쓰기 전에 멈추고(rc 2) `--window-days` 로 창을 이력 안에 맞추게 한다.
_V3_HISTORY = {"daily_prices": "2025-01-02", "investor_detail_flows": "2025-01-23"}


def _v3_target(path: Path, history: dict[str, str]) -> Path:
    """v3 본 파일 모양의 대상(WAL — v3 `connection.py` 와 같다). 표마다 `history` 날짜에 첫 행 하나."""
    con = sqlite3.connect(str(path))
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(quant_db.SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
        if "daily_prices" in history:
            con.execute("INSERT INTO daily_prices VALUES ('005930', ?, 1, 1, 1, 7, 1, 1, 7.0)",
                        (history["daily_prices"],))
        if "investor_detail_flows" in history:
            con.execute("INSERT INTO investor_detail_flows (stock_code, trade_date, individual) "
                        "VALUES ('005930', ?, -1)", (history["investor_detail_flows"],))
        con.commit()
    finally:
        con.close()
    return path


def test_in_place_full_before_target_history_refuses_before_writing(roots, tmp_path: Path,
                                                                    capsys) -> None:
    """730일 창(2024-09-23~)이 두 표의 이력 시작보다 앞 — rc 2, 대상 바이트 그대로. 메시지에 표·이력 시작·창 시작과
    맞출 `--window-days`(as_of − 늦은 이력 시작)가 있다. 그 값으로 맞추면 지난다."""
    target = _v3_target(tmp_path / "quant.db", _V3_HISTORY)
    cal = _calendar(tmp_path)
    before = target.read_bytes()
    days = (D23 - dt.date(2025, 1, 23)).days
    rc = cli_main(["export", "--date", AS_OF, "--basis", "morning", "--equity-root", str(roots[0]),
                   "--stage-root", str(roots[1]), "--target", str(target),
                   "--tables", "daily_prices,investor_detail_flows", "--full", "--in-place",
                   "--calendar-dir", str(cal)])
    err = capsys.readouterr().err
    assert rc == 2
    for needle in ("daily_prices 이력 시작 2025-01-02", "investor_detail_flows 이력 시작 2025-01-23",
                   "창 시작 2024-09-23", f"--window-days {days}"):
        assert needle in err, err
    assert target.read_bytes() == before
    res = _run(roots, target, tables=["daily_prices", "investor_detail_flows"], in_place=True,
               window_days=days, calendar_dir=cal)
    assert res.status == "ok" and res.window["from_date"] == "2025-01-23"


@pytest.mark.parametrize("history, in_place", [({}, True),             # 대상 표가 비었다(이력 없음)
                                                (_V3_HISTORY, False)])  # 그림자(별도 파일)
def test_full_history_floor_skips_empty_tables_and_shadow(roots, tmp_path: Path, history,
                                                         in_place) -> None:
    target = _v3_target(tmp_path / "quant.db", history)
    res = _run(roots, target, tables=["daily_prices", "investor_detail_flows"], in_place=in_place,
               calendar_dir=_calendar(tmp_path))
    assert res.status == "ok" and res.window["from_date"] == "2024-09-23"


# ── --builds-from (과거 날짜 비교) ───────────────────────────────────────────
def test_builds_from_pins_an_older_build(tmp_path: Path) -> None:
    """current_build 가 아니라 인계 이력이 가리킨 판을 읽는다."""
    base = tmp_path / "hist"
    roots2 = _make_roots(base, eq_build=EQ_BUILD_OLD)
    newer = [dict(r) for r in _price_rows()]
    for r in newer:                                  # 새 판은 종가를 1,000 올려 짓는다
        r["close"] += 1_000
    _make_stage_tree(base / "eq", "price_daily", newer, build_id=EQ_BUILD)
    _make_stage_tree(base / "eq", "price_adj_daily", _adj_rows(), build_id=EQ_BUILD)

    hist = tmp_path / "20260923_morning.json"
    hist.write_text(json.dumps({
        "equity_builds": {t: EQ_BUILD_OLD for t in
                          ("price_daily", "price_adj_daily", "universe_daily", "security",
                           "flow_daily")},
        "stage_builds": {t: ST_BUILD for t in
                         ("stg_consensus_matrix", "stg_consensus_annual", "stg_fin_wise",
                          "stg_master_daily")},
    }), encoding="utf-8")

    target = tmp_path / "quant.db"
    res = _run(roots2, target, tables=["daily_prices"], builds_from=hist)
    assert res.equity_builds["price_daily"] == EQ_BUILD_OLD
    assert _rows(target, "SELECT close FROM daily_prices WHERE stock_code='005930' "
                         "AND trade_date='2026-09-23'") == [(70_200,)]
    # --builds-from 없이 돌리면 current_build(새 판)를 읽는다 — 값이 다르다.
    new_target = tmp_path / "new.db"
    res2 = _run(roots2, new_target, tables=["daily_prices"])
    assert res2.equity_builds["price_daily"] == EQ_BUILD
    assert _rows(new_target, "SELECT close FROM daily_prices WHERE stock_code='005930' "
                             "AND trade_date='2026-09-23'") == [(71_200,)]


def test_builds_from_missing_key_falls_back_to_current(roots, tmp_path: Path) -> None:
    """인계 JSON 에 표 키가 아예 없는 날(09-18 실측: 그날은 `sector_snapshot` 이 없었다 — 표가
    없던 날). 지금 `stocks` 가 읽는 표 중에서는 stage `stg_master_daily` 로 같은 경로를 본다."""
    hist = tmp_path / "20260918_morning.json"
    hist.write_text(json.dumps({
        "equity_builds": {t: EQ_BUILD for t in
                          ("price_daily", "price_adj_daily", "universe_daily", "security",
                           "flow_daily")},
        "stage_builds": {t: ST_BUILD for t in
                         ("stg_consensus_matrix", "stg_consensus_annual", "stg_fin_wise")},
    }), encoding="utf-8")                               # stg_master_daily 없음
    with pytest.raises(CompatError, match="stg_master_daily"):
        _run(roots, tmp_path / "e.db", tables=["stocks"], builds_from=hist)

    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["stocks"], builds_from=hist,
               builds_from_missing="current")
    assert res.builds_fallback == ("stg_master_daily",)
    assert res.stage_builds["stg_master_daily"] == ST_BUILD      # current_build
    assert json.loads(_rows(target, "SELECT builds_fallback FROM _compat_meta")[0][0]) == \
        ["stg_master_daily"]


def test_builds_from_gc_removed_build_falls_back_to_current(roots, tmp_path: Path) -> None:
    """09-21 실측 — 인계 판이 stage keep=3 GC 로 MANIFEST 에서 사라졌다.

    stage 는 `fetched_date` append-only 라 current_build + as-of 필터가 같은 행을 고른다.
    """
    hist = tmp_path / "20260921_morning.json"
    hist.write_text(json.dumps({
        "equity_builds": {"price_daily": EQ_BUILD},
        "stage_builds": {"stg_consensus_matrix": "m_20260922T000215_559494Z"},   # GC 로 소멸
    }), encoding="utf-8")
    with pytest.raises(CompatError, match="MANIFEST 에 없다"):
        _run(roots, tmp_path / "e.db", tables=["consensus_revision_daily"], builds_from=hist)

    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["consensus_revision_daily"], builds_from=hist,
               builds_from_missing="current")
    assert res.builds_fallback == ("stg_consensus_matrix",)
    assert res.stage_builds["stg_consensus_matrix"] == ST_BUILD
    # 폴백해도 as-of 필터가 같은 스냅샷을 고른다.
    assert _rows(target, "SELECT base_date, op FROM consensus_revision_daily") == [
        ("2026-09-22", 1100.0)]
    assert "fallback=stg_consensus_matrix" in res.summary()


def test_builds_from_unknown_build_refuses(roots, tmp_path: Path) -> None:
    hist = tmp_path / "bad.json"
    hist.write_text(json.dumps({
        "equity_builds": {"price_daily": "m_없는판", "price_adj_daily": EQ_BUILD},
        "stage_builds": {"stg_fin_wise": ST_BUILD}}), encoding="utf-8")
    with pytest.raises(CompatError, match="MANIFEST 에 없다"):
        _run(roots, tmp_path / "quant.db", tables=["daily_prices"], builds_from=hist)


# ── CLI ──────────────────────────────────────────────────────────────────────
def test_cli_exports_and_prints_summary(roots, tmp_path: Path, capsys) -> None:
    target = tmp_path / "quant.db"
    rc = cli_main(["export", "--date", AS_OF, "--basis", "morning",
                   "--equity-root", str(roots[0]), "--stage-root", str(roots[1]),
                   "--target", str(target), "--tables", "daily_prices,stocks", "--full",
                   "--model-universe", "estimates"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "daily_prices=6" in out and f"stocks={N_STOCKS}" in out
    assert "universe(estimates)=1" in out


def test_cli_in_place_refuses_estimates(roots, tmp_path: Path, capsys) -> None:
    rc = cli_main(["export", "--date", AS_OF, "--basis", "morning",
                   "--equity-root", str(roots[0]), "--stage-root", str(roots[1]),
                   "--target", str(tmp_path / "quant.db"), "--tables", "stocks", "--full",
                   "--in-place", "--model-universe", "estimates"])
    assert rc == 2
    assert "그림자 전용" in capsys.readouterr().err


def test_cli_returns_2_on_error(roots, tmp_path: Path) -> None:
    rc = cli_main(["export", "--date", "20200101", "--basis", "morning",
                   "--equity-root", str(roots[0]), "--stage-root", str(roots[1]),
                   "--target", str(tmp_path / "quant.db"), "--tables", "daily_prices", "--full"])
    assert rc == 2


def test_cli_returns_2_on_unexpected_exception(tmp_path: Path, capsys) -> None:
    """R4 — `CompatError` 가 아닌 예외도 rc 2 + 한 줄 원인으로 끝난다."""
    rc = cli_main(["export", "--date", AS_OF, "--basis", "morning",
                   "--equity-root", str(tmp_path / "없는루트"),
                   "--stage-root", str(tmp_path / "없는루트"),
                   "--target", str(tmp_path / "quant.db"), "--tables", "daily_prices", "--full"])
    assert rc == 2
    assert "compat 실패" in capsys.readouterr().err


def test_cli_score_tables_without_model_root_refuse(roots, tmp_path: Path, capsys) -> None:
    """점수 표(QL-C)는 모델 판 루트가 있어야 한다 — 없으면 조용히 건너뛰지 않고 rc 2 다."""
    target = tmp_path / "quant.db"
    rc = cli_main(["export", "--date", AS_OF, "--basis", "morning",
                   "--equity-root", str(roots[0]), "--stage-root", str(roots[1]),
                   "--target", str(target), "--tables", "score_history",
                   "--calendar-dir", str(_calendar(tmp_path))])    # 증분 창(K1-9d)
    assert rc == 2
    assert "--model-root" in capsys.readouterr().err
    assert not target.exists()                       # 쓰기 전에 멈춘다


def test_cli_exports_score_tables(roots, model_root, tmp_path: Path, capsys) -> None:
    rc = cli_main(["export", "--date", AS_OF, "--basis", "morning",
                   "--equity-root", str(roots[0]), "--stage-root", str(roots[1]),
                   "--model-root", str(model_root), "--target", str(tmp_path / "quant.db"),
                   "--tables", "score_history,score_history_v2",
                   "--calendar-dir", str(_calendar(tmp_path))])    # 증분 창(K1-9d)
    assert rc == 0
    out = capsys.readouterr().out
    assert f"score_history={len(SCORE_CODES)}" in out
    assert f"score_history_v2={len(SCORE_CODES)}" in out


# ── (g) 점수 표 — QL-C(T-16 · T-17) ───────────────────────────────────────────
# 모델 판 규약(`src/model/build.py` · `src/deliver/reader.py`):
#   `<model_root>/<spec_id>/v=<build_id>/scores.parquet` + `_runs/<YYYYMMDD>_<basis>.json`
#   + `latest_<basis>.json`. parquet 은 실제 writer(`model.build.write_parquet` + 레지스트리 spec 의
#   `gates.score_dtypes`)로 쓴다 — 열 구성·순서·dtype 이 서버 판과 같다.
# 합성 판 두 개: 09-23 판(MB_D23)과 다음 날 09-24 판(MB_D24, `latest_morning.json` 이 가리킨다).
#   값은 열마다 다르게 두고 09-24 판은 +100 을 더해, 어느 판을 읽었는지 값으로 가린다.
SCOPE, V2 = "scope@1.0", "v2_percentrank@1.0"
MB_D23 = "m_20260924T040000_000000Z"
MB_D24 = "m_20260925T040000_000000Z"
D23_ISO, D24_ISO = "2026-09-23", "2026-09-24"
SCORE_CODES = ("005930", "000660", "123450")
# scope@1.0 에서 v3 `score_history` 로 옮기지 않는 열 — 리터럴로 고정한다(명세).
#   6열: v3 에서도 항상 NULL(플랜 §8-1, 엔진 MG3 가 NULL 을 강제)
#   val_ev_ebitda: scope 는 EV/EBITDA 를 밸류에 쓰지 않는다(원본 v3 에서 비어 있던 계산과 같게
#                  만든 spec) — 값이 있어도 v3 열 의미('밸류 입력')와 달라 NULL 로 둔다
SCOPE_NULL_COLS = ("growth_score", "sentiment_score", "volatility_score", "size_score",
                   "foreign_score", "shareholder_score", "val_ev_ebitda")
_ALWAYS_NULL = SCOPE_NULL_COLS[:6]


def _score_row(columns: tuple[str, ...], total_col: str, code: str, d_iso: str, rank: int,
               shift: float, null: tuple[str, ...] = ()) -> dict:
    """열마다 다른 값 — 어느 열이 어디로 갔는지 값으로 가린다."""
    row: dict = dict.fromkeys(columns)
    for j, c in enumerate(columns):
        if c in ("stock_code", "score_date", "rank") or c.endswith("_flag") or c in null:
            continue
        row[c] = round(shift + rank * 0.25 + j * 0.001, 6)
    row.update(stock_code=code, score_date=d_iso, rank=rank)
    row[total_col] = shift + 10.0 - rank
    return row


def _scope_rows(d_iso: str, shift: float = 0.0) -> list[dict]:
    from model.contracts import V3_SCORE_COLUMNS
    rows = [_score_row(V3_SCORE_COLUMNS, "composite_score", c, d_iso, i, shift, _ALWAYS_NULL)
            for i, c in enumerate(SCORE_CODES, start=1)]
    rows[0]["op_1w_flag"] = "흑전"                 # 플래그(TEXT) 열도 그대로 간다
    rows[2]["r1m"] = None                          # 엔진 결측(T-9 등)은 결측 그대로
    for r in rows:
        r["val_ev_ebitda"] = 7.5                   # scope 엔진은 원값을 싣는다 → v3 에는 NULL
    return rows


def _v2_rows(d_iso: str, shift: float = 0.0) -> list[dict]:
    from model.contracts import V2_SCORE_COLUMNS
    rows = [_score_row(V2_SCORE_COLUMNS, "total_score", c, d_iso, i, shift)
            for i, c in enumerate(SCORE_CODES, start=1)]
    rows[1]["per_next"] = None
    return rows


def _write_model_run(root: Path, d_iso: str, build_id: str, scores: dict[str, list[dict]], *,
                     status: str = "ok", excluded: tuple[str, ...] = (),
                     runs: bool = True, latest: bool = True) -> None:
    """`model.build.build` 가 남기는 모양 그대로의 판 하나(빠진 spec 은 parquet 도 없다)."""
    from model import gates, registry
    from model.build import write_parquet
    for spec_id, rows in scores.items():
        if spec_id in excluded or status != "ok":       # 실패 판·빠진 spec 은 parquet 이 없다
            continue
        write_parquet(rows, gates.score_dtypes(registry.get(spec_id)),
                      root / spec_id / f"v={build_id}" / "scores.parquet")
    payload = {
        "layer": "model", "status": status, "build_id": build_id, "date": d_iso,
        "basis": "morning", "fi_build_id": "m_20260924T030000_000000Z",
        "generated_at": f"{d_iso}T04:00:00Z",
        "specs": {s: {"n_scores": len(r), "n_ranked": len(r), "n_excluded": 0, "gates": {}}
                  for s, r in scores.items() if s not in excluded},
        "excluded_specs": {s: {"error": "ValueError: 합성 예외"} for s in excluded},
        "primary_spec": SCOPE, "elapsed_s": 1.0}
    text = json.dumps(payload, ensure_ascii=False)
    if runs:
        path = root / "_runs" / f"{d_iso.replace('-', '')}_morning.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if latest:
        root.mkdir(parents=True, exist_ok=True)
        (root / "latest_morning.json").write_text(text, encoding="utf-8")


@pytest.fixture(scope="module")
def model_root(tmp_path_factory) -> Path:
    """09-23 판 + 09-24 판(latest). 읽기만 하므로 모듈에서 공유."""
    root = tmp_path_factory.mktemp("model") / "model"
    _write_model_run(root, D23_ISO, MB_D23, {SCOPE: _scope_rows(D23_ISO),
                                             V2: _v2_rows(D23_ISO)})
    _write_model_run(root, D24_ISO, MB_D24, {SCOPE: _scope_rows(D24_ISO, 100.0),
                                             V2: _v2_rows(D24_ISO, 100.0)})
    return root


def _v3_tuples(rows: list[dict], columns: tuple[str, ...],
               null: tuple[str, ...] = ()) -> list[tuple]:
    """모델 행 → v3 표에 들어가야 할 행(열 순서 = v3 스키마, `null` 열은 NULL)."""
    return sorted(tuple(None if c in null else r[c] for c in columns) for r in rows)


def _seed(target: Path, table: str, rows: list[tuple]) -> None:
    """v3 스키마 대상에 기존 행을 미리 넣는다(v3 가 쓰던 옛 행 흉내)."""
    from compat.quant_db import SCHEMA_SQL_PATH
    con = sqlite3.connect(str(target))
    try:
        con.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
        n = len(rows[0])
        con.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * n)})", rows)
        con.commit()
    finally:
        con.close()


def _old_v3_rows(d_iso: str, codes: tuple[str, ...], n_cols: int, total: float) -> list[tuple]:
    """v3 가 쓰던 옛 행 — 종목코드·날짜·총점만 있고 나머지는 NULL(총점은 두 표 모두 7번째 열)."""
    out = []
    for code in codes:
        row: list = [None] * n_cols
        row[0], row[1], row[6] = code, d_iso, total
        out.append(tuple(row))
    return out


def test_score_mappings_match_v3_schema_and_model_contract() -> None:
    """두 점수 표의 열 = v3 DDL(`v3_schema.sql`) = 모델 spec 의 점수 열 계약(이름·순서)."""
    from compat.mappings import BY_TABLE
    from compat.quant_db import SCHEMA_SQL_PATH
    from model import registry
    from model.contracts import score_columns
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
    for table, spec in (("score_history", SCOPE), ("score_history_v2", V2)):
        m = BY_TABLE[table]
        v3_cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        assert m.source_kind == "model" and m.sources == (spec,)
        assert list(m.columns) == v3_cols
        assert tuple(m.columns) == score_columns(registry.get(spec))
        assert m.pk == ("stock_code", "score_date")
    con.close()
    assert set(BY_TABLE["score_history"].null_columns) == set(SCOPE_NULL_COLS)
    assert BY_TABLE["score_history_v2"].null_columns == ()


def test_score_history_from_scope_run(roots, model_root, tmp_path: Path) -> None:
    """scope@1.0 → score_history 48열. 열 이름 그대로, 값 그대로, NULL 열 7개만 비운다."""
    from model.contracts import V3_SCORE_COLUMNS
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["score_history"], model_root=model_root)
    assert res.tables["score_history"].n_rows == len(SCORE_CODES)
    got = sorted(_rows(target, f"SELECT {', '.join(V3_SCORE_COLUMNS)} FROM score_history"))
    assert got == _v3_tuples(_scope_rows(D23_ISO), V3_SCORE_COLUMNS, SCOPE_NULL_COLS)
    assert _rows(target, "SELECT count(*) FROM score_history WHERE val_ev_ebitda IS NOT NULL "
                         "OR growth_score IS NOT NULL") == [(0,)]
    assert _rows(target, "SELECT op_1w_flag FROM score_history WHERE rank = 1") == [("흑전",)]


def test_score_history_v2_column_mapping(roots, model_root, tmp_path: Path) -> None:
    """v2_percentrank@1.0 → score_history_v2 21열 전부 그대로(NULL 로 비우는 열 없음)."""
    from model.contracts import V2_SCORE_COLUMNS
    target = tmp_path / "quant.db"
    _run(roots, target, tables=["score_history_v2"], model_root=model_root)
    got = sorted(_rows(target, f"SELECT {', '.join(V2_SCORE_COLUMNS)} FROM score_history_v2"))
    assert got == _v3_tuples(_v2_rows(D23_ISO), V2_SCORE_COLUMNS)


@pytest.mark.parametrize("table", ["score_history", "score_history_v2"])
def test_score_date_is_replaced_other_dates_kept(roots, model_root, tmp_path: Path,
                                                 table: str) -> None:
    """T-16 — 그 score_date 행을 모두 지우고 새로 넣는다. 다른 날짜는 그대로다.

    v3 옛 행: 09-22(다른 날) 2종목 + 09-23 4종목(그중 999990·888880 은 새 판에 없다 — v3 유니버스
    1,329 → scope 593 축소 흉내). 교체 뒤 09-23 은 새 판 3종목뿐이고 09-22 는 손대지 않는다.
    """
    from model.contracts import V2_SCORE_COLUMNS, V3_SCORE_COLUMNS
    cols = V3_SCORE_COLUMNS if table == "score_history" else V2_SCORE_COLUMNS
    old_other = _old_v3_rows("2026-09-22", ("005930", "999990"), len(cols), 1.5)
    old_same = _old_v3_rows(D23_ISO, ("005930", "000660", "999990", "888880"), len(cols), -2.0)
    target = tmp_path / "quant.db"
    _seed(target, table, old_other + old_same)
    _run(roots, target, tables=[table], model_root=model_root)
    assert sorted(_rows(target, f"SELECT * FROM {table} WHERE score_date = '2026-09-22'")) == \
        sorted(old_other)
    assert _rows(target, f"SELECT stock_code FROM {table} WHERE score_date = '{D23_ISO}' "
                         "ORDER BY rank") == [(c,) for c in SCORE_CODES]


def test_score_rerun_is_idempotent(roots, model_root, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    tables = ["score_history", "score_history_v2"]
    _run(roots, target, tables=tables, model_root=model_root)
    before = {t: _rows(target, f"SELECT * FROM {t} ORDER BY 1, 2") for t in tables}
    _run(roots, target, tables=tables, model_root=model_root)
    assert {t: _rows(target, f"SELECT * FROM {t} ORDER BY 1, 2") for t in tables} == before


def test_score_run_is_pinned_to_date_not_latest(roots, model_root, tmp_path: Path) -> None:
    """P1 — `latest_morning.json` 은 09-24 판이지만 --date 09-23 은 09-23 판(`_runs`)을 읽는다."""
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["score_history"], model_root=model_root)
    assert res.tables["score_history"].sources == {SCOPE: MB_D23}
    assert _rows(target, "SELECT DISTINCT score_date FROM score_history") == [(D23_ISO,)]
    assert _rows(target, "SELECT max(composite_score) FROM score_history") == [(9.0,)]


def test_score_run_from_latest_of_same_date(roots, tmp_path: Path) -> None:
    """`_runs/<D>` 가 없어도 `latest_<basis>.json` 의 날짜가 D 면 그 판이다(deliver.reader 규약)."""
    root = tmp_path / "model"
    _write_model_run(root, D23_ISO, MB_D23, {SCOPE: _scope_rows(D23_ISO)}, runs=False)
    res = _run(roots, tmp_path / "quant.db", tables=["score_history"], model_root=root)
    assert res.tables["score_history"].sources == {SCOPE: MB_D23}


@pytest.mark.parametrize("case", ["no_run", "gate_failed"])
def test_score_without_run_of_that_date_refuses(roots, tmp_path: Path, case: str) -> None:
    """P1 — 그날 성공 판이 없으면 다른 날(최신) 판으로 대체하지 않고 쓰기 전에 멈춘다."""
    root = tmp_path / "model"
    _write_model_run(root, D24_ISO, MB_D24, {SCOPE: _scope_rows(D24_ISO, 100.0)})
    if case == "gate_failed":
        _write_model_run(root, D23_ISO, "m_20260924T050000_000000Z",
                         {SCOPE: _scope_rows(D23_ISO)}, status="gate_failed", latest=False)
    target = tmp_path / "quant.db"
    old = _old_v3_rows(D23_ISO, ("005930",), 48, 1.0)
    _seed(target, "score_history", old)
    with pytest.raises(CompatError, match="모델 판"):
        _run(roots, target, tables=["score_history"], model_root=root)
    assert _rows(target, "SELECT * FROM score_history") == old


def test_score_excluded_spec_refuses(roots, tmp_path: Path) -> None:
    """비교 모델(v2)이 그날 판에서 빠졌으면(N-11 격리) 그 표는 쓰지 않고 멈춘다."""
    root = tmp_path / "model"
    _write_model_run(root, D23_ISO, MB_D23, {SCOPE: _scope_rows(D23_ISO),
                                             V2: _v2_rows(D23_ISO)}, excluded=(V2,))
    target = tmp_path / "quant.db"
    old = _old_v3_rows(D23_ISO, ("005930",), 21, 50.0)
    _seed(target, "score_history_v2", old)
    with pytest.raises(CompatError, match="v2_percentrank@1.0"):
        _run(roots, target, tables=["score_history_v2"], model_root=root)
    assert _rows(target, "SELECT * FROM score_history_v2") == old


def test_score_rows_of_other_date_refuse(roots, tmp_path: Path) -> None:
    """판 날짜(D)와 다른 score_date 행이 있으면 D 를 지우기 전에 멈춘다."""
    root = tmp_path / "model"
    _write_model_run(root, D23_ISO, MB_D23, {SCOPE: _scope_rows("2026-09-22")})
    target = tmp_path / "quant.db"
    old = _old_v3_rows(D23_ISO, ("005930",), 48, 1.0)
    _seed(target, "score_history", old)
    with pytest.raises(CompatError, match="score_date"):
        _run(roots, target, tables=["score_history"], model_root=root)
    assert _rows(target, "SELECT * FROM score_history") == old


def test_score_empty_run_refuses_without_deleting(roots, tmp_path: Path) -> None:
    """0행 판은 CompatEmptyError — D 의 기존 행을 지운 채 끝나지 않는다."""
    root = tmp_path / "model"
    _write_model_run(root, D23_ISO, MB_D23, {SCOPE: []})
    target = tmp_path / "quant.db"
    old = _old_v3_rows(D23_ISO, ("005930",), 48, 1.0)
    _seed(target, "score_history", old)
    with pytest.raises(CompatEmptyError, match="score_history"):
        _run(roots, target, tables=["score_history"], model_root=root)
    assert _rows(target, "SELECT * FROM score_history") == old


@pytest.mark.parametrize(("table", "spec", "col"), [
    ("score_history", SCOPE, "composite_score"),
    ("score_history", SCOPE, "stock_code"),
    ("score_history_v2", V2, "total_score"),
])
def test_score_required_null_refuses_before_delete(roots, tmp_path: Path, table: str, spec: str,
                                                   col: str) -> None:
    """리뷰 MINOR-1 — v3 NOT NULL 열이 빈 행이 있으면 D 를 지우기 전에 멈춘다.

    전에는 그 행만 건너뛰고(skip) 나머지를 지운 자리에 넣은 뒤, 커밋 후 비율 검사에서 예외가 나
    D 가 일부 행만 남은 채로 끝났다.
    """
    root = tmp_path / "model"
    rows = _scope_rows(D23_ISO) if spec == SCOPE else _v2_rows(D23_ISO)
    rows[0][col] = None
    _write_model_run(root, D23_ISO, MB_D23, {spec: rows})
    target = tmp_path / "quant.db"
    old = _old_v3_rows(D23_ISO, ("005930", "999990"), 48 if spec == SCOPE else 21, 1.0)
    _seed(target, table, old)
    with pytest.raises(CompatError, match=col):
        _run(roots, target, tables=[table], model_root=root)
    assert sorted(_rows(target, f"SELECT * FROM {table}")) == sorted(old)


def test_score_duplicate_code_refuses_before_delete(roots, tmp_path: Path) -> None:
    """리뷰 MINOR-1 — 같은 종목이 두 번 오면 PK 덮어쓰기로 한 행이 조용히 사라진다.

    지우기 전에 멈춘다.
    """
    root = tmp_path / "model"
    rows = _scope_rows(D23_ISO)
    rows[1]["stock_code"] = rows[0]["stock_code"]
    _write_model_run(root, D23_ISO, MB_D23, {SCOPE: rows})
    target = tmp_path / "quant.db"
    old = _old_v3_rows(D23_ISO, ("005930", "999990"), 48, 1.0)
    _seed(target, "score_history", old)
    with pytest.raises(CompatError, match="중복"):
        _run(roots, target, tables=["score_history"], model_root=root)
    assert sorted(_rows(target, "SELECT * FROM score_history")) == sorted(old)


def test_score_tables_all_checked_before_any_write(roots, tmp_path: Path) -> None:
    """리뷰 MINOR-2 — 두 점수 표를 함께 내보낼 때 v2 가 깨졌으면 score_history 도 쓰지 않는다."""
    root = tmp_path / "model"
    v2 = _v2_rows(D23_ISO)
    v2[2]["total_score"] = None
    _write_model_run(root, D23_ISO, MB_D23, {SCOPE: _scope_rows(D23_ISO), V2: v2})
    target = tmp_path / "quant.db"
    old_v3 = _old_v3_rows(D23_ISO, ("005930", "999990"), 48, 1.0)
    old_v2 = _old_v3_rows(D23_ISO, ("005930", "999990"), 21, 50.0)
    _seed(target, "score_history", old_v3)
    _seed(target, "score_history_v2", old_v2)
    with pytest.raises(CompatError, match="total_score"):
        _run(roots, target, tables=["score_history", "score_history_v2"], model_root=root)
    assert sorted(_rows(target, "SELECT * FROM score_history")) == sorted(old_v3)
    assert sorted(_rows(target, "SELECT * FROM score_history_v2")) == sorted(old_v2)
    assert _rows(target, "SELECT status, failed_table FROM _compat_meta") == [
        ("failed", "score_history_v2")]


def test_score_meta_records_spec_build_and_basis(roots, model_root, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["score_history", "score_history_v2"],
               model_root=model_root)
    assert res.model_builds == {SCOPE: MB_D23, V2: MB_D23}
    row = _rows(target, "SELECT date, basis, model_builds, tables, status FROM _compat_meta")[0]
    assert row[0] == D23_ISO and row[1] == "morning" and row[4] == "ok"
    assert json.loads(row[2]) == {SCOPE: MB_D23, V2: MB_D23}
    tables = json.loads(row[3])
    assert tables["score_history"]["sources"] == {SCOPE: MB_D23}
    assert tables["score_history_v2"]["sources"] == {V2: MB_D23}


def test_score_tables_require_model_root(roots, tmp_path: Path) -> None:
    with pytest.raises(CompatError, match="--model-root"):
        _run(roots, tmp_path / "quant.db", tables=["score_history_v2"])


def test_score_tables_from_real_model_build(roots, tmp_path: Path) -> None:
    """실제 `model.build.build` 가 쓴 판(40종목 합성 fi)을 그대로 읽는다 — 파일 계약 대조.

    `_runs`·`v=<build_id>/scores.parquet` 규약을 손으로 흉내 낸 픽스처만으로는 빌더와 어긋나도
    모른다. v3 표 행 = 그 판 scores.parquet 행(NULL 열만 비움)이어야 한다.
    """
    import duckdb
    from model import build as mbuild
    from model.contracts import FI_TABLES, V2_SCORE_COLUMNS, V3_SCORE_COLUMNS
    from test_model_v4_rank import Board, _full

    b = Board()
    for i in range(40):
        _full(b, f"{100000 + i:06d}", i, sector=("G10", "G20", "G30")[i % 3])
    for r in b.tables["fi_prices"]:
        r["close"] = round(float(r["close"]))                  # type: ignore[arg-type]
    fi, fi_root, fi_bid = b.fi(), tmp_path / "fi", "m_20260929T000500_000000Z"
    for name, t in FI_TABLES.items():
        out = fi_root / name / f"v={fi_bid}" / "part0.parquet"
        mbuild.write_parquet(list(fi.tables.get(name, ())),
                             {c.name: c.dtype for c in t.columns}, out)
        (out.parent / "_meta.json").write_text(json.dumps(
            {"table": name, "build_id": fi_bid, "basis": "morning", "date": "2026-09-28"}))
    (fi_root / "latest_morning.json").write_text(json.dumps(
        {"layer": "factor_inputs", "status": "ok", "build_id": fi_bid, "date": "2026-09-28",
         "basis": "morning"}))
    model = tmp_path / "model"
    res = mbuild.build("20260928", "morning", model, fi_root, specs=[SCOPE, V2], primary=SCOPE,
                       min_prices_on_d=10, min_ranked=10)
    assert res.ok and not res.excluded, res.specs

    target = tmp_path / "quant.db"
    got = _run(roots, target, date="20260928", tables=["score_history", "score_history_v2"],
               model_root=model)
    assert got.model_builds == {SCOPE: res.build_id, V2: res.build_id}
    for table, spec, cols, null in (("score_history", SCOPE, V3_SCORE_COLUMNS, SCOPE_NULL_COLS),
                                    ("score_history_v2", V2, V2_SCORE_COLUMNS, ())):
        path = model / spec / f"v={res.build_id}" / "scores.parquet"
        con = duckdb.connect()
        cur = con.execute(f"SELECT * FROM read_parquet('{path}')")
        names = [d[0] for d in cur.description]
        src = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
        con.close()
        assert len(src) == res.specs[spec]["n_scores"] > 0
        assert sorted(_rows(target, f"SELECT {', '.join(cols)} FROM {table}")) == \
            _v3_tuples(src, cols, null)
