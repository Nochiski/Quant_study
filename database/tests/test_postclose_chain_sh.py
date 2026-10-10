"""scripts/postclose_chain.sh — 장 마감 체인(컷오버 PR-8) · 그 훅(daily_evening·daily_build) · 16:30 워치독.

정본 `docs/plans/2026-10-10-cutover-track.md` §3 PR-8 · T-2·T-4·T-7·T-26·T-29·T-34 · P9(완료 감지로 잇기).

HOME 을 임시 폴더로 바꿔 `~/quant-ledger` 에 진짜 스크립트를 두고 돌린다(test_build_chain_sh 와 같은 방식).
대역 python 은 `-m <모듈>` 만 가로채 calls.txt 에 '모듈 인자…' 한 줄을 적고 `RC_<모듈(점→밑줄)>` 로 끝낸다.
`-c`·heredoc(거래일·직전 거래일·세션 예외표·런 로그·스냅샷 GC)은 진짜 python·진짜 `src/` 로 넘긴다 — 판정
달력은 임시 루트의 `data/calendar/kis_holidays_2026.json`, 런 로그는 임시 루트의 `data/raw/daily_run.db` 다.
`scripts/v3_post.sh`·`scripts/notify.sh` 는 대역이고, `flock` 은 PATH 대역(맥에는 flock 이 없다)이 calls.txt 에
'flock 인자' 를 적는다 — 락·단계 순서를 한 줄 목록으로 본다. 진짜 flock 테스트는 서버·CI 우분투에서만 돈다.
운영 경로(/tmp 락·서버 원장·v3 quant.db)는 건드리지 않는다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

import pytest
from daily import runlog

DB_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = DB_ROOT / "scripts"
T = "20261008"            # 목요일(거래일)
D_PREV = "20261007"       # 그 직전 거래일
HOLIDAY = "20261009"      # 한글날(금) — 임시 달력에 휴장으로 둔다
KST = dt.timezone(dt.timedelta(hours=9))

_PY = """#!/usr/bin/env bash
if [ "$1" = "-m" ]; then
  shift
  echo "$*" >> "$QL_HOME/calls.txt"
  var="RC_${1//./_}"
  exit "${!var:-0}"
fi
PYTHONPATH="$REAL_SRC" exec "$REAL_PY" "$@"
"""
_V3_POST = """#!/usr/bin/env bash
echo "v3_post.sh $* | QL_V3_POST_CMD=${QL_V3_POST_CMD-unset}" >> calls.txt
exit "${RC_v3_post:-0}"
"""
# 대역 flock — 장 마감 체인 락(fd 7) 비대기는 FLOCK_CHAIN_RC, 빌드 락(fd 6) 비대기는 FLOCK_BUILD_RC 로 끝난다
_FLOCK = """#!/usr/bin/env bash
echo "flock $*" >> "$FLOCK_LOG"
case "$*" in
  "-n 7") exit "${FLOCK_CHAIN_RC:-0}" ;;
  "-n 6") exit "${FLOCK_BUILD_RC:-0}" ;;
