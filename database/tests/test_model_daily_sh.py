"""scripts/model_daily.sh 와 daily_build.sh 의 모델 단계 — 확정판 뒤 fi → model → 일간 엑셀 발송.

배포 묶음 4 갈래 4-0(N-25 Q0 · P9). HOME 을 임시 폴더로 바꿔 `~/quant-ledger` 에 대역
`.venv/bin/python`·`scripts/notify.sh`·`scripts/build_morning.sh` 를 두고 진짜 스크립트를 돌린다
(test_daily_build_sh 와 같은 방식). 대역 python 은 `-m` 모듈과 인자를 calls.txt 에 적고 모듈별 rc 를
환경변수(FI_RC·MODEL_RC·DELIVER_RC)로 돌려준다 — 실제 factor_inputs·model·deliver·텔레그램은
돌지 않는다. 원장 락은 `QL_RAW_LOCK_HELD=1`(부모가 쥔 것으로 물려받음)로 잡지 않고, 확정 빌드는
대역 build_morning 이다. model_daily 의 빌드 락은 기본으로 `QL_BUILD_LOCK_HELD=1` 로 건너뛰고(맥에
flock 이 없다), daily_build 경로(빌드 락 표식을 비운다)와 대기 테스트는 PATH 대역 flock(호출 인자를
flock.txt 에 적음)을 쓴다. 락 파일은 늘 `QL_BUILD_LOCK_FILE`·`QL_RAW_LOCK_FILE` 임시 경로라 운영 락
(/tmp/quant_ledger_*.lock)을 건드리지 않는다. 실물 flock 테스트는 flock 이 있을 때만(서버·CI
우분투) 돈다.
KRX 단계의 sqlite3 는 PATH 대역('D 수집 완료')이다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import NamedTuple

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
D = "20261006"
MODEL_STEPS = ["factor_inputs", "model", "deliver"]

# 대역 python — `-m` 은 "모듈 인자…" 한 줄을 calls.txt 에 적는다. `-c` 는 D 계산(20261006)과
# '이미 확정판 있음' 가드(GUARD_RC — 0 = 있음 → 건너뜀, 기본 1 = 없음)에 답한다.
_PY = """#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  case "$2" in
    *_morning.json*) exit "${GUARD_RC:-1}" ;;
    *prev_trading_day*) echo 20261006 ;;
  esac
  exit 0
fi
if [ "$1" = "-m" ]; then
  mod="$2"; shift 2
  echo "$mod $*" >> "$QL_HOME/calls.txt"
  case "$mod" in
    factor_inputs) exit "${FI_RC:-0}" ;;
    model) exit "${MODEL_RC:-0}" ;;
    deliver) exit "${DELIVER_RC:-0}" ;;
  esac
fi
exit 0
"""
# 대역 sqlite3 — KRX 단계 질의에 'D 수집 완료'(미완료·fatal 0건, D 기록 1건)로 답한다
_SQLITE = """#!/usr/bin/env bash
case "$2" in
  *group_concat*) echo "" ;;
  *"status='fatal'"*) echo 0 ;;
  *"status IN"*) echo 0 ;;
  *) echo 1 ;;
