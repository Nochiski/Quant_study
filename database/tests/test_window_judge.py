"""daily.window_judge — 연속 창 판정 집계(컷오버 트랙 X-2).

정본 `docs/plans/2026-10-10-cutover-track.md` §3 X-2 · §4 · 로드맵 §8 공통 3. 입력은 전부 tmp 에 실제 모양으로
조립한다 — 런 로그는 진짜 `daily.runlog` 로 쓰고, notify.log 는 `scripts/notify.sh` 가 남기는 한 줄 형식
(`<UTC> <등급> <제목> | <본문>`), 두 판 대조 결과는 PR-7 `daily.board_compare` 의 `compare/<T>.json` 키(schema 2 —
T-36 반영판, 실운영 결과는 `replay: false`).

2026-10 달력: 12(월) 13(화) 14(수) 15(목) 16(금) 17(토) 18(일) 19(월) 20(화) 21(수) 22(목) 23(금) · 09(금) 한글날 휴장.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import sys
from pathlib import Path

import pytest
from daily import runlog
from daily import window_judge as wj

_SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")
NOTIFY_SH = Path(__file__).resolve().parents[1] / "scripts" / "notify.sh"

HOLIDAYS_2026 = ("20261009",)


# ── 픽스처 조립 ────────────────────────────────────────────────────────────
def _home(tmp_path: Path, *, holidays: tuple[str, ...] = HOLIDAYS_2026,
          session: dict[str, str] | None = None) -> Path:
    home = tmp_path / "ql"
    cal = home / "data" / "calendar"
    cal.mkdir(parents=True)
    (cal / "kis_holidays_2026.json").write_text(
        json.dumps({"year": 2026, "holidays": list(holidays)}), encoding="utf-8")
    if session is not None:
        (cal / "session_exceptions.json").write_text(
            json.dumps({"days": session}, ensure_ascii=False), encoding="utf-8")
    (home / "logs").mkdir()
    # 운영 notify.log 는 판정 범위 앞부터 이어진다 — 첫 줄이 범위 시작 뒤면 판정 불가(m-3)
    (home / "logs" / "notify.log").write_text("2026-10-01T00:00:00Z info 시험 기준 줄(판정 범위 앞) | x\n",
                                              encoding="utf-8")
    runlog.recent(home / "data" / "raw" / "daily_run.db", limit=1)   # 빈 런 로그(표만)
    wj.init_ledger(home / wj.LEDGER)                                    # 그림자 시작일의 record --init
    return home


def _chain(home: Path, d: str, statuses: dict[str, str | None] | None = None) -> None:
    """그날 장 마감 체인 단계 런. 기본은 전부 ok, None 이면 그 단계 런 없음, 'running' 이면 닫지 않는다."""
    db = home / "data" / "raw" / "daily_run.db"
    statuses = statuses or {}
    for src in wj.CHAIN_SOURCES:
        st = statuses.get(src, "ok")
        if st is None:
            continue
        rid = runlog.start(db, date=d, source=src)
        if st != "running":
            runlog.finish(db, rid, status=st)


def _compare(home: Path, d: str, *, verdict: str = "pass", reasons: tuple[str, ...] = (),
             n_unexplained: int = 0, error: str = "", **over: object) -> Path:
    """PR-7 `compare/<T>.json` — `Result.to_dict` 의 판정 키(schema 2: replay·thresholds 포함)와 `_write_error` 의
    rc 2 모양."""
    p = home / "data" / "model_db" / "compare" / f"{d}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    base: dict[str, object] = {"schema": 2, "tool": "daily.board_compare", "date": iso,
                               "generated_at": "2026-10-20T00:50:00Z"}
    if verdict == "error":
        payload = {**base, "verdict": "error", "rc": 2, "error": error}
    else:
        payload = {**base, "dprime": iso, "year_boundary": False, "replay": False, "verdict": verdict,
                   "rc": 0 if verdict == "pass" else 1,
                   "thresholds": {"spearman_min": 0.975, "flow_rel_max": 0.10, "flow_flip_max": 0.10},
                   "reasons": list(reasons), "n_unexplained": n_unexplained}
    payload.update(over)
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def _good(home: Path, *days: str) -> None:
    for d in days:
        _chain(home, d)
        _compare(home, d)


def _notify(home: Path, *lines: str) -> None:
    with (home / "logs" / "notify.log").open("a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def _judge(home: Path, start: str, as_of: str, cutover: str | None = None) -> wj.Result:
    return wj.judge(home, wj.parse_date(start), wj.parse_date(as_of),
                    cutover=wj.parse_date(cutover) if cutover else None)


def _day(res: wj.Result, d: str) -> wj.Day:
    return next(x for x in res.days if x.key == d)


# ── 3일 창 ──────────────────────────────────────────────────────────────────
def test_3거래일_연속_통과면_창_통과_rc0(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _good(home, "20261012", "20261013", "20261014")
    res = _judge(home, "20261012", "20261014")
    assert [x.status for x in res.days] == ["pass", "pass", "pass"]
    assert res.streak == ["20261012", "20261013", "20261014"]
    assert (res.verdict, res.rc) == ("pass", 0)


def test_중간_실패면_다음_거래일부터_다시_센다(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _good(home, "20261012", "20261014", "20261015")
    _chain(home, "20261013", {"postclose_fi": "failed", "postclose_model": None,
                              "postclose_excel": None, "postclose_v3": None})
    res = _judge(home, "20261012", "20261015")
    assert _day(res, "20261013").status == "fail"
    assert any("postclose_fi failed" in r for r in _day(res, "20261013").fails)
    # 앞 단계 실패로 뒤 단계 런이 없는 것은 따로 사유로 적지 않는다(당연한 결과)
    assert not any("런 없음" in r for r in _day(res, "20261013").fails)
    assert res.streak == ["20261014", "20261015"]
    assert (res.last_fail, res.restart_from) == ("20261013", "20261014")
    assert (res.verdict, res.rc) == ("not_yet", 1)
    _good(home, "20261016")
    res = _judge(home, "20261012", "20261016")
    assert res.streak == ["20261014", "20261015", "20261016"]
    assert res.rc == 0


def test_뒤에_실패가_오면_통과했던_창도_다시_센다(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _good(home, "20261012", "20261013", "20261014", "20261015")
    _notify(home, "2026-10-15T07:02:00Z crit 장 마감 체인 실패: 엑셀(rc=2) | D=20261015")
    res = _judge(home, "20261012", "20261015")
    assert res.streak == []
    assert (res.last_fail, res.restart_from) == ("20261015", "20261016")
    assert res.rc == 1


def test_세션_예외일과_휴장일은_건너뛰고_연속을_끊지_않는다(tmp_path: Path) -> None:
    home = _home(tmp_path, holidays=("20261009", "20261013"),
                 session={"20261014": "시험용 세션 예외(수능 가정)"})
    _good(home, "20261012", "20261015", "20261016")
    # 세션 예외일: 수집기만 session_exception 으로 남기고 체인은 건너뛴다(PR-8)
    _chain(home, "20261014", {"kiwoom_postclose": "session_exception", "postclose_stage": None,
                              "postclose_fi": None, "postclose_model": None,
                              "postclose_excel": None, "postclose_v3": None})
    res = _judge(home, "20261012", "20261016")
    st = {x.key: (x.status, x.skip) for x in res.days}
    assert st["20261013"] == ("skip", "휴장")
    assert st["20261014"][0] == "skip" and "세션 예외" in str(st["20261014"][1])
    assert res.streak == ["20261012", "20261015", "20261016"]
    assert res.rc == 0


# ── 건너뛴 날 기록의 귀속(T-39) — 직전 거래일 ─────────────────────────────────
def test_토요일_crit_은_금요일_실패(tmp_path: Path) -> None:
    """주말 06:00 체인이 처리하는 D 는 직전 거래일이다 — 토요일 crit 은 금요일 실패(T-39)."""
    home = _home(tmp_path)
    _good(home, "20261014", "20261015", "20261016", "20261019")
    # 토요일 08:10 체인은 금요일 D 의 확정판을 짓는다
    _notify(home, "2026-10-17T00:40:00Z crit daily_build 실패: build_morning(rc=2) | D=20261016")
    res = _judge(home, "20261014", "20261019")
    d16 = _day(res, "20261016")
    assert d16.status == "fail"
    assert any("daily_build 실패" in r and "귀속: 10-17(토" in r and "→ 10-16(금)" in r for r in d16.fails)
    assert _day(res, "20261019").status == "pass"
    assert res.streak == ["20261019"]
    assert res.off_window == []


def test_연휴_중_crit_과_수동_개입은_연휴_전_마지막_거래일_실패(tmp_path: Path) -> None:
    home = _home(tmp_path, holidays=("20261009", "20261013", "20261014", "20261015"))
    _good(home, "20261012", "20261016", "20261019")
    _notify(home, "2026-10-14T01:00:00Z crit 확정판 빌드 실패: equity(rc=2) | 본문",   # 10-14(수) 휴장
            "2026-10-18T02:00:00Z warn 일요일 시험 warn | 본문")          # 10-18(일) → 10-16 경고만
    wj.record(home / wj.LEDGER, date="20261015", what="연휴 중 서버 점검", by="controller")
    res = _judge(home, "20261012", "20261019")
    d12 = _day(res, "20261012")
    assert d12.status == "fail"
    assert any("확정판 빌드 실패" in r and "귀속: 10-14(수" in r and "→ 10-12(월)" in r for r in d12.fails)
    assert any("연휴 중 서버 점검" in r and "귀속: 10-15(목" in r for r in d12.fails)
    d16 = _day(res, "20261016")
    assert d16.status == "pass"
    assert any("일요일 시험 warn" in w and "→ 10-16(금)" in w for w in d16.warns)
    assert res.streak == ["20261016", "20261019"]


def test_세션_예외일_crit_은_직전_거래일_실패(tmp_path: Path) -> None:
    home = _home(tmp_path, session={"20261014": "시험용 세션 예외"})
    _good(home, "20261012", "20261013", "20261015")
    _notify(home, "2026-10-14T06:41:00Z crit v3_post 20261014 morning 실패(gate) rc=2 | 본문")
    res = _judge(home, "20261012", "20261015")
    assert _day(res, "20261013").status == "fail"
    assert res.streak == ["20261015"]


def test_창_시작_전_거래일에_귀속될_기록은_판정_밖으로_보인다(tmp_path: Path) -> None:
    """창 시작이 주말이면 그 주말 기록의 직전 거래일은 창 밖이다 — 버리지 않고 표시한다."""
    home = _home(tmp_path)
    _good(home, "20261012")
    _notify(home, "2026-10-10T00:40:00Z crit daily_build 실패: 창 시작 전 토요일(rc=2) | 본문")   # 10-10(토)
    res = _judge(home, "20261010", "20261012")
    assert _day(res, "20261012").status == "pass"
    assert any("창 시작 전 토요일" in x for x in res.off_window)


# ── 하루 판정 ────────────────────────────────────────────────────────────────
def test_crit_1건이면_그날_실패(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _good(home, "20261012", "20261013")
    _notify(home, "2026-10-13T07:05:00Z crit 장 마감 체인 실패: 모델(rc=2) | D=20261013",
            "2026-10-13T07:06:00Z warn 시험 warn | 본문",
            "2026-10-13T07:07:00Z info 장 마감 판 준비 16:07 | 본문")
    res = _judge(home, "20261012", "20261013")
    d13 = _day(res, "20261013")
    assert d13.status == "fail"
    assert any("장 마감 체인 실패: 모델(rc=2)" in r for r in d13.fails)
    # warn 은 실패가 아니라 보고 대상, info 는 싣지 않는다
    assert any("시험 warn" in w for w in d13.warns)
    assert not any("장 마감 판 준비" in r for r in d13.fails + d13.warns)
    assert _day(res, "20261012").status == "pass"


def test_수동_개입이면_그날_실패(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _good(home, "20261012", "20261013", "20261014")
    rc = wj.main(["record", "--home", str(home), "--date", "20261013",
                  "--what", "postclose_v3 손 재실행", "--by", "controller"])
    assert rc == 0
    lines = (home / wj.LEDGER).read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    assert (rec["date"], rec["what"], rec["by"]) == ("20261013", "postclose_v3 손 재실행", "controller")
    res = _judge(home, "20261012", "20261014")
    assert _day(res, "20261013").status == "fail"
    assert any("수동 개입" in r and "손 재실행" in r for r in _day(res, "20261013").fails)
    assert res.streak == ["20261014"]


def test_대조_미설명이면_실패하고_사유를_그대로_싣는다(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _chain(home, "20261012")
    _compare(home, "20261012", verdict="fail", reasons=("미설명 3건",), n_unexplained=3)
    res = _judge(home, "20261012", "20261012")
    d = _day(res, "20261012")
    assert d.status == "fail"
    assert any("rc 1" in r and "미설명 3건" in r for r in d.fails)


def test_대조_rc2_면_실패로_세고_사유를_그대로_싣는다(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _good(home, "20261012")
    _chain(home, "20261013")
    _compare(home, "20261013", verdict="error",
             error="규칙 판본 다름 — fi rules_version 1.7.0 ≠ 1.8.0(배포가 끼었다)")
    _good(home, "20261014")
    res = _judge(home, "20261012", "20261014")
    d = _day(res, "20261013")
    assert d.status == "fail"
    assert any("rc 2" in r and "rules_version 1.7.0 ≠ 1.8.0" in r for r in d.fails)
    assert res.streak == ["20261014"]
    assert res.rc == 1                    # 대조 rc 2 는 창 실패(재시작)이지 판정 도구의 입력 오류가 아니다


def test_대조_결과가_아직_없으면_미판정이고_연속은_거기서_멈춘다(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _good(home, "20261012", "20261013")
    _chain(home, "20261014")              # 체인은 끝났고 대조는 다음 날 아침
    res = _judge(home, "20261012", "20261014")
    d = _day(res, "20261014")
    assert d.status == "pending"
    assert any("대조" in r and "아직" in r for r in d.pending)
    assert res.streak == ["20261012", "20261013"]
    assert (res.verdict, res.rc) == ("not_yet", 1)


def test_뒤_거래일_대조가_있는데_그날_대조가_없으면_미실행_실패(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _chain(home, "20261012")
    _good(home, "20261013")
    res = _judge(home, "20261012", "20261013")
    d = _day(res, "20261012")
    assert d.status == "fail"
    assert any("미실행" in r for r in d.fails)


def test_체인_런이_하나도_없으면_미판정(tmp_path: Path) -> None:
    """서버 첫 실행 모양 — 장 마감 체인·대조가 아직 없다(10-14 배포 전)."""
    home = _home(tmp_path)
    res = _judge(home, "20261012", "20261016")
    assert [x.status for x in res.days] == ["pending"] * 5
    assert all(any("런 없음" in r for r in x.pending) for x in res.days)
    assert (res.verdict, res.rc) == ("not_yet", 1)


def test_뒤_거래일_체인_런이_있으면_빠진_날은_미실행_실패(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _chain(home, "20261012", {"postclose_excel": None, "postclose_v3": None})
    _chain(home, "20261013", {"postclose_v3": "running"})
    _good(home, "20261014")
    res = _judge(home, "20261012", "20261014")
    assert _day(res, "20261012").status == "fail"
    assert any("postclose_excel" in r and "미실행" in r for r in _day(res, "20261012").fails)
    assert _day(res, "20261013").status == "fail"
    assert any("running" in r or "실행 중" in r for r in _day(res, "20261013").fails)


def test_오늘_체인이_도는_중이면_미판정(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _chain(home, "20261012", {"postclose_excel": "running", "postclose_v3": None})
    res = _judge(home, "20261012", "20261012")
    d = _day(res, "20261012")
    assert d.status == "pending"
    assert any("실행 중" in r for r in d.pending)


def test_재실행으로_회복한_단계도_앞_런_실패면_실패(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _chain(home, "20261012", {"postclose_model": "failed", "postclose_excel": None, "postclose_v3": None})
    _chain(home, "20261012")              # 같은 날 다시 돌려 전부 ok
    _compare(home, "20261012")
    res = _judge(home, "20261012", "20261012")
    assert _day(res, "20261012").status == "fail"


def test_장_마감_수집_cutoff_는_ok_가_아니라_실패(tmp_path: Path) -> None:
    """정본 문구 '단계 전부 ok' — 16:00 컷오프·16:00 뒤 시작은 정상 종료(warn)지만 ok 가 아니다."""
    home = _home(tmp_path)
    _chain(home, "20261012", {"kiwoom_postclose": "cutoff"})
    _compare(home, "20261012")
    res = _judge(home, "20261012", "20261012")
    d = _day(res, "20261012")
    assert d.status == "fail"
    assert any("kiwoom_postclose cutoff" in r for r in d.fails)


# ── UTC → KST 날짜 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize(("stamp", "day"), [
    ("2026-10-13T14:59:59Z", "20261013"),     # 10-13 23:59:59 KST
    ("2026-10-13T15:00:00Z", "20261014"),     # 10-14 00:00:00 KST
    ("2026-10-12T15:30:00Z", "20261013"),     # UTC 로는 10-12 지만 KST 10-13 00:30
])
def test_notify_시각은_UTC_판정_날짜는_KST(tmp_path: Path, stamp: str, day: str) -> None:
    home = _home(tmp_path)
    _good(home, "20261012", "20261013", "20261014")
    _notify(home, f"{stamp} crit 장 마감 체인 실패: 경계 시험(rc=2) | 본문")
    res = _judge(home, "20261012", "20261014")
    assert [x.key for x in res.days if x.status == "fail"] == [day]


def test_일일_리포트_crit_은_세지_않고_판정_밖으로_보인다(tmp_path: Path) -> None:
    """일일 리포트는 요약 줄이라 개별 원인과 이중으로 센다 — 세지 않는다(사용자 10-10)."""
    home = _home(tmp_path)
    _good(home, "20261012", "20261013", "20261014")
    _notify(home,
            "2026-10-13T00:52:00Z crit 일일 리포트 20261012 | 일일 리포트 D=20261012 · 판정 crit",
            "2026-10-14T00:50:00Z crit ⚠ 알림 실패 1 · 일일 리포트 20261013 | 판정 crit")
    res = _judge(home, "20261012", "20261014")
    assert [x.status for x in res.days] == ["pass"] * 3
    assert any("일일 리포트 20261012" in r for r in _day(res, "20261013").ignored)
    assert any("일일 리포트 20261013" in r for r in _day(res, "20261014").ignored)


def test_진짜_일일_리포트와_notify_sh_가_남긴_줄을_읽는다(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`scripts/daily_report.py` main → `scripts/notify.sh`(기록만) 가 실제로 남긴 줄 — 형식 밖이 아니고 세지 않는다."""
    if _SCRIPTS not in sys.path:
        sys.path.insert(0, _SCRIPTS)
    import daily_report as dr

    home = _home(tmp_path)
    (home / "scripts").mkdir()
    os.symlink(NOTIFY_SH, home / "scripts" / "notify.sh")
    monkeypatch.setenv("QL_HOME", str(home))
    monkeypatch.delenv("QL_NOTIFY_TELEGRAM", raising=False)
    db = home / "data" / "raw" / "daily_run.db"
    runlog.finish(db, runlog.start(db, date="20261008", source="kiwoom_fetch"), status="failed")
    assert dr.main(["--date", "20261008", "--home", str(home),
                    "--lock-glob", str(tmp_path / "no_lock_*.lock")]) == 0
    log = wj.read_notify(home / "logs" / "notify.log")
    assert log.unparsed == []
    got = [n for ns in log.by_date.values() for n in ns]
    assert [n.level for n in got] == ["crit"]
    assert got[0].title.endswith("일일 리포트 20261008")
    assert wj.classify_crit(got[0].title) == "excluded"


