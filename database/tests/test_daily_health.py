"""daily.ledger_health — 원장별 완료 판정(실측 기대치)과 등급별 rc. 플랜 P1 Task 1.7."""
import ast
import json
import re
import sqlite3
import zlib
from pathlib import Path

import pytest
from daily import ledger_health as lh
from wics_snapshot import L1, L2

D, DP = "20260908", "20260907"          # 대상일(화), 직전 거래일(월)
RC = lh.req_covered_on("2026-09-08")  # 그 날 런의 커버 종목당 요청 수(재무 추가 전 = 15)


# 필수 표의 기준 원천(모델이 쓰는 원천, K1-7b). 표와 실제 REQUIRED 검사가 어긋나지 않는지 아래 테스트가 대조한다.
_MODEL_SOURCES = ("krx", "kiwoom", "wise", "dart")


def _only(*keep):
    """keep 밖의 모델 원천은 `--skip` 으로 뺀다 — 필수 검사 누락 = 실패(K1-7c) 뒤 일부 원천만 보는 테스트용."""
    return frozenset(_MODEL_SOURCES) - set(keep)


def _cal(tmp_path, holidays=()):
    p = tmp_path / "kis_holidays_2026.json"
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


def _kw(tmp_path, *, n=2563, stale=250, cross_bad=0, vol_bad=0):
    con = sqlite3.connect(tmp_path / "kiwoom.db")
    for tbl in ("ka10008_foreign_holdings", "ka10060_investor_flows", "ka20068_lending_balance", "ka10014_short_selling"):
        con.execute(f"CREATE TABLE {tbl} (ticker TEXT, dt TEXT, poss_stkcnt TEXT, close_pric TEXT, trde_qty TEXT)")
    rows_today, rows_prev = [], []
    for i in range(n):
        tk = f"{i:06d}"
        prev = "100"
        today = "100" if i < stale else "101"
        close = "-1000" if i >= cross_bad else "-999"
        vol = "10" if i >= vol_bad else "11"
        rows_today.append((tk, D, today, close, vol)); rows_prev.append((tk, DP, prev, "-1000", "10"))
    for tbl in ("ka10008_foreign_holdings", "ka10060_investor_flows", "ka20068_lending_balance"):
        con.executemany(f"INSERT INTO {tbl} VALUES (?,?,?,?,?)", rows_today + rows_prev)
    con.executemany("INSERT INTO ka10014_short_selling VALUES (?,?,?,?,?)", rows_today[:2200] + rows_prev[:2250])
    con.execute("CREATE TABLE ka10099_stock_master (snap_date TEXT, mrkt_tp TEXT, code TEXT)")
    con.executemany("INSERT INTO ka10099_stock_master VALUES (?,?,?)",
                    [(D, "0" if i < 2486 else "10", f"{i:06d}") for i in range(4308)])
    con.commit(); con.close()
    return str(tmp_path / "kiwoom.db")


def _kis(tmp_path, max_deal, n=2500):
    """신용잔고 원장 — `deal_date` 최신일만 바꿔 신선도 게이트를 본다."""
    con = sqlite3.connect(tmp_path / "kis.db")
    con.execute("CREATE TABLE kis_credit_balance (row_hash TEXT, req_ticker TEXT, deal_date TEXT, "
                "dup_seq TEXT, collected_at TEXT)")
    con.executemany("INSERT INTO kis_credit_balance VALUES (?,?,?,?,?)",
                    [(f"h{i}", f"{i:06d}", max_deal, "0", "t") for i in range(n)])
    con.commit(); con.close()
    return str(tmp_path / "kis.db")


def _paths(tmp_path, **kw):
    p = lh.Paths(krx=kw.get("krx", str(tmp_path / "none1.db")), kiwoom=kw.get("kiwoom", str(tmp_path / "none2.db")),
                 kis=kw.get("kis", str(tmp_path / "none3.db")), dart=kw.get("dart", str(tmp_path / "none4.db")), wise=kw.get("wise", str(tmp_path / "none5.db")),
                 wiseindex=kw.get("wiseindex", ""),
                 calendar=_cal(tmp_path, kw.get("holidays", ())),
                 universe_state=str(tmp_path / "universe_kw.json"))
    (tmp_path / "universe_kw.json").write_text(json.dumps({"asof": D, "grace": {}, "n_requested": 2563}), encoding="utf-8")
    return p


def _by(report):
    return {c.name: c for c in report.checks}


def test_krx_all_ok_passes(tmp_path):
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path)), skip=_only("krx"))
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
    rep = lh.run(D, _paths(tmp_path, krx=krx), skip=_only("krx"))
    c = _by(rep)["krx.corp_action_candidates"]
    # 기록형이다 — 한국 시장에서 전환·증자·소각은 매일 몇 건씩 나므로 "0건 기대" 는 매일 FAIL 이고
    # 사람은 곧 무시한다(DEFECT-A09). 대조는 equity 의 adj_factor·corp_event 가 매일 한다.
    assert c.level is lh.Level.WARN and c.status is lh.Status.PASS
    assert "후보 2건 기록" in c.detail
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


