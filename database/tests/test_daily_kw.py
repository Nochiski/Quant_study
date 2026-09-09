"""daily.kw_daily — 키움 일일 증분(fetch/merge)과 두 게이트. 플랜 P1 Task 1.3.

콜은 전부 `api.kiwoom` 스텁으로 대체하고 원장은 tmp_path 소형 sqlite 로 만든다(서버 접근 없음).
날짜는 20260908(화) 기준 — 직전 거래일 20260907(월)은 휴장 캐시가 있든 없든 거래일이라
캘린더 폴백(주말만 거름)에서도 실물 캐시에서도 같은 답이 나온다.
"""
import sqlite3
import sys
import types

from daily import kw_daily

D = "20260908"
D_PREV = "20260907"
D_NEXT = "20260909"          # 당일 개장 전 행(dt > D) — 머지에서 버려져야 한다
D_OLD = "20260904"
TICKERS = ("005930", "000660")
CLOSE = {"005930": "-70000", "000660": "+250000"}     # 키움 가격의 +/- 는 방향 표시자
VOL = {"005930": "1000", "000660": "2000"}
KRX_CLOSE = {"005930": "70000", "000660": "250000"}
ROWS_KEY = {"ka10008": "stk_frgnr", "ka10014": "shrts_trnsn",
            "ka20068": "slb_rmnd", "ka10060": "invsr_trde"}

CLEAN_POSS = {D_NEXT: {"005930": "40", "000660": "50"},
              D: {"005930": "30", "000660": "31"},
              D_PREV: {"005930": "20", "000660": "21"},
              D_OLD: {"005930": "10", "000660": "11"}}
# 오염: dt=D 가 dt=D-1 의 복사본(실측 2026-08-24 오염일 99.0%)
STALE_POSS = {D_NEXT: {"005930": "40", "000660": "50"},
              D: {"005930": "20", "000660": "21"},
              D_PREV: {"005930": "20", "000660": "21"},
              D_OLD: {"005930": "10", "000660": "11"}}


# ── 픽스처 ────────────────────────────────────────────────────────────────────
def _kiwoom_db(tmp_path, ledger_rows=()):
    """ka10099 마스터 + ka10008 원장(일부 컬럼만 — 머지가 나머지를 ALTER 로 붙인다)."""
    path = tmp_path / "data" / "raw" / "kiwoom.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE ka10099_stock_master (snap_date TEXT, code TEXT, upSizeName TEXT, "
                "marketName TEXT DEFAULT '거래소')")
    con.executemany("INSERT INTO ka10099_stock_master (snap_date, code, upSizeName) VALUES (?,?,?)",
                    [(D, t, "대형주") for t in TICKERS])
    con.execute('CREATE TABLE ka10008_foreign_holdings ('
                '"ticker" TEXT NOT NULL, "dt" TEXT, "close_pric" TEXT, "trde_qty" TEXT,'
                '"poss_stkcnt" TEXT, "src_api" TEXT, "collected_at" TEXT,'
                'PRIMARY KEY ("ticker", "dt"))')
    con.executemany('INSERT INTO ka10008_foreign_holdings VALUES (?,?,?,?,?,?,?)', ledger_rows)
    con.commit()
    con.close()
    return path


def _krx_db(tmp_path, date=D, close=None):
    path = tmp_path / "data" / "raw" / "krx.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    for tbl in ("krx_stk_bydd_trd", "krx_ksq_bydd_trd"):
        con.execute(f"CREATE TABLE {tbl} (bas_dd_req TEXT, ISU_CD TEXT, TDD_CLSPRC TEXT, "
                    "ACC_TRDVOL TEXT)")
    prices = dict(KRX_CLOSE if close is None else close)
    con.executemany("INSERT INTO krx_stk_bydd_trd VALUES (?,?,?,?)",
                    [(date, t, prices[t], VOL[t]) for t in TICKERS])
    con.commit()
    con.close()
    return path


def _response_rows(api_id, ticker, poss):
    dates = (D_NEXT, D, D_PREV, D_OLD)
    if api_id == "ka10008":
        return [{"dt": d, "close_pric": CLOSE[ticker], "trde_qty": VOL[ticker], "chg_qty": "0",
                 "poss_stkcnt": poss[d][ticker], "wght": "50"} for d in dates]
    if api_id == "ka10014":
        return [{"dt": d, "close_pric": CLOSE[ticker], "shrts_qty": "10"} for d in dates]
    if api_id == "ka20068":
        return [{"dt": d, "rmnd": "5"} for d in dates]
    return [{"dt": d, "ind_invsr": "1", "frgnr_invsr": "2"} for d in dates]   # ka10060: 컬럼 추론


