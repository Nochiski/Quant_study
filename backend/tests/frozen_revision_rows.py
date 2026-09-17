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
) -> dict[str, FrozenRow]:
    """Create the schema through the repository, then insert 1.0 rows with raw SQL.

    Overrides let a test tamper with exactly one column; the defaults are internally consistent.
    """
    open_repository(path).close()
    stored_json = frozen_spec_json() if spec_json is None else spec_json
    stored_hash = FROZEN_SPEC_HASH if spec_hash is None else spec_hash
    rows: dict[str, FrozenRow] = {}
    with sqlite3.connect(path) as connection:
        if document_row:
            text = frozen_source_text()
            text_hash = _sha256(text) if source_hash is None else source_hash
            rows["document"] = FrozenRow("frozen-doc", 1, stored_json, stored_hash, text, text_hash)
            connection.execute(
                "INSERT INTO strategy_heads (strategy_id, latest_revision) VALUES (?, ?)",
                ("frozen-doc", 1),
            )
            connection.execute(
                "INSERT INTO strategy_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "frozen-doc",
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
            rows["legacy"] = FrozenRow("frozen-legacy", 1, stored_json, stored_hash, None, None)
            connection.execute(
                "INSERT INTO strategy_heads (strategy_id, latest_revision) VALUES (?, ?)",
                ("frozen-legacy", 1),
            )
            connection.execute(
                "INSERT INTO strategy_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "frozen-legacy",
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
