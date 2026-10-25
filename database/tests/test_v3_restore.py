"""QL-I — v3 quant.db 고정 백업 2벌 · 표 단위 복원(`compat.v3_restore`, `scripts/v3_backup.sh`·`v3_restore.sh`).

정본 `docs/plans/2026-10-10-cutover-track.md` §3 P4 QL-I · T-21 · §4(되돌리기 창 5거래일), 절차서
`docs/CUTOVER_ROLLBACK.md`. v3 본 파일은 v3 날짜별 사본 소형 픽스처(`fixtures/v3_daily_copy.sql` — 로컬 v3 사본의
표·색인 DDL 전부 + 3종목 행)를 WAL 모드로 연 것이다(v3 `connection.py` 와 같다). 컷오버 기간은 '9표에 compat 이
쓴 모양'(창 행 교체·새 날짜·점수 날짜 교체·스팩 신규·`_compat_meta` ok 기록)과 '9표 밖에 v3 가 계속 쓴 행'(시장
지표 새 날짜·pipeline_runs·research_reports — T-27)을 직접 만든다.
셸 테스트는 진짜 스크립트·진짜 python(`.venv/bin/python` → 이 인터프리터)을 임시 `QL_HOME` 에서 돌린다. 대역은
맥에 없는 `flock`(인자를 flock.txt 에 적고 -n 은 FLOCK_N_RC, 대기형은 FLOCK_WAIT_RC 로 끝난다 — test_v3_post_sh 와
같다)과 `crontab`(CRONTAB_OUT 을 내고 CRONTAB_RC 로 끝난다)뿐이다. 락 파일은 늘 `QL_V3_LOCK_FILE` 임시 경로라 운영
락(/tmp/kael_v3_daily_all.lock)을 건드리지 않는다. 실물 flock 테스트는 flock 이 있을 때만(서버·CI 우분투) 돈다.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

import pytest
from compat import CompatError, v3_post, v3_restore
from compat.__main__ import main as cli_main
from compat.quant_db import ExportResult, TableResult, _write_meta
from compat.v3_post import SCORE_TABLES, TABLES
from compat.v3_restore import DEFAULT_TABLES

DB_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "v3_daily_copy.sql"
STAMP = "20261019T093000"
NEW_DAY = "2026-08-10"            # 컷오버 뒤 첫 거래일(픽스처 마지막 거래일 08-07 다음)
CRONTAB = "5 11 * * 1-5 cd $HOME/kael-system-v3 && flock -n /tmp/kael_v3_daily_all.lock … --chain daily_all"
_FLOCK = """#!/usr/bin/env bash
echo "$*" >> "$FLOCK_LOG"
if [ "$1" = "-n" ]; then exit "${FLOCK_N_RC:-0}"; fi
exit "${FLOCK_WAIT_RC:-0}"
"""
_CRONTAB = """#!/usr/bin/env bash
[ "$1" = "-l" ] || exit 9
printf '%s\\n' "$CRONTAB_OUT"
exit "${CRONTAB_RC:-0}"
"""


class Run(NamedTuple):
    rc: int
    out: str
    flock: list[str]
    elapsed: float


# ── 픽스처 ───────────────────────────────────────────────────────────────────
def _make_v3(path: Path) -> Path:
    """v3 날짜별 사본(컷오버 날 아침 = 백업 시점) — WAL 모드."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(FIXTURE.read_text(encoding="utf-8"))
        con.commit()
    finally:
        con.close()
    return path