def _stub_api(monkeypatch, calls, poss=None):
    """`api.kiwoom` 캔드 응답. `_kiwoom_module()` 이 import 하는 모듈을 통째로 갈아끼운다."""
    table = CLEAN_POSS if poss is None else poss

    def kiwoom(api_id, url, body, cont=None, next_key=None):
        ticker = body["stk_cd"]
        calls.append((api_id, ticker))
        return {"return_code": 0, "return_msg": "정상적으로 처리되었습니다",
                ROWS_KEY[api_id]: _response_rows(api_id, ticker, table)}, {}

    module = types.ModuleType("api")
    module.kiwoom = kiwoom
    monkeypatch.setitem(sys.modules, "api", module)


def _prepare(tmp_path, monkeypatch, calls, poss=None, ledger_rows=()):
    monkeypatch.setenv("QL_HOME", str(tmp_path))
    monkeypatch.setattr(kw_daily, "RATE_PER_SEC", 10_000.0)      # 테스트에서 스로틀 대기 제거
    _stub_api(monkeypatch, calls, poss)
    return _kiwoom_db(tmp_path, ledger_rows)


def _seed_prev(poss=None):
    table = CLEAN_POSS if poss is None else poss
    return [(t, D_PREV, "1", "1", table[D_PREV][t], "ka10008", "old") for t in TICKERS]


def _ledger(tmp_path, where="1=1", args=()):
    con = sqlite3.connect(tmp_path / "data" / "raw" / "kiwoom.db")
    try:
        return con.execute("SELECT ticker, dt, poss_stkcnt FROM ka10008_foreign_holdings "
                           f"WHERE {where} ORDER BY ticker, dt", args).fetchall()
    finally:
        con.close()


def _tables(tmp_path):
    con = sqlite3.connect(tmp_path / "data" / "raw" / "kiwoom.db")
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


# ── (a) fetch — 종목당 TR 당 1콜 · incoming 적재 ──────────────────────────────
def test_fetch_calls_each_tr_once_per_ticker_and_stages_incoming(tmp_path, monkeypatch):
    calls = []
    _prepare(tmp_path, monkeypatch, calls, ledger_rows=_seed_prev())
    rc = kw_daily.main(["--fetch", "--date", D])
    assert rc == 0
    assert len(calls) == len(TICKERS) * len(kw_daily.TRS)          # 4 TR × 2종목 = 8콜
    assert sorted({c for c in calls}) == sorted(
        {(api, t) for api in kw_daily.TRS for t in TICKERS})       # 종목·TR 당 정확히 1콜
    con = sqlite3.connect(tmp_path / "data" / "raw" / "kiwoom.db")
    try:
        for api_id in kw_daily.TRS:
            n = con.execute(f'SELECT COUNT(*) FROM "_kw_incoming_{api_id}"').fetchone()[0]
            assert n == len(TICKERS) * 4, api_id                   # 응답 4행 × 2종목
        # ka10060 은 cols=None — 첫 응답에서 컬럼을 추론한다
        cols = {r[1] for r in con.execute('PRAGMA table_info("_kw_incoming_ka10060")')}
        assert {"ticker", "dt", "ind_invsr", "frgnr_invsr", "fetched_at"} <= cols
    finally:
        con.close()
    assert _ledger(tmp_path) == [(t, D_PREV, CLEAN_POSS[D_PREV][t]) for t in sorted(TICKERS)]


# ── (b) 오염 게이트 — dt=D 가 D-1 의 복사본이면 rc 2 ─────────────────────────
def test_fetch_stale_holdings_fails_gate(tmp_path, monkeypatch):
    calls = []
    _prepare(tmp_path, monkeypatch, calls, poss=STALE_POSS, ledger_rows=_seed_prev(STALE_POSS))
    rc = kw_daily.main(["--fetch", "--date", D])
    assert rc == 2
    con = sqlite3.connect(tmp_path / "data" / "raw" / "daily_run.db")
    try:
        row = con.execute("SELECT source, status, detail FROM run ORDER BY run_id DESC "
                          "LIMIT 1").fetchone()
    finally:
        con.close()
    assert row[0] == "kiwoom_fetch" and row[1] == "gate_failed"
    assert "stale=2/2" in row[2] and "basis=ledger" in row[2]
    # 게이트 실패여도 fetch 자체는 저장한다(머지만 막힌다)
    assert "_kw_incoming_ka10008" in _tables(tmp_path)


