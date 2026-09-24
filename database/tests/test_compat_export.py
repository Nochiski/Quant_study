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
import sqlite3
from pathlib import Path

import pytest
from compat import CompatEmptyError, CompatError, CompatSchemaError, export
from compat.__main__ import main as cli_main
from conftest import _make_stage_tree

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


def _price_rows(*, bad_open: bool = False) -> list[dict]:
    rows: list[dict] = []
    for i, ticker in enumerate(REAL):
        for j, d in enumerate(SESSIONS):
            close = 70_000 + i * 1000 + j * 100
            rows.append(_price_row(ticker, d, close, value=VALUE_KRW + j,
                                   mktcap=(MKTCAP_KRW if i == 0 else MKTCAP_KRW_2) + j))
    if bad_open:
        rows[0]["open"] = None          # R7 — v3 NOT NULL 을 못 채우는 krx 행
    # 저녁 잠정 T 행 — KRX 기본정보가 없어 OHL·거래대금·시총이 전부 NULL (GAP-1/D-8)
    rows.append(_price_row("000270", D23, 55_000, basis="evening", ohl=False,
                           value=None, mktcap=None))
    return rows


def _adj_rows(skip_last: bool = False) -> list[dict]:
    """`price_adj_daily` 실물 15열. `skip_last` 면 마지막 krx 행의 조정가가 없다(R9)."""
    src = [r for r in _price_rows() if r["basis"] == "krx"]
    if skip_last:
        src = src[:-1]
    out: list[dict] = []
    for r in src:
        close = float(r["close"])
        out.append({
            "ticker": r["ticker"], "date": r["date"], "adj_open": close * 0.9,
            "adj_high": close * 0.9, "adj_low": close * 0.9, "adj_close": close * 0.9,
            "adj_volume_shr": 1_000_000.0, "cum_price_factor": 1.0, "cum_share_factor": 0.9,
            "n_factors_applied": 1, "n_unadjusted_events": 0, "available_date": r["date"],
            "available_basis": "derived", "basis": "krx", "corp_action_pending": False})
    return out


def _flow_rows() -> list[dict]:
    """`flow_daily` 실물 23열."""
    rows: list[dict] = []
    for i, ticker in enumerate(REAL):
        for j, d in enumerate(SESSIONS):
            row: dict = {"date": d, "ticker": ticker, "src": "kiwoom"}
            for k, col in enumerate(_FLOW_SRC):
                row[col] = FRGN_KRW + (i + j + k) * 1_000_000
            row.update(foreign_wght_pct=50.0, foreign_limit_exh_pct=50.0,
                       foreign_poss_shr=1_000, pension_net_buy_kiwoom_krw=1_000_000,
                       fill_kind="measured", available_date=d, available_basis="default")
            rows.append(row)
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


def _sec_row(ticker: str, name: str, list_date: dt.date, delist: dt.date | None = None) -> dict:
    """`security` 실물 13열."""
    return {"ticker": ticker, "corp_code": "0" * 8, "isin": "KR" + ticker * 2,
            "name_current": name, "sec_type": "common", "list_date": list_date,
            "list_date_basis": "measured", "delist_date_krx": delist,
            "delist_date_kis": None, "delist_conflict": False, "delist_date": delist,
            "delist_date_basis": "derived" if delist else "unknown"}


def _security_rows(fillers: list[str]) -> list[dict]:
    rows = [_sec_row("005930", "삼성전자", dt.date(1975, 6, 11)),
            _sec_row("000660", "SK하이닉스", dt.date(1996, 12, 26)),
            _sec_row(SPAC, "스팩1호", dt.date(2024, 3, 1)),
            _sec_row("900000", "폐지종목", dt.date(2010, 1, 4), dt.date(2026, 5, 1))]
    rows += [_sec_row(t, f"제외{t}", dt.date(2020, 1, 2)) for t, _, _ in EXCLUDED]
    rows += [_sec_row(t, f"종목{t}", dt.date(2015, 1, 2)) for t in fillers]
    return rows


