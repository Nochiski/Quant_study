"""schema 1.0 동결 revision row를 SQLite에 직접 심는 테스트 헬퍼 (spec D2, P1-03).

1.0 인코더는 더 이상 없으므로 저장소 코드가 아니라 테스트가 raw SQL로 row를 만든다. 값은 P0-01
당시의 canonical payload golden(`canonical_payload.v1_0.json`)과 1.0 YAML fixture에서 그대로
가져온다.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.adapters.outbound.strategy_sqlite.facade.repository import (
    SQLiteStrategyRepository,
)
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompileRequest,
    StrategyAuthoringService,
)
from strategy_workbench.domain.strategy.facade.document import SourceFormat
from strategy_workbench.domain.strategy.facade.specification import canonical_payload_json

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "strategy_documents"
FROZEN_SPEC_HASH = "9eb6872a3ca250dfb78b0887e5b236a98b24fb2ccdf2d6af0540218d49e998fe"
FROZEN_CREATED_AT = "2026-09-04T00:00:00.000000+00:00"


@dataclass(frozen=True)
class FrozenRow:
    strategy_id: str
    revision: int
    spec_json: str
    spec_hash: str
    source_text: str | None
    source_hash: str | None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def frozen_spec_json() -> str:
    payload = json.loads((FIXTURES / "canonical_payload.v1_0.json").read_text(encoding="utf-8"))
    return canonical_payload_json(payload)


def frozen_source_text() -> str:
    return (FIXTURES / "quality_momentum.v1_0.yaml").read_text(encoding="utf-8")


def retired_1_1_spec_json() -> str:
    """보존된 1.1 원본을 그 버전의 canonical payload 로 만든 `spec_json`.

    1.0 은 P1-03 이전 이력이고 실 DB 에 남아 있는 은퇴 row 는 사실상 전부 1.1 이다. 그 버전의
    모델은 더 이상 없으므로(1.2 가 `data`·`execution` 을 지웠다) 저장 시점에 기록됐을 바이트를
    fixture 에서 직접 만든다.
    """
    document = yaml.safe_load((FIXTURES / "quality_momentum.v1_1.yaml").read_text(encoding="utf-8"))
    return canonical_payload_json(document)


def retired_spec_json(schema_version: str) -> str:
    """같은 문서를 주어진 버전으로 각인한 `spec_json`.

    `decode_record` 가 payload 의 `schema_version` 과 컬럼 값이 같은지 먼저 보므로, 미지 버전
    거절을 시험하려면 둘 다 그 값이어야 한다 — 그래야 검사가 `_decode_frozen_spec` 까지 간다.
    """
    document = yaml.safe_load((FIXTURES / "quality_momentum.v1_1.yaml").read_text(encoding="utf-8"))
    document["schema_version"] = schema_version
    return canonical_payload_json(document)


def seed_retired_1_1_row(
    path: Path, *, strategy_id: str = "frozen-1-1", schema_version: str = "1.1"
) -> FrozenRow:
    """은퇴 버전 row 하나를 raw SQL 로 심는다(기본은 1.1, 미지 버전 거절 테스트가 값을 바꾼다).

    `source_*` 세 컬럼은 비운다 — 1.1 row 의 원문 검증은 저장 시점에 끝났고, 여기서 고정하려는
    것은 "저장된 은퇴 버전 row 가 현재 버전으로 복원된다" 하나다.
    """
    open_repository(path).close()
    spec_json = retired_spec_json(schema_version)
    spec_hash = _sha256(spec_json)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO strategy_heads (strategy_id, latest_revision) VALUES (?, ?)",
            (strategy_id, 1),
        )
        connection.execute(
            "INSERT INTO strategy_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                strategy_id,
                1,
                schema_version,
                spec_json,
                spec_hash,
                None,
                None,
                None,
                "legacy_json",
                FROZEN_CREATED_AT,
                None,
            ),
        )
        connection.commit()
    return FrozenRow(strategy_id, 1, spec_json, spec_hash, None, None)


def source_spec_hash(source: str, format: SourceFormat) -> str:
    authoring = StrategyAuthoringService(
        RuamelDocumentCodec(), factor_registry_version="r", dataset_snapshot_id=lambda: "s"
    )
    compiled = authoring.compile(CompileRequest(source, format))
    if compiled.spec_hash is None:
        raise ValueError("test source does not compile")
    return compiled.spec_hash


def open_repository(path: Path) -> SQLiteStrategyRepository:
    return SQLiteStrategyRepository(path, source_spec_hash=source_spec_hash)


def seed_frozen_rows(
    path: Path,
    *,
    document_row: bool = True,
    legacy_row: bool = True,
    spec_json: str | None = None,
    spec_hash: str | None = None,
    source_hash: str | None = None,
    id_suffix: str = "",
) -> dict[str, FrozenRow]:
    """Create the schema through the repository, then insert 1.0 rows with raw SQL.

    Overrides let a test tamper with exactly one column; the defaults are internally consistent.
    `id_suffix`는 e2e가 시도마다 고유한 전략 id(`frozen-doc-<suffix>`)를 심을 때 쓴다.
    """
    open_repository(path).close()
    stored_json = frozen_spec_json() if spec_json is None else spec_json
    stored_hash = FROZEN_SPEC_HASH if spec_hash is None else spec_hash
    rows: dict[str, FrozenRow] = {}
    document_id = f"frozen-doc{id_suffix}"
    legacy_id = f"frozen-legacy{id_suffix}"
    with sqlite3.connect(path) as connection:
        if document_row:
            text = frozen_source_text()
            text_hash = _sha256(text) if source_hash is None else source_hash
            rows["document"] = FrozenRow(document_id, 1, stored_json, stored_hash, text, text_hash)
            connection.execute(
                "INSERT INTO strategy_heads (strategy_id, latest_revision) VALUES (?, ?)",
                (document_id, 1),
            )
            connection.execute(
                "INSERT INTO strategy_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    1,
                    "1.0",
                    stored_json,
                    stored_hash,
                    "yaml",
                    text,
                    text_hash,
                    "document",
                    FROZEN_CREATED_AT,
                    None,
                ),
            )
        if legacy_row:
            rows["legacy"] = FrozenRow(legacy_id, 1, stored_json, stored_hash, None, None)
            connection.execute(
                "INSERT INTO strategy_heads (strategy_id, latest_revision) VALUES (?, ?)",
                (legacy_id, 1),
            )
            connection.execute(
                "INSERT INTO strategy_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    legacy_id,
                    1,
                    "1.0",
                    stored_json,
                    stored_hash,
                    None,
                    None,
                    None,
                    "legacy_json",
                    FROZEN_CREATED_AT,
                    None,
                ),
            )
        connection.commit()
    return rows


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


E2E_DB_ENV = "STRATEGY_WORKBENCH_E2E_DB"
E2E_SUFFIX_ENV = "STRATEGY_WORKBENCH_E2E_SEED_SUFFIX"


if __name__ == "__main__":
    # frontend Playwright(P2-02)가 격리 SQLite에 동결 row를 심을 때 부른다. 경로는 인자 하나 또는
    # `STRATEGY_WORKBENCH_E2E_DB`(shell 인자 분리를 피하려는 e2e 호출부). revision은 트리거로
    # 불변이라 재시도가 같은 DB를 다시 쓰면 지울 수 없으므로, e2e는 시도마다
    # `STRATEGY_WORKBENCH_E2E_SEED_SUFFIX`로 고유한 전략 id를 심는다(같은 id를 두 번 심으면
    # 여기서도 fail-closed).
    import os
    import sys

    argument = sys.argv[1] if len(sys.argv) == 2 else os.environ.get(E2E_DB_ENV)
    if len(sys.argv) > 2 or not argument:
        raise SystemExit(f"usage: frozen_revision_rows.py <sqlite path> (or {E2E_DB_ENV}=<path>)")
    seeded = seed_frozen_rows(Path(argument), id_suffix=os.environ.get(E2E_SUFFIX_ENV, ""))
    print(", ".join(f"{row.strategy_id}@{row.revision}" for row in seeded.values()))