esac
exit 0
"""
SHADOW_CONF = "POSTCLOSE_SEND=0\nPOSTCLOSE_V3=shadow\nPOSTCLOSE_V3_POST_CMD=''\n"
LIVE_CONF = "POSTCLOSE_SEND=1\nPOSTCLOSE_V3=in-place\nPOSTCLOSE_V3_POST_CMD='echo post'\n"
STEP_MODULES = ["daily.postclose", "stage", "stage.health", "factor_inputs", "model", "deliver",
                "v3_post.sh"]


class Run(NamedTuple):
    rc: int
    out: str
    calls: list[str]                # calls.txt 줄 — '모듈 인자…' · 'v3_post.sh 인자… | …' · 'flock 인자'
    notify: list[str]               # 줄마다 "등급|제목|본문"
    runs: list[tuple[str, str]]     # 런 로그 (source, status) — 오래된 것부터
    log: str                        # logs/postclose/*.log 전부

    @property
    def mods(self) -> list[str]:
        """락을 뺀 단계 호출의 첫 낱말(모듈·스크립트 이름)."""
        return [c.split()[0] for c in self.calls if not c.startswith("flock ")]

    def call(self, name: str) -> str:
        hits = [c for c in self.calls if c.split()[0] == name]
        assert len(hits) == 1, (name, self.calls)
        return hits[0]

    def titles(self, level: str) -> list[str]:
        return [n.split("|")[1] for n in self.notify if n.startswith(f"{level}|")]


def _root(home: Path, conf: str | None = SHADOW_CONF) -> Path:
    root = home / "quant-ledger"
    for sub in ("scripts", ".venv/bin", "config", "data/calendar", "data/raw", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (home / "tmp").mkdir(exist_ok=True)
    (home / "fakebin").mkdir(exist_ok=True)
    shutil.copy(SCRIPTS / "postclose_chain.sh", root / "scripts" / "postclose_chain.sh")
    stubs = {root / ".venv/bin/python": _PY,
             root / "scripts/v3_post.sh": _V3_POST,
             root / "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2|$3" >> notify.txt\n',
             home / "fakebin/flock": _FLOCK}
    for path, body in stubs.items():
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    (root / "data/calendar/kis_holidays_2026.json").write_text(
        json.dumps({"year": "2026", "holidays": [HOLIDAY]}), encoding="utf-8")
    (home / "v3_quant.db").write_text("", encoding="utf-8")
    if conf is not None:
        (root / "config/postclose_chain.env").write_text(conf, encoding="utf-8")
    return root


def _env(home: Path, **extra: str) -> dict[str, str]:
    root = home / "quant-ledger"
    env = dict(os.environ, HOME=str(home), TMPDIR=str(home / "tmp"),
               PATH=f"{home / 'fakebin'}:{os.environ['PATH']}", FLOCK_LOG=str(root / "calls.txt"),
               REAL_PY=sys.executable, REAL_SRC=str(DB_ROOT / "src"),
               QL_V3_DB=str(home / "v3_quant.db"),
               QL_BUILD_LOCK_FILE=str(home / "build.lock"),
               QL_POSTCLOSE_CHAIN_LOCK_FILE=str(home / "chain.lock"), **extra)
    for k in ("QL_BUILD_LOCK_HELD", "QL_HOME", "PYTHONPATH", "QL_V3_POST_CMD"):
        env.pop(k, None)
    env.update(extra)
    return env


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _runs(root: Path) -> list[tuple[str, str]]:
    db = root / "data/raw/daily_run.db"
    if not db.exists():
        return []
    return [(r.source, r.status) for r in reversed(runlog.recent(db, limit=100))]


def _seed(root: Path, date: str, source: str, status: str) -> None:
    db = root / "data/raw/daily_run.db"
    runlog.finish(db, runlog.start(db, date=date, source=source), status=status)


def _chain(home: Path, *args: str, **env: str) -> Run:
    root = home / "quant-ledger"
    seeded = len(_runs(root))
    p = subprocess.run(["bash", str(root / "scripts/postclose_chain.sh"), *args],
                       env=_env(home, **env), capture_output=True, text=True, timeout=120,
                       check=False)
    log = "".join(_read(f) for f in sorted((root / "logs/postclose").glob("*.log")))
    return Run(p.returncode, p.stdout + p.stderr, _read(root / "calls.txt").splitlines(),
               _read(root / "notify.txt").splitlines(), _runs(root)[seeded:], log)


# ── close: 15:41 장 마감 체인 ①~⑥ ───────────────────────────────────────────────

def test_close_runs_every_step_in_order_and_logs_each_step(tmp_path: Path) -> None:
    """정상 경로 — 수집 → stage 단독 빌드·건전성 → fi → 모델 → 엑셀 → v3 그림자 반영.

    단계마다 런 로그 source 한 행(ok)이 `daily.runlog.POSTCLOSE_STEPS` 순서로 남고, 준비 info 1건.
    산출 루트는 전부 data/model_db(T-3·T-29), fi 는 D' 아침 인계 이력으로 고정한다(T-2).
    """
    _root(tmp_path)
    r = _chain(tmp_path, "close", "--date", T)
    assert r.rc == 0, r.out + r.log
    assert r.mods == STEP_MODULES
    assert r.call("daily.postclose") == f"daily.postclose --date {T}"
    stage = r.call("stage")
    assert ("--table stg_flow_postclose_kiwoom --basis evening --stage-root data/model_db/stage "
            "--snapshot-root data/model_db/snapshots") in stage
    health = r.call("stage.health")
    assert "--stage-root data/model_db/stage --basis evening" in health
    assert f"--date {T}" in health and "--tables stg_flow_postclose_kiwoom" in health
    assert f"--out logs/health/postclose_stage_{T}.json" in health
    assert r.call("factor_inputs") == (
        f"factor_inputs build --date {T} --basis evening --root data/model_db/factor_inputs "
        f"--builds-from data/deliver/history/{D_PREV}_morning.json --calendar-dir data/calendar")
    assert r.call("model") == (f"model build --date {T} --basis evening --root data/model_db/model "
                               "--fi-root data/model_db/factor_inputs")
    assert r.call("deliver") == (
        f"deliver model-daily --date {T} --basis evening --model-root data/model_db/model "
        "--fi-root data/model_db/factor_inputs --out-root data/model_db/deliver")
    assert r.call("v3_post.sh") == (
        f"v3_post.sh --date {T} --basis evening --v3-db {tmp_path / 'v3_quant.db'} "
        "--model-root data/model_db/model --shadow | QL_V3_POST_CMD=unset")
    assert r.runs == [(s, "ok") for s in runlog.POSTCLOSE_STEPS]
    assert r.titles("info")[-1].startswith("장 마감 판 준비 ")
    assert r.titles("crit") == [] and r.titles("warn") == []


def test_build_lock_covers_stage_to_excel_only(tmp_path: Path) -> None:
    """체인 락(fd 7)은 처음에 비대기로, 빌드 락(fd 6)은 ②~⑤ 동안만 — 수집(①)은 빌드 락 밖이라 16:00 창이
    빌드·배포에 묶이지 않고(T-4), v3 반영(⑥)은 빌드 락을 놓은 뒤다."""
    _root(tmp_path)
    r = _chain(tmp_path, "close", "--date", T)
    assert r.rc == 0, r.out + r.log
    order = [c.split(" | ")[0] if c.startswith("v3_post") else c for c in r.calls]
    pick = [c for c in order if c.startswith(("flock", "daily.postclose", "stage ", "deliver",
                                              "v3_post"))]
    assert [c.split(" --")[0] for c in pick] == [
        "flock -n 7", "daily.postclose", "flock -n 6", "stage", "deliver model-daily",
        "flock -u 6", "v3_post.sh"]


@pytest.mark.parametrize(("module", "rc", "source", "name"), [
    ("stage", 1, "postclose_stage", "stage 단독 빌드"),
    ("stage.health", 2, "postclose_stage", "stage 단독 빌드"),
    ("factor_inputs", 1, "postclose_fi", "fi 장 마감 판"),
    ("model", 2, "postclose_model", "모델 장 마감 판"),
    ("deliver", 3, "postclose_excel", "엑셀"),
    ("v3_post.sh", 2, "postclose_v3", "v3 반영"),
])
def test_step_failure_stops_later_steps_with_one_crit(tmp_path: Path, module: str, rc: int,
                                                      source: str, name: str) -> None:
    """단계가 실패하면 뒤 단계는 돌지 않는다 — 그 단계 런 로그는 failed, 뒤 단계 행은 없다. crit 1건(기록만)."""
    _root(tmp_path)
    key = "RC_v3_post" if module == "v3_post.sh" else f"RC_{module.replace('.', '_')}"
    r = _chain(tmp_path, "close", "--date", T, **{key: str(rc)})
    assert r.rc == 2, r.out + r.log
    assert r.mods == STEP_MODULES[:STEP_MODULES.index(module) + 1]
    steps = list(runlog.POSTCLOSE_STEPS)
    i = steps.index(source)
    assert r.runs == [(s, "ok") for s in steps[:i]] + [(source, "failed")]
    assert r.titles("crit") == [f"장 마감 체인 실패: {name}(rc={rc})"]
    assert not any(t.startswith("장 마감 판 준비") for t in r.titles("info"))
    if module != "v3_post.sh":
        assert "flock -u 6" in r.calls       # 실패해도 빌드 락은 놓는다


@pytest.mark.parametrize("rc", [1, 2])
def test_collect_failure_stops_the_chain(tmp_path: Path, rc: int) -> None:
    """① 수집이 실패하면(rc 2 토큰·오류·대상 없음) stage 부터 돌지 않는다 — 수집 런 로그는 수집기가 쓴다."""
    _root(tmp_path)
    r = _chain(tmp_path, "close", "--date", T, RC_daily_postclose=str(rc))
    assert r.rc == 2, r.out + r.log
    assert r.mods == ["daily.postclose"]
    assert r.runs == []
    assert r.titles("crit") == [f"장 마감 체인 실패: 장 마감 수집(rc={rc})"]
    assert "flock -n 6" not in r.calls


def test_session_exception_day_skips_the_whole_chain(tmp_path: Path) -> None:
    """T-26 — 세션 시각이 바뀌는 날 수집기는 rc 3 이고, 체인은 그날 전체를 건너뛴다(기록만: warn 1건).

    rc 3 을 세션 예외로 읽는 근거는 수집기와 같은 달력 모듈의 예외표(`load_session_exceptions`)다.
    """
    root = _root(tmp_path)
    (root / "data/calendar/session_exceptions.json").write_text(
        json.dumps({"days": {T: "시험 — 개장·폐장 1시간 늦춤"}}, ensure_ascii=False), encoding="utf-8")
    r = _chain(tmp_path, "close", "--date", T, RC_daily_postclose="3")
    assert r.rc == 0, r.out + r.log
    assert r.mods == ["daily.postclose"]
    assert r.runs == []
    assert r.titles("warn") == ["장 마감 체인 세션 예외일 — 건너뜀"]
    assert "시험 — 개장·폐장 1시간 늦춤" in r.notify[0]
    assert r.titles("crit") == []


def test_collect_rc3_on_a_normal_day_is_a_failure(tmp_path: Path) -> None:
    """세션 예외일이 아닌 날의 rc 3(수집기 락 경합·15:41 전)은 건너뜀이 아니라 실패다."""
    _root(tmp_path)
    r = _chain(tmp_path, "close", "--date", T, RC_daily_postclose="3")
    assert r.rc == 2, r.out + r.log
    assert r.mods == ["daily.postclose"]
    crit = r.titles("crit")
    assert crit == ["장 마감 체인 실패: 장 마감 수집(rc=3)"]
    assert "세션 예외일 아님" in r.notify[0]


def test_holiday_skips_before_collecting(tmp_path: Path) -> None:
    """휴장일은 수집 전에 건너뛴다(info 1건) — 수집기는 거래일이 아니면 rc 0 이라 체인이 먼저 본다."""
    _root(tmp_path)
    r = _chain(tmp_path, "close", "--date", HOLIDAY)
    assert r.rc == 0, r.out + r.log
    assert r.mods == []
    assert r.titles("info") == ["장 마감 체인 휴장 — 건너뜀"]


def test_shadow_switch_never_sends_or_writes_v3(tmp_path: Path) -> None:
    """T-7 — 그림자 기간(기본 설정): 엑셀은 짓되 --send 없음, v3 는 --shadow, daily_post 명령 없음.
    환경에 QL_V3_POST_CMD 가 남아 있어도 v3_post 에 새지 않는다."""
    _root(tmp_path)
    r = _chain(tmp_path, "close", "--date", T, QL_V3_POST_CMD="echo leaked")
    assert r.rc == 0, r.out + r.log
    assert "--send" not in r.call("deliver")
    v3 = r.call("v3_post.sh")
    assert "--shadow" in v3 and "--v3-post-cmd" not in v3 and v3.endswith("QL_V3_POST_CMD=unset")


@pytest.mark.parametrize("conf", [None, "POSTCLOSE_SEND=yes\nPOSTCLOSE_V3=inplace\n", ""],
                         ids=["no_file", "near_miss_values", "empty_file"])
def test_switch_turns_on_only_with_exact_values(tmp_path: Path, conf: str | None) -> None:
    """설정 파일이 없거나 값이 정확하지 않으면 그림자(미발송·--shadow) — 켜는 쪽만 정확한 값을 요구한다(P1)."""
    _root(tmp_path, conf=conf)
    r = _chain(tmp_path, "close", "--date", T)
    assert r.rc == 0, r.out + r.log
    assert "--send" not in r.call("deliver")
    assert "--shadow" in r.call("v3_post.sh")


def test_live_switch_sends_and_reflects_in_place_with_daily_post(tmp_path: Path) -> None:
    """PR-9 가 켤 자리 — POSTCLOSE_SEND=1 이면 --send, POSTCLOSE_V3=in-place 면 --shadow 없이 제자리 반영 +
    --v3-post-cmd(설정의 명령)."""
    _root(tmp_path, conf=LIVE_CONF)
    r = _chain(tmp_path, "close", "--date", T)
    assert r.rc == 0, r.out + r.log
    assert r.call("deliver").endswith("--out-root data/model_db/deliver --send")
    v3 = r.call("v3_post.sh")
    assert "--shadow" not in v3
    assert "--model-root data/model_db/model --v3-post-cmd echo post |" in v3


def test_chain_lock_busy_skips_with_warn(tmp_path: Path) -> None:
    """같은 T 의 close 가 이미 돌면(체인 락 비대기 실패) 아무 단계도 돌지 않고 warn·rc 3."""
    _root(tmp_path)
    r = _chain(tmp_path, "close", "--date", T, FLOCK_CHAIN_RC="1")
    assert r.rc == 3, r.out + r.log
    assert r.mods == []
    assert r.titles("warn") == ["장 마감 체인 이미 실행 중 — 건너뜀"]


def test_held_build_lock_is_waited_for(tmp_path: Path) -> None:
    """빌드 락을 다른 빌드가 쥐고 있으면 기다렸다 이어서 돈다(시간 한도 없음 — P9, model_daily 와 같은 모양).
    대기 시작은 info 1건으로 남는다."""
    _root(tmp_path)
    r = _chain(tmp_path, "close", "--date", T, FLOCK_BUILD_RC="1")
    assert r.rc == 0, r.out + r.log
    locks = [c for c in r.calls if c.startswith("flock")]
    assert locks == ["flock -n 7", "flock -n 6", "flock 6", "flock -u 6"]
    assert r.mods == STEP_MODULES
    assert "장 마감 체인 빌드 락 대기" in r.titles("info")


def test_dry_run_prints_the_plan_only(tmp_path: Path) -> None:
    """dry-run 은 계획만 — 락·단계·런 로그·알림 없음."""
    root = _root(tmp_path)
    r = _chain(tmp_path, "close", "--date", T, "--dry-run")
    assert r.rc == 0, r.out
    assert r.calls == [] and r.notify == [] and r.runs == []
    assert not (root / "data/raw/daily_run.db").exists()
    assert f"D'={D_PREV}" in r.out and "--shadow" in r.out and "--send" not in r.out


@pytest.mark.parametrize("args", [("refill",), ("morning",), ("bogus", "--date", T),
                                  ("close", "--date", "2026108")])
def test_argument_errors(tmp_path: Path, args: tuple[str, ...]) -> None:
    """refill·morning 은 --date 기본값이 없다(전날 D 를 오늘로 잘못 잡지 않게). 모르는 모드·형식 오류는 rc 2."""
    _root(tmp_path)
    r = _chain(tmp_path, *args)
    assert r.rc == 2, r.out
    assert r.calls == [] and r.notify == []


def test_future_date_is_refused(tmp_path: Path) -> None:
    """아직 오지 않은 T(오타)는 거부한다 — build_chain.sh 와 같은 경계."""
    _root(tmp_path)
    future = (dt.datetime.now(KST) + dt.timedelta(days=2)).strftime("%Y%m%d")
    r = _chain(tmp_path, "close", "--date", future)
    assert r.rc == 2, r.out
    assert r.calls == []


# ── refill: 21:05 저녁 원장 뒤 재반영 ⑦ ─────────────────────────────────────────

def test_refill_reflects_again_when_the_postclose_reflect_was_ok(tmp_path: Path) -> None:
    """그날 ⑥ 의 마지막 런이 ok 면 v3_post --basis evening 을 한 번 더(16:00 컷오프 종목을 21:05 값으로) —
    compat 만, daily_post 없음. 제자리 설정이어도 post 명령은 넘기지 않는다."""
    root = _root(tmp_path, conf=LIVE_CONF)
    _seed(root, T, "postclose_v3", "ok")
    r = _chain(tmp_path, "refill", "--date", T)
    assert r.rc == 0, r.out + r.log
    assert [c for c in r.calls if c.startswith("flock")] == ["flock -n 7"]
    assert r.mods == ["v3_post.sh"]
    v3 = r.call("v3_post.sh")
    assert v3.startswith(f"v3_post.sh --date {T} --basis evening --v3-db ")
    assert "--model-root data/model_db/model" in v3
    assert "--v3-post-cmd" not in v3 and "--shadow" not in v3
    assert r.runs == [("postclose_v3_refill", "ok")]


def test_refill_in_shadow_stays_shadow(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _seed(root, T, "postclose_v3", "ok")
    r = _chain(tmp_path, "refill", "--date", T)
    assert r.rc == 0, r.out + r.log
    assert "--shadow" in r.call("v3_post.sh")


@pytest.mark.parametrize("seed", [None, "failed", "ok_then_failed"])
def test_refill_skips_without_an_ok_postclose_reflect(tmp_path: Path, seed: str | None) -> None:
    """⑥ 의 마지막 런이 ok 가 아니면(없음·실패) 재반영하지 않는다 — 16:xx 에 판이 나가지 않았는데 v3 에 장 마감
    점수를 넣으면 다음 날 대체 발송 엑셀(T-7)과 v3 점수가 갈린다(T-34). info 1건, 런 로그 없음."""
    root = _root(tmp_path)
    if seed == "failed":
        _seed(root, T, "postclose_v3", "failed")
    elif seed == "ok_then_failed":
        _seed(root, T, "postclose_v3", "ok")
        _seed(root, T, "postclose_v3", "failed")
    _seed(root, D_PREV, "postclose_v3", "ok")         # 다른 날의 ok 는 세지 않는다
    r = _chain(tmp_path, "refill", "--date", T)
    assert r.rc == 0, r.out + r.log
    assert r.mods == []
    assert r.runs == []
    assert r.titles("info") == ["장 마감 재반영 건너뜀"]


def test_refill_failure_is_crit(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _seed(root, T, "postclose_v3", "ok")
    r = _chain(tmp_path, "refill", "--date", T, RC_v3_post="2")
    assert r.rc == 2, r.out + r.log
    assert r.runs == [("postclose_v3_refill", "failed")]
    assert r.titles("crit") == ["장 마감 재반영 실패: 21:05 원장 뒤 재반영(rc=2)"]


# ── morning: 다음 날 아침 잇기 ⑧ ───────────────────────────────────────────────

def test_morning_reflects_krx_and_compares_the_boards(tmp_path: Path) -> None:
    """확정판 뒤 v3 아침 KRX 재반영(compat 만, T-34 — 반영 표는 compat 이 고른다)과 두 판 대조(PR-7).
    체인 락은 잡지 않는다."""
    root = _root(tmp_path, conf=LIVE_CONF)
    _seed(root, T, "postclose_model", "ok")
    r = _chain(tmp_path, "morning", "--date", T)
    assert r.rc == 0, r.out + r.log
    assert not any(c.startswith("flock") for c in r.calls)
    assert r.mods == ["v3_post.sh", "daily.board_compare"]
    v3 = r.call("v3_post.sh")
    assert v3 == (f"v3_post.sh --date {T} --basis morning --v3-db {tmp_path / 'v3_quant.db'} "
                  "| QL_V3_POST_CMD=unset")
    assert r.call("daily.board_compare") == (
        f"daily.board_compare --date {T} --evening-root data/model_db --research-root data")
    assert r.runs == [("postclose_v3_morning", "ok"), ("postclose_compare", "ok")]


def test_morning_in_shadow_passes_shadow(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _seed(root, T, "postclose_model", "ok")
    r = _chain(tmp_path, "morning", "--date", T)
    assert r.rc == 0, r.out + r.log
    assert "--basis morning" in r.call("v3_post.sh") and "--shadow" in r.call("v3_post.sh")


def test_morning_without_an_evening_board_skips_only_the_compare(tmp_path: Path) -> None:
    """그날 장 마감 모델 판이 없으면(세션 예외일·체인 실패) 대조만 건너뛴다 — 가격 재반영은 늘 한다(T-34)."""
    _root(tmp_path)
    r = _chain(tmp_path, "morning", "--date", T)
    assert r.rc == 0, r.out + r.log
    assert r.mods == ["v3_post.sh"]
    assert r.runs == [("postclose_v3_morning", "ok")]
    assert "대조 건너뜀" in r.notify[-1]


def test_morning_compare_mismatch_is_warn(tmp_path: Path) -> None:
    """대조 rc 1(미설명·Spearman 하한 미달)은 실패가 아니라 기록 — 런 로그 상태 mismatch, warn·rc 1."""
    root = _root(tmp_path)
    _seed(root, T, "postclose_model", "ok")
    r = _chain(tmp_path, "morning", "--date", T, RC_daily_board_compare="1")
    assert r.rc == 1, r.out + r.log
    assert r.runs == [("postclose_v3_morning", "ok"), ("postclose_compare", "mismatch")]
    assert r.titles("warn") == ["장 마감 판 아침 잇기 — 두 판 대조 불일치"]
    assert "mismatch" in runlog.WARN_STATUSES["postclose_compare"]


def test_morning_v3_failure_does_not_block_the_compare(tmp_path: Path) -> None:
    """두 일은 서로의 소비자가 아니다 — v3 재반영이 실패해도 대조는 돈다. crit 1건·rc 2."""
    root = _root(tmp_path)
    _seed(root, T, "postclose_model", "ok")
    r = _chain(tmp_path, "morning", "--date", T, RC_v3_post="2")
    assert r.rc == 2, r.out + r.log
    assert r.mods == ["v3_post.sh", "daily.board_compare"]
    assert r.runs == [("postclose_v3_morning", "failed"), ("postclose_compare", "ok")]
    assert r.titles("crit") == ["장 마감 판 아침 잇기 실패: v3 아침 KRX 재반영(rc=2)"]


@pytest.mark.skipif(shutil.which("flock") is None,
                    reason="flock(util-linux) 없음(맥) — 서버·CI 우분투에서 돈다")
def test_real_chain_lock_held_by_another_close(tmp_path: Path) -> None:
    """진짜 flock — 다른 프로세스가 체인 락을 쥐고 있으면 close 는 기다리지 않고 rc 3."""
    root = _root(tmp_path)
    (tmp_path / "fakebin/flock").unlink()
    held = tmp_path / "held"
    holder = subprocess.Popen(["flock", str(tmp_path / "chain.lock"), "-c",
                               f"touch '{held}'; sleep 3"])
    try:
        deadline = time.monotonic() + 10
        while not held.exists():
            assert time.monotonic() < deadline, "holder 가 락을 잡지 못했다"
            time.sleep(0.05)
        r = _chain(tmp_path, "close", "--date", T)
    finally:
        holder.wait(timeout=30)
    assert r.rc == 3, r.out + r.log
    assert r.mods == []
    assert _read(root / "calls.txt") == ""


@pytest.mark.skipif(shutil.which("flock") is None,
                    reason="flock(util-linux) 없음(맥) — 서버·CI 우분투에서 돈다")
def test_real_refill_waits_for_a_running_close(tmp_path: Path) -> None:
    """진짜 flock — close 가 돌고 있으면(체인 락) refill 은 끝날 때까지 기다렸다가 ⑥ 결과로 판정한다(P9)."""
    root = _root(tmp_path)
    (tmp_path / "fakebin/flock").unlink()
    held = tmp_path / "held"
    db = root / "data/raw/daily_run.db"
    seed = (f"{sys.executable} -c \"import sys; sys.path.insert(0, '{DB_ROOT / 'src'}'); "
            f"from daily import runlog; db='{db}'; "
            f"runlog.finish(db, runlog.start(db, date='{T}', source='postclose_v3'), status='ok')\"")
    holder = subprocess.Popen(["flock", str(tmp_path / "chain.lock"), "-c",
                               f"touch '{held}'; sleep 2; {seed}"])
    try:
        deadline = time.monotonic() + 10
        while not held.exists():
            assert time.monotonic() < deadline, "holder 가 락을 잡지 못했다"
            time.sleep(0.05)
        r = _chain(tmp_path, "refill", "--date", T)
    finally:
        holder.wait(timeout=30)
    assert holder.returncode == 0
    assert r.rc == 0, r.out + r.log
    assert r.mods == ["v3_post.sh"]          # 기다린 뒤 close 가 남긴 ⑥ ok 를 보고 재반영했다


# ── 훅: daily_evening.sh(⑦) · daily_build.sh(⑧) ──────────────────────────────

_HOOK = ('#!/usr/bin/env bash\n'
         'echo "postclose_chain.sh $*" >> "$HOME/quant-ledger/hook.txt"\necho HOOK_MARK\n')
_EVENING_PY = """#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  case "$2" in
    *is_trading_day*) exit 0 ;;
    *runlog.recent*) exit 1 ;;
    *runlog.start*) echo 7; exit 0 ;;
    *runlog.finish*) exit 0 ;;
  esac
  exec "$REAL_PY" "$@"
