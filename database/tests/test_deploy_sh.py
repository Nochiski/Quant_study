"""scripts/deploy.sh 의 되돌림 방지(서버 DEPLOYED.json rev 조상 검사)와 빌드 락 — 컷오버 K1-1e.

결함(N-41 ② 배포일 점검표가 손으로 막던 것):
- **상황**: main 을 역병합한 배포 브랜치는 origin/main 을 포함하므로 ② 검사
  (`merge-base --is-ancestor origin/main HEAD`)를 늘 통과한다.
- **인풋**: wip 브랜치에서 핫픽스 H 를 배포해 서버 DEPLOYED.json rev 가 H 가 된 뒤,
  H 를 포함하지 않는 브랜치로 `deploy.sh --apply` 를 실행한다.
- **위치**: 옛 `deploy.sh` 는 DEPLOYED.json rev 를 쓰기만 하고 읽지 않았고 빌드 락
  (`/tmp/quant_ledger_build.lock`, `build_chain.sh` 가 잡는 것)도 잡지 않았다.
- **위험성**: 핫픽스가 rsync `--delete` 로 조용히 되돌아간다(DEFECT-B05·D05 와 같은 종류).
  체인이 빌드하는 도중 코드가 바뀌면 한 판 안에 옛 코드·새 코드가 섞인다.

방식: 임시 폴더에 진짜 git 저장소(+ 맨 저장소 origin)를 만들고 진짜 `deploy.sh` 를 복사해
돌린다. git 은 진짜다(조상 판정을 대역으로 흉내 내면 검사 자체를 검사하지 못한다).
`ssh`·`rsync` 는 PATH 대역이다 — ssh 는 명령을 가짜 원격 홈(`FAKE_REMOTE_HOME`)에서 bash 로
돌리고, rsync 는 인자만 적는다. 둘 다 호출 순간 빌드 락이 쥐여 있었는지(held/free)를 함께
적는다. 락 파일은 `QL_BUILD_LOCK_FILE` 임시 경로라 운영 락 `/tmp/quant_ledger_build.lock` 을
건드리지 않는다. 맥에는 flock 명령이 없어 fcntl.flock 으로 같은 일을 하는 대역을 두고(커널
락이라 의미가 같다), 진짜 flock 이 있으면(서버·CI 우분투) 그것을 쓴다. 테스트는 `--skip-tests`
로 ③(uv pytest)을 건너뛴다. 가짜 원격에는 비밀 파일 `quant-ledger/.env`(빈 더미, 600)를 기본으로
둔다 — ⑧ 비밀 파일 검사(RG-C7-4)를 통과시키기 위해서다. 실제 비밀 파일은 없다.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ROOT = "quant-ledger"         # deploy.sh 기본 QL_REMOTE_ROOT(원격 홈 기준 상대경로)
PUSHES = 5                    # src · scripts · config · _engine · rebuild_share

# 호출 순간 빌드 락이 쥐여 있었는지 — 비차단으로 잡아 보고 바로 놓는다
# (daily_report._lock_state 와 같은 방식)
_PROBE = """import fcntl, os, sys
try:
    fd = os.open(sys.argv[1], os.O_RDONLY)
except OSError:
    print("free"); sys.exit(0)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    print("held")
else:
    print("free")
"""
# `flock [-n] FD` — 물려받은 fd 에 커널 flock 을 건다. 락은 열린 파일 설명에 붙으므로 이
# 프로세스가 끝나도 그 fd 를 연 셸이 쥔 채로 남는다(진짜 flock(1) 과 같다).
_FLOCK_PY = """import fcntl, sys
args = sys.argv[1:]
flags = fcntl.LOCK_EX
if args and args[0] == "-n":
    flags |= fcntl.LOCK_NB
    args = args[1:]
try:
    fcntl.flock(int(args[0]), flags)
except OSError:
    sys.exit(1)
