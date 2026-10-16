"""연구 입력 보존·검증 CLI. S01 계약은 docs/planning/research-to-trading/S01-snapshot.md."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

import duckdb
from ledger_sync.hashing import PartitionHashError, compute_partition_hash
from ledger_sync.layout import MANIFEST_NAME, is_table_name, parse_manifest, snapshot_id

from . import catalog, views

PENDING_NAME = "_research_snapshot.pending"
SNAPSHOT_NAME = "_research_snapshot.json"
FORMAT_VERSION = 1
# 필드 의미·단위·공개시점·소비자 투영의 정본을 원문 그대로 보존한다.
CONTRACT_FILES = (
    ".claude/rules/strategy-workbench-sot.md",
    "database/docs/EQUITY_DESIGN.md",
    "database/docs/EQUITY_FIELD_MAP.md",
    "database/src/equity/model.py",
    "database/src/equity/rules_s19.py",
    "database/src/equity/views.py",
    "backend/src/strategy_workbench/adapters/outbound/equity_duckdb/_specs.py",
    "backend/src/strategy_workbench/application/equity_workspace/ports/outgoing/equity_data.py",
)
ENVIRONMENT_FILES = ("pyproject.toml", "uv.lock", "backend/pyproject.toml", "backend/uv.lock")


class SnapshotStatus(Enum):
    READY = "ready"
    INVALID = "invalid"


@dataclass(frozen=True)
class SnapshotResult:
    """예상 가능한 보존·검증 실패를 호출자가 확인하는 결과 값."""

    root: Path
    detail: str | None = None
    ledger_snapshot_id: str | None = None
    contract_sources_sha256: str | None = None
    status: SnapshotStatus = field(init=False)

    def __post_init__(self) -> None:
        if self.detail is None:
            if not self.ledger_snapshot_id or not self.contract_sources_sha256:
                raise ValueError(f"ready snapshot requires both identities — root={self.root}")
            object.__setattr__(self, "status", SnapshotStatus.READY)
        else:
            if not self.detail.strip() or self.ledger_snapshot_id or self.contract_sources_sha256:
                raise ValueError(f"invalid snapshot requires detail and no identities — root={self.root}")
            object.__setattr__(self, "status", SnapshotStatus.INVALID)

    @property
    def ok(self) -> bool:
        return self.status is SnapshotStatus.READY


class SnapshotInvalid(ValueError):
    """보존 또는 검증 경계에서 발견한 입력 계약 위반."""


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(value: object) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _safe_file(root: Path, path: Path) -> None:
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
        raise SnapshotInvalid(f"regular file inside root required — root={root} path={path}")
    for parent in path.relative_to(root).parents:
        if (root / parent).is_symlink():
            raise SnapshotInvalid(f"symlink directory forbidden — root={root} path={path}")


def _manifests(root: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for directory in sorted(root.iterdir()):
        path = directory / MANIFEST_NAME
        if not is_table_name(directory.name) or not directory.is_dir():
            continue
        if not path.exists():
            if any(child.name.startswith("v=") for child in directory.iterdir()):
                raise SnapshotInvalid(f"table MANIFEST missing — table={directory.name} root={root}")
            continue
        _safe_file(root, path)
        raw = path.read_bytes()
        view = parse_manifest(directory.name, raw)
        if not view.ok:
            raise SnapshotInvalid(f"invalid MANIFEST — table={directory.name} {view.detail}")
        document = json.loads(raw)
        records = [b for b in document["builds"]
                   if isinstance(b, dict) and b.get("build_id") == view.current_build]
        if document.get("table") != directory.name or len(records) != 1:
            raise SnapshotInvalid(f"ambiguous MANIFEST — table={directory.name} current={view.current_build}")
        found[directory.name] = raw
    if not found:
        raise SnapshotInvalid(f"no committed tables — root={root}")
    return found


def _check_partitions(root: Path, manifests: dict[str, bytes]) -> dict[str, str]:
    builds: dict[str, str] = {}
    with duckdb.connect() as connection:
        for table, raw in manifests.items():
            view = parse_manifest(table, raw)
            build = view.current
            if build is None:
                raise SnapshotInvalid(f"current build missing — table={table} root={root}")
            builds[table] = build.build_id
            paths = [p.path for p in build.partitions]
            if len(paths) != len(set(paths)):
                raise SnapshotInvalid(f"duplicate partition — table={table} paths={paths}")
            for partition in build.partitions:
                directory = root / table / partition.path
                if not partition.content_hash:
                    raise SnapshotInvalid(f"partition hash required — table={table} path={partition.path}")
                if not directory.is_dir() or directory.is_symlink():
                    raise SnapshotInvalid(f"partition missing/unsafe — table={table} path={directory}")
                for path in directory.iterdir():
                    if path.is_symlink():
                        raise SnapshotInvalid(f"symlink forbidden — table={table} path={path}")
                    if path.is_file():
                        _safe_file(root, path)
                actual = compute_partition_hash(connection, directory, partition)
                if actual.split(":", 1)[0] != str(partition.n_rows):
                    raise SnapshotInvalid(
                        f"partition n_rows differs — table={table} path={partition.path} "
                        f"expected={partition.n_rows} actual_hash={actual}")
                if actual != partition.content_hash:
                    raise SnapshotInvalid(
                        f"content_hash differs — table={table} path={partition.path} "
                        f"expected={partition.content_hash} actual={actual}")
    return builds


def _inventory(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SnapshotInvalid(f"symlink forbidden — root={root} path={path}")
        if path.is_file() and path not in (root / PENDING_NAME, root / SNAPSHOT_NAME):
            _safe_file(root, path)
            files[path.relative_to(root).as_posix()] = _sha(path)
    return files


def _git(repo_root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo_root), *args], check=True,
                          capture_output=True).stdout


def _source_hashes(repo_root: Path) -> dict[str, str]:
    names = _git(repo_root, "ls-files", "--cached", "--others", "--exclude-standard", "--",
                 "database/src", "backend/src", "backend/rust").decode().splitlines()
    hashes: dict[str, str] = {}
    for relative in sorted(set(names)):
        source = repo_root / relative
        if not source.exists():
            continue
        _safe_file(repo_root.resolve(), source)
        hashes[relative] = _sha(source)
    return hashes


def _provenance(repo_root: Path, root: Path) -> dict[str, object]:
    archived = root / "_research_provenance"
    contract_hashes: dict[str, str] = {}
    for relative in CONTRACT_FILES + ENVIRONMENT_FILES:
        source = repo_root / relative
        _safe_file(repo_root, source)
        target = archived / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if relative in CONTRACT_FILES:
            contract_hashes[relative] = _sha(target)
    patch = _git(repo_root, "diff", "--binary", "HEAD", "--", "database/src", "backend/src",
                 "backend/rust", *CONTRACT_FILES, *ENVIRONMENT_FILES)
    (archived / "working-tree.patch").write_bytes(patch)
    # SHA만으로 작업 트리의 수정·미추적 코드를 설명할 수 없으므로 파일 지문도 남긴다.
    source_hashes = _source_hashes(repo_root)
    untracked = set(_git(repo_root, "ls-files", "--others", "--exclude-standard", "--",
                         "database/src", "backend/src", "backend/rust").decode().splitlines())
    for relative in sorted(set(source_hashes) & untracked):
        target = archived / "untracked" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo_root / relative, target)
    return {
        "git_sha": _git(repo_root, "rev-parse", "HEAD").decode().strip(),
        "working_tree_patch_sha256": _sha(archived / "working-tree.patch"),
        "source_files_sha256": source_hashes,
        "contract_files_sha256": contract_hashes,
        "environment_files_sha256": {
            relative: _sha(archived / relative) for relative in ENVIRONMENT_FILES
        },
        "contract_sources_sha256": _identity(contract_hashes),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": dict(sorted((d.metadata["Name"], d.version)
                                for d in importlib.metadata.distributions() if d.metadata["Name"])),
    }


def create_snapshot(source_root: Path, root: Path, *, repo_root: Path) -> SnapshotResult:
    """current 빌드 전체를 복사하고 검증한 뒤 pending을 해제해 공개한다. 기존 root는 거절한다."""
    source_root, root, repo_root = source_root.resolve(), root.resolve(), repo_root.resolve()
    reserved = False
    try:
        if root.resolve().is_relative_to(source_root) or source_root.is_relative_to(root.resolve()):
            raise SnapshotInvalid(f"source/destination must be separate — source={source_root} root={root}")
        manifests = _manifests(source_root)
        _check_partitions(source_root, manifests)
        root.mkdir(parents=False, exist_ok=False)
        reserved = True
        (root / PENDING_NAME).write_text("research snapshot publication pending\n", encoding="utf-8")
        for table, raw in manifests.items():
            view = parse_manifest(table, raw)
            build = view.current
            if build is None:
                raise SnapshotInvalid(f"current missing while copying — table={table}")
            for partition in build.partitions:
                source = source_root / table / partition.path
                target = root / table / partition.path
                target.mkdir(parents=True, exist_ok=True)
                for path in sorted(source.iterdir()):
                    if path.is_file():
                        _safe_file(source_root, path)
                        shutil.copyfile(path, target / path.name)
            document = json.loads(raw)
            document["builds"] = [b for b in document["builds"]
                                  if isinstance(b, dict) and b.get("build_id") == build.build_id]
            document["keep"] = 1
            (root / table / MANIFEST_NAME).write_bytes(_json_bytes(document))
        provenance = _provenance(repo_root, root)
        macros, skipped = views.render_macros(root)
        catalog.write_catalog(root, macros)
        builds = _check_partitions(root, _manifests(root))
        if _manifests(source_root) != manifests:
            raise SnapshotInvalid(f"source MANIFESTs changed during copy — source={source_root}")
        document = {
            "format_version": FORMAT_VERSION,
            "root": str(root.resolve()),
            "created_at_utc": datetime.now(UTC).isoformat(),
            "ledger_snapshot_id": snapshot_id(builds),
            "builds": builds,
            "provenance": provenance,
            "macros_skipped": skipped,
            "files_sha256": _inventory(root),
        }
        temporary = root / f".{SNAPSHOT_NAME}.tmp"
        temporary.write_bytes(_json_bytes(document))
        os.replace(temporary, root / SNAPSHOT_NAME)
        verified = _verify_contents(root, repo_root=None)
        if not verified.ok:
            raise SnapshotInvalid(str(verified.detail))
        (root / PENDING_NAME).unlink()
        return verified
    except (OSError, ValueError, duckdb.Error, PartitionHashError,
            subprocess.CalledProcessError) as error:
        if reserved:
            try:
                shutil.rmtree(root)
            except OSError as cleanup_error:
                # 정리가 중단되어도 남은 입력이 완료된 root로 오인되지 않게 차단을 복구한다.
                (root / PENDING_NAME).touch(exist_ok=True)
                return SnapshotResult(root, f"create/cleanup failed — root={root} "
                                      f"error={error} cleanup_error={cleanup_error}")
        return SnapshotResult(root, f"create failed — source={source_root} root={root} error={error}")


def verify_snapshot(root: Path, *, repo_root: Path | None = None) -> SnapshotResult:
    """완료·파일 집합/바이트·파티션 내용 해시를 재검증한다. repo_root는 현 코드/계약도 대조한다."""
    root = root.resolve()
    if (root / PENDING_NAME).exists():
        return SnapshotResult(root, f"snapshot publication pending — root={root}")
    return _verify_contents(root, repo_root=repo_root)


def _verify_contents(root: Path, *, repo_root: Path | None) -> SnapshotResult:
    """생성자는 pending을 유지한 채 같은 전체 검증을 통과한 뒤에만 공개한다."""
    try:
        descriptor = root / SNAPSHOT_NAME
        _safe_file(root, descriptor)
        document = json.loads(descriptor.read_bytes())
        if not isinstance(document, dict):
            raise SnapshotInvalid(f"snapshot descriptor must be object — root={root}")
        if document.get("format_version") != FORMAT_VERSION or document.get("root") != str(root):
            raise SnapshotInvalid(f"snapshot format/location differs — root={root}")
        if _inventory(root) != document.get("files_sha256"):
            raise SnapshotInvalid(f"snapshot file set/sha256 differs — root={root}")
        builds = _check_partitions(root, _manifests(root))
        sid = snapshot_id(builds)
        if document.get("builds") != builds or document.get("ledger_snapshot_id") != sid:
            raise SnapshotInvalid(f"snapshot builds/id differs — root={root} actual={builds}")
        provenance = document["provenance"]
        if not isinstance(provenance, dict):
            raise SnapshotInvalid(f"snapshot provenance must be object — root={root}")
        contracts = provenance["contract_files_sha256"]
        if not isinstance(contracts, dict) or not isinstance(provenance["source_files_sha256"], dict):
            raise SnapshotInvalid(f"snapshot source/contract hashes must be objects — root={root}")
        if set(contracts) != set(CONTRACT_FILES) or any(
            not isinstance(value, str) for value in contracts.values()
        ):
            raise SnapshotInvalid(f"snapshot contract source set invalid — root={root}")
        if _identity(contracts) != provenance["contract_sources_sha256"]:
            raise SnapshotInvalid(f"snapshot field contract identity differs — root={root}")
        if repo_root is not None:
            repo_root = repo_root.resolve()
            environment_files = provenance["environment_files_sha256"]
            if not isinstance(environment_files, dict) or set(environment_files) != set(ENVIRONMENT_FILES):
                raise SnapshotInvalid(f"snapshot environment source set invalid — root={root}")
            for relative, expected in environment_files.items():
                _safe_file(repo_root, repo_root / relative)
                if _sha(repo_root / relative) != expected:
                    raise SnapshotInvalid(f"current environment source differs — file={relative} root={root}")
            if _source_hashes(repo_root) != provenance["source_files_sha256"]:
                raise SnapshotInvalid(f"current code file set/sha256 differs — root={root}")
            for relative, expected in contracts.items():
                if not isinstance(relative, str) or relative not in CONTRACT_FILES:
                    raise SnapshotInvalid(f"unexpected contract path — root={root} path={relative!r}")
                _safe_file(repo_root, repo_root / relative)
                if _sha(repo_root / relative) != expected:
                    raise SnapshotInvalid(f"current field contract differs — file={relative} root={root}")
        return SnapshotResult(root, ledger_snapshot_id=sid,
                              contract_sources_sha256=provenance["contract_sources_sha256"])
    except (OSError, ValueError, KeyError, TypeError, duckdb.Error, PartitionHashError) as error:
        return SnapshotResult(root, f"verify failed — root={root} error={error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="별도 root에 연구 입력 보존")
    create.add_argument("--source-root", type=Path, required=True)
    create.add_argument("--root", type=Path, required=True)
    create.add_argument("--repo-root", type=Path, required=True)
    for verb in ("verify", "open"):
        command = sub.add_parser(verb, help="전체 무결성 검증" if verb == "verify" else "검증 후 root 반환")
        command.add_argument("--root", type=Path, required=True)
        command.add_argument("--repo-root", type=Path)
    args = parser.parse_args(argv)
    result = (create_snapshot(args.source_root, args.root, repo_root=args.repo_root)
              if args.command == "create" else verify_snapshot(args.root, repo_root=args.repo_root))
    if result.ok:
        print(str(result.root) if args.command == "open" else json.dumps({
            "root": str(result.root), "ledger_snapshot_id": result.ledger_snapshot_id,
            "contract_sources_sha256": result.contract_sources_sha256}, ensure_ascii=False))
        return 0
    print(result.detail, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