def test_stale_gate_ratio_and_threshold():
    now = {f"{i:06d}": "100" for i in range(10)}
    prev = dict(now)
    prev["000009"] = "999"                                   # 1종목만 변했다 → 90% stale
    gate = kw_daily.StaleGate(n_compared=10, n_stale=9, basis="ledger")
    assert gate.pct == 90.0 and not gate.passed
    assert kw_daily.StaleGate(10, 3, "ledger").passed        # 30% 는 경계 안쪽(실측 정상일 9.7%)
    assert kw_daily.StaleGate(0, 0, "none").pct is None      # 비교 대상이 없으면 판정 불가


# ── (c) merge — KRX 전건 일치면 dt <= D 만 머지, dt > D 는 버린다 ─────────────
def test_merge_writes_only_dates_up_to_d_and_overwrites_by_pk(tmp_path, monkeypatch):
    calls = []
    # 원장 D-1 은 낡은 값("999") — PK (ticker, dt) 로 incoming 값이 덮어써야 한다
    stale_ledger = [(t, D_PREV, "1", "1", "999", "ka10008", "old") for t in TICKERS]
    _prepare(tmp_path, monkeypatch, calls, ledger_rows=stale_ledger)
    _krx_db(tmp_path)
    assert kw_daily.main(["--fetch", "--date", D]) == 0
    assert kw_daily.main(["--merge", "--date", D]) == 0

    rows = _ledger(tmp_path)
    assert [r[1] for r in rows] == [D_OLD, D_PREV, D] * 2          # dt > D 는 들어오지 않았다
    assert D_NEXT not in {r[1] for r in rows}
    by_key = {(t, d): p for t, d, p in rows}
    assert by_key[("005930", D_PREV)] == CLEAN_POSS[D_PREV]["005930"]
    con = sqlite3.connect(tmp_path / "data" / "raw" / "kiwoom.db")
    try:
        for api_id, spec in kw_daily.TRS.items():
            n = con.execute(f'SELECT COUNT(*) FROM "{spec.table}" WHERE dt > ?', (D,)).fetchone()
            assert n[0] == 0, api_id
            n = con.execute(f'SELECT COUNT(*) FROM "{spec.table}"').fetchone()
            assert n[0] == len(TICKERS) * 3, api_id                # D_OLD·D_PREV·D
    finally:
        con.close()
    run = sqlite3.connect(tmp_path / "data" / "raw" / "daily_run.db")
    try:
        row = run.execute("SELECT source, status, n_rows, detail FROM run WHERE source=? "
                          "ORDER BY run_id DESC LIMIT 1", ("kiwoom_merge",)).fetchone()
    finally:
        run.close()
    assert row[1] == "ok" and row[2] == len(TICKERS) * 3 * len(kw_daily.TRS)
    assert "matched=2 same_close=2 same_vol=2" in row[3]


# ── (d) 크로스소스 불일치 1행이면 머지하지 않는다 ────────────────────────────
def test_merge_refuses_when_krx_close_differs(tmp_path, monkeypatch):
    calls = []
    _prepare(tmp_path, monkeypatch, calls, ledger_rows=_seed_prev())
    _krx_db(tmp_path, close={"005930": "70000", "000660": "999999"})
    assert kw_daily.main(["--fetch", "--date", D]) == 0
    before = _ledger(tmp_path)
    assert kw_daily.main(["--merge", "--date", D]) == 2
    assert _ledger(tmp_path) == before                             # 원장 무변경
    con = sqlite3.connect(tmp_path / "data" / "raw" / "daily_run.db")
    try:
        row = con.execute("SELECT status, detail FROM run WHERE source=? ORDER BY run_id DESC "
                          "LIMIT 1", ("kiwoom_merge",)).fetchone()
    finally:
        con.close()
    assert row[0] == "cross_source_failed" and "000660" in row[1]


# ── (e) KRX 에 D 가 아직 없으면 대기(rc 3) ───────────────────────────────────
def test_merge_waits_when_krx_has_no_rows_for_date(tmp_path, monkeypatch):
    calls = []
    _prepare(tmp_path, monkeypatch, calls, ledger_rows=_seed_prev())
    _krx_db(tmp_path, date=D_PREV)                                 # D 는 아직 미공표
    assert kw_daily.main(["--fetch", "--date", D]) == 0
    before = _ledger(tmp_path)
    assert kw_daily.main(["--merge", "--date", D]) == 3
    assert _ledger(tmp_path) == before


