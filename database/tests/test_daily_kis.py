"""daily.kis_daily — KIS 신용잔고 일일 증분.

플랜 `docs/plans/2026-09-09-daily-incremental.md` Task 1.4 / R6.
근거 `docs/reviews/2026-09-09-daily-findings-A-krx-kiwoom-kis.md` §1-4(DEFECT-A-03) · §2-3 · §6-3.

콜은 전부 `api.kis` monkeypatch 다 — 이 파일은 네트워크를 쓰지 않는다.
"""
import importlib
import sqlite3
from pathlib import Path

import pytest

from daily import kis_daily, runlog

# api.py 는 import 시점에 KRX 키를 요구한다(api.py:35). 실제 자격증명 대신 가짜 .env 를 물린다.
_FAKE_ENV = ("KRX_API_KEY=krx\nKRX_ID=id\nKRX_PW=pw\n"
             "KIS_APP_KEY=appkey\nKIS_APP_SECRET=secret\n")


@pytest.fixture
def api_mod(tmp_path, monkeypatch):
    """`api` 모듈. 토큰 캐시 경로도 tmp 로 돌려 실제 캐시를 지우지 않게 한다."""
    env = tmp_path / "fake.env"
    env.write_text(_FAKE_ENV, encoding="utf-8")
    monkeypatch.setenv("QL_ENV", str(env))
    api = importlib.import_module("api")
    monkeypatch.setattr(api, "_KIS_CACHE", str(tmp_path / "kis_token.json"))
    monkeypatch.setattr(kis_daily.time, "sleep", lambda _s: None)   # 재시도 백오프 제거
    return api


def _fact(deal_date: str, rmnd: str = "1000") -> dict[str, str]:
    """신용잔고 응답 1행(실제 26컬럼 중 판정에 쓰는 것만)."""
    return {"deal_date": deal_date, "stlm_date": "20260909", "stck_prpr": "10000",
            "whol_loan_rmnd_stcn": rmnd, "whol_loan_new_stcn": "10"}


def _ok(rows: list[dict[str, str]]) -> dict[str, object]:
    return {"rt_cd": "0", "msg_cd": "MCA00000", "output": rows}


def _fake_kis(by_ticker: dict[str, list[dict[str, str]]], seen: list[dict[str, str]] | None = None):
    """`api.kis(url, tr_id, params)` 대역. 종목별로 정해진 행을 돌려준다."""
    def kis(url: str, tr_id: str, params: dict[str, str]) -> dict[str, object]:
        if seen is not None:
            seen.append(dict(params))
        return _ok(by_ticker.get(params["FID_INPUT_ISCD"], []))
    return kis


