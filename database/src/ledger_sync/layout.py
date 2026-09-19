"""서버·로컬이 공유하는 디렉토리 규약 — MANIFEST 해석, 테이블 판별, 파티션 경로.

EQUITY_DESIGN §2: `<layer_root>/<table>/MANIFEST.json` 의 `current_build` 가 가리키는 BuildRecord 의
`partitions[].path`(`v=<build>[/year=YYYY]`) 디렉토리만 읽는다. `v=*` glob 은 금지 — 구판본이
공존한다.
MANIFEST 자체는 서버 원문(bytes)을 그대로 보관하므로 여기서는 읽기만 하고 다시 쓰지 않는다.

신뢰 경계: 원격이 주는 문자열(build_id·파티션 경로·디렉토리/파일 이름)은 전부 로컬 경로 조각이
된다. 서버가 침해되거나 MANIFEST 생성기가 잘못돼도 로컬 루트 밖을 지우거나 쓰지 못하도록
이름 문법을 여기서 강제한다(`is_safe_segment`).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import unquote

MANIFEST_NAME = "MANIFEST.json"
BUILD_DIR_PREFIX = "v="
INCOMING_DIR = "_incoming"
SYNC_DIR = "_sync"
# 층 루트 바로 아래의 메타 파일 중 로컬 루트에 그대로 두는 것 — `baseline.json` 은 `equity catalog`
# 가 읽는다. `_catalog_meta.json` 은 **로컬 catalog 산출물**이라 서버 것으로 덮어쓰면 워크벤치의
# stale-catalog 가드(`_source.read_catalog`)가 무력화된다 → 서버 원문은 `_sync/remote/` 에만 둔다.
LAYER_META_FILES: tuple[str, ...] = ("baseline.json",)
REMOTE_META_FILES: tuple[str, ...] = ("_catalog_meta.json", "_contract_meta.json")
REMOTE_META_DIR = "remote"  # `<layer_root>/_sync/remote/<name>`
CATALOG_META_NAME = "_catalog_meta.json"
HIVE_NULL_PARTITION = "__HIVE_DEFAULT_PARTITION__"

# 원격 이름 하나(디렉토리·파일·build_id·꼬리 세그먼트)에 허용하는 문법. 경로 구분자·`..`·드라이브
# 문자·공백을 배제한다. 서버 실측 이름: `e_20260918T133020_791078Z`, `year=2010`, `part0.parquet`,
# `_meta.json`, `MANIFEST.json`.
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._=%-]*$")


def is_safe_segment(name: str) -> bool:
    return bool(name) and name not in (".", "..") and _SAFE_SEGMENT.match(name) is not None


class ManifestStatus(Enum):
    OK = "ok"
    INVALID_JSON = "invalid_json"
    NOT_OBJECT = "not_object"
    NO_CURRENT_BUILD = "no_current_build"
    CURRENT_NOT_IN_BUILDS = "current_not_in_builds"
    BAD_PARTITION = "bad_partition"
    UNSAFE_NAME = "unsafe_name"


@dataclass(frozen=True)
class Partition:
    """BuildRecord.partitions[] 한 항목. `path` 는 테이블 루트 기준 상대경로
    (`v=<build>/year=2010`)."""

    path: str
    n_rows: int
    content_hash: str

    @property
    def tail(self) -> str:
        """`v=<build>/` 뒤의 꼬리. whole 테이블은 빈 문자열."""
        _, _, tail = self.path.partition("/")
        return tail

    def hive_columns(self) -> tuple[tuple[str, str | None], ...]:
        """꼬리의 `key=value` 조각들 — content_hash 재계산 때 하이브 컬럼으로 덧붙인다. duckdb 의
        hive 규약대로 값은 url-디코딩하고 `__HIVE_DEFAULT_PARTITION__` 은 NULL(None)이다."""
        pairs: list[tuple[str, str | None]] = []
        for segment in self.tail.split("/"):
            if "=" in segment:
                key, _, value = segment.partition("=")
                decoded = unquote(value)
                pairs.append((key, None if decoded == HIVE_NULL_PARTITION else decoded))
        return tuple(pairs)


@dataclass(frozen=True)
class BuildInfo:
    table: str
    build_id: str
    built_at_utc: str
    content_hash: str
    partitions: tuple[Partition, ...]

    @property
    def dir_name(self) -> str:
        return f"{BUILD_DIR_PREFIX}{self.build_id}"


@dataclass(frozen=True)
class ManifestView:
    """MANIFEST.json 해석 결과. status 가 OK 가 아니면 `current` 는 None 이고 `detail` 이 이유다."""

    table: str
    raw: bytes
    status: ManifestStatus
    detail: str | None
    current_build: str | None
    builds: dict[str, BuildInfo]

    @property
    def ok(self) -> bool:
        return self.status is ManifestStatus.OK

    @property
    def current(self) -> BuildInfo | None:
        if not self.ok or self.current_build is None:
            return None
        return self.builds.get(self.current_build)


def _failed(table: str, raw: bytes, status: ManifestStatus, detail: str) -> ManifestView:
    return ManifestView(table, raw, status, detail, None, {})


def _partition_path_ok(path: str, build_id: str) -> bool:
    """`v=<build_id>` 로 시작하고, 뒤따르는 꼬리 세그먼트가 전부 안전한 이름이다."""
    head, _, tail = path.partition("/")
    if head != f"{BUILD_DIR_PREFIX}{build_id}":
        return False
    return all(is_safe_segment(segment) for segment in tail.split("/")) if tail else True


@dataclass(frozen=True)
class BuildParseError:
    """BuildRecord 하나를 읽지 못한 이유. current 빌드면 MANIFEST 전체가 이 status 로 실패한다."""

    status: ManifestStatus
    detail: str


def _parse_build(table: str, build_id: str,
                 record: dict[str, object]) -> BuildInfo | BuildParseError:
    """BuildRecord 하나. 실패는 `BuildParseError` — 호출부가 current 인지에 따라 처리한다."""
    if not is_safe_segment(build_id):
        return BuildParseError(
            ManifestStatus.UNSAFE_NAME,
            f"build_id is not a safe path segment — table={table} build_id={build_id!r}")
    partitions: list[Partition] = []
    raw_parts = record.get("partitions") or []
    for part in raw_parts if isinstance(raw_parts, list) else []:
        path = part.get("path") if isinstance(part, dict) else None
        if not isinstance(part, dict) or not isinstance(path, str) \
                or not _partition_path_ok(path, build_id):
            return BuildParseError(
                ManifestStatus.BAD_PARTITION,
                f"partition path malformed — table={table} build={build_id} "
                f"partition={part!r} (expected v={build_id}[/<safe segment>…])")
        try:
            n_rows = int(str(part.get("n_rows", 0)))
        except (TypeError, ValueError):
            return BuildParseError(
                ManifestStatus.BAD_PARTITION,
                f"partition n_rows is not an integer — table={table} build={build_id} "
                f"path={path} n_rows={part.get('n_rows')!r}")
        partitions.append(Partition(path=path, n_rows=n_rows,
                                    content_hash=str(part.get("content_hash") or "")))
    return BuildInfo(
        table=table,
        build_id=build_id,
        built_at_utc=str(record.get("built_at_utc", "")),
        content_hash=str(record.get("content_hash", "")),
        partitions=tuple(partitions),
    )


def parse_manifest(table: str, raw: bytes) -> ManifestView:
    """서버·로컬 MANIFEST 원문을 해석한다. 잘못된 문서는 예외가 아니라 status 로 돌려준다."""
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        return _failed(table, raw, ManifestStatus.INVALID_JSON,
                       f"MANIFEST is not JSON — table={table} bytes={len(raw)} error={error!r}")
    if not isinstance(document, dict):
        return _failed(table, raw, ManifestStatus.NOT_OBJECT,
                       f"MANIFEST must be a JSON object — table={table} "
                       f"got={type(document).__name__}")
    current = document.get("current_build")
    if not isinstance(current, str) or not current:
        return _failed(table, raw, ManifestStatus.NO_CURRENT_BUILD,
                       f"MANIFEST has no current_build — table={table} current={current!r}")
    builds: dict[str, BuildInfo] = {}
    raw_builds = document.get("builds")
    for record in raw_builds if isinstance(raw_builds, list) else []:
        if not isinstance(record, dict):
            continue
        build_id = record.get("build_id")
        if not isinstance(build_id, str):
            continue
        parsed = _parse_build(table, build_id, record)
        if isinstance(parsed, BuildInfo):
            builds[build_id] = parsed
        elif build_id == current:
            # 어댑터가 읽는 것은 current 뿐이다 — 구판본 레코드가 깨진 것은 테이블을 막지 않는다
            return _failed(table, raw, parsed.status, parsed.detail)
    if current not in builds:
        return _failed(table, raw, ManifestStatus.CURRENT_NOT_IN_BUILDS,
                       f"current_build not in builds[] — table={table} current={current} "
                       f"builds={sorted(builds)}")
    if not builds[current].partitions:
        return _failed(table, raw, ManifestStatus.BAD_PARTITION,
                       f"current build has no partitions — table={table} build={current}")
    return ManifestView(table, raw, ManifestStatus.OK, None, current, builds)


def is_table_name(name: str) -> bool:
    """`_pinned`·`_tmp`·`_failed`·`_asof`·`_sync`·`.…` 는 테이블이 아니다(workbench
    `table_builds` 와 같은 규칙). MANIFEST 가 없는 디렉토리(`fixtures`)는 호출부가 MANIFEST
    읽기 실패로 거른다. 안전하지 않은 이름도 테이블로 보지 않는다."""
    return not name.startswith(("_", ".")) and is_safe_segment(name)


def snapshot_id(builds: dict[str, str]) -> str:
    """전 테이블 `table=build` 정렬 해시 16자리 — `equity.catalog.snapshot_id`·workbench 와 같은
    규칙."""
    payload = "\n".join(f"{table}={build}" for table, build in sorted(builds.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_dir_names(names: list[str]) -> list[str]:
    """디렉토리 이름 목록에서 `v=<build>` 만 골라 build_id 순으로 돌려준다."""
    return sorted(name for name in names if name.startswith(BUILD_DIR_PREFIX))
