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


# 기업행위 후보 픽스처 — (시장, 종목 인덱스, 액면가 전/후, 주식수 전/후). 09-09 서버 실측 형태.
_PARVAL, _LIST_SHRS = "500", "1000000"


def _krx(tmp_path, *, statuses="ok", stk=942, ksq=1820, kospi=51, kosdaq=40, etf=1150, base_mismatch=False,
         corp_actions=(), base_prev=True):
    """`base_prev=False` 면 직전 거래일 `*_isu_base_info` 행을 아예 넣지 않는다(판정 불가 경로)."""
    con = sqlite3.connect(tmp_path / "krx.db")
    con.execute("CREATE TABLE ingest_log (endpoint TEXT, bas_dd TEXT, n_rows INTEGER, status TEXT, note TEXT, collected_at TEXT)")
    eps = ["sto/stk_bydd_trd", "sto/ksq_bydd_trd", "sto/stk_isu_base_info", "sto/ksq_isu_base_info",
           "idx/kospi_dd_trd", "idx/kosdaq_dd_trd", "etp/etf_bydd_trd"]
    con.executemany("INSERT INTO ingest_log VALUES (?,?,0,?,NULL,'t')", [(e, D, statuses) for e in eps])
    base = {"stk": {}, "ksq": {}}
    for market, i, parval, shrs in corp_actions:
        base[market][i] = (parval, shrs)
    for tbl, n in (("krx_stk_bydd_trd", stk), ("krx_ksq_bydd_trd", ksq),
                   ("krx_kospi_dd_trd", kospi), ("krx_kosdaq_dd_trd", kosdaq), ("krx_etf_bydd_trd", etf)):
        con.execute(f"CREATE TABLE {tbl} (bas_dd_req TEXT, ISU_CD TEXT, TDD_CLSPRC TEXT, ACC_TRDVOL TEXT)")
        con.executemany(f"INSERT INTO {tbl} VALUES (?,?,?,?)", [(D, f"{i:06d}", "1000", "10") for i in range(n)])
    for market, n in (("stk", stk - (1 if base_mismatch else 0)), ("ksq", ksq)):
        tbl = f"krx_{market}_isu_base_info"
        con.execute(f"CREATE TABLE {tbl} (bas_dd_req TEXT, ISU_CD TEXT, ISU_SRT_CD TEXT, ISU_ABBRV TEXT, "
                    "PARVAL TEXT, LIST_SHRS TEXT)")
        rows = []
        for i in range(n):
            parval, shrs = base[market].get(i, ((_PARVAL, _PARVAL), (_LIST_SHRS, _LIST_SHRS)))
            code = f"{i:06d}"
            rows.append((D, f"KR7{code}003", code, f"종목{i}", parval[1], shrs[1]))
            if base_prev:
                rows.append((DP, f"KR7{code}003", code, f"종목{i}", parval[0], shrs[0]))
        con.executemany(f"INSERT INTO {tbl} VALUES (?,?,?,?,?,?)", rows)
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


def test_krx_corp_action_candidates_flags_parval_and_shares_changes(tmp_path):
    """검수 종합 H1 — 액면병합·주식수 변동은 원장이 정확해도 하류에서 조용히 틀린다.

    09-09 실측 형태 두 건(001290 액면병합 1,000→5,000 · 주식수 ÷5 / 099190 액면가 불변 주식수
    +1.89%)과 문턱 아래 변동 한 건(+0.04%)을 넣어 앞 둘만 잡히는지 본다.
    """
    krx = _krx(tmp_path, corp_actions=(
        ("stk", 3, ("1000", "5000"), ("108337120", "21667424")),      # 액면병합
        ("ksq", 7, ("500", "500"), ("28757309", "29301512")),          # 주식수 +1.89%
        ("ksq", 9, ("500", "500"), ("1000000", "1000400")),            # +0.04% — 문턱 아래
    ))
    rep = lh.run(D, _paths(tmp_path, krx=krx))
    c = _by(rep)["krx.corp_action_candidates"]
    assert c.level is lh.Level.WARN and c.status is lh.Status.FAIL
    assert c.value["n"] == 2
    got = {i["code"]: i for i in c.value["items"]}
    assert set(got) == {"000003", "000007"}
    assert got["000003"]["parval"] == ["1000", "5000"] and got["000003"]["shares_ratio"] == 0.2
    assert got["000007"]["parval"] == ["500", "500"] and got["000007"]["shares_ratio"] == 1.018924
    assert rep.ok                                                      # warn 은 rc 를 바꾸지 않는다