def _cutover_writes(main: Path) -> None:
    """컷오버 기간 — compat 이 9표에 쓴 것(T-16·T-33·QL-E·T-25)과 v3 가 9표 밖에 계속 쓴 것(T-27)."""
    con = sqlite3.connect(str(main), isolation_level=None)
    try:
        con.execute("BEGIN")
        # compat 9표: 창 안 행 교체(KRX 종가·전방 조정), 새 날짜, 점수 날짜 교체, 스냅샷 표 갱신, 신규 스팩
        con.execute("UPDATE daily_prices SET close = close + 1, adj_close = adj_close * 0.5 "
                    "WHERE trade_date >= '2026-08-05'")
        con.execute("INSERT INTO daily_prices VALUES ('005930', ?, 7, 7, 7, 70000, 10, 1, 70000.0)",
                    (NEW_DAY,))
        con.execute("INSERT INTO investor_detail_flows (stock_code, trade_date, individual) "
                    "VALUES ('005930', ?, -5)", (NEW_DAY,))
        con.execute("DELETE FROM score_history WHERE score_date = '2026-08-07'")
        con.execute("INSERT INTO score_history (stock_code, score_date, composite_score) "
                    "VALUES ('005930', ?, 1.5)", (NEW_DAY,))
        con.execute("INSERT INTO score_history_v2 (stock_code, score_date, total_score) "
                    "VALUES ('005930', ?, 2.5)", (NEW_DAY,))
        con.execute("INSERT INTO stocks (stock_code, stock_name, market, is_active, updated_at) "
                    "VALUES ('0000Z0', '신규스팩', 'KOSDAQ', 1, 'compat')")
        con.execute("UPDATE stocks SET market_cap = market_cap * 2")
        con.execute("UPDATE consensus_revision_daily SET op = op + 1")
        con.execute("DELETE FROM consensus_revision_compare WHERE stock_code = '035720'")
        con.execute("UPDATE consensus_annual SET op = 0")
        con.execute("UPDATE financial_summary SET roe = -1")
        # v3 가 계속 쓰는 표(daily_insight · 리서치 수집 · job_runner)
        con.execute("INSERT INTO market_indices VALUES ('001', ?, 1, 1, 1, 2600, 1)", (NEW_DAY,))
        con.execute("UPDATE market_regime SET regime_label = 'RISK_ON' WHERE trade_date = '2026-08-07'")
        con.execute("INSERT INTO pipeline_runs (job_name, started_at, status) "
                    "VALUES ('chain:daily_insight', '2026-08-10T11:05:00', 'success')")
        con.execute("INSERT INTO research_reports (category, nid, title, report_date, collected_at) "
                    "VALUES ('company', 1, '합성 리포트', ?, '2026-08-10T11:30:00')", (NEW_DAY,))
        con.execute("COMMIT")
        # compat 반영 기록 둘(첫 반영 --full 아침 · 장 마감 점수 포함) — v3_post 가 COMMIT 때 옮기는 그 행
        for basis, at in (("morning", "2000-01-01T00:00:00.000000+00:00"),
                          ("evening", "2000-01-01T09:00:00.000000+00:00")):
            _write_meta(con, ExportResult(
                date=NEW_DAY, basis=basis, target="x", exported_at=at,
                window={"days": 730, "full": basis == "morning", "from_date": "2024-08-10",
                        "to_date": NEW_DAY},
                consensus_asof=NEW_DAY,
                tables={t: TableResult(n_rows=1, n_skipped=0, sources={}) for t in TABLES}))
    finally:
        con.close()


