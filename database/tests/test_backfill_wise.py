"""backfill_wise.probe_coverage — 커버 판정은 대상 3개년을 다 본 뒤에만 none 이다.

검수 D H1(2026-09-10): 당해 연도(ymms[0]) 의 cF5001 만 보고 none 을 확정해 09-10 커버 상실 4건 중
3건(036010·190510·342870)이 오탐이었다 — FY2027 추정치가 실재했다. 연말로 갈수록 당해 연도가
먼저 비므로 오탐이 늘어난다. false-none 은 그날 스냅샷의 영구 손실이다.
"""
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import backfill_wise as bw
import pytest
from daily import ledger_health as lh
from daily.universe import kiwoom_common

YMMS = ["202612", "202712", "202812"]


def _fetch(covered_years, fail_years=()):
    calls = []

    def fetch(cmp_cd, ep, pk, url):
        calls.append((ep, pk))
        if pk in fail_years:
            return cmp_cd, "http/500", b"", 0, 1
        c1 = {"select_item": [1 if pk in covered_years else None], "target_price": [None]}
        body = json.dumps({"chart1": json.dumps(c1), "chart2": json.dumps({"select_item": [None]})}).encode()
        return cmp_cd, "ok", body, len(body), 1
    return fetch, calls


def test_later_year_coverage_counts_as_covered_and_fetches_full_set():
    fetch, calls = _fetch({"202712"})
    covered, out = bw.probe_coverage("036010", YMMS, fetch)
    assert covered
    # 판정 순서: cF5001 을 연도순으로 보다가 커버가 나오면 나머지 연도·cF5002 전부 받는다 (총 6콜 = 종전과 같다)
    assert calls == [("cF5001", "202612"), ("cF5001", "202712"), ("cF5001", "202812"),
                     ("cF5002", "202612"), ("cF5002", "202712"), ("cF5002", "202812")]
    assert len(out) == 6


def test_none_is_declared_only_after_all_three_years():
    fetch, calls = _fetch(set())
    covered, out = bw.probe_coverage("000000", YMMS, fetch)
    assert not covered
    assert calls == [("cF5001", y) for y in YMMS] and len(out) == 3


def test_probe_fetch_failure_is_treated_as_covered():
    # 오판 비용이 비대칭(false-none = 영구 손실)이라 판정 불능은 covered 로 둔다 (is_covered 와 같은 원칙)
    fetch, calls = _fetch(set(), fail_years={"202612"})
    covered, _ = bw.probe_coverage("000000", YMMS, fetch)
    assert covered
    assert calls[0] == ("cF5001", "202612") and len(calls) == 6


def test_request_budget_per_stock():
    # ledger_health.wise.req_identity 의 상수와 맞물린다: 커버 18(종전 15 + 재무 추가 3) · 무커버 4(목록 1 + cF5001 3)
    assert bw.REQ_COVERED == 15 + len(bw.FIN_REQUESTS) - 2 == 18 and bw.REQ_NONE == 4
    assert bw.req_covered_on(bw.FIN_EXT_SINCE) == 18 and bw.req_covered_on("2026-09-30") == 15


def test_fin_requests_add_quarterly_income_and_annual_bs_cf():
    """플랜 2026-09-30 T-Q2 — 분기 손익·연간 재무상태·현금흐름을 pkey 로 가른다. 기존 두 요청(pkey 'Y')은 그대로."""
    got = {(ep, pk): extra for ep, pk, extra in bw.FIN_REQUESTS}
    assert got[("cF3002", "Y")] == {"frq": "0", "rpt": "0", "frqTyp": "0"}
    assert got[("cF4002", "Y")] == {"frq": "0", "rpt": "5", "frqTyp": "0"}
    assert got[("cF3002", "Q:IS")] == {"frq": "1", "rpt": "0", "frqTyp": "1"}
    assert got[("cF3002", "Y:BS")] == {"frq": "0", "rpt": "1", "frqTyp": "0"}
    assert got[("cF3002", "Y:CF")] == {"frq": "0", "rpt": "2", "frqTyp": "0"}
    assert len(got) == len(bw.FIN_REQUESTS) == 5          # (ep, pkey) 유일 = ws_raw 키가 안 겹친다


