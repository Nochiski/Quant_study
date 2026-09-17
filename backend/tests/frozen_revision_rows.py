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
