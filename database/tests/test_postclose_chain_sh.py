"""scripts/postclose_chain.sh — 장 마감 체인(컷오버 PR-8) · 그 훅(daily_evening·daily_build) · 16:30 워치독.

정본 `docs/plans/2026-10-10-cutover-track.md` §3 PR-8 · T-2·T-4·T-7·T-26·T-29·T-34·T-37·T-38 · P9(완료 감지로 잇기).

HOME 을 임시 폴더로 바꿔 `~/quant-ledger` 에 진짜 스크립트를 두고 돌린다(test_build_chain_sh 와 같은 방식).
대역 python 은 `-m <모듈>` 만 가로채 calls.txt 에 '모듈 인자…' 한 줄을 적고 `RC_<모듈(점→밑줄)>` 로 끝낸다
(fi 는 판 manifest `_runs/<T>_evening.json` 을, 대조는 `COMPARE_VERDICT` 가 있으면 `compare/<T>.json` 을 쓴다.
`compat` 하위 명령은 실물 v3_post.sh 계약 테스트용 — v3-tables 는 표 목록을 찍고 export 는 `--builds-from` 파일이
없으면 rc 2).
`-c`·heredoc(거래일·직전 거래일·세션 예외표·고정 판·런 로그·스냅샷 GC)은 진짜 python·진짜 `src/` 로 넘긴다 — 판정
달력은 임시 루트의 `data/calendar/kis_holidays_2026.json`, 인계 이력은 `data/deliver/history/`, 런 로그는
`data/raw/daily_run.db` 다. `scripts/v3_post.sh` 대역은 인자를 적고 `--builds-from` 파일이 없으면 rc 2(compat 이
인계 이력을 못 읽고 멈추는 것과 같은 결과)로 끝난다. `flock` 은 PATH 대역(맥에는 flock 이 없다)이 calls.txt 에
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
  d=""; prev=""
  for a in "$@"; do [ "$prev" = "--date" ] && d="$a"; prev="$a"; done
  if [ "$1" = factor_inputs ]; then
    mkdir -p "$QL_HOME/data/model_db/factor_inputs/_runs"
    w=false; [ -n "${FI_WARN:-}" ] && w=true
    printf '{"status": "ok", "gates": [{"name": "FG4", "status": "pass", "detail": "", "metrics": {}},
      {"name": "FG5", "status": "pass", "detail": "후보 600 중 T 가격 없음 0 · warn: 후보 중 T-6 보류 20/600",
       "metrics": {"warn": %s}}]}' "$w" > "$QL_HOME/data/model_db/factor_inputs/_runs/${d}_evening.json"
  fi
  if [ "$1" = daily.board_compare ] && [ -n "${COMPARE_VERDICT:-}" ]; then
    mkdir -p "$QL_HOME/data/model_db/compare"
    printf '{"date": "%s-%s-%s", "verdict": "%s", "rc": %s}' "${d:0:4}" "${d:4:2}" "${d:6:2}" \\
      "$COMPARE_VERDICT" "${RC_daily_board_compare:-0}" > "$QL_HOME/data/model_db/compare/$d.json"
  fi
  if [ "$1" = compat ]; then
    case "$2" in
      v3-tables)
        case " $* " in
          *" --no-scores "*) echo "daily_prices,investor_detail_flows,stocks,consensus_annual" ;;
          *) echo "daily_prices,investor_detail_flows,stocks,consensus_annual,score_history,score_history_v2" ;;
        esac ;;
      export)
        prev=""
        for a in "$@"; do
          if [ "$prev" = "--builds-from" ] && [ ! -f "$a" ]; then
            echo "compat 실패: 인계 이력(--builds-from)이 없다: $a" >&2; exit 2
          fi
          prev="$a"
        done ;;
    esac
    exit 0
  fi
  var="RC_${1//./_}"
  exit "${!var:-0}"
fi
# 조용한 손실 관문(-c … silent_loss …)을 SL_GATE_RC 로 죽인다 — 관문 예외·모듈 import 실패 대역
if [ "$1" = "-c" ] && [ -n "${SL_GATE_RC:-}" ] && [[ "$2" == *silent_loss* ]]; then
  echo "관문 대역 실패(ImportError 흉내)" >&2; exit "$SL_GATE_RC"
fi
PYTHONPATH="$REAL_SRC" exec "$REAL_PY" "$@"
"""
_V3_POST = """#!/usr/bin/env bash
echo "v3_post.sh $* | QL_V3_POST_CMD=${QL_V3_POST_CMD-unset}" >> calls.txt
prev=""
for a in "$@"; do
  if [ "$prev" = "--builds-from" ] && [ ! -f "$a" ]; then
    echo "compat 실패: 인계 이력(--builds-from)이 없다: $a" >&2
    exit 2
  fi
  prev="$a"
done
exit "${RC_v3_post:-0}"
"""
# 대역 flock — 장 마감 체인 락(fd 7) 비대기는 FLOCK_CHAIN_RC, 빌드 락(fd 6) 비대기는
# FLOCK_BUILD_RC 로 끝난다
_FLOCK = """#!/usr/bin/env bash
echo "flock $*" >> "$FLOCK_LOG"
case "$*" in
  "-n 7") exit "${FLOCK_CHAIN_RC:-0}" ;;
  "-n 6") exit "${FLOCK_BUILD_RC:-0}" ;;
esac
exit 0
"""
SHADOW_CONF = ("POSTCLOSE_ENABLED=1\nPOSTCLOSE_SEND=0\nPOSTCLOSE_V3=shadow\n"
               "POSTCLOSE_V3_POST_CMD=''\n")
LIVE_CONF = ("POSTCLOSE_ENABLED=1\nPOSTCLOSE_SEND=1\nPOSTCLOSE_V3=in-place\n"
             "POSTCLOSE_V3_POST_CMD='echo post'\n")
STEP_MODULES = ["daily.postclose", "stage", "stage.health", "factor_inputs", "model", "deliver",
                "v3_post.sh"]
H_PREV = f"data/deliver/history/{D_PREV}_morning.json"
H_T = f"data/deliver/history/{T}_morning.json"


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


