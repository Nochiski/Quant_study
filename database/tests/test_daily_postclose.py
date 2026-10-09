"""daily.postclose — 15:41 장 마감 수집기(컷오버 트랙 PR-1 · N-35 · N-42 Q3 · T-4).

키움 콜은 전부 가짜(`api.kiwoom` 갈아끼우기)이고, 토큰 재발급 시험만 진짜 `api.kiwoom` 에 가짜 HTTP
(`requests.post`)를 물린다. 판(인계 이력·equity `universe_daily`·fi `fi_universe`)은 실물과 같은
MANIFEST 규약의 소형 parquet 로 tmp 루트에 만든다. 시계는 `postclose._now_kst` 를 갈아끼워 가짜 콜이
1초씩 전진시킨다 — 16:00 경계를 초 단위로 재현한다.

날짜: T = 20261013(화), D' = 20261012(월). 20261009(금, 한글날)는 휴장. 20261119(목)는 저장소 기본
세션 예외 표의 수능일.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import importlib
import json
import sqlite3
import sys
import types
from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest
from daily import calendar as trading_calendar
from daily import kw_daily, postclose, runlog
from stage import manifest

KST = dt.timezone(dt.timedelta(hours=9))
T = "20261013"
D_PREV = "20261012"
HOLIDAY = "20261009"
UNI_BUILD = "m_20261012T231500Z"
FI_BUILD = "m_20261012T233000Z"
# ① fi 판 eligible(종목코드 순) — 005935 는 우선주라 ② v3 유니버스에는 없다
CAND = ("000020", "005930", "005935")
# ② v3 유니버스(보통주·스팩, KOSPI·KOSDAQ) − ①
REST = ("035720", "440790")
ORDER = [*CAND, *REST]
ROWS_KEY = "stk_invsr_orgn_chart"
LEDGER_COLS = ["ticker", "dt", "cur_prc", "pred_pre", "acc_trde_prica", *kw_daily.FLOW_KEYS,
               "src_api", "collected_at"]
DEAD = {"return_code": 3, "return_msg": "[8005:Token이 유효하지 않습니다]"}


# ── 판 픽스처 ─────────────────────────────────────────────────────────────────
def _table(table_root: Path, build_id: str, parts: dict[str, list[tuple]], cols: str) -> None:
    """`<table_root>/v=<build_id>/<파티션>/part0.parquet` + MANIFEST(stage.manifest.commit 그대로)."""
    records = []
    con = duckdb.connect()
    try:
        for label, rows in parts.items():
            pdir = table_root / f"v={build_id}" / label if label else table_root / f"v={build_id}"
            pdir.mkdir(parents=True, exist_ok=True)
            con.execute(f"CREATE OR REPLACE TEMP TABLE t ({cols})")
            con.executemany(f"INSERT INTO t VALUES ({','.join('?' * len(rows[0]))})", rows)
            con.execute(f"COPY t TO '{pdir / 'part0.parquet'}' (FORMAT PARQUET)")
            records.append({"path": f"v={build_id}" + (f"/{label}" if label else ""),
                            "n_rows": len(rows)})
    finally:
        con.close()
    manifest.commit(table_root, manifest.BuildRecord(
        build_id=build_id, snapshot_id="", rules_version="test",
        built_at_utc="2026-10-12T23:15:00Z", n_rows=sum(int(r["n_rows"]) for r in records),
        content_hash="test", partitions=records))


def _universe(home: Path) -> None:
    """인계 이력 `<D'>_morning.json` + 그것이 가리키는 equity `universe_daily` 판."""
    hist = home / "data" / "deliver" / "history"
    hist.mkdir(parents=True, exist_ok=True)
    (hist / f"{D_PREV}_morning.json").write_text(json.dumps({
        "date": D_PREV, "basis": "morning",
        "equity_builds": {"universe_daily": UNI_BUILD, "price_daily": "m_20261012T231000Z"},
        "stage_builds": {"stg_price_daily": "m_20261012T230000Z"},
        "health": {"stage": "ok", "equity": "ok"}}), encoding="utf-8")
    d_prev = dt.date(2026, 10, 12)
    rows = [
        (d_prev, "000020", "KOSPI", "common", "listed"),
        (d_prev, "005930", "KOSPI", "common", "listed"),
        (d_prev, "035720", "KOSDAQ", "common", "suspended"),
        (d_prev, "440790", "KOSDAQ", "spac", "listed"),
        (d_prev, "005935", "KOSPI", "preferred", "listed"),     # 종류 밖
        (d_prev, "069500", "KOSPI", "etf", "listed"),           # 종류 밖
        (d_prev, "900110", "KONEX", "common", "listed"),        # 시장 밖
        (dt.date(2026, 10, 8), "111110", "KOSPI", "common", "listed"),   # D' 가 아닌 날
    ]
    _table(home / "data" / "equity" / "universe_daily", UNI_BUILD, {"year=2026": rows},
           "date DATE, ticker VARCHAR, market VARCHAR, sec_type VARCHAR, status VARCHAR")


def _fi(home: Path, status: str = "ok") -> None:
    """fi 판 manifest `_runs/<D'>_morning.json` + `fi_universe` 판."""
    root = home / "data" / "factor_inputs"
    (root / "_runs").mkdir(parents=True, exist_ok=True)
    (root / "_runs" / f"{D_PREV}_morning.json").write_text(json.dumps({
        "layer": "factor_inputs", "status": status, "build_id": FI_BUILD,
        "date": "2026-10-12", "basis": "morning"}), encoding="utf-8")
    rows = [("000020", True), ("005930", True), ("005935", True), ("035720", False)]
    _table(root / "fi_universe", FI_BUILD, {"": rows}, "ticker VARCHAR, eligible BOOLEAN")


def _calendar(home: Path, exceptions: dict[str, str] | None = None) -> None:
    """2026 판정 연도 파일 — 주말 + 한글날(10-09). `exceptions` 를 주면 운영 세션 예외 표도 쓴다."""
    d = home / "data" / "calendar"
    d.mkdir(parents=True, exist_ok=True)
    days = (dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(365))
    hol = [x.strftime("%Y%m%d") for x in days if x.weekday() >= 5] + [HOLIDAY]
    (d / "kis_holidays_2026.json").write_text(json.dumps({"year": 2026, "holidays": hol}),
                                              encoding="utf-8")
    if exceptions is not None:
        (d / "session_exceptions.json").write_text(json.dumps({"days": exceptions}),
                                                   encoding="utf-8")


# ── 가짜 시계·키움 ─────────────────────────────────────────────────────────────
class Clock:
    """`postclose._now_kst` 대역. 가짜 콜이 `advance` 로 전진시킨다."""

    def __init__(self, hms: str, day: str = T) -> None:
        self.now = dt.datetime.strptime(day + hms, "%Y%m%d%H:%M:%S").replace(tzinfo=KST)

    def __call__(self) -> dt.datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += dt.timedelta(seconds=seconds)


def _flows(seed: int) -> dict[str, str]:
    return {k: str(seed * (i + 1)) for i, k in enumerate(kw_daily.FLOW_KEYS)}


def _rows(seed: int = 1, dates: tuple[str, ...] = (T, D_PREV)) -> list[dict[str, str]]:
    """ka10060 응답 행 — 최신→과거, T 행 + 직전 행(저장되면 안 된다)."""
    return [{"dt": d, "cur_prc": f"-{70000 + seed}", "pred_pre": "-500", "acc_trde_prica": "1234",
             **_flows(seed)} for d in dates]


def _ok(rows: list[dict[str, str]]) -> dict[str, object]:
    return {"return_code": 0, "return_msg": "정상적으로 처리되었습니다", ROWS_KEY: rows}


class FakeKiwoom:
    """`api.kiwoom` 대역 — 종목별 응답(`plan`)이 없으면 정상 응답, 콜마다 시계 1초 전진."""

    def __init__(self, clock: Clock, seed: int = 1,
                 plan: dict[str, Callable[[], dict[str, object]]] | None = None) -> None:
        self.clock, self.seed, self.plan = clock, seed, plan or {}
        self.calls: list[str] = []
        self.dts: set[str] = set()

    def kiwoom(self, api_id, url, body, cont=None, next_key=None):
        assert (api_id, url) == ("ka10060", "/api/dostk/chart")
        assert body["amt_qty_tp"] == "1"                           # 운영 수집기와 같은 요청 본문
        self.calls.append(body["stk_cd"])
        self.dts.add(body["dt"])
        self.clock.advance(1)
        make = self.plan.get(body["stk_cd"])
        return (make() if make else _ok(_rows(self.seed))), {}


def _prepare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock, *, fi: bool = True,
             universe: bool = True) -> Path:
    monkeypatch.setenv("QL_HOME", str(tmp_path))
    _calendar(tmp_path)
    if universe:
        _universe(tmp_path)
    if fi:
        _fi(tmp_path)
    monkeypatch.setattr(postclose, "_now_kst", clock)
    monkeypatch.setattr(postclose, "LOCK_FILE", str(tmp_path / "postclose.lock"))
    monkeypatch.setattr(kw_daily, "RATE_PER_SEC", 10_000.0)      # 스로틀 대기 제거
    monkeypatch.setattr(kw_daily, "BACKOFF_SEC", 0.0)
    return tmp_path


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakeKiwoom) -> None:
    module = types.ModuleType("api")
    module.kiwoom = fake.kiwoom
    monkeypatch.setitem(sys.modules, "api", module)


def _ledger(home: Path) -> dict[str, dict[str, str]]:
    con = sqlite3.connect(home / "data" / "raw" / "postclose.db")
    con.row_factory = sqlite3.Row
    try:
        return {r["ticker"]: dict(r) for r in con.execute(
            "SELECT * FROM ka10060_investor_flows ORDER BY ticker")}
    finally:
        con.close()


def _runs(home: Path) -> list[tuple]:
    path = home / "data" / "raw" / "daily_run.db"
    if not path.exists():
        return []
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT date, source, status, n_calls, n_rows, detail FROM run "
                           "ORDER BY run_id").fetchall()
    finally:
        con.close()


# ── 대상 순서: 후보 먼저 → v3 유니버스 나머지 ─────────────────────────────────────
def test_candidates_first_then_universe_rest_and_only_t_rows_stored(tmp_path, monkeypatch):
    clock = Clock("15:41:00")
    home = _prepare(tmp_path, monkeypatch, clock)
    fake = FakeKiwoom(clock)
    _install(monkeypatch, fake)
    assert postclose.main([]) == 0                    # --date 생략 = 오늘(KST) = T
    assert fake.calls == ORDER and fake.dts == {T}
    got = _ledger(home)
    assert sorted(got) == sorted(ORDER)
    assert {r["dt"] for r in got.values()} == {T}     # 직전 행(D')은 남기지 않는다
    one = got["000020"]
    assert one["cur_prc"] == "-70001" and one["pred_pre"] == "-500"
    assert one["frgnr_invsr"] == _flows(1)["frgnr_invsr"] and one["src_api"] == "ka10060"
    assert one["price_valid"] == "1"
    assert one["collected_at"] == "2026-10-13T06:41:01"           # UTC, 받은 시각
    assert one["fetched_at"] == "2026-10-13T06:41:00"             # UTC, 런 시작
    (date, source, status, n_calls, n_rows, detail), = _runs(home)
    assert (date, source, status, n_calls, n_rows) == (T, "kiwoom_postclose", "ok", 5, 5)
    assert detail.startswith("errors=0/5(0.0%) ")                 # 오류 비율이 머리
    assert f"Dp={D_PREV}" in detail and "cand=3/3" in detail and "rest=2/2" in detail
    assert f"fi_build={FI_BUILD}" in detail and f"universe_build={UNI_BUILD}" in detail
    assert "health=equity:ok,stage:ok" in detail                  # 보지는 않고 남긴다


def test_ledger_schema_is_kiwoom_ledger_columns_plus_two(tmp_path, monkeypatch):
    clock = Clock("15:41:00")
    home = _prepare(tmp_path, monkeypatch, clock)
    _install(monkeypatch, FakeKiwoom(clock))
    assert postclose.main(["--date", T, "--limit", "1"]) == 0
    con = sqlite3.connect(home / "data" / "raw" / "postclose.db")
    try:
        info = con.execute('PRAGMA table_info("ka10060_investor_flows")').fetchall()
    finally:
        con.close()
    assert [r[1] for r in info] == [*LEDGER_COLS, "fetched_at", "price_valid"]
    assert [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]] == ["ticker", "dt"]   # PK


# ── 15:41 하한 · 16:00 규칙 ───────────────────────────────────────────────────
def test_row_received_at_1600_is_price_invalid_and_remaining_not_called(tmp_path, monkeypatch):
    clock = Clock("15:59:58")
    home = _prepare(tmp_path, monkeypatch, clock)
    fake = FakeKiwoom(clock)
    _install(monkeypatch, fake)
    assert postclose.main(["--date", T]) == 0          # 컷오프는 실패가 아니다(남은 종목 = 21:05 저녁 값)
    assert fake.calls == ["000020", "005930"]          # 16:00:00 이 되면 더 부르지 않는다
    got = _ledger(home)
    assert sorted(got) == ["000020", "005930"]
    assert got["000020"]["price_valid"] == "1"          # 15:59:59 수신
    assert got["005930"]["price_valid"] == "0"          # 16:00:00 수신 — 가격은 애프터마켓 값
    assert got["005930"]["collected_at"] == "2026-10-13T07:00:00"
    assert got["005930"]["frgnr_invsr"] == _flows(1)["frgnr_invsr"]   # 수급은 그대로 남긴다
    (_, _, status, n_calls, n_rows, detail), = _runs(home)
    assert (status, n_calls, n_rows) == ("cutoff", 2, 2)
    assert "cand=2/3" in detail and "rest=0/2" in detail and "price_invalid=1" in detail
    assert "not_called(cutoff)=005935,035720,440790" in detail


def test_started_after_1600_is_late_rc0_without_calls(tmp_path, monkeypatch):
    clock = Clock("16:00:00")
    home = _prepare(tmp_path, monkeypatch, clock)
    fake = FakeKiwoom(clock)
    _install(monkeypatch, fake)
    assert postclose.main(["--date", T]) == 0
    assert fake.calls == []
    (_, _, status, n_calls, _, detail), = _runs(home)
    assert (status, n_calls) == ("late", 0)
    assert "not_called(late)=" + ",".join(ORDER) in detail


@pytest.mark.parametrize(("hms", "rc", "n_calls"), [("15:40:59", 3, 0), ("15:41:00", 0, 5)])
def test_floor_is_1541_same_as_cron(tmp_path, monkeypatch, hms, rc, n_calls):
    # 15:40 = KRX 정규장 수급 확정(N-35 ②). 그 전 값을 첫 관측으로 굳히면 다음 회차가 덮지 못한다
    clock = Clock(hms)
    home = _prepare(tmp_path, monkeypatch, clock)
    fake = FakeKiwoom(clock)
    _install(monkeypatch, fake)
    assert postclose.main(["--date", T]) == rc
    assert len(fake.calls) == n_calls
    if rc == 3:
        assert not (home / "data" / "raw" / "postclose.db").exists()
        assert [r[2] for r in _runs(home)] == ["too_early"]


# ── 세션 예외일(수능일 등) ────────────────────────────────────────────────────
def test_session_exception_from_repo_default_skips_rc3(tmp_path, monkeypatch, capsys):
    """저장소 기본 표만으로(운영 표 없음) 2027학년도 수능일 20261119 는 건너뛴다."""
    clock = Clock("15:41:00", day="20261119")
    home = _prepare(tmp_path, monkeypatch, clock)
    fake = FakeKiwoom(clock)
    _install(monkeypatch, fake)
    assert postclose.main([]) == 3
    assert fake.calls == []
    (date, _, status, _, _, detail), = _runs(home)
    assert (date, status) == ("20261119", "session_exception") and "수능" in detail
    assert "수능" in capsys.readouterr().err


def test_session_exception_from_ops_table_skips_rc3(tmp_path, monkeypatch):
    clock = Clock("15:41:00")
    home = _prepare(tmp_path, monkeypatch, clock)
    _calendar(home, exceptions={T: "임시 폐장 연장(시험)"})
    fake = FakeKiwoom(clock)
    _install(monkeypatch, fake)
    assert postclose.main(["--date", T]) == 3
    assert fake.calls == []
    assert [(r[2], "임시 폐장 연장" in r[5]) for r in _runs(home)] == [("session_exception", True)]


# ── 다시 돌기: 첫 관측 유지 · 이미 받은 종목은 대상에서 뺀다 ─────────────────────────
def test_rerun_calls_only_missing_and_keeps_first_observation(tmp_path, monkeypatch):
    clock = Clock("15:41:00")
    home = _prepare(tmp_path, monkeypatch, clock)
    _install(monkeypatch, FakeKiwoom(clock, plan={"005930": lambda: DEAD}))
    assert postclose.main(["--date", T]) == 2                # 첫 종목만 받고 토큰 실패
    first = _ledger(home)
    assert sorted(first) == ["000020"]
    fake = FakeKiwoom(clock, seed=2)                          # 값이 다른 두 번째 관측
    _install(monkeypatch, fake)
    assert postclose.main(["--date", T]) == 0
    assert fake.calls == ORDER[1:]                            # 이미 받은 000020 은 부르지 않는다
    got = _ledger(home)
    assert got["000020"] == first["000020"]                   # 값·collected_at·fetched_at 그대로
    assert sorted(got) == sorted(ORDER)
    detail = _runs(home)[-1][5]
    assert "have=1" in detail and "cand=2/2" in detail
    fake = FakeKiwoom(clock, seed=3)
    _install(monkeypatch, fake)
    assert postclose.main(["--date", T]) == 0                # 다 받았으면 콜 0, ok
    assert fake.calls == [] and _ledger(home) == got
    assert _runs(home)[-1][2:4] == ("ok", 0) and "have=5" in _runs(home)[-1][5]


# ── 락 경합 ───────────────────────────────────────────────────────────────────
def test_lock_held_exits_3_without_calls_or_writes(tmp_path, monkeypatch):
    clock = Clock("15:41:00")
    home = _prepare(tmp_path, monkeypatch, clock)
    fake = FakeKiwoom(clock)
    _install(monkeypatch, fake)
    with open(postclose.LOCK_FILE, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert postclose.main(["--date", T]) == 3
    assert fake.calls == []
    assert _runs(home) == []                                 # 쥔 쪽이 자기 런 로그를 남긴다
    assert not (home / "data" / "raw" / "postclose.db").exists()
    assert postclose.main(["--date", T]) == 0                # 풀리면 그대로 돈다


# ── 토큰 ──────────────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload, self.headers, self.status_code, self.text = payload, {}, 200, ""

    def json(self) -> dict[str, object]:
        return self._payload


def _real_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, post) -> None:
    """진짜 `api` 모듈(가짜 .env · 저장소 밖 토큰 캐시) + 가짜 `requests.post`."""
    env = tmp_path / ".env"
    env.write_text("KRX_API_KEY=krx\nKRX_ID=id\nKRX_PW=pw\nKIWOOM_APP_KEY=ak\n"
                   "KIWOOM_SECRET_KEY=sk\n", encoding="utf-8")
    monkeypatch.setenv("QL_ENV", str(env))
    monkeypatch.delitem(sys.modules, "api", raising=False)
    api = importlib.import_module("api")
    monkeypatch.setitem(sys.modules, "api", api)
    monkeypatch.setattr(api, "_KW_CACHE", str(tmp_path / "kw_token.json"))   # 저장소 캐시 보호
    monkeypatch.setattr(api.time, "sleep", lambda _s: None)
    monkeypatch.setattr(api.requests, "post", post)


def test_token_revoked_midrun_is_reissued_once_and_run_continues(tmp_path, monkeypatch):
    """서버가 런 도중 토큰을 폐기(8005)하면 `api.kiwoom` 이 강제 재발급 1회 뒤 이어 받는다."""
    clock = Clock("15:41:00")
    home = _prepare(tmp_path, monkeypatch, clock)
    issued = iter(["tok1", "tok2"])
    seen: dict[str, list[str]] = {"token": [], "data": []}

    def post(url, json=None, headers=None, timeout=None):
        if url.endswith("/oauth2/token"):
            tok = next(issued)
            seen["token"].append(tok)
            return _Resp({"token": tok, "expires_dt": "20991231235959"})
        seen["data"].append(json["stk_cd"])
        clock.advance(1)
        # 두 번째 종목부터 서버가 tok1 을 폐기했다(09-02 실측: 만료 9시간 전 8005)
        if headers["authorization"] == "Bearer tok1" and len(seen["data"]) >= 2:
            return _Resp(DEAD)
        return _Resp(_ok(_rows(1)))

    _real_api(tmp_path, monkeypatch, post)
    assert postclose.main(["--date", T]) == 0
    assert seen["token"] == ["tok1", "tok2"]                       # 첫 발급 + 재발급 1회
    assert seen["data"] == [ORDER[0], ORDER[1], ORDER[1], *ORDER[2:]]   # 폐기된 종목만 한 번 더
    assert sorted(_ledger(home)) == sorted(ORDER)
    assert _runs(home)[-1][2] == "ok"


def test_token_reissue_failure_keyerror_closes_runlog_as_error_rc2(tmp_path, monkeypatch):
    """M1 — 재발급 응답에 token 이 없으면 `api._kw_token` 이 KeyError 를 낸다. 런 로그를 running 으로
    남기지 않고 error 로 닫는다(그때까지 받은 커버를 함께)."""
    clock = Clock("15:41:00")
    home = _prepare(tmp_path, monkeypatch, clock)
    n_token = [0]

    def post(url, json=None, headers=None, timeout=None):
        if url.endswith("/oauth2/token"):
            n_token[0] += 1
            if n_token[0] == 1:
                return _Resp({"token": "tok1", "expires_dt": "20991231235959"})
            return _Resp({"return_code": 3, "return_msg": "[8050:지정단말기 인증 실패]"})
        clock.advance(1)
        if json["stk_cd"] == ORDER[1]:
            return _Resp(DEAD)
        return _Resp(_ok(_rows(1)))

    _real_api(tmp_path, monkeypatch, post)
    assert postclose.main(["--date", T]) == 2
    assert sorted(_ledger(home)) == [ORDER[0]]                     # 받은 행은 남는다
    (_, _, status, n_calls, n_rows, detail), = _runs(home)
    assert (status, n_calls, n_rows) == ("error", 1, 1)
    assert "KeyError: 'token'" in detail and "cand=1/3" in detail


def test_token_dead_after_reissue_stops_run_keeps_received_rows(tmp_path, monkeypatch):
    """재발급 뒤에도 8005(= `api.kiwoom` 의 1회 재시도까지 실패)면 남은 종목을 두드리지 않고 rc 2."""
    clock = Clock("15:41:00")
    home = _prepare(tmp_path, monkeypatch, clock)
    fake = FakeKiwoom(clock, plan={"005930": lambda: DEAD})
    _install(monkeypatch, fake)
    assert postclose.main(["--date", T]) == 2
    assert fake.calls == ["000020", "005930"]
    assert sorted(_ledger(home)) == ["000020"]                     # 받은 행은 남는다
    (_, _, status, n_calls, n_rows, detail), = _runs(home)
    assert (status, n_calls, n_rows) == ("token_failed", 2, 1)
    assert detail.startswith("errors=1/2(50.0%) ")
    assert "8005" in detail and "error_tickers=005930" in detail   # 부른 종목은 오류로
    assert "not_called(token_failed)=005935,035720,440790" in detail


# ── 빈 응답 · 전부 오류 ───────────────────────────────────────────────────────
def test_empty_or_no_t_row_response_is_listed_not_stored(tmp_path, monkeypatch):
    clock = Clock("15:41:00")
    home = _prepare(tmp_path, monkeypatch, clock)
    fake = FakeKiwoom(clock, plan={"035720": lambda: _ok([]),
                                   "440790": lambda: _ok(_rows(1, dates=(D_PREV,)))})
    _install(monkeypatch, fake)
    assert postclose.main(["--date", T]) == 0
    assert fake.calls == ORDER                                     # 재시도 없이 다음 종목으로
    assert sorted(_ledger(home)) == list(CAND)
    (_, _, status, _, n_rows, detail), = _runs(home)
    assert (status, n_rows) == ("ok", 3)
    assert "rest=0/2" in detail and "no_row=035720,440790" in detail


def test_every_call_failing_is_error_rc2(tmp_path, monkeypatch):
    clock = Clock("15:41:00")
    home = _prepare(tmp_path, monkeypatch, clock)
    broken = {"return_code": 1, "return_msg": "[9999:시스템 오류]"}
    fake = FakeKiwoom(clock, plan=dict.fromkeys(ORDER, lambda: broken))
    _install(monkeypatch, fake)
    assert postclose.main(["--date", T]) == 2
    assert fake.calls == ORDER
    (_, _, status, n_calls, _, detail), = _runs(home)
    assert (status, n_calls) == ("error", 5) and detail.startswith("errors=5/5(100.0%) ")


# ── 휴장일 ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("day", [HOLIDAY, "20261010"])            # 한글날 · 토요일
def test_non_trading_day_skips_with_rc0(tmp_path, monkeypatch, capsys, day):
    clock = Clock("15:41:00", day=day)
    home = _prepare(tmp_path, monkeypatch, clock)
    fake = FakeKiwoom(clock)
    _install(monkeypatch, fake)
    assert postclose.main([]) == 0
    assert fake.calls == [] and _runs(home) == []
    assert not (home / "data" / "raw" / "postclose.db").exists()
    assert "거래일 아님" in capsys.readouterr().out


# ── 입력 판 결손 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("status", [None, "gate_failed"])
def test_no_successful_fi_board_still_collects_universe(tmp_path, monkeypatch, status):
    clock = Clock("15:41:00")
    home = _prepare(tmp_path, monkeypatch, clock, fi=False)
    if status is not None:
        _fi(home, status=status)
    fake = FakeKiwoom(clock)
    _install(monkeypatch, fake)
    assert postclose.main(["--date", T]) == 0
    assert fake.calls == ["000020", "005930", "035720", "440790"]   # 유니버스만, 종목코드 순
    detail = _runs(home)[-1][5]
    assert "cand=0/0" in detail and "fi_build=None" in detail and "inputs:" in detail


def test_no_board_at_all_is_rc2_but_schema_exists(tmp_path, monkeypatch):
    clock = Clock("15:41:00")
    home = _prepare(tmp_path, monkeypatch, clock, fi=False, universe=False)
    fake = FakeKiwoom(clock)
    _install(monkeypatch, fake)
    assert postclose.main(["--date", T]) == 2
    assert fake.calls == []
    (_, _, status, _, _, detail), = _runs(home)
    assert status == "no_targets" and f"{D_PREV}_morning.json" in detail
    assert _ledger(home) == {}                                     # 대상 0 이어도 표는 선다(m2)


# ── dry-run · check ───────────────────────────────────────────────────────────
def test_dry_run_inside_trading_window_is_refused_before_lock(tmp_path, monkeypatch):
    """거래일 15:40~16:00 의 dry-run 은 운영 수집과 락·콜 한도를 다툰다 — 락도 잡지 않고 rc 3."""
    clock = Clock("15:40:00")
    _prepare(tmp_path, monkeypatch, clock)
    fake = FakeKiwoom(clock)
    _install(monkeypatch, fake)
    assert postclose.main(["--date", "20261012", "--dry-run"]) == 3   # 지난 T 라도 오늘 창이면 거부
    assert fake.calls == [] and not Path(postclose.LOCK_FILE).exists()


def test_dry_run_after_window_resolves_targets_and_writes_nothing(tmp_path, monkeypatch, capsys):
    clock = Clock("16:05:00")
    home = _prepare(tmp_path, monkeypatch, clock)
    fake = FakeKiwoom(clock)
    _install(monkeypatch, fake)
    assert postclose.main(["--date", T, "--dry-run"]) == 0
    assert fake.calls == []                                        # 16:00 뒤라 late
    assert not (home / "data" / "raw" / "postclose.db").exists() and _runs(home) == []
    assert "대상=5" in capsys.readouterr().out


def test_check_mode_reads_only_a_few_outside_window(tmp_path, monkeypatch, capsys):
    clock = Clock("10:00:00", day="20261014")     # 창 밖(다음 날 오전) — 지난 T 를 점검한다
    home = _prepare(tmp_path, monkeypatch, clock)
    fake = FakeKiwoom(clock)
    _install(monkeypatch, fake)
    assert postclose.main(["--date", T, "--check"]) == 0
    assert fake.calls == ORDER[:postclose.CHECK_DEFAULT_N]          # 후보 먼저, 몇 종목만
    assert not (home / "data" / "raw" / "postclose.db").exists() and _runs(home) == []
    out = capsys.readouterr().out
    assert "check" in out and "000020" in out and "frgnr_invsr" in out


# ── 리포트 등급 계약 ──────────────────────────────────────────────────────────
def test_normal_but_noted_statuses_are_registered_as_warn_in_runlog():
    """cutoff·late·session_exception 은 수집기가 정한 정상 종료 — 일일 리포트가 warn 으로 센다."""
    assert runlog.WARN_STATUSES[postclose.SOURCE] == frozenset(
        s.value for s in (postclose.Status.CUTOFF, postclose.Status.LATE,
                          postclose.Status.SESSION_EXCEPTION))


def test_repo_default_session_table_has_2027_csat_day():
    days = trading_calendar.load_session_exceptions(Path("/nonexistent-ops-dir"))
    assert "20261119" in days and "KRX 공지로 확인 필요" in days["20261119"]