fi
if [ "$1" = "-m" ]; then
  [ "$2" = daily.kw_daily ] && exit "${KW_RC:-0}"
  exit 0
fi
exit 0
"""


def _evening(home: Path, *, kw_rc: int = 0, dry: bool = False) -> tuple[int, list[str], str]:
    root = home / "quant-ledger"
    for sub in ("scripts", "logs", ".venv/bin", "data/raw"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (home / "tmp").mkdir(exist_ok=True)
    for name in ("daily_evening.sh", "raw_lock.sh"):
        shutil.copy(SCRIPTS / name, root / "scripts" / name)
    stubs = {".venv/bin/python": _EVENING_PY, "scripts/postclose_chain.sh": _HOOK,
             "scripts/notify.sh": "#!/usr/bin/env bash\nexit 0\n",
             "scripts/sync_calendar.sh": "#!/usr/bin/env bash\nexit 0\n"}
    for rel, body in stubs.items():
        (root / rel).write_text(body, encoding="utf-8")
        (root / rel).chmod(0o755)
    env = dict(os.environ, HOME=str(home), TMPDIR=str(home / "tmp"), QL_RAW_LOCK_HELD="1",
               QL_KW_EVENING_HHMM="0000", REAL_PY=sys.executable, KW_RC=str(kw_rc))
    for k in ("QL_HOME", "PYTHONPATH"):
        env.pop(k, None)
    p = subprocess.run(["bash", str(root / "scripts/daily_evening.sh"), "--date", T,
                        *(["--dry-run"] if dry else [])],
                       env=env, capture_output=True, text=True, timeout=120, check=False)
    log = "".join(_read(f) for f in sorted((root / "logs").glob("daily_evening_*.log")))
    return p.returncode, _read(root / "hook.txt").splitlines(), log


def test_daily_evening_calls_refill_after_the_kiwoom_commit(tmp_path: Path) -> None:
    """⑦ — 21:05 키움 원장 커밋(rc 0)이 끝나면 그 완료를 받아 재반영을 부른다(고정 시각 크론이 아니다).
    잠정 빌드 트리거인 첫 인계 파일 쓰기 뒤에 부르므로 그 시각을 늦추지 않는다."""
    rc, hook, log = _evening(tmp_path)
    assert rc == 0, log
    assert hook == [f"postclose_chain.sh refill --date {T}"]
    first_deliver = log.index("deliver/ledger_evening.json 기록")
    assert first_deliver < log.index("HOOK_MARK")


def test_daily_evening_skips_refill_when_the_kiwoom_branch_failed(tmp_path: Path) -> None:
    rc, hook, log = _evening(tmp_path, kw_rc=1)
    assert rc == 2, log
    assert hook == []


def test_daily_evening_dry_run_never_calls_refill(tmp_path: Path) -> None:
    rc, hook, log = _evening(tmp_path, dry=True)
    assert rc == 0, log
    assert hook == []


_BUILD_PY = """#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  case "$2" in
    *prev_trading_day*) echo 2026-09-23 ;;
  esac
  exit 0