def test_fin_screens_request_main_basis_not_consolidated_only():
    """DQ-5(2026-09-26): cF3002/cF4002 를 연결(IFRSL) 고정으로 부르면 별도만 내는 회사(예 샘씨엔에스
    252990)의 값이 전부 NULL 로 온다. 주재무제표(MAIN)로 불러야 연결 있는 회사는 연결, 없는 회사는
    별도가 온다(09-26 실측: 252990 MAIN=별도 순이익 150.6억, 005930 MAIN=연결=IFRSL 과 동일)."""
    import inspect
    assert bw.FIN_GUBUN == "MAIN"
    src = inspect.getsource(bw)
    assert '"finGubun": "IFRSL"' not in src, "cF3002/cF4002 요청이 다시 연결 고정으로 돌아갔다"
    assert src.count('"finGubun": FIN_GUBUN') == 2


def test_daily_mode_is_gone():
    """DQ-9: `--mode daily` 는 무커버 판정 종목을 영구 스킵(재프로브 없음)하는데 건전성 검사
    `wise.run` 은 mode='full' 을 요구한다 — 운영에서 쓸 수 없는 길이라 제거했다(크론은 full).
    네트워크·DB 를 타지 않고 파서만 보려고 `main()` 에서 `_parse_args` 를 뽑아 썼다."""
    with pytest.raises(SystemExit):
        bw._parse_args(["--mode", "daily"])
    assert bw._parse_args(["--mode", "full"]).mode == "full"
    assert bw._parse_args([]).mode == "full"


# ── universe() — WISE 수집 유니버스는 키움 수집과 같은 규칙이다(A-07, 10-06) ──────

def _kiwoom_master(base: Path, rows: list[tuple[str, str, str, str]]) -> Path:
    """`<base>/data/raw/kiwoom.db` 에 키움 마스터를 만든다.

    rows: (snap_date, code, upSizeName, marketName). 열은 `kiwoom_common`·`universe()` 가
    읽는 넷만 둔다(실물은 응답 필드 전부 TEXT)."""
    raw = base / "data" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(raw / "kiwoom.db")) as con:
        con.execute("CREATE TABLE ka10099_stock_master "
                    "(snap_date TEXT, code TEXT, upSizeName TEXT, marketName TEXT)")
        con.executemany("INSERT INTO ka10099_stock_master VALUES (?,?,?,?)", rows)
        con.commit()
    return raw / "kiwoom.db"


