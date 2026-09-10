"""daily.ledger_health — 원장별 완료 판정(실측 기대치)과 등급별 rc. 플랜 P1 Task 1.7."""
import json
import sqlite3
from pathlib import Path

from daily import ledger_health as lh

D, DP = "20260908", "20260907"          # 대상일(화), 직전 거래일(월)


def _cal(tmp_path, holidays=()):
    p = tmp_path / "kis_holidays.json"
    p.write_text(json.dumps({"year": 2026, "holidays": list(holidays)}), encoding="utf-8")
    return str(p)


def _krx(tmp_path, *, statuses="ok", stk=942, ksq=1820, kospi=51, kosdaq=40, etf=1150, base_mismatch=False):
    con = sqlite3.connect(tmp_path / "krx.db")
    con.execute("CREATE TABLE ingest_log (endpoint TEXT, bas_dd TEXT, n_rows INTEGER, status TEXT, note TEXT, collected_at TEXT)")
    eps = ["sto/stk_bydd_trd", "sto/ksq_bydd_trd", "sto/stk_isu_base_info", "sto/ksq_isu_base_info",
           "idx/kospi_dd_trd", "idx/kosdaq_dd_trd", "etp/etf_bydd_trd"]
    con.executemany("INSERT INTO ingest_log VALUES (?,?,0,?,NULL,'t')", [(e, D, statuses) for e in eps])
    for tbl, n in (("krx_stk_bydd_trd", stk), ("krx_ksq_bydd_trd", ksq),
                   ("krx_stk_isu_base_info", stk - (1 if base_mismatch else 0)), ("krx_ksq_isu_base_info", ksq),
                   ("krx_kospi_dd_trd", kospi), ("krx_kosdaq_dd_trd", kosdaq), ("krx_etf_bydd_trd", etf)):
        con.execute(f"CREATE TABLE {tbl} (bas_dd_req TEXT, ISU_CD TEXT, TDD_CLSPRC TEXT, ACC_TRDVOL TEXT)")
        con.executemany(f"INSERT INTO {tbl} VALUES (?,?,?,?)", [(D, f"{i:06d}", "1000", "10") for i in range(n)])
    con.commit(); con.close()
    return str(tmp_path / "krx.db")


def _kw(tmp_path, *, n=2563, stale=250, cross_bad=0):
    con = sqlite3.connect(tmp_path / "kiwoom.db")
    for tbl in ("ka10008_foreign_holdings", "ka10060_investor_flows", "ka20068_lending_balance", "ka10014_short_selling"):
        con.execute(f"CREATE TABLE {tbl} (ticker TEXT, dt TEXT, poss_stkcnt TEXT, close_pric TEXT, trde_qty TEXT)")
    rows_today, rows_prev = [], []
    for i in range(n):
        tk = f"{i:06d}"
        prev = "100"
        today = "100" if i < stale else "101"
        close = "-1000" if i >= cross_bad else "-999"
        rows_today.append((tk, D, today, close, "10")); rows_prev.append((tk, DP, prev, "-1000", "10"))
    for tbl in ("ka10008_foreign_holdings", "ka10060_investor_flows", "ka20068_lending_balance"):
        con.executemany(f"INSERT INTO {tbl} VALUES (?,?,?,?,?)", rows_today + rows_prev)
    con.executemany("INSERT INTO ka10014_short_selling VALUES (?,?,?,?,?)", rows_today[:2200] + rows_prev[:2250])
    con.execute("CREATE TABLE ka10099_stock_master (snap_date TEXT, mrkt_tp TEXT, code TEXT)")
    con.executemany("INSERT INTO ka10099_stock_master VALUES (?,?,?)",
                    [(D, "0" if i < 2486 else "10", f"{i:06d}") for i in range(4308)])
    con.commit(); con.close()
    return str(tmp_path / "kiwoom.db")


def _paths(tmp_path, **kw):
    p = lh.Paths(krx=kw.get("krx", str(tmp_path / "none1.db")), kiwoom=kw.get("kiwoom", str(tmp_path / "none2.db")),
                 kis=str(tmp_path / "none3.db"), dart=str(tmp_path / "none4.db"), wise=kw.get("wise", str(tmp_path / "none5.db")),
                 calendar=_cal(tmp_path), universe_state=str(tmp_path / "universe_kw.json"))
    (tmp_path / "universe_kw.json").write_text(json.dumps({"asof": D, "grace": {}, "n_requested": 2563}), encoding="utf-8")
    return p


def _by(report):
    return {c.name: c for c in report.checks}


def test_krx_all_ok_passes(tmp_path):
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path)))
    c = _by(rep)
    assert c["krx.ingest_log"].status is lh.Status.PASS and c["krx.rows"].status is lh.Status.PASS
    assert c["krx.holiday_misfire"].status is lh.Status.PASS and rep.ok