# ── 반대 방향: 달력상 휴장일에 KRX 시세 (K1-9 ②) ─────────────────────────────────────
_KRX_EPS = ("sto/stk_bydd_trd", "sto/ksq_bydd_trd", "sto/stk_isu_base_info", "sto/ksq_isu_base_info",
            "idx/kospi_dd_trd", "idx/kosdaq_dd_trd", "etp/etf_bydd_trd")


def _krx_rows_on(krx_db: str, bas_dd: str, status: str = "ok", n_rows: int = 942,
                 eps: tuple[str, ...] = _KRX_EPS) -> None:
    """D 밖의 날짜 하나에 KRX 엔드포인트 ingest_log 행을 더한다(08:10 재수집 창 안의 날)."""
    con = sqlite3.connect(krx_db)
    con.executemany("INSERT INTO ingest_log VALUES (?,?,?,?,NULL,'t')",
                    [(e, bas_dd, n_rows if status == "ok" else 0, status) for e in eps])
    con.commit(); con.close()


def test_krx_rows_on_a_calendar_holiday_halt(tmp_path):
    """달력이 휴장이라 한 평일(09-03)에 KRX 가 시세를 줬다 = 달력이 틀렸다 → 중단.

    옛 게이트는 한 방향(KRX '휴장' ↔ 달력 거래일, krx.holiday_misfire)뿐이라 이 날을 보지 못했다.
    """
    krx = _krx(tmp_path)
    _krx_rows_on(krx, "20260903")
    rep = lh.run(D, _paths(tmp_path, krx=krx, holidays=("20260903",)))
    c = _by(rep)["krx.holiday_traded"]
    assert c.level is lh.Level.HALT and c.status is lh.Status.FAIL
    assert c.value == ["20260903"] and "20260903" in c.detail
    assert not rep.ok


def test_krx_holiday_rows_on_a_calendar_holiday_pass(tmp_path):
    krx = _krx(tmp_path)
    _krx_rows_on(krx, "20260903", status="holiday")          # 휴장일의 빈 응답은 정상
    _krx_rows_on(krx, "20260904")                            # 달력상 거래일의 ok 행도 정상
    rep = lh.run(D, _paths(tmp_path, krx=krx, holidays=("20260903",)), skip=_only("krx"))
    assert _by(rep)["krx.holiday_traded"].status is lh.Status.PASS and rep.ok


def test_base_info_rows_on_a_holiday_are_not_price(tmp_path):
    """명세는 '휴장일에 KRX **시세**' — 종목기본정보 행만 있는 휴장일은 HALT 가 아니다(하-4)."""
    krx = _krx(tmp_path)
    _krx_rows_on(krx, "20260903", eps=("sto/stk_isu_base_info", "sto/ksq_isu_base_info"))
    rep = lh.run(D, _paths(tmp_path, krx=krx, holidays=("20260903",)))
    assert _by(rep)["krx.holiday_traded"].status is lh.Status.PASS


def test_price_endpoints_are_backfill_krx_endpoints():
    """시세 엔드포인트 목록이 정본(`backfill_krx.EPS`)에서 어긋나지 않는지 — import 없이 소스를 읽는다."""
    tree = ast.parse((Path(__file__).resolve().parents[1] / "src" / "backfill_krx.py").read_text(encoding="utf-8"))
    eps = next(ast.literal_eval(n.value) for n in tree.body
               if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "EPS" for t in n.targets))
    paths = {e[0] for e in eps}
    assert set(lh.KRX_PRICE_ENDPOINTS) <= paths
    assert paths - set(lh.KRX_PRICE_ENDPOINTS) == {"sto/stk_isu_base_info", "sto/ksq_isu_base_info"}


def test_recheck_sessions_match_daily_build_krx_step():
    """② 의 창 폭 = 08:10 krx_step 재수집 폭(`prev_trading_day(..., n=10)`) — 한쪽만 바뀌면 창이 어긋난다."""
    sh = (Path(__file__).resolve().parents[1] / "scripts" / "daily_build.sh").read_text(encoding="utf-8")
    step = sh[sh.index("krx_step() {"):sh.index("for attempt in")]
    assert re.findall(r"prev_trading_day\(.*, n=(\d+)\)", step) == [str(lh.KRX_RECHECK_SESSIONS)]


def test_krx_rows_on_a_holiday_outside_the_recheck_window_are_not_judged(tmp_path):
    # 판정 창 = 08:10 KRX 재수집 창(직전 10거래일 ~ D). 그 밖의 옛 기록은 이 게이트가 보지 않는다.
    krx = _krx(tmp_path)
    _krx_rows_on(krx, "20260803")
    rep = lh.run(D, _paths(tmp_path, krx=krx, holidays=("20260803",)))
    assert _by(rep)["krx.holiday_traded"].status is lh.Status.PASS


def test_kiwoom_relative_gates_and_cross_source(tmp_path):
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path, stk=2563, ksq=1820), kiwoom=_kw(tmp_path)))
    c = _by(rep)
    assert c["kiwoom.ka10008.rows"].status is lh.Status.PASS          # 2563/2563
    assert c["kiwoom.ka10008.stale_pct"].value == 9.8 and c["kiwoom.ka10008.stale_pct"].status is lh.Status.PASS
    assert c["kiwoom.krx_cross"].status is lh.Status.PASS
    assert c["kiwoom.master"].status is lh.Status.PASS
    assert c["kiwoom.ka10014.trend"].status is lh.Status.PASS          # 2200/2250 = 0.98


