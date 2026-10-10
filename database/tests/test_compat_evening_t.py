"""compat 장 마감 판 T 행 — v3 소비자가 T 저녁에 읽을 가격·수급 행의 원천
(컷오버 QL-D · N-42 Q3 · T-2).

equity 판은 직전 거래일 D' 의 연구 판(`m_`, 또는 그날 저녁 판 `e_`)이고 오늘 T 의 행이 없다.
T 행은 원장 두 개에서 만든다 — ① `postclose.db`(15:41~16:00, `price_valid='1'`) ② 그 밖은 키움
원장 `kiwoom.db` 의 T 행(21:05 저녁 수집). 원장은 실물 코드(`daily.postclose.connect`·
`insert_first`, `daily.kw_daily.ensure_table`·`insert_rows`)로 만든 소형 sqlite 이고, equity 판은
`make_stage_tree` 규약이다.

날짜: T = 2026-09-23(수) · D' = 2026-09-22(화) · 2026-09-21(월). 판정 달력은 주말 + 09-24·09-25
휴장.

종목(D' `universe_daily` — 보통주·스팩·우선주)
  FILL  보통주 120 — postclose 정상 행(R9 결측 비율을 실물 규모로 맞추는 몫)
  A     005930 보통주 — postclose 정상 행. 저녁 원장 행은 값이 달라 쓰이면 안 된다
  B     000660 보통주 — postclose 정상 행이지만 키움 기준가(종가 − 전일대비) ≠ D' KRX 종가(기업행위)
  C     035720 보통주 — postclose `price_valid='0'`(16:00 뒤 응답) → 저녁 원장
  D     123450 스팩   — postclose 행 없음(16:00 컷오프) → 저녁 원장
  E     068270 보통주 — 어느 원장에도 T 행 없음 → 행 없음(기록)
  X     005935 우선주 — 두 원장에 행이 있어도 v3 종목 집합 밖
  N     999990 —       T 당일 신규 상장(D' 유니버스에 없음) → 빠진다
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest
from compat import CompatEmptyError, CompatError, export
from compat.__main__ import main as cli_main
from conftest import _make_stage_tree
from daily import kw_daily, postclose
from stage.rules_kiwoom import STG_FLOW_DAILY_KIWOOM, STG_FLOW_POSTCLOSE_KIWOOM
from test_compat_export import (
    _adj_rows,
    _flow_row,
    _master_rows,
    _price_row,
    _sec_row,
    _uni_row,
)

SRC = Path(__file__).resolve().parents[1] / "src"

T = "20260923"
T_ISO = "2026-09-23"
D21, D22, D23 = dt.date(2026, 9, 21), dt.date(2026, 9, 22), dt.date(2026, 9, 23)

# 판 id — D' 아침 확정판(T 아침 08:10 KST 빌드) · T 저녁 판 · T+1 아침 확정판(T 의 KRX 행)
DP_BUILD = "m_20260922T231000_000000Z"
EV_BUILD = "e_20260923T122000_000000Z"
TM_BUILD = "m_20260923T231000_000000Z"

A, B, C, D, E = "005930", "000660", "035720", "123450", "068270"
X, N = "005935", "999990"
FILL = tuple(f"2{i:05d}" for i in range(120))
UNIVERSE = (*FILL, A, B, C, D, E)                    # v3 종목 집합(X 는 유니버스엔 있지만 밖)
MARKET = {A: ("KOSPI", "common"), B: ("KOSPI", "common"), C: ("KOSDAQ", "common"),
          D: ("KOSDAQ", "spac"), E: ("KOSDAQ", "common"), X: ("KOSPI", "preferred")}
# D' KRX 종가 — 종목별로 다르게
DP_CLOSE = {A: 70_000, B: 100_000, C: 30_000, D: 2_100, E: 8_000, X: 60_000}
ADJ = 0.9                                            # 픽스처 조정가 = 종가 × 0.9(전방 누적계수)


def _dp_close(t: str) -> int:
    return DP_CLOSE.get(t, 10_000)


# ── equity 판 ────────────────────────────────────────────────────────────────
def _equity(base: Path, build: str, sessions: tuple[dt.date, ...], *,
            evening_t: bool = False, krx_t_skip: tuple[str, ...] = ()) -> Path:
    """price_daily·price_adj_daily·universe_daily·flow_daily 한 판. 반환은 equity 루트."""
    tickers = (*UNIVERSE, X)
    prices, unis, flows = [], [], []
    for d in sessions:
        for t in tickers:
            if d == D23 and t in krx_t_skip:
                continue
            close = _dp_close(t) - (100 if d == D21 else 0) - (300 if d == D23 else 0)
            prices.append(_price_row(t, d, close, value=1_000_000_000, mktcap=10**12))
            market, sec = MARKET.get(t, ("KOSPI", "common"))
            unis.append(_uni_row(t, d, market, sec))
            flows.append(_flow_row(t, d))
    adj = _adj_rows(price_rows=prices)
    if evening_t:
        # 그날 저녁 판의 잠정 T 행(basis='evening') — compat 은 쓰지 않고 원장에서 다시 만든다
        prices.append(_price_row(A, D23, 1, basis="evening", ohl=False, value=None, mktcap=None))
    for table, rows in (("price_daily", prices), ("price_adj_daily", adj),
                        ("universe_daily", unis), ("flow_daily", flows)):
        _make_stage_tree(base, table, rows, build_id=build)
    return base / "stage"


def _stage_root(base: Path) -> Path:
    """가격·수급 두 표는 stage 를 읽지 않지만 export 인자는 받는다."""
    (base / "stage").mkdir(parents=True, exist_ok=True)
    return base / "stage"


# ── 원장 ─────────────────────────────────────────────────────────────────────
def _flows(ind: str, frgn: str) -> dict[str, str]:
    row = {k: str(10 + i) for i, k in enumerate(kw_daily.FLOW_KEYS)}
    row.update(ind_invsr=ind, frgnr_invsr=frgn)
    return row


def _ledger_row(day: str, cur: str, pred: str, vol: str, ind: str = "-4321",
                frgn: str = "+12") -> dict[str, str]:
    return {"dt": day, "cur_prc": cur, "pred_pre": pred, "acc_trde_prica": vol,
            **_flows(ind, frgn)}


def _postclose_rows() -> dict[str, tuple[dict[str, str], bool]]:
    """장 마감 원장 기본 행 — (종목 → (행, price_valid))."""
    rows = {t: (_ledger_row(T, "+10200", "+200", "500000"), True) for t in FILL}
    rows.update({
        A: (_ledger_row(T, "-69500", "-500", "1000000"), True),
        # 기준가 51,000 − 1,000 = 50,000 ≠ D' 종가 100,000 — 2:1 분할 같은 기업행위
        B: (_ledger_row(T, "+51000", "+1000", "300000"), True),
        C: (_ledger_row(T, "+31000", "+1000", "70000"), False),   # 16:00 뒤 — 가격 무효
        X: (_ledger_row(T, "60000", "0", "100"), True),
        N: (_ledger_row(T, "+5000", "0", "100"), True),
    })
    # 전 거래일 행 — T 가 아니라 쓰이면 안 된다
    rows["__d22__"] = (_ledger_row("20260922", "70000", "0", "1"), True)
    return rows


def _postclose_db(path: Path, *, rows: dict[str, tuple[dict[str, str], bool]] | None = None
                  ) -> Path:
    """`daily.postclose` 원장(실물 `connect`·`insert_first`)."""
    rows = _postclose_rows() if rows is None else rows
    con = postclose.connect(path)
    try:
        for t, (r, valid) in rows.items():
            ticker = A if t == "__d22__" else t
            postclose.insert_first(con, list(postclose.COLS), ticker, [r],
                                   collected_at="2026-09-23T06:45:00",
                                   fetched_at="2026-09-23T06:41:00", price_valid=valid)
    finally:
        con.close()
    return path


def _kiwoom_db(path: Path, *, with_t: bool = True) -> Path:
    """키움 원장(21:05 저녁 수집이 쓰는 같은 표)."""
    con = sqlite3.connect(path)
    try:
        table = kw_daily.TRS["ka10060"].table
        cols = list(postclose.COLS)
        kw_daily.ensure_table(con, table, cols)
        rows: dict[str, list[dict[str, str]]] = {
            A: [_ledger_row("20260922", "70000", "+100", "9")],
            C: [_ledger_row("20260922", "30000", "0", "9")],
        }
        if with_t:
            rows[A].append(_ledger_row(T, "-69800", "-200", "1200000", ind="-1"))   # 애프터마켓
            rows[C].append(_ledger_row(T, "+30100", "+100", "80000", ind="-777", frgn="555"))
            rows[D] = [_ledger_row(T, "2100", "0", "4000", ind="+5", frgn="-6")]
            rows[X] = [_ledger_row(T, "60100", "+100", "100")]
            rows[N] = [_ledger_row(T, "5100", "+100", "100")]
            rows.update({t: [_ledger_row(T, "+10300", "+300", "600000")] for t in FILL})
        for t, rs in rows.items():
            kw_daily.insert_rows(con, table, cols, t, rs, "ka10060", "2026-09-23T12:05:00")
        con.commit()
    finally:
        con.close()
    return path


def _calendar(base: Path) -> Path:
    d = base / "calendar"
    d.mkdir(parents=True, exist_ok=True)
    days = (dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(365))
    hol = [x.strftime("%Y%m%d") for x in days if x.weekday() >= 5] + ["20260924", "20260925"]
    (d / "kis_holidays_2026.json").write_text(json.dumps({"year": 2026, "holidays": hol}),
                                              encoding="utf-8")
    return d


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Path]:
    """D' 아침 확정판 + 두 원장 + 달력."""
    return {"equity": _equity(tmp_path / "dp", DP_BUILD, (D21, D22)),
            "stage": _stage_root(tmp_path / "st"),
            "postclose": _postclose_db(tmp_path / "raw" / "postclose.db"),
            "kiwoom": _kiwoom_db(tmp_path / "raw" / "kiwoom.db"),
            "calendar": _calendar(tmp_path),
            "target": tmp_path / "quant.db"}


