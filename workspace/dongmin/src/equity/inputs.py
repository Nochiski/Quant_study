"""stage 입력 해석·고정 (EQUITY_WORKFLOW §1 · DESIGN §2 [결정 2]).

읽기 계약: `<stage_root>/<table>/MANIFEST.json` 의 `current_build` → 그 BuildRecord 의
`partitions[].path` 만 쓴다. 구버전 `v=…` 디렉토리가 keep=3 으로 공존하므로 디렉토리 glob 은
금지다(STAGE_HANDOFF §1).

고정: 고른 build 의 파티션 파일을 `<equity_root>/_pinned/<table>/v=<build>/` 로 하드링크하고
BuildRecord 1건만 담은 MANIFEST 를 그 옆에 원자 기록한다 — stage 의 keep=3 GC
(`manifest.commit()` 의 rmtree)가 입력을 지워도 빌드가 재현된다.
`_pinned/` 에는 `manifest.commit()` 을 부르지 않는다(그 함수가 keep 밖 `v=` 를 rmtree 한다).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb
from stage import manifest


@dataclass(frozen=True)
class PinnedBuild:
    """고정한 stage 빌드 1개. 경로는 전부 MANIFEST 의 partitions[].path 에서 나온다."""

    table: str
    build_id: str
    root: Path                          # v=<build_id> 디렉토리
    partition_paths: tuple[Path, ...]   # partitions[].path 를 절대경로로 푼 것
    meta: dict[str, object]             # 첫 파티션 _meta.json (gates·lag_known·coverage_from)

    @property
    def globs(self) -> tuple[str, ...]:
        return tuple(str(p / "*.parquet") for p in self.partition_paths)

    @property
    def has_year_axis(self) -> bool:
        return any(p.name.startswith("year=") for p in self.partition_paths)


# `_pinned/<table>/v=<build_id>/` 경로에서 hive_partitioning 이 뽑아내는 축. stage 컬럼이 아니므로
# 뷰에 노출하지 않는다 — 노출하면 같은 이름의 stage 컬럼을 build_id 문자열로 덮어쓴다.
_PIN_AXIS_COLUMN = "v"


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _record(m: manifest.Manifest, table: str, path: Path) -> manifest.BuildRecord:
    if m.current_build is None:
        raise FileNotFoundError(
            f"stage table has no committed build — build it first: table={table} manifest={path}")
    for b in m.builds:
        if b.build_id == m.current_build:
            return b
    raise FileNotFoundError(
        f"MANIFEST.current_build is not in builds[]: table={table} manifest={path} "
        f"current_build={m.current_build} builds={[b.build_id for b in m.builds]}")


def _load_meta(partition_paths: tuple[Path, ...]) -> dict[str, object]:
    """첫 파티션의 `_meta.json`. build.py 가 전 파티션에 같은 gates 를 쓰므로 하나면 충분하다."""
    for p in partition_paths:
        f = p / "_meta.json"
        if f.exists():
            raw = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
    return {}


def _build(table_root: Path, table: str, rec: manifest.BuildRecord) -> PinnedBuild:
    paths = tuple(table_root / str(p["path"]) for p in rec.partitions)
    return PinnedBuild(table, rec.build_id, table_root / f"v={rec.build_id}", paths,
                       _load_meta(paths))


def resolve(stage_root: Path, table: str) -> PinnedBuild:
    """stage 테이블의 current_build 를 MANIFEST 경유로 푼다. 커밋된 빌드가 없으면 예외."""
    table_root = stage_root / table
    path = table_root / "MANIFEST.json"
    return _build(table_root, table, _record(manifest.load(path), table, path))


def load_pinned(equity_root: Path, table: str, build_id: str) -> PinnedBuild:
    """이미 `_pinned/` 에 고정된 빌드를 다시 연다 (재판정·재현 빌드용)."""
    table_root = equity_root / "_pinned" / table
    path = table_root / "MANIFEST.json"
    rec = next((b for b in manifest.load(path).builds if b.build_id == build_id), None)
    if rec is None:
        raise FileNotFoundError(f"pinned build not found — table={table} build_id={build_id} "
                                f"manifest={path}")
    return _build(table_root, table, rec)


def pin(stage_root: Path, equity_root: Path, table: str) -> PinnedBuild:
    """current_build 를 `_pinned/` 에 하드링크로 고정한다. 같은 build 재고정은 no-op.

    다른 파일시스템이면 `os.link` 의 OSError(EXDEV)를 그대로 올린다 — 조용한 복사로 대체하지
    않는다(DESIGN §10 P9 가 stage 와 equity 가 같은 디바이스임을 실측했다).
    """
    src = resolve(stage_root, table)
    dst_table_root = equity_root / "_pinned" / table
    dst_root = dst_table_root / f"v={src.build_id}"
    src_root = stage_root / table / f"v={src.build_id}"
    dst_paths: list[Path] = []
    for src_dir in src.partition_paths:
        out = dst_root / src_dir.relative_to(src_root)
        dst_paths.append(out)
        if out.exists():
            continue                     # 이미 고정됨 — 재링크하지 않는다
        out.mkdir(parents=True)
        for f in sorted(src_dir.iterdir()):
            if f.is_file():              # `_reject/` 는 입력이 아니다 → 디렉토리는 건너뛴다
                os.link(f, out / f.name)
    _write_build_record(dst_table_root, table, manifest.load(
        stage_root / table / "MANIFEST.json"), src.build_id)
    parts = tuple(dst_paths)
    return PinnedBuild(table, src.build_id, dst_root, parts, _load_meta(parts))


def _write_build_record(dst_table_root: Path, table: str, m: manifest.Manifest,
                        build_id: str) -> None:
    """`_pinned/<table>/MANIFEST.json` 에 해당 BuildRecord 1건만 원자 기록한다.

    `manifest.commit()` 을 쓰면 안 된다 — keep 밖 `v=` 를 rmtree 해서 고정한 하드링크를 지운다.
    `manifest._write_atomic` 은 private 이라 같은 규약(임시 파일 → os.replace)을 여기서 쓴다.
    """
    rec = next((b for b in m.builds if b.build_id == build_id), None)
    if rec is None:
        raise FileNotFoundError(f"build record disappeared while pinning: table={table} "
                                f"build_id={build_id} builds={[b.build_id for b in m.builds]}")
    dst_table_root.mkdir(parents=True, exist_ok=True)
    pinned = manifest.Manifest(table=table, current_build=build_id, keep=1, builds=[rec])
    path = dst_table_root / "MANIFEST.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(pinned), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def create_views(con: duckdb.DuckDBPyConnection, pinned: dict[str, PinnedBuild],
                 declared: dict[str, list[str]]) -> None:
    """stage 테이블마다 선언 컬럼만 투영한 TEMP VIEW 를 stage 실명으로 만든다.

    선언에 없는 컬럼은 투영하지 않는다. 선언했는데 parquet 에 없는 컬럼도 투영하지 않고
    빠뜨린다 — 뷰 생성이 Binder 오류로 죽는 대신 EG0 가 `declared_columns_missing` 으로 잡게
    하기 위해서다. `hive_partitioning=true` 는 경로의 `year=` 를 컬럼으로 붙이므로(HANDOFF §1)
    그 축은 선언과 무관하게 노출해 `.sql` 이 연도 프루닝 술어로 쓴다. 같은 이유로 붙는
    `v=<build_id>` 축(`v` 컬럼)은 stage 컬럼이 아니므로 뷰에서 뺀다.
    """
    for table, pb in sorted(pinned.items()):
        globs = ", ".join(f"'{g}'" for g in pb.globs)
        src = (f"read_parquet([{globs}], hive_partitioning=true, union_by_name=true)")
        described = [str(r[0]) for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()]
        actual = [c for c in described if c != _PIN_AXIS_COLUMN]
        want = declared.get(table) or actual
        sel = [c for c in want if c in actual]
        if "year" in actual and "year" not in sel:
            sel.append("year")
        cols = ", ".join(_q(c) for c in sel) if sel else "NULL AS _no_declared_column"
        con.execute(f"CREATE OR REPLACE TEMP VIEW {_q(table)} AS SELECT {cols} FROM {src}")


def declared_columns_missing(con: duckdb.DuckDBPyConnection, table: str,
                             cols: list[str]) -> list[str]:
    """EG0-P02 — 선언 컬럼 중 그 뷰에 없는 것. stage 컬럼명 기준이다."""
    actual = {str(r[0]) for r in con.execute(f"DESCRIBE {_q(table)}").fetchall()}
    actual.discard(_PIN_AXIS_COLUMN)
    return [c for c in cols if c not in actual]
