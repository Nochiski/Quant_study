"""계획 실행 — 수신·재사용·원자 교체·state 갱신·로컬 GC·드리프트 감지.

원자성: 새 빌드는 `<table>/_incoming/v=<build>/` 에 완성한 뒤 `<table>/v=<build>/` 로 rename 하고
그 다음에야 MANIFEST 를 바꾼다. 어댑터는 MANIFEST 의 current_build 만 따라가므로 중간에 죽어도
반쪽 빌드를 읽지 않는다. `_incoming` 에 남은 같은 크기의 파일은 재실행 때 건너뛴다(재개).

부분 실패 격리: 한 테이블의 전송·복사·교체·기록이 실패하면(원격 오류든 로컬 OSError 든) 그 테이블만
FAILED 로 남기고 다음 테이블로 간다. state 는 테이블마다 저장해 중단 지점까지의 진행이 남는다.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from stage.model import (  # pyright: ignore[reportMissingImports]  # reason: database/src 는 PYTHONPATH 로 얹는다(equity 와 같은 규약)
    build_id_time,
)

from .layout import INCOMING_DIR, MANIFEST_NAME, build_dir_names
from .plan import SyncPlan, TableAction, TablePlan, read_local_manifest, read_remote_manifest
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
OLD_DIR_SUFFIX = ".replaced"


class TableOutcome(Enum):
    DONE = "done"          # 새 빌드를 받아 교체했다
    UNCHANGED = "unchanged"  # 이미 최신
    FAILED = "failed"      # 전송·크기 불일치·로컬 IO 실패 — `_incoming` 을 남겨 재개한다
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
    warnings: list[str] = field(default_factory=list)
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
    """수신 + 재사용(하드링크가 안 되는 볼륨이면 복사) 바이트를 합쳐 여유를 본다."""
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
    """`source` 를 `target` 자리에 넣는다. 살아 있는 `target`(같은 build_id 재수신)은 먼저 옆으로
    rename 해 두고 교체한 뒤 지운다 — MANIFEST 가 빈 경로를 가리키는 창을 rename 한 번으로
    줄인다."""
    old = target.with_name(target.name + OLD_DIR_SUFFIX)
    if old.exists():
        shutil.rmtree(old)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        os.replace(target, old)
    os.replace(source, target)
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)


def _inside(root: Path, path: Path) -> Path:
    """원격이 준 이름으로 만든 로컬 경로가 테이블 루트 밖으로 나가면 즉시 멈춘다(사후 방어 —
    1차 방어는 `layout.is_safe_segment`)."""
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise RuntimeError(f"local path escapes table root — root={root} path={path}")
    return path


def _failed(plan: TablePlan, build_id: str, detail: str, *, downloaded: int, reused: int,
            files_downloaded: int) -> TableResult:
    return TableResult(plan.table, TableOutcome.FAILED, build_id, downloaded, reused,
                       files_downloaded, detail=detail)


def _pull_table(remote: RemoteFS, layer_root: Path, plan: TablePlan, state: SyncState,
                log: Log, *, clear_incoming: bool) -> TableResult:
    build = plan.remote_build
    if plan.action is TableAction.ERROR or build is None or plan.remote is None:
        return TableResult(plan.table, TableOutcome.ERROR, None, detail=plan.detail)
    table_root = layer_root / plan.table
    if plan.action is TableAction.UP_TO_DATE:
        if plan.manifest_refresh:
            _write_bytes_atomic(table_root / MANIFEST_NAME, plan.remote.raw)
        return TableResult(plan.table, TableOutcome.UNCHANGED, build.build_id)

    incoming = _inside(table_root, table_root / INCOMING_DIR / build.dir_name)
    files: dict[str, FileRecord] = {}
    bytes_reused = 0
    bytes_downloaded = 0
    files_downloaded = 0
    try:
        if clear_incoming and incoming.exists():
            # `--no-reuse`: 앞선 실행이 재사용해 둔 복사본을 "받은 파일" 로 승인하면 바이트 동일
            # 보증이 깨진다 — 재개 없이 처음부터 받는다.
            shutil.rmtree(incoming)
        for reuse in plan.reuses:
            for name, size in reuse.files:
                source = table_root / reuse.source_partition_path / name
                target = incoming / reuse.partition_path.split("/", 1)[1] / name \
                    if "/" in reuse.partition_path else incoming / name
                _inside(table_root, target)
                if not (target.is_file() and target.stat().st_size == size):
                    _link_or_copy(source, target)
                files[f"{reuse.partition_path}/{name}"] = FileRecord(size, ORIGIN_REUSED)
                bytes_reused += size
        for transfer in plan.downloads:
            target = _inside(table_root, incoming / transfer.rel_path.split("/", 1)[1])
            if target.is_file() and target.stat().st_size == transfer.size:
                files[transfer.rel_path] = FileRecord(transfer.size, ORIGIN_DOWNLOADED)
                continue
            try:
                got = remote.download(transfer.remote_path, target)
            except (RemoteTransferError, FileNotFoundError, OSError) as error:
                return _failed(plan, build.build_id,
                               f"download failed — table={plan.table} build={build.build_id} "
                               f"file={transfer.remote_path} error={error!r}",
                               downloaded=bytes_downloaded, reused=bytes_reused,
                               files_downloaded=files_downloaded)
            if got != transfer.size:
                return _failed(plan, build.build_id,
                               f"size mismatch — table={plan.table} build={build.build_id} "
                               f"file={transfer.remote_path} expected={transfer.size} got={got}",
                               downloaded=bytes_downloaded, reused=bytes_reused,
                               files_downloaded=files_downloaded)
            files[transfer.rel_path] = FileRecord(transfer.size, ORIGIN_DOWNLOADED)
            bytes_downloaded += got
            files_downloaded += 1
            log(f"  {plan.table} {transfer.rel_path} {got:,}B")
        _replace_dir(incoming, _inside(table_root, table_root / build.dir_name))
        _write_bytes_atomic(table_root / MANIFEST_NAME, plan.remote.raw)
        state.tables[plan.table] = TableState(build.build_id, files, now_utc())
        save_state(layer_root, state)
    except OSError as error:
        # 로컬 IO(공유 위반·ENOSPC·권한)도 테이블 단위로 격리한다 — `_incoming` 은 남겨 재개한다.
        return _failed(plan, build.build_id,
                       f"local io failed — table={plan.table} build={build.build_id} "
                       f"errno={error.errno} winerror={getattr(error, 'winerror', None)} "
                       f"path={error.filename!r} error={error!r}",
                       downloaded=bytes_downloaded, reused=bytes_reused,
                       files_downloaded=files_downloaded)
    incoming_root = table_root / INCOMING_DIR
    try:
        if incoming_root.exists() and not any(incoming_root.iterdir()):
            incoming_root.rmdir()
    except OSError:
        pass  # 빈 `_incoming` 정리는 다음 실행이 다시 한다
    return TableResult(plan.table, TableOutcome.DONE, build.build_id, bytes_downloaded,
                       bytes_reused, files_downloaded)


def _manifest_current_build(path: Path) -> str | None:
    """로컬 MANIFEST 가 가리키는 build_id — 문서가 일부 깨져 있어도 포인터만은 읽어 GC 보호에
    쓴다."""
    view = read_local_manifest(path.parent.parent, path.parent.name)
    if view is not None and view.current_build:
        return view.current_build
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    current = document.get("current_build") if isinstance(document, dict) else None
    return current if isinstance(current, str) and current else None


def _build_sort_key(name: str) -> tuple[float, str]:
    """`v=<build>` 디렉토리를 build_id 가 박은 시각 순으로(파싱 불가는 이름순 폴백) — `e_`·`m_`
    접두어 때문에 사전순은 시간순이 아니다."""
    stamp = build_id_time(name[len("v="):])
    return (stamp.timestamp() if stamp else float("-inf"), name)


def gc_table(layer_root: Path, table: str, state: SyncState, keep: int,
             warnings: list[str] | None = None) -> list[str]:
    """current 를 제외한 `v=*` 중 최신 keep-1 개만 남기고 지운다. current 는 state 와 로컬 MANIFEST
    양쪽이 가리키는 빌드를 모두 보호한다(둘이 다르면 경고). `_incoming` 잔재도 정리. 삭제 실패는
    경고로 남기고 다음 실행에 맡긴다."""
    table_root = layer_root / table
    current = state.tables.get(table)
    if current is None or not table_root.is_dir():
        return []
    protected = {f"v={current.build_id}"}
    manifest_current = _manifest_current_build(table_root / MANIFEST_NAME)
    if manifest_current:
        if manifest_current != current.build_id and warnings is not None:
            warnings.append(f"gc: state and MANIFEST disagree — table={table} "
                            f"state={current.build_id} manifest={manifest_current}")
        protected.add(f"v={manifest_current}")
    removed: list[str] = []
    names = sorted(build_dir_names([p.name for p in table_root.iterdir() if p.is_dir()]),
                   key=_build_sort_key)
    others = [n for n in names if n not in protected]
    for name in others[: max(0, len(others) - (keep - 1))]:
        try:
            shutil.rmtree(table_root / name)
        except OSError as error:
            if warnings is not None:
                warnings.append(f"gc: could not remove — path={table_root / name} error={error!r}")
            continue
        removed.append(f"{table}/{name}")
    incoming = table_root / INCOMING_DIR
    if incoming.is_dir():
        try:
            for stale in incoming.iterdir():
                shutil.rmtree(stale) if stale.is_dir() else stale.unlink()
                removed.append(f"{table}/{INCOMING_DIR}/{stale.name}")
            incoming.rmdir()
        except OSError as error:
            if warnings is not None:
                warnings.append(f"gc: could not clean _incoming — path={incoming} error={error!r}")
    return removed


def execute_plan(remote: RemoteFS, plan: SyncPlan, layer_root: Path, state: SyncState, *,
                 keep: int = KEEP_DEFAULT, check_space: bool = True,
                 log: Log = _noop_log) -> PullReport:
    started = time.monotonic()
    report = PullReport(plan.layer)
    if check_space:
        check_free_space(layer_root, plan.download_bytes + plan.reused_bytes)
    for table_plan in plan.tables:
        result = _pull_table(remote, layer_root, table_plan, state, log,
                             clear_incoming=not plan.reuse)
        report.results.append(result)
        log(f"{result.table:22s} {result.outcome.value:9s} {result.build_id or '-'} "
            f"down={result.bytes_downloaded:,} reused={result.bytes_reused:,}"
            + (f"  {result.detail}" if result.detail else ""))
        if result.outcome is TableOutcome.DONE:
            report.gc_removed.extend(gc_table(layer_root, result.table, state, keep,
                                              report.warnings))
    for meta in plan.meta_files:
        target = layer_root / meta.rel_path
        try:
            data = remote.read_bytes(meta.remote_path)
            _write_bytes_atomic(target, data)
        except (RemoteTransferError, FileNotFoundError, OSError) as error:
            report.warnings.append(f"meta {meta.rel_path} skipped — {error!r}")
            log(f"meta {meta.rel_path} skipped — {error!r}")
            continue
        report.meta_files.append(meta.rel_path)
    # 수신 중 서버가 새 빌드를 커밋했으면 로컬 집합이 한 시점의 스냅샷이 아니다 — 다시 pull
    # 하라고 알린다.
    for table_plan in plan.tables:
        planned = table_plan.remote_build
        if table_plan.action is TableAction.ERROR or planned is None:
            continue
        try:
            latest = read_remote_manifest(remote, plan.remote_root, table_plan.table)
        except (RemoteTransferError, OSError) as error:
            report.warnings.append(f"drift check skipped — table={table_plan.table} "
                                   f"error={error!r}")
            continue
        if latest is not None and latest.ok and latest.current_build != planned.build_id:
            report.drifted.append(table_plan.table)
    report.elapsed_s = round(time.monotonic() - started, 1)
    return report
