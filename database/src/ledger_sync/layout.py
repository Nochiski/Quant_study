"""서버·로컬이 공유하는 디렉토리 규약 — MANIFEST 해석, 테이블 판별, 파티션 경로.

EQUITY_DESIGN §2: `<layer_root>/<table>/MANIFEST.json` 의 `current_build` 가 가리키는 BuildRecord 의
`partitions[].path`(`v=<build>[/year=YYYY]`) 디렉토리만 읽는다. `v=*` glob 은 금지 — 구판본이
공존한다.
MANIFEST 자체는 서버 원문(bytes)을 그대로 보관하므로 여기서는 읽기만 하고 다시 쓰지 않는다.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum

MANIFEST_NAME = "MANIFEST.json"
BUILD_DIR_PREFIX = "v="
INCOMING_DIR = "_incoming"
SYNC_DIR = "_sync"
# 층 루트 바로 아래의 메타 파일 — 테이블이 아니지만 소비자(카탈로그 재생성·계약 대조)가 읽는다.
LAYER_META_FILES: tuple[str, ...] = ("baseline.json", "_catalog_meta.json", "_contract_meta.json")
CATALOG_META_NAME = "_catalog_meta.json"


class ManifestStatus(Enum):
    OK = "ok"
    INVALID_JSON = "invalid_json"
    NOT_OBJECT = "not_object"
    NO_CURRENT_BUILD = "no_current_build"
    CURRENT_NOT_IN_BUILDS = "current_not_in_builds"
    BAD_PARTITION = "bad_partition"


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

    def hive_columns(self) -> tuple[tuple[str, str], ...]:
        """꼬리의 `key=value` 조각들 — content_hash 재계산 때 하이브 컬럼으로 덧붙인다."""
        pairs: list[tuple[str, str]] = []
        for segment in self.tail.split("/"):
            if "=" in segment:
                key, _, value = segment.partition("=")
                pairs.append((key, value))
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
        partitions: list[Partition] = []
        for part in record.get("partitions") or []:
            path = part.get("path") if isinstance(part, dict) else None
            if not isinstance(path, str) or not path.startswith(BUILD_DIR_PREFIX):
                return _failed(table, raw, ManifestStatus.BAD_PARTITION,
                               f"partition path malformed — table={table} build={build_id} "
                               f"partition={part!r}")
            partitions.append(Partition(
                path=path,
                n_rows=int(str(part.get("n_rows", 0))),
                content_hash=str(part.get("content_hash", "")),
            ))
        builds[build_id] = BuildInfo(
            table=table,
            build_id=build_id,
            built_at_utc=str(record.get("built_at_utc", "")),
            content_hash=str(record.get("content_hash", "")),
            partitions=tuple(partitions),
        )
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
    읽기 실패로 거른다."""
    return not name.startswith(("_", "."))


def snapshot_id(builds: dict[str, str]) -> str:
    """전 테이블 `table=build` 정렬 해시 16자리 — `equity.catalog.snapshot_id`·workbench 와 같은
    규칙."""
    payload = "\n".join(f"{table}={build}" for table, build in sorted(builds.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_dir_names(names: list[str]) -> list[str]:
    """디렉토리 이름 목록에서 `v=<build>` 만 골라 build_id 순으로 돌려준다."""
    return sorted(name for name in names if name.startswith(BUILD_DIR_PREFIX))
