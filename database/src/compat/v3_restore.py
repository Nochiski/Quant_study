"""v3 `quant.db` 고정 백업 2벌 · 표 단위 복원 — 컷오버 되돌리기(컷오버 트랙 QL-I).

정본 `docs/plans/2026-10-10-cutover-track.md` §3 P4 QL-I · T-21 · §4(되돌리기 창 5거래일), 절차서
`docs/CUTOVER_ROLLBACK.md`. `scripts/v3_backup.sh`(`python -m compat backup`)와 `scripts/v3_restore.sh`
(`python -m compat restore`)가 부른다. 서버 밖 백업은 하지 않는다(N-39 보류).

백업(`backup`) — 컷오버 날 V3-A~E 전에 한 번:
  · v3 quant.db 를 sqlite 온라인 백업(`compat stage` 와 같은 API — 한 번에 전 페이지라 읽는 동안 한 시점의 사본)으로
    첫 경로에 `.partial` 로 뜬다 → rollback journal 모드로 바꾼다(0444 사본을 읽기 전용으로 열 때 -wal/-shm 을
    만들지 않게 — 본 파일이 WAL 이면 사본 머리도 WAL 이다) → `integrity_check` ok → 이름을 바꾼다.
    둘째 경로에는 그 파일을 바이트 복사하고 sha256 이 같아야 한다(두 벌이 같은 시점).
  · V3-A~E 가 바꿀 파일(`files` — 셸이 COMPAT_LAYER §8-1 목록과 `crontab -l` 을 넘긴다)을 `<원래 이름>.bak.<stamp>`
    로 두 경로에 복사한다.
  · 이름은 `quant_<stamp>.db` · `<원래 이름>.bak.<stamp>` 이다. 어느 경로에든 같은 이름(·SHA256SUMS 줄)이 있으면
    시작 전에 멈춘다 — 덮어쓰지 않는다.
  · 디스크 여유: 파일시스템마다 (본 파일 + -wal + 사본 파일) 크기 × `DISK_FACTOR`(2, 스테이징과 같은 규칙) × 그
    파일시스템에 뜨는 벌 수.
  · 전부 뜬 뒤에 권한 0444, 각 경로의 SHA256SUMS(`sha256sum -c` 형식)에 덧붙인다. 복원은 SHA256SUMS 에 줄이 없는
    파일을 받지 않으므로 중간에 멈춘 벌은 쓰이지 않는다. 실패하면 이번에 만든 파일을 지운다.
  · v3 락은 잡지 않는다 — 온라인 백업은 한 시점의 일관된 사본이고, 락(`flock -n` 인 v3 체인)을 쥐면 그 시각 v3
    체인이 조용히 건너뛰어진다. 절차서는 v3 체인이 돌지 않는 때 뜨게 한다.

복원(`restore`) — 되돌릴 때:
  · 백업이 v3 본 파일(링크·사이드카 포함)과 같은 파일이면 거부한다.
  · 표는 compat 9표(`v3_post.TABLES`) 안에서만 고른다(기본 9표 전부). 9표 밖 — v3 가 계속 쓰는 `market_*`·
    `pipeline_runs`·`research_reports` 등(T-27)과 반영 기록 `_compat_meta` — 은 고를 수 없다.
  · 백업 sha256 을 그 폴더 SHA256SUMS 와 먼저 대조한다(줄이 없거나 다르면 멈춤 — 본 파일을 열지 않는다).
  · 표마다 백업과 본 파일의 열 이름 집합이 같아야 한다(백업 뒤 v3 마이그레이션이 열을 바꿨으면 멈춘다).
  · 한 트랜잭션: 본 파일 `mode=rw` · 백업 ATTACH `mode=ro` → BEGIN IMMEDIATE → 표마다 전체 DELETE → 백업 INSERT
    (열 이름으로) → `_compat_meta` 복원 기록 1행 → COMMIT. 중간에 실패하면 ROLLBACK 이라 본 파일은 그대로다
    (`v3_post._move` 와 같은 원자성).
  · `dry_run`: 위 검증 뒤 표별 행 수(본 파일 → 백업)만 내고 쓰지 않는다.
  · 락(v3 체인과 같은 `/tmp/kael_v3_daily_all.lock`)은 셸이 잡는다.

복원 기록(`_compat_meta` 1행): basis = `v3_post.RESTORE_BASIS` · status ok · exported_at = 복원 시각(UTC ISO —
  compat 기록과 같은 형식이라 앞뒤를 문자열로 가른다) · date = 복원한 KST 날짜 · tables = 표 → {n_before, n_rows} ·
  window = {backup, sha256}(이 기록만 창 대신 원천 백업을 싣는다) · 판 열(equity·stage)은 빈 객체, consensus_asof
  는 빈 문자열. `v3_post` 는 이 기록을 장벽으로 본다 — 그 앞 반영 기록은 순서(T-35)·아침 반영 표(T-34) 판정에서
  빠지고, 그 뒤 첫 제자리 반영은 `--full` 만 받는다(`v3_post` 머리 주석).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from .quant_db import META_DDL, META_TABLE, CompatError, _ensure_meta_columns
from .v3_post import (
    _SIDECARS,
    BUSY_TIMEOUT_MS,
    DISK_FACTOR,
    RESTORE_BASIS,
    TABLES,
    _require,
    _ro,
    _same_file,
)

SUMS = "SHA256SUMS"
_KST = timezone(timedelta(hours=9))
_READ_CHUNK = 1 << 20


@dataclass(frozen=True)
class BackupResult:
    """백업 2벌 — 두 경로에 같은 이름·같은 sha 로 남았다."""

    stamp: str
    db_name: str                                  # quant_<stamp>.db
    dests: tuple[Path, ...]
    sha256: dict[str, str] = field(default_factory=dict)   # 백업 이름 → sha256(DB·사본 파일)

    def lines(self) -> list[str]:
        out = [f"compat backup {d}: {n} {self.sha256[n][:12]}…" for d in self.dests
               for n in self.sha256]
        out.append(f"compat backup 완료 — {len(self.dests)}벌 × {len(self.sha256)}파일, "
                   f"{self.db_name} sha256 {self.sha256[self.db_name]}")
        return out


@dataclass(frozen=True)
class RestoreReport:
    """복원 결과(또는 계획). counts = 표 → (본 파일 행 수, 백업 행 수) — 복원이면 트랜잭션 안에서 센 값."""

    backup: str
    sha256: str
    dry_run: bool
    counts: dict[str, tuple[int, int]]
    exported_at: str | None = None                # 복원 기록 — dry_run 이면 None

    def lines(self) -> list[str]:
        head = "restore 계획" if self.dry_run else "restore"
        out = [f"compat {head} {t}: 본 파일 {a}행 → 백업 {b}행 ({b - a:+d})"
               for t, (a, b) in self.counts.items()]
        if self.dry_run:
            out.append(f"compat restore 계획(--dry-run) — {len(self.counts)}표, 쓰지 않았다 · 백업 "
                       f"{self.backup} sha256 {self.sha256} 확인")
        else:
            out.append(f"compat restore 완료 — {len(self.counts)}표 한 트랜잭션 ← {self.backup} "
                       f"(sha256 {self.sha256}) · 복원 기록 exported_at={self.exported_at}")
        return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_READ_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_sums(folder: Path) -> dict[str, str]:
    """폴더 SHA256SUMS → {이름: sha256}. `sha256sum` 형식(`<sha>  <이름>`, `-b` 의 `<sha> *<이름>`)을 읽는다.
    같은 이름이 다른 sha 로 두 번 있으면 모호하므로 멈춘다(P1)."""
    path = folder / SUMS
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        sha, _, rest = line.partition(" ")
        name = rest[1:] if rest[:1] in (" ", "*") else rest
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha) or not name:
            raise CompatError(f"{path}:{n} 형식 밖 줄 — '<sha256>  <이름>' 이어야 한다: {line!r}")
        if out.get(name, sha) != sha:
            raise CompatError(f"{path}: {name} 이 서로 다른 sha256 으로 두 번 있다 — 어느 쪽인지 모른다")
        out[name] = sha
    return out


def _append_sums(folder: Path, sums: dict[str, str]) -> None:
    path = folder / SUMS
    if path.exists():
        path.chmod(0o644)
    with open(path, "a", encoding="utf-8") as f:
        for name, sha in sums.items():
            f.write(f"{sha}  {name}\n")
        f.flush()
        os.fsync(f.fileno())
    path.chmod(0o444)


def _copy(src: Path, dst: Path, made: list[Path]) -> str:
    """src → dst.partial → dst(이름 바꾸기). sha256 을 돌려준다."""
    part = Path(f"{dst}.partial")
    made.append(part)
    shutil.copyfile(src, part)
    os.replace(part, dst)
    made.append(dst)
    return _sha256(dst)


def backup(v3_db: Path, dests: Sequence[Path], stamp: str,
           files: Iterable[Path] = ()) -> BackupResult:
    """v3 quant.db 온라인 백업 + V3-A~E 대상 파일 사본을 서로 다른 두 경로에(모듈 머리 주석)."""
    v3_db = Path(v3_db)
    _require(v3_db, "v3 quant.db")
    dests = tuple(Path(d) for d in dests)
    if len(dests) != 2:
        raise CompatError(f"백업 경로는 둘이어야 한다(T-21 — 서버 안 서로 다른 경로 2벌), 받은 {len(dests)}개")
    for d in dests:
        d.mkdir(parents=True, exist_ok=True)
    if _same_file(dests[0], dests[1]):
        raise CompatError(f"두 백업 경로가 같은 폴더다({dests[0]} = {dests[1]}) — 서로 다른 경로 2벌이어야 "
                          "한다(T-21)")
    db_name = f"quant_{stamp}.db"
    sources: dict[str, Path] = {}
    for f in (Path(x) for x in files):
        _require(f, "사본 대상 파일")
        name = f"{f.name}.bak.{stamp}"
        if name in sources or name == db_name:
            raise CompatError(f"사본 이름이 겹친다: {name}({sources.get(name)} · {f})")
        sources[name] = f
    names = [db_name, *sources]
    for d in dests:
        have = _read_sums(d)
        for name in names:
            if (d / name).exists() or Path(f"{d / name}.partial").exists() or name in have:
                raise CompatError(f"{d / name} 이 이미 있다(또는 SHA256SUMS 에 줄이 있다) — 덮어쓰지 않는다")
        for a in _SIDECARS:
            if _same_file(d / db_name, Path(f"{v3_db}{a}")):
                raise CompatError(f"백업 경로가 v3 본 파일과 같은 파일이다: {d / db_name}")
    wal = Path(f"{v3_db}-wal")
    size = (v3_db.stat().st_size + (wal.stat().st_size if wal.exists() else 0)
            + sum(p.stat().st_size for p in sources.values()))
    by_dev: dict[int, list[Path]] = {}
    for d in dests:
        by_dev.setdefault(d.stat().st_dev, []).append(d)
    for ds in by_dev.values():
        need = size * DISK_FACTOR * len(ds)
        free = shutil.disk_usage(ds[0]).free
        if free < need:
            raise CompatError(f"디스크 여유 부족: {ds[0]} 여유 {free:,} bytes < 백업 크기 × {DISK_FACTOR} × "
                              f"{len(ds)}벌 = {need:,} bytes — 뜨지 않는다")

    made: list[Path] = []
    try:
        first = dests[0] / db_name
        part = Path(f"{first}.partial")
        made.append(part)
        src, dst = _ro(v3_db), sqlite3.connect(str(part))
        try:
            src.backup(dst)
            mode = dst.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            check = dst.execute("PRAGMA integrity_check").fetchall()
        finally:
            dst.close()
            src.close()
        if str(mode).lower() != "delete":
            raise CompatError(f"백업 사본을 rollback journal 로 바꾸지 못했다(journal_mode={mode})")
        if check != [("ok",)]:
            raise CompatError(f"백업 사본 integrity_check 실패: {check[:5]}")
        os.replace(part, first)
        made.append(first)
        sha = {db_name: _sha256(first)}
        for name, f in sources.items():
            sha[name] = _copy(f, dests[0] / name, made)
        for d in dests[1:]:
            for name in names:
                got = _copy(dests[0] / name, d / name, made)
                if got != sha[name]:
                    raise CompatError(f"둘째 벌 sha256 이 다르다: {d / name} {got} ≠ {sha[name]}")
        for d in dests:
            for name in names:
                (d / name).chmod(0o444)
        for d in dests:
            _append_sums(d, sha)
    except BaseException:
        for p in made:
            p.unlink(missing_ok=True)
        raise
    return BackupResult(stamp, db_name, dests, sha)


def verify_backup(path: Path) -> str:
    """백업 sha256 을 그 폴더 SHA256SUMS 와 대조 — 맞으면 sha256, 줄이 없거나 다르면 `CompatError`."""
    path = Path(path)
    _require(path, "백업")
    want = _read_sums(path.parent).get(path.name)
    if want is None:
        raise CompatError(f"{path.parent / SUMS} 에 {path.name} 줄이 없다 — 검증할 수 없는 백업은 받지 않는다")
    got = _sha256(path)
    if got != want:
        raise CompatError(f"백업 sha256 불일치: {path} {got} ≠ SHA256SUMS {want} — 손상·변조된 백업이다")
    return got


def write_record(con: sqlite3.Connection, exported_at: str, date_iso: str, backup_path: Path,
                 sha256: str, tables: dict[str, dict[str, int]]) -> None:
    """복원 기록 1행 — 열린 트랜잭션 안에서 부른다(모듈 머리 주석). `v3_post` 가 장벽으로 본다."""
    con.execute(META_DDL)
    _ensure_meta_columns(con)
    con.execute(
        f'INSERT INTO main.{META_TABLE} (exported_at, date, basis, equity_builds, stage_builds, '
        f'tables, "window", consensus_asof, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (exported_at, date_iso, RESTORE_BASIS, "{}", "{}",
         json.dumps(tables, ensure_ascii=False, sort_keys=True),
         json.dumps({"backup": str(backup_path), "sha256": sha256}, ensure_ascii=False,
                    sort_keys=True),
         "", "ok"))


def _columns(con: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [r[1] for r in con.execute(f'PRAGMA {schema}.table_info("{table}")')]


def restore(backup_path: Path, v3_db: Path, tables: Sequence[str] = TABLES,
            dry_run: bool = False) -> RestoreReport:
    """백업의 표들을 v3 본 파일로 한 트랜잭션에 되돌린다(모듈 머리 주석). 락은 부르는 쪽이 쥔다."""
    backup_path, v3_db = Path(backup_path), Path(v3_db)
    _require(backup_path, "백업")
    _require(v3_db, "v3 quant.db")
    for a in _SIDECARS:
        for b in _SIDECARS:
            if _same_file(Path(f"{backup_path}{a}"), Path(f"{v3_db}{b}")):
                raise CompatError(f"백업이 v3 본 파일과 같은 파일이다: {backup_path}{a} = {v3_db}{b}")
    tables = tuple(tables)
    bad = sorted({t for t in tables if t not in TABLES})
    if not tables or bad or len(set(tables)) != len(tables):
        raise CompatError(f"복원 표는 compat 9표({','.join(TABLES)}) 안에서 겹치지 않게 고른다 — 받은 "
                          f"{','.join(tables) or '(없음)'}"
                          + (f", 9표 밖 {bad}" if bad else ""))
    sha = verify_backup(backup_path)

    con = sqlite3.connect(f"{v3_db.resolve().as_uri()}?mode={'ro' if dry_run else 'rw'}", uri=True,
                          isolation_level=None)
    try:
        con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        con.execute("ATTACH DATABASE ? AS bak", (f"{backup_path.resolve().as_uri()}?mode=ro",))
        cols: dict[str, list[str]] = {}
        for t in tables:
            have, want = _columns(con, "main", t), _columns(con, "bak", t)
            if not want:
                raise CompatError(f"{t}: 백업에 표가 없다")
            if sorted(have) != sorted(want):
                raise CompatError(f"{t}: 본 파일과 백업의 열이 다르다(본 파일 {have} · 백업 {want}) — 백업 뒤 "
                                  "v3 스키마가 바뀌었다. 맞춰 넣으면 새 열 값이 조용히 사라지므로 멈춘다")
            cols[t] = want

        def count(schema: str, t: str) -> int:
            return int(con.execute(f'SELECT count(*) FROM {schema}."{t}"').fetchone()[0])

        if dry_run:
            return RestoreReport(str(backup_path), sha, True,
                                 {t: (count("main", t), count("bak", t)) for t in tables})
        exported_at = datetime.now(UTC).isoformat(timespec="microseconds")
        done: dict[str, dict[str, int]] = {}
        con.execute("BEGIN IMMEDIATE")
        try:
            for t in tables:
                names = ", ".join(f'"{c}"' for c in cols[t])
                n_before = count("main", t)
                con.execute(f'DELETE FROM main."{t}"')
                n_rows = con.execute(f'INSERT INTO main."{t}" ({names}) '
                                     f'SELECT {names} FROM bak."{t}"').rowcount
                done[t] = {"n_before": n_before, "n_rows": n_rows}
            write_record(con, exported_at, datetime.now(_KST).date().isoformat(),
                         backup_path.resolve(), sha, done)
            con.execute("COMMIT")
        except BaseException:
            # 원래 원인을 남긴다 — 트랜잭션이 이미 풀렸는데 ROLLBACK 하면 그 오류가 원인을 덮는다
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        return RestoreReport(str(backup_path), sha, False,
                             {t: (d["n_before"], d["n_rows"]) for t, d in done.items()}, exported_at)
    finally:
        con.close()