def test_kiwoom_contamination_and_cross_mismatch_fail(tmp_path):
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path, stk=2563), kiwoom=_kw(tmp_path, stale=2540, vol_bad=1)))
    c = _by(rep)
    assert c["kiwoom.ka10008.stale_pct"].status is lh.Status.FAIL     # 99.1%
    assert c["kiwoom.krx_cross"].status is lh.Status.FAIL and c["kiwoom.krx_cross"].value["same_vol"] == 2562


def test_kiwoom_cross_close_mismatch_is_recorded_only(tmp_path):
    # 09-14 애프터마켓 뒤 키움 종가는 장후 체결가 — 종가만 다르면 PASS, 수는 기록(결정 11)
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path, stk=2563), kiwoom=_kw(tmp_path, cross_bad=1)))
    c = _by(rep)
    assert c["kiwoom.krx_cross"].status is lh.Status.PASS
    assert c["kiwoom.krx_cross"].value == {"matched": 2563, "same_close": 2562, "same_vol": 2563}


def test_report_json_roundtrip(tmp_path):
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path)), skip=_only("krx"))
    path = lh.write_report(rep, str(tmp_path / "health"))
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    first = next(c for c in data["checks"] if c["name"] == "krx.ingest_log")
    assert data["ok"] is True and first["level"] == "required" and "OK" in data["summary"]


def test_skip_source_excludes_its_checks(tmp_path):
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path, stk=2563), kiwoom=_kw(tmp_path, stale=2540)),
                 skip=_only("krx"))
    names = {c.name for c in rep.checks}
    assert "kiwoom.skipped" in names and not any(n.startswith("kiwoom.ka") for n in names)
    assert rep.ok                                                     # 오염 99% 픽스처인데 kiwoom 을 제외했으니 통과


# WISE 스냅샷 시각 3종 — (run_at UTC, checked_at UTC, fetched_date KST). 대상일 D=20260908 기준.
_WISE_SNAPSHOT = {
    "evening": ("2026-09-08T09:05:00", "2026-09-08T09:05:30", "2026-09-08"),       # D 18:05 KST (V2-3)
    "next_morning": ("2026-09-08T21:04:00", "2026-09-08T21:04:30", "2026-09-09"),  # D+1 06:04 KST (옛 방식)
    "stale": ("2026-09-04T09:05:00", "2026-09-04T09:05:30", "2026-09-04"),         # D-4 저녁 — 두 기준 다 아님
}


# 종목 1건의 일일 요청 ep 목록 — `backfill_wise.collect` 순서(커버 15 · 무커버 4). 커버 종목에만 cF3002 가 있다.
_EPS_COVERED = (["c1050001_data"] + ["cF5001"] * 3 + ["cF5002"] * 3 + ["c1050001_data"] * 2
                + ["c1050001_data"] * 3 + ["c1010001", "cF3002", "cF4002"])
_EPS_NONE = ["c1050001_data"] + ["cF5001"] * 3


def _wise(tmp_path, *, cov=804, none=1759, n_req=None, raw_rows=None, raw_stocks=None, snapshot="evening",
          call_log=True):
    """ws_run_log 1건 + 같은 런의 checked_at 커버리지 + ws_call_log + ws_raw(기본은 항등식대로).

    n_req 기본값은 항등식대로. `call_log=False` 는 호출 원장이 없던 옛 판(폴백 경로) 재현용.
    """
    n_req = n_req if n_req is not None else cov * RC + none * lh.REQ_NONE
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
    if call_log:
        con.execute("CREATE TABLE ws_call_log (ts TEXT NOT NULL, cmp_cd TEXT, ep TEXT, pkey TEXT, "
                    "status TEXT NOT NULL, bytes INTEGER, ms INTEGER)")
        con.executemany("INSERT INTO ws_call_log VALUES (?,?,?,?,'ok',100,10)",
                        [(checked_at, f"{i:06d}", ep, str(k))
                         for i in range(cov + none)
                         for k, ep in enumerate(_EPS_COVERED if i < cov else _EPS_NONE)])
    con.commit(); con.close()
    return str(tmp_path / "wise.db")


def test_wise_request_identity_counts_four_requests_per_uncovered_stock(tmp_path):
    # 검수 D H1 후속: 무커버 판정이 3개년 cF5001 을 다 본 뒤에만 나므로 무커버 종목은 4콜(목록 1 + cF5001 3)이다.
    rep = lh.run(D, _paths(tmp_path, wise=_wise(tmp_path)))
    ident = next(c for c in rep.checks if c.name == "wise.req_identity")
    assert ident.status is lh.Status.PASS and ident.value["expected"] == 804 * RC + 1759 * lh.REQ_NONE
    sub = tmp_path / "b"; sub.mkdir()
    rep2 = lh.run(D, _paths(sub, wise=_wise(sub, n_req=804 * RC + 1759 * 2)))
    assert next(c for c in rep2.checks if c.name == "wise.req_identity").status is lh.Status.FAIL


