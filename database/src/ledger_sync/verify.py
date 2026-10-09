"""로컬 사본이 서버와 같은지 — 세 층위(manifest · files · hash).

- manifest: 원격 테이블 집합·current_build 와 로컬 state·MANIFEST 가 같다. 로컬 `_catalog_meta.json`
  이 있으면 그 snapshot_id 가 로컬 빌드 집합의 해시와 같아야 카탈로그가 stale 이 아니다.
- files: current_build 파티션 파일의 이름·크기가 원격과 같다. `reused` 파일은 크기 대조를 건너뛴다
  (바이트가 다를 수 있음 — hash 층위가 보증). MANIFEST 원문 바이트도 같아야 한다.
- hash: 파티션마다 duckdb 로 content_hash 를 다시 계산해 MANIFEST 와 대조한다. 서버 규칙
  (`stage/build.py:_content_hash`)은 tmp 경로에서 `hive_partitioning=true` 로 떠서 `year=YYYY` 만
  컬럼으로 붙는다. 로컬 경로에는 `v=<build>` 가 섞이므로 hive 를 끄고 꼬리 컬럼을 직접 덧붙인다.
  원격 접속이 필요 없다.

결과는 값(`VerifyReport`)이고 CLI 가 종료 코드로 바꾼다.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .hashing import PartitionHashError, compute_partition_hash
from .layout import (
    CATALOG_META_NAME,
    MANIFEST_NAME,
    SYNC_DIR,
    BuildInfo,
    is_table_name,
    snapshot_id,
)
from .plan import list_remote_tables, read_local_manifest, read_remote_manifest
from .remote import RemoteFS
from .state import ORIGIN_REUSED, STATE_NAME, SyncState


class Level(Enum):
    MANIFEST = "manifest"
    FILES = "files"
    HASH = "hash"


@dataclass(frozen=True)
class Finding:
    level: Level
    table: str
    detail: str


@dataclass
class VerifyReport:
    findings: list[Finding] = field(default_factory=list)
    checked: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)  # 검사할 수 없어 건너뛴 항목(사유별)

    @property
    def ok(self) -> bool:
        return not self.findings

    def add(self, level: Level, table: str, detail: str) -> None:
        self.findings.append(Finding(level, table, detail))

    def count(self, level: Level, n: int = 1) -> None:
        self.checked[level.value] = self.checked.get(level.value, 0) + n

    def skip(self, reason: str, n: int = 1) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + n


NO_TABLES_LABEL = "_state"


def require_coverage(layer_root: Path, state: SyncState, report: VerifyReport, *,
                     tables: Iterable[str] | None = None,
                     level: Level = Level.MANIFEST) -> bool:
    """검사 대상이 하나도 없으면 "전부 통과" 가 아니라 finding 이다 — state 가 비었거나(pull 전·다른
    루트) `--tables` 가 state 에 없는 이름이면 아무것도 대조하지 않고 ok 를 내는 silent false-pass
    를 막는다. 대상이 있으면 True."""
    if not state.tables:
        report.add(level, NO_TABLES_LABEL,
                   f"no synced tables in state — nothing to verify (root={layer_root} "
                   f"state={layer_root / SYNC_DIR / STATE_NAME}); run pull first")
        return False
    if tables is not None:
        unknown = sorted(set(tables) - set(state.tables))
        for table in unknown:
            report.add(level, table,
                       f"table not in sync state — nothing to verify "
                       f"(known={sorted(state.tables)})")
        if len(unknown) == len(set(tables)):
            return False
    return True


def local_builds(layer_root: Path) -> dict[str, str]:
    """디스크의 `<table>/MANIFEST.json` current_build 집합 — `equity.catalog.table_builds`·workbench
    `_source.table_builds` 와 같은 정의(디렉토리 스캔). state 가 아니라 이것이 카탈로그 snapshot 의
    입력이므로 대조도 같은 집합으로 한다."""
    builds: dict[str, str] = {}
    if not layer_root.is_dir():
        return builds
    for directory in sorted(layer_root.iterdir()):
        if not directory.is_dir() or not is_table_name(directory.name):
            continue
        view = read_local_manifest(layer_root, directory.name)
        if view is not None and view.ok and view.current_build:
            builds[directory.name] = view.current_build
    return builds


def verify_manifest(remote: RemoteFS, remote_root: str, layer_root: Path, state: SyncState,
                    report: VerifyReport, *, tables: Iterable[str] | None = None) -> None:
    wanted = set(tables) if tables is not None else None
    remote_tables = [t for t in list_remote_tables(remote, remote_root)
                     if wanted is None or t in wanted]
    seen: set[str] = set()
    for table in remote_tables:
        view = read_remote_manifest(remote, remote_root, table)
        if view is None:
            continue
        seen.add(table)
        report.count(Level.MANIFEST)
        local = state.tables.get(table)
        if local is None:
            report.add(Level.MANIFEST, table,
                       f"table missing locally — remote={view.current_build}")
            continue
        if view.current_build != local.build_id:
            report.add(Level.MANIFEST, table,
                       f"current_build differs — remote={view.current_build} "
                       f"local={local.build_id}")
        local_view = read_local_manifest(layer_root, table)
        if local_view is None or local_view.current_build != local.build_id:
            report.add(Level.MANIFEST, table,
                       f"local MANIFEST does not point at synced build — "
                       f"manifest={local_view.current_build if local_view else None} "
                       f"state={local.build_id}")
    for table in sorted(state.tables):
        if wanted is not None and table not in wanted:
            continue
        if table not in seen:
            report.add(Level.MANIFEST, table, "table synced locally but absent on remote")
    if wanted is None:
        _verify_disk_matches_state(layer_root, state, report)
        _verify_catalog_snapshot(layer_root, report)


def _verify_disk_matches_state(layer_root: Path, state: SyncState, report: VerifyReport) -> None:
    """디스크 MANIFEST 집합(소비자·카탈로그가 보는 것)과 sync state 가 같은가 — 예전 rsync 잔재나
    지워진 표를 잡는다. 카탈로그 유무와 무관하게 항상 본다."""
    on_disk = local_builds(layer_root)
    synced = {table: ts.build_id for table, ts in state.tables.items()}
    report.count(Level.MANIFEST)
    if on_disk != synced:
        report.add(Level.MANIFEST, NO_TABLES_LABEL,
                   f"disk MANIFESTs and sync state disagree — only_on_disk="
                   f"{sorted(set(on_disk) - set(synced))} only_in_state="
                   f"{sorted(set(synced) - set(on_disk))} build_mismatch="
                   f"{sorted(t for t in set(on_disk) & set(synced) if on_disk[t] != synced[t])}")


def _verify_catalog_snapshot(layer_root: Path, report: VerifyReport) -> None:
    meta_path = layer_root / CATALOG_META_NAME
    if not meta_path.exists():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        report.add(Level.MANIFEST, "_catalog_meta",
                   f"unreadable — path={meta_path} error={error!r}")
        return
    expected = snapshot_id(local_builds(layer_root))
    actual = meta.get("snapshot_id") if isinstance(meta, dict) else None
    report.count(Level.MANIFEST)
    if actual != expected:
        report.add(Level.MANIFEST, "_catalog_meta",
                   f"catalog snapshot_id does not match local builds — catalog={actual!r} "
                   f"local={expected!r} (rebuild: python -m equity catalog)")


def verify_files(remote: RemoteFS, remote_root: str, layer_root: Path, state: SyncState,
                 report: VerifyReport, *, tables: Iterable[str] | None = None) -> None:
    wanted = set(tables) if tables is not None else None
    for table, local in sorted(state.tables.items()):
        if wanted is not None and table not in wanted:
            continue
        table_root = layer_root / table
        local_view = read_local_manifest(layer_root, table)
        build = local_view.builds.get(local.build_id) if local_view else None
        if local_view is None or build is None:
            report.add(Level.FILES, table, f"local MANIFEST missing build — build={local.build_id}")
            continue
        try:
            remote_manifest = remote.read_bytes(f"{remote_root}/{table}/{MANIFEST_NAME}")
        except FileNotFoundError:
            report.add(Level.FILES, table, "remote MANIFEST missing")
            continue
        if remote_manifest != local_view.raw:
            report.add(Level.FILES, table, "MANIFEST bytes differ from remote")
        for partition in build.partitions:
            remote_dir = f"{remote_root}/{table}/{partition.path}"
            try:
                entries = {e.name: e for e in remote.listdir(remote_dir) if not e.is_dir}
            except FileNotFoundError:
                report.add(Level.FILES, table,
                           f"partition gone on remote (GC?) — path={partition.path}")
                continue
            local_files = local.partition_files(partition.path)
            local_names = {rel.rsplit("/", 1)[-1] for rel in local_files}
            partition_dir = table_root / partition.path
            on_disk_names = {p.name for p in partition_dir.iterdir() if p.is_file()} \
                if partition_dir.is_dir() else set()
            missing = sorted(set(entries) - local_names)
            extra = sorted(local_names - set(entries))
            stray = sorted(on_disk_names - local_names)
            if missing or extra or stray:
                report.add(Level.FILES, table,
                           f"file set differs — path={partition.path} missing={missing} "
                           f"extra={extra} stray_on_disk={stray}")
            for rel, rec in local_files.items():
                name = rel.rsplit("/", 1)[-1]
                path = table_root / rel
                report.count(Level.FILES)
                if not path.is_file():
                    report.add(Level.FILES, table, f"file missing on disk — {rel}")
                    continue
                size_on_disk = path.stat().st_size
                if size_on_disk != rec.size:
                    report.add(Level.FILES, table,
                               f"size differs from state — {rel} disk={size_on_disk} "
                               f"state={rec.size}")
                entry = entries.get(name)
                if entry is not None and rec.origin != ORIGIN_REUSED \
                        and entry.size != size_on_disk:
                    report.add(Level.FILES, table,
                               f"size differs from remote — {rel} remote={entry.size} "
                               f"local={size_on_disk}")


def verify_hash(layer_root: Path, state: SyncState, report: VerifyReport, *,
                tables: Iterable[str] | None = None) -> None:
    import duckdb

    wanted = set(tables) if tables is not None else None
    connection = duckdb.connect()
    try:
        for table, local in sorted(state.tables.items()):
            if wanted is not None and table not in wanted:
                continue
            local_view = read_local_manifest(layer_root, table)
            build: BuildInfo | None = local_view.builds.get(local.build_id) if local_view else None
            if build is None:
                report.add(Level.HASH, table,
                           f"local MANIFEST missing build — build={local.build_id}")
                continue
            for partition in build.partitions:
                if not partition.content_hash:
                    # stage 층 MANIFEST 는 파티션에 content_hash 를 싣지 않는다(`stage/build.py`) —
                    # 대조할 기준이 없으니 통과도 실패도 아닌 skip 으로 센다.
                    report.skip("hash_no_reference")
                    continue
                report.count(Level.HASH)
                partition_dir = layer_root / table / partition.path
                try:
                    actual = compute_partition_hash(connection, partition_dir, partition)
                except FileNotFoundError:
                    report.add(Level.HASH, table,
                               f"partition directory missing — path={partition.path} "
                               f"dir={partition_dir}")
                    continue
                except PartitionHashError as error:
                    # 손상·절단된 parquet 는 예상 가능한 검증 실패다 — 예외가 아니라 finding.
                    report.add(Level.HASH, table, str(error))
                    continue
                if actual != partition.content_hash:
                    report.add(Level.HASH, table,
                               f"content_hash differs — path={partition.path} "
                               f"manifest={partition.content_hash} local={actual}")
    finally:
        connection.close()