# ── 세는 crit — 점수에 영향을 주는 경로만(사용자 10-10) ─────────────────────────
@pytest.mark.parametrize(("title", "kind"), [
    # 장 마감 체인 — postclose_chain.sh close·refill·morning(PR-8)
    ("장 마감 체인 실패: fi 장 마감 판(rc=2)", "counted"),
    ("장 마감 체인 실패: 빌드 락 열기(rc=3)", "counted"),
    ("장 마감 재반영 실패: 21:05 원장 뒤 재반영(rc=2)", "counted"),
    ("장 마감 판 아침 잇기 실패: 두 판 대조(rc=2)", "counted"),
    # 연구 아침 빌드 — daily_build.sh · build_morning.sh · build_chain.sh(확정판) · model_daily.sh
    ("daily_build 실패: build_morning(rc=2)", "counted"),
    ("daily_build 중단 — 대상 거래일 산출 실패", "counted"),
    ("daily_build 원장 락 대기 중 날짜가 바뀜(시작 2026-10-14 → 지금 2026-10-15) — 이번 실행 중단", "counted"),
    ("확정 빌드 시작 불가 — 대상일 계산 실패", "counted"),
    ("확정판 빌드 실패: equity(rc=2)", "counted"),
    ("모델 단계 실패: fi(rc=2)", "counted"),
    # v3 반영 — v3_post.sh
    ("v3_post 20261014 evening 실패(gate) rc=2", "counted"),
    # 워치독 — postclose_board · morning_build(확정 빌드 · 확정판 엑셀 발송 장부)
    ("watchdog: 16:30 까지 장 마감 판 보고 없음/실패", "counted"),
    ("watchdog: 10:30 까지 확정 빌드 보고 없음/실패", "counted"),
    ("watchdog: 10:30 까지 확정판 엑셀 발송 기록 없음", "counted"),
    ("watchdog: 확정판 엑셀 발송 여부 판정 불가", "counted"),
    # 수집 단계 — 결손이 점수에 닿으면 아침 빌드·장 마감 판 실패로 잡힌다
    ("daily_evening 실패: DART(rc=2)", "excluded"),
    ("daily_evening 중단 — 캘린더 판정 불가", "excluded"),
    ("daily_ledger 실패: dart(rc=2)", "excluded"),
    ("daily_ledger 중단 — 대상 거래일 산출 실패", "excluded"),
    ("daily_master 실패", "excluded"),
    ("daily_evening 원장 락 대기 중 날짜가 바뀜(시작 2026-10-14 → 지금 2026-10-15) — 이번 실행 중단", "excluded"),
    ("휴장 달력 갱신 crit(rc=2)", "excluded"),
    ("WICS 주간 스냅샷 dt=20261016 실패 rc=2", "excluded"),
    ("watchdog: 21:50 까지 저녁 원장 보고 없음/실패", "excluded"),
    ("watchdog: 토 11:30 까지 WICS 주간 스냅샷 없음/불완전", "excluded"),
    # 21:20 연구 저녁 잠정판 빌드와 그 워치독(그림자 시작 때 중단)
    ("잠정판 빌드 실패: stage(rc=2)", "excluded"),
    ("잠정 빌드 시작 불가 — 저녁 원장 미완료", "excluded"),
    ("watchdog: 23:55 까지 잠정판 보고 없음/실패", "excluded"),
    ("watchdog: 23:30 까지 잠정판 보고 없음/실패", "excluded"),
    # 그 밖 — 백업·수동 도구·요약 줄
    ("backup_raw 실패: online_backup(rc=2)", "excluded"),
    ("compat export 20261014 morning 실패 rc=2", "excluded"),
    ("점수 대조 D=20261014 실패 rc=2", "excluded"),
    ("일일 리포트 20261013", "excluded"),
    ("⚠ 알림 실패 1 · 일일 리포트 20261013", "excluded"),
    # 워치독 제목의 시각이 바뀌어도 같은 분류(HH:MM 일반화)
    ("watchdog: 10:45 까지 확정 빌드 보고 없음/실패", "counted"),
    ("watchdog: 16:40 까지 장 마감 판 보고 없음/실패", "counted"),
    ("watchdog: 23:10 까지 잠정판 보고 없음/실패", "excluded"),
    # 목록 어디에도 없다 — 분류 안 된 crit(그날 실패)
    ("v3 반영 실패: gate(rc=2)", "unclassified"),
    (" 장 마감 체인 실패: 앞 공백", "unclassified"),
    ("새 잡 실패: 무엇(rc=2)", "unclassified"),
])
def test_crit_제목_분류(title: str, kind: str) -> None:
    assert wj.classify_crit(title) == kind