def test_wise_identity_survives_coverage_overwrite(tmp_path):
    """DQ-9(09-26 실측): `ws_coverage` 는 cmp_cd PK + INSERT OR REPLACE 라 나중 런이 `checked_at` 을 전건
    덮어쓴다 — 토요일 수동 full 뒤 `--date 20260923` 재판정이 covered 0·none 0 으로 FAIL 했다. 항등식 입력은
    append-only 호출 원장(`ws_call_log`)이어야 과거일 재판정이 살아남는다."""
    wise = _wise(tmp_path)
    con = sqlite3.connect(tmp_path / "wise.db")
    con.execute("UPDATE ws_coverage SET checked_at=?", ("2026-09-09T09:05:30",))   # 다음날 런이 전건 덮어씀
    con.commit(); con.close()
    c = _by(lh.run(D, _paths(tmp_path, wise=wise)))
    ident = c["wise.req_identity"]
    assert ident.status is lh.Status.PASS, ident
    assert ident.value["source"] == "call_log"
    assert ident.value["covered"] == 804 and ident.value["none"] == 1759
    assert c["wise.raw"].status is lh.Status.PASS
    assert c["wise.cov_rate"].value == round(804 / 2563, 3)


def test_wise_identity_falls_back_to_coverage_without_call_log(tmp_path):
    """전환기 호환: 호출 원장이 없는 원장 사본은 종전대로 `ws_coverage.checked_at` 에서 센다."""
    c = _by(lh.run(D, _paths(tmp_path, wise=_wise(tmp_path, call_log=False))))
    ident = c["wise.req_identity"]
    assert ident.status is lh.Status.PASS and ident.value["source"] == "coverage"
    assert ident.value["covered"] == 804 and ident.value["none"] == 1759
    assert c["wise.raw"].status is lh.Status.PASS


def test_wise_raw_expectation_derives_from_coverage_not_a_fixed_band(tmp_path):
    # 09-11 실측: 규모구분 갱신으로 유니버스 2,563→2,610, 무커버 4콜 → rows 19,416. 옛 절대 밴드(15,500~15,700 ·
    # 2,560~2,570)는 오탐. 기대치 = covered×REQ_COVERED + none×REQ_NONE · stocks = covered+none.
    rep = lh.run(D, _paths(tmp_path, wise=_wise(tmp_path, cov=816, none=1794)))
    raw = next(c for c in rep.checks if c.name == "wise.raw")
    assert raw.status is lh.Status.PASS and raw.value == {"rows": 816 * RC + 1794 * lh.REQ_NONE, "stocks": 2610}
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


# ── A-03: 같은 날 런 여럿(중단 뒤 재실행·실패 재수집)은 합쳐 본다 ──────────────────────────
_NONE_KEYS = [("c1050001_data", "")] + [("cF5001", y) for y in ("202612", "202712", "202812")]


def _calls(ts, tk, status):
    """무커버 종목 1회 수집(4콜). 두 번째 키(cF5001 202612)만 status, 나머지는 ok."""
    return [(ts, tk, ep, pk, status if (ep, pk) == _NONE_KEYS[1] else "ok")
            for ep, pk in _NONE_KEYS]


@pytest.mark.parametrize(("first", "second", "want"), [
    ("http500", "ok", lh.Status.PASS),       # 뒤 런이 같은 키를 ok 로 다시 받았다 → 회복
    ("ok", "http500", lh.Status.FAIL),       # 재수집에서 새로 실패 — 마지막 호출이 실패면 미회복
    ("http500", "http500", lh.Status.FAIL),
])
def test_wise_failure_recovers_only_when_the_keys_last_call_is_ok(tmp_path, first, second, want):
    """1차 런: 무커버 000001·000002(4콜씩), 000001 의 한 키가 first. 같은 날 재실행이 000001 만 다시
    받아 그 키가 second. 그날 런을 합쳐 보고, 실패는 같은 (종목, ep, pkey) 의 마지막 호출이 ok 면
    회복이다. 항등식 실제값은 고유 키 수(8) — 종전엔 마지막 런 n_req(4)를 그날 원장(8)과
    비교해 FAIL 했다."""
    r1, r2 = "2026-09-08T09:05:00", "2026-09-08T09:40:00"
    calls = _calls(r1, "000001", first) + _calls(r1, "000002", "ok") + _calls(r2, "000001", second)
    con = sqlite3.connect(tmp_path / "wise.db")
    con.execute("CREATE TABLE ws_run_log (run_at TEXT, mode TEXT, n_stocks INTEGER, n_req INTEGER, "
                "n_ok INTEGER, n_bad INTEGER, bad_summary TEXT)")
    con.executemany("INSERT INTO ws_run_log VALUES (?,'full',?,?,?,0,'{}')",
                    [(r1, 2, 8, 7 + (first == "ok")), (r2, 1, 4, 3 + (second == "ok"))])
    con.execute("CREATE TABLE ws_call_log (ts TEXT NOT NULL, cmp_cd TEXT, ep TEXT, pkey TEXT, "
                "status TEXT NOT NULL, bytes INTEGER, ms INTEGER)")
    con.executemany("INSERT INTO ws_call_log VALUES (?,?,?,?,?,1,1)", calls)
    con.commit()
    con.close()
    c = _by(lh.run(D, _paths(tmp_path, wise=str(tmp_path / "wise.db"))))
    assert c["wise.run"].status is want, c["wise.run"]
    assert c["wise.req_identity"].status is lh.Status.PASS, c["wise.req_identity"]
    assert c["wise.req_identity"].value["actual"] == 8


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
    assert c.status is lh.Status.PASS                       # 기록형(A09) — 목록에 남기는 것이 판정이다
    assert [i["code"] for i in c.value["items"]] == ["000003"]
    assert c.value["items"][0]["shares_ratio"] is None


