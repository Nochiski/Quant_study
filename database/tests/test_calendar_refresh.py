"""daily.calendar_refresh — KIS chk-holiday 직접 갱신과 K1-9 ①③④⑥(결정 Q-2 = N-31 ②, 범위 N-40 ④).

플랜 `docs/plans/2026-10-10-holiday-calendar-direct.md`. 콜은 전부 `api.kis` monkeypatch 다 — 네트워크를 쓰지 않는다.
응답 모양은 KIS 공식 예제(open-trading-api examples_llm/domestic_stock/chk_holiday)의 output 열
(bass_dt·wday_dvsn_cd·bzdy_yn·tr_day_yn·opnd_yn·sttl_day_yn)과 v3 가 휴장 판정에 쓰는 `opnd_yn` 을 따른다.
"""
from __future__ import annotations

import datetime as dt
import importlib
import json
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from daily import calendar as cal
from daily import calendar_refresh as cr
from daily import runlog

_FAKE_ENV = ("KRX_API_KEY=krx\nKRX_ID=id\nKRX_PW=pw\n"
             "KIS_APP_KEY=appkey\nKIS_APP_SECRET=secret\n")
# 판정 연도 파일의 평일 휴장(시드 = v3 사본에서 온 2026 판의 일부)
_HOL_2026 = ("20260101", "20261005", "20261009", "20261225", "20261231")
_HOL_2027 = ("20270101", "20270209", "20271231")


@pytest.fixture
def api_mod(tmp_path, monkeypatch):
    """`api` 모듈 — 가짜 .env 를 물리고 토큰 캐시를 tmp 로 돌린다(실제 캐시를 건드리지 않는다).

    `calendar_refresh` 가 지연 import 하는 `backfill_kis`·`backfill_dart` 는 끝나면 import 전 상태로 되돌린다 —
    남겨 두면 뒤 테스트(test_daily_dart)가 `backfill_dart` 만 다시 import 해 `backfill_kis` 가 옛 모듈의
    SPEC 을 쥔 채 남고, test_daily_kis 의 `_spec_shim` 이 KeyError 로 깨진다(실행 순서 의존).
    """
    before = {m: sys.modules.get(m) for m in ("backfill_kis", "backfill_dart")}
    env = tmp_path / "fake.env"
    env.write_text(_FAKE_ENV, encoding="utf-8")
    monkeypatch.setenv("QL_ENV", str(env))
    api = importlib.import_module("api")
    monkeypatch.setattr(api, "_KIS_CACHE", str(tmp_path / "kis_token.json"))
    monkeypatch.setattr(cr.time, "sleep", lambda _s: None)
    yield api
    for name, mod in before.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


def _closed(year: int, weekdays: tuple[str, ...]) -> list[str]:
    """그해 휴장 목록 = 토·일 전부 + 주어진 평일 휴장."""
    d = dt.date(year, 1, 1)
    out: list[str] = []
    while d.year == year:
        s = d.strftime("%Y%m%d")
        if d.weekday() >= 5 or s in weekdays:
            out.append(s)
        d += dt.timedelta(days=1)
    return out


def _home(tmp_path: Path, years: dict[int, tuple[str, ...]] | None = None) -> Path:
    home = tmp_path / "ql"
    d = home / "data" / "calendar"
    d.mkdir(parents=True)
    for year, weekdays in (years if years is not None else {2026: _HOL_2026}).items():
        (d / f"kis_holidays_{year}.json").write_text(
            json.dumps({"year": year, "holidays": _closed(year, weekdays)}), encoding="utf-8")
    return home


def _rows(start: str, n_days: int, closed: tuple[str, ...] = (), opened: tuple[str, ...] = ()
          ) -> list[dict[str, object]]:
    """KIS chk-holiday output 행 — 토·일과 `closed` 는 개장 N, `opened` 는 주말이어도 Y(오류 주입용)."""
    d = dt.date(int(start[:4]), int(start[4:6]), int(start[6:]))
    out: list[dict[str, object]] = []
    for _ in range(n_days):
        s = d.strftime("%Y%m%d")
        shut = (d.weekday() >= 5 or s in closed) and s not in opened
        yn = "N" if shut else "Y"
        out.append({"bass_dt": s, "wday_dvsn_cd": f"0{(d.weekday() + 1) % 7 + 1}", "bzdy_yn": yn,
                    "tr_day_yn": yn, "opnd_yn": yn, "sttl_day_yn": yn})
        d += dt.timedelta(days=1)
    return out