def _con(tmp_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(tmp_path / "kis.db")


def _n_rows(con: sqlite3.Connection) -> int:
    return int(con.execute("SELECT COUNT(*) FROM kis_credit_balance").fetchone()[0])


def _seed_by_backfill(con: sqlite3.Connection, ticker: str, d1: str, d2: str,
                      rows: list[dict[str, str]]) -> None:
    """기존 백필 경로(`backfill_kis` → `backfill_dart.store`)가 만든 행을 그대로 재현한다."""
    from backfill_dart import store
    from backfill_kis import _spec_shim
    store(con, _spec_shim("credit"), rows,
          {"ticker": ticker, "d1": d1, "d2": d2, "name": "credit"})


# ── 사실 중복 차단 (DEFECT-A-03) ─────────────────────────────────────────────

def test_same_fact_with_a_moved_window_is_not_inserted_twice(api_mod, tmp_path):
    """같은 (종목, deal_date) 사실을 창만 바꿔 두 번 받아도 원장은 늘지 않는다."""
    con = _con(tmp_path)
    rows = [_fact("20260904"), _fact("20260903", "900")]
    first = kis_daily.store_new_facts(con, "005930", "20260726", "20260905", rows)
    second = kis_daily.store_new_facts(con, "005930", "20260730", "20260909", rows)
    assert (first.n_new, first.n_dup_skipped) == (2, 0)
    assert (second.n_new, second.n_dup_skipped) == (0, 2)
    assert _n_rows(con) == 2


def test_row_written_by_the_backfill_path_is_recognized_as_the_same_fact(api_mod, tmp_path):
    """백필이 넣은 행(다른 req_d1/req_d2)도 같은 사실로 본다 — 컬럼·해시 규약 호환."""
    con = _con(tmp_path)
    _seed_by_backfill(con, "005930", "20100104", "20260828", [_fact("20260904")])
    out = kis_daily.store_new_facts(con, "005930", "20260730", "20260909", [_fact("20260904")])
    assert (out.n_new, out.n_dup_skipped) == (0, 1)
    assert _n_rows(con) == 1
    cols = {r[1] for r in con.execute("PRAGMA table_info(kis_credit_balance)")}
    assert {"row_hash", "req_ticker", "req_d1", "req_d2", "req_name", "dup_seq",
            "collected_at"} <= cols


def test_corrected_balance_is_kept_as_a_new_row(api_mod, tmp_path):
    """값이 달라지면(정정) 새 행으로 남긴다 — 덮어쓰지 않는다."""
    con = _con(tmp_path)
    kis_daily.store_new_facts(con, "005930", "20260726", "20260905", [_fact("20260904", "1000")])
    out = kis_daily.store_new_facts(con, "005930", "20260730", "20260909",
                                    [_fact("20260904", "1200")])
    assert (out.n_new, out.n_dup_skipped) == (1, 0)
    got = con.execute("SELECT whol_loan_rmnd_stcn FROM kis_credit_balance "
                      "WHERE req_ticker='005930' AND deal_date='20260904' ORDER BY 1").fetchall()
    assert [r[0] for r in got] == ["1000", "1200"]


def test_new_response_field_is_added_as_a_column(api_mod, tmp_path):
    """응답에 없던 필드가 나타나면 ALTER TABLE 로 붙인다(백필 store 와 같은 규약)."""
    con = _con(tmp_path)
    kis_daily.store_new_facts(con, "005930", "20260726", "20260905", [_fact("20260904")])
    kis_daily.store_new_facts(con, "005930", "20260730", "20260909",
                              [{**_fact("20260905"), "new_fld": "7"}])
    cols = {r[1] for r in con.execute("PRAGMA table_info(kis_credit_balance)")}
    assert "new_fld" in cols
    assert _n_rows(con) == 2


# ── 완료 판정 · 중복 감시 (A §6-3) ───────────────────────────────────────────

def test_gate_passes_and_run_records_the_day(api_mod, monkeypatch, tmp_path):
    tickers = ("000660", "005930", "035420", "051910")
    monkeypatch.setattr(api_mod, "kis", _fake_kis({t: [_fact("20260907")] for t in tickers}))
    con = _con(tmp_path)
    run_db = tmp_path / "daily_run.db"
    res = kis_daily.run(con, date="20260908", gate_date="20260907", d1="20260730", d2="20260909",
                        tickers=tickers, run_db=run_db)
    assert res.status is kis_daily.Status.OK
    assert res.rc == 0
    assert (res.n_calls, res.n_new_rows, res.n_tickers) == (4, 4, 4)
    assert runlog.recent(run_db, source="kis_credit")[0].status == "ok"


def test_gate_fails_with_rc2_but_keeps_what_was_collected(api_mod, monkeypatch, tmp_path):
    """행수/요청 유니버스 < 0.95 면 rc 2. 받은 행은 그대로 남긴다."""
    tickers = ("000660", "005930", "035420", "051910")
    monkeypatch.setattr(api_mod, "kis", _fake_kis({"000660": [_fact("20260907")],
                                                   "005930": [_fact("20260907")]}))
    con = _con(tmp_path)
    res = kis_daily.run(con, date="20260908", gate_date="20260907", d1="20260730", d2="20260909",
                        tickers=tickers, run_db=tmp_path / "daily_run.db")
    assert res.status is kis_daily.Status.GATE_FAILED
    assert res.rc == 2
    assert "ratio=0.5000" in res.detail
    assert _n_rows(con) == 2


def test_gate_rejects_a_ticker_with_two_rows_on_the_target_date(api_mod, tmp_path):
    """`COUNT(DISTINCT req_ticker) = COUNT(*)` 위반은 비율이 충분해도 실패다."""
    con = _con(tmp_path)
    kis_daily.store_new_facts(con, "005930", "20260726", "20260905", [_fact("20260907", "1000")])
    kis_daily.store_new_facts(con, "005930", "20260730", "20260909", [_fact("20260907", "1200")])
    kis_daily.store_new_facts(con, "000660", "20260730", "20260909", [_fact("20260907")])
    g = kis_daily.gate(con, "20260907", 2)
    assert not g.ok
    assert (g.n_rows, g.n_distinct_tickers) == (3, 2)
    assert "종목당 1행 위반" in g.detail


def test_correction_is_not_duplicate_growth(api_mod, monkeypatch, tmp_path):
    """값이 바뀐 정정은 새 행으로 남지만(사실 보존) DUP_GROWTH 가 아니다 — payload 가 다르기 때문."""
    con = _con(tmp_path)
    kis_daily.store_new_facts(con, "005930", "20260726", "20260905", [_fact("20260907", "1000")])
    monkeypatch.setattr(api_mod, "kis", _fake_kis({"005930": [_fact("20260907", "1200")]}))
    res = kis_daily.run(con, date="20260908", gate_date="20260907", d1="20260730", d2="20260909",
                        tickers=("005930",), run_db=tmp_path / "daily_run.db")
    assert res.status is not kis_daily.Status.DUP_GROWTH
    assert _n_rows(con) == 2 and kis_daily.dup_pairs(con, "20260101") == 0


def test_duplicate_growth_stops_the_run(api_mod, monkeypatch, tmp_path):
    """payload 까지 같은 행이 창만 달리해 다시 쌓이면(DEFECT-A-03 재발) rc 2.

    `store_new_facts` 가 정상이면 도달할 수 없는 경로라, 백필 경로(`backfill_dart.store`, 중복 차단 없음)로
    같은 사실을 다른 `req_d2` 로 한 번 더 넣는 회귀를 흉내 낸다.
    """
    con = _con(tmp_path)
    kis_daily.store_new_facts(con, "005930", "20260726", "20260905", [_fact("20260907", "1000")])

    def leaky_store(con_, ticker, d1, d2, rows):          # 중복 차단이 빠진 저장 경로
        _seed_by_backfill(con_, ticker, d1, d2, rows)
        return kis_daily.StoreOutcome(len(rows), 0)

    monkeypatch.setattr(kis_daily, "store_new_facts", leaky_store)
    monkeypatch.setattr(api_mod, "kis", _fake_kis({"005930": [_fact("20260907", "1000")]}))
    res = kis_daily.run(con, date="20260908", gate_date="20260907", d1="20260730", d2="20260909",
                        tickers=("005930",), run_db=tmp_path / "daily_run.db")
    assert res.status is kis_daily.Status.DUP_GROWTH
    assert res.rc == 2
    assert "dup_pairs 0->1" in res.detail


# ── 토큰 만료는 조용히 넘어가지 않는다 ───────────────────────────────────────

def test_token_expiry_fails_loudly_after_one_reissue(api_mod, monkeypatch, tmp_path):
    calls: list[dict[str, str]] = []

    def kis(url: str, tr_id: str, params: dict[str, str]) -> dict[str, object]:
        calls.append(dict(params))
        return {"rt_cd": "1", "msg_cd": "EGW00121", "msg1": "기간이 만료된 token"}

    monkeypatch.setattr(api_mod, "kis", kis)
    run_db = tmp_path / "daily_run.db"
    res = kis_daily.run(_con(tmp_path), date="20260908", gate_date="20260907", d1="20260730",
                        d2="20260909", tickers=("005930", "000660"), run_db=run_db)
    assert res.status is kis_daily.Status.TOKEN_FAILED
    assert res.rc == 2
    assert len(calls) == 2                       # 최초 콜 + 재발급 후 1회, 그리고 중단
    assert runlog.recent(run_db, source="kis_credit")[0].status == "token_failed"


# ── dry-run (CLI 전체 경로) ──────────────────────────────────────────────────

def _ql_home(tmp_path: Path, tickers: tuple[str, ...]) -> Path:
    home = tmp_path / "ql"
    (home / "data" / "raw").mkdir(parents=True)
    kw = sqlite3.connect(home / "data" / "raw" / "kiwoom.db")
    kw.execute("CREATE TABLE ka10099_stock_master (snap_date TEXT, code TEXT, upSizeName TEXT)")
    kw.executemany("INSERT INTO ka10099_stock_master VALUES ('20260909',?,'대형주')",
                   [(t,) for t in tickers])
    kw.execute("CREATE TABLE ka10008_foreign_holdings (ticker TEXT, dt TEXT)")
    kw.commit()
    kw.close()
    return home


def test_dry_run_calls_the_api_but_writes_nothing(api_mod, monkeypatch, tmp_path):
    tickers = ("000660", "005930", "035420")
    home = _ql_home(tmp_path, tickers)
    monkeypatch.setenv("QL_HOME", str(home))
    seen: list[dict[str, str]] = []
    monkeypatch.setattr(api_mod, "kis",
                        _fake_kis({t: [_fact("20260907")] for t in tickers}, seen))

    rc = kis_daily.main(["--date", "2026-09-08", "--dry-run", "--limit", "2"])

    assert rc == 0
    assert len(seen) == 2                                     # --limit 만큼 실제 콜
    assert seen[0]["FID_INPUT_DATE_1"] == kis_daily.window(
        kis_daily.dt.datetime.now(kis_daily.KST).date())[1]   # d2 = 오늘(T) — R6
    con = sqlite3.connect(home / "data" / "raw" / "kis.db")
    assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND "
                       "name='kis_credit_balance'").fetchone()[0] == 0
    assert not (home / "data" / "raw" / "daily_run.db").exists()
    assert not (home / "data" / "daily" / "universe_kw.json").exists()   # 유예 상태도 무변경


def test_full_run_writes_the_ledger_and_the_universe_state(api_mod, monkeypatch, tmp_path):
    tickers = ("000660", "005930", "035420")
    home = _ql_home(tmp_path, tickers)
    monkeypatch.setenv("QL_HOME", str(home))
    monkeypatch.setattr(api_mod, "kis", _fake_kis({t: [_fact("20260907")] for t in tickers}))

    rc = kis_daily.main(["--date", "2026-09-08"])

    assert rc == 0
    con = sqlite3.connect(home / "data" / "raw" / "kis.db")
    assert _n_rows(con) == 3
    assert (home / "data" / "raw" / "daily_run.db").exists()
    assert (home / "data" / "daily" / "universe_kw.json").exists()
