"""저장된 schema 1.1 revision 읽기 계약 (P2-03 리뷰 P2-01·P2-02).

1.0 은 P1-03 이전 이력이고 실 DB 에 남아 있는 은퇴 row 는 사실상 전부 1.1 이다. 즉 이 PR 이
"목록·이력·문서 조회는 그대로 동작한다"고 선언한 바로 그 버전이 여기서 고정된다.

`_record_codec.py` 의 `_decode_frozen_spec` 이 1.1 row 를 1.1 → 1.2 단계만 태워 읽는 경로가
대상이다(P2-09 버전 디스패치). 그 경로가 깨지면 목록·이력 화면이 통째로 500 이 된다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

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
from strategy_workbench.domain.strategy.facade.specification import SignalNormalization
from tests import frozen_revision_rows
from tests.frozen_revision_rows import (
    E2E_SCHEMA_ENV,
    E2E_SUFFIX_ENV,
    FIXTURES,
    open_repository,
    seed_retired_1_1_document_row,
    seed_retired_1_1_row,
    source_spec_hash,
)

RETIRED_VERSION = "1.1"
SEED_CLI = Path(frozen_revision_rows.__file__)


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


def test_a_retired_1_1_row_keeps_its_raw_weighted_sum_meaning(tmp_path: Path) -> None:
    """P2-04 1차 리뷰 P3: 1.1 row 를 1.2 기본값(`rank`)으로 읽으면 설명 문장이 저장된 적 없는
    의미를 말한다. 1.1 → 1.2 단계가 `normalization: none` 을 명시해 그 revision 의 실제 의미
    (원시값 가중 합)로 복원한다."""
    path = tmp_path / "meaning.sqlite3"
    row = seed_retired_1_1_row(path)

    with open_repository(path) as repository:
        record = repository.get(row.strategy_id, 1)

    assert record.spec.signal.normalization is SignalNormalization.NONE


def test_a_retired_row_with_a_saved_reference_node_fails_closed(tmp_path: Path) -> None:
    """1.2 에 없는 `saved_*` 노드는 조용히 지우지 않는다 — 그 row 는 무결성 오류로 읽기를 멈춘다.

    1.1 에서도 실행이 거부되던 노드라 실행 결과를 잃지는 않는다. 조용히 지운 spec 을 보이면
    사용자가 저장한 적 없는 그래프가 그 revision 의 사실로 화면에 뜬다.
    """
    document = yaml.safe_load((FIXTURES / "quality_momentum.v1_1.yaml").read_text(encoding="utf-8"))
    document["factors"][0]["graph"]["nodes"].append({"kind": "saved_factor", "node_id": "ref"})
    path = tmp_path / "saved-node.sqlite3"
    row = seed_retired_1_1_row(path, strategy_id="saved-node", document=document)

    with pytest.raises(StrategyRepositoryStorageError, match="upgrade_unsupported_node"):
        with open_repository(path) as repository:
            repository.get(row.strategy_id, 1)


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


def test_a_retired_1_1_document_row_serves_its_saved_yaml_for_upgrade(tmp_path: Path) -> None:
    """원문이 함께 저장된 1.1 row 는 generated source 가 아니라 그 YAML 원문을 돌려준다.

    revision 화면의 "현재 버전으로 업그레이드"는 이 원문을 upgrade API 에 보내는 경로다. e2e·매뉴얼
    캡처가 CLI(`STRATEGY_WORKBENCH_E2E_SEED_SCHEMA=1.1`)로 심는 row 가 이것이다.
    """
    path = tmp_path / "document.sqlite3"
    row = seed_retired_1_1_document_row(path)
    authoring = _authoring()

    with open_repository(path) as repository:
        record = repository.get(row.strategy_id, 1)
        documents = StrategyDocumentService(authoring, repository, new_id=lambda: "unused")
        document = documents.get(row.strategy_id, 1)

    assert record.requires_upgrade is True
    assert record.spec.identity.schema_version == RETIRED_VERSION
    assert not document.generated and document.format is SourceFormat.YAML
    assert document.source == row.source_text
    assert document.source.startswith('schema_version: "1.1"')
    assert document.requires_upgrade and document.schema_version == RETIRED_VERSION


def _run_seed_cli(path: Path, schema: str) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, E2E_SUFFIX_ENV: "-cli", E2E_SCHEMA_ENV: schema}
    return subprocess.run(
        [sys.executable, str(SEED_CLI), str(path)],
        cwd=SEED_CLI.parents[1],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


@pytest.mark.parametrize(
    ("schema", "expected"),
    [("", ["frozen-doc-cli", "frozen-legacy-cli"]), ("1.1", ["retired-1-1-cli"])],
)
def test_the_seed_cli_picks_the_retired_version_from_its_environment(
    tmp_path: Path, schema: str, expected: list[str]
) -> None:
    """e2e 는 1.0 row 둘과 1.1 row 하나를, 매뉴얼 촬영은 `1.1`(원문 있는 row 하나)을 CLI 로 심는다.

    환경 변수 분기가 함수 계약과 따로 놀지 않게 CLI 를 직접 돌린다(#263 리뷰 P3-5).
    """
    path = tmp_path / "cli.sqlite3"
    completed = _run_seed_cli(path, schema)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == ", ".join(f"{strategy_id}@1" for strategy_id in expected)
    with open_repository(path) as repository:
        assert all(repository.get(strategy_id, 1).requires_upgrade for strategy_id in expected)


def test_the_seed_cli_refuses_an_unknown_retired_version(tmp_path: Path) -> None:
    completed = _run_seed_cli(tmp_path / "cli.sqlite3", "1.2")

    assert completed.returncode != 0
    assert f"{E2E_SCHEMA_ENV} must be 1.0 or 1.1" in completed.stderr


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
