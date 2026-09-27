"""은퇴 schema 문서 업그레이드 — 버전 디스패치 체인 (spec D3·D7, P1-03 → P2-09).

1.0 문서는 1.0 → 1.1 → 1.2 를 차례로 타고, 1.1 문서는 1.1 → 1.2 만 탄다. 단계마다 그 단계의
목표 버전을 찍고, 그 단계가 없애야 하는 옛 모양이 남았으면 거절한다.
"""

from __future__ import annotations

import copy
import json
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
import yaml
from ruamel.yaml import YAML

from strategy_workbench.domain.strategy import _upgrade as _upgrade_module
from strategy_workbench.domain.strategy.facade.document import (
    CURRENT_SCHEMA_VERSION,
    FROZEN_SCHEMA_VERSIONS,
    UPGRADE_CHAIN,
    UPGRADE_STEPS,
    UPGRADE_WARNING_CODES,
    NotUpgradeableDocumentError,
    UpgradeUnsupportedNodeError,
    UpgradeWarning,
    apply_upgrade_steps,
    hydrate_strategy_document,
    is_frozen_schema_version,
    is_upgradeable_document,
    legacy_shape_hints,
    upgrade_document,
)
from strategy_workbench.domain.strategy.facade.specification import (
    SignalNormalization,
    StrategyIdentity,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"
DRAFT = StrategyIdentity("draft", 0)
V1_0, V1_1 = UPGRADE_CHAIN[0], UPGRADE_CHAIN[1]


def _yaml(name: str) -> dict[str, Any]:
    loaded = yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _tree(document: dict[str, Any], *, until: str | None = None) -> dict[str, Any]:
    """테스트 편의: 변환 결과 tree 를 dict[str, Any] 로 다룬다(프로덕션 시그니처는 object 값)."""
    tree = upgrade_document(document, until=until).tree
    assert isinstance(tree, dict)
    return tree


def _current_of(name: str) -> dict[str, Any]:
    """1.1 fixture 의 1.2 기대 tree.

    실행 설정 세 자리를 빼고 1.1 의미(원시값 가중 합)를 명시한다.
    """
    expected = _yaml(name)
    expected.pop("data")
    expected.pop("execution")
    for factor in expected["factors"]:
        factor["graph"].pop("missing_policy", None)
    signal = expected.setdefault("signal", {})
    signal["normalization"] = SignalNormalization.NONE.value
    expected["schema_version"] = CURRENT_SCHEMA_VERSION
    return expected


def test_the_chain_is_keyed_by_from_version_and_ends_at_the_current_version() -> None:
    """동결 집합은 체인 키에서 유도된다.

    단계 검증 조건 맵은 같은 키 집합이어야 한다(감사 NB-2(c)).
    """
    assert tuple(UPGRADE_STEPS) == UPGRADE_CHAIN[:-1]
    assert set(_upgrade_module._STAGE_LEFTOVERS) == set(UPGRADE_STEPS)  # pyright: ignore[reportPrivateUsage]  # reason: 두 버전 맵의 키 동일성을 고정한다
    assert UPGRADE_CHAIN[-1] == CURRENT_SCHEMA_VERSION
    assert frozenset(UPGRADE_STEPS) == FROZEN_SCHEMA_VERSIONS
    assert CURRENT_SCHEMA_VERSION not in FROZEN_SCHEMA_VERSIONS


def test_a_1_0_document_climbs_the_whole_chain_to_a_hydratable_current_document() -> None:
    outcome = upgrade_document(_yaml("quality_momentum.v1_0.yaml"))

    assert outcome.source_version == V1_0
    assert outcome.tree == {**_current_of("quality_momentum.v1_1.yaml")}
    hydrated = hydrate_strategy_document(outcome.tree, identity=DRAFT)
    assert hydrated.ok, hydrated.issues
    assert hydrated.spec is not None
    assert hydrated.spec.signal.normalization is SignalNormalization.NONE


def test_a_1_1_document_takes_only_the_last_stage() -> None:
    outcome = upgrade_document(_yaml("quality_momentum.v1_1.yaml"))

    assert outcome.source_version == V1_1
    assert outcome.tree == _current_of("quality_momentum.v1_1.yaml")
    assert hydrate_strategy_document(outcome.tree, identity=DRAFT).ok


def test_the_1_0_stage_stops_at_its_own_target_version() -> None:
    """중간 단계는 자기 목표 버전을 찍는다 — 1.0 step 이 곧장 현재 버전을 찍으면 "1.2 라고 적혔지만
    `data`·`execution` 이 남은 문서"가 나와 중간 검증이 사라진다(spec D7)."""
    intermediate = _tree(_yaml("quality_momentum.v1_0.yaml"), until=V1_1)

    assert intermediate == {**_yaml("quality_momentum.v1_1.yaml"), "signal": {}}
    assert intermediate["schema_version"] == V1_1
    # 중간 산출물은 은퇴 버전이라 현재 모델로는 hydrate 되지 않는다.
    hydrated = hydrate_strategy_document(intermediate, identity=DRAFT)
    assert [issue.code for issue in hydrated.issues] == ["structure.unsupported_schema_version"]


def test_climbing_in_two_hops_equals_climbing_at_once() -> None:
    """1.0 → 1.1 결과를 다시 올린 것과 1.0 을 한 번에 올린 것이 같다(경로 무관)."""
    document = _yaml("quality_momentum.v1_0.yaml")

    assert upgrade_document(_tree(document, until=V1_1)).tree == upgrade_document(document).tree


def test_the_1_1_stage_moves_execution_settings_to_the_outcome() -> None:
    """지운 실행 설정은 버리지 않고 결과의 `environment` 로 돌려준다(spec D7) — 원문 값 그대로."""
    outcome = upgrade_document(_yaml("quality_momentum.v1_1.yaml"))

    assert outcome.environment is not None
    assert outcome.environment.data_section == {
        "market": "KRX",
        "start": "2021-01-01",
        "end": "2026-08-31",
        "universe_id": "krx.common-stock",
        "frequency": "daily",
    }
    assert outcome.environment.execution_section == {"timing": "next_open", "fee_bps": 15.0}
    assert outcome.environment.missing_policy == "drop"
    assert outcome.environment.missing_policy_pointer == "/factors/0/graph/missing_policy"
    assert outcome.warnings == ()


def test_a_stop_before_the_1_1_stage_moves_nothing() -> None:
    assert upgrade_document(_yaml("quality_momentum.v1_0.yaml"), until=V1_1).environment is None


def test_differing_missing_policies_use_the_first_and_warn() -> None:
    """spec D7: 팩터별 정책이 다르면 첫 값 + warning. 실행 설정의 결측 처리는 전략 전체에 하나다."""
    document = _yaml("quality_momentum.v1_1.yaml")
    second = copy.deepcopy(document["factors"][0])
    second["factor_id"] = "momentum_zero"
    second["graph"]["missing_policy"] = "zero"
    document["factors"].append(second)

    outcome = upgrade_document(document)

    assert outcome.environment is not None and outcome.environment.missing_policy == "drop"
    assert [(w.code, w.pointer) for w in outcome.warnings] == [
        (
            "strategy_document.upgrade_missing_policy_conflict",
            "/factors/1/graph/missing_policy",
        )
    ]
    assert "used='drop'" in outcome.warnings[0].message
    assert all("missing_policy" not in f["graph"] for f in _tree(document)["factors"])


def test_factor_score_weighting_warns_that_weights_may_differ_from_1_1() -> None:
    """P2-04 결정 5: `factor_score` 는 선정은 같지만 목표 비중이 1.1 과 다를 수 있다."""
    document = _yaml("quality_momentum.v1_1.yaml")
    document["portfolio"]["weighting"] = "factor_score"

    outcome = upgrade_document(document)

    assert [(w.code, w.pointer) for w in outcome.warnings] == [
        ("strategy_document.upgrade_weighting_rule_changed", "/portfolio/weighting")
    ]


def test_every_warning_code_is_registered() -> None:
    with pytest.raises(ValueError, match="no owner"):
        UpgradeWarning("strategy_document.made_up", "", "")  # pyright: ignore[reportArgumentType]  # reason: 런타임 게이트 확인
    assert "strategy_document.upgrade_environment_unavailable" in UPGRADE_WARNING_CODES


def test_a_missing_signal_section_is_inserted_in_model_order() -> None:
    """1.1 템플릿에는 `signal` 이 없다. 새 섹션은 모델 순서 자리(`factors` 뒤)에 들어간다."""
    tree = _tree(_yaml("quality_momentum.v1_1.yaml"))

    keys = list(tree)
    assert keys.index("signal") == keys.index("factors") + 1
    assert keys[0] == "schema_version"


def test_an_explicit_normalization_is_kept() -> None:
    """작성자가 이미 적은 값은 덮지 않는다 — 기본값을 채우거나 지우지 않는다는 규칙 그대로."""
    document = _yaml("quality_momentum.v1_1.yaml")
    document["signal"] = {"normalization": "zscore"}

    assert _tree(document)["signal"] == {"normalization": "zscore"}


@pytest.mark.parametrize("kind", ["saved_factor", "saved_subgraph"])
def test_saved_reference_nodes_refuse_the_upgrade(kind: str) -> None:
    """spec D7: 실행 경로가 원래 없던 노드라 잃는 것이 없다. 조용히 지우지 않고 거절한다."""
    document = _yaml("quality_momentum.v1_1.yaml")
    document["factors"][0]["graph"]["nodes"].append({"kind": kind, "node_id": "ref"})

    with pytest.raises(UpgradeUnsupportedNodeError) as info:
        upgrade_document(document)

    assert info.value.pointer == "/factors/0/graph/nodes/2/kind"
    assert info.value.code == "strategy_document.upgrade_unsupported_node"
    assert kind in str(info.value)


def test_upgrade_does_not_mutate_its_input() -> None:
    document = _yaml("quality_momentum.v1_0.yaml")
    snapshot = copy.deepcopy(document)

    upgrade_document(document)

    assert document == snapshot


def test_the_output_is_not_upgradeable_again() -> None:
    """멱등: 업그레이드 결과는 현재 버전이고 옛 모양이 없어서 다시 올릴 것이 없다."""
    upgraded = _tree(_yaml("quality_momentum.v1_0.yaml"))

    assert not is_upgradeable_document(upgraded)
    with pytest.raises(NotUpgradeableDocumentError, match="already current"):
        upgrade_document(upgraded)


def test_frozen_means_any_version_other_than_current() -> None:
    """Phase 1 감사 DEFECT-P1X-001·003: 동결 술어는 `!= CURRENT` 그대로다.

    P2-09 에서도 바꾸지 않는다(spec D7).
    """
    assert StrategyIdentity("x", 1).schema_version == CURRENT_SCHEMA_VERSION
    assert not is_frozen_schema_version(CURRENT_SCHEMA_VERSION)
    for version in FROZEN_SCHEMA_VERSIONS:
        assert is_frozen_schema_version(version)
    assert is_frozen_schema_version("0.9")
    assert is_frozen_schema_version("1.3")


def test_an_unquoted_1_0_version_line_is_still_the_declared_1_0() -> None:
    """따옴표 없는 `schema_version: 1.0` 은 YAML 이 float 로 읽는다. 선언은 여전히 1.0 이다."""
    document = _yaml("quality_momentum.v1_0.yaml")
    document["schema_version"] = 1.0

    outcome = upgrade_document(document)

    assert outcome.source_version == V1_0
    assert outcome.tree["schema_version"] == CURRENT_SCHEMA_VERSION
    assert hydrate_strategy_document(outcome.tree, identity=DRAFT).ok


# 1.0 전용 모양 하나씩. 현재 버전 문서에 섞일 수 있는 옛 키의 종류 전부다(`legacy_shape_hints` 가
# 짚는 네 갈래: 두 겹 factors, 은퇴 키 3개, unary alias).
_LEGACY_SHAPES: dict[str, Any] = {
    "nested-factors": lambda d: d.__setitem__("factors", {"factors": d["factors"]}),
    "signal-method": lambda d: d.setdefault("signal", {}).__setitem__("method", "weighted_sum"),
    "signal-entry": lambda d: d.setdefault("signal", {}).__setitem__("entry_percentile", 0.2),
    "execution-order-style": lambda d: d.__setitem__("execution", {"order_style": "market"}),
    "unary-alias": lambda d: (
        d["factors"]["factors"] if isinstance(d["factors"], dict) else d["factors"]
    )[0]["graph"]["nodes"].append(
        {"kind": "unary", "node_id": "legacy_rank", "operator": "rank", "input_node_id": "close"}
    ),
}
_CURRENT_BASES = (
    "quality_momentum.yaml",
    *(f"ideas/{p.name}" for p in sorted((FIXTURES / "ideas").glob("*.yaml"))),
)


def _with_shapes(base: str, shapes: tuple[str, ...]) -> dict[str, Any]:
    document = _yaml(base)
    first = document["factors"][0]["graph"]["nodes"][0]
    for name in shapes:
        _LEGACY_SHAPES[name](document)
    if "unary-alias" in shapes:  # 별칭 노드의 입력을 그 문서의 첫 잎으로 맞춘다
        factors = (
            document["factors"]["factors"]
            if isinstance(document["factors"], dict)
            else document["factors"]
        )
        factors[0]["graph"]["nodes"][-1]["input_node_id"] = first["node_id"]
    return document


def _subsets() -> list[tuple[str, ...]]:
    names = tuple(_LEGACY_SHAPES)
    return [
        tuple(name for bit, name in enumerate(names) if mask >> bit & 1)
        for mask in range(1, 1 << len(names))
    ]


@pytest.mark.parametrize("base", _CURRENT_BASES)
def test_a_current_document_with_any_mix_of_1_0_shapes_is_a_structure_error_not_an_upgrade(
    base: str,
) -> None:
    """Phase 2 감사 NB-1: 문서가 선언한 버전을 믿는다(property — 옛 모양 5종의 모든 조합).

    버전 줄이 현재 판인 문서에 1.0 키가 섞이면 업그레이드 대상이 아니라 제자리에서 고칠 구조
    오류다. 체인을 1.0 부터 태우면 1.1 → 1.2 단계가 `normalization: none` 을 조용히 넣어 1.2 기본값
    `rank` 의 의미를 바꾸고, 문서에 없던 `/data/*` 를 짚는 warning 까지 낸다(감사 탐침 실측).
    그래서 판정은 거절이고, 진단 문장은 업그레이드를 시키지 않는다(시키는 일은 눌러서 되어야 한다).
    """
    for shapes in _subsets():
        document = _with_shapes(base, shapes)
        assert legacy_shape_hints(document), shapes

        assert not is_upgradeable_document(document), shapes
        with pytest.raises(NotUpgradeableDocumentError, match="already current") as info:
            upgrade_document(document)
        assert "fix them in place" in str(info.value), shapes

        # 구조 오류로 멈추고, 어느 문장도 업그레이드를 시키지 않는다. `execution` 절은 1.2 에서 절
        # 자체가 모르는 키라 `structure.unknown_key` 로 짚히고, 나머지 옛 모양은 `legacy_shape` 다.
        issues = hydrate_strategy_document(document, identity=DRAFT).issues
        codes = {issue.code for issue in issues}
        assert codes, shapes
        assert codes <= {"structure.legacy_shape", "structure.unknown_key"}, (shapes, codes)
        if set(shapes) != {"execution-order-style"}:
            assert "structure.legacy_shape" in codes, (shapes, codes)
        assert all("업그레이드" not in issue.message for issue in issues), shapes


@pytest.mark.parametrize("version", [V1_1, None])
def test_an_older_shape_than_the_declared_version_refuses_the_upgrade(version: object) -> None:
    """선언된 버전보다 앞선 단계는 타지 않는다(NB-1). 1.1 선언 문서에 1.0 모양이 섞였거나 버전
    줄이 없으면, 체인을 어디서 시작할지 문서가 말하지 않으므로 거절한다."""
    document = _with_shapes("quality_momentum.v1_1.yaml", ("signal-method",))
    if version is None:
        del document["schema_version"]
    else:
        document["schema_version"] = version

    assert not is_upgradeable_document(document)
    expected = "older than the declared version" if version is not None else "missing"
    with pytest.raises(NotUpgradeableDocumentError, match=expected):
        upgrade_document(document)


@pytest.mark.parametrize("version", ["1.3", "2.0", 2.0, "0.9", "draft"])
def test_an_unknown_version_line_is_never_downgraded_even_with_a_1_0_body(version: object) -> None:
    """버전 상한(P1-05 2차 리뷰 P3-7, BACKLOG-010): "미래 버전 + 옛 키 하나"를 1.x 로 강등하지
    않는다. 모르는 버전은 본문 모양과 상관없이 거절한다 — 저장 row 읽기가 이 거절에 기댄다."""
    document = _yaml("quality_momentum.v1_0.yaml")
    document["schema_version"] = version

    assert legacy_shape_hints(document) != {}
    assert not is_upgradeable_document(document)
    with pytest.raises(NotUpgradeableDocumentError, match="neither current nor a known retired"):
        upgrade_document(document)


def test_a_valid_current_document_is_never_mistaken_for_an_old_one() -> None:
    """정상 1.2 문서에 오탐이 없다. 오탐이면 멀쩡한 문서에 업그레이드 배너가 뜬다."""
    document = _yaml("quality_momentum.yaml")

    assert legacy_shape_hints(document) == {}
    assert not is_upgradeable_document(document)


def test_a_stage_that_leaves_its_old_shape_behind_is_refused() -> None:
    """단계마다 검증한다.

    한 번 평탄화해도 옛 모양이 남으면(세 겹 factors) 다음 단계로 넘기지 않는다.
    """
    document = _yaml("quality_momentum.v1_0.yaml")
    document["factors"] = {"factors": {"factors": document["factors"]["factors"]}}

    with pytest.raises(NotUpgradeableDocumentError, match=f"stage {V1_0}->{V1_1}") as info:
        upgrade_document(document)
    assert "/factors" in str(info.value)


def test_unary_aliases_move_to_cross_sectional_and_drop_periods() -> None:
    document = _yaml("quality_momentum.v1_0.yaml")
    nodes = document["factors"]["factors"][0]["graph"]["nodes"]
    nodes.extend(
        [
            {"node_id": "r", "operator": "rank", "input_node_id": "close", "kind": "unary"},
            {"node_id": "z", "operator": "zscore", "input_node_id": "close", "kind": "unary"},
            {
                "node_id": "w",
                "operator": "winsorize",
                "input_node_id": "close",
                "kind": "unary",
                "periods": 3,
            },
            {"node_id": "n", "operator": "neutralize", "input_node_id": "close", "kind": "unary"},
            {"node_id": "g", "operator": "negate", "input_node_id": "close", "kind": "unary"},
            {
                "node_id": "l",
                "operator": "lag",
                "input_node_id": "close",
                "kind": "unary",
                "periods": 2,
            },
        ]
    )

    upgraded = _tree(document)

    by_id = {node["node_id"]: node for node in upgraded["factors"][0]["graph"]["nodes"]}
    assert (by_id["r"]["kind"], by_id["r"]["operator"]) == ("cross_sectional", "rank")
    assert (by_id["z"]["kind"], by_id["z"]["operator"]) == ("cross_sectional", "zscore")
    assert (by_id["w"]["kind"], by_id["w"]["operator"]) == ("cross_sectional", "winsorize")
    assert "periods" not in by_id["w"]
    assert (by_id["n"]["kind"], by_id["n"]["operator"]) == ("cross_sectional", "demean")
    assert by_id["g"] == {
        "node_id": "g",
        "operator": "negate",
        "input_node_id": "close",
        "kind": "unary",
    }
    assert by_id["l"]["kind"] == "unary" and by_id["l"]["periods"] == 2


def test_removed_fields_go_and_nothing_else_changes() -> None:
    document = _yaml("quality_momentum.v1_0.yaml")
    document["signal"] = {
        "method": "rank_threshold",
        "entry_percentile": 0.2,
        "score_threshold": 1.5,
    }
    document["execution"] = {"timing": "next_open", "fee_bps": 15.0, "order_style": "market"}
    document["portfolio"]["selection_count"] = 7  # 명시값은 그대로

    outcome = upgrade_document(document)
    upgraded = _tree(document)

    assert upgraded["signal"] == {"score_threshold": 1.5, "normalization": "none"}
    # 1.0 전용 `order_style` 은 1.0 단계에서 지워져 실행 설정으로 넘어가지 않는다.
    assert outcome.environment is not None
    assert outcome.environment.execution_section == {"timing": "next_open", "fee_bps": 15.0}
    assert upgraded["portfolio"]["selection_count"] == 7
    assert upgraded["description"] == "" and upgraded["eligibility"] == {"rules": []}


def test_stage_step_order_is_declared_and_flattening_precedes_node_rules() -> None:
    names = {version: [name for name, _ in steps] for version, steps in UPGRADE_STEPS.items()}
    assert names == {
        V1_0: ["flatten_factors", "remove_dead_fields", "unary_aliases"],
        V1_1: ["reject_saved_nodes", "strip_execution_settings", "explicit_normalization"],
    }


def test_steps_apply_identically_to_ruamel_round_trip_containers() -> None:
    """source 경로가 같은 체인을 CST 에 쓴다: 결과 tree 가 dict 경로와 같고 주석·따옴표가 남는다."""
    text = (FIXTURES / "quality_momentum.v1_0.yaml").read_text(encoding="utf-8")
    loader = YAML(typ="rt")
    document = loader.load(text)

    apply_upgrade_steps(document)

    buffer = StringIO()
    loader.dump(document, buffer)
    dumped = buffer.getvalue()
    assert json.loads(json.dumps(yaml.safe_load(dumped))) == _tree(yaml.safe_load(text))
    assert dumped.startswith("# P0-01 golden authoring fixture")  # 선두 주석 보존
    # 따옴표 보존은 loader 설정(`preserve_quotes`) 몫이라 여기서는 어느 쪽이든 현재 버전이면 된다.
    assert (
        f"schema_version: '{CURRENT_SCHEMA_VERSION}'" in dumped
        or f'schema_version: "{CURRENT_SCHEMA_VERSION}"' in dumped
    )
