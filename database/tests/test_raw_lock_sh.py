"""원장 락 대기 규칙 — 원장을 쓰는 다섯 스크립트가 `scripts/raw_lock.sh` 하나로 같은 규칙을 따른다.

배포 묶음 5-3(TECH_DEBT B-52, 결정 N-27 ⑥). 옛 코드는 08:10 `daily_build.sh` 만 원장 락을
기다렸고(묶음 3, N-23 ①·P9) 06:00 `daily_ledger.sh`·18:05 `daily_evening.sh`·`daily_wise.sh`·
`wics_weekly.sh` 는 `flock -n` 이 실패하면 warn + rc 3 으로 그 회차를 통째로 건너뛰었다.
이제 모두 기다렸다 이어서 돈다.
대신 스크립트마다 대기자는 하나다 — 같은 스크립트가 이미 기다리는 중이면 두 번째 인스턴스는 info 를
남기고 rc 3(대기자 락 `<원장 락>.<이름>.wait`). 알림은 notify 기록만이고 dry-run 이면 남기지 않는다.

HOME 을 임시 폴더로 바꿔 `~/quant-ledger` 에 진짜 스크립트 다섯 개 + `raw_lock.sh` 를 두고, 대역
`.venv/bin/python`(`-c` 는 rc 0 — D 는 20261006, 가드·'이미 완료'는 참 / 그 밖은 calls.txt 에 적고
rc 0)·`scripts/notify.sh`·`sync_calendar.sh`·`dart_company_gap.sh` 로 돌린다. 크론 경로(인자 없음)는
각 스크립트가 '이미 완료·건너뜀' 또는 완료 info 로 끝난다. 락 파일은 `QL_RAW_LOCK_FILE` 임시 경로라
운영 락 `/tmp/quant_ledger_raw.lock` 을 잡지 않는다. 맥에는 flock 이 없어 PATH 대역 flock
(fd 별 rc)으로 호출 순서를 보고, 진짜 flock 이 있으면(서버·CI 우분투) 다른 프로세스가 쥔 락으로
실물 대기·대기자 1 을 본다.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import NamedTuple

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class Spec(NamedTuple):
    script: str
    name: str           # 알림 제목·대기자 락 파일에 쓰는 이름
    dry: bool           # --dry-run 이 있는가
    done: str           # 크론 경로가 끝까지 돌았다는 마지막 알림(제목 앞부분)


LEDGER = Spec("daily_ledger.sh", "daily_ledger", True, "info|daily_ledger 건너뜀(D=20261006")
EVENING = Spec("daily_evening.sh", "daily_evening", True, "info|daily_evening 이미 완료 — 건너뜀")
MASTER = Spec("daily_wise.sh", "daily_master", False, "info|daily_master 완료")
WICS = Spec("wics_weekly.sh", "wics_weekly", True, "info|WICS 주간 스냅샷 dt=20261006 완료")
BUILD = Spec("daily_build.sh", "daily_build", True,
             "info|daily_build 건너뜀(D=20261006 확정판 완료)")
NEWLY_WAITING = [LEDGER, EVENING, MASTER, WICS]         # 배포 묶음 5-3 에서 대기로 바뀐 넷
ALL = [*NEWLY_WAITING, BUILD]                           # 대기자 1 은 다섯 모두
_ID = {"ids": lambda s: s.name}

_PY = """#!/usr/bin/env bash
if [ "$1" = "-c" ]; then
  case "$2" in *prev_trading_day*) echo 20261006 ;; esac
  exit 0
fi
if [ "$1" = "-m" ]; then echo "$2"; else echo "$1"; fi >> "$QL_HOME/calls.txt"
exit 0
"""
# 대역 flock — 인자를 적고 fd 별로 답한다: 원장 락 비대기(-n 9)는 FLOCK_N_RC(1 = 다른 작업이 쥠),
# 대기자 락(-n 8)은 FLOCK_WAITER_RC(1 = 같은 스크립트가 이미 기다림), 대기형(9)은 FLOCK_WAIT_RC.
_FLOCK = """#!/usr/bin/env bash
echo "$*" >> "$FLOCK_LOG"
case "$*" in
  "-n 9") exit "${FLOCK_N_RC:-0}" ;;
  "-n 8") exit "${FLOCK_WAITER_RC:-0}" ;;
  "9") exit "${FLOCK_WAIT_RC:-0}" ;;
  "-u 8"|"-u 9") exit 0 ;;
