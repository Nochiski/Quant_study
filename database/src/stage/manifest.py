"""MANIFEST.json — 테이블당 1개. 포인터 1회 원자 교체 + 구버전 GC (§2).

os.replace 는 파일에만 원자적이므로 디렉토리가 아니라 MANIFEST.json 을 바꾼다.

equity 층이 이 모듈의 `BuildRecord`·`load`·`commit`(그리고 `gates.GateResult`·`GateStatus`,
`baseline.write`)을 import 한다 — 시그니처를 바꾸면 equity 세션에 통지한다 (2026-09-05 합의).
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

KEEP_DEFAULT = 3


@dataclass(frozen=True)
class BuildRecord:
    build_id: str
    snapshot_id: str
    rules_version: str
    built_at_utc: str
    n_rows: int
    content_hash: str
    partitions: list[dict[str, object]] = field(default_factory=list)
    gates: list[dict[str, object]] = field(default_factory=list)
    # equity 층 전용: 입력 stage 테이블 → 고정한 build_id. stage 빌드는 빈 dict (EQUITY_WORKFLOW §1)
    inputs: dict[str, str] = field(default_factory=dict)


@dataclass
class Manifest:
    table: str
    current_build: str | None = None
    keep: int = KEEP_DEFAULT
    builds: list[BuildRecord] = field(default_factory=list)


def load(path: Path) -> Manifest:
    if not path.exists():
        return Manifest(table=path.parent.name)
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Manifest(table=raw["table"], current_build=raw.get("current_build"),
                    keep=int(raw.get("keep", KEEP_DEFAULT)),
                    builds=[BuildRecord(**b) for b in raw.get("builds", [])])


def _write_atomic(path: Path, m: Manifest) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(m), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)   # 파일 교체 — 여기가 유일한 포인터 전환 지점


def commit(table_root: Path, record: BuildRecord, keep: int = KEEP_DEFAULT) -> Manifest:
    """`v=<build_id>` 디렉토리가 완성된 뒤 호출. 포인터 전환 후 keep 개 밖의 구버전을 지운다."""
    path = table_root / "MANIFEST.json"
    m = load(path)
    m.table = table_root.name
    m.keep = keep
    m.builds = [b for b in m.builds if b.build_id != record.build_id] + [record]
    m.current_build = record.build_id
    stale = m.builds[:-keep] if len(m.builds) > keep else []
    m.builds = m.builds[-keep:]
    _write_atomic(path, m)
    for b in stale:
        d = table_root / f"v={b.build_id}"
        if d.exists():
            shutil.rmtree(d)
    return m