def test_KRX_지수가_추가돼도_행수_검사는_통과한다(tmp_path) -> None:
    """09-12 08:10 실전: KRX 가 09-11 에 '코스피 200 25/50' 계열 지수 3개를 추가해 코스피 지수 행이 51→54 가 됐고
    등호 검사가 원장 건전성을 FAIL 시켜 확정 빌드가 막혔다. 지수 행수는 하한이다 — 줄어드는 쪽만 위험하다."""
    krx = _krx(tmp_path, kospi=54)
    rep = lh.run(D, _paths(tmp_path, krx=krx))
    assert _by(rep)["krx.rows"].status is lh.Status.PASS


def test_KRX_지수가_줄면_행수_검사는_실패한다(tmp_path) -> None:
    krx = _krx(tmp_path, kospi=50)
    rep = lh.run(D, _paths(tmp_path, krx=krx))
    assert _by(rep)["krx.rows"].status is lh.Status.FAIL


# ── KIS 신용잔고 신선도 (DEFECT-A06·E01) ────────────────────────────────────
def test_kis_credit_fresh_passes_at_d_minus_2(tmp_path):
    rep = lh.run(D, _paths(tmp_path, kis=_kis(tmp_path, "20260904")), skip=_only())   # D-2 세션 = 정상
    c = _by(rep)["kis.credit.fresh"]
    assert c.level is lh.Level.REQUIRED and c.status is lh.Status.PASS and rep.ok


def test_kis_credit_fresh_warns_one_session_behind(tmp_path):
    rep = lh.run(D, _paths(tmp_path, kis=_kis(tmp_path, "20260903")), skip=_only())   # D-3 — 하루 밀렸다
    c = _by(rep)["kis.credit.fresh"]
    assert c.level is lh.Level.WARN and c.status is lh.Status.FAIL
    assert rep.ok                                                        # 경고는 체인을 세우지 않는다


def test_kis_credit_fresh_fails_when_two_sessions_behind(tmp_path):
    rep = lh.run(D, _paths(tmp_path, kis=_kis(tmp_path, "20260902")))   # D-4 — 무음 정지
    c = _by(rep)["kis.credit.fresh"]
    assert c.level is lh.Level.REQUIRED and c.status is lh.Status.FAIL
    assert not rep.ok


# ── KIS 신용잔고 행수 — 수집기와 같은 기대 집합 판정 (플랜 2026-09-30 T-K2) ──────
def _kis_hist(tmp_path, n_got, n_expected=2500):
    """D-2(20260904) 직전 5세션에 `n_expected` 종목, D-2 당일엔 앞 `n_got` 종목."""
    con = sqlite3.connect(tmp_path / "kis.db")
    con.execute("CREATE TABLE kis_credit_balance (row_hash TEXT, req_ticker TEXT, deal_date TEXT, "
                "dup_seq TEXT, collected_at TEXT)")
    hist = ("20260828", "20260831", "20260901", "20260902", "20260903")
    con.executemany("INSERT INTO kis_credit_balance VALUES (?,?,?,?,?)",
                    [(f"h{d}{i}", f"{i:06d}", d, "0", "t") for d in hist for i in range(n_expected)]
                    + [(f"g{i}", f"{i:06d}", "20260904", "0", "t") for i in range(n_got)])
    con.commit(); con.close()
    return str(tmp_path / "kis.db")


def test_kis_credit_rows_ignore_requested_tickers_that_never_have_credit(tmp_path):
    # 요청 2,563 중 신용잔고가 있는 2,500 이 전부 왔다 — 옛 판정(행/요청)은 0.9754 였다
    rep = lh.run(D, _paths(tmp_path, kis=_kis_hist(tmp_path, 2500)))
    c = _by(rep)["kis.credit.rows"]
    assert c.status is lh.Status.PASS
    assert c.value == {"expected": 2500, "got": 2500, "ratio": 1.0, "distinct_ok": True, "requested": 2563}


def test_kis_credit_rows_warn_when_three_percent_of_expected_is_missing(tmp_path):
    rep = lh.run(D, _paths(tmp_path, kis=_kis_hist(tmp_path, 2425)), skip=_only())
    c = _by(rep)["kis.credit.rows"]
    assert c.level is lh.Level.WARN and c.status is lh.Status.FAIL
    assert "판정일 행 부족" in c.detail and rep.ok


# ── WICS 주간 원장 (플랜 wics-weekly T1) ────────────────────────────────────


