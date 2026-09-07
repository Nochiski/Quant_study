"""equity_root 읽기 규약 — MANIFEST `current_build` 해석 · 카탈로그 meta · `snapshot_id`.

DESIGN §2 판본 규약: `<equity_root>/<table>/MANIFEST.json` 의 `current_build` → 그 BuildRecord 의
`partitions[].path` 디렉토리만 읽는다(`v=*` glob 금지 — keep=3 GC 로 구버전이 공존한다).
커널 어댑터 `backtest_engine.adapters.equity_duckdb.resolve_table` 과 같은 규약이지만 패키지 경계
(`.claude/rules/backend-package-boundary.md`: 커널 접근은 `adapters/outbound/backtest_engine` 만)
때문에 import 하지 않고 여기서 다시 쓴다. `snapshot_id` 규칙은 `equity.catalog.snapshot_id`
(전 테이블 `table=build` 정렬 해시 16자리)와 같아야 카탈로그 meta 와 대조할 수 있다.

여기서 나는 예외는 전부 환경·설정 오류(빌드 안 된 테이블, 깨진 MANIFEST)다 — 질의 시점의 도메인
실패(데이터 없음 등)는 어댑터가 포트 결과 값으로 돌려준다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

MANIFEST_NAME = "MANIFEST.json"
CATALOG_NAME = "equity.duckdb"
CATALOG_META_NAME = "_catalog_meta.json"


class EquityDuckdbSetupError(RuntimeError):
    """어댑터를 세울 수 없는 환경·설정 오류 — duckdb 미설치, 미빌드 테이블, 깨진 MANIFEST."""


@dataclass(frozen=True)
class TableBuild:
    table: str
    build_id: str
    built_at: datetime
    partitions: tuple[Path, ...]  # partitions[].path 를 절대경로로 푼 디렉토리

    def parquet_source(self) -> str:
        """이 빌드의 파티션 parquet 를 읽는 duckdb 관계식(절대경로 glob, hive 축 없음)."""
        globs = ", ".join(f"'{directory / '*.parquet'}'" for directory in self.partitions)
        return f"read_parquet([{globs}], hive_partitioning=false)"


def _manifest(equity_root: Path, table: str) -> dict[str, object]:
    path = equity_root / table / MANIFEST_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise EquityDuckdbSetupError(
            f"unreadable equity MANIFEST — root={equity_root} table={table} path={path} "
            f"error={error!r}"
        ) from error
    if not isinstance(raw, dict):
        raise EquityDuckdbSetupError(
            f"equity MANIFEST must be a JSON object — root={equity_root} table={table} "
            f"path={path} got={type(raw).__name__}"
        )
    return raw


def table_builds(equity_root: Path) -> dict[str, str]:
    """커밋된 equity 테이블 → current_build. `_`·`.` 로 시작하는 디렉토리(`_pinned` 등)는 테이블이
    아니다(`equity.catalog.table_builds` 와 같은 규칙)."""
    if not equity_root.is_dir():
        raise EquityDuckdbSetupError(f"equity root is not a directory — root={equity_root}")
    builds: dict[str, str] = {}
    for directory in sorted(equity_root.iterdir()):
        name = directory.name
        if not directory.is_dir() or name.startswith(("_", ".")):
            continue
        if not (directory / MANIFEST_NAME).exists():
            continue
        current = _manifest(equity_root, name).get("current_build")
        if isinstance(current, str) and current:
            builds[name] = current
    return builds


def snapshot_id(builds: dict[str, str]) -> str:
    """전 테이블 current_build 정렬 해시 — `equity.catalog.snapshot_id` 와 같은 규칙."""
    payload = "\n".join(f"{table}={build}" for table, build in sorted(builds.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def resolve_table(equity_root: Path, table: str) -> TableBuild:
    """`<equity_root>/<table>/MANIFEST.json` 의 current_build 파티션 디렉토리를 푼다."""
    manifest_path = equity_root / table / MANIFEST_NAME
    if not manifest_path.exists():
        raise EquityDuckdbSetupError(
            f"equity table not built — root={equity_root} table={table} manifest={manifest_path}"
        )
    raw = _manifest(equity_root, table)
    current = raw.get("current_build")
    if not isinstance(current, str) or not current:
        raise EquityDuckdbSetupError(
            f"equity table has no current_build — root={equity_root} table={table} "
            f"manifest={manifest_path}"
        )
    builds = raw.get("builds")
    record = next(
        (
            item
            for item in (builds if isinstance(builds, list) else [])
            if isinstance(item, dict) and item.get("build_id") == current
        ),
        None,
    )
    if record is None:
        raise EquityDuckdbSetupError(
            f"current_build not in builds[] — root={equity_root} table={table} build={current}"
        )
    built_at_raw = record.get("built_at_utc")
    try:
        built_at = datetime.fromisoformat(str(built_at_raw))
    except ValueError as error:
        raise EquityDuckdbSetupError(
            f"built_at_utc is not ISO-8601 — root={equity_root} table={table} build={current} "
            f"built_at_utc={built_at_raw!r}"
        ) from error
    partitions: list[Path] = []
    for part in record.get("partitions") or []:
        path = part.get("path") if isinstance(part, dict) else None
        if not isinstance(path, str):
            raise EquityDuckdbSetupError(
                f"partition without path — root={equity_root} table={table} build={current} "
                f"partition={part!r}"
            )
        directory = (equity_root / table / path).resolve()
        if not directory.is_dir():
            raise EquityDuckdbSetupError(
                f"partition directory missing — root={equity_root} table={table} "
                f"build={current} path={directory}"
            )
        partitions.append(directory)
    if not partitions:
        raise EquityDuckdbSetupError(
            f"build has no partitions — root={equity_root} table={table} build={current}"
        )
    return TableBuild(table, current, built_at, tuple(partitions))


@dataclass(frozen=True)
class CatalogState:
    """`equity.duckdb` + `_catalog_meta.json` 이 가리키는 것. `usable` 이 아니면 `reason` 이 왜인지
    말한다 — 카탈로그 없음 · meta 없음 · 테이블 판본이 meta 와 다름(stale) · 매크로 건너뜀."""

    path: Path
    usable: bool
    reason: str | None
    snapshot_id: str | None
    macros: tuple[str, ...]

    def has_macro(self, name: str) -> bool:
        return any(signature.split("(", 1)[0] == name for signature in self.macros)


def read_catalog(equity_root: Path, expected_snapshot_id: str) -> CatalogState:
    """카탈로그 파일·meta 를 읽고 현재 MANIFEST 판본(`expected_snapshot_id`)과 대조한다.

    빌드·GC 뒤 재생성되지 않은 카탈로그는 매크로 본문이 옛 `v=` 경로를 물고 있다(DESIGN §2) —
    그런 카탈로그로 조정가를 내면 조용히 옛 판본을 읽으므로 usable=False 로 막는다.
    """
    path = equity_root / CATALOG_NAME
    meta_path = equity_root / CATALOG_META_NAME
    if not path.exists():
        return CatalogState(path, False, f"catalog file missing — path={path}", None, ())
    if not meta_path.exists():
        return CatalogState(path, False, f"catalog meta missing — path={meta_path}", None, ())
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise EquityDuckdbSetupError(
            f"unreadable catalog meta — path={meta_path} error={error!r}"
        ) from error
    if not isinstance(meta, dict):
        raise EquityDuckdbSetupError(
            f"catalog meta must be a JSON object — path={meta_path} got={type(meta).__name__}"
        )
    raw_macros = meta.get("macros")
    macros = tuple(str(item) for item in raw_macros) if isinstance(raw_macros, list) else ()
    actual = meta.get("snapshot_id")
    if actual != expected_snapshot_id:
        return CatalogState(
            path,
            False,
            "catalog is stale — rebuild it (`python -m equity catalog`): "
            f"catalog_snapshot_id={actual!r} manifest_snapshot_id={expected_snapshot_id!r}",
            str(actual) if actual is not None else None,
            macros,
        )
    return CatalogState(path, True, None, expected_snapshot_id, macros)