def _history(root: Path, d: str, *, health: str = "ok") -> None:
    """인계 이력 `<d>_morning.json` — build_chain deliver_step 이 쓰는 모양."""
    p = root / "data/deliver/history" / f"{d}_morning.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"date": d, "basis": "morning", "stage_builds": {"stg_x": "m_1"},
                             "equity_builds": {"price_daily": "m_2"},
                             "health": {"stage": "ok", "equity": health}}), encoding="utf-8")


def _root(home: Path, conf: str | None = SHADOW_CONF) -> Path:
    root = home / "quant-ledger"
    for sub in ("scripts", ".venv/bin", "config", "data/calendar", "data/raw", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (home / "tmp").mkdir(exist_ok=True)
    (home / "fakebin").mkdir(exist_ok=True)
    for name in ("postclose_chain.sh", "postclose_conf.sh"):
        shutil.copy(SCRIPTS / name, root / "scripts" / name)
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
    for d in (D_PREV, T):
        _history(root, d)
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
               QL_POSTCLOSE_CHAIN_LOCK_FILE=str(home / "chain.lock"))
    for k in ("QL_BUILD_LOCK_HELD", "QL_HOME", "PYTHONPATH", "QL_V3_POST_CMD"):
        env.pop(k, None)
    env.update(extra)
    return env


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _all_runs(root: Path) -> list[runlog.Run]:
    db = root / "data/raw/daily_run.db"
    return list(reversed(runlog.recent(db, limit=100))) if db.exists() else []


