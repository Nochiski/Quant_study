"""scripts/daily_build.sh — 원장 락이 잡혀 있으면 풀릴 때까지 기다렸다 이어서 돈다.

배포 묶음 3 T16(N-23 ①·P9). HOME 을 임시 폴더로 바꿔 `~/quant-ledger` 에 대역
`.venv/bin/python`·`scripts/notify.sh` 를 두고 진짜 스크립트를 `--dry-run` 으로 돌린다
(test_daily_ledger_sh 와 같은 방식). 락 구간은 인자 해석보다 앞이라 dry-run 과 운영 경로가
같고, dry-run 은 원장 단계(대역 python)만 돌아 KRX·원장·빌드를 건드리지 않는다.
락 파일은 `QL_RAW_LOCK_FILE` 로 임시 경로를 준다 — 운영 락 `/tmp/quant_ledger_raw.lock` 을
잡지 않는다. 맥에는 flock 이 없어 PATH 대역 flock 으로 '대기형으로 불렸는가'를 보고,
진짜 flock 이 있으면(서버·CI 우분투) 다른 프로세스가 쥔 락이 풀린 뒤 이어서 도는지 실물로 본다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import NamedTuple

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "daily_build.sh"
STEPS = ["daily.kw_daily", "daily.kw_daily", "daily.ledger_health"]   # dry-run 원장 단계

_PY = """#!/usr/bin/env bash
if [ "$1" = "-m" ]; then echo "$2" >> "$QL_HOME/calls.txt"; fi
exit 0
"""
# 대역 flock — 인자를 적고, 비대기(-n)는 FLOCK_N_RC(1 = 다른 작업이 쥐고 있음),
# 대기형은 FLOCK_WAIT_RC(0 = 풀려서 잡았음)로 끝난다.
_FLOCK = """#!/usr/bin/env bash
echo "$*" >> "$FLOCK_LOG"
if [ "$1" = "-n" ]; then exit "$FLOCK_N_RC"; fi
exit "$FLOCK_WAIT_RC"
"""


class Run(NamedTuple):
    rc: int
    out: str            # stdout + stderr — 대기 시작·끝이 실시간으로 나온다(크론에선 cron 로그)
    flock: list[str]    # 대역 flock 호출 인자
    calls: list[str]    # 대역 python 이 받은 모듈(+ 실물 테스트의 holder 표식)
    notify: str
    log: str            # 체인 로그 logs/daily_build_<KST 오늘>.log


def _root(home: Path) -> Path:
    root = home / "quant-ledger"
    for sub in ("scripts", "logs", ".venv/bin"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (home / "tmp").mkdir(exist_ok=True)
    shutil.copy(SCRIPT, root / "scripts" / "daily_build.sh")
    stubs = {".venv/bin/python": _PY,
             "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2" >> notify.txt\n'}
    for rel, body in stubs.items():
        p = root / rel
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    return root


def _run(home: Path, *, flock_stub: bool, n_rc: int = 1, wait_rc: int = 0,
         held: bool = False) -> Run:
    root = home / "quant-ledger"
    env = dict(os.environ, HOME=str(home), TMPDIR=str(home / "tmp"),
               QL_RAW_LOCK_FILE=str(home / "raw.lock"))
    for k in ("QL_RAW_LOCK_HELD", "QL_BUILD_LOCK_HELD", "QL_SKIP_KW", "QL_FORCE"):
        env.pop(k, None)
    if held:
        env["QL_RAW_LOCK_HELD"] = "1"
    if flock_stub:
        fake = home / "fakebin"
        fake.mkdir(exist_ok=True)
        (fake / "flock").write_text(_FLOCK, encoding="utf-8")
        (fake / "flock").chmod(0o755)
        env.update(PATH=f"{fake}:{env['PATH']}", FLOCK_LOG=str(home / "flock.txt"),
                   FLOCK_N_RC=str(n_rc), FLOCK_WAIT_RC=str(wait_rc))
    p = subprocess.run(["bash", str(root / "scripts" / "daily_build.sh"),
                        "--date", "20261006", "--dry-run"],
                       env=env, capture_output=True, text=True, timeout=60, check=False)

    def read(path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.exists() else ""

    log = "".join(read(f) for f in sorted((root / "logs").glob("daily_build_*.log")))
    return Run(p.returncode, p.stdout + p.stderr, read(home / "flock.txt").splitlines(),
               read(root / "calls.txt").split(), read(root / "notify.txt"), log)


def test_waits_for_held_raw_lock_then_continues(tmp_path: Path) -> None:
    """G1 — 원장 락이 잡혀 있으면 포기하지 않고 대기형으로 잡은 뒤 이어서 돈다.

    옛 코드는 `flock -n` 실패에서 warn + exit 3 으로 물러나 그 D 확정판이 영구히 빠졌다
    (06:00 체인이 길어지는 공시 마감일). 기다린 시간은 실시간 출력과 체인 로그 둘 다에 남는다.
    """
    _root(tmp_path)
    r = _run(tmp_path, flock_stub=True)
    assert r.rc == 0, r.out
    assert r.flock == ["-n 9", "9"]          # 비대기로 확인 → 시간 한도 없는 대기형으로 잡는다
    assert r.calls == STEPS
    assert "원장 락 대기 시작" in r.out and "원장 락 대기 끝" in r.out
    assert "원장 락 대기" in r.log
    assert "락 실패" not in r.notify
    assert (tmp_path / "raw.lock").exists()   # QL_RAW_LOCK_FILE 경로를 썼다


def test_free_raw_lock_runs_without_waiting(tmp_path: Path) -> None:
    """락이 비어 있으면 지금처럼 바로 돈다 — 대기 기록 없음."""
    _root(tmp_path)
    r = _run(tmp_path, flock_stub=True, n_rc=0)
    assert r.rc == 0, r.out
    assert r.flock == ["-n 9"]
    assert r.calls == STEPS
    assert "원장 락 대기" not in r.out + r.log


def test_inherited_raw_lock_skips_flock(tmp_path: Path) -> None:
    """부모가 락을 물려주면(QL_RAW_LOCK_HELD=1) 지금처럼 flock 을 부르지 않는다."""
    _root(tmp_path)
    r = _run(tmp_path, flock_stub=True, held=True)
    assert r.rc == 0, r.out
    assert r.flock == []
    assert r.calls == STEPS


def test_failed_wait_never_runs_without_lock(tmp_path: Path) -> None:
    """대기형 flock 자체가 실패하면(락을 못 잡음) 락 없이 돌지 않는다 — 지금처럼 warn + exit 3."""
    _root(tmp_path)
    r = _run(tmp_path, flock_stub=True, wait_rc=1)
    assert r.rc == 3, r.out
    assert r.flock == ["-n 9", "9"]
    assert r.calls == []
    assert r.notify.startswith("warn|daily_build 락 실패")


@pytest.mark.skipif(shutil.which("flock") is None,
                    reason="flock(util-linux) 없음(맥) — 서버·CI 우분투에서 돈다")
def test_real_lock_released_then_chain_continues(tmp_path: Path) -> None:
    """진짜 flock — 다른 프로세스가 원장 락을 2초 쥐었다 풀면, 그 뒤에 이어서 돈다."""
    root = _root(tmp_path)
    held = tmp_path / "held"
    # holder 는 락을 잡은 뒤 표식을 남기고, 풀기 직전에 calls.txt 에 released 를 적는다
    holder = subprocess.Popen(["flock", str(tmp_path / "raw.lock"), "-c",
                               f"touch '{held}'; sleep 2; echo released >> '{root / 'calls.txt'}'"])
    try:
        deadline = time.monotonic() + 10
        while not held.exists():
            assert time.monotonic() < deadline, "holder 가 락을 잡지 못했다"
            time.sleep(0.05)
        t0 = time.monotonic()
        r = _run(tmp_path, flock_stub=False)
        waited = time.monotonic() - t0
    finally:
        holder.wait(timeout=30)
    assert holder.returncode == 0
    assert r.rc == 0, r.out
    assert waited >= 1.0
    assert r.calls == ["released", *STEPS]   # 원장 단계는 holder 가 락을 푼 뒤에 돌았다
    assert "원장 락 대기 시작" in r.out and "원장 락 대기 끝" in r.out
    assert "원장 락 대기" in r.log