esac
"""
_BUILD = """#!/usr/bin/env bash
echo "build_morning $*" >> "$HOME/quant-ledger/calls.txt"
exit "${BUILD_RC:-0}"
"""
# 대역 flock — 인자를 적고, 비대기(-n)는 FLOCK_N_RC(1 = 다른 빌드가 쥐고 있음), 대기형은
# FLOCK_WAIT_RC(0 = 풀려서 잡았음)로 끝난다(test_daily_build_sh 의 대역과 같다).
_FLOCK = """#!/usr/bin/env bash
echo "$*" >> "$FLOCK_LOG"
if [ "$1" = "-n" ]; then exit "${FLOCK_N_RC:-0}"; fi
exit "${FLOCK_WAIT_RC:-0}"
"""


class Run(NamedTuple):
    rc: int
    out: str              # stdout + stderr
    calls: list[str]      # 대역이 받은 호출 — "모듈 인자…" 줄
    notify: list[str]     # 대역 notify — 줄마다 "등급|제목|본문"
    flock: list[str]      # 대역 flock 호출 인자(빌드 락)

    @property
    def mods(self) -> list[str]:
        return [c.split()[0] for c in self.calls]


def _root(home: Path, *, stub_flock: bool = True) -> Path:
    root = home / "quant-ledger"
    for sub in ("scripts", "logs", ".venv/bin"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (home / "tmp").mkdir(exist_ok=True)
    (home / "fakebin").mkdir(exist_ok=True)
    for name in ("daily_build.sh", "model_daily.sh", "raw_lock.sh"):
        shutil.copy(SCRIPTS / name, root / "scripts" / name)
    stubs = {root / ".venv/bin/python": _PY,
             root / "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2|$3" >> notify.txt\n',
             root / "scripts/build_morning.sh": _BUILD,
             # 장 마감 판 아침 잇기 훅(PR-8 ⑧) — 이 파일은 모델 단계만 보므로 rc 0 대역(훅은 test_postclose_chain_sh)
             root / "scripts/postclose_chain.sh": "#!/usr/bin/env bash\nexit 0\n",
             home / "fakebin/sqlite3": _SQLITE}
    if stub_flock:
        stubs[home / "fakebin/flock"] = _FLOCK
    for p, body in stubs.items():
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    return root


def _run(home: Path, script: str, *args: str, held: bool = True, stub_flock: bool = True,
         build_lock: Path | None = None, **rcs: int) -> Run:
    """held=True 면 빌드 락을 물려받은 것으로(QL_BUILD_LOCK_HELD=1) model_daily 가 락을 건너뛴다.
    build_lock 은 빌드 락 파일 경로(기본 home/build.lock)."""
    root = _root(home, stub_flock=stub_flock)
    env = dict(os.environ, HOME=str(home), TMPDIR=str(home / "tmp"),
               PATH=f"{home / 'fakebin'}:{os.environ['PATH']}",
               QL_RAW_LOCK_HELD="1", QL_RAW_LOCK_FILE=str(home / "raw.lock"),
               QL_BUILD_LOCK_FILE=str(build_lock or home / "build.lock"),
               FLOCK_LOG=str(home / "flock.txt"))
    for k in ("QL_BUILD_LOCK_HELD", "QL_SKIP_KW", "QL_FORCE"):
        env.pop(k, None)
    if held:
        env["QL_BUILD_LOCK_HELD"] = "1"
    env.update({k.upper(): str(v) for k, v in rcs.items()})
    p = subprocess.run(["bash", str(root / "scripts" / script), *args],
                       env=env, capture_output=True, text=True, timeout=60, check=False)

    def lines(path: Path) -> list[str]:
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    return Run(p.returncode, p.stdout + p.stderr, lines(root / "calls.txt"),
               lines(root / "notify.txt"), lines(home / "flock.txt"))


def _crit(r: Run) -> list[str]:
    return [n for n in r.notify if n.startswith("crit|")]


# ── model_daily.sh ──────────────────────────────────────────────────────────────
def test_model_daily_runs_three_steps_in_order_and_sends_once(tmp_path: Path) -> None:
    """G1 — fi → model → deliver(--send) 가 순서대로 한 번씩, 전부 basis=morning · 같은 D."""
    r = _run(tmp_path, "model_daily.sh", "--date", D)
    assert r.rc == 0, r.out
    assert r.calls == [f"factor_inputs build --date {D} --basis morning",
                       f"model build --date {D} --basis morning",
                       f"deliver model-daily --date {D} --basis morning --send"]
    assert _crit(r) == []
    assert f"모델 단계 완료 D={D}" in r.out


@pytest.mark.parametrize(("failing", "rc"), [("factor_inputs", 2), ("model", 1), ("deliver", 3)])
def test_model_daily_failure_stops_later_steps_and_records_crit(tmp_path: Path, failing: str,
                                                                rc: int) -> None:
    """G1 — 한 단계가 실패하면 뒤 단계는 돌지 않는다(발송 0). 실패 단계 이름·rc 는 notify crit
    1건(notify.sh 는 logs/notify.log 기록만 — 텔레그램 아님), 스크립트 rc 는 그 단계의 rc."""
    r = _run(tmp_path, "model_daily.sh", "--date", D,
             fi_rc=rc if failing == "factor_inputs" else 0,
             model_rc=rc if failing == "model" else 0,
             deliver_rc=rc if failing == "deliver" else 0)
    assert r.rc == rc, r.out
    assert r.mods == MODEL_STEPS[:MODEL_STEPS.index(failing) + 1]
    crit = _crit(r)
    assert len(crit) == 1 and f"모델 단계 실패: {failing}(rc={rc})" in crit[0]
    assert f"D={D}" in crit[0]
    assert f"모델 단계 실패: {failing}(rc={rc})" in r.out
    # deliver 실패는 '발송 0' 이라 단정하지 않는다 — rc 3 은 발송 뒤 장부 기록 실패일 수 있다(B-58)
    note = ("발송 여부는 deliver 출력과 장부" if failing == "deliver" else "엑셀 발송 0")
    assert note in crit[0] and note in r.out


def test_model_daily_resend_is_passed_to_deliver(tmp_path: Path) -> None:
    """손으로 정정 발송할 때 — `--resend` 를 deliver 에 그대로 넘긴다(Q9, 캡션 '정정 n')."""
    r = _run(tmp_path, "model_daily.sh", "--date", D, "--resend")
    assert r.rc == 0, r.out
    assert r.calls[-1] == f"deliver model-daily --date {D} --basis morning --send --resend"


@pytest.mark.parametrize("args", [(), ("--date",), ("--date", "2026-10-06")])
def test_model_daily_needs_a_yyyymmdd_date(tmp_path: Path, args: tuple[str, ...]) -> None:
    """D 가 없거나(`--date` 값 없음 포함 — 옛 코드는 set -u 로 rc 1) 형식이 틀리면 아무 단계도
    돌지 않고 rc 2(인자 오류)와 같은 안내문."""
    r = _run(tmp_path, "model_daily.sh", *args)
    assert r.rc == 2, r.out
    assert "--date YYYYMMDD 가 필요하다" in r.out
    assert r.calls == [] and r.notify == []


# ── model_daily.sh 빌드 락 ─────────────────────────────────────────────────────
def _waits(r: Run) -> list[str]:
    return [n for n in r.notify if n.startswith("info|model_daily 빌드 락 대기|")]


def test_model_daily_waits_for_held_build_lock_then_runs(tmp_path: Path) -> None:
    """다른 빌드가 빌드 락을 쥐고 있으면 비대기로 확인한 뒤 한도 없는 대기형으로 잡고(P9), 대기 줄과
    notify info 1건(기록만)을 남긴 뒤 세 단계를 돈다. 옛 스크립트는 락 없이 바로 돌았다 — 체인과 손
    실행이 같은 D 를 동시에 보내거나, 손 equity 재빌드 도중 fi 가 섞인 판을 읽을 수 있었다."""
    r = _run(tmp_path, "model_daily.sh", "--date", D, held=False, flock_n_rc=1)
    assert r.rc == 0, r.out
    assert r.flock == ["-n 9", "9"]
    assert "빌드 락 대기 시작" in r.out and "빌드 락 대기 끝" in r.out
    waits = _waits(r)
    assert len(waits) == 1 and f"D={D}" in waits[0]
    assert r.mods == MODEL_STEPS and _crit(r) == []
    assert (tmp_path / "build.lock").exists()           # QL_BUILD_LOCK_FILE 경로를 썼다


def test_model_daily_failed_lock_wait_runs_nothing(tmp_path: Path) -> None:
    """대기형 flock 자체가 실패하면 락 없이 돌지 않는다 — 아무 단계도 없이 rc 3 + crit 1건."""
    r = _run(tmp_path, "model_daily.sh", "--date", D, held=False, flock_n_rc=1, flock_wait_rc=1)
    assert r.rc == 3, r.out
    assert r.calls == [] and len(_waits(r)) == 1
    crit = _crit(r)
    assert len(crit) == 1 and "모델 단계 실패: 빌드 락 대기(rc=3)" in crit[0]


def test_model_daily_unopenable_lock_file_runs_nothing(tmp_path: Path) -> None:
    """빌드 락 파일을 열 수 없으면(`exec 9>` 실패) 멈춘다 — bash 는 이 실패에 멈추지 않아, 그대로
    가면 물려받은 fd 9(daily_build 의 원장 락)가 빌드 락 행세를 했다. rc 3, 단계 0, crit 1건."""
    r = _run(tmp_path, "model_daily.sh", "--date", D, held=False,
             build_lock=tmp_path / "no_such_dir" / "build.lock")
    assert r.rc == 3, r.out
    assert r.calls == [] and r.flock == []
    crit = _crit(r)
    assert len(crit) == 1 and "모델 단계 실패: 빌드 락 열기(rc=3)" in crit[0]


@pytest.mark.skipif(shutil.which("flock") is None,
                    reason="flock(util-linux) 없음(맥) — 서버·CI 우분투에서 돈다")
def test_model_daily_real_build_lock_released_then_runs(tmp_path: Path) -> None:
    """진짜 flock — 다른 프로세스가 빌드 락을 쥔 동안 model_daily 가 대기 줄·info 1건을 남기고,
    holder 가 그 info 를 본 뒤 풀면 그 뒤에 돈다(고정 sleep 이 아니라 대기 기록을 보고 푼다 —
    부하 큰 CI 에서 대기 전에 풀려 버리는 간헐 실패 방지, 상한 30초)."""
    root = _root(tmp_path, stub_flock=False)
    held = tmp_path / "held"
    notify = root / "notify.txt"
    # holder 는 락을 잡은 뒤 표식을 남기고, notify 에 '빌드 락 대기' 가 생기면(최대 30초)
    # calls.txt 에 released 를 적고 푼다. flock -c 는 /bin/sh(우분투 dash)로 돌므로 POSIX
    # 문법만 쓴다.
    wait_then_release = (f"touch '{held}'; i=0; while [ $i -lt 300 ]; do "
                         f"grep -q '빌드 락 대기' '{notify}' 2>/dev/null && break; "
                         f"sleep 0.1; i=$((i+1)); done; echo released >> '{root / 'calls.txt'}'")
    holder = subprocess.Popen(["flock", str(tmp_path / "build.lock"), "-c", wait_then_release])
    try:
        deadline = time.monotonic() + 10
        while not held.exists():
            assert time.monotonic() < deadline, "holder 가 빌드 락을 잡지 못했다"
            time.sleep(0.05)
        r = _run(tmp_path, "model_daily.sh", "--date", D, held=False, stub_flock=False)
    finally:
        holder.wait(timeout=30)
    assert holder.returncode == 0
    assert r.rc == 0, r.out
    assert r.mods == ["released", *MODEL_STEPS]          # 세 단계는 holder 가 락을 푼 뒤에 돌았다
    assert "빌드 락 대기 시작" in r.out and "빌드 락 대기 끝" in r.out
    waits = _waits(r)
    assert len(waits) == 1 and f"D={D}" in waits[0]


# ── daily_build.sh → 모델 단계 ──────────────────────────────────────────────────
# 원장 단계의 -m 호출(KRX 는 대역 python 이 파일로 실행해 calls 에 남지 않는다)
LEDGER_STEPS = ["daily.kw_daily", "daily.kw_daily", "daily.ledger_health"]


def test_daily_build_runs_model_step_after_the_morning_build(tmp_path: Path) -> None:
    """G1 — 확정 빌드 rc 0 → 이어서 fi·model·deliver 가 순서대로 한 번씩(발송 1회), 같은 D.
    체인 rc·요약 등급은 종전 그대로(info 'daily_build 완료'), 요약 본문에 모델 단계 결과가
    보인다."""
    r = _run(tmp_path, "daily_build.sh", "--date", D)
    assert r.rc == 0, r.out
    assert r.mods == [*LEDGER_STEPS, "build_morning", *MODEL_STEPS]
    assert r.calls[-1] == f"deliver model-daily --date {D} --basis morning --send"
    assert _crit(r) == []
    done = [n for n in r.notify if n.startswith("info|daily_build 완료|")]
    assert len(done) == 1 and f"모델 단계 완료 D={D}" in done[0]
    # 모델 결과 줄이 요약 맨 앞이다 — 확정판 줄이 길어도 cut -c1-900 에 잘리지 않게
    assert done[0].split("|", 2)[2].startswith(f"모델 단계 완료 D={D}")


def test_daily_build_soft_build_failure_still_runs_model_step(tmp_path: Path) -> None:
    """확정 빌드 rc 1(판은 쓸 수 있고 스냅샷 GC 등 후처리만 실패 — V2-7)이면 모델 단계도 돈다.
    체인 rc 1·warn 은 종전 그대로."""
    r = _run(tmp_path, "daily_build.sh", "--date", D, build_rc=1)
    assert r.rc == 1, r.out
    assert r.mods[-3:] == MODEL_STEPS
    assert [n.split("|")[0] for n in r.notify] == ["warn"]


def test_daily_build_failed_morning_build_sends_nothing(tmp_path: Path) -> None:
    """G1 — 확정 빌드가 실패(rc ≥ 2)하면 모델 단계는 돌지 않는다(발송 0회)."""
    r = _run(tmp_path, "daily_build.sh", "--date", D, build_rc=2)
    assert r.rc == 2, r.out
    assert r.mods == [*LEDGER_STEPS, "build_morning"]
    assert [n.split("|")[:2] for n in r.notify] == [
        ["crit", "daily_build 실패: build_morning(rc=2)"]]
    # 모델 단계 없는 날의 요약은 종전 그대로(확정판 줄로 시작)
    assert r.notify[0].split("|", 2)[2].startswith("──── krx 종료")


def test_daily_build_model_failure_is_soft_and_recorded(tmp_path: Path) -> None:
    """G1 — fi 실패 → model·deliver 0회 + crit 1건(실패 단계 이름·rc). 모델 단계는 soft step 이라
    확정판 rc(0)·요약 등급(info)을 실패로 바꾸지 않고, 요약 본문에 실패 단계가 보인다."""
    r = _run(tmp_path, "daily_build.sh", "--date", D, fi_rc=2)
    assert r.rc == 0, r.out
    assert r.mods == [*LEDGER_STEPS, "build_morning", "factor_inputs"]
    crit = _crit(r)
    assert len(crit) == 1 and "모델 단계 실패: factor_inputs(rc=2)" in crit[0]
    done = [n for n in r.notify if n.startswith("info|daily_build 완료|")]
    assert len(done) == 1 and "모델 단계 실패: factor_inputs(rc=2)" in done[0]


@pytest.mark.parametrize(("args", "guard_rc"), [
    (("--date", D, "--dry-run"), 1),     # dry-run — 원장 단계만, 빌드·모델 없음
    (("--date", D, "--no-build"), 1),    # --no-build — 원장 단계에서 멈춘다
    ((), 0),                             # 크론 경로 · 가드 '이미 확정판 있음'(휴장 포함) — 건너뜀
])
def test_daily_build_skipped_paths_never_run_model_step(tmp_path: Path, args: tuple[str, ...],
                                                        guard_rc: int) -> None:
    """dry-run·--no-build·가드로 건너뛴 날은 확정 빌드가 없으므로 모델 단계도 없다(발송 0회)."""
    r = _run(tmp_path, "daily_build.sh", *args, guard_rc=guard_rc)
    assert r.rc == 0, r.out
    assert not set(MODEL_STEPS) & set(r.mods)
    assert "build_morning" not in r.mods


def test_daily_build_model_step_takes_the_build_lock_itself(tmp_path: Path) -> None:
    """daily_build 는 빌드 락 표식(QL_BUILD_LOCK_HELD)을 비운 채 모델 단계를 부른다 — model_daily 가
    빌드 락을 직접 잡는다(build_chain 이 푼 직후라 비대기 한 번에 잡힌다). 하네스는 표식 1 을 주지만
    daily_build 가 비우므로 대역 flock 이 `-n 9` 한 번 불린다."""
    r = _run(tmp_path, "daily_build.sh", "--date", D)
    assert r.rc == 0, r.out
    assert r.mods[-3:] == MODEL_STEPS
    assert r.flock == ["-n 9"]
    assert (tmp_path / "build.lock").exists()
    assert _waits(r) == []