esac
echo "unexpected flock $*" >&2
exit 99
"""
# 쥐여 있음 → 대기자 자리 확보 → 대기형으로 잡음 → 대기자 자리 반납
WAITED = ["-n 9", "-n 8", "9", "-u 8"]


class Run(NamedTuple):
    rc: int
    out: str             # stdout + stderr — 대기 시작·끝이 실시간으로 나온다(크론에선 cron 로그)
    flock: list[str]     # 대역 flock 호출 인자
    calls: list[str]     # 대역 python·sync_calendar 가 받은 호출 — 비었으면 체인이 아무것도 안 했다
    notify: list[str]    # 대역 notify — 줄마다 "등급|제목|본문"

    def titled(self, prefix: str) -> list[str]:
        return [n for n in self.notify if n.startswith(prefix)]


def _root(home: Path) -> Path:
    root = home / "quant-ledger"
    for sub in ("scripts", "logs", ".venv/bin", "data/raw"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (home / "tmp").mkdir(exist_ok=True)
    for s in ALL:
        shutil.copy(SCRIPTS / s.script, root / "scripts" / s.script)
    shutil.copy(SCRIPTS / "raw_lock.sh", root / "scripts" / "raw_lock.sh")
    shutil.copy(SCRIPTS / "postclose_conf.sh", root / "scripts" / "postclose_conf.sh")   # 06:00·18:05 휴장 스위치 판정
    stubs = {".venv/bin/python": _PY,
             "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2|$3" >> notify.txt\n',
             "scripts/sync_calendar.sh": "#!/usr/bin/env bash\necho sync_calendar >> calls.txt\n",
             "scripts/dart_company_gap.sh": "#!/usr/bin/env bash\necho gap >> calls.txt\n"}
    for rel, body in stubs.items():
        p = root / rel
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    return root


def _kst_today() -> str:
    """오늘 KST 날짜 YYYY-MM-DD — `raw_lock.sh` 가 대기 시작 직전에 적는 날짜와 같은 축."""
    return (dt.datetime.now(dt.UTC) + dt.timedelta(hours=9)).strftime("%Y-%m-%d")


def _env(home: Path, *, flock_stub: bool, held: bool = False, wake: str | None = None,
         **flock_rc: int) -> dict[str, str]:
    env = dict(os.environ, HOME=str(home), TMPDIR=str(home / "tmp"),
               QL_RAW_LOCK_FILE=str(home / "raw.lock"), QL_WEEKDAY="3", QL_KW_EVENING_HHMM="0000")
    for k in ("QL_RAW_LOCK_HELD", "QL_LEDGER_ROOT", "QL_HOME", "PYTHONPATH", "QL_SKIP_KW",
              "QL_FORCE", "QL_EVENING_NOT_BEFORE", "QL_RAW_LOCK_WAKE_DATE"):
        env.pop(k, None)
    if held:
        env["QL_RAW_LOCK_HELD"] = "1"
    if wake is not None:
        env["QL_RAW_LOCK_WAKE_DATE"] = wake
    if flock_stub:
        fake = home / "fakebin"
        fake.mkdir(exist_ok=True)
        (fake / "flock").write_text(_FLOCK, encoding="utf-8")
        (fake / "flock").chmod(0o755)
        env.update(PATH=f"{fake}:{env['PATH']}", FLOCK_LOG=str(home / "flock.txt"),
                   **{f"FLOCK_{k.upper()}_RC": str(v) for k, v in flock_rc.items()})
    return env


def _read(home: Path) -> tuple[list[str], list[str], list[str]]:
    root = home / "quant-ledger"

    def lines(path: Path) -> list[str]:
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    return lines(home / "flock.txt"), lines(root / "calls.txt"), lines(root / "notify.txt")


def _run(home: Path, spec: Spec, *args: str, flock_stub: bool = True, held: bool = False,
         wake: str | None = None, **flock_rc: int) -> Run:
    root = _root(home)
    p = subprocess.run(["bash", str(root / "scripts" / spec.script), *args],
                       env=_env(home, flock_stub=flock_stub, held=held, wake=wake, **flock_rc),
                       capture_output=True, text=True, timeout=60, check=False)
    return Run(p.returncode, p.stdout + p.stderr, *_read(home))


# ── 대역 flock ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("spec", NEWLY_WAITING, **_ID)
def test_waits_for_held_raw_lock_then_continues(tmp_path: Path, spec: Spec) -> None:
    """G1 — 원장 락이 잡혀 있으면 건너뛰지 않고 기다렸다 이어서 돈다(daily_build 와 같은 모양, P9).

    옛 코드는 warn '<이름> 락 실패' + rc 3 으로 그 회차(18:05 저녁 슬롯 전체 · WICS 주간 등)를
    잃었다.
    대기 시작은 실시간 출력과 notify info 1건, 대기 끝은 실시간 출력에 남는다.
    """
    r = _run(tmp_path, spec, n=1)
    assert r.rc == 0, r.out
    assert r.flock == WAITED
    assert f"{spec.name} 원장 락 대기 시작" in r.out and f"{spec.name} 원장 락 대기 끝" in r.out
    assert len(r.titled(f"info|{spec.name} 원장 락 대기|")) == 1, r.notify
    assert not r.titled("warn|"), r.notify
    assert r.titled(spec.done), r.notify            # 대기 뒤 크론 경로를 끝까지 돌았다
    assert (tmp_path / f"raw.lock.{spec.name}.wait").exists()   # 대기자 락은 원장 락 옆, 스크립트별


@pytest.mark.parametrize("spec", ALL, **_ID)
def test_second_waiter_of_same_script_skips(tmp_path: Path, spec: Spec) -> None:
    """대기자 1 — 같은 스크립트가 이미 기다리는 중이면(대기자 락이 쥐여 있음) info 후 rc 3.

    락 보유자가 하루 넘게 멈추면 08:10 대기 인스턴스가 날마다 쌓여, 풀리는 순간 같은 D 를
    N번 돌았다(B-52 ③).
    """
    r = _run(tmp_path, spec, n=1, waiter=1)
    assert r.rc == 3, r.out
    assert r.flock == ["-n 9", "-n 8"]
    assert [n.split("|")[:2] for n in r.notify] == [
        ["info", f"{spec.name} 이미 대기 중인 실행 있음 — 이번 실행 건너뜀"]]
    assert r.calls == []
    assert f"{spec.name} 이미 대기 중인 실행 있음" in r.out


@pytest.mark.parametrize("case", ["wait", "second"])
@pytest.mark.parametrize("spec", [s for s in ALL if s.dry], **_ID)
def test_dry_run_waits_and_skips_without_notify(tmp_path: Path, spec: Spec, case: str) -> None:
    """dry-run 은 다른 알림처럼 대기·대기자 알림도 남기지 않는다 — 출력과 rc 는 운영 경로와 같다."""
    if case == "wait":
        r = _run(tmp_path, spec, "--dry-run", n=1)
        assert r.rc == 0, r.out
        assert r.flock == WAITED
        assert f"{spec.name} 원장 락 대기 시작" in r.out
    else:
        r = _run(tmp_path, spec, "--dry-run", n=1, waiter=1)
        assert r.rc == 3, r.out
        assert r.calls == []
    assert r.notify == []


@pytest.mark.parametrize("spec", NEWLY_WAITING, **_ID)
def test_failed_wait_never_runs_without_lock(tmp_path: Path, spec: Spec) -> None:
    """대기형 flock 자체가 실패하면 락 없이 돌지 않는다 — 대기 info 뒤 warn '락 실패', rc 3."""
    r = _run(tmp_path, spec, n=1, wait=1)
    assert r.rc == 3, r.out
    assert r.flock == ["-n 9", "-n 8", "9"]
    assert r.calls == []
    assert [n.split("|")[:2] for n in r.notify] == [
        ["info", f"{spec.name} 원장 락 대기"], ["warn", f"{spec.name} 락 실패"]]


@pytest.mark.parametrize("spec", ALL, **_ID)
def test_date_change_during_wait_stops(tmp_path: Path, spec: Spec) -> None:
    """G1 — 기다리는 동안 KST 날짜가 바뀌면(자정을 넘는 점유) 락을 잡아도 본 작업을 하지 않는다.

    crit 1건 뒤 원장 락을 놓고 rc 3. 옛 코드는 날짜를 다시 보지 않고 대기 뒤에 D(·오늘)를 새
    날짜로 계산해 그대로 돌았다(B-51 — 저녁은 WISE 스냅샷을 장중 갱신 전 값으로 채운다).
    """
    today = _kst_today()                         # 실행 전에 잰다(자정 경계)
    r = _run(tmp_path, spec, n=1, wake="2099-01-01")
    assert r.rc == 3, r.out
    assert r.flock == [*WAITED, "-u 9"]          # 잡은 원장 락을 바로 놓는다
    assert r.calls == []                         # 본 작업(캘린더 동기화·수집·빌드) 미실행
    title = (f"{spec.name} 원장 락 대기 중 날짜가 바뀜(시작 {today} → 지금 2099-01-01)"
             " — 이번 실행 중단")
    assert [n.split("|")[:2] for n in r.notify] == [
        ["info", f"{spec.name} 원장 락 대기"], ["crit", title]]
    assert "다시 돌릴지는 사람 판단" in r.notify[-1]
    assert "날짜가 바뀜" in r.out


@pytest.mark.parametrize("spec", [s for s in ALL if s.dry], **_ID)
def test_date_change_during_wait_stops_quietly_in_dry_run(tmp_path: Path, spec: Spec) -> None:
    """dry-run 도 같은 rc 3·본 작업 미실행이고, 알림만 남기지 않는다."""
    r = _run(tmp_path, spec, "--dry-run", n=1, wake="2099-01-01")
    assert r.rc == 3, r.out
    assert r.calls == [] and r.notify == []


@pytest.mark.parametrize("spec", ALL, **_ID)
def test_same_day_wait_continues(tmp_path: Path, spec: Spec) -> None:
    """회귀 가드 — 같은 날 안에 대기가 끝나면(날짜 주입 = 오늘 KST) 지금처럼 이어서 돈다."""
    r = _run(tmp_path, spec, n=1, wake=_kst_today())
    assert r.rc == 0, r.out
    assert r.flock == WAITED
    assert not r.titled("crit|"), r.notify
    assert r.titled(spec.done), r.notify


@pytest.mark.parametrize("spec", ALL, **_ID)
def test_free_raw_lock_runs_without_waiting(tmp_path: Path, spec: Spec) -> None:
    """회귀 가드 — 락이 비어 있으면 지금처럼 바로 돈다. 대기자 락은 열지도 않는다."""
    r = _run(tmp_path, spec)
    assert r.rc == 0, r.out
    assert r.flock == ["-n 9"]
    assert "원장 락 대기" not in r.out and not r.titled(f"info|{spec.name} 원장 락 대기")
    assert r.titled(spec.done), r.notify
    assert not (tmp_path / f"raw.lock.{spec.name}.wait").exists()


@pytest.mark.parametrize("spec", ALL, **_ID)
def test_inherited_raw_lock_skips_both_locks(tmp_path: Path, spec: Spec) -> None:
    """부모가 원장 락을 물려주면(QL_RAW_LOCK_HELD=1) 원장 락·대기자 락 둘 다 열지 않는다.

    `daily_ledger.sh` 가 부르는 `daily_wise.sh` 가 이 경로다. `wics_weekly.sh` 는 옛 코드가 이
    규약을 몰라 부모 아래에서도 락을 다시 열었다(같은 파일을 새로 열면 부모와 충돌해 rc 3).
    """
    r = _run(tmp_path, spec, held=True, n=1)
    assert r.rc == 0, r.out
    assert r.flock == []
    assert r.titled(spec.done), r.notify


# ── 진짜 flock(서버·CI 우분투) ─────────────────────────────────────────────────────
real_flock = pytest.mark.skipif(shutil.which("flock") is None,
                                reason="flock(util-linux) 없음(맥) — 서버·CI 우분투에서 돈다")


def _hold(home: Path, seconds: float) -> subprocess.Popen[bytes]:
    """다른 프로세스가 원장 락을 `seconds` 초 쥐었다 푼다. 풀기 직전 calls.txt 에 released."""
    held = home / "held"
    calls = home / "quant-ledger" / "calls.txt"
    holder = subprocess.Popen(["flock", str(home / "raw.lock"), "-c",
                               f"touch '{held}'; sleep {seconds}; echo released >> '{calls}'"])
    deadline = time.monotonic() + 10
    while not held.exists():
        assert time.monotonic() < deadline, "holder 가 락을 잡지 못했다"
        time.sleep(0.05)
    return holder


@real_flock
@pytest.mark.parametrize("spec", NEWLY_WAITING, **_ID)
def test_real_lock_released_then_chain_continues(tmp_path: Path, spec: Spec) -> None:
    """진짜 flock — 다른 프로세스가 원장 락을 2초 쥐었다 풀면, 그 뒤에 이어서 돈다."""
    root = _root(tmp_path)
    holder = _hold(tmp_path, 2)
    try:
        t0 = time.monotonic()
        p = subprocess.run(["bash", str(root / "scripts" / spec.script)],
                           env=_env(tmp_path, flock_stub=False),
                           capture_output=True, text=True, timeout=60, check=False)
        waited = time.monotonic() - t0
    finally:
        holder.wait(timeout=30)
    _, calls, notify = _read(tmp_path)
    assert holder.returncode == 0
    assert p.returncode == 0, p.stdout + p.stderr
    assert waited >= 1.0
    assert calls[0] == "released" and len(calls) > 1    # 체인 단계는 holder 가 락을 푼 뒤에 돌았다
    assert f"{spec.name} 원장 락 대기 끝" in p.stdout
    assert [n for n in notify if n.startswith(f"info|{spec.name} 원장 락 대기|")]
    assert [n for n in notify if n.startswith(spec.done)]


@real_flock
@pytest.mark.parametrize("spec", ALL, **_ID)
def test_real_second_instance_skips_while_first_waits(tmp_path: Path, spec: Spec) -> None:
    """진짜 flock — 첫 인스턴스가 기다리는 동안 둘째는 rc 3 + info, 풀리면 첫째만 이어서 돈다."""
    root = _root(tmp_path)
    env = _env(tmp_path, flock_stub=False)
    cmd = ["bash", str(root / "scripts" / spec.script)]
    holder = _hold(tmp_path, 5)   # 둘째 실행이 끝날 때까지 넉넉히(느린 CI)
    first = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True)
    try:
        head = ""
        assert first.stdout is not None
        while "원장 락 대기 시작" not in head:   # 대기자 락을 잡은 뒤에 찍는 줄
            line = first.stdout.readline()
            assert line, f"첫 인스턴스가 대기에 들어가지 않았다: {head}"
            head += line
        second = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30,
                                check=False)
        rest, _ = first.communicate(timeout=60)
    finally:
        holder.wait(timeout=30)
        if first.poll() is None:
            first.kill()
    _, _, notify = _read(tmp_path)
    assert second.returncode == 3, second.stdout + second.stderr
    assert first.returncode == 0, head + rest
    assert len([n for n in notify if n.startswith(
        f"info|{spec.name} 이미 대기 중인 실행 있음 — 이번 실행 건너뜀|")]) == 1, notify
    assert len([n for n in notify if n.startswith(f"info|{spec.name} 원장 락 대기|")]) == 1, notify
    assert len([n for n in notify if n.startswith(spec.done)]) == 1, notify


@real_flock
def test_real_killed_waiter_frees_its_seat(tmp_path: Path) -> None:
    """진짜 flock — 기다리던 스크립트의 bash 만 죽어도(크론 kill·OOM) 대기자 자리가 빈다.

    다음 실행이 새 대기자가 되고, 락이 풀리면 그 실행이 이어서 돈다. 옛 코드는 대기형 `flock 9`
    자식이 대기자 락 fd 8 을 물려받아, bash 가 죽으면 고아 flock 이 자리를 계속 쥐었다 — 다음
    실행은 전부 '이미 대기 중' rc 3 이고, 락이 풀리면 고아만 잡고 끝나 아무 작업도 돌지 않았다
    (배포 묶음 5-3 리뷰 중-1). 규칙이 공용 조각 한 곳이라 스크립트 하나로 본다.
    """
    spec = BUILD
    root = _root(tmp_path)
    env = _env(tmp_path, flock_stub=False)
    cmd = ["bash", str(root / "scripts" / spec.script)]
    holder = _hold(tmp_path, 6)
    first = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True)
    second: subprocess.Popen[str] | None = None
    try:
        assert first.stdout is not None
        head = ""
        while "원장 락 대기 시작" not in head:
            line = first.stdout.readline()
            assert line, f"첫 인스턴스가 대기에 들어가지 않았다: {head}"
            head += line
        first.kill()                                   # bash 만 — 대기형 flock 자식은 고아로 남는다
        first.wait(timeout=10)
        second = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True)
        out, _ = second.communicate(timeout=60)
    finally:
        holder.wait(timeout=30)
        for p in (first, second):
            if p is not None and p.poll() is None:
                p.kill()
    _, _, notify = _read(tmp_path)
    assert "이미 대기 중인 실행 있음" not in out, out    # 죽은 실행이 자리를 쥐고 있지 않다
    assert second.returncode == 0, out
    assert f"{spec.name} 원장 락 대기 시작" in out and f"{spec.name} 원장 락 대기 끝" in out
    assert len([n for n in notify if n.startswith(spec.done)]) == 1, notify
