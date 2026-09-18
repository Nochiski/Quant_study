"""계획 실행 — 수신·재사용·원자 교체·state 갱신·로컬 GC·드리프트 감지.

원자성: 새 빌드는 `<table>/_incoming/v=<build>/` 에 완성한 뒤 `<table>/v=<build>/` 로 rename 하고
그 다음에야 MANIFEST 를 바꾼다. 어댑터는 MANIFEST 의 current_build 만 따라가므로 중간에 죽어도
반쪽 빌드를 읽지 않는다. `_incoming` 에 남은 같은 크기의 파일은 재실행 때 건너뛴다(재개).

부분 실패 격리: 한 테이블의 전송이 실패하면 그 테이블만 FAILED 로 남기고 다음 테이블로 간다.
state 는 테이블마다 저장해 중단 지점까지의 진행이 남는다.
"""
from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .layout import INCOMING_DIR, MANIFEST_NAME, build_dir_names
from .plan import SyncPlan, TableAction, TablePlan, read_remote_manifest
from .remote import RemoteFS, RemoteTransferError
from .state import (
    ORIGIN_DOWNLOADED,
    ORIGIN_REUSED,
    FileRecord,
    SyncState,
    TableState,
    now_utc,
    save_state,
)

KEEP_DEFAULT = 2
FREE_SPACE_MARGIN = 1.2


class TableOutcome(Enum):
    DONE = "done"          # 새 빌드를 받아 교체했다
    UNCHANGED = "unchanged"  # 이미 최신
    FAILED = "failed"      # 전송·크기 불일치 — `_incoming` 을 남겨 재개한다
    ERROR = "error"        # 계획 단계 오류(MANIFEST 해석 불가 등)


@dataclass(frozen=True)
class TableResult:
    table: str
    outcome: TableOutcome
    build_id: str | None
    bytes_downloaded: int = 0
    bytes_reused: int = 0
    files_downloaded: int = 0
    detail: str | None = None


@dataclass
class PullReport:
    layer: str
    results: list[TableResult] = field(default_factory=list)
    meta_files: list[str] = field(default_factory=list)
    drifted: list[str] = field(default_factory=list)
    gc_removed: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def failed(self) -> list[TableResult]:
        return [r for r in self.results if r.outcome in (TableOutcome.FAILED, TableOutcome.ERROR)]

    @property
    def bytes_downloaded(self) -> int:
        return sum(r.bytes_downloaded for r in self.results)

    @property
    def bytes_reused(self) -> int:
        return sum(r.bytes_reused for r in self.results)

    @property
    def ok(self) -> bool:
        return not self.failed and not self.drifted


class InsufficientDiskSpace(RuntimeError):
    """계획 바이트 × 여유율보다 디스크 여유가 작다. 받기 전에 중단한다."""


Log = Callable[[str], None]


def _noop_log(_: str) -> None:
    return None


def check_free_space(layer_root: Path, needed_bytes: int,
                     margin: float = FREE_SPACE_MARGIN) -> None:
    layer_root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(layer_root).free
    required = int(needed_bytes * margin)
    if free < required:
        raise InsufficientDiskSpace(
            f"not enough free space — root={layer_root} free={free:,} required={required:,} "
            f"(plan={needed_bytes:,} × {margin})"
        )


def _link_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _replace_dir(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)