def _ok(rows: list[dict[str, object]]) -> dict[str, object]:
    return {"rt_cd": "0", "msg_cd": "KIOK0500", "msg1": "조회가 완료되었습니다", "output": rows,
            "ctx_area_nk": rows[-1]["bass_dt"] if rows else "", "ctx_area_fk": ""}


def _fake_kis(by_start: dict[str, object], seen: list[str]) -> Callable[..., object]:
    def kis(url: str, tr_id: str, params: dict[str, str]) -> object:
        assert url == cr.URL and tr_id == cr.TR_ID
        assert params == {"BASS_DT": params["BASS_DT"], "CTX_AREA_NK": "", "CTX_AREA_FK": ""}
        seen.append(params["BASS_DT"])
        return by_start[params["BASS_DT"]]
    return kis


def _trading_days(years: dict[int, tuple[str, ...]], until: dt.date) -> Callable[[Path], list[dt.date]]:
    """equity `trading_calendar` 대역 — 판정 달력과 같은 거래일(첫 해 1/1 ~ until)."""
    def read(_root: Path) -> list[dt.date]:
        out = []
        for year, weekdays in years.items():
            d = dt.date(year, 1, 1)
            while d.year == year and d <= until:
                if d.weekday() < 5 and d.strftime("%Y%m%d") not in weekdays:
                    out.append(d)
                d += dt.timedelta(days=1)
        return out
    return read


_TC_2026 = _trading_days({2026: _HOL_2026}, dt.date(2026, 10, 8))


def _year_holidays(home: Path, year: int) -> list[str]:
    data = json.loads((home / "data" / "calendar" / f"kis_holidays_{year}.json").read_text(encoding="utf-8"))
    return [str(x) for x in data["holidays"]]


def _value(f: cr.Finding) -> dict[str, object]:
    assert isinstance(f.value, dict)
    return f.value


def _raw(home: Path) -> list[tuple[str, str, str, str]]:
    db = home / "data" / "raw" / "kis.db"
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    try:
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='kis_holiday'").fetchone() is None:
            return []
        return [tuple(r) for r in con.execute(
            "SELECT bass_dt, opnd_yn, req_bass_dt, collected_at FROM kis_holiday ORDER BY bass_dt, collected_at")]
    finally:
        con.close()


def _runs(home: Path, source: str) -> list[runlog.Run]:
    return runlog.recent(home / "data" / "raw" / "daily_run.db", source=source)


def _by(report: cr.RefreshReport) -> dict[str, cr.Finding]:
    return {f.name: f for f in report.findings}


# ── ① 창 1콜 → 원장 → 판정 연도 파일 ──────────────────────────────────────────────
def test_window_applies_a_new_holiday_and_keeps_first_seen(api_mod, monkeypatch, tmp_path):
    home = _home(tmp_path)
    seen: list[str] = []
    monkeypatch.setattr(api_mod, "kis", _fake_kis({
        "20261012": _ok(_rows("20261012", 24, closed=("20261020",))),          # 월중 임시공휴일 지정
        "20261013": _ok(_rows("20261013", 24, closed=("20261020",)))}, seen))

    rep = cr.run(home, dt.date(2026, 10, 12), read_trading_days=_TC_2026)

    assert seen == ["20261012"] and rep.n_calls == 1
    f = _by(rep)
    assert f["window"].severity is cr.Severity.OK
    assert _value(f["window"])["added"] == ["20261020"] and _value(f["window"])["removed"] == []
    assert "20261020" in _year_holidays(home, 2026)
    assert cal.load(home / "data" / "calendar").is_trading_day(dt.date(2026, 10, 20)) is False
    raw = _raw(home)
    assert len(raw) == 24 and {r[2] for r in raw} == {"20261012"}
    [r] = _runs(home, cr.SRC_WINDOW)
    assert (r.date, r.status, r.n_calls) == ("20261012", "ok", 1)
    assert rep.rc == 0
    assert json.loads((home / "logs" / "calendar" / "20261012.json").read_text(encoding="utf-8"))["rc"] == 0

    # 같은 날 두 번째 실행 — 부르지 않는다(KIS '1일 1회' 권고)
    again = cr.run(home, dt.date(2026, 10, 12), read_trading_days=_TC_2026)
    assert seen == ["20261012"] and again.n_calls == 0
    assert _by(again)["window"].severity is cr.Severity.INFO

    # 다음 날 — 같은 사실은 새 행이 아니다(collected_at = 최초 관측), 새로 덮인 날 1행만 는다
    first_seen = {r[0]: r[3] for r in _raw(home)}
    cr.run(home, dt.date(2026, 10, 13), read_trading_days=_TC_2026)
    raw2 = _raw(home)
    assert len(raw2) == 25
    assert {r[0]: r[3] for r in raw2 if r[0] in first_seen} == first_seen
    assert [r[0] for r in raw2 if r[2] == "20261013"] == ["20261105"]


