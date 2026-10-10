"""scripts/v3_post.sh — 스테이징 → compat --in-place → 게이트 → 9표 한 트랜잭션 → daily_post (QL-F).

정본 `docs/plans/2026-10-10-cutover-track.md` §3 P4 QL-F. HOME 대신 `QL_HOME` 을 임시 폴더로 두고 진짜
스크립트·진짜 python(`.venv/bin/python` → 이 인터프리터)·진짜 compat 을 돌린다. compat 원천은
`test_compat_export` 의 합성 equity·stage 판과 모델 판(09-23, basis morning)이다. 대역은 `notify.sh`
(줄마다 "등급|제목|본문")와 맥에 없는 `flock`(인자를 flock.txt 에 적고 -n 은 FLOCK_N_RC, 대기형은
FLOCK_WAIT_RC 로 끝난다)뿐이다. 락 파일은 늘 `QL_V3_LOCK_FILE` 임시 경로라 운영 락
(/tmp/kael_v3_daily_all.lock)을 건드리지 않는다. 실물 flock 테스트는 flock 이 있을 때만(서버·CI 우분투) 돈다.
v3 본 파일은 compat 이 쓰는 9표 DDL(`v3_schema.sql`) + compat 밖 표 `market_indices` 다. 얕은 본 파일이라
compat 은 `--full` 로 돈다(730일 창).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

import pytest
import test_compat_export as tce

from compat.quant_db import SCHEMA_SQL_PATH

DB_ROOT = Path(__file__).resolve().parents[1]
D = tce.AS_OF                       # 20260923
D_ISO = tce.D23_ISO
MARKET_INDICES_DDL = """
CREATE TABLE IF NOT EXISTS market_indices (
    index_code TEXT NOT NULL, trade_date TEXT NOT NULL, open INTEGER NOT NULL,
    high INTEGER NOT NULL, low INTEGER NOT NULL, close INTEGER NOT NULL, volume INTEGER,
    PRIMARY KEY (index_code, trade_date)
) WITHOUT ROWID;
"""
_FLOCK = """#!/usr/bin/env bash
echo "$*" >> "$FLOCK_LOG"
if [ "$1" = "-n" ]; then exit "${FLOCK_N_RC:-0}"; fi
exit "${FLOCK_WAIT_RC:-0}"
"""


class Run(NamedTuple):
    rc: int
    out: str
    notify: list[str]
    flock: list[str]
    elapsed: float


def _model_root(base: Path) -> Path:
    root = base / "model"
    tce._write_model_run(root, D_ISO, tce.MB_D23, {tce.SCOPE: tce._scope_rows(D_ISO),
                                                   tce.V2: tce._v2_rows(D_ISO)})
    return root


@pytest.fixture(scope="module")
def sources(tmp_path_factory) -> tuple[Path, Path, Path]:
    """(equity_root, stage_root, model_root) — 게이트를 통과하는 원천."""
    base = tmp_path_factory.mktemp("src")
    return (*tce._make_roots(base), _model_root(base))


@pytest.fixture(scope="module")
def skip_sources(tmp_path_factory) -> tuple[Path, Path, Path]:
    """daily_prices 36행 중 1행(005930 09-21)의 open 이 비었다 — compat 5% 허용(2.8%)은 통과, 게이트 0건은 실패."""
    base = tmp_path_factory.mktemp("skip")
    fillers = tce._filler_tickers(tce.N_FILLER)
    rows = tce._price_rows(bad_open=True) + [
        tce._price_row(t, tce.D23, 10_000, value=1_000_000, mktcap=1_000_000_000)
        for t in fillers[:30]]
    return (*tce._make_roots(base, price_rows=rows, adj_rows=tce._adj_rows(price_rows=rows)),
            _model_root(base))


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "ql"
    (home / "scripts").mkdir(parents=True)
    (home / ".venv" / "bin").mkdir(parents=True)
    (home / "fakebin").mkdir()
    shutil.copy(DB_ROOT / "scripts" / "v3_post.sh", home / "scripts" / "v3_post.sh")
    (home / "src").symlink_to(DB_ROOT / "src")
    stubs = {home / ".venv/bin/python": f'#!/bin/sh\nexec "{sys.executable}" "$@"\n',
             home / "scripts/notify.sh": '#!/usr/bin/env bash\necho "$1|$2|$3" >> notify.txt\n',
             home / "fakebin/flock": _FLOCK}
    for p, body in stubs.items():
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    return home


def _main_db(tmp_path: Path) -> Path:
    main = tmp_path / "v3" / "quant.db"
    main.parent.mkdir(parents=True)
    con = sqlite3.connect(str(main))
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8") + MARKET_INDICES_DDL)
    con.execute("INSERT INTO daily_prices VALUES ('005930', '2020-01-02', 1, 1, 1, 7, 1, 1, 7.0)")
    con.execute("INSERT INTO market_indices VALUES ('001', ?, 1, 1, 1, 2500, 1)", (D_ISO,))
    con.commit()
    con.close()
    return main


def _run(tmp_path: Path, src: tuple[Path, Path, Path], *args: str, stub_flock: bool = True,
         today: str | None = D, **env_extra: str) -> Run:
    home = tmp_path / "ql"
    env = dict(os.environ, QL_HOME=str(home), TMPDIR=str(tmp_path),
               QL_EQUITY_ROOT=str(src[0]), QL_STAGE_ROOT=str(src[1]), QL_MODEL_ROOT=str(src[2]),
               QL_V3_LOCK_FILE=str(tmp_path / "v3.lock"), FLOCK_LOG=str(tmp_path / "flock.txt"))
    if stub_flock:
        env["PATH"] = f"{home / 'fakebin'}:{os.environ['PATH']}"
    for k in ("QL_V3_DB", "QL_V3_POST_CMD", "QL_V3_POST_TODAY"):
        env.pop(k, None)
    if today is not None:
        env["QL_V3_POST_TODAY"] = today
    env.update(env_extra)
    t0 = time.monotonic()
    p = subprocess.run(["bash", str(home / "scripts" / "v3_post.sh"), *args], env=env,
                       capture_output=True, text=True, timeout=180, check=False)
    elapsed = time.monotonic() - t0

    def lines(path: Path) -> list[str]:
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    return Run(p.returncode, p.stdout + p.stderr, lines(home / "notify.txt"),
               lines(tmp_path / "flock.txt"), elapsed)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _q(path: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _levels(r: Run, level: str) -> list[str]:
    return [n for n in r.notify if n.startswith(level + "|")]


@pytest.fixture
def env(tmp_path: Path) -> tuple[Path, Path]:
    return _home(tmp_path), _main_db(tmp_path)


def _base(main: Path, *more: str) -> list[str]:
    return ["--date", D, "--basis", "morning", "--v3-db", str(main), "--full", *more]


# ── 제자리 반영 ──────────────────────────────────────────────────────────────
def test_in_place_reflects_nine_tables_and_keeps_other_tables(env, sources, tmp_path) -> None:
    home, main = env
    r = _run(tmp_path, sources, *_base(main))
    assert r.rc == 0, r.out
    assert _q(main, f"SELECT count(*) FROM score_history WHERE score_date='{D_ISO}'") == [
        (len(tce.SCORE_CODES),)]
    assert _q(main, f"SELECT count(*) FROM score_history_v2 WHERE score_date='{D_ISO}'") == [
        (len(tce.SCORE_CODES),)]
    assert _q(main, "SELECT count(*) FROM stocks") == [(tce.N_STOCKS,)]
    assert _q(main, "SELECT close FROM daily_prices WHERE trade_date='2020-01-02'") == [(7,)]
    assert _q(main, "SELECT close FROM market_indices") == [(2500,)]
    assert _q(main, "SELECT basis, status FROM _compat_meta") == [("morning", "ok")]
    # 락은 v3 와 같은 파일을 비대기로 잡았다(경합 없음)
    assert r.flock == ["-n 9"]
    assert "daily_post 명령 없음 — 건너뜀(기록만)" in r.out
    assert [n.split("|")[1] for n in _levels(r, "info")] == [f"v3_post {D} morning 반영 완료"]
    assert not (home / "data/_v3_post/staging_morning.db").exists()       # 성공하면 지운다
    assert (home / f"logs/v3_post/{D}_morning.log").exists()


def test_daily_post_runs_after_reflection_when_today_is_d(env, sources, tmp_path) -> None:
    home, main = env
    marker = tmp_path / "post.txt"
    probe = tmp_path / "probe.py"          # daily_post 대역 — 그 순간 본 파일의 점수 행 수를 적는다
    probe.write_text(
        "import sqlite3, sys\n"
        "n = sqlite3.connect(sys.argv[1]).execute('SELECT count(*) FROM score_history')"
        ".fetchone()[0]\n"
        "open(sys.argv[2], 'a').write(f'called {n}\\n')\n", encoding="utf-8")
    cmd = f"'{sys.executable}' '{probe}' '{main}' '{marker}'"
    r = _run(tmp_path, sources, *_base(main, "--v3-post-cmd", cmd))
    assert r.rc == 0, r.out
    # daily_post 는 반영이 끝난 본 파일을 본다
    assert marker.read_text(encoding="utf-8").split() == ["called", str(len(tce.SCORE_CODES))]
    assert "daily_post 완료" in r.out


def test_daily_post_not_called_when_local_date_is_not_d(env, sources, tmp_path) -> None:
    """v3 잡은 date.today() 로 그날을 정한다 — 자정을 넘기면 부르지 않고 warn(rc 6). 반영은 됐다."""
    home, main = env
    marker = tmp_path / "post.txt"
    r = _run(tmp_path, sources, *_base(main, "--v3-post-cmd", f"echo x >> {marker}"),
             today="20260924")
    assert r.rc == 6, r.out
    assert not marker.exists()
    assert _q(main, f"SELECT count(*) FROM score_history WHERE score_date='{D_ISO}'") == [(3,)]
    assert len(_levels(r, "warn")) == 1 and "미호출" in _levels(r, "warn")[0]
    assert _levels(r, "crit") == []


def test_daily_post_failure_is_warn_rc6(env, sources, tmp_path) -> None:
    home, main = env
    r = _run(tmp_path, sources, *_base(main, "--v3-post-cmd", "exit 7"))
    assert r.rc == 6, r.out
    assert "daily_post 실패 rc=7" in _levels(r, "warn")[0]
    assert _levels(r, "crit") == []


# ── 실패하면 본 파일 무변경 ──────────────────────────────────────────────────
def test_gate_failure_leaves_main_unchanged(env, skip_sources, tmp_path) -> None:
    """compat 은 5% 허용으로 성공하지만(1/36 건너뜀) 제자리 게이트는 0건이라 막는다(G11)."""
    home, main = env
    marker = tmp_path / "post.txt"
    before = _sha(main)
    r = _run(tmp_path, skip_sources, *_base(main, "--v3-post-cmd", f"echo x >> {marker}"))
    assert r.rc == 2, r.out
    assert "+1 skipped" in r.out                       # compat 자체는 통과했다
    assert "필수 열" in r.out
    assert _sha(main) == before
    assert not marker.exists()
    crit = _levels(r, "crit")
    assert len(crit) == 1 and "③④ 게이트·반영" in crit[0] and "v3 본 파일 무변경" in crit[0]
    assert (home / "data/_v3_post/staging_morning.db").exists()            # 실패하면 남긴다


def test_compat_failure_leaves_main_unchanged(env, sources, tmp_path) -> None:
    """그날 모델 판이 없다 — compat 이 멈추고 반영·daily_post 는 돌지 않는다."""
    home, main = env
    before = _sha(main)
    r = _run(tmp_path, sources, *_base(main, "--model-root", str(tmp_path / "no-model"),
                                       "--v3-post-cmd", "echo x"))
    assert r.rc == 2, r.out
    assert _sha(main) == before
    crit = _levels(r, "crit")
    assert len(crit) == 1 and "② compat export --in-place" in crit[0]
    assert "③④" not in r.out


# ── 그림자 ───────────────────────────────────────────────────────────────────
def test_shadow_stages_and_gates_without_writing_main_or_lock(env, sources, tmp_path) -> None:
    home, main = env
    marker = tmp_path / "post.txt"
    before = _sha(main)
    r = _run(tmp_path, sources, *_base(main, "--shadow", "--v3-post-cmd", f"echo x >> {marker}"))
    assert r.rc == 0, r.out
    assert _sha(main) == before
    assert r.flock == []                                # v3 daily_all 의 flock -n 을 막지 않는다
    assert not marker.exists()
    stg = home / "data/_v3_post/staging_morning.db"
    assert _q(stg, f"SELECT count(*) FROM score_history WHERE score_date='{D_ISO}'") == [(3,)]
    assert "그림자" in _levels(r, "info")[0]


def test_shadow_gate_failure_is_crit_and_main_unchanged(env, skip_sources, tmp_path) -> None:
    home, main = env
    before = _sha(main)
    r = _run(tmp_path, skip_sources, *_base(main, "--shadow"))
    assert r.rc == 2, r.out
    assert _sha(main) == before
    assert len(_levels(r, "crit")) == 1


def test_custom_staging_path(env, sources, tmp_path) -> None:
    home, main = env
    stg = tmp_path / "cmp" / "staging_1008.db"
    r = _run(tmp_path, sources, *_base(main, "--shadow", "--staging", str(stg)))
    assert r.rc == 0, r.out
    assert _q(stg, "SELECT status FROM _compat_meta") == [("ok",)]


# ── 락 경합 ──────────────────────────────────────────────────────────────────
def test_lock_contention_waits_then_runs(env, sources, tmp_path) -> None:
    home, main = env
    r = _run(tmp_path, sources, *_base(main), FLOCK_N_RC="1", FLOCK_WAIT_RC="0")
    assert r.rc == 0, r.out
    assert r.flock == ["-n 9", "9"]                     # 비대기 실패 → 대기형으로 기다림
    assert "v3 락 대기" in _levels(r, "info")[0]
    assert _q(main, f"SELECT count(*) FROM score_history WHERE score_date='{D_ISO}'") == [(3,)]


def test_lock_failure_is_warn_rc3_and_touches_nothing(env, sources, tmp_path) -> None:
    home, main = env
    before = _sha(main)
    r = _run(tmp_path, sources, *_base(main), FLOCK_N_RC="1", FLOCK_WAIT_RC="1")
    assert r.rc == 3, r.out
    assert _sha(main) == before
    assert not (home / "data/_v3_post").exists()        # 스테이징도 뜨지 않았다
    assert len(_levels(r, "warn")) == 1 and _levels(r, "crit") == []


@pytest.mark.skipif(shutil.which("flock") is None, reason="flock 없음(맥) — 서버·CI 우분투에서만")
def test_real_lock_held_by_v3_chain_is_waited_for(env, sources, tmp_path) -> None:
    """v3 체인(`flock -n <락> …`)이 락을 쥐고 있으면 풀릴 때까지 기다렸다가 반영한다."""
    home, main = env
    lock = tmp_path / "v3.lock"
    holder = subprocess.Popen(["flock", str(lock), "sleep", "3"])
    try:
        time.sleep(0.5)
        r = _run(tmp_path, sources, *_base(main), stub_flock=False)
    finally:
        holder.wait(timeout=10)
    assert r.rc == 0, r.out
    assert r.elapsed >= 2.0
    assert "v3 락 대기 시작" in r.out


# ── 인자 ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("args", [
    ["--basis", "morning", "--v3-db", "MAIN"],                       # --date 없음(기본값 없다)
    ["--date", D, "--basis", "noon", "--v3-db", "MAIN"],
    ["--date", D, "--basis", "morning"],                             # v3 경로 없음
    ["--date", D, "--basis", "morning", "--v3-db", "NOPE"],
    ["--date", D, "--basis", "morning", "--v3-db", "MAIN", "--bogus"],
])
def test_argument_errors_are_rc5_warn(env, sources, tmp_path, args) -> None:
    home, main = env
    args = [str(main) if a == "MAIN" else str(tmp_path / "nope.db") if a == "NOPE" else a
            for a in args]
    before = _sha(main)
    r = _run(tmp_path, sources, *args)
    assert r.rc == 5, r.out
    assert _sha(main) == before
    assert len(_levels(r, "warn")) == 1
    assert r.flock == []
