"""빌드 입력 동결 (§2) — 원장 SQLite 를 `VACUUM INTO` 로 스냅샷 사본으로 뜬다.

GC 도 여기 있다 (플랜 v1 Task 4.2 — 스냅샷 정리는 `gc.sh` 가 아니라 빌드에 내장한다. 한 곳에서만).
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from collections.abc import Collection
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

from . import manifest

SNAPSHOT_PREFIX = "snap_"
# 스냅샷 id = 만든 시각(UTC). 만드는 쪽(`make_snapshot`)과 날짜로 읽는 쪽(`snapshot_kst_date`)의
# 정본이다.
SNAPSHOT_ID_FORMAT = "snap_%Y%m%dT%H%M%SZ"
_KST = timezone(timedelta(hours=9))
KEEP_DEFAULT = 3      # 판당 ≈16 GB(09-14 실측). 하루 2판이면 1.5거래일 — 결정 9(09-14 사용자): 디스크 우선


@dataclass(frozen=True)
class SnapshotFile:
    path: Path
    bytes: int
    src_mtime: float       # 원장 파일 mtime (epoch) — _meta.json src_mtime


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    dir: Path
    files: dict[str, SnapshotFile]
    created_utc: str


def make_snapshot(raw: dict[str, Path], snap_root: Path,
                  snapshot_id: str | None = None) -> Snapshot:
    """raw = {db 이름: 원장 경로}. 스냅샷 디렉토리 `snap_root/<snapshot_id>/<db>.db` 를 만든다.

    원장은 읽기 전용 URI 로 열고 VACUUM INTO 만 실행한다 (원장 무변경).
    """
    sid = snapshot_id or datetime.now(UTC).strftime(SNAPSHOT_ID_FORMAT)
    d = snap_root / sid
    d.mkdir(parents=True, exist_ok=False)
    files: dict[str, SnapshotFile] = {}
    for db, src in raw.items():
        if not src.exists():
            raise FileNotFoundError(f"ledger not found: db={db} path={src}")
        dst = d / f"{db}.db"
        con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        try:
            con.execute(f"VACUUM INTO '{str(dst).replace(chr(39), chr(39) * 2)}'")
        finally:
            con.close()
        files[db] = SnapshotFile(path=dst, bytes=dst.stat().st_size,
                                 src_mtime=os.stat(src).st_mtime)
    snap = Snapshot(sid, d, files, datetime.now(UTC).isoformat(timespec="seconds"))
    (d / "snapshot.json").write_text(
        json.dumps({"snapshot_id": sid, "created_utc": snap.created_utc,
                    "files": {k: asdict(v) for k, v in files.items()}},
                   ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return snap


def snapshot_kst_date(snapshot_id: str) -> date:
    """스냅샷 id 시각(UTC)의 KST 날짜 — equity EG13 이 공개일 상한으로 쓴다(K1-3a · 컷오버 T-12).

    아침 체인 스냅샷(06:00 KST)은 UTC 로 전날이라 id 의 날짜 숫자를 그대로 쓰면 하루 이르다.
    형식 밖(`--snapshot-id` 로 손으로 붙인 이름 등)이면 ValueError — 기준일을 추정하지 않는다.
    """
    try:
        at = datetime.strptime(snapshot_id, SNAPSHOT_ID_FORMAT)
    except ValueError as e:
        raise ValueError(f"snapshot id outside format {SNAPSHOT_ID_FORMAT!r} (UTC): "
                         f"got={snapshot_id!r}") from e
    if at.strftime(SNAPSHOT_ID_FORMAT) != snapshot_id:     # strptime 은 자릿수가 모자라도 받아 준다
        raise ValueError(f"snapshot id outside format {SNAPSHOT_ID_FORMAT!r} (UTC): "
                         f"got={snapshot_id!r}")
    return at.replace(tzinfo=UTC).astimezone(_KST).date()


def load_snapshot(snap_dir: Path) -> Snapshot:
    meta = json.loads((snap_dir / "snapshot.json").read_text(encoding="utf-8"))
    files = {k: SnapshotFile(Path(v["path"]), int(v["bytes"]), float(v["src_mtime"]))
             for k, v in meta["files"].items()}
    return Snapshot(meta["snapshot_id"], snap_dir, files, meta["created_utc"])


@dataclass(frozen=True)
class GcResult:
    """GC 1회 결과 — 무엇을 지웠고 얼마를 확보했는가."""

    deleted: tuple[str, ...]
    kept: tuple[str, ...]
    freed_bytes: int

    @property
    def freed_gb(self) -> float:
        return self.freed_bytes / 1e9

    def summary(self) -> str:
        return (f"snapshot gc: 삭제 {len(self.deleted)}세트 {self.freed_gb:.2f}GB 확보 · "
                f"잔존 {len(self.kept)}세트"
                + (f" (지운 판 {', '.join(self.deleted)})" if self.deleted else ""))


def current_snapshot_ids(stage_root: Path) -> set[str]:
    """stage 표들의 `current_build` 가 서 있는 스냅샷 id 집합 — GC 보호 세트의 정본.

    이 판을 지우면 "같은 스냅샷으로 재빌드해 content_hash 를 대조" 하는 재현성 검증이 끊긴다.
    """
    out: set[str] = set()
    for path in sorted(stage_root.glob("*/MANIFEST.json")):
        m = manifest.load(path)
        for b in m.builds:
            if b.build_id == m.current_build:
                out.add(b.snapshot_id)
    return out


def _dir_bytes(d: Path) -> int:
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())


def gc(snap_root: Path, keep: int = KEEP_DEFAULT,
       protect: Collection[str] = ()) -> GcResult:
    """보호 세트 + mtime 최신 `keep` 세트를 남기고 나머지 `snap_*` 디렉토리를 지운다.

    보호 세트는 `keep` 과 **별개로 더해진다** — 현재 판이 선 스냅샷은 몇 번째로 오래됐든 남는다.
    `snap_` 으로 시작하지 않는 항목(사람이 둔 디렉토리·파일)은 건드리지 않는다.
    """
    if keep < 0:
        raise ValueError(f"snapshot gc keep must be >= 0: keep={keep} snap_root={snap_root}")
    if not snap_root.is_dir():
        return GcResult((), (), 0)
    sets = sorted((d for d in snap_root.iterdir()
                   if d.is_dir() and d.name.startswith(SNAPSHOT_PREFIX)),
                  key=lambda d: d.stat().st_mtime, reverse=True)
    protected = set(protect)
    keep_names = {d.name for d in sets[:keep]} | protected
    deleted: list[str] = []
    kept: list[str] = []
    freed = 0
    for d in sets:
        if d.name in keep_names:
            kept.append(d.name)
            continue
        freed += _dir_bytes(d)
        shutil.rmtree(d)
        deleted.append(d.name)
    return GcResult(tuple(deleted), tuple(kept), freed)