def _runs(root: Path) -> list[tuple[str, str]]:
    return [(r.source, r.status) for r in _all_runs(root)]


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
    """정상 경로 — 수집 → 고정 판 확인 → stage 단독 빌드·건전성 → fi → 모델 → 엑셀 → v3 그림자 반영.

    단계마다 런 로그 source 한 행(ok)이 `daily.runlog.POSTCLOSE_STEPS` 순서로 남고, 준비 info 1건.
    산출 루트는 전부 data/model_db(T-3·T-29), fi 와 v3 반영은 같은 D' 아침 인계 이력으로 고정한다(T-2 · P1).
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
        f"--builds-from {H_PREV} --calendar-dir data/calendar")
    assert r.call("model") == (f"model build --date {T} --basis evening --root data/model_db/model "
                               "--fi-root data/model_db/factor_inputs")
    assert r.call("deliver") == (
        f"deliver model-daily --date {T} --basis evening --model-root data/model_db/model "
        "--fi-root data/model_db/factor_inputs --out-root data/model_db/deliver")
    assert r.call("v3_post.sh") == (
        f"v3_post.sh --date {T} --basis evening --v3-db {tmp_path / 'v3_quant.db'} "
        f"--model-root data/model_db/model --builds-from {H_PREV} --shadow | QL_V3_POST_CMD=unset")
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
    ("v3_post.sh", 6, "postclose_v3", "v3 반영"),
])
def test_step_failure_stops_later_steps_with_one_crit(tmp_path: Path, module: str, rc: int,
                                                      source: str, name: str) -> None:
    """단계가 실패하면 뒤 단계는 돌지 않는다 — 그 단계 런 로그는 failed, 뒤 단계 행은 없다. crit 1건(기록만).
    v3_post rc 6(반영 COMMIT 뒤 daily_post 실패)도 실패로 센다."""
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


@pytest.mark.parametrize("broken", ["missing", "health_fail"])
def test_missing_pinned_board_fails_before_the_build_lock(tmp_path: Path, broken: str) -> None:
    """MINOR-2 — 고정 판(D' 아침 인계 이력, health ok)이 없으면 빌드 락을 잡지 않고 crit(T-7 대체 발송 경로).
    수집(①)은 그 앞이라 이미 돌았다 — 21:05 뒤 점수 없는 7표 반영(T-38)의 원장이 쌓인다."""
    root = _root(tmp_path)
    if broken == "missing":
        (root / H_PREV).unlink()
    else:
        _history(root, D_PREV, health="fail")
    r = _chain(tmp_path, "close", "--date", T)
    assert r.rc == 2, r.out + r.log
    assert r.mods == ["daily.postclose"]
    assert not any(c.startswith("flock") and " 6" in c for c in r.calls)
    assert r.runs == []
    assert r.titles("crit") == ["장 마감 체인 실패: 고정 판 확인"]
    assert H_PREV in r.notify[0]


def _silent_loss(root: Path, d: str, *, unexplained: int, undecidable: int = 0) -> None:
    """D 의 조용한 손실 검사 결과(daily.silent_loss check 가 쓰는 모양 — gate 가 읽는 키만)."""
    from daily import silent_loss

    p = root / silent_loss.OUT_DIR / f"{d}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema": silent_loss.SCHEMA, "tool": silent_loss.TOOL, "date": d, "error": None,
                             "totals": {"unexplained": unexplained, "undecidable": undecidable}}),
                 encoding="utf-8")


@pytest.mark.parametrize(("block", "unexplained", "blocked"), [
    ("1", 3, True),        # 차단형 + 미설명 → 막음
    ("1", 0, False),       # 차단형 + 미설명 0 → 통과
    ("0", 3, False),       # 기록형(저장소 값) → 결과와 무관하게 통과
])
def test_silent_loss_gate_blocks_before_the_build_lock_only_when_switched_on(
        tmp_path: Path, block: str, unexplained: int, blocked: bool) -> None:
    """K1-4a — 조용한 손실 차단 스위치(config/silent_loss.env)가 켜져 있고 D' 아침 확정판 검사에 미설명이 있으면
    고정 판 확인 뒤·빌드 락 전에 crit(그날 장 마감 판 없음 — T-7 대체 발송 경로). 꺼져 있으면 지금처럼 다 돈다."""
    root = _root(tmp_path)
    (root / "config/silent_loss.env").write_text(f"SILENT_LOSS_BLOCK={block}\n", encoding="utf-8")
    _silent_loss(root, D_PREV, unexplained=unexplained)
    r = _chain(tmp_path, "close", "--date", T)
    if blocked:
        assert r.rc == 2, r.out + r.log
        assert r.mods == ["daily.postclose"]
        assert not any(c.startswith("flock") and " 6" in c for c in r.calls)
        assert r.titles("crit") == ["장 마감 체인 실패: 조용한 손실 차단"]
        assert "미설명 3" in r.notify[0]
    else:
        assert r.rc == 0, r.out + r.log
        assert r.mods == STEP_MODULES
        assert r.titles("crit") == []


def test_silent_loss_gate_blocks_a_missing_result_when_switched_on(tmp_path: Path) -> None:
    """차단형인데 D' 검사 결과가 없으면(08:10 끝 검사가 돌지 않음) 막는다 — 못 쟀으면 통과가 아니다(P1)."""
    root = _root(tmp_path)
    (root / "config/silent_loss.env").write_text("SILENT_LOSS_BLOCK=1\n", encoding="utf-8")
    r = _chain(tmp_path, "close", "--date", T)
    assert r.rc == 2, r.out + r.log
    assert r.mods == ["daily.postclose"]
    assert r.titles("crit") == ["장 마감 체인 실패: 조용한 손실 차단"]


@pytest.mark.parametrize("conf", [None, "SILENT_LOSS_BLOCK=0\n", "SILENT_LOSS_BLOCK=yes\n", "SILENT_LOSS_BLOCK=\n"],
                         ids=["no_file", "zero", "odd", "empty"])
@pytest.mark.parametrize("state", ["missing", "broken_json", "rc1", "rc2", "rc3", "gate_crash"])
def test_silent_loss_gate_never_blocks_when_switched_off(tmp_path: Path, conf: str | None, state: str) -> None:
    """K1-4a 꺼짐 보장 — 차단 스위치가 꺼져 있으면(파일 없음·0·이상한 값·빈 값) D' 검사 결과가 어떤 상태든(없음·깨진 JSON·
    미설명 rc 1·판정 불가 rc 2·차단 rc 3) 관문 파이썬이 죽어도(예외·import 실패) 15:41 장 마감 체인은 끝까지 돈다."""
    root = _root(tmp_path)
    if conf is not None:
        (root / "config/silent_loss.env").write_text(conf, encoding="utf-8")
    out = root / "logs/silent_loss" / f"{D_PREV}.json"
    if state == "broken_json":
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("{", encoding="utf-8")
    elif state in ("rc1", "rc2", "rc3"):
        _silent_loss(root, D_PREV, unexplained=3 if state != "rc2" else 0,
                     undecidable=0 if state == "rc1" else 2)
    env = {"SL_GATE_RC": "1"} if state == "gate_crash" else {}
    r = _chain(tmp_path, "close", "--date", T, **env)
    assert r.rc == 0, r.out + r.log
    assert r.mods == STEP_MODULES
    assert r.titles("crit") == []
    assert "차단 스위치 off" in r.log


def test_silent_loss_gate_crash_blocks_only_when_switched_on(tmp_path: Path) -> None:
    """켜져 있으면 관문이 죽어도(예외·import 실패) 통과로 보지 않는다(P1) — 결과가 깨끗해도 막는다."""
    root = _root(tmp_path)
    (root / "config/silent_loss.env").write_text("SILENT_LOSS_BLOCK=1\n", encoding="utf-8")
    _silent_loss(root, D_PREV, unexplained=0)
    r = _chain(tmp_path, "close", "--date", T, SL_GATE_RC="1")
    assert r.rc == 2, r.out + r.log
    assert r.mods == ["daily.postclose"]
    assert r.titles("crit") == ["장 마감 체인 실패: 조용한 손실 차단"]


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
    assert r.titles("crit") == ["장 마감 체인 실패: 장 마감 수집(rc=3)"]
    assert "세션 예외일 아님" in r.notify[0]


def test_holiday_skips_before_collecting(tmp_path: Path) -> None:
    """휴장일은 수집 전에 건너뛴다(info 1건) — 수집기는 거래일이 아니면 rc 0 이라 체인이 먼저 본다."""
    _root(tmp_path)
    r = _chain(tmp_path, "close", "--date", HOLIDAY)
    assert r.rc == 0, r.out + r.log
    assert r.mods == []
    assert r.titles("info") == ["장 마감 체인 휴장 — 건너뜀"]


def test_fg5_warn_reaches_the_runlog_and_notify(tmp_path: Path) -> None:
    """PR-5 리뷰 MINOR-1 — fi 가 rc 0 이어도 판 manifest 게이트에 metrics.warn 이 있으면(FG5 후보 중 T-6 보류,
    T-37) 그 단계 런 로그 detail 에 warn:FG5 를 남기고 warn 한 줄을 낸다. 판정·뒤 단계는 그대로다."""
    root = _root(tmp_path)
    r = _chain(tmp_path, "close", "--date", T, FI_WARN="1")
    assert r.rc == 0, r.out + r.log
    assert r.mods == STEP_MODULES
    fi = [run for run in _all_runs(root) if run.source == "postclose_fi"]
    assert len(fi) == 1 and fi[0].status == "ok" and "warn:FG5" in (fi[0].detail or "")
    assert r.titles("warn") == ["장 마감 체인 게이트 경고"]
    assert "FG5" in r.notify[-1] and "T-6 보류 20/600" in r.notify[-1]
    assert r.titles("info")[-1].startswith("장 마감 판 준비 ")


def test_shadow_switch_never_sends_or_writes_v3(tmp_path: Path) -> None:
    """T-7 — 그림자 기간: 엑셀은 짓되 --send 없음, v3 는 --shadow, daily_post 명령 없음.
    환경에 QL_V3_POST_CMD 가 남아 있어도 v3_post 에 새지 않는다."""
    _root(tmp_path)
    r = _chain(tmp_path, "close", "--date", T, QL_V3_POST_CMD="echo leaked")
    assert r.rc == 0, r.out + r.log
    assert "--send" not in r.call("deliver")
    v3 = r.call("v3_post.sh")
    assert "--shadow" in v3 and "--v3-post-cmd" not in v3 and v3.endswith("QL_V3_POST_CMD=unset")


@pytest.mark.parametrize("conf", [None, "", "POSTCLOSE_ENABLED=yes\n", "POSTCLOSE_ENABLED=\n",
                                  "POSTCLOSE_ENABLED=0\nPOSTCLOSE_SEND=1\nPOSTCLOSE_V3=in-place\n",
                                  "POSTCLOSE_ENABLED=$UNDEFINED_VAR\n"],
                         ids=["no_file", "empty", "near_miss", "blank", "off_with_live", "bad_line"])
@pytest.mark.parametrize("mode", ["close", "refill", "morning"])
def test_disabled_chain_does_nothing(tmp_path: Path, conf: str | None, mode: str) -> None:
    """MINOR-1 — POSTCLOSE_ENABLED=1 이 아니면(파일 없음·빈 값·비슷한 값) 세 모드 모두 info 한 줄·rc 0 이고
    아무 단계도 돌지 않는다. 설정 읽기는 set +u 안이라 정의 안 된 변수 참조가 셸을 조용히 끝내지 않는다."""
    root = _root(tmp_path, conf=conf)
    _seed(root, T, "postclose_model", "ok")
    r = _chain(tmp_path, mode, "--date", T)
    assert r.rc == 0, r.out + r.log
    assert r.calls == [] and r.runs == []
    assert r.titles("info") == [f"장 마감 체인 꺼짐 — {mode} 건너뜀"]
    assert "꺼짐" in r.out


@pytest.mark.parametrize("conf", ["POSTCLOSE_ENABLED=1\nPOSTCLOSE_SEND=yes\nPOSTCLOSE_V3=inplace\n",
                                  "POSTCLOSE_ENABLED=1\n"],
                         ids=["near_miss_values", "only_enabled"])
def test_switch_turns_on_only_with_exact_values(tmp_path: Path, conf: str) -> None:
    """발송·제자리 반영은 정확한 값일 때만 — 그 밖은 그림자(미발송·--shadow)다(P1)."""
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
    assert f"--builds-from {H_PREV} --v3-post-cmd echo post |" in v3


@pytest.mark.parametrize("mode", ["close", "refill", "morning"])
def test_in_place_without_send_is_refused(tmp_path: Path, mode: str) -> None:
    """NIT — 제자리 반영인데 발송이 꺼져 있으면 보내지 않은 점수가 v3 에 들어간다 — rc 5 로 거부(crit),
    아무 단계도 돌지 않는다. PR-9 가 켤 때의 안전장치."""
    _root(tmp_path, conf="POSTCLOSE_ENABLED=1\nPOSTCLOSE_SEND=0\nPOSTCLOSE_V3=in-place\n")
    r = _chain(tmp_path, mode, "--date", T)
    assert r.rc == 5, r.out + r.log
    assert r.calls == [] and r.runs == []
    assert r.titles("crit") == [f"장 마감 체인 설정 오류 — {mode} 거부"]


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


@pytest.mark.parametrize("conf", [SHADOW_CONF, None], ids=["enabled", "disabled"])
def test_dry_run_prints_the_plan_only(tmp_path: Path, conf: str | None) -> None:
    """dry-run 은 계획만 — 락·단계·런 로그·알림 없음. 꺼져 있어도 계획은 보여 준다(꺼짐 표시).
    스냅샷 GC keep 은 상수(snapshot.KEEP_DEFAULT)에서 읽는다."""
    from stage import snapshot

    root = _root(tmp_path, conf=conf)
    r = _chain(tmp_path, "close", "--date", T, "--dry-run")
    assert r.rc == 0, r.out
    assert r.calls == [] and r.notify == [] and r.runs == []
    assert not (root / "data/raw/daily_run.db").exists()
    assert f"D'={D_PREV}" in r.out and "--shadow" in r.out and "--send" not in r.out
    assert f"--builds-from {H_PREV}" in r.out and "health ok" in r.out
    assert f"keep {snapshot.KEEP_DEFAULT} = snapshot.KEEP_DEFAULT" in r.out
    assert ("꺼짐" in r.out) == (conf is None)


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


# ── refill: 21:05 저녁 원장 뒤 7표 반영 ⑦ ───────────────────────────────────────

@pytest.mark.parametrize("seed", [None, "ok", "failed", "session_exception"],
                         ids=["no_postclose_run", "postclose_ok", "postclose_failed", "session_day"])
def test_refill_always_reflects_seven_tables_without_scores(tmp_path: Path, seed: str | None) -> None:
    """T-38(컨트롤러 결정) — refill 은 ⑥ 결과와 상관없이 늘 점수 없는 7표 반영이다(v3_post --no-scores, compat 만,
    daily_post 없음). 점수는 ⑥ 만 쓴다. 장 마감 판이 없던 날(판 실패·세션 예외일)엔 그날 v3 T 행의 유일한 경로다."""
    root = _root(tmp_path)
    if seed == "session_exception":
        _seed(root, T, "kiwoom_postclose", "session_exception")
    elif seed is not None:
        _seed(root, T, "postclose_v3", seed)
    r = _chain(tmp_path, "refill", "--date", T)
    assert r.rc == 0, r.out + r.log
    assert [c for c in r.calls if c.startswith("flock")] == ["flock -n 7"]
    assert r.call("v3_post.sh") == (
        f"v3_post.sh --date {T} --basis evening --v3-db {tmp_path / 'v3_quant.db'} --no-scores "
        f"--builds-from {H_PREV} --shadow | QL_V3_POST_CMD=unset")
    assert r.runs == [("postclose_v3_refill", "ok")]
    assert r.titles("info") == ["장 마감 재반영 완료"]


def test_refill_in_place_passes_no_post_command(tmp_path: Path) -> None:
    """제자리 설정이어도 refill 에는 daily_post 명령을 넘기지 않는다(점수 없는 반영 — compat 만)."""
    _root(tmp_path, conf=LIVE_CONF)
    r = _chain(tmp_path, "refill", "--date", T, QL_V3_POST_CMD="echo leaked")
    assert r.rc == 0, r.out + r.log
    v3 = r.call("v3_post.sh")
    assert "--no-scores" in v3 and "--shadow" not in v3 and "--v3-post-cmd" not in v3
    assert v3.endswith("QL_V3_POST_CMD=unset")


def test_refill_failure_is_crit(tmp_path: Path) -> None:
    """v3_post 가 실패하면 crit."""
    _root(tmp_path)
    r = _chain(tmp_path, "refill", "--date", T, RC_v3_post="2")
    assert r.rc == 2, r.out + r.log
    assert r.runs == [("postclose_v3_refill", "failed")]
    assert r.titles("crit") == ["장 마감 재반영 실패: 21:05 원장 뒤 7표 반영(rc=2)"]


@pytest.mark.parametrize("broken", ["missing", "health_fail"])
def test_refill_checks_its_pinned_board_first(tmp_path: Path, broken: str) -> None:
    """재리뷰 MINOR-A — 고정 판(D' 아침 인계 이력)이 없거나 health 가 ok 가 아니면 v3_post 를 부르지 않고 crit 1건.
    compat 은 health 를 보지 않아 equity 일부만 실패한 날 옛 판이 7표에 조용히 섞인다(P1)."""
    root = _root(tmp_path)
    if broken == "missing":
        (root / H_PREV).unlink()
    else:
        _history(root, D_PREV, health="fail")
    r = _chain(tmp_path, "refill", "--date", T)
    assert r.rc == 2, r.out + r.log
    assert r.mods == [] and r.runs == []
    assert r.titles("crit") == ["장 마감 재반영 실패: 고정 판 확인"]
    assert H_PREV in r.notify[-1]


# ── morning: 다음 날 아침 잇기 ⑧ ───────────────────────────────────────────────

def test_morning_reflects_krx_and_compares_the_boards(tmp_path: Path) -> None:
    """확정판 뒤 v3 아침 KRX 재반영(compat 만, T-34 — 반영 표는 compat 이 고른다, 고정 판 = D 아침 인계 이력)과
    두 판 대조(PR-7). 체인 락은 잡지 않는다."""
    root = _root(tmp_path, conf=LIVE_CONF)
    _seed(root, T, "postclose_model", "ok")
    r = _chain(tmp_path, "morning", "--date", T)
    assert r.rc == 0, r.out + r.log
    assert not any(c.startswith("flock") for c in r.calls)
    assert r.mods == ["v3_post.sh", "daily.board_compare"]
    assert r.call("v3_post.sh") == (
        f"v3_post.sh --date {T} --basis morning --v3-db {tmp_path / 'v3_quant.db'} "
        f"--builds-from {H_T} | QL_V3_POST_CMD=unset")
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


@pytest.mark.parametrize("broken", ["missing", "health_fail"])
def test_morning_checks_its_pinned_board_first(tmp_path: Path, broken: str) -> None:
    """재리뷰 MINOR-A — 아침 재반영 앞에서 고정 판(D 아침 인계 이력)의 날짜·health 를 본다. 쓸 수 없으면
    v3_post 를 부르지 않고 crit 1건. 대조는 v3 반영의 소비자가 아니라 그대로 돈다."""
    root = _root(tmp_path)
    _seed(root, T, "kiwoom_postclose", "ok")
    _seed(root, T, "postclose_model", "ok")
    if broken == "missing":
        (root / H_T).unlink()
    else:
        _history(root, T, health="fail")
    r = _chain(tmp_path, "morning", "--date", T)
    assert r.rc == 2, r.out + r.log
    assert r.mods == ["daily.board_compare"]
    assert r.runs == [("postclose_compare", "ok")]
    assert r.titles("crit") == ["장 마감 판 아침 잇기 실패: 고정 판 확인"]
    assert H_T in r.notify[-1]


@pytest.mark.parametrize(("seed", "level"), [(None, "warn"), ("ok", "info"), ("cutoff", "info"),
                                             ("session_exception", "info")],
                         ids=["no_collect_run", "collect_ok", "collect_cutoff", "session_day"])
def test_morning_warns_when_the_close_chain_never_ran(tmp_path: Path, seed: str | None,
                                                     level: str) -> None:
    """재리뷰 NIT-2 — 켜져 있는데 그날(D) 수집기 런(kiwoom_postclose)이 아예 없으면 15:41 close 크론이 돌지 않은
    것이라 아침 잇기 끝 알림이 info 대신 warn 이다. 세션 예외일엔 수집기 런(session_exception)이 남아 구분된다.
    rc 는 그대로 0."""
    root = _root(tmp_path)
    if seed is not None:
        _seed(root, T, "kiwoom_postclose", seed)
    r = _chain(tmp_path, "morning", "--date", T)
    assert r.rc == 0, r.out + r.log
    assert [n.split("|")[0] for n in r.notify] == [level]
    if level == "warn":
        assert r.titles("warn") == ["장 마감 판 아침 잇기 완료 — 그날 장 마감 체인 런 없음"]
        assert "kiwoom_postclose" in r.notify[0]
    else:
        assert r.titles("info") == ["장 마감 판 아침 잇기 완료"]


def test_morning_compare_mismatch_is_warn(tmp_path: Path) -> None:
    """대조 rc 1 이고 이번 실행의 compare/<D>.json 이 불일치 판정(verdict fail · rc 1)이면 mismatch — warn·rc 1."""
    root = _root(tmp_path)
    _seed(root, T, "postclose_model", "ok")
    r = _chain(tmp_path, "morning", "--date", T, RC_daily_board_compare="1", COMPARE_VERDICT="fail")
    assert r.rc == 1, r.out + r.log
    assert r.runs == [("postclose_v3_morning", "ok"), ("postclose_compare", "mismatch")]
    assert r.titles("warn") == ["장 마감 판 아침 잇기 — 두 판 대조 불일치"]
    assert "mismatch" in runlog.WARN_STATUSES["postclose_compare"]


@pytest.mark.parametrize("report", ["none", "stale", "pass_verdict"])
def test_morning_compare_rc1_without_its_report_is_failure(tmp_path: Path, report: str) -> None:
    """MINOR-1 — rc 1 이어도 이번 실행이 쓴 불일치 보고서가 없으면(모듈 없음·import 예외·옛 보고서) 실패 — crit."""
    root = _root(tmp_path)
    _seed(root, T, "postclose_model", "ok")
    env = {"RC_daily_board_compare": "1"}
    if report == "stale":
        old = root / "data/model_db/compare" / f"{T}.json"
        old.parent.mkdir(parents=True)
        old.write_text(json.dumps({"date": "2026-10-08", "verdict": "fail", "rc": 1}),
                       encoding="utf-8")
        os.utime(old, (time.time() - 3600, time.time() - 3600))
    elif report == "pass_verdict":
        env["COMPARE_VERDICT"] = "pass"
    r = _chain(tmp_path, "morning", "--date", T, **env)
    assert r.rc == 2, r.out + r.log
    assert r.runs == [("postclose_v3_morning", "ok"), ("postclose_compare", "failed")]
    assert r.titles("crit") == ["장 마감 판 아침 잇기 실패: 두 판 대조(rc=2)"]


def test_morning_v3_failure_does_not_block_the_compare(tmp_path: Path) -> None:
    """두 일은 서로의 소비자가 아니다 — v3 재반영이 실패해도 대조는 돈다. crit 1건·rc 2."""
    root = _root(tmp_path)
    _seed(root, T, "postclose_model", "ok")
    r = _chain(tmp_path, "morning", "--date", T, RC_v3_post="2")
    assert r.rc == 2, r.out + r.log
    assert r.mods == ["v3_post.sh", "daily.board_compare"]
    assert r.runs == [("postclose_v3_morning", "failed"), ("postclose_compare", "ok")]
    assert r.titles("crit") == ["장 마감 판 아침 잇기 실패: v3 아침 KRX 재반영(rc=2)"]


def test_followup_sources_match_the_runlog_registry(tmp_path: Path) -> None:
    """NIT — refill·아침 재반영·대조가 남기는 source 이름이 `daily.runlog.POSTCLOSE_FOLLOWUPS` 와 같다
    (워치독·일일 리포트가 그 목록으로 읽는다)."""
    root = _root(tmp_path)
    _seed(root, T, "postclose_model", "ok")
    a = _chain(tmp_path, "refill", "--date", T)
    b = _chain(tmp_path, "morning", "--date", T)
    assert a.rc == 0 and b.rc == 0, a.out + b.out
    assert {s for s, _ in a.runs + b.runs} == set(runlog.POSTCLOSE_FOLLOWUPS)


# ── 실물 v3_post.sh 와의 인자 계약(QL-F2 · COMPAT_LAYER §8 호출 표) ─────────────────────

def _real_v3(home: Path, conf: str = SHADOW_CONF) -> Path:
    """대역 대신 실물 scripts/v3_post.sh — compat 하위 명령만 대역 python 이 받는다."""
    root = _root(home, conf=conf)
    shutil.copy(SCRIPTS / "v3_post.sh", root / "scripts/v3_post.sh")
    return root


def _compat(r: Run, sub: str) -> list[str]:
    return [c for c in r.calls if c.startswith(f"compat {sub} ")]


@pytest.mark.parametrize("conf", [SHADOW_CONF, LIVE_CONF], ids=["shadow", "live"])
def test_real_v3_post_accepts_all_three_call_shapes(tmp_path: Path, conf: str) -> None:
    """체인의 세 호출(⑥ · refill · 아침)이 실물 v3_post.sh 인자 계약을 통과한다(rc 5 인자 오류 없음).
    refill 은 `--no-scores` 가 v3-tables·apply 까지 가고 스테이징 기본 경로가 `_noscores` 를 붙인다. 셋 다
    `--builds-from` 이 compat export 에 닿는다. 제자리(live)는 ⑥ 에만 daily_post 를 부른다."""
    root = _real_v3(tmp_path, conf)
    _seed(root, T, "postclose_model", "ok")
    env = {"QL_V3_LOCK_FILE": str(tmp_path / "v3.lock"), "QL_V3_POST_TODAY": T}
    shadow = "_shadow" if conf == SHADOW_CONF else ""
    close = _chain(tmp_path, "close", "--date", T, **env)
    assert close.rc == 0, close.out + close.log
    assert close.runs[-1] == ("postclose_v3", "ok")
    (stage,) = _compat(close, "stage")
    assert stage.endswith(f"--out data/_v3_post/staging_evening{shadow}.db")
    (export,) = _compat(close, "export")
    assert f"--builds-from {H_PREV}" in export and "--model-root data/model_db/model" in export
    assert "score_history" in export
    (root / "calls.txt").unlink()
    refill = _chain(tmp_path, "refill", "--date", T, **env)
    assert refill.rc == 0, refill.out + refill.log
    assert refill.runs == [("postclose_v3_refill", "ok")]
    (stage,) = _compat(refill, "stage")
    assert stage.endswith(f"--out data/_v3_post/staging_evening_noscores{shadow}.db")
    (tables,) = _compat(refill, "v3-tables")
    assert tables.endswith("--no-scores")
    (export,) = _compat(refill, "export")
    assert f"--builds-from {H_PREV}" in export and "score_history" not in export
    (apply,) = _compat(refill, "apply")
    assert apply.endswith("--no-scores")
    (root / "calls.txt").unlink()
    morning = _chain(tmp_path, "morning", "--date", T, **env)
    assert morning.rc == 0, morning.out + morning.log
    assert morning.runs[0] == ("postclose_v3_morning", "ok")
    (export,) = _compat(morning, "export")
    assert f"--builds-from {H_T}" in export and "--basis morning" in export
    assert "--no-scores" not in " ".join(_compat(morning, "v3-tables"))
    post = [n for n in morning.notify if "daily_post" in n]     # notify.txt 는 세 실행에 걸쳐 쌓인다
    if conf == LIVE_CONF:      # 제자리 — ⑥ 만 daily_post('echo post')를 불렀다
        assert any("v3_post 20261008 evening 반영 완료|daily_post 완료" in n for n in post)
        assert sum("daily_post 완료" in n for n in post) == 1
    else:
        assert all("daily_post 완료" not in n for n in post)


@pytest.mark.parametrize("mode", ["refill", "morning"])
def test_real_v3_post_is_not_called_without_its_pinned_board(tmp_path: Path, mode: str) -> None:
    """MAJOR-1 · 재리뷰 MINOR-A — 고정 판(인계 이력)이 health ok 가 아니면 체인이 실물 v3_post 를 아예 부르지 않는다
    (compat 하위 명령 0회 — 스테이징도 뜨지 않는다). crit 은 체인의 '고정 판 확인' 1건."""
    root = _real_v3(tmp_path)
    _history(root, D_PREV if mode == "refill" else T, health="fail")
    r = _chain(tmp_path, mode, "--date", T, QL_V3_LOCK_FILE=str(tmp_path / "v3.lock"))
    assert r.rc == 2, r.out + r.log
    assert not [c for c in r.calls if c.startswith("compat ")]
    label = "장 마감 재반영" if mode == "refill" else "장 마감 판 아침 잇기"
    assert r.titles("crit") == [f"{label} 실패: 고정 판 확인"]


def test_shipped_config_is_off_and_shadow(tmp_path: Path) -> None:
    """저장소에 실린 설정은 꺼짐이다 — 머지·배포만으로는 아무것도 돌지 않는다. 켜도(ENABLED=1 만 더함)
    발송·제자리 반영은 그림자 값이다(PR-9 몫)."""
    shipped = (DB_ROOT / "config/postclose_chain.env").read_text(encoding="utf-8")
    _root(tmp_path, conf=shipped)
    off = _chain(tmp_path, "close", "--date", T)
    assert off.rc == 0 and off.calls == [], off.out
    _root(tmp_path, conf=shipped + "POSTCLOSE_ENABLED=1\n")
    on = _chain(tmp_path, "close", "--date", T)
    assert on.rc == 0, on.out + on.log
    assert "--send" not in on.call("deliver")
    v3 = on.call("v3_post.sh")
    assert "--shadow" in v3 and "--v3-post-cmd" not in v3


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
    """진짜 flock — close 가 돌고 있으면(체인 락) refill 은 끝날 때까지 기다렸다가 그 뒤에 반영한다(P9 — 같은 v3
    반영을 겹치지 않는다)."""
    root = _root(tmp_path)
    (tmp_path / "fakebin/flock").unlink()
    held = tmp_path / "held"
    released = tmp_path / "released"
    holder = subprocess.Popen(["flock", str(tmp_path / "chain.lock"), "-c",
                               f"touch '{held}'; sleep 2; touch '{released}'"])
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
    assert released.exists()
    assert r.mods == ["v3_post.sh"]
    assert "장 마감 재반영 체인 락 대기" in r.titles("info")


# ── 훅: daily_evening.sh(⑦) · daily_build.sh(⑧) ──────────────────────────────

# 대역 훅 — 받은 인자, 원장 락 fd 9 가 열려 있는지, QL_RAW_LOCK_HELD 를 적고 HOOK_RC 로 끝낸다
_HOOK = ('#!/usr/bin/env bash\n'
         'fd9=closed; { true >&9; } 2>/dev/null && fd9=open\n'
         'echo "postclose_chain.sh $* fd9=$fd9 held=${QL_RAW_LOCK_HELD:-unset}"'
         ' >> "$HOME/quant-ledger/hook.txt"\n'
         'echo HOOK_MARK\nexit "${HOOK_RC:-0}"\n')
_EVENING_PY = """#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  case "$2" in
    *is_trading_day*) exit 0 ;;
    *runlog.recent*) exit 1 ;;
    *runlog.start*) echo 7; exit 0 ;;
    *runlog.finish*) echo RUNLOG_FINISH; exit 0 ;;
  esac
  exec "$REAL_PY" "$@"
fi
if [ "$1" = "-m" ]; then
  [ "$2" = daily.kw_daily ] && exit "${KW_RC:-0}"
  exit 0
fi
exit 0
"""


class Hooked(NamedTuple):
    rc: int
    hook: list[str]
    log: str
    notify: list[str]


def _evening(home: Path, *, kw_rc: int = 0, dry: bool = False, inherited: bool = True,
             hook_rc: int = 0) -> Hooked:
    root = home / "quant-ledger"
    for sub in ("scripts", "logs", ".venv/bin", "data/raw"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (home / "tmp").mkdir(exist_ok=True)
    (home / "fakebin").mkdir(exist_ok=True)
    for name in ("daily_evening.sh", "raw_lock.sh", "postclose_conf.sh"):
        shutil.copy(SCRIPTS / name, root / "scripts" / name)
    stubs = {root / ".venv/bin/python": _EVENING_PY, root / "scripts/postclose_chain.sh": _HOOK,
             root / "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2|$3" >> notify.txt\n',
             root / "scripts/sync_calendar.sh": "#!/usr/bin/env bash\nexit 0\n",
             home / "fakebin/flock": "#!/usr/bin/env bash\nexit 0\n"}
    for path, body in stubs.items():
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    env = dict(os.environ, HOME=str(home), TMPDIR=str(home / "tmp"), QL_KW_EVENING_HHMM="0000",
               REAL_PY=sys.executable, KW_RC=str(kw_rc), HOOK_RC=str(hook_rc),
               QL_RAW_LOCK_FILE=str(home / "raw.lock"),
               PATH=f"{home / 'fakebin'}:{os.environ['PATH']}")
    for k in ("QL_HOME", "PYTHONPATH", "QL_RAW_LOCK_HELD"):
        env.pop(k, None)
    if inherited:
        env["QL_RAW_LOCK_HELD"] = "1"
    p = subprocess.run(["bash", str(root / "scripts/daily_evening.sh"), "--date", T,
                        *(["--dry-run"] if dry else [])],
                       env=env, capture_output=True, text=True, timeout=120, check=False)
    log = "".join(_read(f) for f in sorted((root / "logs").glob("daily_evening_*.log")))
    return Hooked(p.returncode, _read(root / "hook.txt").splitlines(), log,
                  _read(root / "notify.txt").splitlines())


def test_daily_evening_calls_refill_at_the_end_of_the_chain(tmp_path: Path) -> None:
    """⑦ — 21:05 키움 원장 커밋(rc 0)이 끝난 저녁 체인의 끝(런 로그 종료·최종 인계 파일 뒤)에 재반영을 잇는다
    (고정 시각 크론이 아니다). 물려받은 원장 락은 놓지 않는다."""
    h = _evening(tmp_path)
    assert h.rc == 0, h.log
    assert h.hook == [f"postclose_chain.sh refill --date {T} fd9=closed held=1"]
    writes = [i for i, ln in enumerate(h.log.splitlines()) if "deliver/ledger_evening.json 기록" in ln]
    lines = h.log.splitlines()
    mark = lines.index("HOOK_MARK")
    assert len(writes) == 2 and writes[-1] < mark
    assert lines.index("RUNLOG_FINISH") < mark
    assert "장 마감 재반영 rc=0" in h.log
    assert not any(n.startswith("warn|") and "재반영" in n for n in h.notify)   # rc 0 은 요약에만


def test_daily_evening_releases_its_own_raw_lock_before_refill(tmp_path: Path) -> None:
    """MINOR-3 — 저녁 체인이 스스로 잡은 원장 락은 재반영 전에 놓는다(재반영은 원장을 읽기만 한다). 자식에는
    원장 락 fd 도 QL_RAW_LOCK_HELD 도 넘어가지 않는다."""
    h = _evening(tmp_path, inherited=False)
    assert h.rc == 0, h.log
    assert h.hook == [f"postclose_chain.sh refill --date {T} fd9=closed held=unset"]
    assert "원장 락 반납" in h.log


@pytest.mark.parametrize("hook_rc", [2, 4])
def test_daily_evening_warns_when_refill_ends_badly(tmp_path: Path, hook_rc: int) -> None:
    """MINOR-4 — 재반영이 0·1 이 아닌 rc 로 끝나면 저녁 체인이 warn 1건을 낸다(재반영이 자기 crit 을 못 남긴 경우도
    사람에게 닿게). 저녁 체인 rc 는 그대로다."""
    h = _evening(tmp_path, hook_rc=hook_rc)
    assert h.rc == 0, h.log
    assert [n.split("|")[1] for n in h.notify if n.startswith("warn|")
            and "재반영" in n] == [f"daily_evening 장 마감 재반영 rc={hook_rc}"]
    assert f"장 마감 재반영 rc={hook_rc}" in [n for n in h.notify if n.startswith("info|")][0]


def test_daily_evening_skips_refill_when_the_kiwoom_branch_failed(tmp_path: Path) -> None:
    h = _evening(tmp_path, kw_rc=1)
    assert h.rc == 2, h.log
    assert h.hook == []


def test_daily_evening_dry_run_never_calls_refill(tmp_path: Path) -> None:
    h = _evening(tmp_path, dry=True)
    assert h.rc == 0, h.log
    assert h.hook == []


_BUILD_PY = """#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  case "$2" in
    *prev_trading_day*) echo 2026-09-23 ;;
  esac
  exit 0
fi
exit 0
"""


def _build(home: Path, *, brc: int = 0, mrc: int = 0, prc: int = 0) -> Hooked:
    root = home / "quant-ledger"
    for sub in ("scripts", "logs", ".venv/bin", "data/raw"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (home / "tmp").mkdir(exist_ok=True)
    for name in ("daily_build.sh", "raw_lock.sh"):
        shutil.copy(SCRIPTS / name, root / "scripts" / name)
    order = 'echo "$(basename "$0") $*" >> "$HOME/quant-ledger/hook.txt"\n'
    stubs = {".venv/bin/python": _BUILD_PY,
             "scripts/postclose_chain.sh": "#!/usr/bin/env bash\n" + order + f"exit {prc}\n",
             "scripts/build_morning.sh": "#!/usr/bin/env bash\n" + order + f"exit {brc}\n",
             "scripts/model_daily.sh": "#!/usr/bin/env bash\n" + order + f"exit {mrc}\n",
             "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2|$3" >> notify.txt\n'}
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
    return Hooked(p.returncode, _read(root / "hook.txt").splitlines(), log,
                  _read(root / "notify.txt").splitlines())


NO_SQLITE3 = pytest.mark.skipif(shutil.which("sqlite3") is None,
                                reason="sqlite3 CLI 없음 — daily_build 의 KRX 단계가 쓴다")


@NO_SQLITE3
@pytest.mark.parametrize(("brc", "mrc"), [(0, 0), (1, 0), (0, 2)])
def test_daily_build_chains_the_morning_follow_up(tmp_path: Path, brc: int, mrc: int) -> None:
    """⑧ — 확정판이 서면(build_morning rc 0·1) 모델 단계 뒤에 아침 잇기를 부른다. 모델 단계가 실패해도
    부른다(T-34 — 가격 재반영이 아침 모델 실패에 묶이지 않는다)."""
    h = _build(tmp_path, brc=brc, mrc=mrc)
    assert h.rc in (0, 1), h.log
    assert h.hook == [f"build_morning.sh --date {T}", f"model_daily.sh --date {T}",
                      f"postclose_chain.sh morning --date {T}"]


@NO_SQLITE3
def test_daily_build_skips_the_morning_follow_up_without_a_board(tmp_path: Path) -> None:
    """확정 빌드가 실패하면(rc ≥ 2) 아침 잇기를 부르지 않는다 — 어제 판 위에 재반영·대조하지 않는다."""
    h = _build(tmp_path, brc=2)
    assert h.rc == 2, h.log
    assert h.hook == [f"build_morning.sh --date {T}"]


@NO_SQLITE3
@pytest.mark.parametrize("prc", [0, 1, 2, 5])
def test_daily_build_reports_the_morning_follow_up_rc(tmp_path: Path, prc: int) -> None:
    """MINOR-4 — 아침 잇기 rc 를 요약에 싣고, 0·1 이 아니면 warn 1건(확정판 rc·등급은 그대로)."""
    h = _build(tmp_path, prc=prc)
    assert h.rc == 0, h.log
    final = [n for n in h.notify if n.startswith("info|daily_build 완료|")]
    assert len(final) == 1 and f"장 마감 판 아침 잇기 종료 rc={prc}" in final[0]
    warns = [n.split("|")[1] for n in h.notify if n.startswith("warn|")]
    assert warns == ([] if prc in (0, 1) else [f"daily_build 장 마감 판 아침 잇기 rc={prc}"])


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