def _bad_pages() -> dict[str, object]:
    good = _rows("20261012", 24)
    gap = [r for r in good if r["bass_dt"] != "20261015"]
    late = _rows("20261013", 23)
    bad_yn = [dict(r) for r in good]
    bad_yn[3]["opnd_yn"] = "?"
    return {
        "empty": _ok([]),
        "gap": _ok(gap),
        "weekend_open": _ok(_rows("20261012", 24, opened=("20261017",))),
        "wrong_start": _ok(late),
        "bad_opnd_yn": _ok(bad_yn),
        "rt_cd_error": {"rt_cd": "1", "msg_cd": "OPSQ0002", "msg1": "없는 서비스", "output": []},
        "output_is_object": {"rt_cd": "0", "msg_cd": "KIOK0500", "output": good[0]},
    }


@pytest.mark.parametrize("kind", sorted(_bad_pages()))
def test_invalid_page_changes_nothing_and_is_crit(api_mod, monkeypatch, tmp_path, kind):
    """① — 구간의 모든 날짜·주말 휴장·요청 날짜에서 시작·응답 형태 중 하나라도 어기면 그날 창은 무효."""
    home = _home(tmp_path)
    before = (home / "data" / "calendar" / "kis_holidays_2026.json").read_bytes()
    seen: list[str] = []
    monkeypatch.setattr(api_mod, "kis", _fake_kis({"20261012": _bad_pages()[kind]}, seen))

    rep = cr.run(home, dt.date(2026, 10, 12), read_trading_days=_TC_2026)

    w = _by(rep)["window"]
    assert w.severity is cr.Severity.CRIT and "20261012" in w.detail
    assert rep.rc == 2 and seen == ["20261012"]                 # 같은 날 재시도 0
    assert (home / "data" / "calendar" / "kis_holidays_2026.json").read_bytes() == before
    assert _raw(home) == []                                      # 부분 저장 0
    [r] = _runs(home, cr.SRC_WINDOW)
    assert (r.status, r.n_calls) == ("failed", 1)


def test_token_expiry_is_reissued_once(api_mod, monkeypatch, tmp_path):
    home = _home(tmp_path)
    answers = [{"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."},
               _ok(_rows("20261012", 24))]
    seen: list[str] = []

    def kis(url: str, tr_id: str, params: dict[str, str]) -> object:
        seen.append(params["BASS_DT"])
        return answers.pop(0)
    monkeypatch.setattr(api_mod, "kis", kis)

    rep = cr.run(home, dt.date(2026, 10, 12), read_trading_days=_TC_2026)

    assert _by(rep)["window"].severity is cr.Severity.OK and rep.n_calls == 2
    assert seen == ["20261012", "20261012"]


def test_network_exception_is_call_failed(api_mod, monkeypatch, tmp_path):
    home = _home(tmp_path)

    def kis(url: str, tr_id: str, params: dict[str, str]) -> object:
        raise ConnectionError("Read timed out")
    monkeypatch.setattr(api_mod, "kis", kis)

    rep = cr.run(home, dt.date(2026, 10, 12), read_trading_days=_TC_2026)

    w = _by(rep)["window"]
    assert w.severity is cr.Severity.CRIT and "call_failed" in w.detail and "ConnectionError" in w.detail
    assert rep.n_calls == 1 and _raw(home) == []