"""
_SSH = """#!/usr/bin/env bash
host="$1"; shift
state="$("$FAKE_PY" "$FAKE_BIN/_probe.py" "$QL_BUILD_LOCK_FILE")"
printf '%s\\t%s\\t%s\\n' "$state" "$host" "$*" >> "$FAKE_LOG/ssh.log"
cd "$FAKE_REMOTE_HOME" || exit 255
exec bash -c "$*"
"""
_RSYNC = """#!/usr/bin/env bash
mode=push
for a in "$@"; do
  case "$a" in --dry-run) mode=dry ;; --*) ;; -*n*) mode=dry ;; esac
done
state="$("$FAKE_PY" "$FAKE_BIN/_probe.py" "$QL_BUILD_LOCK_FILE")"
printf '%s\\t%s\\t%s\\n' "$mode" "$state" "$*" >> "$FAKE_LOG/rsync.log"
if [ "$mode" = push ] && [ -n "${FAKE_RSYNC_FAIL:-}" ]; then
  case "$*" in *"$FAKE_RSYNC_FAIL"*) exit 23 ;; esac
fi
"""
_FLOCK = '#!/usr/bin/env bash\nexec "$FAKE_PY" "$FAKE_BIN/_flock.py" "$@"\n'


class Scenario(NamedTuple):
    repo: Path
    remote: Path              # 가짜 원격 홈
    lock: Path                # QL_BUILD_LOCK_FILE
    log: Path
    env: dict[str, str]
    base: str                 # A — main 의 첫 커밋
    hotfix: str               # H — wip 브랜치의 핫픽스(main 에 없음)
    head: str                 # F — 배포 브랜치(main 역병합 뒤) HEAD


class Run(NamedTuple):
    rc: int
    out: str
    err: str
    rsync: list[tuple[str, str, str]]     # (push|dry, held|free, 인자)
    ssh: list[tuple[str, str]]            # (held|free, 원격 명령)
    deployed: dict[str, str] | None

    @property
    def pushes(self) -> list[tuple[str, str, str]]:
        return [r for r in self.rsync if r[0] == "push"]

    @property
    def writes(self) -> list[tuple[str, str]]:
        return [s for s in self.ssh if "DEPLOYED.json" in s[1] and ">" in s[1]]


def _git(cwd: Path, env: dict[str, str], *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True)
    return p.stdout.strip()


def _commit(repo: Path, env: dict[str, str], rel: str, body: str, msg: str) -> str:
    (repo / rel).write_text(body, encoding="utf-8")
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-q", "-m", msg)
    return _git(repo, env, "rev-parse", "HEAD")


def _scenario(tmp_path: Path) -> Scenario:
    """main: A → M · wip: A → H(핫픽스, 서버에 먼저 배포됨) · feat: A → B → merge(main) = HEAD.
    HEAD 는 origin/main 을 포함하므로 ② 는 통과하지만 H 는 들어 있지 않다."""
    fakebin, log, remote, home = (tmp_path / n for n in ("fakebin", "log", "remote", "home"))
    for d in (fakebin, log, remote / ROOT, home):
        d.mkdir(parents=True)
    stubs = {"_probe.py": _PROBE, "ssh": _SSH, "rsync": _RSYNC}
    if shutil.which("flock") is None:
        stubs |= {"_flock.py": _FLOCK_PY, "flock": _FLOCK}
    for name, body in stubs.items():
        (fakebin / name).write_text(body, encoding="utf-8")
        (fakebin / name).chmod(0o755)
    lock = tmp_path / "build.lock"
    # HOME 을 비운 폴더로, 전역·시스템 git 설정은 끈다 — 개발자 ~/.gitconfig(서명·훅)가 테스트
    # 커밋에 끼어들지 않게
    env = dict(os.environ, HOME=str(home), PATH=f"{fakebin}:{os.environ['PATH']}",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
               QL_REMOTE="fake-host", QL_REMOTE_ROOT=ROOT, QL_BUILD_LOCK_FILE=str(lock),
               FAKE_PY=sys.executable, FAKE_BIN=str(fakebin), FAKE_LOG=str(log),
               FAKE_REMOTE_HOME=str(remote))
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "FAKE_RSYNC_FAIL"):
        env.pop(k, None)

    origin, repo = tmp_path / "origin.git", tmp_path / "repo"
    _git(tmp_path, env, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, env, "init", "-q", "-b", "main", str(repo))
    for rel in ("database/scripts", "database/src", "database/config",
                "backend/src/strategy_workbench", "backend/ops"):
        (repo / rel).mkdir(parents=True)
    shutil.copy(SCRIPTS / "deploy.sh", repo / "database/scripts/deploy.sh")
    (repo / "database/scripts/deploy.sh").chmod(0o755)
    (repo / "backend/src/strategy_workbench/__init__.py").write_text("", encoding="utf-8")
    (repo / "backend/ops/rebuild_share.py").write_text("", encoding="utf-8")
    (repo / "database/config/m.toml").write_text("", encoding="utf-8")
    base = _commit(repo, env, "database/src/app.py", "v = 1\n", "A")
    _git(repo, env, "remote", "add", "origin", str(origin))
    _git(repo, env, "push", "-q", "origin", "main")
    _git(repo, env, "checkout", "-q", "-b", "wip", base)
    hotfix = _commit(repo, env, "database/src/app.py", "v = 2  # 핫픽스\n", "H")
    _git(repo, env, "checkout", "-q", "main")
    _commit(repo, env, "database/config/m.toml", "x = 1\n", "M")
    _git(repo, env, "push", "-q", "origin", "main")
    _git(repo, env, "checkout", "-q", "-b", "feat", base)
    _commit(repo, env, "database/src/feat.py", "f = 1\n", "B")
    _git(repo, env, "merge", "-q", "--no-ff", "-m", "merge main", "main")
    head = _git(repo, env, "rev-parse", "HEAD")
    secret = remote / ROOT / ".env"          # ⑧ 비밀 파일 — 빈 더미, 권한 600
    secret.write_text("", encoding="utf-8")
    secret.chmod(0o600)
    return Scenario(repo, remote, lock, log, env, base, hotfix, head)


def _server_rev(sc: Scenario, rev: str) -> None:
    (sc.remote / ROOT / "DEPLOYED.json").write_text(
        json.dumps({"rev": rev, "branch": "wip", "at_utc": "2026-10-09T01:00:00Z",
                    "by": "x@y", "tests": "ok"}, separators=(",", ":")) + "\n", encoding="utf-8")


@contextmanager
def _held(lock: Path) -> Iterator[None]:
    """다른 프로세스(체인)가 빌드 락을 쥔 상태."""
    with lock.open("w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield


def _lock_free(lock: Path) -> bool:
    with lock.open("a") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True


def _deploy(sc: Scenario, *args: str) -> Run:
    p = subprocess.run([str(sc.repo / "database/scripts/deploy.sh"), *args], cwd=sc.repo,
                       env=sc.env, capture_output=True, text=True, timeout=60, check=False)

    def rows(name: str) -> list[list[str]]:
        f = sc.log / name
        if not f.exists():
            return []
        return [ln.split("\t") for ln in f.read_text(encoding="utf-8").splitlines()]

    dep = sc.remote / ROOT / "DEPLOYED.json"
    return Run(p.returncode, p.stdout, p.stderr,
               [(m, s, a) for m, s, a in rows("rsync.log")],
               [(s, c) for s, _host, c in rows("ssh.log")],
               json.loads(dep.read_text(encoding="utf-8")) if dep.exists() else None)


# ── 조상 검사 ────────────────────────────────────────────────────────────────
def test_apply_passes_when_server_rev_is_ancestor_and_holds_lock(tmp_path: Path) -> None:
    """서버 rev(A)가 HEAD 의 조상이면 배포한다. rsync 5건과 DEPLOYED.json 쓰기는 모두 빌드 락을
    쥔 채로 돌고, 끝나면 락이 풀린다. 옛 코드는 락 없이 밀었다(rsync·쓰기 시점 free)."""
    sc = _scenario(tmp_path)
    _server_rev(sc, sc.base)
    r = _deploy(sc, "--apply", "--skip-tests")
    assert r.rc == 0, r.out + r.err
    assert len(r.pushes) == PUSHES
    assert all(state == "held" for _m, state, _a in r.pushes), r.pushes
    assert [state for state, _c in r.writes] == ["held"]
    assert r.deployed is not None and r.deployed["rev"] == sc.head
    assert "rollback_from" not in r.deployed
    assert f"서버 {sc.base} 가 HEAD" in r.out
    assert "비밀 파일: " in r.out and "권한 600" in r.out
    assert _lock_free(sc.lock)


def test_apply_refuses_server_rev_missing_from_head_after_main_backmerge(tmp_path: Path) -> None:
    """main 역병합으로 ②(origin/main 포함)는 통과하지만 서버 rev(핫픽스 H)가 HEAD 에 없다 →
    거부(rc 2). rsync 실전송 0 · DEPLOYED.json 그대로 · 안내문에 되돌림 인자 그대로.
    옛 코드는 H 를 조용히 덮었다."""
    sc = _scenario(tmp_path)
    _server_rev(sc, sc.hotfix)
    r = _deploy(sc, "--apply", "--skip-tests")
    assert r.rc == 2, r.out + r.err
    assert "origin/main 을 포함한다" in r.out           # ② 는 통과했다 — 막은 것은 새 검사다
    assert f"서버 rev {sc.hotfix} 가 HEAD=feat ({sc.head}) 에 들어 있지 않다" in r.err
    assert f"deploy.sh --apply --allow-rollback {sc.hotfix}" in r.err
    assert r.pushes == [] and r.writes == []
    assert r.deployed is not None and r.deployed["rev"] == sc.hotfix
    assert _lock_free(sc.lock)


def test_apply_allow_rollback_passes_and_records(tmp_path: Path) -> None:
    """`--allow-rollback <서버 rev>` 면 알고 되돌리는 배포로 통과하고 DEPLOYED.json 에
    rollback_from 을 남긴다."""
    sc = _scenario(tmp_path)
    _server_rev(sc, sc.hotfix)
    r = _deploy(sc, "--apply", "--skip-tests", "--allow-rollback", sc.hotfix)
    assert r.rc == 0, r.out + r.err
    assert len(r.pushes) == PUSHES and all(s == "held" for _m, s, _a in r.pushes)
    assert r.deployed is not None
    assert (r.deployed["rev"], r.deployed["rollback_from"]) == (sc.head, sc.hotfix)
    assert f"되돌림 허용: 서버 {sc.hotfix}" in r.out
    assert f"rollback_from={sc.hotfix}" in r.out


def test_allow_rollback_must_name_the_server_rev(tmp_path: Path) -> None:
    """`--allow-branch` 처럼 덮을 대상을 정확히 적어야 한다 — 다른 rev 를 적으면 거부."""
    sc = _scenario(tmp_path)
    _server_rev(sc, sc.hotfix)
    r = _deploy(sc, "--apply", "--skip-tests", "--allow-rollback", sc.base)
    assert r.rc == 2, r.out + r.err
    assert f"--allow-rollback {sc.base} 은 서버 rev 와 다르다" in r.err
    assert r.pushes == [] and r.writes == []
    assert r.deployed is not None and r.deployed["rev"] == sc.hotfix


def test_apply_refuses_when_server_rev_unreadable(tmp_path: Path) -> None:
    """서버 DEPLOYED.json 이 없으면(또는 rev 를 못 읽으면) 되돌림 여부를 판정할 수 없다 → 거부."""
    sc = _scenario(tmp_path)
    r = _deploy(sc, "--apply", "--skip-tests")
    assert r.rc == 2, r.out + r.err
    assert "DEPLOYED.json 을 읽지 못했다" in r.err
    assert "DEPLOYED.json 을 마지막 배포 rev 로 되살린 뒤 다시 실행한다" in r.err   # 복구 안내
    assert r.pushes == [] and r.writes == [] and r.deployed is None


# ── 빌드 락 ──────────────────────────────────────────────────────────────────
def test_apply_refuses_when_build_lock_held(tmp_path: Path) -> None:
    """체인이 빌드 락을 쥐고 있으면 기다리지 않고(P9) 거부한다 — rsync 실전송 0 ·
    DEPLOYED.json 그대로."""
    sc = _scenario(tmp_path)
    _server_rev(sc, sc.base)
    with _held(sc.lock):
        r = _deploy(sc, "--apply", "--skip-tests")
    assert r.rc == 2, r.out + r.err
    assert "다른 빌드·배포가 실행 중" in r.err and str(sc.lock) in r.err
    assert r.pushes == [] and r.writes == []
    assert r.deployed is not None and r.deployed["rev"] == sc.base


def test_apply_refuses_when_lock_state_unknown(tmp_path: Path) -> None:
    """원격 응답이 LOCKED·BUSY 어느 쪽도 아니면(여기선 락 파일을 열 수 없어 빈 응답) 겹침을
    판정할 수 없다 → 거부. rsync 실전송 0 · DEPLOYED.json 그대로."""
    sc = _scenario(tmp_path)
    _server_rev(sc, sc.base)
    bad = tmp_path / "no-such-dir" / "build.lock"
    r = _deploy(sc._replace(env={**sc.env, "QL_BUILD_LOCK_FILE": str(bad)}),
                "--apply", "--skip-tests")
    assert r.rc == 2, r.out + r.err
    assert "서버 빌드 락" in r.err and "확인하지 못했다" in r.err and str(bad) in r.err
    assert r.pushes == [] and r.writes == []
    assert r.deployed is not None and r.deployed["rev"] == sc.base


def test_apply_rsync_failure_midway_releases_lock_and_keeps_deployed(tmp_path: Path) -> None:
    """두 번째 실전송(scripts/)에서 rsync 가 실패하면 그 rc 로 멈추고 DEPLOYED.json 은 쓰지 않으며
    (서버 rev 그대로) 락은 풀린다 — 다음 체인이 막히지 않는다."""
    sc = _scenario(tmp_path)
    _server_rev(sc, sc.base)
    r = _deploy(sc._replace(env={**sc.env, "FAKE_RSYNC_FAIL": "database/scripts/"}),
                "--apply", "--skip-tests")
    assert r.rc == 23, r.out + r.err
    assert len(r.pushes) == 2 and all(s == "held" for _m, s, _a in r.pushes)
    assert r.writes == []
    assert r.deployed is not None and r.deployed["rev"] == sc.base
    assert _lock_free(sc.lock)


# ── dry-run ─────────────────────────────────────────────────────────────────
def test_dry_run_reports_both_checks_and_writes_nothing(tmp_path: Path) -> None:
    """dry-run 은 두 검사를 판정해 결과만 출력하고(rc 0) 쓰기는 0 — rsync 는 전부 dry,
    DEPLOYED.json 쓰기 없음."""
    sc = _scenario(tmp_path)
    _server_rev(sc, sc.hotfix)
    with _held(sc.lock):
        r = _deploy(sc)
    assert r.rc == 0, r.out + r.err
    assert "(dry-run) --apply 라면 거부" in r.out
    assert "다른 빌드·배포가 실행 중" in r.out
    assert f"서버 rev {sc.hotfix} 가 HEAD=feat ({sc.head}) 에 들어 있지 않다" in r.out
    assert r.rsync and r.pushes == []
    assert r.writes == []
    assert r.deployed is not None and r.deployed["rev"] == sc.hotfix


def test_dry_run_passing_checks_leaves_lock_free(tmp_path: Path) -> None:
    """두 검사가 통과하는 dry-run — 락은 잡았다가 바로 놓아 체인을 막지 않는다."""
    sc = _scenario(tmp_path)
    _server_rev(sc, sc.base)
    r = _deploy(sc)
    assert r.rc == 0, r.out + r.err
    assert "빌드 락: " in r.out and "비어 있음" in r.out
    assert f"서버 {sc.base} 가 HEAD" in r.out
    assert "--apply 라면 거부" not in r.out
    assert r.pushes == [] and r.writes == []
    assert _lock_free(sc.lock)


# ── ⑧ 비밀 파일(RG-C7-4) ─────────────────────────────────────────────────────
def _break_secret(sc: Scenario, how: str) -> None:
    secret = sc.remote / ROOT / ".env"
    secret.unlink()
    if how == "mode644":
        secret.write_text("", encoding="utf-8")
        secret.chmod(0o644)
    elif how == "symlink":                   # 다른 시스템의 비밀 파일을 가리키는 링크
        other = sc.remote / "other-system" / ".env"
        other.parent.mkdir()
        other.write_text("", encoding="utf-8")
        other.chmod(0o600)
        secret.symlink_to(other)


def test_apply_refuses_when_secret_file_not_ready(tmp_path: Path) -> None:
    """서버 `~/quant-ledger/.env` 가 없거나·600 이 아니거나·링크면 rsync 전에 거부(rc 2) —
    체인 스크립트가 QL_ENV 를 그 경로로 고정하므로 그대로 밀면 다음 체인이 비밀을 못 읽는다.
    rsync 실전송 0 · DEPLOYED.json 그대로 · 빌드 락은 잡지도 않는다(락보다 먼저 판정)."""
    for how, needle in (("missing", "가 없다"), ("mode644", "권한이 644"),
                        ("symlink", "일반 파일이 아니다(SYMLINK)")):
        sc = _scenario(tmp_path / how)
        _server_rev(sc, sc.base)
        _break_secret(sc, how)
        r = _deploy(sc, "--apply", "--skip-tests")
        assert r.rc == 2, (how, r.out + r.err)
        assert needle in r.err, (how, r.err)
        assert "서버 비밀 파일 ~/quant-ledger/.env" in r.err
        assert "README '운영 (P6) → 비밀 파일'" in r.err
        assert r.pushes == [] and r.writes == []
        assert not any("flock" in c for _s, c in r.ssh)          # 락 명령 전에 멈췄다
        assert r.deployed is not None and r.deployed["rev"] == sc.base


def test_dry_run_reports_secret_file_problem(tmp_path: Path) -> None:
    """dry-run 도 같은 검사를 하고 결과만 출력한다(rc 0, 실전송·쓰기 0)."""
    sc = _scenario(tmp_path)
    _server_rev(sc, sc.base)
    _break_secret(sc, "missing")
    r = _deploy(sc)
    assert r.rc == 0, r.out + r.err
    assert "(dry-run) --apply 라면 거부: 서버 비밀 파일 ~/quant-ledger/.env 가 없다" in r.out
    assert "README '운영 (P6) → 비밀 파일'" in r.out
    assert r.rsync and r.pushes == [] and r.writes == []


def test_secret_check_never_reads_content(tmp_path: Path) -> None:
    """⑧ 은 존재·권한만 본다 — `.env` 를 다루는 원격 명령에 내용을 읽는 명령이 없다."""
    sc = _scenario(tmp_path)
    _server_rev(sc, sc.base)
    r = _deploy(sc)
    cmds = [c for _s, c in r.ssh if ".env" in c]
    assert len(cmds) == 1, r.ssh
    assert re.search(r"\b(cat|grep|head|tail|sed|awk|cut|source|xargs|less|more|od|xxd)\b",
                     cmds[0]) is None, cmds[0]