def test_universe_follows_kiwoom_rule_and_keeps_new_listing_without_size_class(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """A-07: 규모구분(upSizeName)은 상장 몇 주 뒤에야 붙는다. 옛 규칙(`upSizeName<>''` 만)은
    0039P0 매드업(07-01 상장, 코스닥, 시총 1,497억 — 원본 v3 에는 2026E 컨센서스가 있다)을
    한 번도 받지 않아 '추정치 보유' 유니버스에서 조용히 뺐다. 종목 선정은 키움 수집과 같은
    `kiwoom_common` 하나여야 한다."""
    kw = _kiwoom_master(tmp_path, [
        ("20261002", "000660", "대형주", "거래소"),   # 직전 스냅샷에만 있다 → 최신만 본다
        ("20261005", "005930", "대형주", "거래소"),   # 규모구분 있는 보통주
        ("20261005", "0039P0", "", "코스닥"),         # 신규 상장 보통주, 규모구분 없음 → 포함
        ("20261005", "005935", "", "거래소"),         # 우선주(6번째 자리 '5') → 제외
        ("20261005", "0238P0", "", "ETF"),            # ETF → 제외
    ])
    monkeypatch.setattr(bw, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(bw, "KRX", str(tmp_path / "absent.db"))   # 폴백하면 실물 대신 실패한다
    with closing(sqlite3.connect(":memory:")) as con_w:     # WISE 원장 자리 — 선정에 안 쓴다
        got = bw.universe(con_w)
    with closing(sqlite3.connect(kw)) as con_kw:
        want = kiwoom_common(con_kw).tickers
    assert "0039P0" in got and "005935" not in got
    assert got == sorted(want) == ["0039P0", "005930"]
    assert "· 유니버스 = 키움 마스터 20261005 (2종목)" in capsys.readouterr().out


@pytest.mark.parametrize("case", ["no_snapshot", "no_match", "no_file", "no_table"])
def test_universe_falls_back_to_krx_without_kiwoom_snapshot(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str], case: str) -> None:
    """키움 마스터에 스냅샷이 없거나(`kiwoom_common` 의 ValueError), 고른 종목이 0 이거나,
    파일·테이블을 못 읽으면(sqlite3.Error) 종전대로 KRX 종목기본 최신일의 보통주로 폴백한다.
    실패 안내 줄은 sqlite 오류(열기·조회 실패)일 때만 찍힌다(종전과 같다)."""
    if case == "no_snapshot":
        _kiwoom_master(tmp_path, [])
    elif case == "no_match":            # 스냅샷은 있으나 우선주·ETF 뿐 → 고른 종목 0
        _kiwoom_master(tmp_path, [("20261005", "005935", "", "거래소"),
                                  ("20261005", "0238P0", "", "ETF")])
    elif case == "no_table":            # 파일은 있으나 마스터 테이블이 없다
        (tmp_path / "data" / "raw").mkdir(parents=True)
        with closing(sqlite3.connect(tmp_path / "data" / "raw" / "kiwoom.db")) as con:
            con.execute("CREATE TABLE other (x TEXT)")
            con.commit()
    krx = tmp_path / "krx.db"
    with closing(sqlite3.connect(krx)) as con:
        for t, rows in (("krx_stk_isu_base_info", [("20260820", "005930", "보통주"),
                                                   ("20260820", "005935", "구형우선주")]),
                        ("krx_ksq_isu_base_info", [("20260820", "247540", "보통주")])):
            con.execute(f"CREATE TABLE {t} "
                        "(bas_dd_req TEXT, ISU_SRT_CD TEXT, KIND_STKCERT_TP_NM TEXT)")
            con.executemany(f"INSERT INTO {t} VALUES (?,?,?)", rows)
        con.commit()
    monkeypatch.setattr(bw, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(bw, "KRX", str(krx))
    with closing(sqlite3.connect(":memory:")) as con_w:
        assert bw.universe(con_w) == ["005930", "247540"]
    out = capsys.readouterr().out
    assert "유니버스 = 키움 마스터" not in out
    assert ("키움 마스터 조회 실패" in out) == (case in ("no_file", "no_table"))


# ── 같은 날 재실행(A-03) — 수집기 main() 을 가짜 fetch 로 돌리고 그날 wise.* 건전성을 본다 ──
# 감사 재현(2026-10-06 audit sim_wise.py)을 수집기 경로 그대로 옮겼다. 종목 수·커버 비율은
# wise.raw 하한(2,400종목)·wise.cov_rate(>= 0.25)를 실물처럼 넘기는 값이다. 그날은 10-01 뒤다.
DAY, NEXT_DAY = "2026-10-06", "2026-10-07"
TICKERS = [f"{i:06d}" for i in range(2400)]
COVERED = frozenset(TICKERS[::3])                  # 800종목(33%) — 나머지 1,600 은 무커버
N_CALLS = len(COVERED) * bw.REQ_COVERED + (len(TICKERS) - len(COVERED)) * bw.REQ_NONE
WISE_CHECKS = {"wise.snapshot_day", "wise.run", "wise.req_identity", "wise.cov_rate", "wise.raw"}
_PAGE = ("<html>추정기관수" + "x" * 5000 + "</html>").encode()   # c1010001 검증 통과 본문


def _fake_fetch(fail):
    """네트워크 대역. `fail(cmp_cd, ep, pkey)` 가 (verdict, body) 를 주면 그 응답, None 이면
    정상 응답."""
    def fetch(cmp_cd, ep, pk, url):
        got = fail(cmp_cd, ep, pk) if fail else None
        if got is not None:
            return cmp_cd, got[0], got[1], len(got[1]), 1
        if ep == "c1010001":
            body = _PAGE
        elif ep == "cF5001":
            c1 = {"select_item": [1 if cmp_cd in COVERED else None], "target_price": [None]}
            body = json.dumps({"chart1": json.dumps(c1),
                               "chart2": json.dumps({"select_item": [None]})}).encode()
        else:       # 목록·cF5002·대체 축·재무. 목록이 비면 기본 3개년(202612·202712·202812)
            body = b'{"JsonData": []}'
        return cmp_cd, "ok", body, len(body), 1
    return fetch


def _collect(monkeypatch, db, ts, fail=None, limit=0, tickers=None):
    """`backfill_wise.main()` 한 번 = 런 1회. 기록 시각은 전부 ts(UTC) — 런 안의 순서는 rowid.
    limit 은 `--limit`(저녁 체인 dry-run 은 3), tickers 는 유니버스(기본 TICKERS)."""
    universe = list(TICKERS if tickers is None else tickers)
    monkeypatch.setattr(bw, "DB", str(db))
    monkeypatch.setattr(bw, "universe", lambda con: list(universe))
    monkeypatch.setattr(bw, "encparam", lambda refresh=False: "tok")
    monkeypatch.setattr(bw, "fetch", _fake_fetch(fail))
    monkeypatch.setattr(bw, "kst_today", lambda: DAY)
    monkeypatch.setattr(bw, "now_utc", lambda: ts)
    monkeypatch.setattr(sys, "argv", ["backfill_wise.py", "--mode", "full"]
                        + (["--limit", str(limit)] if limit else []))
    bw.main()


def _crash_at(i):
    """i 번째 종목에서 런을 죽인다(encparam 실패·OOM·kill 재현) — 그 앞 종목만 커밋된다."""
    def fail(cmp_cd, ep, pk):
        if cmp_cd == TICKERS[i]:
            raise RuntimeError("런 중단 재현")
    return fail


def _health(db):
    """그날(DAY) `ledger_health.check_wise` 결과 {검사 이름: Check}."""
    with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as con:
        return {c.name: c for c in lh.check_wise(con, DAY, NEXT_DAY)}


def _not_pass(checks):
    return {n: (c.status.value, c.value) for n, c in checks.items()
            if c.status is not lh.Status.PASS}


def _runs(db):
    with closing(sqlite3.connect(db)) as con:
        return con.execute("SELECT n_stocks, n_req, n_ok, n_bad FROM ws_run_log "
                           "ORDER BY run_at, rowid").fetchall()


def test_rerun_after_an_interrupted_run_passes_that_days_health(tmp_path, monkeypatch):
    """A-03 (a): 1차 런이 중간에 끊기면(encparam 실패·OOM·kill) 런 로그가 없다. 같은 날 재실행은
    남은 종목만 받고, 그날 wise.* 는 전부 PASS 여야 한다. 종전에는 마지막 런 n_req(남은 종목분)를
    그날 호출 원장 전체와 비교해 `wise.req_identity` 가 반드시 FAIL 했다(감사 재현 40 vs 18)."""
    db = tmp_path / "wisereport.db"
    with pytest.raises(RuntimeError, match="런 중단 재현"):
        _collect(monkeypatch, db, "2026-10-06T09:05:00", fail=_crash_at(1000))
    assert _runs(db) == []                                   # 끊긴 런은 런 로그를 못 남긴다
    _collect(monkeypatch, db, "2026-10-06T09:40:00")
    assert [r[0] for r in _runs(db)] == [len(TICKERS) - 1000]   # 앞 런이 커밋한 종목은 안 부른다
    with closing(sqlite3.connect(db)) as con:
        assert con.execute("SELECT COUNT(*) FROM ws_call_log").fetchone()[0] == N_CALLS
    checks = _health(db)
    assert set(checks) == WISE_CHECKS and _not_pass(checks) == {}


def test_rerun_refetches_only_failed_stocks_and_passes_that_days_health(tmp_path, monkeypatch):
    """A-03 (b): 런은 끝났지만 콜 일부가 실패(http 5xx·예외·본문 검증 실패)하면 그날 wise.run 은
    FAIL 이다(종전과 같다). 같은 날 재실행은 실패가 남은 종목만 다시 받고, 회복되면 그날 wise.* 가
    전부 PASS 다. 다 회복된 뒤 재실행은 대상 0 이어도 런 로그를 한 줄(n_req 0) 남긴다. 종전에는
    cF5001 을 받은 종목을 통째로 건너뛰어 '대상 0' 으로 끝났고 런 로그도 없어 실패가 그대로
    남았다."""
    db = tmp_path / "wisereport.db"
    cov, none = sorted(COVERED), sorted(set(TICKERS) - COVERED)
    fails = {(cov[0], "cF5002", "202712"): ("http503", b""),
             (cov[1], "c1010001", ""): ("ok", "접속장애".encode()),         # 200 인데 bad/errpage
             (none[0], "cF5001", "202612"): ("exc/ConnectionError", b"")}   # 판정 불능 → 커버 세트
    _collect(monkeypatch, db, "2026-10-06T09:05:00", fail=lambda *k: fails.get(k))
    assert _health(db)["wise.run"].status is lh.Status.FAIL                # 런 하나·일부 실패
    _collect(monkeypatch, db, "2026-10-06T09:40:00")
    _collect(monkeypatch, db, "2026-10-06T10:10:00")                       # 다 회복된 뒤 — 대상 0
    runs = _runs(db)
    assert [r[0] for r in runs] == [len(TICKERS), len(fails), 0]
    flip = bw.REQ_COVERED - bw.REQ_NONE             # none[0] 은 그 런에서 커버 세트를 받았다
    assert runs[0][1:] == (N_CALLS + flip, N_CALLS + flip - len(fails), 1)
    rerun = 2 * bw.REQ_COVERED + bw.REQ_NONE        # cov[0]·cov[1] 커버 + none[0] 무커버
    assert runs[1][1:] == (rerun, rerun, 0) and runs[2][1:] == (0, 0, 0)
    checks = _health(db)
    assert set(checks) == WISE_CHECKS and _not_pass(checks) == {}


@pytest.mark.parametrize("failing", [False, True], ids=["all_ok", "one_failed"])
def test_single_run_health_is_unchanged(tmp_path, monkeypatch, failing):
    """A-03 회귀 가드: 그날 런이 하나면 판정은 종전과 같다. 전부 성공이면 wise.* 전부 PASS,
    콜 하나가 실패하고 재실행이 없으면 wise.run·wise.raw FAIL(항등식은 호출 수가 맞아 PASS)."""
    db = tmp_path / "wisereport.db"
    bad = (min(COVERED), "cF4002", "Y")

    def fail(*k):
        return ("http500", b"") if failing and k == bad else None

    _collect(monkeypatch, db, "2026-10-06T09:05:00", fail=fail)
    checks = _health(db)
    assert set(checks) == WISE_CHECKS
    assert set(_not_pass(checks)) == ({"wise.run", "wise.raw"} if failing else set())
    assert checks["wise.run"].value["basis"] == "run_log"            # 판정식도 종전 그대로


def test_limited_run_after_an_interrupted_run_is_not_completion_evidence(tmp_path, monkeypatch):
    """(검토 C-1) 끊긴 런 뒤 dry-run(--limit 3)만 돌았으면 그날 wise.run 은 FAIL 이어야 한다.
    --limit 은 done 을 거르기 전에 유니버스 앞 3종목에 걸려 '대상 0' 으로 끝나므로 그날 완료의
    증거가 아니다 — 그 런의 런 로그(n_stocks 0)를 완료로 읽으면 빠진 종목을 두고 PASS 한다."""
    db = tmp_path / "wisereport.db"
    with pytest.raises(RuntimeError, match="런 중단 재현"):
        _collect(monkeypatch, db, "2026-10-06T09:05:00", fail=_crash_at(1000))
    _collect(monkeypatch, db, "2026-10-06T12:00:00", limit=3)
    assert _runs(db) == []                                   # --limit 런은 런 로그를 남기지 않는다
    assert _health(db)["wise.run"].status is lh.Status.FAIL


def test_dry_run_before_a_run_that_breaks_late_is_not_completion_evidence(tmp_path, monkeypatch):
    """(검토 C-1) 18:05 전 dry-run(--limit 3) 뒤 정규 런이 끊기면 그날 wise.run 은 FAIL 이어야
    한다 — dry-run 의 런 로그를 완료로 읽으면 끊긴 정규 런의 하루가 PASS 한다."""
    db = tmp_path / "wisereport.db"
    _collect(monkeypatch, db, "2026-10-06T05:00:00", limit=3)
    with pytest.raises(RuntimeError, match="런 중단 재현"):
        _collect(monkeypatch, db, "2026-10-06T09:05:00", fail=_crash_at(1000))
    assert _runs(db) == []
    assert _health(db)["wise.run"].status is lh.Status.FAIL


def test_dry_run_before_the_evening_run_passes_that_days_health(tmp_path, monkeypatch):
    """(검토 M-4) 18:05 전 dry-run(--limit 3) → 정규 런 완주 → 그날 wise.* 전부 PASS. dry-run 은
    런 로그를 남기지 않아 런 로그가 그날 호출을 1:1 로 담지 않으므로 호출 원장 기준(basis
    call_log)이다. 원래 코드는 마지막 런 n_req(남은 종목분)를 그날 원장 전체와 비교해 거짓 FAIL."""
    db = tmp_path / "wisereport.db"
    _collect(monkeypatch, db, "2026-10-06T05:00:00", limit=3)
    _collect(monkeypatch, db, "2026-10-06T09:05:00")
    assert [r[0] for r in _runs(db)] == [len(TICKERS) - 3]
    checks = _health(db)
    assert set(checks) == WISE_CHECKS and _not_pass(checks) == {}
    assert checks["wise.run"].value["basis"] == "call_log"


def test_empty_universe_day_is_not_a_completed_run(tmp_path, monkeypatch):
    """(검토 M-3) 유니버스가 0 이면 대상 0 런 로그(n_req 0)만 남고 호출은 없다. 그날 wise.run 이
    0 == 0 으로 PASS 하면 안 된다 — 원래 코드는 런 로그가 없어 FAIL 이었다."""
    db = tmp_path / "wisereport.db"
    _collect(monkeypatch, db, "2026-10-06T09:05:00", tickers=[])
    assert _runs(db) == [(0, 0, 0, 0)]
    assert _health(db)["wise.run"].status is lh.Status.FAIL