def _wics(tmp_path, *, dt_="20260904", n=2500, missing=(), l1_mismatch=False):
    """L1 10 × L2 28 스냅샷. 종목 i 는 L1 = i % 10 번째, 그 안에서 L2 라운드로빈. KRX 픽스처(0~2761)와 겹친다."""
    con = sqlite3.connect(tmp_path / "wiseindex.db")
    con.execute("CREATE TABLE wics_raw (dt TEXT, sec_cd TEXT, collected_at TEXT, http_status INTEGER, n_rows INTEGER, body BLOB)")
    by_l1 = {c: [] for c in L1}
    for i in range(n):
        by_l1[L1[i % 10]].append(f"{i:06d}")
    l2_by_l1 = {c: [l for l in L2 if l.startswith(c)] for c in L1}
    bodies = {c: list(by_l1[c]) for c in L1}
    for c in L1:                                                       # L1 의 종목을 그 L1 의 L2 들에 라운드로빈
        for j, l in enumerate(l2_by_l1[c]):
            bodies[l] = [tk for k, tk in enumerate(by_l1[c]) if k % len(l2_by_l1[c]) == j]
    if l1_mismatch:
        bodies["G4510"] = bodies["G4510"][:-1]                          # G45 의 L2 합이 L1 보다 1 작다
    for code, tickers in bodies.items():
        if code in missing:
            continue
        rows = [{"CMP_CD": t, "CMP_KOR": "x", "MKT_VAL": 1, "IDX_CD": code, "IDX_NM_KOR": "n", "SEC_CD": code[:3], "SEC_NM_KOR": "s"} for t in tickers]
        body = zlib.compress(json.dumps({"info": {"CNT": len(rows)}, "sector": [], "list": rows}).encode("utf-8"))
        con.execute("INSERT INTO wics_raw VALUES (?,?,?,?,?,?)", (dt_, code, "2026-09-05T18:00:00", 200, len(rows), body))
    con.commit(); con.close()
    return str(tmp_path / "wiseindex.db")


def test_wics_latest_snapshot_passes_integrity_freshness_and_coverage(tmp_path):
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path), wiseindex=_wics(tmp_path)))     # dt 09-04(금), D 09-08(화) → 4일
    c = _by(rep)
    assert c["wics.integrity"].status is lh.Status.PASS and c["wics.integrity"].value["missing"] == []
    assert c["wics.fresh"].status is lh.Status.PASS and c["wics.fresh"].value["age_days"] == 4
    assert c["wics.coverage"].status is lh.Status.PASS and c["wics.coverage"].value["ratio"] >= 0.85   # 2,500/2,762


