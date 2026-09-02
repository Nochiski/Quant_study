"""빌드 입력 동결 (§2) — 원장 SQLite 를 `VACUUM INTO` 로 스냅샷 사본으로 뜬다."""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


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
    sid = snapshot_id or datetime.now(UTC).strftime("snap_%Y%m%dT%H%M%SZ")
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


def load_snapshot(snap_dir: Path) -> Snapshot:
    meta = json.loads((snap_dir / "snapshot.json").read_text(encoding="utf-8"))
    files = {k: SnapshotFile(Path(v["path"]), int(v["bytes"]), float(v["src_mtime"]))
             for k, v in meta["files"].items()}
    return Snapshot(meta["snapshot_id"], snap_dir, files, meta["created_utc"])