def _sector_rows() -> list[dict]:
    """`sector_snapshot` 실물 11열."""
    return [{"ticker": t, "snapshot_date": dt.date(2026, 9, 19), "wics_l1_cd": "G45",
             "wics_l1_nm": "IT", "wics_l2_cd": "G4530", "wics_l2_nm": "반도체",
             "float_shares_shr": 1_000, "float_mktcap_krw": 1_000_000.0,
             "wgt_in_l2_pct": 10.0, "available_date": dt.date(2026, 9, 19),
             "available_basis": "convention"} for t in REAL]


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
def _make_roots(base: Path, *, fillers: list[str] | None = None,
                price_rows: list[dict] | None = None, adj_rows: list[dict] | None = None,
                eq_build: str = EQ_BUILD, adj_build: str | None = None) -> tuple[Path, Path]:
    eq, st = base / "eq", base / "st"
    f = _filler_tickers(N_FILLER) if fillers is None else fillers
    equity = {
        "price_daily": (price_rows if price_rows is not None else _price_rows(), eq_build),
        "price_adj_daily": (adj_rows if adj_rows is not None else _adj_rows(),
                            adj_build or eq_build),
        "universe_daily": (_universe_rows(f), eq_build),
        "security": (_security_rows(f), eq_build),
        "sector_snapshot": (_sector_rows(), eq_build),
        "flow_daily": (_flow_rows(), eq_build),
    }
    for table, (rows, build) in equity.items():
        _make_stage_tree(eq, table, rows, build_id=build)
    for table, rows in (("stg_consensus_matrix", _matrix_rows()),
                        ("stg_consensus_annual", _annual_rows()),
                        ("stg_fin_wise", _fin_wise_rows())):
        _make_stage_tree(st, table, rows, build_id=ST_BUILD)
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
    assert got == [("005930", "2026-09-23", 69_700, 70_900, 69_300, 70_200, 1_000_000,
                    1234, pytest.approx(63_180.0))]
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
        ("000660", "SK하이닉스", "KOSPI", "IT", 98_765, "1996-12-26", 1, None),
        ("005930", "삼성전자", "KOSPI", "IT", 123_457, "1975-06-11", 1, None),
    ]


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


def test_investor_flows_in_million_krw(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    res = _run(roots, target, tables=["investor_detail_flows"])
    assert res.tables["investor_detail_flows"].n_rows == 6     # 미측정 셀 1개는 빠진다
    got = _rows(target, "SELECT individual, foreign_investor, institution_total, "
                        "etc_corporation FROM investor_detail_flows "
                        "WHERE stock_code='005930' AND trade_date='2026-09-21'")
    # ind = -3,456,000,000원 → -3,456 백만원. 주체마다 +1,000,000원(=+1 백만원)씩 어긋난다.
    assert got == [(-3456, -3455, -3454, -3445)]


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
    """WISE cF3002/cF4002 에 재료가 없는 열은 0 이 아니라 NULL 이다(결측은 결측)."""
    from compat.mappings import BY_TABLE
    target = tmp_path / "quant.db"
    _run(roots, target, tables=["financial_summary"])
    nulls = BY_TABLE["financial_summary"].null_columns
    assert set(nulls) == {"roe", "roa", "debt_ratio", "fcf", "capex", "op_margin", "ni_margin",
                          "yoy", "total_assets"}
    cols = ", ".join(nulls)
    for row in _rows(target, f"SELECT {cols} FROM financial_summary"):
        assert all(v is None for v in row)


# ── R1 — accode 충돌(금융업)은 계정명으로 가른다. 실물 절단본으로 본다 ───────
@pytest.mark.skipif(not FIN_SLICE, reason="stage_slice stg_fin_wise 픽스처 없음")
def test_financial_summary_acc_nm_whitelist_on_real_slice() -> None:
    """003540(대신증권)의 cF3002 200000 은 '순이자이익' 이다 — revenue 로 새면 안 된다."""
    import duckdb
    from compat.mappings import BY_TABLE
    globs = ", ".join(repr(str(p)) for p in FIN_SLICE)
    expr = f"read_parquet([{globs}], hive_partitioning=true, union_by_name=true)"
    sql = BY_TABLE["financial_summary"].sql.format(
        stg_fin_wise=expr, consensus_asof="2026-09-03")
    con = duckdb.connect()
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in (cur.description or [])]
        rows = cur.fetchall()
    finally:
        con.close()
    assert cols == list(BY_TABLE["financial_summary"].columns)
    i_code, i_rev = cols.index("stock_code"), cols.index("revenue")
    by_code: dict[str, set] = {}
    for r in rows:
        by_code.setdefault(r[i_code], set()).add(r[i_rev])
    assert by_code["003540"] == {None}, "금융업 '순이자이익' 이 revenue 로 새고 있다"
    assert any(v is not None for v in by_code["005930"])