fi
exit 0
"""


def _build(home: Path, *, brc: int = 0, mrc: int = 0) -> tuple[int, list[str], str]:
    root = home / "quant-ledger"
    for sub in ("scripts", "logs", ".venv/bin", "data/raw"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (home / "tmp").mkdir(exist_ok=True)
    for name in ("daily_build.sh", "raw_lock.sh"):
        shutil.copy(SCRIPTS / name, root / "scripts" / name)
    order = 'echo "$(basename "$0") $*" >> "$HOME/quant-ledger/hook.txt"\n'
    stubs = {".venv/bin/python": _BUILD_PY,
             "scripts/postclose_chain.sh": "#!/usr/bin/env bash\n" + order,
             "scripts/build_morning.sh": "#!/usr/bin/env bash\n" + order + f"exit {brc}\n",
             "scripts/model_daily.sh": "#!/usr/bin/env bash\n" + order + f"exit {mrc}\n",
             "scripts/notify.sh": "#!/usr/bin/env bash\nexit 0\n"}
    for rel, body in stubs.items():
        (root / rel).write_text(body, encoding="utf-8")
        (root / rel).chmod(0o755)
    con = sqlite3.connect(root / "data/raw/krx.db")
    con.execute("CREATE TABLE ingest_log (bas_dd TEXT, status TEXT)")
    con.execute("INSERT INTO ingest_log VALUES (?, 'ok')", (T,))
    con.commit()
    con.close()
    env = dict(os.environ, HOME=str(home), TMPDIR=str(home / "tmp"), QL_RAW_LOCK_HELD="1")
    for k in ("QL_HOME", "PYTHONPATH", "QL_SKIP_KW", "QL_FORCE"):
        env.pop(k, None)
    p = subprocess.run(["bash", str(root / "scripts/daily_build.sh"), "--date", T],
                       env=env, capture_output=True, text=True, timeout=120, check=False)
    log = "".join(_read(f) for f in sorted((root / "logs").glob("daily_build_*.log")))
    return p.returncode, _read(root / "hook.txt").splitlines(), log


NO_SQLITE3 = pytest.mark.skipif(shutil.which("sqlite3") is None,
                                reason="sqlite3 CLI 없음 — daily_build 의 KRX 단계가 쓴다")


@NO_SQLITE3
@pytest.mark.parametrize(("brc", "mrc"), [(0, 0), (1, 0), (0, 2)])
def test_daily_build_chains_the_morning_follow_up(tmp_path: Path, brc: int, mrc: int) -> None:
    """⑧ — 확정판이 서면(build_morning rc 0·1) 모델 단계 뒤에 아침 잇기를 부른다. 모델 단계가 실패해도
    부른다(T-34 — 가격 재반영이 아침 모델 실패에 묶이지 않는다)."""
    rc, hook, log = _build(tmp_path, brc=brc, mrc=mrc)
    assert rc in (0, 1), log
    assert hook == [f"build_morning.sh --date {T}", f"model_daily.sh --date {T}",
                    f"postclose_chain.sh morning --date {T}"]


@NO_SQLITE3
def test_daily_build_skips_the_morning_follow_up_without_a_board(tmp_path: Path) -> None:
    """확정 빌드가 실패하면(rc ≥ 2) 아침 잇기를 부르지 않는다 — 어제 판 위에 재반영·대조하지 않는다."""
    rc, hook, log = _build(tmp_path, brc=2)
    assert rc == 2, log
    assert hook == [f"build_morning.sh --date {T}"]


# ── 16:30 워치독 postclose_board ─────────────────────────────────────────────────

_WD_CALENDAR = """import datetime as dt
import os