def _evening(env: dict[str, Path], **kw):
    kw.setdefault("tables", ["daily_prices", "investor_detail_flows"])
    kw.setdefault("full", True)
    return export(equity_root=kw.pop("equity_root", env["equity"]), stage_root=env["stage"],
                  date=kw.pop("date", T), basis="evening", target=env["target"],
                  postclose_db=kw.pop("postclose_db", env["postclose"]),
                  kiwoom_db=kw.pop("kiwoom_db", env["kiwoom"]),
                  calendar_dir=env["calendar"], **kw)


def _rows(target: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(str(target))
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _t_prices(target: Path) -> dict[str, tuple]:
    return {r[0]: r[1:] for r in _rows(
        target, "SELECT stock_code, open, high, low, close, volume, amount, adj_close "
                f"FROM daily_prices WHERE trade_date = '{T_ISO}'")}


# ── 원천 선택 ────────────────────────────────────────────────────────────────
def test_t_rows_take_postclose_first_then_evening_ledger(env) -> None:
    res = _evening(env)
    got = _t_prices(env["target"])
    # 대상 = D' 유니버스 ∩ v3 종목 집합. E 는 원장 행이 없고, X(우선주)·N(T 신규 상장)은 밖이다.
    assert set(got) == {*FILL, A, B, C, D}
    # A — postclose 정상 행(정규장 종가). 저녁 원장(−69,800)이 아니다.
    assert got[A][3:6] == (69_500, 1_000_000, 69_500)          # close · volume · amount(백만원)
    # D — postclose 행 없음 → 저녁 원장
    assert got[D][3:5] == (2_100, 4_000)
    info = res.tables["daily_prices"].t_rows
    assert info is not None
    assert (info["date"], info["d_prime"]) == (T_ISO, "2026-09-22")
    assert (info["postclose"], info["kiwoom_2105"], info["missing"]) == (len(FILL) + 2, 2, 1)
    assert info["kiwoom_2105_tickers"] == sorted([C, D])
    assert info["missing_tickers"] == [E]
    assert info["n_universe"] == len(UNIVERSE)
    # MINOR-3 — 행 없는 종목 비율은 기록만 한다(상한으로 막지 않는다)
    assert info["missing_ratio"] == pytest.approx(1 / len(UNIVERSE))


def test_price_valid_0_falls_back_to_evening_ledger(env) -> None:
    """PR-1 s4 — 16:00 뒤 받은 postclose 행(`price_valid='0'`)은 가격이 애프터마켓 값이라
    쓰지 않는다. 종목 하나의 T 행은 한 원천에서만 온다 — 수급도 저녁 원장 값이다."""
    _evening(env)
    assert _t_prices(env["target"])[C][3:5] == (30_100, 80_000)        # postclose 31,000 이 아니다
    assert _rows(env["target"], "SELECT individual, foreign_investor FROM investor_detail_flows "
                                f"WHERE stock_code = '{C}' AND trade_date = '{T_ISO}'") == [
        (-777, 555)]


def test_t_row_values_ohl_amount_and_adj_close(env) -> None:
    """v3 NOT NULL 시·고·저 = 종가(D2-9 (c)), 거래대금 = 종가 × 거래량(백만원),
    조정가 = T 종가 × D' 누적계수."""
    _evening(env)
    got = _t_prices(env["target"])
    o, h, low, close, volume, amount, adj = got[A]
    assert (o, h, low) == (close, close, close) == (69_500, 69_500, 69_500)
    assert (volume, amount) == (1_000_000, 69_500)
    assert adj == pytest.approx(69_500 * ADJ)
    # 저녁 원장 행도 같은 규칙 — 기준가 30,100 − 100 = D' 종가 30,000
    assert got[C][6] == pytest.approx(30_100 * ADJ)


def test_adj_close_is_null_when_base_price_differs_from_d_prime_close(env) -> None:
    """키움 기준가 ≠ D' KRX 종가 = T 에 기업행위(계수 미상) — 조정가를 지어내지 않는다(P1)."""
    res = _evening(env)
    assert _t_prices(env["target"])[B][3] == 51_000
    assert _t_prices(env["target"])[B][6] is None
    assert res.n_adj_close_null == 1


def test_t_flows_in_million_krw(env) -> None:
    """ka10060 수급 원문은 백만원이다(stage 규칙 ×1e6 → 원 → compat ÷1e6). 부호 '+' 도 읽는다."""
    res = _evening(env)
    got = _rows(env["target"], "SELECT individual, foreign_investor, institution_total "
                               "FROM investor_detail_flows "
                               f"WHERE stock_code = '{A}' AND trade_date = '{T_ISO}'")
    assert got == [(-4321, 12, 12)]                    # orgn = FLOW_KEYS[2] → '12'
    info = res.tables["investor_detail_flows"].t_rows
    assert info is not None and (info["postclose"], info["kiwoom_2105"], info["missing"]) == \
        (len(FILL) + 2, 2, 1)


def test_evening_ledger_rows_before_21h_are_missing_then_rerun_fills(env, tmp_path: Path) -> None:
    """16:30 무렵엔 21:05 원장이 아직 없다 — 대체 종목은 '행 없음'으로 남고, 21:05 뒤 다시 돌리면
    그 날짜 행을 갈아 끼워 채운다(중복 없음)."""
    early = _kiwoom_db(tmp_path / "raw" / "kiwoom_early.db", with_t=False)
    res = _evening(env, kiwoom_db=early)
    info = res.tables["daily_prices"].t_rows
    assert info is not None
    assert (info["kiwoom_2105"], info["missing_tickers"]) == (0, sorted([C, D, E]))
    assert C not in _t_prices(env["target"])
    _evening(env)
    got = _t_prices(env["target"])
    assert {C, D} <= set(got)
    assert _rows(env["target"], "SELECT count(*) FROM daily_prices "
                                f"WHERE trade_date = '{T_ISO}'") == [(len(FILL) + 4,)]


# ── 다른 날짜 · 다음 날 아침 교체 ────────────────────────────────────────────
def _seed_old(target: Path) -> None:
    """창 밖 옛 행(v3 가 쓴 것) — 어떤 실행도 건드리면 안 된다."""
    con = sqlite3.connect(str(target))
    try:
        con.execute("INSERT INTO daily_prices VALUES ('005930','2026-01-05',1,2,3,4,5,6,7.0)")
        con.execute("INSERT INTO investor_detail_flows (stock_code, trade_date, individual) "
                    "VALUES ('005930','2026-01-05',9)")
        con.commit()
    finally:
        con.close()


def _not_t(target: Path) -> dict[str, list[tuple]]:
    return {t: _rows(target, f"SELECT * FROM {t} WHERE trade_date <> '{T_ISO}' ORDER BY 1, 2")
            for t in ("daily_prices", "investor_detail_flows")}


def test_evening_leaves_other_dates_unchanged(env) -> None:
    # 전날 밤 상태: D' 판의 아침 반영 + 창 밖 옛 행
    export(equity_root=env["equity"], stage_root=env["stage"], date="20260922",
           basis="morning", target=env["target"], full=True,
           tables=["daily_prices", "investor_detail_flows"])
    _seed_old(env["target"])
    before = _not_t(env["target"])
    _evening(env)
    assert _not_t(env["target"]) == before


def test_next_morning_replaces_t_rows_with_krx(env, tmp_path: Path) -> None:
    """T+1 아침 KRX 확정 반영(`--basis morning --date T`) — T 행을 날짜 단위로 갈아 끼운다
    (QL-C 규칙). KRX 값으로 바뀌고, 아침 판에 KRX T 행이 없는 종목(D)의 저녁 행은 남지 않는다.
    다른 날짜는 그대로다."""
    _evening(env)
    _seed_old(env["target"])
    before = _not_t(env["target"])
    morning = _equity(tmp_path / "tm", TM_BUILD, (D21, D22, D23), krx_t_skip=(D,))
    export(equity_root=morning, stage_root=env["stage"], date=T, basis="morning",
           target=env["target"], full=True, tables=["daily_prices", "investor_detail_flows"])
    got = _t_prices(env["target"])
    assert set(got) == {*FILL, A, B, C, E}                   # D 의 저녁 행은 지워지고, E 는 KRX 행
    # KRX 확정 행 — 픽스처 OHL = 종가 −500/+700/−900, 거래대금 1,000 백만원, 조정가 = 종가 × 0.9
    assert got[A] == (69_200, 70_400, 68_800, 69_700, 1_000_000, 1_000, pytest.approx(69_700 * ADJ))
    flows = dict(_rows(env["target"], "SELECT stock_code, individual FROM investor_detail_flows "
                                      f"WHERE trade_date = '{T_ISO}'"))
    # 수급은 equity flow_daily T 행(픽스처 −3,456 백만원 — 저녁 원장 −4,321 이 아니다). 아침 판에 T
    # 행이 없는 D 의 저녁 수급 행도 날짜 단위 교체로 지워진다
    assert flows[A] == -3456 and D not in flows
    after = _not_t(env["target"])
    assert after == before


# ── 판·원장 가드 ─────────────────────────────────────────────────────────────
def test_evening_reads_the_dprime_morning_build_and_the_evening_build(env, tmp_path: Path) -> None:
    """장 마감 판(T-2)은 D' 아침 확정판(`m_`)을 읽는다. 그날 저녁 판(`e_`)도 받되 그 판의 잠정 T 행
    (basis='evening')은 쓰지 않고 원장에서 다시 만든다."""
    res = _evening(env)
    assert res.equity_builds["price_daily"] == DP_BUILD
    ev = _equity(tmp_path / "ev", EV_BUILD, (D21, D22), evening_t=True)
    target2 = tmp_path / "ev.db"
    res2 = export(equity_root=ev, stage_root=env["stage"], date=T, basis="evening",
                  target=target2, full=True, tables=["daily_prices"],
                  postclose_db=env["postclose"], kiwoom_db=env["kiwoom"],
                  calendar_dir=env["calendar"])
    assert res2.n_evening_rows_skipped == 1
    assert _t_prices(target2)[A][3] == 69_500


def test_evening_refuses_a_build_that_already_has_t(env, tmp_path: Path) -> None:
    """이음매 — T 의 KRX 행을 가진 판(다음 날 아침 판)을 저녁으로 내보내면 확정 행을 원장 값으로
    덮는다."""
    morning = _equity(tmp_path / "tm", TM_BUILD, (D21, D22, D23))
    with pytest.raises(CompatError, match="이음매"):
        _evening(env, equity_root=morning)
    assert not env["target"].exists() or _rows(
        env["target"], f"SELECT count(*) FROM daily_prices WHERE trade_date = '{T_ISO}'") == [(0,)]


def test_evening_refuses_a_build_that_stops_before_dprime(env, tmp_path: Path) -> None:
    stale = _equity(tmp_path / "old", DP_BUILD, (D21,))
    with pytest.raises(CompatError, match="이음매"):
        _evening(env, equity_root=stale)


def test_evening_refuses_without_ledgers(env) -> None:
    with pytest.raises(CompatError, match="--postclose-db"):
        _evening(env, postclose_db=None)
    with pytest.raises(CompatError, match="없다"):
        _evening(env, kiwoom_db=env["kiwoom"].with_name("nope.db"))
    bare = env["kiwoom"].with_name("bare.db")             # 파일은 있는데 TR 표가 없다
    sqlite3.connect(bare).close()
    with pytest.raises(CompatError, match="표가 없다"):
        _evening(env, kiwoom_db=bare)


def test_evening_refuses_non_trading_t(env) -> None:
    with pytest.raises(CompatError, match="거래일"):
        _evening(env, date="20260924")                      # 휴장


def test_evening_with_no_t_rows_refuses_before_writing(env, tmp_path: Path) -> None:
    """T 행이 하나도 없으면(두 원장 모두 비었다) 07:00 브리핑이 D' 를 T 로 읽는다 — 쓰지 않고
    멈춘다."""
    empty_pc = _postclose_db(tmp_path / "raw2" / "postclose.db", rows={})
    early = _kiwoom_db(tmp_path / "raw2" / "kiwoom.db", with_t=False)
    with pytest.raises(CompatEmptyError, match="T 행"):
        _evening(env, postclose_db=empty_pc, kiwoom_db=early)
    assert _rows(env["target"], "SELECT count(*) FROM daily_prices") == [(0,)]


def test_morning_basis_still_refuses_an_evening_build(env, tmp_path: Path) -> None:
    """R5 은 아침 쪽에서 그대로다 — 저녁 판을 아침 확정으로 내보내지 않는다."""
    ev = _equity(tmp_path / "ev", EV_BUILD, (D21, D22), evening_t=True)
    with pytest.raises(CompatError, match="--basis"):
        export(equity_root=ev, stage_root=env["stage"], date="20260922", basis="morning",
               target=env["target"], full=True, tables=["daily_prices"])


# ── 메타 · CLI ───────────────────────────────────────────────────────────────
def test_t_sources_are_recorded_in_compat_meta(env) -> None:
    _evening(env)
    tables = json.loads(_rows(env["target"], "SELECT tables FROM _compat_meta")[0][0])
    info = tables["daily_prices"]["t_rows"]
    assert (info["postclose"], info["kiwoom_2105"], info["missing"]) == (len(FILL) + 2, 2, 1)
    assert info["kiwoom_2105_tickers"] == sorted([C, D]) and info["missing_tickers"] == [E]


def test_cli_passes_ledgers_and_prints_t_sources(env, capsys) -> None:
    rc = cli_main(["export", "--date", T, "--basis", "evening", "--full",
                   "--equity-root", str(env["equity"]), "--stage-root", str(env["stage"]),
                   "--target", str(env["target"]), "--tables", "daily_prices",
                   "--postclose-db", str(env["postclose"]), "--kiwoom-db", str(env["kiwoom"]),
                   "--calendar-dir", str(env["calendar"]), "--allow-older"])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"T={T_ISO}" in out and f"postclose={len(FILL) + 2}" in out and "kiwoom_2105=2" in out


# ── 리뷰 수정(MAJOR-1 · MINOR-1~5 · NIT) ────────────────────────────────────
def _t_count(target: Path) -> list[tuple]:
    return _rows(target, f"SELECT count(*) FROM daily_prices WHERE trade_date = '{T_ISO}'")


def test_morning_without_t_rows_refuses_and_keeps_evening_t_rows(env) -> None:
    """MAJOR-1 재현 — 저녁 반영 뒤 D' 판(T 를 못 담은 판)으로 아침 `--date T` 를 돌리면 날짜 단위
    교체가 T 행을 지우고 0행을 넣는다. 지우기 전에 멈추고 T 행은 그대로 남아야 한다(QL-C
    `_score_rows` 규칙)."""
    _evening(env)
    before = _rows(env["target"], f"SELECT * FROM daily_prices WHERE trade_date = '{T_ISO}' "
                                  "ORDER BY 1")
    with pytest.raises(CompatEmptyError, match="행이 0"):
        export(equity_root=env["equity"], stage_root=env["stage"], date=T, basis="morning",
               target=env["target"], full=True,
               tables=["daily_prices", "investor_detail_flows"])
    assert _rows(env["target"], f"SELECT * FROM daily_prices WHERE trade_date = '{T_ISO}' "
                                "ORDER BY 1") == before
    assert _rows(env["target"], "SELECT count(*) FROM investor_detail_flows "
                                f"WHERE trade_date = '{T_ISO}'") == [(len(FILL) + 4,)]


def test_evening_does_not_overwrite_a_morning_reflect(env, tmp_path: Path) -> None:
    """MINOR-1 — 대상에 같은 날 아침 확정 ok 기록이 있으면 장 마감 판은 멈춘다(T-35 순서).
    재생은 `--allow-older` 로 돌린다."""
    morning = _equity(tmp_path / "tm", TM_BUILD, (D21, D22, D23))
    export(equity_root=morning, stage_root=env["stage"], date=T, basis="morning",
           target=env["target"], full=True, tables=["daily_prices", "investor_detail_flows"])
    krx = _t_prices(env["target"])
    with pytest.raises(CompatError, match="--allow-older"):
        _evening(env)
    assert _t_prices(env["target"]) == krx                   # KRX 확정 행 그대로
    _evening(env, allow_older=True)
    assert _t_prices(env["target"])[A][3] == 69_500


def test_late_evening_after_next_day_evening_is_refused(env) -> None:
    """재리뷰 MINOR-1 재현 — 순서 판정은 QL-F T-35(`v3_post._newer`)와 한 곳이다. 대상에 (T+1, 장
    마감, ok) 기록만 있어도 늦게 온 T 장 마감은 멈춘다(같은 날 아침만 보면 이 경우를 놓친다)."""
    from compat.quant_db import META_DDL
    con = sqlite3.connect(env["target"])
    try:
        con.execute(META_DDL)
        con.execute("INSERT INTO _compat_meta (exported_at, date, basis, equity_builds, "
                    "stage_builds, tables, \"window\", consensus_asof, status) VALUES "
                    "('2026-09-28T07:00:00', '2026-09-28', 'evening', '{}', '{}', '{}', '{}', "
                    "'2026-09-28', 'ok')")
        con.commit()
    finally:
        con.close()
    with pytest.raises(CompatError, match="2026-09-28"):
        _evening(env)
    assert _rows(env["target"], "SELECT count(*) FROM _compat_meta") == [(1,)]   # 대상 무변경


def test_morning_on_a_holiday_refuses_and_leaves_tables(env, tmp_path: Path) -> None:
    """아침 휴장 D(2026-09-24) — 판에 그날 행이 없으니 날짜 단위 교체 전에 CompatEmptyError,
    가격·수급 표는 그대로다(실패 기록은 R6 대로 `_compat_meta` 에 남는다)."""
    morning = _equity(tmp_path / "tm", TM_BUILD, (D21, D22, D23))
    export(equity_root=morning, stage_root=env["stage"], date=T, basis="morning",
           target=env["target"], full=True, tables=["daily_prices", "investor_detail_flows"])
    before = {t: _rows(env["target"], f"SELECT * FROM {t} ORDER BY 1, 2")
              for t in ("daily_prices", "investor_detail_flows")}
    with pytest.raises(CompatEmptyError, match="2026-09-24"):
        export(equity_root=morning, stage_root=env["stage"], date="20260924", basis="morning",
               target=env["target"], full=True, tables=["daily_prices", "investor_detail_flows"])
    assert {t: _rows(env["target"], f"SELECT * FROM {t} ORDER BY 1, 2") for t in before} == before
    assert _rows(env["target"], "SELECT date, status FROM _compat_meta ORDER BY exported_at") == [
        ("2026-09-23", "ok"), ("2026-09-24", "failed")]


def test_evening_m_build_checks_the_seam_for_any_table(env, tmp_path: Path) -> None:
    """MINOR-2 — 장 마감 판이 `m_` 판을 받으면 T 표를 고르지 않아도 이음매를 본다. T 의 KRX 행을
    가진 아침 판으로 `stocks` 만 내보내도 멈춘다."""
    base = tmp_path / "tm"
    morning = _equity(base, TM_BUILD, (D21, D22, D23))
    _make_stage_tree(base, "security", [_sec_row(t, f"종목{t}", dt.date(2015, 1, 2))
                                        for t in (*UNIVERSE, X)], build_id=TM_BUILD)
    stage = tmp_path / "st2"
    _make_stage_tree(stage, "stg_master_daily", _master_rows(), partition_class="date_axis",
                     build_id="b_20260902T164532_554736Z")
    with pytest.raises(CompatError, match="이음매"):
        export(equity_root=morning, stage_root=stage / "stage", date=T, basis="evening",
               target=env["target"], full=True, tables=["stocks"],
               calendar_dir=env["calendar"])


def test_both_tables_share_one_source_per_ticker(env) -> None:
    """MINOR-4 — 원천 선택은 한 번(임시 표)이고 두 표가 같은 선택을 쓴다."""
    res = _evening(env)
    dp, fl = res.tables["daily_prices"].t_rows, res.tables["investor_detail_flows"].t_rows
    assert dp is not None and fl is not None
    keys = ("postclose", "kiwoom_2105", "missing", "kiwoom_2105_tickers", "missing_tickers")
    assert {k: dp[k] for k in keys} == {k: fl[k] for k in keys}


@pytest.mark.parametrize("case", ["empty_price", "price_valid_null"])
def test_postclose_row_without_usable_price_falls_back_to_2105(env, tmp_path: Path,
                                                                case: str) -> None:
    """NIT 1·5 — `price_valid='1'` 인데 가격 칸이 빈 행, `price_valid` 가 NULL 인 행은 건너뛰지 않고
    그 종목을 21:05 원장으로 넘긴다(가격·수급 모두)."""
    rows = _postclose_rows()
    ticker = FILL[0]
    if case == "empty_price":
        rows[ticker] = (_ledger_row(T, "", "+200", "500000", ind="-9"), True)
    path = _postclose_db(tmp_path / "raw3" / "postclose.db", rows=rows)
    if case == "price_valid_null":
        con = sqlite3.connect(path)
        con.execute("UPDATE ka10060_investor_flows SET price_valid = NULL WHERE ticker = ?",
                    (ticker,))
        con.commit()
        con.close()
    res = _evening(env, postclose_db=path)
    info = res.tables["daily_prices"].t_rows
    assert info is not None
    tickers = info["kiwoom_2105_tickers"]
    assert isinstance(tickers, list) and ticker in tickers
    assert _t_prices(env["target"])[ticker][3:5] == (10_300, 600_000)       # 21:05 원장 값
    assert _rows(env["target"], "SELECT individual FROM investor_detail_flows "
                                f"WHERE stock_code = '{ticker}' AND trade_date = '{T_ISO}'") == \
        [(-4321,)]


def test_t6_base_price_predicate() -> None:
    """NIT 3 — T-6 첫 조건 술어(`daily.kw_daily.ka10060_base_price_differs_sql`, PR-5 와 공유).
    판정 불가(값 없음 · 직전 종가 0 이하)는 '다르다'로 닫는다."""
    # 술어는 부호를 뗀 종가를 전제한다 — 두 원장 규칙이 cur_prc 를 abs 로 읽는지 함께 고정한다
    assert STG_FLOW_DAILY_KIWOOM.column("close_krw").sign == "abs"
    assert STG_FLOW_POSTCLOSE_KIWOOM.column("close_krw").sign == "abs"
    expr = kw_daily.ka10060_base_price_differs_sql("c", "p", "v")
    con = duckdb.connect()
    try:
        def differs(c: int | None, p: int | None, v: int | None) -> bool:
            row = con.execute(f"SELECT {expr} FROM (SELECT ?::BIGINT c, ?::BIGINT p, "
                              "?::BIGINT v)", [c, p, v]).fetchone()
            assert row is not None
            return bool(row[0])
        assert not differs(69_500, -500, 70_000)       # 기준가 70,000 = 직전 종가
        assert differs(51_000, 1_000, 100_000)         # 기준가 50,000 — 기업행위
        assert differs(None, 0, 1) and differs(1, None, 1) and differs(1, 0, None)
        assert differs(0, 0, 0)
    finally:
        con.close()


def test_collector_import_stays_light() -> None:
    """MINOR-5 — 장 마감 수집기(16:00 창)는 `compat.mappings` 를 읽지만 T 행 모듈·stage 빌더·규칙을
    끌어오지 않는다. 남는 stage 모듈은 판 해석(`equity.inputs` → `stage.manifest`)과 판 이름
    (`compat.quant_db` → `stage.model`)뿐이다."""
    code = ("import sys, daily.postclose; "
            "print(','.join(sorted(m for m in sys.modules "
            "if m == 'stage' or m.startswith('stage.') or m == 'compat.t_rows')))")
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                         text=True, check=True).stdout.strip()
    assert set(out.split(",")) <= {"stage", "stage.manifest", "stage.model"}