def test_token_still_expired_after_reissue_is_call_failed(api_mod, monkeypatch, tmp_path):
    home = _home(tmp_path)
    seen: list[str] = []

    def kis(url: str, tr_id: str, params: dict[str, str]) -> object:
        seen.append(params["BASS_DT"])
        return {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."}
    monkeypatch.setattr(api_mod, "kis", kis)

    rep = cr.run(home, dt.date(2026, 10, 12), read_trading_days=_TC_2026)

    w = _by(rep)["window"]
    assert w.severity is cr.Severity.CRIT and "verdict=token" in w.detail
    assert seen == ["20261012", "20261012"] and rep.n_calls == 2      # 재발급 1회뿐


def test_exception_after_runlog_start_closes_the_run_as_failed(api_mod, monkeypatch, tmp_path):
    """하-6 — 원장 적재 중 예외가 나도 런 로그가 'running' 으로 남지 않는다. 예외는 main 이 rc 2 로 올린다."""
    home = _home(tmp_path)
    monkeypatch.setattr(api_mod, "kis", _fake_kis({"20261012": _ok(_rows("20261012", 24))}, []))

    def boom(db: Path, page: cr.Page) -> int:
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(cr, "store_raw", boom)

    with pytest.raises(sqlite3.OperationalError):
        cr.run(home, dt.date(2026, 10, 12), read_trading_days=_TC_2026)
    [r] = _runs(home, cr.SRC_WINDOW)
    assert (r.status, r.n_calls) == ("failed", 1) and "database is locked" in (r.detail or "")

    monkeypatch.setattr(cr, "_today_kst", lambda: dt.date(2026, 10, 13))
    monkeypatch.setattr(api_mod, "kis", _fake_kis({"20261013": _ok(_rows("20261013", 24))}, []))
    assert cr.main(["--home", str(home), "--equity-root", str(tmp_path / "none")]) == 2


def test_store_raw_adds_a_new_response_column(api_mod, tmp_path):
    db = tmp_path / "kis.db"
    rows = _rows("20261012", 2)
    cr.store_raw(db, cr.validate_page("20261012", rows))
    more = [{**r, "new_flag": "1"} for r in _rows("20261012", 3)]
    gained = cr.store_raw(db, cr.validate_page("20261012", more))
    con = sqlite3.connect(db)
    cols = [str(c[1]) for c in con.execute("PRAGMA table_info(kis_holiday)")]
    assert "new_flag" in cols
    assert gained == 3            # 열이 늘면 해시가 달라져 새 사실로 남는다(append-only)
    con.close()


# ── ⑥ 휴장 목록 감소 ─────────────────────────────────────────────────────────────
def test_removed_holiday_is_applied_with_warn(api_mod, monkeypatch, tmp_path):
    home = _home(tmp_path, {2026: (*_HOL_2026, "20261020")})
    monkeypatch.setattr(api_mod, "kis", _fake_kis({"20261012": _ok(_rows("20261012", 24))}, []))

    rep = cr.run(home, dt.date(2026, 10, 12),
                 read_trading_days=_trading_days({2026: (*_HOL_2026, "20261020")}, dt.date(2026, 10, 8)))

    f = _by(rep)
    assert f["holidays.removed"].severity is cr.Severity.WARN
    assert f["holidays.removed"].value == ["20261020"]
    assert "20261020" not in _year_holidays(home, 2026)
    assert rep.rc == 1


# ── 점검 모드(읽기 전용) ───────────────────────────────────────────────────────────
def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_check_mode_calls_once_and_writes_nothing(api_mod, monkeypatch, tmp_path, capsys):
    home = _home(tmp_path)
    (home / "data" / "raw").mkdir(parents=True)
    runlog.finish(home / "data" / "raw" / "daily_run.db",
                  runlog.start(home / "data" / "raw" / "daily_run.db", date="20261012", source=cr.SRC_WINDOW),
                  status="ok", n_calls=1)                         # 06:00 이 이미 불렀다
    before = _snapshot(home)
    seen: list[str] = []
    monkeypatch.setattr(api_mod, "kis", _fake_kis(
        {"20261012": _ok(_rows("20261012", 24, closed=("20261020",)))}, seen))

    rep = cr.run(home, dt.date(2026, 10, 12), check=True, read_trading_days=_TC_2026)

    assert seen == ["20261012"]                                   # 명시적 점검은 부른다
    assert _snapshot(home) == before                              # 원장·달력·런 로그·보고서 무변경
    w = _by(rep)["window"]
    assert _value(w)["added"] == ["20261020"] and "오늘 2번째" in w.detail
    assert rep.check


# ── 이듬해 이어 받기 · ④ 기한 ─────────────────────────────────────────────────────
def test_next_year_paging_starts_on_nov_21_from_jan_1(api_mod, monkeypatch, tmp_path):
    home = _home(tmp_path)
    seen: list[str] = []
    monkeypatch.setattr(api_mod, "kis", _fake_kis({
        "20261120": _ok(_rows("20261120", 24, closed=_HOL_2026)),
        "20261121": _ok(_rows("20261121", 24, closed=_HOL_2026)),
        "20270101": _ok(_rows("20270101", 24, closed=_HOL_2027))}, seen))
    tc = _trading_days({2026: _HOL_2026}, dt.date(2026, 11, 19))

    cr.run(home, dt.date(2026, 11, 20), read_trading_days=tc)
    assert seen == ["20261120"]                                   # 11-20 은 아직 아니다

    rep = cr.run(home, dt.date(2026, 11, 21), read_trading_days=tc)
    assert seen == ["20261120", "20270101", "20261121"]          # 이어 받기 → 창 순서
    nxt = _by(rep)["next_year"]
    assert nxt.severity is cr.Severity.OK and nxt.value == {"year": "2027", "n_days": 24, "need": 365}
    staged = json.loads((home / "data" / "calendar" / "direct" / "next_2027.json").read_text(encoding="utf-8"))
    assert min(staged["days"]) == "20270101" and max(staged["days"]) == "20270124"
    assert not (home / "data" / "calendar" / "kis_holidays_2027.json").exists()
    [r] = _runs(home, cr.SRC_NEXT)
    assert (r.date, r.status, r.n_calls) == ("20261121", "ok", 1)


def _stage(home: Path, year: int, until: str, weekdays: tuple[str, ...]) -> None:
    """이어 받기 누적분을 1/1 ~ until 까지 미리 심는다(앞선 날들의 페이지를 받은 상태)."""
    days: dict[str, str] = {}
    d = dt.date(year, 1, 1)
    closed = set(_closed(year, weekdays))
    while d.strftime("%Y%m%d") <= until:
        s = d.strftime("%Y%m%d")
        days[s] = "N" if s in closed else "Y"
        d += dt.timedelta(days=1)
    p = home / "data" / "calendar" / "direct" / f"next_{year}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"year": str(year), "days": days, "pages": []}), encoding="utf-8")


def test_next_year_is_published_when_the_year_is_full(api_mod, monkeypatch, tmp_path):
    home = _home(tmp_path)
    _stage(home, 2027, "20271210", _HOL_2027)
    seen: list[str] = []
    monkeypatch.setattr(api_mod, "kis", _fake_kis({
        "20261203": _ok(_rows("20261203", 24, closed=_HOL_2026)),
        "20271211": _ok(_rows("20271211", 24, closed=(*_HOL_2027, "20280101")))}, seen))

    rep = cr.run(home, dt.date(2026, 12, 3),
                 read_trading_days=_trading_days({2026: _HOL_2026}, dt.date(2026, 12, 2)))

    assert seen == ["20271211", "20261203"]
    f = _by(rep)
    assert f["next_year"].severity is cr.Severity.OK and "게시" in f["next_year"].detail
    assert _year_holidays(home, 2027) == _closed(2027, _HOL_2027)   # 2028 행은 버린다
    assert f["next_year.deadline"].severity is cr.Severity.OK
    assert "2027" in cal.load(home / "data" / "calendar").years
    assert rep.rc == 0


def test_full_year_that_fails_validation_is_not_published(api_mod, monkeypatch, tmp_path):
    home = _home(tmp_path)
    _stage(home, 2027, "20271210", _HOL_2027)
    staged = home / "data" / "calendar" / "direct" / "next_2027.json"
    data = json.loads(staged.read_text(encoding="utf-8"))
    del data["days"]["20270315"]                                  # 누적분에 빈 날(손상된 누적 파일)
    staged.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(api_mod, "kis", _fake_kis({
        "20261203": _ok(_rows("20261203", 24, closed=_HOL_2026)),
        "20271211": _ok(_rows("20271211", 24, closed=_HOL_2027))}, []))

    rep = cr.run(home, dt.date(2026, 12, 3),
                 read_trading_days=_trading_days({2026: _HOL_2026}, dt.date(2026, 12, 2)))

    nxt = _by(rep)["next_year"]
    assert nxt.severity is cr.Severity.CRIT and "20270315" in nxt.detail
    assert not (home / "data" / "calendar" / "kis_holidays_2027.json").exists()
    assert rep.rc == 2


@pytest.mark.parametrize(("today", "years", "severity"), [
    (dt.date(2026, 12, 14), {"2026"}, cr.Severity.OK),
    (dt.date(2026, 12, 15), {"2026"}, cr.Severity.CRIT),
    (dt.date(2026, 12, 31), {"2026"}, cr.Severity.CRIT),
    (dt.date(2026, 12, 15), {"2026", "2027"}, cr.Severity.OK),
    (dt.date(2027, 1, 2), {"2026", "2027"}, cr.Severity.OK),
    (dt.date(2027, 1, 2), {"2026"}, cr.Severity.CRIT),       # 연도 경계를 이미 넘었다 — 옛 코드는 거짓 OK
    (dt.date(2027, 6, 1), {"2026", "2028"}, cr.Severity.CRIT),
])
def test_next_year_deadline_is_dec_15(today, years, severity):
    assert cr.deadline_finding(today, frozenset(years)).severity is severity


def test_missing_this_year_is_crit_with_the_recovery_procedure():
    f = cr.deadline_finding(dt.date(2027, 1, 2), frozenset({"2026"}))
    assert "올해 2027" in f.detail and "§8" in f.detail and "data/calendar/v3/" in f.detail
    g = cr.deadline_finding(dt.date(2026, 12, 15), frozenset({"2026"}))
    assert "§8" in g.detail


@pytest.mark.parametrize(("today", "years", "want"), [
    (dt.date(2027, 1, 2), {"2026"}, "2027"),                  # 올해 판 없음 → 올해부터(스스로 회복)
    (dt.date(2027, 3, 2), {"2026", "2028"}, "2027"),
    (dt.date(2026, 11, 20), {"2026"}, None),
    (dt.date(2026, 11, 21), {"2026"}, "2027"),
    (dt.date(2026, 11, 21), {"2026", "2027"}, None),
])
def test_next_year_target(today, years, want):
    assert cr.next_year_target(today, frozenset(years)) == want


def test_missing_this_year_is_paged_from_jan_1(api_mod, monkeypatch, tmp_path):
    """연도 경계 사고(2027-01-02 에 2027 판 없음) — 창은 부르지 않고 2027 을 1/1 부터 이어 받는다."""
    home = _home(tmp_path)
    seen: list[str] = []
    monkeypatch.setattr(api_mod, "kis", _fake_kis(
        {"20270101": _ok(_rows("20270101", 24, closed=_HOL_2027))}, seen))

    rep = cr.run(home, dt.date(2027, 1, 2),
                 read_trading_days=_trading_days({2026: _HOL_2026}, dt.date(2026, 12, 30)))

    assert seen == ["20270101"]
    f = _by(rep)
    assert f["window"].severity is cr.Severity.CRIT and "§8" in f["window"].detail
    assert f["next_year"].value == {"year": "2027", "n_days": 24, "need": 365}
    assert f["next_year.deadline"].severity is cr.Severity.CRIT and rep.rc == 2


def test_unpublished_next_year_page_is_not_staged(api_mod, monkeypatch, tmp_path):
    """I-2 — KIS 가 이듬해 휴장을 아직 안 실었으면 1/1 신정이 개장(Y)으로 온다. 누적하지 않고 warn, 커서 유지."""
    home = _home(tmp_path)
    seen: list[str] = []
    monkeypatch.setattr(api_mod, "kis", _fake_kis({
        "20261121": _ok(_rows("20261121", 24, closed=_HOL_2026)),
        "20261122": _ok(_rows("20261122", 24, closed=_HOL_2026)),
        "20270101": _ok(_rows("20270101", 24))}, seen))          # 주말만 휴장 — 신정이 개장
    tc = _trading_days({2026: _HOL_2026}, dt.date(2026, 11, 20))

    rep = cr.run(home, dt.date(2026, 11, 21), read_trading_days=tc)

    nxt = _by(rep)["next_year"]
    assert nxt.severity is cr.Severity.WARN and "미게시로 보임" in nxt.detail and "20270101" in nxt.detail
    assert not (home / "data" / "calendar" / "direct" / "next_2027.json").exists()
    assert {r[2] for r in _raw(home)} == {"20261121", "20270101"}      # 원장 적재는 한다
    cr.run(home, dt.date(2026, 11, 22), read_trading_days=tc)
    assert seen == ["20270101", "20261121", "20270101", "20261122"]     # 다음 날 같은 커서


def test_next_year_page_failure_is_warn_and_keeps_the_cursor(api_mod, monkeypatch, tmp_path):
    home = _home(tmp_path)
    _stage(home, 2027, "20270124", _HOL_2027)
    before = (home / "data" / "calendar" / "direct" / "next_2027.json").read_bytes()
    seen: list[str] = []
    monkeypatch.setattr(api_mod, "kis", _fake_kis({
        "20261122": _ok(_rows("20261122", 24, closed=_HOL_2026)),
        "20270125": _ok([])}, seen))                              # 빈 응답 = ① 실패

    rep = cr.run(home, dt.date(2026, 11, 22),
                 read_trading_days=_trading_days({2026: _HOL_2026}, dt.date(2026, 11, 20)))

    nxt = _by(rep)["next_year"]
    assert nxt.severity is cr.Severity.WARN and "다음 날 같은 커서" in nxt.detail
    assert (home / "data" / "calendar" / "direct" / "next_2027.json").read_bytes() == before
    [r] = _runs(home, cr.SRC_NEXT)
    assert r.status == "failed" and rep.rc == 1


def test_next_year_already_called_today_is_skipped(api_mod, monkeypatch, tmp_path):
    home = _home(tmp_path)
    db = home / "data" / "raw" / "daily_run.db"
    runlog.finish(db, runlog.start(db, date="20261121", source=cr.SRC_NEXT), status="failed", n_calls=1)
    seen: list[str] = []
    monkeypatch.setattr(api_mod, "kis", _fake_kis(
        {"20261121": _ok(_rows("20261121", 24, closed=_HOL_2026))}, seen))

    rep = cr.run(home, dt.date(2026, 11, 21),
                 read_trading_days=_trading_days({2026: _HOL_2026}, dt.date(2026, 11, 20)))

    assert seen == ["20261121"]                                   # 창만 부른다
    assert _by(rep)["next_year"].severity is cr.Severity.INFO


def test_publish_runs_before_the_window_so_year_end_lands_the_same_day(api_mod, monkeypatch, tmp_path):
    """Minor 5 — 이듬해 판이 게시되는 날, 창이 덮는 이듬해 날짜(새 지정 01-05)도 그날 반영된다."""
    home = _home(tmp_path)
    _stage(home, 2027, "20271210", _HOL_2027)
    monkeypatch.setattr(api_mod, "kis", _fake_kis({
        "20271211": _ok(_rows("20271211", 24, closed=_HOL_2027)),
        "20261220": _ok(_rows("20261220", 24, closed=(*_HOL_2026, *_HOL_2027, "20270105")))}, []))

    rep = cr.run(home, dt.date(2026, 12, 20),
                 read_trading_days=_trading_days({2026: _HOL_2026}, dt.date(2026, 12, 18)))

    assert "20270105" in _year_holidays(home, 2027)
    assert _value(_by(rep)["window"])["not_applied_years"] == []


def test_window_across_the_year_boundary_updates_both_files(api_mod, monkeypatch, tmp_path):
    home = _home(tmp_path, {2026: _HOL_2026, 2027: _HOL_2027})
    monkeypatch.setattr(api_mod, "kis", _fake_kis({"20261220": _ok(_rows(
        "20261220", 24, closed=(*_HOL_2026, "20261228", *_HOL_2027, "20270105")))}, []))

    rep = cr.run(home, dt.date(2026, 12, 20),
                 read_trading_days=_trading_days({2026: _HOL_2026}, dt.date(2026, 12, 18)))

    assert _value(_by(rep)["window"])["added"] == ["20261228", "20270105"]
    assert "20261228" in _year_holidays(home, 2026) and "20270105" in _year_holidays(home, 2027)


# ── ③ 지난 거래일 = trading_calendar ─────────────────────────────────────────────
def _cal2026(*extra: str) -> cal.Calendar:
    return cal.Calendar(frozenset(_closed(2026, (*_HOL_2026, *extra))), "kis_cache",
                        years=frozenset({"2026"}))


def test_trading_calendar_matches():
    f = cr.compare_trading_calendar(_cal2026(), _TC_2026(Path(".")))
    assert f.severity is cr.Severity.OK and "2026-01-01" in f.detail and "2026-10-08" in f.detail


def test_trading_calendar_mismatch_both_directions_is_crit():
    dates = [d for d in _TC_2026(Path(".")) if d != dt.date(2026, 9, 23)] + [dt.date(2026, 10, 5)]
    f = cr.compare_trading_calendar(_cal2026(), sorted(dates))
    assert f.severity is cr.Severity.CRIT
    assert f.value == {"only_calendar": ["20260923"], "only_trading_calendar": ["20261005"]}


def test_trading_calendar_unreadable_is_crit_not_pass(api_mod, monkeypatch, tmp_path):
    home = _home(tmp_path)
    monkeypatch.setattr(api_mod, "kis", _fake_kis({"20261012": _ok(_rows("20261012", 24))}, []))

    def broken(_root: Path) -> list[dt.date]:
        raise FileNotFoundError("input table has no committed build — table=trading_calendar")

    rep = cr.run(home, dt.date(2026, 10, 12), read_trading_days=broken)
    f = _by(rep)["trading_calendar"]
    assert f.severity is cr.Severity.CRIT and "판정 불가" in f.detail and rep.rc == 2


def test_read_trading_calendar_reads_the_current_build(make_stage_tree, tmp_path):
    rows = [{"date": d} for d in _TC_2026(Path("."))[-5:]]
    tree = make_stage_tree(tmp_path, "trading_calendar", rows)
    got = cr.read_trading_calendar(tree.stage_root)
    assert got == [r["date"] for r in rows]


# ── v3 사본 병행 대조(기록) · 달력 없음 ──────────────────────────────────────────────
def test_v3_copy_comparison_is_recorded_not_alerted(api_mod, monkeypatch, tmp_path):
    home = _home(tmp_path)
    v3 = home / "data" / "calendar" / "v3"
    v3.mkdir()
    (v3 / "kis_holidays_2026.json").write_text(
        json.dumps({"year": 2026, "holidays": _closed(2026, _HOL_2026)}), encoding="utf-8")
    monkeypatch.setattr(api_mod, "kis", _fake_kis(
        {"20261012": _ok(_rows("20261012", 24, closed=("20261020",)))}, []))

    rep = cr.run(home, dt.date(2026, 10, 12), read_trading_days=_TC_2026)

    f = _by(rep)["v3_compare"]
    assert f.severity is cr.Severity.INFO
    assert f.value == {"2026": {"match": False, "only_direct": ["20261020"], "only_v3": []}}
    assert rep.rc == 0
    saved = json.loads((home / "logs" / "calendar" / "20261012.json").read_text(encoding="utf-8"))
    assert {x["name"]: x for x in saved["findings"]}["v3_compare"]["value"]["2026"]["match"] is False


def test_same_day_rerun_without_a_call_keeps_the_first_report(api_mod, monkeypatch, tmp_path):
    home = _home(tmp_path)
    monkeypatch.setattr(api_mod, "kis", _fake_kis(
        {"20261012": _ok(_rows("20261012", 24, closed=("20261020",)))}, []))
    cr.run(home, dt.date(2026, 10, 12), read_trading_days=_TC_2026)
    report = home / "logs" / "calendar" / "20261012.json"
    first = report.read_bytes()

    again = cr.run(home, dt.date(2026, 10, 12), read_trading_days=_TC_2026)

    assert again.n_calls == 0 and report.read_bytes() == first


def test_summary_is_one_line():
    rep = cr.RefreshReport("20261012", False, (cr.Finding("window", cr.Severity.CRIT, "a\nb\nc"),), 1)
    assert "\n" not in rep.summary() and "a b c" in rep.summary()


def test_v3_only_year_is_recorded(tmp_path):
    v3 = tmp_path / "v3"
    v3.mkdir()
    for y in (2026, 2027):
        (v3 / f"kis_holidays_{y}.json").write_text(
            json.dumps({"year": y, "holidays": _closed(y, ())}), encoding="utf-8")
    f = cr.compare_v3({"2026": frozenset(_closed(2026, ()))}, v3)
    value = _value(f)
    assert value["2026"] == {"match": True, "only_direct": [], "only_v3": []}
    assert value["2027"] == {"judging": "missing", "n_v3": len(_closed(2027, ()))}


def test_unreadable_judging_calendar_is_crit_without_a_call(api_mod, monkeypatch, tmp_path):
    home = _home(tmp_path, {})
    seen: list[str] = []
    monkeypatch.setattr(api_mod, "kis", _fake_kis({}, seen))

    rep = cr.run(home, dt.date(2026, 10, 12), read_trading_days=_TC_2026)

    assert seen == [] and rep.rc == 2
    assert _by(rep)["calendar.read"].severity is cr.Severity.CRIT


def test_main_check_prints_a_summary_and_returns_rc(api_mod, monkeypatch, tmp_path, capsys,
                                                    make_stage_tree):
    home = _home(tmp_path)
    tree = make_stage_tree(tmp_path / "eq", "trading_calendar",
                           [{"date": d} for d in _TC_2026(Path("."))])
    monkeypatch.setattr(api_mod, "kis", _fake_kis({"20261012": _ok(_rows("20261012", 24))}, []))
    monkeypatch.setattr(cr, "_today_kst", lambda: dt.date(2026, 10, 12))

    rc = cr.main(["--check", "--home", str(home), "--equity-root", str(tree.stage_root)])

    out = capsys.readouterr().out.strip().splitlines()
    assert rc == 0
    assert out[-1].startswith("휴장 달력 20261012 점검: rc=0")
    assert not (home / "logs").exists()