# ── (b) 멱등 ─────────────────────────────────────────────────────────────────
def test_second_run_is_idempotent(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    first = _run(roots, target)
    before = {t: _rows(target, f"SELECT * FROM {t} ORDER BY 1, 2") for t in first.tables}
    second = _run(roots, target)
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
def test_basis_mismatch_refuses(roots, tmp_path: Path) -> None:
    with pytest.raises(CompatError, match="--basis"):
        _run(roots, tmp_path / "quant.db", basis="evening", tables=["daily_prices"])


# ── R9 — 가격 두 표의 판 체인 · adj_close 결측 ───────────────────────────────
def test_price_build_chain_gap_refuses(tmp_path: Path) -> None:
    far = _make_roots(tmp_path / "far", adj_build="m_20260924T235911_000000Z")
    with pytest.raises(CompatError, match="빌드 시각 차"):
        _run(far, tmp_path / "quant.db", tables=["daily_prices"])


def test_price_build_chain_basis_mismatch_refuses(tmp_path: Path) -> None:
    # 수동 재빌드(`b_`)는 `--basis` 검사(R5)를 통과하지만 가격 두 표의 판 축은 어긋난다.
    mixed = _make_roots(tmp_path / "mix", adj_build="b_20260924T035911_573147Z")
    with pytest.raises(CompatError, match="판 축이 다르다"):
        _run(mixed, tmp_path / "quant.db", tables=["daily_prices"])


def test_adj_close_gap_refuses_and_records_failure(tmp_path: Path) -> None:
    """R9 + R6 — 조정가가 빠지면 멈추고, 실패도 `_compat_meta` 에 남는다."""
    gap = _make_roots(tmp_path / "gap", adj_rows=_adj_rows(skip_last=True))
    target = tmp_path / "quant.db"
    with pytest.raises(CompatError, match="adj_close 결측"):
        _run(gap, target, tables=["daily_prices"])
    assert _rows(target, "SELECT status, failed_table FROM _compat_meta") == [
        ("failed", "daily_prices")]


# ── R7 — 건너뛴 행 비율 ──────────────────────────────────────────────────────
def test_skip_ratio_over_limit_refuses(tmp_path: Path) -> None:
    bad = _make_roots(tmp_path / "bad", price_rows=_price_rows(bad_open=True))
    with pytest.raises(CompatError, match="건너뛴 행 비율"):
        _run(bad, tmp_path / "quant.db", tables=["daily_prices"])


# ── R10 — 증분 가드 ──────────────────────────────────────────────────────────
def test_incremental_on_fresh_target_refuses(roots, tmp_path: Path) -> None:
    with pytest.raises(CompatError, match="daily_prices 가 없다"):
        _run(roots, tmp_path / "quant.db", tables=["daily_prices"], full=False)


def test_incremental_on_shallow_target_refuses(roots, tmp_path: Path) -> None:
    target = tmp_path / "quant.db"
    _run(roots, target, tables=["daily_prices"])
    with pytest.raises(CompatError, match="세션 중앙값"):
        _run(roots, target, tables=["daily_prices"], full=False)


# ── R3 — --window-days 는 --full 전용 ────────────────────────────────────────
def test_window_days_without_full_refuses(roots, tmp_path: Path) -> None:
    with pytest.raises(CompatError, match="--window-days"):
        _run(roots, tmp_path / "quant.db", tables=["daily_prices"], full=False,
             window_days=550)


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
                           "sector_snapshot", "flow_daily")},
        "stage_builds": {t: ST_BUILD for t in
                         ("stg_consensus_matrix", "stg_consensus_annual", "stg_fin_wise")},
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


def test_cli_rejects_unmapped_score_tables(roots, tmp_path: Path) -> None:
    """score_history 는 T2.7 이 채운다 — 지금 요청하면 조용히 건너뛰지 않고 rc 2 다."""
    rc = cli_main(["export", "--date", AS_OF, "--basis", "morning",
                   "--equity-root", str(roots[0]), "--stage-root", str(roots[1]),
                   "--target", str(tmp_path / "quant.db"), "--tables", "score_history"])
    assert rc == 2
