"""lang2 완료 정의의 퀀트 아이디어 5개가 backend 에서 hydrate·검증·미리보기까지 통과한다 (P2-08).

spec 5절 1항의 backend 수준 완료 정의다. `fixtures/strategy_documents/ideas/*.yaml` 은 레시피 빌더가
만들 문서의 정본 형태(spec D2)이고, P5-03 e2e 가 빈 문서에서 그래프 탭만으로 같은 `spec_hash` 에
도달해야 한다. 그래서 여기서는 세 가지를 본다.

1. 연결된 mock 어댑터의 필드 계약으로 compile 하면 error 가 0 건이고, 나오는 경고가 아이디어가
   의도한 것뿐이다(예: 변동성 역가중의 `strategy.risk.risk_factor_excluded`).
2. compile 결과를 실행 설정과 함께 미리보기에 넣으면 예외 없이 끝나고 엔진이 호환된다.
3. 문서가 레시피 빌더 산출 형태다 — 팩터 그래프마다 소스 하나로 시작하는 체인이고, 다중 입력
   노드의 부가 입력은 체인 밖의 새 잎이다(체인 머리 재참조 금지).

아이디어가 쓰는 필드는 실데이터(duckdb) 어댑터 선언과 같은 단위·값 타입이어야 한다. mock 에서만
통과하는 문서는 실데이터에서 단위 판정이 달라져 "검증 통과 = 실행 가능"이 깨진다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.adapters.outbound.engine_portfolio.facade.bridge import (
    BacktestEnginePortfolioAdapter,
)
from strategy_workbench.adapters.outbound.equity_duckdb._specs import FIELD_BY_ID
from strategy_workbench.adapters.outbound.equity_mock.facade.provider import (
    MockEquityDataAdapter,
)
from strategy_workbench.application.portfolio_design.facade.design import (
    PortfolioDesignService,
    PortfolioPreviewRequest,
)
from strategy_workbench.application.strategy_authoring.facade.authoring import (
    CompiledDocument,
    CompileRequest,
    StrategyAuthoringService,
)
from strategy_workbench.application.strategy_authoring.facade.ports import (
    DiagnosticSeverity,
    SourceFormat,
)
from strategy_workbench.domain.backtest.facade.environment import RunEnvironment
from strategy_workbench.domain.strategy.facade.specification import (
    StrategySpec,
    composite_factors,
    inverse_risk_factor_id,
)

IDEAS = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents" / "ideas"

# 파일 이름 → 그 아이디어가 compile 에서 내야 하는 진단 코드 전부(error 는 없어야 한다).
_EXPECTED_DIAGNOSTICS: dict[str, frozenset[str]] = {
    "momentum_12_1.yaml": frozenset(),
    "low_pbr_high_roe.yaml": frozenset(),
    "ma20_breakout.yaml": frozenset(),
    "top_trading_value.yaml": frozenset(),
    # 변동성 팩터는 합성 점수에서 빠지고 역가중에만 쓰인다는 정보 경고(spec D3 S6).
    "inverse_volatility.yaml": frozenset({"strategy.risk.risk_factor_excluded"}),
}
_SOURCE_KINDS = frozenset({"field", "constant", "parameter"})
# mock 어댑터의 세션(2024-01-02 ~ 2024-01-12) 안쪽. 미리보기 경로 전체를 태우는 것이 목적이다.
_ENVIRONMENT = RunEnvironment(
    start=date(2024, 1, 2), end=date(2024, 1, 12), universe_id="krx.common-stock"
)


@dataclass(frozen=True)
class _Node:
    node_id: str
    kind: str
    inputs: tuple[str, ...]


@pytest.fixture(scope="module")
def adapter() -> MockEquityDataAdapter:
    return MockEquityDataAdapter.demo()


@pytest.fixture(scope="module")
def authoring(adapter: MockEquityDataAdapter) -> StrategyAuthoringService:
    return StrategyAuthoringService(
        RuamelDocumentCodec(),
        factor_registry_version="ideas",
        dataset_snapshot_id=lambda: adapter.snapshot().snapshot_id,
        field_catalog=adapter,
    )


@pytest.fixture(scope="module")
def portfolio(adapter: MockEquityDataAdapter) -> PortfolioDesignService:
    return PortfolioDesignService(
        adapter,
        BacktestEnginePortfolioAdapter(),
        factor_metadata=adapter,
        factor_registry_version="ideas",
    )


def _compile(authoring: StrategyAuthoringService, name: str) -> CompiledDocument:
    source = (IDEAS / name).read_text(encoding="utf-8")
    return authoring.compile(CompileRequest(source, SourceFormat.YAML))


def _spec(authoring: StrategyAuthoringService, name: str) -> StrategySpec:
    compiled = _compile(authoring, name)
    assert compiled.spec is not None, [item.message for item in compiled.diagnostics]
    return compiled.spec


def _document_nodes(authoring: StrategyAuthoringService, name: str) -> dict[str, list[_Node]]:
    """사용자 문서의 팩터별 노드. 승격 노드(`__promote_*`)는 compile 이 붙인 것이라 뺀다."""
    graphs: dict[str, list[_Node]] = {}
    for factor in _spec(authoring, name).factors:
        nodes = []
        for node in factor.graph.nodes:
            if node.node_id.startswith("__promote_"):
                continue
            inputs = tuple(
                str(getattr(node, item))
                for item in type(node).__dataclass_fields__
                if item.endswith("_node_id")
            )
            nodes.append(_Node(node.node_id, str(node.kind), inputs))
        graphs[factor.factor_id] = nodes
    return graphs


def test_the_ideas_directory_holds_exactly_the_five_ideas() -> None:
    assert {path.name for path in IDEAS.glob("*.yaml")} == set(_EXPECTED_DIAGNOSTICS)


@pytest.mark.parametrize("name", sorted(_EXPECTED_DIAGNOSTICS))
def test_idea_compiles_against_the_connected_field_contracts(
    authoring: StrategyAuthoringService, name: str
) -> None:
    compiled = _compile(authoring, name)

    errors = [
        f"{item.code}: {item.message}"
        for item in compiled.diagnostics
        if item.severity is DiagnosticSeverity.ERROR
    ]
    assert not errors, errors
    assert compiled.spec is not None
    assert {item.code for item in compiled.diagnostics} == _EXPECTED_DIAGNOSTICS[name]


@pytest.mark.parametrize("name", sorted(_EXPECTED_DIAGNOSTICS))
def test_idea_previews_with_a_run_environment(
    authoring: StrategyAuthoringService, portfolio: PortfolioDesignService, name: str
) -> None:
    spec = _spec(authoring, name)

    preview = portfolio.preview(PortfolioPreviewRequest(spec, _ENVIRONMENT))

    assert preview.engine.compatible, preview.engine.issues
    assert preview.tape.strategy_hash


@pytest.mark.parametrize("name", sorted(_EXPECTED_DIAGNOSTICS))
def test_idea_fields_have_the_same_contract_on_the_real_data_adapter(
    adapter: MockEquityDataAdapter, authoring: StrategyAuthoringService, name: str
) -> None:
    spec = _spec(authoring, name)
    field_ids = {rule.field_id for rule in spec.eligibility.rules}
    for factor in spec.factors:
        field_ids.update(
            str(node.field_id) for node in factor.graph.nodes if node.kind == "field"
        )
    mock = {profile.field_id: profile for profile in adapter.list_fields()}

    for field_id in sorted(field_ids):
        real = FIELD_BY_ID[field_id]
        assert (mock[field_id].unit, mock[field_id].value_type) == (real.unit, real.value_type), (
            field_id
        )


@pytest.mark.parametrize("name", sorted(_EXPECTED_DIAGNOSTICS))
def test_every_factor_graph_is_a_recipe_chain(
    authoring: StrategyAuthoringService, name: str
) -> None:
    """spec D2 체인 판정: 소스 하나로 시작해 각 단계가 직전 단계를 읽고, 부가 입력은 새 잎이다.

    판정의 owner 는 frontend `recipe-projection.ts`(P5-01)다. 여기서는 fixture 가 그 규칙을
    만족하는 형태로 적혔는지만 확인한다 — 체인 머리를 두 번 읽는 문서는 고급 수준으로 가서
    P5-03 의 "빌더 산출물과 같은 hash" 단언이 깨진다.
    """
    for factor_id, nodes in _document_nodes(authoring, name).items():
        leaves = {node.node_id for node in nodes if node.kind in _SOURCE_KINDS}
        steps = [node for node in nodes if node.kind not in _SOURCE_KINDS]
        head = nodes[0]
        assert head.kind in _SOURCE_KINDS, factor_id
        tail = head.node_id
        extra_leaves: set[str] = set()
        for step in steps:
            assert tail in step.inputs, f"{factor_id}.{step.node_id} 가 직전 단계를 읽지 않는다"
            others = [item for item in step.inputs if item != tail]
            for other in others:
                assert other in leaves and other != head.node_id, (
                    f"{factor_id}.{step.node_id} 의 부가 입력 {other} 는 새 잎이어야 한다"
                )
            extra_leaves.update(others)
            tail = step.node_id
        assert extra_leaves == leaves - {head.node_id}, f"{factor_id}: 쓰이지 않는 잎"


def test_ma20_breakout_is_four_nodes_with_two_leaves_and_a_promoted_output(
    authoring: StrategyAuthoringService,
) -> None:
    """아이디어 3 의 정본 형태(spec D2): close → ma20 → 잎 close_2 → breakout(gt, close_2, ma20)."""
    (nodes,) = _document_nodes(authoring, "ma20_breakout.yaml").values()

    assert [(node.node_id, node.kind, node.inputs) for node in nodes] == [
        ("close", "field", ()),
        ("ma20", "time_series", ("close",)),
        ("close_2", "field", ()),
        ("breakout", "comparison", ("close_2", "ma20")),
    ]
    # 비교 출력(boolean)은 P2-07 승격으로 0/1 숫자 점수가 되어 compile 을 통과한다.
    (factor,) = _spec(authoring, "ma20_breakout.yaml").factors
    assert factor.graph.output_node_id.startswith("__promote_")


def test_inverse_volatility_weights_by_a_second_factor_outside_the_composite(
    authoring: StrategyAuthoringService,
) -> None:
    """아이디어 5: 알파 팩터 1개 + 변동성 팩터 1개. 변동성 하나만 두면 알파가 0개라 막힌다."""
    spec = _spec(authoring, "inverse_volatility.yaml")

    risk_factor = inverse_risk_factor_id(spec)
    assert risk_factor is not None
    assert [factor.factor_id for factor in composite_factors(spec)] == [
        factor.factor_id for factor in spec.factors if factor.factor_id != risk_factor
    ]
    assert len(composite_factors(spec)) == 1
