"""실행 설정(`RunEnvironment`)의 런타임 JSON Schema (WORKFLOW P2-01).

전략 authoring 문서 스키마(`strategy_document_schema`, owner `domain/strategy`)와는 다른
산출물이다. 표기법만 같은 빌더(`dataclass_json_schema`)에서 오고, 내용의 owner 는
`RunEnvironment` 를 가진 이 노드다. 여기서 필드 이름·기본값·enum 을 손으로 적지 않는다 —
전부 dataclass 에서 유도하므로 모델과 스키마가 갈라질 수 없다.
"""

from __future__ import annotations

from typing import Any

from strategy_workbench.domain.strategy.facade.schema import dataclass_json_schema

from ._canonical import canonical_json_hash
from ._models import RUN_ENVIRONMENT_CONSTRAINTS, RunEnvironment

RUN_ENVIRONMENT_SCHEMA_ID = "urn:strategy-workbench:run-environment:1"


def run_environment_schema() -> dict[str, Any]:
    """실행 설정 패널이 필드·기본값·enum·범위를 읽는 JSON Schema(2020-12).

    범위는 `RunEnvironment.__post_init__` 이 쓰는 것과 **같은 제약 행**에서 온다. 손으로 적은
    수치가 없으므로 검증이 거부하는 값을 스키마가 허용하는 일이 생길 수 없다.
    """
    return dataclass_json_schema(
        RunEnvironment,
        schema_id=RUN_ENVIRONMENT_SCHEMA_ID,
        constraints={f"/{name}": row for name, row in RUN_ENVIRONMENT_CONSTRAINTS.items()},
    )


def run_environment_schema_hash(schema: dict[str, Any]) -> str:
    """canonical 스키마 JSON 의 sha256. HTTP ETag 로 그대로 쓴다."""
    return canonical_json_hash(schema)