def _pull_table(remote: RemoteFS, layer_root: Path, plan: TablePlan, state: SyncState,
                log: Log) -> TableResult:
    build = plan.remote_build
    if plan.action is TableAction.ERROR or build is None or plan.remote is None:
        return TableResult(plan.table, TableOutcome.ERROR, None, detail=plan.detail)
    table_root = layer_root / plan.table
    if plan.action is TableAction.UP_TO_DATE:
        if plan.manifest_refresh:
            _write_bytes_atomic(table_root / MANIFEST_NAME, plan.remote.raw)
        return TableResult(plan.table, TableOutcome.UNCHANGED, build.build_id)

    incoming = table_root / INCOMING_DIR / build.dir_name
    files: dict[str, FileRecord] = {}
    bytes_reused = 0
    for reuse in plan.reuses:
        for name, size in reuse.files:
            source = table_root / reuse.source_partition_path / name
            target = incoming / reuse.partition_path.split("/", 1)[1] / name \
                if "/" in reuse.partition_path else incoming / name
            if not (target.is_file() and target.stat().st_size == size):
                _link_or_copy(source, target)
            files[f"{reuse.partition_path}/{name}"] = FileRecord(size, ORIGIN_REUSED)
            bytes_reused += size
    bytes_downloaded = 0
    files_downloaded = 0
    for transfer in plan.downloads:
        target = incoming / transfer.rel_path.split("/", 1)[1]
        if target.is_file() and target.stat().st_size == transfer.size:
            files[transfer.rel_path] = FileRecord(transfer.size, ORIGIN_DOWNLOADED)
            continue
        try:
            got = remote.download(transfer.remote_path, target)
        except (RemoteTransferError, FileNotFoundError, OSError) as error:
            return TableResult(plan.table, TableOutcome.FAILED, build.build_id, bytes_downloaded,
                               bytes_reused, files_downloaded,
                               detail=f"download failed — table={plan.table} "
                                      f"build={build.build_id} file={transfer.remote_path} "
                                      f"error={error!r}")
        if got != transfer.size:
            return TableResult(plan.table, TableOutcome.FAILED, build.build_id, bytes_downloaded,
                               bytes_reused, files_downloaded,
                               detail=f"size mismatch — table={plan.table} build={build.build_id} "
                                      f"file={transfer.remote_path} expected={transfer.size} "
                                      f"got={got}")
        files[transfer.rel_path] = FileRecord(transfer.size, ORIGIN_DOWNLOADED)
        bytes_downloaded += got
        files_downloaded += 1
        log(f"  {plan.table} {transfer.rel_path} {got:,}B")
    _replace_dir(incoming, table_root / build.dir_name)
    _write_bytes_atomic(table_root / MANIFEST_NAME, plan.remote.raw)
    state.tables[plan.table] = TableState(build.build_id, files, now_utc())
    save_state(layer_root, state)
    incoming_root = table_root / INCOMING_DIR
    if incoming_root.exists() and not any(incoming_root.iterdir()):
        incoming_root.rmdir()
    return TableResult(plan.table, TableOutcome.DONE, build.build_id, bytes_downloaded,
                       bytes_reused, files_downloaded)


def gc_table(layer_root: Path, table: str, state: SyncState, keep: int) -> list[str]:
    """current(state) 를 제외한 `v=*` 중 최신 keep-1 개만 남기고 지운다. `_incoming` 잔재도 정리."""
    table_root = layer_root / table
    current = state.tables.get(table)
    if current is None or not table_root.is_dir():
        return []
    removed: list[str] = []
    names = build_dir_names([p.name for p in table_root.iterdir() if p.is_dir()])
    current_dir = f"v={current.build_id}"
    others = [n for n in names if n != current_dir]
    for name in others[: max(0, len(others) - (keep - 1))]:
        shutil.rmtree(table_root / name)
        removed.append(f"{table}/{name}")
    incoming = table_root / INCOMING_DIR
    if incoming.is_dir():
        for stale in incoming.iterdir():
            shutil.rmtree(stale) if stale.is_dir() else stale.unlink()
            removed.append(f"{table}/{INCOMING_DIR}/{stale.name}")
        incoming.rmdir()
    return removed


def execute_plan(remote: RemoteFS, plan: SyncPlan, layer_root: Path, state: SyncState, *,
                 keep: int = KEEP_DEFAULT, check_space: bool = True,
                 log: Log = _noop_log) -> PullReport:
    started = time.monotonic()
    report = PullReport(plan.layer)
    if check_space:
        check_free_space(layer_root, plan.download_bytes)
    for table_plan in plan.tables:
        result = _pull_table(remote, layer_root, table_plan, state, log)
        report.results.append(result)
        log(f"{result.table:22s} {result.outcome.value:9s} {result.build_id or '-'} "
            f"down={result.bytes_downloaded:,} reused={result.bytes_reused:,}"
            + (f"  {result.detail}" if result.detail else ""))
        if result.outcome is TableOutcome.DONE:
            report.gc_removed.extend(gc_table(layer_root, result.table, state, keep))
    for meta in plan.meta_files:
        target = layer_root / meta.rel_path
        try:
            data = remote.read_bytes(meta.remote_path)
        except (RemoteTransferError, FileNotFoundError) as error:
            log(f"meta {meta.rel_path} skipped — {error!r}")
            continue
        _write_bytes_atomic(target, data)
        report.meta_files.append(meta.rel_path)
    # 수신 중 서버가 새 빌드를 커밋했으면 로컬 집합이 한 시점의 스냅샷이 아니다 — 다시 pull
    # 하라고 알린다.
    for table_plan in plan.tables:
        planned = table_plan.remote_build
        if table_plan.action is TableAction.ERROR or planned is None:
            continue
        latest = read_remote_manifest(remote, plan.remote_root, table_plan.table)
        if latest is not None and latest.ok and latest.current_build != planned.build_id:
            report.drifted.append(table_plan.table)
    report.elapsed_s = round(time.monotonic() - started, 1)
    return report