class _Cal:
    def is_trading_day(self, d):
        return os.environ.get("WD_TRADING", "1") == "1"

    def prev_trading_day(self, d, n=1):
        return d - dt.timedelta(days=1)


def load(path=None):
    return _Cal()


def load_session_exceptions(directory):
    reason = os.environ.get("WD_SESSION", "")
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y%m%d")
    return {today: reason} if reason else {}
"""


def _today() -> str:
    return dt.datetime.now(KST).strftime("%Y%m%d")


def _wd_root(home: Path) -> Path:
    root = home / "quant-ledger"
    for sub in ("scripts", ".venv/bin", "src/daily", "data/raw", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPTS / "watchdog.sh", root / "scripts/watchdog.sh")
    shutil.copy(DB_ROOT / "src/daily/runlog.py", root / "src/daily/runlog.py")
    (root / "src/daily/__init__.py").write_text("", encoding="utf-8")
    (root / "src/daily/calendar.py").write_text(_WD_CALENDAR, encoding="utf-8")
    stubs = {".venv/bin/python": f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n',
             "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2|$3" >> notify.txt\n'}
    for rel, body in stubs.items():
        (root / rel).write_text(body, encoding="utf-8")
        (root / rel).chmod(0o755)
    return root


def _seed_board(root: Path, overrides: dict[str, str] | None = None, *,
                skip: tuple[str, ...] = ()) -> None:
    """오늘(KST) 장 마감 체인 런 로그 — 수집 ok + 체인 단계 전부 ok. overrides 로 상태를 바꾸고, skip 은 뺀다."""
    statuses = {"kiwoom_postclose": "ok", **{s: "ok" for s in runlog.POSTCLOSE_STEPS},
                **(overrides or {})}
    for src, status in statuses.items():
        if src not in skip:
            _seed(root, _today(), src, status)


def _watch(home: Path, **env: str) -> tuple[int, list[str]]:
    root = home / "quant-ledger"
    e = dict(os.environ, HOME=str(home), **env)
    for k in ("QL_HOME", "PYTHONPATH"):
        e.pop(k, None)
    p = subprocess.run(["bash", str(root / "scripts/watchdog.sh"), "postclose_board"],
                       env=e, capture_output=True, text=True, timeout=60, check=False)
    note = _read(root / "notify.txt").splitlines()
    extra = [f"OUT|{p.stdout}{p.stderr}"] if p.returncode not in (0, 2) else []
    return p.returncode, note + extra


def test_watchdog_board_ok(tmp_path: Path) -> None:
    root = _wd_root(tmp_path)
    _seed_board(root)
    rc, note = _watch(tmp_path)
    assert rc == 0, note
    assert len(note) == 1 and note[0].startswith("info|watchdog postclose_board 정상 ")


@pytest.mark.parametrize("status", ["cutoff", "late"])
def test_watchdog_collect_cutoff_is_normal(tmp_path: Path, status: str) -> None:
    """수집의 16:00 컷오프·늦은 시작은 정상 종료다(남은 종목은 21:05 값) — 판이 서면 정상."""
    root = _wd_root(tmp_path)
    _seed_board(root, {"kiwoom_postclose": status})
    rc, note = _watch(tmp_path)
    assert rc == 0, note
    assert f"kiwoom_postclose={status}" in note[0]


@pytest.mark.parametrize(("overrides", "skip", "expect"), [
    ({"postclose_fi": "failed"}, (), "postclose_fi failed"),
    ({"postclose_v3": "running"}, (), "postclose_v3 running"),
    ({}, ("postclose_excel", "postclose_v3"), "postclose_excel 없음"),
    ({"kiwoom_postclose": "error"}, (), "kiwoom_postclose error"),
    ({}, ("kiwoom_postclose",), "kiwoom_postclose 없음"),
], ids=["failed", "running", "missing", "collect_error", "collect_missing"])
def test_watchdog_board_missing_or_failed_is_crit(tmp_path: Path, overrides: dict[str, str],
                                                  skip: tuple[str, ...], expect: str) -> None:
    root = _wd_root(tmp_path)
    _seed_board(root, overrides, skip=skip)
    rc, note = _watch(tmp_path)
    assert rc == 2, note
    assert len(note) == 1
    level, title, body = note[0].split("|", 2)
    assert (level, title) == ("crit", "watchdog: 16:30 까지 장 마감 판 보고 없음/실패")
    assert expect in body


def test_watchdog_no_runs_at_all_is_crit(tmp_path: Path) -> None:
    """체인이 아예 안 돌았으면(크론 누락·런 로그 파일 없음) crit."""
    _wd_root(tmp_path)
    rc, note = _watch(tmp_path)
    assert rc == 2, note
    assert note[0].startswith("crit|watchdog: 16:30 까지 장 마감 판 보고 없음/실패|")


def test_watchdog_last_run_wins(tmp_path: Path) -> None:
    """같은 날 손으로 다시 돌려 성공했으면 마지막 런 기준으로 정상이다."""
    root = _wd_root(tmp_path)
    _seed(root, _today(), "postclose_fi", "failed")
    _seed_board(root)
    rc, note = _watch(tmp_path)
    assert rc == 0, note


def test_watchdog_session_exception_day_is_normal(tmp_path: Path) -> None:
    """T-26 — 세션 예외일엔 체인이 통째로 건너뛰므로 런 로그가 없어도 정상이다."""
    _wd_root(tmp_path)
    rc, note = _watch(tmp_path, WD_SESSION="시험 — 수능")
    assert rc == 0, note
    assert note[0].startswith("info|watchdog postclose_board 정상")
    assert "세션 예외일" in note[0] and "시험 — 수능" in note[0]


def test_watchdog_holiday_is_normal(tmp_path: Path) -> None:
    _wd_root(tmp_path)
    rc, note = _watch(tmp_path, WD_TRADING="0")
    assert rc == 0, note
    assert note == ["info|watchdog postclose_board — 휴장|" + f"{_today()}(KST)는 거래일이 아니다 — 판정 건너뜀"]


def test_shipped_config_is_shadow(tmp_path: Path) -> None:
    """저장소에 실린 설정(config/postclose_chain.env)은 그림자 값이다 — 이 PR 은 발송·제자리 반영을
    켜지 않는다(PR-9 몫)."""
    _root(tmp_path, conf=(DB_ROOT / "config/postclose_chain.env").read_text(encoding="utf-8"))
    r = _chain(tmp_path, "close", "--date", T)
    assert r.rc == 0, r.out + r.log
    assert "--send" not in r.call("deliver")
    v3 = r.call("v3_post.sh")
    assert "--shadow" in v3 and "--v3-post-cmd" not in v3
