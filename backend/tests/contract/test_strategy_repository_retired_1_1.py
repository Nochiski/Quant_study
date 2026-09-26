"""저장된 schema 1.1 revision 읽기 계약 (P2-03 리뷰 P2-01·P2-02).

1.0 은 P1-03 이전 이력이고 실 DB 에 남아 있는 은퇴 row 는 사실상 전부 1.1 이다. 즉 이 PR 이
"목록·이력·문서 조회는 그대로 동작한다"고 선언한 바로 그 버전이 여기서 고정된다.

`_record_codec.py` 의 `_decode_frozen_spec` 이 1.0 step 을 건너뛰고 실행 설정 제거(1.1 → 1.2)만
태우는 가지가 대상이다. 그 가지를 지워도 초록이면 P2-09 가 `UPGRADE_STEPS` 를 버전 디스패치로
바꿀 때 목록·이력 화면이 통째로 500 이 되는 회귀가 테스트 없이 통과한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.adapters.outbound.strategy_sqlite.facade.repository import (
    StrategyRepositoryStorageError,
)
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    StrategyAuthoringService,
    StrategyDocumentService,
)
from strategy_workbench.application.strategy_design.facade.ports import PageRequest
from strategy_workbench.domain.strategy.facade.document import (
    CURRENT_SCHEMA_VERSION,
    SourceFormat,
)
from tests.frozen_revision_rows import (
    open_repository,
    seed_retired_1_1_row,
    source_spec_hash,
)

RETIRED_VERSION = "1.1"


def _authoring() -> StrategyAuthoringService:
    return StrategyAuthoringService(
        RuamelDocumentCodec(),  # pyright: ignore[reportArgumentType]  # reason: 테스트 이중체
        factor_registry_version="r",
        dataset_snapshot_id=lambda: "s",
    )


def test_a_retired_1_1_row_is_restored_under_the_current_model(tmp_path: Path) -> None:
    """실행 설정만 벗겨 현재 버전으로 복원하고, 동결 표식은 저장된 버전 그대로 둔다."""
    path = tmp_path / "retired.sqlite3"
    row = seed_retired_1_1_row(path)

    with open_repository(path) as repository:
        record = repository.get(row.strategy_id, 1)

    assert record.spec.identity.schema_version == RETIRED_VERSION
    assert record.requires_upgrade is True
    assert record.spec_hash == row.spec_hash
    # 문서에서 실행 설정이 벗겨졌는지: 1.2 모델에는 그 자리가 없고 팩터는 그대로 남는다.
    assert [factor.factor_id for factor in record.spec.factors] == ["momentum"]
    assert record.spec.risk.max_name_weight == 0.05
    assert not hasattr(record.spec, "data") and not hasattr(record.spec, "execution")
    assert not hasattr(record.spec.factors[0].graph, "missing_policy")


def test_list_history_and_document_views_stay_green_on_a_retired_1_1_row(tmp_path: Path) -> None:
    """목록·이력·문서 조회 세 화면이 은퇴 row 하나로 500 이 되지 않는다."""
    path = tmp_path / "views.sqlite3"
    row = seed_retired_1_1_row(path)
    authoring = _authoring()

    with open_repository(path) as repository:
        strategies = repository.list_strategies(PageRequest())
        history = repository.history(row.strategy_id, PageRequest())
        documents = StrategyDocumentService(authoring, repository, new_id=lambda: "unused")
        document = documents.get(row.strategy_id, 1)

    assert [item.strategy_id for item in strategies.items] == [row.strategy_id]
    assert [item.revision for item in history.items] == [1]
    assert all(item.requires_upgrade for item in history.items)
    # 원문이 없는 row 라 generated source 를 만들어 주고, 그것은 현재 버전이다.
    assert document.generated and document.format is SourceFormat.JSON
    assert f'"schema_version": "{CURRENT_SCHEMA_VERSION}"' in document.source
    assert document.requires_upgrade and document.schema_version == RETIRED_VERSION
    # generated source 는 그대로 컴파일된다 — hash 는 저장된 1.1 hash 와 다르다(문서가 달라졌다).
    recompiled = source_spec_hash(document.source, SourceFormat.JSON)
    assert recompiled != row.spec_hash


@pytest.mark.parametrize("schema_version", ["1.3", "9.9"])
def test_an_unknown_schema_version_row_is_refused_instead_of_silently_reinterpreted(
    tmp_path: Path, schema_version: str
) -> None:
    """P2-03 리뷰 P2-02: 알려진 은퇴 버전이 아니면 현재 모델로 해석하지 않는다.

    `"1.3"` 은 더 새 backend 가 쓴 row 를 구 backend 가 읽는 다운그레이드, `"9.9"` 는 손상·수기
    편집이다. 둘 다 현재 모델로 읽으면 사용자가 저장한 적 없는 값이 그 revision 의 사실로 화면에
    뜬다 — 미래 버전이 키를 **지우거나 의미를 바꿨다면** `structure.unknown_key` 도 잡지 못하고,
    `spec_hash` 검증은 변환 전에 끝나 이 변형을 못 본다.
    """
    path = tmp_path / f"unknown-{schema_version}.sqlite3"
    row = seed_retired_1_1_row(path, strategy_id="future", schema_version=schema_version)

    with pytest.raises(StrategyRepositoryStorageError, match="neither current nor a known retired"):
        with open_repository(path) as repository:
            repository.get(row.strategy_id, 1)