def test_krx_corp_action_candidates_pass_and_skip(tmp_path):
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path)))
    c = _by(rep)["krx.corp_action_candidates"]
    assert c.status is lh.Status.PASS and c.value["n"] == 0 and c.value["items"] == []
    assert c.value["judged"] == ["stk", "ksq"] and c.value["skipped"] == []
    sub = tmp_path / "b"; sub.mkdir()
    rep2 = lh.run(D, _paths(sub, krx=_krx(sub, base_prev=False)))
    assert _by(rep2)["krx.corp_action_candidates"].status is lh.Status.SKIP


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


# WISE 스냅샷 시각 3종 — (run_at UTC, checked_at UTC, fetched_date KST). 대상일 D=20260908 기준.
_WISE_SNAPSHOT = {
    "evening": ("2026-09-08T09:05:00", "2026-09-08T09:05:30", "2026-09-08"),       # D 18:05 KST (V2-3)
    "next_morning": ("2026-09-08T21:04:00", "2026-09-08T21:04:30", "2026-09-09"),  # D+1 06:04 KST (옛 방식)
    "stale": ("2026-09-04T09:05:00", "2026-09-04T09:05:30", "2026-09-04"),         # D-4 저녁 — 두 기준 다 아님
}


def _wise(tmp_path, *, cov=804, none=1759, n_req=None, raw_rows=None, raw_stocks=None, snapshot="evening"):
    """ws_run_log 1건 + 같은 런의 checked_at 커버리지 + ws_raw(기본은 항등식대로). n_req 기본값은 항등식대로."""
    n_req = n_req if n_req is not None else cov * 15 + none * 4
    raw_rows = n_req if raw_rows is None else raw_rows
    raw_stocks = cov + none if raw_stocks is None else raw_stocks
    run_at, checked_at, fetched_date = _WISE_SNAPSHOT[snapshot]
    con = sqlite3.connect(tmp_path / "wise.db")
    con.execute("CREATE TABLE ws_raw (cmp_cd TEXT, ep TEXT, pkey TEXT, fetched_date TEXT)")
    con.executemany("INSERT INTO ws_raw VALUES (?,?,?,?)",
                    [(f"{i % raw_stocks:06d}", f"ep{i // raw_stocks}", str(i), fetched_date) for i in range(raw_rows)])
    con.execute("CREATE TABLE ws_run_log (run_at TEXT, mode TEXT, n_stocks INTEGER, n_req INTEGER, n_ok INTEGER, n_bad INTEGER, bad_summary TEXT)")
    con.execute("INSERT INTO ws_run_log VALUES (?,'full',?,?,?,0,'{}')", (run_at, cov + none, n_req, n_req))
    con.execute("CREATE TABLE ws_coverage (cmp_cd TEXT PRIMARY KEY, status TEXT, checked_at TEXT)")
    con.executemany("INSERT INTO ws_coverage VALUES (?,?,?)",
                    [(f"{i:06d}", "covered" if i < cov else "none", checked_at) for i in range(cov + none)])
    con.commit(); con.close()
    return str(tmp_path / "wise.db")


def test_wise_request_identity_counts_four_requests_per_uncovered_stock(tmp_path):
    # 검수 D H1 후속: 무커버 판정이 3개년 cF5001 을 다 본 뒤에만 나므로 무커버 종목은 4콜(목록 1 + cF5001 3)이다.
    rep = lh.run(D, _paths(tmp_path, wise=_wise(tmp_path)))
    ident = next(c for c in rep.checks if c.name == "wise.req_identity")
    assert ident.status is lh.Status.PASS and ident.value["expected"] == 804 * 15 + 1759 * 4
    sub = tmp_path / "b"; sub.mkdir()
    rep2 = lh.run(D, _paths(sub, wise=_wise(sub, n_req=804 * 15 + 1759 * 2)))
    assert next(c for c in rep2.checks if c.name == "wise.req_identity").status is lh.Status.FAIL


def test_wise_raw_expectation_derives_from_coverage_not_a_fixed_band(tmp_path):
    # 09-11 실측: 규모구분 갱신으로 유니버스 2,563→2,610, 무커버 4콜 → rows 19,416. 옛 절대 밴드(15,500~15,700 ·
    # 2,560~2,570)는 오탐. 기대치 = covered×15 + none×4 · stocks = covered+none.
    rep = lh.run(D, _paths(tmp_path, wise=_wise(tmp_path, cov=816, none=1794)))
    raw = next(c for c in rep.checks if c.name == "wise.raw")
    assert raw.status is lh.Status.PASS and raw.value == {"rows": 816 * 15 + 1794 * 4, "stocks": 2610}
    sub = tmp_path / "b"; sub.mkdir()
    rep2 = lh.run(D, _paths(sub, wise=_wise(sub, cov=816, none=1794, raw_rows=19000)))
    assert next(c for c in rep2.checks if c.name == "wise.raw").status is lh.Status.FAIL


