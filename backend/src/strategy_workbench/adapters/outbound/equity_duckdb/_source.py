"""equity_root 읽기 규약 — MANIFEST `current_build` 해석 · 카탈로그 meta · `snapshot_id`.

DESIGN §2 판본 규약: `<equity_root>/<table>/MANIFEST.json` 의 `current_build` → 그 BuildRecord 의
`partitions[].path` 디렉토리만 읽는다(`v=*` glob 금지 — keep=3 GC 로 구버전이 공존한다).
커널 어댑터 `backtest_engine.adapters.equity_duckdb.resolve_table` 과 같은 규약이지만 패키지 경계
(`.claude/rules/backend-package-boundary.md`: 커널 접근은 `adapters/outbound/backtest_engine` 만)
때문에 import 하지 않고 여기서 다시 쓴다. `snapshot_id` 규칙은 `equity.catalog.snapshot_id`
(전 테이블 `table=build` 정렬 해시 16자리)와 같아야 카탈로그 meta 와 대조할 수 있다. 이 값은
원장 판이고, 어댑터가 뒤에 필드 계약 판을 붙여 워크벤치 데이터 스냅샷 id 를 만든다(#235).

여기서 나는 예외는 전부 환경·설정 오류(빌드 안 된 테이블, 깨진 MANIFEST)다 — 질의 시점의 도메인
실패(데이터 없음 등)는 어댑터가 포트 결과 값으로 돌려준다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

MANIFEST_NAME = "MANIFEST.json"
CATALOG_NAME = "equity.duckdb"
CATALOG_META_NAME = "_catalog_meta.json"
# 카탈로그를 다시 만드는 조치 — 카탈로그 때문에 원천을 뺀 사유 문장(`catalog_*`, FIELD_MAP §3)이 이
# 문구로 조치를 알린다. 원천은 부팅 때 정해지므로 다시 만든 뒤 서버도 다시 띄워야 돌아온다.
CATALOG_REBUILD = (
    "카탈로그를 다시 만든 뒤(`ledger_sync catalog` 또는 `python -m equity catalog`) 서버를 다시 "
    "띄워야 한다"
)

logger = logging.getLogger(__name__)


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
    말한다 — 카탈로그·meta 없음(`catalog_missing`) · 파일이나 meta 손상(`catalog_unreadable`) ·
    테이블 판본이 meta 와 다름(`catalog_stale`)."""

    path: Path
    usable: bool
    reason: str | None
    snapshot_id: str | None
    macros: tuple[str, ...]
    # 부팅 때 파일에서 읽은 (매크로 이름, 본문) — parquet 경로는 테이블 이름으로 접었다. 필드 계약
    # 판의 입력이다(#235).
    bodies: tuple[tuple[str, str], ...] = ()

    def has_macro(self, name: str) -> bool:
        return any(signature.split("(", 1)[0] == name for signature in self.macros)


def unreadable_catalog(path: Path, unreadable: Path, error: Exception) -> CatalogState:
    """카탈로그 파일이나 meta(`unreadable`)를 못 읽어 쓸 수 없는 카탈로그(`catalog_unreadable`).

    다시 만들어야 풀리는 손상이라 카탈로그가 없을 때처럼 매크로를 읽는 원천을 모두 빼고 경고한다
    (#247·#278). 사유는 질의 거절 상세로 사용자에게 가므로 파일 이름과 오류 종류만 싣고, 경로와
    원문은 경고 로그에만 남긴다(#163).
    """
    reason = (
        "카탈로그 파일을 읽을 수 없어 카탈로그 매크로를 읽는 원천의 필드를 뺀다 — "
        f"{CATALOG_REBUILD} (catalog_unreadable) — file={unreadable.name} "
        f"error={type(error).__name__}"
    )
    logger.warning(f"{reason} path={unreadable} detail={error!r}")
    return CatalogState(path, False, reason, None, ())


def read_catalog(equity_root: Path, expected_snapshot_id: str) -> CatalogState:
    """카탈로그 파일·meta 를 읽고 현재 MANIFEST 판본(`expected_snapshot_id`)과 대조한다.

    빌드·GC 뒤 재생성되지 않은 카탈로그는 매크로 본문이 옛 `v=` 경로를 물고 있다(DESIGN §2) —
    그런 카탈로그로 조정가를 내면 조용히 옛 판본을 읽으므로 usable=False 로 막는다.
    `reason` 은 질의 거절 상세로 사용자에게 가므로 루트 기준 파일 이름만 싣는다(#163). 쓸 수 없는
    카탈로그는 매크로를 읽는 원천이 모두 빠지므로 사유를 만든 여기서 경고도 한 번 남긴다 — 부팅
    로그에 남아야 재생성한다(#292 리뷰 P2-2).
    """
    path = equity_root / CATALOG_NAME
    meta_path = equity_root / CATALOG_META_NAME
    for required in (path, meta_path):
        if not required.exists():
            reason = (
                "카탈로그 파일이 없어 카탈로그 매크로를 읽는 원천의 필드를 뺀다 — "
                f"{CATALOG_REBUILD} (catalog_missing) — file={required.name}"
            )
            logger.warning(f"{reason} path={required}")
            return CatalogState(path, False, reason, None, ())
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            raise ValueError(f"catalog meta must be a JSON object — got {type(meta).__name__}")
    except (OSError, ValueError) as error:
        # meta 는 카탈로그가 어느 판으로 만들어졌는지(`snapshot_id`) 대조하는 근거일 뿐이고
        # 워크벤치 스냅샷 id 는 MANIFEST 에서 센다. 그래서 못 읽으면 meta 가 없을 때처럼 카탈로그만
        # 쓸 수 없는 것으로 두면 된다(#278).
        return unreadable_catalog(path, meta_path, error)
    raw_macros = meta.get("macros")
    macros = tuple(str(item) for item in raw_macros) if isinstance(raw_macros, list) else ()
    actual = meta.get("snapshot_id")
    if actual != expected_snapshot_id:
        reason = (
            "카탈로그가 원장 테이블 판본과 달라(낡음) 카탈로그 매크로를 읽는 원천의 필드를 뺀다 — "
            f"{CATALOG_REBUILD} (catalog_stale) — catalog_snapshot_id={actual!r} "
            f"manifest_snapshot_id={expected_snapshot_id!r}"
        )
        logger.warning(f"{reason} path={path}")
        return CatalogState(
            path, False, reason, str(actual) if actual is not None else None, macros
        )
    return CatalogState(path, True, None, expected_snapshot_id, macros)