def _user_tables(path: Path) -> list[str]:
    return [r[0] for r in _q(path, "SELECT name FROM sqlite_master WHERE type='table' "
                                   "AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _dump(path: Path, table: str) -> list[tuple]:
    return sorted(_q(path, f'SELECT * FROM "{table}"'), key=repr)


def _q(path: Path, sql: str, params: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sums(dest: Path) -> dict[str, str]:
    out = {}
    for line in (dest / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        sha, name = line.split("  ", 1)
        out[name] = sha
    return out


@pytest.fixture
def v3(tmp_path: Path) -> Path:
    return _make_v3(tmp_path / "v3" / "data" / "quant.db")


def _backup(v3_db: Path, tmp_path: Path, stamp: str = STAMP) -> Path:
    """Python 으로 2벌 — 첫 벌의 DB 경로."""
    res = v3_restore.backup(v3_db, [tmp_path / "bak1", tmp_path / "bak2"], stamp)
    return res.dests[0] / res.db_name


# ── 셸 하네스 ─────────────────────────────────────────────────────────────────
def _home(tmp_path: Path) -> Path:
    home = tmp_path / "ql"
    (home / "scripts").mkdir(parents=True)
    (home / ".venv" / "bin").mkdir(parents=True)
    (home / "fakebin").mkdir()
    for s in ("v3_backup.sh", "v3_restore.sh"):
        shutil.copy(DB_ROOT / "scripts" / s, home / "scripts" / s)
    (home / "src").symlink_to(DB_ROOT / "src")
    stubs = {home / ".venv/bin/python": f'#!/bin/sh\nexec "{sys.executable}" "$@"\n',
             home / "fakebin/flock": _FLOCK, home / "fakebin/crontab": _CRONTAB}
    for p, body in stubs.items():
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    return home


def _v3_files(tmp_path: Path) -> tuple[Path, Path]:
    """(v3 루트, uni 루트) — V3-A~E 가 바꿀 파일(COMPAT_LAYER §8-1)."""
    v3_root, uni_root = tmp_path / "v3", tmp_path / "uni"
    (v3_root / "scripts").mkdir(parents=True, exist_ok=True)
    (v3_root / "data").mkdir(parents=True, exist_ok=True)
    (uni_root / "sources").mkdir(parents=True)
    (v3_root / "scripts/job_runner.py").write_text("CHAINS = {'daily_all': []}\n", encoding="utf-8")
    (v3_root / "data/.kis_holidays.json").write_text('{"2026": ["20261009"]}', encoding="utf-8")
    (uni_root / "sources/kael_db.py").write_text("def get_signal_insights(): ...\n",
                                                 encoding="utf-8")
    return v3_root, uni_root


def _run(tmp_path: Path, script: str, *args: str, stub_flock: bool = True, **env_extra: str) -> Run:
    home = tmp_path / "ql"
    env = dict(os.environ, QL_HOME=str(home), TMPDIR=str(tmp_path),
               QL_V3_LOCK_FILE=str(tmp_path / "v3.lock"), FLOCK_LOG=str(tmp_path / "flock.txt"),
               CRONTAB_OUT=CRONTAB, PATH=f"{home / 'fakebin'}:{os.environ['PATH']}")
    if not stub_flock:
        (home / "fakebin/flock").unlink()
    env.update(env_extra)
    t0 = time.monotonic()
    p = subprocess.run(["bash", str(home / "scripts" / script), *args], env=env,
                       capture_output=True, text=True, timeout=120, check=False)
    flock = tmp_path / "flock.txt"
    return Run(p.returncode, p.stdout + p.stderr,
               flock.read_text(encoding="utf-8").splitlines() if flock.exists() else [],
               time.monotonic() - t0)


def _backup_sh(tmp_path: Path, v3_db: Path, *more: str, **env: str) -> Run:
    v3_root, uni_root = tmp_path / "v3", tmp_path / "uni"
    return _run(tmp_path, "v3_backup.sh", "--v3-db", str(v3_db), "--dest", str(tmp_path / "bak1"),
                "--dest", str(tmp_path / "bak2"), "--v3-root", str(v3_root),
                "--uni-root", str(uni_root), *more, **env)


def _restore_sh(tmp_path: Path, backup: Path, v3_db: Path, *more: str, **env: str) -> Run:
    return _run(tmp_path, "v3_restore.sh", "--backup", str(backup), "--v3-db", str(v3_db), *more,
                **env)


# ── 백업 ─────────────────────────────────────────────────────────────────────
def test_backup_sh_writes_two_identical_read_only_sets(tmp_path, v3) -> None:
    """T-21 — 서로 다른 두 경로에 같은 사본. DB 는 온라인 백업 · integrity ok · 0444, V3-A~E 가 바꿀 파일 사본은
    `<이름>.bak.<stamp>`, SHA256SUMS 가 전부를 맞춘다. 본 파일은 그대로다."""
    _home(tmp_path)
    v3_root, uni_root = _v3_files(tmp_path)
    before = _sha(v3)
    r = _backup_sh(tmp_path, v3)
    assert r.rc == 0, r.out
    assert r.flock == []                                  # 백업은 v3 락을 잡지 않는다
    assert _sha(v3) == before
    sets = []
    for dest in (tmp_path / "bak1", tmp_path / "bak2"):
        sums = _sums(dest)
        dbs = [n for n in sums if n.startswith("quant_") and n.endswith(".db")]
        assert len(dbs) == 1
        stamp = dbs[0][len("quant_"):-len(".db")]
        assert sorted(sums) == sorted([dbs[0], f"job_runner.py.bak.{stamp}",
                                       f".kis_holidays.json.bak.{stamp}", f"kael_db.py.bak.{stamp}",
                                       f"crontab.bak.{stamp}"])
        for name, sha in sums.items():
            p = dest / name
            assert _sha(p) == sha, name
            assert stat.S_IMODE(p.stat().st_mode) == 0o444, name
        assert (dest / f"job_runner.py.bak.{stamp}").read_bytes() == \
            (v3_root / "scripts/job_runner.py").read_bytes()
        assert (dest / f".kis_holidays.json.bak.{stamp}").read_bytes() == \
            (v3_root / "data/.kis_holidays.json").read_bytes()
        assert (dest / f"kael_db.py.bak.{stamp}").read_bytes() == \
            (uni_root / "sources/kael_db.py").read_bytes()
        assert (dest / f"crontab.bak.{stamp}").read_text(encoding="utf-8") == CRONTAB + "\n"
        db = dest / dbs[0]
        assert _q(db, "PRAGMA integrity_check") == [("ok",)]
        for t in _user_tables(v3):
            assert _dump(db, t) == _dump(v3, t), t
        sets.append(sums)
    assert sets[0] == sets[1]                              # 두 벌이 바이트까지 같다
    assert list((tmp_path / "ql/logs/v3_backup").glob("*.log"))


def test_backup_is_rollback_journal_and_reads_without_sidecars(tmp_path, v3) -> None:
    """본 파일이 WAL 이어도 백업은 rollback journal 이다 — 0444 파일을 읽기 전용으로 열 때 -wal/-shm 을
    만들지 않는다(복원이 백업 폴더에 아무것도 쓰지 않는다)."""
    db = _backup(v3, tmp_path)
    assert _q(db, "PRAGMA journal_mode") == [("delete",)]
    assert _q(db, "SELECT count(*) FROM daily_prices") == [(18,)]
    assert sorted(p.name for p in db.parent.iterdir()) == sorted([*_sums(db.parent), "SHA256SUMS"])


def test_backup_never_overwrites(tmp_path, v3) -> None:
    """같은 이름이 이미 있으면(어느 한 경로라도) 시작 전에 멈춘다 — 있던 사본·SHA256SUMS 그대로."""
    db = _backup(v3, tmp_path)
    shas = {p: _sha(p) for d in (tmp_path / "bak1", tmp_path / "bak2") for p in d.iterdir()}
    with pytest.raises(CompatError, match="이미 있다"):
        _backup(v3, tmp_path)
    assert {p: _sha(p) for d in (tmp_path / "bak1", tmp_path / "bak2") for p in d.iterdir()} == shas
    # 둘째 경로에만 있어도 멈춘다 — 첫 경로에 새 벌을 만들지 않는다
    (tmp_path / "bak1" / db.name).chmod(0o644)
    (tmp_path / "bak1" / db.name).unlink()
    with pytest.raises(CompatError, match="이미 있다"):
        _backup(v3, tmp_path)
    assert not (tmp_path / "bak1" / db.name).exists()


@pytest.mark.parametrize("how", ["same", "symlink"])
def test_backup_needs_two_different_paths(tmp_path, v3, how) -> None:
    """T-21 '서로 다른 경로 2벌' — 같은 폴더(링크 포함)를 두 번 주면 한 벌뿐이라 거부한다."""
    a = tmp_path / "bak1"
    a.mkdir()
    b = a
    if how == "symlink":
        b = tmp_path / "link"
        b.symlink_to(a)
    with pytest.raises(CompatError, match="서로 다른"):
        v3_restore.backup(v3, [a, b], STAMP)
    assert list(a.iterdir()) == []
    with pytest.raises(CompatError, match="둘"):
        v3_restore.backup(v3, [a], STAMP)


def test_backup_refuses_when_disk_is_short(tmp_path, v3, monkeypatch) -> None:
    """여유 < 백업 크기 × 2(벌마다, 같은 파일시스템이면 합친다) 이면 아무것도 만들지 않는다."""
    size = v3.stat().st_size
    real = shutil.disk_usage
    monkeypatch.setattr(v3_restore.shutil, "disk_usage",
                        lambda p: real(p)._replace(free=size * v3_post.DISK_FACTOR * 2 - 1))
    with pytest.raises(CompatError, match="디스크 여유"):
        _backup(v3, tmp_path)
    assert not any((tmp_path / d).exists() and any((tmp_path / d).iterdir())
                   for d in ("bak1", "bak2"))


def test_backup_failure_removes_what_it_made(tmp_path, v3, monkeypatch) -> None:
    """둘째 벌 복사 중 실패 — 이번에 만든 파일을 지우고, SHA256SUMS 에는 아무 줄도 남기지 않는다."""
    def flaky(src, dst, **kw):
        raise OSError("디스크 오류(시험)")

    monkeypatch.setattr(v3_restore.shutil, "copyfile", flaky)
    with pytest.raises(OSError, match="시험"):
        _backup(v3, tmp_path)
    for d in ("bak1", "bak2"):
        assert [p.name for p in (tmp_path / d).iterdir()] == [], d


def test_backup_failure_on_second_sums_restores_the_first_sums(tmp_path, v3, monkeypatch) -> None:
    """둘째 경로 SHA256SUMS 덧붙이기에서 실패 — 첫 경로에 이미 덧붙인 줄을 걷어 내 SHA256SUMS 가 그 전 내용(앞 백업
    줄)으로 돌아가고, 이번 파일은 지워진다(지워진 파일을 가리키는 줄이 남지 않는다)."""
    _backup(v3, tmp_path, stamp="20261018T090000")              # 앞 백업 — SHA256SUMS 에 줄이 있다
    before = {d: (tmp_path / d / "SHA256SUMS").read_bytes() for d in ("bak1", "bak2")}
    calls = {"n": 0}
    real = v3_restore._append_sums

    def flaky(folder, sums):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("SUMS 쓰기 오류(시험)")
        return real(folder, sums)

    monkeypatch.setattr(v3_restore, "_append_sums", flaky)
    with pytest.raises(OSError, match="시험"):
        _backup(v3, tmp_path)
    for d in ("bak1", "bak2"):
        assert (tmp_path / d / "SHA256SUMS").read_bytes() == before[d], d
        assert not list((tmp_path / d).glob(f"*{STAMP}*")), d


def test_backup_sh_missing_v3_file_is_rc5_and_writes_nothing(tmp_path, v3) -> None:
    _home(tmp_path)
    v3_root, _ = _v3_files(tmp_path)
    (v3_root / "scripts/job_runner.py").unlink()
    r = _backup_sh(tmp_path, v3)
    assert r.rc == 5, r.out
    assert "job_runner.py" in r.out
    assert not (tmp_path / "bak1").exists() and not (tmp_path / "bak2").exists()


def test_backup_sh_crontab_failure_is_rc2_and_writes_nothing(tmp_path, v3) -> None:
    _home(tmp_path)
    _v3_files(tmp_path)
    r = _backup_sh(tmp_path, v3, CRONTAB_RC="1")
    assert r.rc == 2, r.out
    assert "crontab" in r.out
    assert not (tmp_path / "bak1").exists() and not (tmp_path / "bak2").exists()


@pytest.mark.parametrize("args", [
    [],                                                          # 인자 없음
    ["--dest", "only-one"],
])
def test_backup_sh_argument_errors_are_rc5(tmp_path, v3, args) -> None:
    _home(tmp_path)
    r = _run(tmp_path, "v3_backup.sh", "--v3-db", str(v3), *args)
    assert r.rc == 5, r.out


# ── 복원: 리허설(백업 → 컷오버 기간 쓰기 → 계획 → 복원) ─────────────────────────────
def test_rehearsal_restores_seven_tables_and_leaves_the_rest(tmp_path, v3) -> None:
    """정본 QL-I 리허설(T-42) — 기본 7표는 백업과 같아지고, 점수 두 표(컷오버 기간에 실제로 나간 점수)와 9표 밖(v3 가
    계속 쓴 행 포함)은 그대로, `_compat_meta` 에 복원 기록 1행이 붙는다. --dry-run 은 표별 행 수만 보이고 락도 잡지
    않는다. 백업 파일은 바뀌지 않는다."""
    _home(tmp_path)
    _v3_files(tmp_path)
    assert _backup_sh(tmp_path, v3).rc == 0
    backup = next((tmp_path / "bak1").glob("quant_*.db"))
    bak_sha = _sha(backup)
    _cutover_writes(v3)
    outside = [t for t in _user_tables(v3) if t not in TABLES and t != "_compat_meta"]
    assert "market_indices" in outside and "pipeline_runs" in outside
    keep = {t: _dump(v3, t) for t in (*outside, *SCORE_TABLES)}
    meta_before = _dump(v3, "_compat_meta")
    assert len(meta_before) == 2
    assert all(_dump(v3, t) != _dump(backup, t) for t in TABLES)

    before = _sha(v3)
    dry = _restore_sh(tmp_path, backup, v3, "--dry-run")
    assert dry.rc == 0, dry.out
    assert dry.flock == []
    assert _sha(v3) == before
    assert "daily_prices: 본 파일 19행 → 백업 18행 (-1)" in dry.out
    assert "stocks: 본 파일 4행 → 백업 3행 (-1)" in dry.out
    assert "score_history" not in dry.out
    assert "쓰지 않았다" in dry.out

    r = _restore_sh(tmp_path, backup, v3)
    assert r.rc == 0, r.out
    assert r.flock == ["-n 9"]
    for t in DEFAULT_TABLES:
        assert _dump(v3, t) == _dump(backup, t), t
    for t in (*outside, *SCORE_TABLES):
        assert _dump(v3, t) == keep[t], t
    # 반영 기록은 지우지 않고(이력) 복원 기록 1행만 덧붙는다
    after = _dump(v3, "_compat_meta")
    assert len(after) == 3 and all(m in after for m in meta_before)
    meta = _q(v3, "SELECT exported_at, date, basis, status, tables, \"window\" FROM _compat_meta "
                  "WHERE basis = 'restore'")
    assert len(meta) == 1
    rec = meta[0]
    assert rec[0] > max(m[0] for m in meta_before)
    assert rec[2:4] == ("restore", "ok")
    counts = json.loads(rec[4])
    assert set(counts) == set(DEFAULT_TABLES) and len(counts) == 7
    assert counts["daily_prices"] == {"n_before": 19, "n_rows": 18}
    win = json.loads(rec[5])
    assert win["sha256"] == bak_sha and win["backup"].endswith(backup.name)
    assert _q(v3, "PRAGMA integrity_check") == [("ok",)]
    assert _sha(backup) == bak_sha
    assert list((tmp_path / "ql/logs/v3_restore").glob("*.log"))
    # 복원 뒤 다음 반영은 '첫 반영' — 복원 앞 장 마감 점수 기록은 T-34 판정에 들지 않는다
    assert v3_post.tables_for(v3, NEW_DAY.replace("-", ""), "morning") == TABLES


def test_with_scores_restores_all_nine(tmp_path, v3) -> None:
    """되돌리는 이유가 점수 오류일 때(T-42) — `--with-scores` 면 점수 두 표까지 9표가 백업과 같아진다."""
    _home(tmp_path)
    backup = _backup(v3, tmp_path)
    _cutover_writes(v3)
    r = _restore_sh(tmp_path, backup, v3, "--with-scores")
    assert r.rc == 0, r.out
    for t in TABLES:
        assert _dump(v3, t) == _dump(backup, t), t
    rec = _q(v3, "SELECT tables FROM _compat_meta WHERE basis = 'restore'")
    assert sorted(json.loads(rec[0][0])) == sorted(TABLES)


def test_score_tables_need_with_scores(tmp_path, v3) -> None:
    """점수 표를 --tables 로 골라도 --with-scores 없이는 거부한다(T-42 — 실수로 발송된 점수를 지우지 않게)."""
    backup = _backup(v3, tmp_path)
    _cutover_writes(v3)
    before = _sha(v3)
    with pytest.raises(CompatError, match="with-scores"):
        v3_restore.restore(backup, v3, tables=("daily_prices", "score_history"))
    assert _sha(v3) == before


def test_verification_runs_before_the_lock(tmp_path, v3) -> None:
    """백업 sha 검증은 락을 잡기 전 — 손상된 백업이면 v3 락을 한 번도 잡지 않고 rc 2."""
    _home(tmp_path)
    backup = _backup(v3, tmp_path)
    _cutover_writes(v3)
    backup.chmod(0o644)
    with open(backup, "ab") as f:
        f.write(b"x")
    before = _sha(v3)
    r = _restore_sh(tmp_path, backup, v3)
    assert r.rc == 2, r.out
    assert r.flock == []
    assert "sha256" in r.out and "락을 잡지 않았다" in r.out
    assert _sha(v3) == before


# ── 복원: 락 ──────────────────────────────────────────────────────────────────
def test_restore_lock_contention_waits_then_restores(tmp_path, v3) -> None:
    _home(tmp_path)
    backup = _backup(v3, tmp_path)
    _cutover_writes(v3)
    r = _restore_sh(tmp_path, backup, v3, FLOCK_N_RC="1", FLOCK_WAIT_RC="0")
    assert r.rc == 0, r.out
    assert r.flock == ["-n 9", "9"]                     # 비대기 실패 → 대기형으로 기다림
    assert "v3 락 대기" in r.out
    assert _dump(v3, "daily_prices") == _dump(backup, "daily_prices")


def test_restore_lock_failure_is_rc3_and_touches_nothing(tmp_path, v3) -> None:
    _home(tmp_path)
    backup = _backup(v3, tmp_path)
    _cutover_writes(v3)
    before = _sha(v3)
    r = _restore_sh(tmp_path, backup, v3, FLOCK_N_RC="1", FLOCK_WAIT_RC="1")
    assert r.rc == 3, r.out
    assert _sha(v3) == before


@pytest.mark.skipif(shutil.which("flock") is None, reason="flock 없음(맥) — 서버·CI 우분투에서만")
def test_real_lock_held_by_v3_chain_is_waited_for(tmp_path, v3) -> None:
    """v3 체인(`flock -n <락> …`)이나 v3_post 가 락을 쥐고 있으면 풀릴 때까지 기다렸다가 복원한다."""
    _home(tmp_path)
    backup = _backup(v3, tmp_path)
    _cutover_writes(v3)
    holder = subprocess.Popen(["flock", str(tmp_path / "v3.lock"), "sleep", "3"])
    try:
        time.sleep(0.5)
        r = _restore_sh(tmp_path, backup, v3, stub_flock=False)
    finally:
        holder.wait(timeout=10)
    assert r.rc == 0, r.out
    assert r.elapsed >= 2.0
    assert "v3 락 대기 시작" in r.out
    assert _dump(v3, "daily_prices") == _dump(backup, "daily_prices")


# ── 복원: 원자성·검증 ─────────────────────────────────────────────────────────
def test_restore_failure_rolls_back_the_whole_transaction(tmp_path, v3) -> None:
    """마지막 표 INSERT 에서 실패 — 앞 6표의 DELETE·INSERT 도 되돌아가 본 파일 바이트가 그대로다."""
    _home(tmp_path)
    backup = _backup(v3, tmp_path)
    _cutover_writes(v3)
    con = sqlite3.connect(str(v3))
    con.execute(f"CREATE TRIGGER boom BEFORE INSERT ON {DEFAULT_TABLES[-1]} "
                "BEGIN SELECT RAISE(ABORT, '시험 실패'); END")
    con.commit()
    con.close()
    before = _sha(v3)
    r = _restore_sh(tmp_path, backup, v3)
    assert r.rc == 2, r.out
    assert "시험 실패" in r.out
    assert _sha(v3) == before
    assert _q(v3, "SELECT count(*) FROM _compat_meta WHERE basis = 'restore'") == [(0,)]


def test_restore_refuses_a_tampered_backup(tmp_path, v3) -> None:
    """sha 를 먼저 본다 — 백업 바이트가 SHA256SUMS 와 다르면 본 파일을 열지도 않는다."""
    backup = _backup(v3, tmp_path)
    _cutover_writes(v3)
    backup.chmod(0o644)
    con = sqlite3.connect(str(backup))
    con.execute("DELETE FROM daily_prices")
    con.commit()
    con.close()
    before = _sha(v3)
    for dry in (True, False):
        with pytest.raises(CompatError, match="sha256"):
            v3_restore.restore(backup, v3, dry_run=dry)
    assert _sha(v3) == before


def test_restore_refuses_a_backup_without_a_sums_line(tmp_path, v3) -> None:
    backup = _backup(v3, tmp_path)
    other = tmp_path / "loose" / "quant.db"
    other.parent.mkdir()
    shutil.copyfile(backup, other)
    with pytest.raises(CompatError, match="SHA256SUMS"):
        v3_restore.restore(other, v3, dry_run=True)
    (other.parent / "SHA256SUMS").write_text(f"{'0' * 64}  something_else.db\n", encoding="utf-8")
    with pytest.raises(CompatError, match="SHA256SUMS"):
        v3_restore.restore(other, v3, dry_run=True)


def test_restore_accepts_sha256sum_binary_marker(tmp_path, v3) -> None:
    """`sha256sum -b` 형식(`<sha> *<이름>`)도 읽는다 — QL-H 날짜별 사본 폴더 같은 다른 도구의 SHA256SUMS."""
    src = _backup(v3, tmp_path)
    other = tmp_path / "daily" / "quant_20261016.db"
    other.parent.mkdir()
    shutil.copyfile(src, other)
    (other.parent / "SHA256SUMS").write_text(f"{_sha(other)} *{other.name}\n", encoding="utf-8")
    report = v3_restore.restore(other, v3, dry_run=True)
    assert report.sha256 == _sha(other)


@pytest.mark.parametrize("tables", [("market_indices",), ("_compat_meta",), ("daily_prices", "x")])
def test_restore_refuses_tables_outside_the_nine(tmp_path, v3, tables) -> None:
    """9표 밖(v3 가 계속 쓰는 표 · 반영 기록)은 고를 수 없다."""
    backup = _backup(v3, tmp_path)
    _cutover_writes(v3)
    before = _sha(v3)
    with pytest.raises(CompatError, match="9표"):
        v3_restore.restore(backup, v3, tables=tables)
    assert _sha(v3) == before


def test_restore_cli_tables_subset_only(tmp_path, v3, capsys) -> None:
    """--tables 로 고른 표만 되돌린다(창 밖 되돌리기에서 가격·수급을 남기는 선택지). 점수 표는 --with-scores 와 함께."""
    backup = _backup(v3, tmp_path)
    _cutover_writes(v3)
    prices = _dump(v3, "daily_prices")
    assert cli_main(["restore", "--backup", str(backup), "--v3-db", str(v3),
                     "--tables", "score_history,score_history_v2", "--with-scores"]) == 0
    assert "2표" in capsys.readouterr().out
    assert _dump(v3, "score_history") == _dump(backup, "score_history")
    assert _dump(v3, "score_history_v2") == _dump(backup, "score_history_v2")
    assert _dump(v3, "daily_prices") == prices
    rec = _q(v3, "SELECT tables FROM _compat_meta WHERE basis = 'restore'")
    assert sorted(json.loads(rec[0][0])) == ["score_history", "score_history_v2"]


def test_restore_refuses_a_column_mismatch(tmp_path, v3) -> None:
    """v3 가 백업 뒤 마이그레이션으로 열을 바꿨으면 멈춘다 — 열을 맞춰 넣으면 새 열 값이 조용히 사라진다."""
    backup = _backup(v3, tmp_path)
    con = sqlite3.connect(str(v3))
    con.execute("ALTER TABLE stocks ADD COLUMN new_col TEXT")
    con.commit()
    con.close()
    before = _sha(v3)
    with pytest.raises(CompatError, match="열"):
        v3_restore.restore(backup, v3, dry_run=True)
    assert _sha(v3) == before


@pytest.mark.parametrize("how", ["same", "symlink"])
def test_restore_refuses_backup_on_the_main_path(tmp_path, v3, how) -> None:
    db = v3
    if how == "symlink":
        db = tmp_path / "link.db"
        db.symlink_to(v3)
    with pytest.raises(CompatError, match="같은 파일"):
        v3_restore.restore(db, v3, dry_run=True)
