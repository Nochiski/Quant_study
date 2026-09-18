"""동기화 계획 — 원격 MANIFEST 와 로컬 state 를 대조해 테이블마다 무엇을 받고 무엇을 재사용할지
정한다.

계획은 순수 값이다(원격 읽기만 하고 로컬을 바꾸지 않는다). 실행은 `pull.py`.

판정:
- `UP_TO_DATE`  로컬 state 의 build_id 가 원격 current_build 와 같고 state 가 아는 파일이 전부
                같은 크기로 디스크에 있다. MANIFEST 원문이 달라졌으면(서버 GC 로 builds[] 만
                줄어든 경우) `manifest_refresh` 만 켠다.
- `NEW_BUILD`   원격 current_build 가 다르거나 로컬이 없다. 파티션마다 로컬 직전 빌드에 같은
                꼬리·같은 content_hash 파티션이 있으면 parquet 를 재사용하고, 나머지(`_meta.json`
                포함)는 받는다.
- `ERROR`       원격 MANIFEST 를 해석할 수 없다. 다른 테이블은 계속 진행한다.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .hashing import PARQUET_SUFFIX, ReuseCheck, duckdb_reuse_check
from .layout import (
    LAYER_META_FILES,
    MANIFEST_NAME,
    REMOTE_META_DIR,
    REMOTE_META_FILES,
    SYNC_DIR,
    BuildInfo,
    ManifestView,
    Partition,
    is_safe_segment,
    is_table_name,
    parse_manifest,
)
from .remote import RemoteEntry, RemoteFS, RemoteTransferError
from .state import SyncState, TableState


class TableAction(Enum):
    UP_TO_DATE = "up_to_date"
    NEW_BUILD = "new_build"
    ERROR = "error"


@dataclass(frozen=True)
class FileTransfer:
    remote_path: str  # 원격 절대경로
    rel_path: str     # 테이블(또는 층) 루트 기준 상대경로
    size: int


@dataclass(frozen=True)
class PartitionReuse:
    """로컬 구판본 파티션의 parquet 를 새 빌드 디렉토리로 복사한다(content_hash 동일)."""

    partition_path: str          # 새 빌드의 파티션 경로(`v=<new>/year=2010`)
    source_partition_path: str   # 로컬 구판본 파티션 경로(`v=<old>/year=2010`)
    content_hash: str
    files: tuple[tuple[str, int], ...]  # (파일 이름, 크기)


@dataclass(frozen=True)
class TablePlan:
    table: str
    action: TableAction
    remote: ManifestView | None
    local_build: str | None
    downloads: tuple[FileTransfer, ...] = ()
    reuses: tuple[PartitionReuse, ...] = ()
    manifest_refresh: bool = False
    detail: str | None = None

    @property
    def download_bytes(self) -> int:
        return sum(t.size for t in self.downloads)

    @property
    def reused_bytes(self) -> int:
        return sum(size for r in self.reuses for _, size in r.files)

    @property
    def remote_build(self) -> BuildInfo | None:
        return self.remote.current if self.remote is not None else None


@dataclass(frozen=True)
class SyncPlan:
    layer: str
    remote_root: str
    tables: tuple[TablePlan, ...]
    meta_files: tuple[FileTransfer, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)
    reuse: bool = True  # False(`--no-reuse`)면 실행 단계가 `_incoming` 잔재를 버리고 전부 받는다

    @property
    def download_bytes(self) -> int:
        return sum(t.download_bytes for t in self.tables) + sum(m.size for m in self.meta_files)

    @property
    def reused_bytes(self) -> int:
        return sum(t.reused_bytes for t in self.tables)

    def by_action(self, action: TableAction) -> tuple[TablePlan, ...]:
        return tuple(t for t in self.tables if t.action is action)


def read_remote_manifest(remote: RemoteFS, remote_root: str, table: str) -> ManifestView | None:
    """MANIFEST 가 없는 디렉토리(`fixtures`)는 테이블이 아니므로 None."""
    try:
        raw = remote.read_bytes(f"{remote_root}/{table}/{MANIFEST_NAME}")
    except FileNotFoundError:
        return None
    return parse_manifest(table, raw)


def list_remote_tables(remote: RemoteFS, remote_root: str) -> list[str]:
    return sorted(e.name for e in remote.listdir(remote_root) if e.is_dir and is_table_name(e.name))


def read_local_manifest(layer_root: Path, table: str) -> ManifestView | None:
    path = layer_root / table / MANIFEST_NAME
    if not path.exists():
        return None
    return parse_manifest(table, path.read_bytes())


def _local_files_intact(layer_root: Path, table: str, state: TableState) -> bool:
    table_root = layer_root / table
    for rel, rec in state.files.items():
        path = table_root / rel
        if not path.is_file() or path.stat().st_size != rec.size:
            return False
    return True


def _reusable_partition(partition: Partition, local_state: TableState | None,
                        local_manifest: ManifestView | None, layer_root: Path,
                        table: str, reuse_check: ReuseCheck,
                        current_build_id: str) -> PartitionReuse | None:
    """로컬 직전 빌드에서 같은 꼬리·같은 content_hash 의 파티션을 찾는다. 파일이 실제로 있고
    `reuse_check`(기본: duckdb 로 해시 재계산)를 통과해야 한다 — 손상된 parquet 가 재사용 체인을
    타고 전파되지 않게 한다."""
    if local_state is None or local_manifest is None or not partition.content_hash:
        return None
    previous = local_manifest.builds.get(local_state.build_id)
    if previous is None or previous.build_id == current_build_id:
        # 같은 build_id 재수신은 로컬 사본이 손상됐다는 뜻이다 — 자기 자신을 재사용원으로 쓰지
        # 않는다
        return None
    for candidate in previous.partitions:
        if candidate.tail != partition.tail or candidate.content_hash != partition.content_hash:
            continue
        files = local_state.partition_files(candidate.path)
        parquet = [(rel.rsplit("/", 1)[-1], rec.size) for rel, rec in files.items()
                   if rel.endswith(PARQUET_SUFFIX)]
        if not parquet:
            return None
        table_root = layer_root / table
        for name, size in parquet:
            path = table_root / candidate.path / name
            if not path.is_file() or path.stat().st_size != size:
                return None
        if not reuse_check(table_root / candidate.path, partition):
            return None
        return PartitionReuse(partition.path, candidate.path, partition.content_hash,
                              tuple(sorted(parquet)))
    return None


def _plan_table(remote: RemoteFS, remote_root: str, layer_root: Path, table: str,
                view: ManifestView, state: SyncState, *, reuse: bool,
                reuse_check: ReuseCheck) -> TablePlan:
    local_state = state.tables.get(table)
    local_build = local_state.build_id if local_state else None
    if not view.ok or view.current is None:
        return TablePlan(table, TableAction.ERROR, view, local_build, detail=view.detail)
    build = view.current
    if local_state is not None and local_state.build_id == build.build_id \
            and _local_files_intact(layer_root, table, local_state):
        local_manifest = read_local_manifest(layer_root, table)
        refresh = local_manifest is None or local_manifest.raw != view.raw
        return TablePlan(table, TableAction.UP_TO_DATE, view, local_build,
                         manifest_refresh=refresh)

    local_manifest = read_local_manifest(layer_root, table) if reuse else None
    downloads: list[FileTransfer] = []
    reuses: list[PartitionReuse] = []
    for partition in build.partitions:
        remote_dir = f"{remote_root}/{table}/{partition.path}"
        try:
            entries = remote.listdir(remote_dir)
        except FileNotFoundError:
            return TablePlan(table, TableAction.ERROR, view, local_build,
                             detail=f"partition dir missing on remote — table={table} "
                                    f"build={build.build_id} path={remote_dir}")
        except (RemoteTransferError, OSError) as error:
            return TablePlan(table, TableAction.ERROR, view, local_build,
                             detail=f"remote listdir failed — table={table} "
                                    f"build={build.build_id} path={remote_dir} error={error!r}")
        remote_parquet = {e.name for e in _files_only(entries) if e.name.endswith(PARQUET_SUFFIX)}
        reuse_hit = _reusable_partition(partition, local_state, local_manifest, layer_root,
                                        table, reuse_check, build.build_id) if reuse else None
        if reuse_hit and {name for name, _ in reuse_hit.files} != remote_parquet:
            # content_hash 는 행 내용 해시라 파일 분할이 달라져도 같다 — parquet 파일 집합이 원격과
            # 정확히 같을 때만 재사용한다(다르면 구판본 조각이 새 빌드에 섞여 행이 중복된다).
            reuse_hit = None
        reused_names = {name for name, _ in reuse_hit.files} if reuse_hit else set()
        if reuse_hit:
            reuses.append(reuse_hit)
        for entry in _files_only(entries):
            if not is_safe_segment(entry.name):
                # 원격 이름이 곧 로컬 경로 조각이다 — 문법 밖 이름은 받지 않고 테이블을 멈춘다.
                return TablePlan(table, TableAction.ERROR, view, local_build,
                                 detail=f"unsafe remote file name — table={table} "
                                        f"build={build.build_id} dir={remote_dir} "
                                        f"name={entry.name!r}")
            if entry.name in reused_names:
                continue
            downloads.append(FileTransfer(f"{remote_dir}/{entry.name}",
                                          f"{partition.path}/{entry.name}", entry.size))
    return TablePlan(table, TableAction.NEW_BUILD, view, local_build,
                     downloads=tuple(downloads), reuses=tuple(reuses))


def _files_only(entries: Iterable[RemoteEntry]) -> list[RemoteEntry]:
    return sorted((e for e in entries if not e.is_dir), key=lambda e: e.name)


def make_plan(remote: RemoteFS, remote_root: str, layer_root: Path, state: SyncState, *,
              layer: str, tables: Iterable[str] | None = None, reuse: bool = True,
              reuse_check: ReuseCheck | None = None) -> SyncPlan:
    """원격 층 전체(또는 `tables`)의 동기화 계획. 로컬은 읽기만 한다.

    reuse_check: 재사용 후보 파티션을 받아들일지 정하는 함수. 기본은 duckdb 로 해시를 다시 계산해
    MANIFEST 와 대조한다(`hashing.duckdb_reuse_check`)."""
    if reuse_check is None:
        reuse_check = duckdb_reuse_check()
    wanted = set(tables) if tables is not None else None
    warnings: list[str] = []
    plans: list[TablePlan] = []
    remote_tables = list_remote_tables(remote, remote_root)
    if wanted is not None:
        unknown = sorted(wanted - set(remote_tables))
        if unknown:
            warnings.append(f"requested tables not on remote — layer={layer} tables={unknown}")
        remote_tables = [t for t in remote_tables if t in wanted]
    for table in remote_tables:
        view = read_remote_manifest(remote, remote_root, table)
        if view is None:
            continue
        plans.append(_plan_table(remote, remote_root, layer_root, table, view, state,
                                 reuse=reuse, reuse_check=reuse_check))
    meta: list[FileTransfer] = []
    root_entries = {e.name: e for e in remote.listdir(remote_root) if not e.is_dir}
    for name in LAYER_META_FILES:
        entry = root_entries.get(name)
        if entry is not None:
            meta.append(FileTransfer(f"{remote_root}/{name}", name, entry.size))
    for name in REMOTE_META_FILES:
        # 서버 카탈로그·계약 메타는 참고용이다 — 로컬 catalog 산출물을 덮어쓰지 않도록
        # `_sync/remote/` 에 둔다.
        entry = root_entries.get(name)
        if entry is not None:
            meta.append(FileTransfer(f"{remote_root}/{name}",
                                     f"{SYNC_DIR}/{REMOTE_META_DIR}/{name}", entry.size))
    return SyncPlan(layer, remote_root, tuple(plans), tuple(meta), tuple(warnings), reuse)

