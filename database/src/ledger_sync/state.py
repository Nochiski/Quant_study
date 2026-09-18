"""로컬 동기화 상태 `_sync/state.json` — 테이블별 어느 빌드를 어떤 파일로 갖고 있는지.

MANIFEST 는 서버 원문을 그대로 두므로 "로컬에 실제로 있는 파일" 은 여기서만 안다. 파일마다
`origin` 을 남긴다 — `downloaded`(서버 바이트 그대로) / `reused`(content_hash 가 같은 로컬
구판본에서 복사). `verify files` 가 reused 파일의 크기 대조를 건너뛰는 근거다.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .layout import SYNC_DIR

STATE_NAME = "state.json"
ORIGIN_DOWNLOADED = "downloaded"
ORIGIN_REUSED = "reused"
STATE_VERSION = 1


@dataclass(frozen=True)
class FileRecord:
    size: int
    origin: str


@dataclass(frozen=True)
class TableState:
    build_id: str
    files: dict[str, FileRecord]  # 테이블 루트 기준 상대경로(`v=<build>/year=2010/part0.parquet`)
    synced_at_utc: str

    def partition_files(self, partition_path: str) -> dict[str, FileRecord]:
        prefix = partition_path.rstrip("/") + "/"
        return {rel: rec for rel, rec in self.files.items()
                if rel.startswith(prefix) and "/" not in rel[len(prefix):]}


@dataclass
class SyncState:
    layer: str
    tables: dict[str, TableState] = field(default_factory=dict)
    updated_at_utc: str = ""


def state_path(layer_root: Path) -> Path:
    return layer_root / SYNC_DIR / STATE_NAME


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load_state(layer_root: Path, layer: str) -> SyncState:
    path = state_path(layer_root)
    if not path.exists():
        return SyncState(layer=layer)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"sync state unreadable — path={path} error={error!r}") from error
    if not isinstance(document, dict):
        raise RuntimeError(f"sync state must be a JSON object — path={path} "
                           f"got={type(document).__name__}")
    tables: dict[str, TableState] = {}
    for table, raw in (document.get("tables") or {}).items():
        files = {rel: FileRecord(int(rec["size"]), str(rec["origin"]))
                 for rel, rec in (raw.get("files") or {}).items()}
        tables[table] = TableState(str(raw["build_id"]), files, str(raw.get("synced_at_utc", "")))
    return SyncState(layer=str(document.get("layer", layer)), tables=tables,
                     updated_at_utc=str(document.get("updated_at_utc", "")))


def save_state(layer_root: Path, state: SyncState) -> Path:
    path = state_path(layer_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at_utc = now_utc()
    document = {
        "version": STATE_VERSION,
        "layer": state.layer,
        "updated_at_utc": state.updated_at_utc,
        "tables": {
            table: {
                "build_id": ts.build_id,
                "synced_at_utc": ts.synced_at_utc,
                "files": {rel: {"size": rec.size, "origin": rec.origin}
                          for rel, rec in sorted(ts.files.items())},
            }
            for table, ts in sorted(state.tables.items())
        },
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return path