def test_wise_snapshot_read_on_target_day_evening(tmp_path):
    # V2-3: 스냅샷이 06:00(D+1) → 18:05(D) 로 옮겨간다. D+1 아침 판정은 실행일이 아니라 D 의 KST 날짜를 봐야 한다.
    rep = lh.run(D, _paths(tmp_path, wise=_wise(tmp_path, snapshot="evening")))
    c = _by(rep)
    assert c["wise.snapshot_day"].value == "target_day"
    assert c["wise.run"].status is lh.Status.PASS and c["wise.req_identity"].status is lh.Status.PASS
    assert c["wise.raw"].status is lh.Status.PASS


def test_wise_falls_back_to_next_morning_snapshot(tmp_path):
    # 전환기: D 저녁 행이 없고 옛 방식(D+1 아침) 행만 있으면 그것으로 판정하되 snapshot_day 에 남긴다.
    rep = lh.run(D, _paths(tmp_path, wise=_wise(tmp_path, snapshot="next_morning")))
    c = _by(rep)
    assert c["wise.snapshot_day"].value == "next_morning"
    assert c["wise.run"].status is lh.Status.PASS and c["wise.req_identity"].status is lh.Status.PASS
    assert c["wise.raw"].status is lh.Status.PASS


def test_wise_without_either_snapshot_fails(tmp_path):
    rep = lh.run(D, _paths(tmp_path, wise=_wise(tmp_path, snapshot="stale")))
    c = _by(rep)
    assert c["wise.run"].status is lh.Status.FAIL and not rep.ok
    assert c["wise.snapshot_day"].status is lh.Status.SKIP and c["wise.snapshot_day"].value is None


# ── 검수 R3-02·R3-03: 기업행위 후보 게이트가 시장별 결측·NULL 을 "0건" 으로 위장하지 않는다 ──
def test_한_시장의_직전거래일_행이_없으면_그_시장만_SKIP_이고_다른_시장은_판정한다(tmp_path) -> None:
    """stk 정상 · ksq 만 직전 거래일 base_info 가 비었을 때. ksq 에 심은 액면병합은 대조 상대가 없어
    잡을 수 없지만, 그 사실이 `skipped` 로 드러나야지 PASS('0건') 로 보이면 안 된다(R3-02)."""
    krx = _krx(tmp_path, corp_actions=(("ksq", 7, ("500", "5000"), ("28757309", "2875730")),))
    con = sqlite3.connect(tmp_path / "krx.db")
    con.execute("DELETE FROM krx_ksq_isu_base_info WHERE bas_dd_req=?", (DP,))
    con.commit(); con.close()
    rep = lh.run(D, _paths(tmp_path, krx=krx))
    c = _by(rep)["krx.corp_action_candidates"]
    assert c.status is lh.Status.PASS          # stk 는 판정했고 후보 0
    assert c.value["judged"] == ["stk"]
    assert any(s.startswith("ksq(") for s in c.value["skipped"]), c.value
    assert "판정 불가 시장: ksq" in c.expected


def test_LIST_SHRS가_NULL이_되면_후보로_뜬다(tmp_path) -> None:
    """D 쪽 LIST_SHRS 가 NULL(수집 누락) — `<>` 는 NULL 로 떨어져 행이 빠지지만 `IS NOT` 은 잡는다(R3-03)."""
    krx = _krx(tmp_path)
    con = sqlite3.connect(tmp_path / "krx.db")
    con.execute("UPDATE krx_stk_isu_base_info SET LIST_SHRS=NULL "
                "WHERE bas_dd_req=? AND ISU_SRT_CD='000003'", (D,))
    con.commit(); con.close()
    rep = lh.run(D, _paths(tmp_path, krx=krx))
    c = _by(rep)["krx.corp_action_candidates"]
    assert c.status is lh.Status.FAIL
    assert [i["code"] for i in c.value["items"]] == ["000003"]
    assert c.value["items"][0]["shares_ratio"] is None


def test_KRX_지수가_추가돼도_행수_검사는_통과한다(tmp_path) -> None:
    """09-12 08:10 실전: KRX 가 09-11 에 '코스피 200 25/50' 계열 지수 3개를 추가해 코스피 지수 행이 51→54 가 됐고
    등호 검사가 원장 건전성을 FAIL 시켜 확정 빌드가 막혔다. 지수 행수는 하한이다 — 줄어드는 쪽만 위험하다."""
    krx = _krx(tmp_path, kospi=54)
    rep = lh.run(D, _paths(tmp_path, krx=krx))
    assert _by(rep)["krx.rows"].status is lh.Status.PASS
    krx = _krx(tmp_path, kospi=50)
    rep = lh.run(D, _paths(tmp_path, krx=krx))
    assert _by(rep)["krx.rows"].status is lh.Status.FAIL