def test_wics_missing_code_or_l1_mismatch_fails_integrity_only(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    rep = lh.run(D, _paths(tmp_path, krx=_krx(a), wiseindex=_wics(a, missing=("G5510",))))
    c = _by(rep)
    assert c["wics.integrity"].status is lh.Status.FAIL and c["wics.integrity"].value["missing"] == ["G5510"]
    assert c["wics.integrity"].level is lh.Level.REQUIRED and not rep.ok
    rep = lh.run(D, _paths(tmp_path, krx=_krx(b), wiseindex=_wics(b, l1_mismatch=True)))
    c = _by(rep)
    assert c["wics.integrity"].status is lh.Status.FAIL and c["wics.integrity"].value["l1_mismatch"] == ["G45"]


def test_wics_stale_or_thin_snapshot_only_warns(tmp_path):
    """주간 축을 일일 건전성이 FAIL 로 보면 안 된다 — 나이·커버리지는 WARN 등급."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    rep = lh.run(D, _paths(tmp_path, krx=_krx(a), wiseindex=_wics(a, dt_="20260821", n=200)),
                 skip=_only("krx"))                                                    # 18일 · 200/2,762
    c = _by(rep)
    assert c["wics.fresh"].status is lh.Status.FAIL and c["wics.fresh"].level is lh.Level.WARN
    assert c["wics.coverage"].status is lh.Status.FAIL and c["wics.coverage"].level is lh.Level.WARN
    assert c["wics.integrity"].status is lh.Status.PASS and rep.ok                     # WARN 은 ok 를 깨지 않는다
    rep = lh.run(D, _paths(tmp_path, krx=_krx(b), wiseindex=_wics(b)), skip=frozenset({"wics"}))
    assert "wics.integrity" not in _by(rep) and _by(rep)["wics.skipped"].status is lh.Status.SKIP



def test_wise_request_budget_follows_the_run_date():
    """재무 추가 3콜(10-01 수집부터) 전 런은 15콜 — 전환 다음 날 아침에 전날(09-30) 런을 18 로 판정하면 거짓 FAIL."""
    assert lh.req_covered_on("2026-09-30") == 15
    assert lh.req_covered_on("2026-10-01") == 18


# ── K1-7b: 필수 검사 SKIP = 실패(N-42 Q4) ──────────────────────────────────────────


def _dart(tmp_path, n=410):
    """공시 목록 D 행 n 건 + 빈 문서 저장소(문서 대상 0 → dart.docs 통과)."""
    con = sqlite3.connect(tmp_path / "dart.db")
    con.execute("CREATE TABLE dart_disclosure (rcept_no TEXT, rcept_dt TEXT, stock_code TEXT, report_nm TEXT)")
    con.executemany("INSERT INTO dart_disclosure VALUES (?,?,?,?)",
                    [(f"{D}{i:06d}", D, f"{i:06d}", "임원ㆍ주요주주특정증권등소유상황보고서") for i in range(n)])
    con.execute("CREATE TABLE doc_store (rcept_no TEXT, zip_ok INTEGER, http_status TEXT)")
    con.commit(); con.close()
    return str(tmp_path / "dart.db")


def _skip_rows(tmp_path):
    """요청 유니버스 크기 미상(상태 파일 n_requested 0) — 키움 행수 3검사가 비율을 못 낸다."""
    p = _paths(tmp_path, krx=_krx(tmp_path, stk=2563), kiwoom=_kw(tmp_path))
    (tmp_path / "universe_kw.json").write_text(json.dumps({"asof": D, "grace": {}, "n_requested": 0}),
                                               encoding="utf-8")
    return p


def _skip_stale(tmp_path):
    """ka10008 직전 거래일 행이 없다 — D·D-1 겹침 0 이라 오염률을 못 낸다."""
    kw = _kw(tmp_path)
    con = sqlite3.connect(kw)
    con.execute("DELETE FROM ka10008_foreign_holdings WHERE dt=?", (DP,))
    con.commit(); con.close()
    return _paths(tmp_path, krx=_krx(tmp_path, stk=2563), kiwoom=kw)


def _skip_cross(tmp_path):
    """KRX 행수는 정상인데 키움 티커와 맞는 종목이 0 — 교차 대조 matched 0."""
    krx = _krx(tmp_path, stk=2563)
    con = sqlite3.connect(krx)
    for tbl in ("krx_stk_bydd_trd", "krx_ksq_bydd_trd"):
        con.execute(f"UPDATE {tbl} SET ISU_CD = 'X' || ISU_CD")
    con.commit(); con.close()
    return _paths(tmp_path, krx=krx, kiwoom=_kw(tmp_path))


@pytest.mark.parametrize(("make", "names"), [
    (_skip_rows, ("kiwoom.ka10008.rows", "kiwoom.ka10060.rows", "kiwoom.ka20068.rows")),
    (_skip_stale, ("kiwoom.ka10008.stale_pct",)),
    (_skip_cross, ("kiwoom.krx_cross",)),
])
def test_required_skip_without_skip_record_fails(tmp_path, make, names):
    """필수 검사가 판정을 못 하면(SKIP) 실패다 — 종전엔 ok 가 FAIL 만 봐서 SKIP 이 통과로 집계됐다
    (DECISIONS §6-6 · RM K1-7). 리포트 JSON 의 status 도 fail 이어야 일일 리포트·워치독이 crit 로 센다."""
    rep = lh.run(D, make(tmp_path), skip=_only("krx", "kiwoom"))
    c = _by(rep)
    for n in names:
        assert c[n].level is lh.Level.REQUIRED and c[n].status is lh.Status.FAIL, c[n]
        assert "--skip kiwoom" in c[n].detail and "K1-7b" in c[n].detail
    assert {x.name for x in rep.failed_required} == set(names)
    assert not rep.ok and "K1-7b" in rep.summary()
    data = json.loads(Path(lh.write_report(rep, str(tmp_path / "health"))).read_text(encoding="utf-8"))
    assert data["ok"] is False
    assert {x["name"] for x in data["checks"] if x["level"] == "required" and x["status"] == "fail"} == set(names)


def test_required_skip_with_skip_record_passes_and_is_recorded(tmp_path):
    """운영자가 `--skip kiwoom` 으로 뺐으면 통과 — 그 기록(`kiwoom.skipped`)이 리포트에 남는다."""
    rep = lh.run(D, _skip_rows(tmp_path), skip=_only("krx"))
    assert rep.ok
    data = json.loads(Path(lh.write_report(rep, str(tmp_path / "health"))).read_text(encoding="utf-8"))
    assert data["ok"] is True
    rec = [x for x in data["checks"] if x["name"] == "kiwoom.skipped"]
    assert len(rec) == 1 and rec[0]["status"] == "skip" and "--skip" in rec[0]["expected"]
    assert not any(x["name"].startswith("kiwoom.ka") for x in data["checks"])


def test_non_required_skip_still_passes(tmp_path):
    """필수 표 밖의 SKIP 은 종전대로 통과 — WARN(기업행위 후보: 직전일 기본정보 없음) · REQUIRED 지만
    기준 원천 밖(WICS 첫 스냅샷 전 `wics.raw`) · HALT 기준선(KIS 중복쌍: 전날 리포트 없음)."""
    wics = tmp_path / "wiseindex.db"
    con = sqlite3.connect(wics)
    con.execute("CREATE TABLE other (a TEXT)")
    con.commit(); con.close()
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path, base_prev=False), kis=_kis(tmp_path, "20260904"),
                           wiseindex=str(wics)), skip=_only("krx"))
    c = _by(rep)
    assert c["krx.corp_action_candidates"].status is lh.Status.SKIP
    assert c["wics.raw"].level is lh.Level.REQUIRED and c["wics.raw"].status is lh.Status.SKIP
    assert c["kis.credit.dup_growth"].status is lh.Status.SKIP
    assert rep.ok


def test_required_table_is_exactly_the_required_checks_of_model_sources(tmp_path):
    """필수 표(SoT)는 모델 원천 4개가 내는 REQUIRED 검사 이름과 정확히 같다 — 이름이 바뀌어 표가 조용히
    무력화되거나, 새 REQUIRED 검사가 표 밖에서 SKIP = 통과로 남지 않게 한다. 항목마다 근거가 있다."""
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path, stk=2563), kiwoom=_kw(tmp_path),
                           wise=_wise(tmp_path), dart=_dart(tmp_path)))
    assert rep.ok                                   # 정상 픽스처 — 모든 검사가 실제로 판정됐다
    emitted = {c.name for c in rep.checks
               if c.level is lh.Level.REQUIRED and c.name.split(".", 1)[0] in _MODEL_SOURCES}
    assert set(lh.REQUIRED_NO_SKIP) == emitted
    assert all(isinstance(v, str) and v.strip() for v in lh.REQUIRED_NO_SKIP.values())


# ── K1-7c: 필수 검사가 아예 실행되지 않으면 실패 ─────────────────────────────────────────
def _full(tmp_path, **kw):
    """모델 원천 4개가 모두 정상인 경로. `kw` 로 원천 경로를 바꿔 끼운다(없는 파일 = 그 원장 부재)."""
    base = {"krx": lambda: _krx(tmp_path, stk=2563), "kiwoom": lambda: _kw(tmp_path),
            "wise": lambda: _wise(tmp_path), "dart": lambda: _dart(tmp_path)}
    return _paths(tmp_path, **{k: kw.get(k) or make() for k, make in base.items()},
                  **{k: v for k, v in kw.items() if k not in base})


def _not_run(rep):
    return {c.name for c in rep.checks if c.status is lh.Status.FAIL and "실행되지 않음" in c.detail}


def test_missing_kiwoom_ledger_fails(tmp_path):
    """`kiwoom.db` 가 없으면 키움 검사가 하나도 안 돈다 — 종전엔 검사 0개로 ok=True 였다(K1-7b 보고 결함 2)."""
    rep = lh.run(D, _full(tmp_path, kiwoom=str(tmp_path / "absent_kiwoom.db")))
    kiwoom = {n for n in lh.REQUIRED_NO_SKIP if n.startswith("kiwoom.")}
    assert _not_run(rep) == kiwoom
    c = _by(rep)["kiwoom.ka10060.rows"]
    assert c.level is lh.Level.REQUIRED and "원장 파일 없음(absent_kiwoom.db)" in c.detail
    assert "--skip kiwoom" in c.expected and "K1-7c" in c.expected
    assert not rep.ok
    data = json.loads(Path(lh.write_report(rep, str(tmp_path / "health"))).read_text(encoding="utf-8"))
    assert {x["name"] for x in data["checks"] if x["level"] == "required" and x["status"] == "fail"} == kiwoom


def test_missing_wise_run_log_table_fails(tmp_path):
    """WISE 원장은 있는데 런 로그 표가 없으면 `check_wise` 가 빈 목록을 돌려준다 — 세 필수 검사가 FAIL 이다."""
    wise = _wise(tmp_path)
    con = sqlite3.connect(wise)
    con.execute("DROP TABLE ws_run_log")
    con.commit(); con.close()
    rep = lh.run(D, _full(tmp_path, wise=wise))
    assert _not_run(rep) == {"wise.run", "wise.req_identity", "wise.raw"}
    assert "원장 표 없음" in _by(rep)["wise.run"].detail
    assert not rep.ok


def test_skipped_source_is_not_reported_missing(tmp_path):
    """`--skip kiwoom` 이면 원장이 없어도 통과 — `kiwoom.skipped` 기록만 남는다."""
    rep = lh.run(D, _full(tmp_path, kiwoom=str(tmp_path / "absent_kiwoom.db")), skip=frozenset({"kiwoom"}))
    assert rep.ok and not _not_run(rep)
    assert _by(rep)["kiwoom.skipped"].status is lh.Status.SKIP


def test_calendar_holiday_keeps_the_old_flow(tmp_path):
    """달력상 휴장일 실행엔 누락 판정을 하지 않는다 — 그날은 키움·WISE·DART 행이 없는 것이 정상이다."""
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path, statuses="holiday"), holidays=(D,)))
    assert not _not_run(rep) and rep.ok


def test_krx_all_holiday_on_a_trading_day_is_left_to_the_halt(tmp_path):
    """거래일에 KRX 가 전부 휴장 응답 — `krx.holiday_misfire` HALT 가 세운다. 이 응답이면 시세가 없어
    `krx.rows` 가 안 나오는데, 누락 FAIL 로 겹쳐 싣지 않는다."""
    rep = lh.run(D, _paths(tmp_path, krx=_krx(tmp_path, statuses="holiday")), skip=_only("krx"))
    c = _by(rep)
    assert c["krx.holiday_misfire"].status is lh.Status.FAIL and "krx.rows" not in c
    assert not rep.failed_required and rep.halts and not rep.ok