def test_krx_base_mismatch_and_pending_fail(tmp_path):
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path, base_mismatch=True)))
    assert _by(rep)["krx.rows"].status is lh.Status.FAIL and not rep.ok
    sub = tmp_path / "b"
    sub.mkdir()
    rep2 = lh.run(D, _paths(tmp_path, krx=_krx(sub, statuses="pending")))
    assert _by(rep2)["krx.ingest_log"].status is lh.Status.FAIL


def test_holiday_on_trading_day_is_halt(tmp_path):
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path, statuses="holiday")))
    c = _by(rep)
    assert c["krx.holiday_misfire"].level is lh.Level.HALT and c["krx.holiday_misfire"].status is lh.Status.FAIL
    assert rep.halts and not rep.ok


def test_kiwoom_relative_gates_and_cross_source(tmp_path):
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path, stk=2563, ksq=1820), kiwoom=_kw(tmp_path)))
    c = _by(rep)
    assert c["kiwoom.ka10008.rows"].status is lh.Status.PASS          # 2563/2563
    assert c["kiwoom.ka10008.stale_pct"].value == 9.8 and c["kiwoom.ka10008.stale_pct"].status is lh.Status.PASS
    assert c["kiwoom.krx_cross"].status is lh.Status.PASS
    assert c["kiwoom.master"].status is lh.Status.PASS
    assert c["kiwoom.ka10014.trend"].status is lh.Status.PASS          # 2200/2250 = 0.98


def test_kiwoom_contamination_and_cross_mismatch_fail(tmp_path):
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path, stk=2563), kiwoom=_kw(tmp_path, stale=2540, cross_bad=1)))
    c = _by(rep)
    assert c["kiwoom.ka10008.stale_pct"].status is lh.Status.FAIL     # 99.1%
    assert c["kiwoom.krx_cross"].status is lh.Status.FAIL and c["kiwoom.krx_cross"].value["same_close"] == 2562


def test_report_json_roundtrip(tmp_path):
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path)))
    path = lh.write_report(rep, str(tmp_path / "health"))
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["ok"] is True and data["checks"][0]["level"] == "required" and "OK" in data["summary"]


def test_skip_source_excludes_its_checks(tmp_path):
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path, stk=2563), kiwoom=_kw(tmp_path, stale=2540)),
                 skip=frozenset({"kiwoom"}))
    names = {c.name for c in rep.checks}
    assert "kiwoom.skipped" in names and not any(n.startswith("kiwoom.ka") for n in names)
    assert rep.ok                                                     # 오염 99% 픽스처인데 kiwoom 을 제외했으니 통과


def _wise(tmp_path, *, cov=804, none=1759, n_req=None):
    """ws_run_log 1건 + 당일 checked_at 커버리지. n_req 기본값은 항등식대로."""
    n_req = n_req if n_req is not None else cov * 15 + none * 4
    con = sqlite3.connect(tmp_path / "wise.db")
    con.execute("CREATE TABLE ws_run_log (run_at TEXT, mode TEXT, n_stocks INTEGER, n_req INTEGER, n_ok INTEGER, n_bad INTEGER, bad_summary TEXT)")
    con.execute("INSERT INTO ws_run_log VALUES ('2026-09-07T21:04:00','full',?,?,?,0,'{}')", (cov + none, n_req, n_req))
    con.execute("CREATE TABLE ws_coverage (cmp_cd TEXT PRIMARY KEY, status TEXT, checked_at TEXT)")
    con.executemany("INSERT INTO ws_coverage VALUES (?,?,'2026-09-07T21:04:30')",
                    [(f"{i:06d}", "covered" if i < cov else "none") for i in range(cov + none)])
    con.commit(); con.close()
    return str(tmp_path / "wise.db")


def test_wise_request_identity_counts_four_requests_per_uncovered_stock(tmp_path):
    # 검수 D H1 후속: 무커버 판정이 3개년 cF5001 을 다 본 뒤에만 나므로 무커버 종목은 4콜(목록 1 + cF5001 3)이다.
    import datetime as dt
    rep = lh.run(D, _paths(tmp_path, wise=_wise(tmp_path)), today=dt.date(2026, 9, 8))
    ident = next(c for c in rep.checks if c.name == "wise.req_identity")
    assert ident.status is lh.Status.PASS and ident.value["expected"] == 804 * 15 + 1759 * 4
    sub = tmp_path / "b"; sub.mkdir()
    rep2 = lh.run(D, _paths(sub, wise=_wise(sub, n_req=804 * 15 + 1759 * 2)), today=dt.date(2026, 9, 8))
    assert next(c for c in rep2.checks if c.name == "wise.req_identity").status is lh.Status.FAIL