# ── (f) dry-run 은 원장·incoming·runlog·유니버스 상태에 쓰지 않는다 ──────────
def test_dry_run_calls_but_writes_only_incoming_scratch(tmp_path, monkeypatch):
    """dry-run: 콜은 하고 incoming(스크래치)은 쓰되 원장·runlog·유니버스 상태는 안 쓴다 — merge dry-run 의 대조 대상."""
    calls = []
    _prepare(tmp_path, monkeypatch, calls, ledger_rows=_seed_prev())
    before = _ledger(tmp_path)
    rc = kw_daily.main(["--fetch", "--date", D, "--dry-run", "--limit", "1"])
    assert rc == 0
    assert len(calls) == len(kw_daily.TRS)                         # --limit 1 → TR 당 1콜
    assert any(t.startswith("_kw_incoming_") for t in _tables(tmp_path))
    assert _ledger(tmp_path) == before
    assert not (tmp_path / "data" / "raw" / "daily_run.db").exists()
    assert not (tmp_path / "data" / "daily" / "universe_kw.json").exists()


def test_dry_run_merge_reports_gate_without_writing(tmp_path, monkeypatch):
    calls = []
    _prepare(tmp_path, monkeypatch, calls, ledger_rows=_seed_prev())
    _krx_db(tmp_path)
    assert kw_daily.main(["--fetch", "--date", D]) == 0
    before = _ledger(tmp_path)
    assert kw_daily.main(["--merge", "--date", D, "--dry-run"]) == 0
    assert _ledger(tmp_path) == before
    con = sqlite3.connect(tmp_path / "data" / "raw" / "daily_run.db")
    try:
        sources = [r[0] for r in con.execute("SELECT source FROM run")]
    finally:
        con.close()
    assert sources == ["kiwoom_fetch"]                             # merge dry-run 은 기록하지 않는다


# ── --not-before: 실행 하한 미달이면 rc 3 ────────────────────────────────────
def test_not_before_blocks_with_rc3(tmp_path, monkeypatch):
    calls = []
    _prepare(tmp_path, monkeypatch, calls, ledger_rows=_seed_prev())
    assert kw_daily.main(["--fetch", "--date", D, "--not-before", "23:59"]) == 3
    assert calls == []
    assert kw_daily.main(["--fetch", "--date", D, "--not-before", "00:00"]) == 0
    assert len(calls) == len(TICKERS) * len(kw_daily.TRS)


# ── 콜 분류: 8005 는 조용히 건너뛰지 않고 rc 2 ───────────────────────────────
def test_token_failure_after_reissue_is_rc2(tmp_path, monkeypatch):
    calls = []
    _prepare(tmp_path, monkeypatch, calls, ledger_rows=_seed_prev())

    def dead(api_id, url, body, cont=None, next_key=None):
        calls.append((api_id, body["stk_cd"]))
        return {"return_code": 3, "return_msg": "[8005:유효하지 않은 토큰입니다]"}, {}

    module = types.ModuleType("api")
    module.kiwoom = dead
    monkeypatch.setitem(sys.modules, "api", module)
    assert kw_daily.main(["--fetch", "--date", D]) == 2
    con = sqlite3.connect(tmp_path / "data" / "raw" / "daily_run.db")
    try:
        row = con.execute("SELECT status, detail FROM run ORDER BY run_id DESC LIMIT 1").fetchone()
    finally:
        con.close()
    assert row[0] == "token_failed" and "8005" in row[1]


def test_nodata_code_is_not_an_error(tmp_path, monkeypatch):
    calls = []
    _prepare(tmp_path, monkeypatch, calls, ledger_rows=_seed_prev())

    def gone(api_id, url, body, cont=None, next_key=None):
        calls.append((api_id, body["stk_cd"]))
        return {"return_code": 3, "return_msg": "[1901:종목정보가 없습니다]"}, {}

    module = types.ModuleType("api")
    module.kiwoom = gone
    monkeypatch.setitem(sys.modules, "api", module)
    assert kw_daily.main(["--fetch", "--date", D]) == 0            # 폐지종목 등 — 실패가 아니다
    assert len(calls) == len(TICKERS) * len(kw_daily.TRS)          # 재시도 없이 1콜씩


# ── 순수 함수 ────────────────────────────────────────────────────────────────
def test_cross_source_requires_full_match_and_nonzero_overlap():
    krx = {"005930": kw_daily.Quote(70000, 1000), "000660": kw_daily.Quote(250000, 2000)}
    same = {"005930": kw_daily.Quote(70000, 1000), "000660": kw_daily.Quote(250000, 2000)}
    assert kw_daily.cross_source(krx, same).passed
    off = {"005930": kw_daily.Quote(70000, 1000), "000660": kw_daily.Quote(250000, 1)}
    check = kw_daily.cross_source(krx, off)
    assert not check.passed and check.n_same_close == 2 and check.n_same_vol == 1
    assert kw_daily.cross_source(krx, {}).passed is False          # 겹치는 종목이 0이면 실패