_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _script_crit_titles() -> list[tuple[str, str]]:
    """`scripts/*.sh` 의 `notify.sh crit "<제목>"` 리터럴 접두(첫 `$` 앞)와 `watchdog.sh` `TITLE_BAD=` 값.
    제목이 변수로 시작하면 그 파일의 대입값으로 펼친다($LABEL · $TITLE_BAD · raw_lock.sh 의 $name)."""
    texts = {p.name: p.read_text(encoding="utf-8") for p in sorted(_SCRIPTS_DIR.glob("*.sh"))}
    names = sorted({n for text in texts.values() for n in re.findall(r"raw_lock_acquire (\w+)", text)})
    out: list[tuple[str, str]] = []
    for fname, text in texts.items():
        labels = re.findall(r'\bLABEL="([^"$]+)"', text)
        bads = re.findall(r'\bTITLE_BAD="([^"$]+)"', text)
        for raw in re.findall(r'notify\.sh crit "([^"]*)"', text):
            if raw.startswith("$TITLE_BAD"):
                cands = bads
            elif raw.startswith("$LABEL"):
                cands = [lab + raw[len("$LABEL"):] for lab in labels]
            elif raw.startswith("$name"):
                cands = [n + raw[len("$name"):] for n in names]
            else:
                cands = [raw]
            assert cands, f"{fname}: 변수로 시작하는 crit 제목을 펼치지 못했다 — {raw!r}"
            out += [(fname, c.split("$", 1)[0]) for c in cands]
        out += [(fname, b) for b in bads]
    return out


def test_스크립트_crit_제목은_빠짐없이_분류된다() -> None:
    """새 crit 제목이 생기면 여기서 깨진다 — 세는 목록·제외 목록 중 한 곳에 넣는다(fail-closed, M-3)."""
    titles = _script_crit_titles()
    assert len(titles) >= 20                       # 스크립트를 못 읽어 빈 목록으로 통과하지 않게
    assert any(f == "watchdog.sh" for f, _ in titles)
    left = [(f, t) for f, t in titles if not t.strip() or wj.classify_crit(t) == "unclassified"]
    assert left == [], f"분류 안 된 crit 제목 — COUNTED/EXCLUDED_CRIT_PREFIXES 에 넣을 것: {left}"


def test_DART_crit_만_있는_날은_통과하고_판정_밖으로_보인다(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _good(home, "20261012", "20261013", "20261014")
    _notify(home, "2026-10-13T09:40:00Z crit daily_evening 실패: DART(rc=2) | D=20261013",
            "2026-10-13T21:30:00Z crit daily_ledger 실패: dart(rc=2) | D=20261013")   # 10-14 06:30 KST
    res = _judge(home, "20261012", "20261014")
    assert [x.status for x in res.days] == ["pass"] * 3
    assert res.rc == 0
    assert any("daily_evening 실패: DART" in r for r in _day(res, "20261013").ignored)
    assert any("daily_ledger 실패: dart" in r for r in _day(res, "20261014").ignored)
    assert "판정 밖 crit" in wj.render(res, None)


def test_아침_빌드_crit_이면_실패(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _good(home, "20261012", "20261013")
    _notify(home, "2026-10-13T00:55:00Z crit 확정판 빌드 실패: equity(rc=2) | D=20261012")   # 10-13 09:55 KST
    res = _judge(home, "20261012", "20261013")
    assert _day(res, "20261013").status == "fail"
    assert any("확정판 빌드 실패" in r for r in _day(res, "20261013").fails)


def test_장_마감_체인_crit_이면_실패(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _good(home, "20261012")
    _notify(home, "2026-10-12T12:10:00Z crit 장 마감 재반영 실패: 21:05 원장 뒤 재반영(rc=2) | T=20261012")
    res = _judge(home, "20261012", "20261012")
    assert _day(res, "20261012").status == "fail"


def test_목록_밖_새_제목_crit_은_분류_안_됨으로_실패(tmp_path: Path) -> None:
    """세는 목록·제외 목록 어디에도 없으면 그날 실패(fail-closed, P1 — 리뷰 M-3)."""
    home = _home(tmp_path)
    _good(home, "20261012")
    _notify(home, "2026-10-12T05:00:00Z crit 새 잡 실패: 무엇(rc=2) | 본문")
    res = _judge(home, "20261012", "20261012")
    d = _day(res, "20261012")
    assert d.status == "fail"
    assert any("분류 안 된 crit" in r and "새 잡 실패" in r for r in d.fails)
    assert d.ignored == []


def test_주말_판정_밖_crit_도_직전_거래일에_귀속해_보인다(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _good(home, "20261016")
    _notify(home, "2026-10-16T21:10:00Z crit daily_ledger 실패: dart(rc=2) | D=20261016")   # 10-17(토) 06:10 KST
    res = _judge(home, "20261016", "20261017")
    d = _day(res, "20261016")
    assert d.status == "pass"
    assert any("daily_ledger 실패" in r and "→ 10-16(금)" in r for r in d.ignored)


# ── 되돌리기 창 ──────────────────────────────────────────────────────────────
def test_되돌리기_창은_컷오버일부터_5거래일을_달력으로_센다(tmp_path: Path) -> None:
    home = _home(tmp_path, holidays=("20261009", "20261021"))
    _good(home, "20261014", "20261015", "20261016", "20261019")
    _chain(home, "20261022")              # 체인은 끝났고 대조는 다음 날 아침
    _chain(home, "20261020")
    _compare(home, "20261020", verdict="fail", reasons=("미설명 2건",), n_unexplained=2)
    res = _judge(home, "20261014", "20261022", cutover="20261019")
    rb = res.to_dict()["rollback"]
    assert isinstance(rb, dict)
    # 10-21(수) 휴장 → 5거래일 = 19·20·22·23·26
    assert [x["date"] for x in rb["days"]] == ["20261019", "20261020", "20261022", "20261023", "20261026"]
    assert (rb["last_day"], rb["n_reached"], rb["elapsed"]) == ("20261026", 3, False)
    assert [f["date"] for f in rb["failures"]] == ["20261020"]
    assert "미설명 2건" in " ".join(rb["failures"][0]["reasons"])
    assert [x["status"] for x in rb["days"]] == ["pass", "fail", "pending", "upcoming", "upcoming"]
    _good(home, "20261023", "20261026")
    rb = _judge(home, "20261014", "20261027", cutover="20261019").to_dict()["rollback"]
    assert isinstance(rb, dict)
    assert (rb["n_reached"], rb["elapsed"]) == (5, True)


# ── 입력 오류 · CLI ──────────────────────────────────────────────────────────
def test_notify_log_없음은_입력_오류_rc2(tmp_path: Path) -> None:
    home = _home(tmp_path)
    (home / "logs" / "notify.log").unlink()
    rc = wj.main(["judge", "--home", str(home), "--start", "20261012", "--as-of", "20261014"])
    assert rc == 2
    out = json.loads((home / wj.WINDOW_JSON).read_text(encoding="utf-8"))
    assert (out["verdict"], out["rc"]) == ("error", 2)
    assert "notify.log" in out["error"]


def test_런_로그_없음은_입력_오류(tmp_path: Path) -> None:
    home = _home(tmp_path)
    (home / "data" / "raw" / "daily_run.db").unlink()
    with pytest.raises(wj.InputError, match="daily_run.db"):
        _judge(home, "20261012", "20261014")


def test_장부_깨진_줄은_입력_오류(tmp_path: Path) -> None:
    home = _home(tmp_path)
    (home / wj.LEDGER).write_text('{"date": "20261013", "what": "x"}\n', encoding="utf-8")
    with pytest.raises(wj.InputError, match="장부"):
        _judge(home, "20261012", "20261014")


def test_대조_결과_모양이_다르면_판정_불가_rc2(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _good(home, "20261012", "20261013")
    _chain(home, "20261014")
    _compare(home, "20261014", schema=1)
    res = _judge(home, "20261012", "20261014")
    d = _day(res, "20261014")
    assert d.status == "error"
    assert any("schema" in e for e in d.errors)
    assert (res.verdict, res.rc) == ("error", 2)


def test_재생_대조_결과는_실운영_판정에_쓰지_않는다(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _chain(home, "20261012")
    _compare(home, "20261012", replay=True)
    d = _day(_judge(home, "20261012", "20261012"), "20261012")
    assert d.status == "error"
    assert any("재생" in e for e in d.errors)


def test_대조_pass_인데_미설명이_있으면_판정_불가(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _chain(home, "20261012")
    _compare(home, "20261012", n_unexplained=1)
    assert _day(_judge(home, "20261012", "20261012"), "20261012").status == "error"


def test_달력이_없으면_입력_오류(tmp_path: Path) -> None:
    home = _home(tmp_path)
    (home / "data" / "calendar" / "kis_holidays_2026.json").unlink()
    with pytest.raises(wj.InputError, match="달력"):
        _judge(home, "20261012", "20261014")


def test_main_은_window_json_을_쓰고_dry_run_은_쓰지_않는다(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = _home(tmp_path)
    _good(home, "20261012", "20261013", "20261014")
    out = home / wj.WINDOW_JSON
    assert wj.main(["judge", "--home", str(home), "--start", "20261012", "--as-of", "20261014",
                    "--dry-run"]) == 0
    assert not out.exists()
    assert wj.main(["judge", "--home", str(home), "--start", "20261012", "--as-of", "20261014",
                    "--cutover", "20261014"]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert (payload["schema"], payload["tool"], payload["verdict"], payload["rc"]) == (
        1, "daily.window_judge", "pass", 0)
    assert payload["streak_days"] == ["20261012", "20261013", "20261014"]
    assert [d["status"] for d in payload["days"]] == ["pass"] * 3
    text = capsys.readouterr().out
    assert "통과" in text and "10-13(화)" in text


def test_record_는_빈_값과_틀린_날짜를_거부한다(tmp_path: Path) -> None:
    home = _home(tmp_path)
    assert wj.main(["record", "--home", str(home), "--date", "20261332", "--what", "x",
                    "--by", "y"]) == 2
    assert wj.main(["record", "--home", str(home), "--date", "20261013", "--what", " ",
                    "--by", "y"]) == 2
    assert (home / wj.LEDGER).read_text(encoding="utf-8") == ""


def test_체인_단계_이름은_러너와_같다() -> None:
    from daily import postclose
    assert wj.CHAIN_SOURCES[0] == postclose.SOURCE
    steps = getattr(runlog, "POSTCLOSE_STEPS", None)
    if steps is None:
        pytest.skip("PR-8 머지 전 — runlog.POSTCLOSE_STEPS 없음(머지 때 이 대조가 돈다)")
    assert wj.CHAIN_SOURCES[1:] == tuple(steps)
    assert wj.COMPARE_SOURCE in runlog.POSTCLOSE_FOLLOWUPS


def test_대조_결과_모양은_PR7_과_같다(tmp_path: Path) -> None:
    """PR-7 머지 뒤에 돈다 — 상수 대조, pass·fail 결과가 싣는 키, PR-7 이 실제로 쓰는 rc 2 파일 읽기."""
    import datetime as dt

    bc = pytest.importorskip("daily.board_compare", reason="PR-7 머지 전 — 머지 때 이 대조가 돈다")
    assert (wj.COMPARE_SCHEMA, wj.COMPARE_TOOL, wj.COMPARE_SPEARMAN_MIN) == (bc.SCHEMA, bc.TOOL, bc.SPEARMAN_MIN)
    src = inspect.getsource(bc.Result.to_dict)
    for key in ("schema", "tool", "date", "replay", "verdict", "rc", "reasons", "thresholds", "spearman_min",
                "n_unexplained"):
        assert f'"{key}"' in src, f"PR-7 pass·fail 결과에 {key} 키가 없다 — read_compare 를 맞춘다"
    root = tmp_path / "model_db"
    root.mkdir()
    path = bc._write_error(root, dt.date(2026, 10, 14), "규칙 판본 다름 — 시험")
    payload = wj.read_compare(Path(path), dt.date(2026, 10, 14))
    assert payload is not None and payload["verdict"] == "error"


# ── 리뷰 보강(MAJOR M-1~M-4 · MINOR m-1~m-4 · NIT) ──────────────────────────────
def test_기준일_뒤_주말_crit_도_금요일에_귀속한다(tmp_path: Path) -> None:
    """M-1 — 금요일까지로 월요일에 판정해도(PR-9 판정 명령 --as-of 금요일) 주말 기록이 금요일에 들어간다."""
    home = _home(tmp_path)
    _good(home, "20261014", "20261015", "20261016")
    _notify(home, "2026-10-16T22:10:00Z crit daily_build 실패: build_morning(rc=2) | D=20261016")  # 10-17(토) 07:10
    res = _judge(home, "20261014", "20261016")
    assert _day(res, "20261016").status == "fail"
    assert res.rc == 1
    assert [d.key for d in res.days] == ["20261014", "20261015", "20261016"]   # 창 목록은 기준일까지


def test_기준일_뒤_다음_거래일_기록은_넣지_않는다(tmp_path: Path) -> None:
    """월요일(다음 창 거래일) 기록은 그 날 몫이다 — 금요일까지 판정에 넣지 않는다(KST 날짜 규칙)."""
    home = _home(tmp_path)
    _good(home, "20261014", "20261015", "20261016")
    _notify(home, "2026-10-19T01:30:00Z crit daily_build 실패: build_morning(rc=2) | D=20261016")  # 10-19(월)
    assert _judge(home, "20261014", "20261016").rc == 0


def test_범위_바로_앞_건너뛴_날_기록은_판정_밖으로_보인다(tmp_path: Path) -> None:
    """NIT — 창 시작 직전 평일 휴장·주말 기록은 귀속 거래일이 범위 밖이라 '판정 밖 기록'(버리지 않는다)."""
    home = _home(tmp_path, holidays=("20261009", "20261013"))
    _good(home, "20261014", "20261019")
    _notify(home, "2026-10-13T03:00:00Z crit daily_build 실패: 휴장일(rc=2) | x",     # 10-13(화) 휴장 → 10-12
            "2026-10-18T03:00:00Z crit daily_build 실패: 일요일(rc=2) | x")       # 10-18(일) → 10-16
    res = _judge(home, "20261014", "20261014")
    assert _day(res, "20261014").status == "pass"
    assert any("휴장일" in x and "10-12(월)" in x and "범위 밖" in x for x in res.off_window)
    res = _judge(home, "20261019", "20261019")
    assert any("일요일" in x and "10-16(금)" in x for x in res.off_window)


def test_장부_date_가_문자열이_아니면_입력_오류(tmp_path: Path) -> None:
    """M-2 — 숫자 date 는 조용히 빠지지 않고 판정 불가."""
    home = _home(tmp_path)
    _good(home, "20261012", "20261013", "20261014")
    (home / wj.LEDGER).write_text('{"date": 20261013, "what": "손 재실행", "by": "controller"}\n',
                                  encoding="utf-8")
    with pytest.raises(wj.InputError, match='"date": "YYYYMMDD"'):
        _judge(home, "20261012", "20261014")


def test_장부가_없으면_입력_오류이고_record_init_으로_만든다(tmp_path: Path) -> None:
    """m-4 — 장부 없음은 '개입 0' 이 아니라 판정 불가. record --init 은 빈 장부를 만들고 있으면 그대로 둔다."""
    home = _home(tmp_path)
    _good(home, "20261012")
    (home / wj.LEDGER).unlink()
    assert wj.main(["judge", "--home", str(home), "--start", "20261012", "--as-of", "20261012"]) == 2
    assert "장부 없음" in json.loads((home / wj.WINDOW_JSON).read_text(encoding="utf-8"))["error"]
    assert wj.main(["record", "--home", str(home), "--date", "20261012", "--what", "x", "--by", "y"]) == 2
    assert wj.main(["record", "--home", str(home), "--init"]) == 0
    assert (home / wj.LEDGER).read_text(encoding="utf-8") == ""
    wj.record(home / wj.LEDGER, date="20261012", what="x", by="y")
    assert wj.main(["record", "--home", str(home), "--init"]) == 0       # 있으면 그대로
    assert len((home / wj.LEDGER).read_text(encoding="utf-8").splitlines()) == 1
    assert wj.main(["record", "--home", str(home), "--init", "--date", "20261012"]) == 2


def test_record_는_끝_줄바꿈_없는_장부에_덧붙이지_않는다(tmp_path: Path) -> None:
    """m-2 — 덧붙이면 마지막 줄이 깨진다. 거부하고 장부는 그대로."""
    home = _home(tmp_path)
    body = '{"date": "20261013", "what": "a", "by": "b"}'
    (home / wj.LEDGER).write_text(body, encoding="utf-8")
    assert wj.main(["record", "--home", str(home), "--date", "20261014", "--what", "c", "--by", "d"]) == 2
    assert (home / wj.LEDGER).read_text(encoding="utf-8") == body


def test_record_는_깨진_장부에_덧붙이지_않는다(tmp_path: Path) -> None:
    home = _home(tmp_path)
    (home / wj.LEDGER).write_text("{not json\n", encoding="utf-8")
    assert wj.main(["record", "--home", str(home), "--date", "20261014", "--what", "c", "--by", "d"]) == 2
    assert (home / wj.LEDGER).read_text(encoding="utf-8") == "{not json\n"


def test_record_쓰기_실패는_rc2(tmp_path: Path) -> None:
    """m-1 — OSError 를 잡아 rc 2."""
    home = _home(tmp_path)
    os.chmod(home / wj.LEDGER, 0o400)
    try:
        assert wj.main(["record", "--home", str(home), "--date", "20261014", "--what", "c",
                        "--by", "d"]) == 2
    finally:
        os.chmod(home / wj.LEDGER, 0o600)


def test_오류_판정_window_json_을_못_써도_rc2(tmp_path: Path) -> None:
    """m-1 — 입력 오류 경로의 JSON 쓰기 실패도 예외가 아니라 rc 2."""
    home = _home(tmp_path)
    (home / "logs" / "notify.log").unlink()
    out_dir = (home / wj.WINDOW_JSON).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o500)
    try:
        assert wj.main(["judge", "--home", str(home), "--start", "20261012", "--as-of", "20261014"]) == 2
    finally:
        os.chmod(out_dir, 0o700)


@pytest.mark.parametrize("line", [
    "2026-10-13T07:05:00Z CRIT 장 마감 체인 실패: x | x",
    "2026-10-13 07:05:00 crit 장 마감 체인 실패: x | x",
    "2026-10-13T07:05:00+00:00 crit 장 마감 체인 실패: x | x",
    "\ufeff2026-10-13T07:05:00Z crit BOM | x",
])
def test_ISO_시각으로_시작하는_형식_밖_줄은_판정_불가(tmp_path: Path, line: str) -> None:
    """M-3 (c) — 날짜·등급을 믿을 수 없는 줄을 빼고 통과시키지 않는다."""
    home = _home(tmp_path)
    _good(home, "20261012", "20261013", "20261014")
    _notify(home, line)
    with pytest.raises(wj.InputError, match="형식 밖"):
        _judge(home, "20261012", "20261014")


def test_ISO_로_시작하지_않는_형식_밖_줄은_표시만(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _good(home, "20261012", "20261013", "20261014")
    _notify(home, "garbage line")
    res = _judge(home, "20261012", "20261014")
    assert res.rc == 0 and res.unparsed == ["garbage line"]


def test_notify_log_첫_줄이_범위_시작_뒤면_판정_불가(tmp_path: Path) -> None:
    """m-3 — 파일이 지워졌다 새로 생겼으면 범위 앞부분 crit 이 사라졌을 수 있다."""
    home = _home(tmp_path)
    _good(home, "20261012", "20261013", "20261014")
    (home / "logs" / "notify.log").write_text("2026-10-15T00:00:00Z info 무관 | x\n", encoding="utf-8")
    with pytest.raises(wj.InputError, match="첫 줄"):
        _judge(home, "20261012", "20261014")
    (home / "logs" / "notify.log").write_text("", encoding="utf-8")
    with pytest.raises(wj.InputError, match="줄 없음"):
        _judge(home, "20261012", "20261014")
    # 범위 시작 00:00 KST(= 전날 15:00Z) 바로 앞이면 된다
    (home / "logs" / "notify.log").write_text("2026-10-11T14:59:59Z info 경계 | x\n", encoding="utf-8")
    assert _judge(home, "20261012", "20261014").rc == 0


def test_대조_런을_재실행해_회복해도_실패(tmp_path: Path) -> None:
    """M-4 — 그날 대조 런에 ok 가 아닌 런이 하나라도 있으면 실패(재실행 회복 불인정)."""
    home = _home(tmp_path)
    _good(home, "20261012", "20261013", "20261014")
    db = home / "data" / "raw" / "daily_run.db"
    runlog.finish(db, runlog.start(db, date="20261013", source=wj.COMPARE_SOURCE), status="mismatch")
    runlog.finish(db, runlog.start(db, date="20261013", source=wj.COMPARE_SOURCE), status="ok")
    res = _judge(home, "20261012", "20261014")
    assert _day(res, "20261013").status == "fail"
    assert any("두 판 대조 런 mismatch" in r for r in _day(res, "20261013").fails)
    assert res.rc == 1


def test_대조_pass_에_replay_키가_없으면_판정_불가(tmp_path: Path) -> None:
    """M-4 — 실운영 결과라는 표시(replay false)가 없으면 통과로 인정하지 않는다."""
    home = _home(tmp_path)
    _chain(home, "20261012")
    p = _compare(home, "20261012")
    payload = json.loads(p.read_text(encoding="utf-8"))
    del payload["replay"]
    p.write_text(json.dumps(payload), encoding="utf-8")
    d = _day(_judge(home, "20261012", "20261012"), "20261012")
    assert d.status == "error" and any("replay" in e for e in d.errors)


@pytest.mark.parametrize("thresholds", [{"spearman_min": 0.9}, {}, None, {"spearman_min": "0.975"}])
def test_대조_pass_가_등록_하한보다_낮은_하한이면_판정_불가(tmp_path: Path, thresholds: object) -> None:
    """M-4 — 기록형 하한(--spearman-min 낮춤)으로 낸 pass 는 통과가 아니다."""
    home = _home(tmp_path)
    _chain(home, "20261012")
    _compare(home, "20261012", thresholds=thresholds)
    d = _day(_judge(home, "20261012", "20261012"), "20261012")
    assert d.status == "error" and any("spearman_min" in e for e in d.errors)
