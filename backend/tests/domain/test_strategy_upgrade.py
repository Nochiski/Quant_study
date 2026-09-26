"""schema 1.0 → 1.1 업그레이드 변환 (spec D3, P1-03).

**현재 버전은 1.2 다.** 이 모듈이 아직 1.1 까지만 올리므로 결과는 저장·실행할 수 없는 중간
산출물이다 — 1.1 → 1.2 step 과 버전 디스패치는 P2-09 가 붙인다(spec D7).
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

from strategy_workbench.domain.strategy.facade.document import (
    CURRENT_SCHEMA_VERSION,
    LEGACY_SCHEMA_VERSION,
    LEGACY_UPGRADE_TARGET_VERSION,
    UPGRADE_STEPS,
    NotALegacyDocumentError,
    apply_upgrade_steps,
    hydrate_strategy_document,
    is_frozen_schema_version,
    is_legacy_document,
    is_upgradeable_document,
    legacy_shape_hints,
    upgrade_document_1_0,
)
from strategy_workbench.domain.strategy.facade.specification import (
    StrategyIdentity,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"
DRAFT = StrategyIdentity("draft", 0)


def _yaml(name: str) -> dict[str, Any]:
    loaded = yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _upgrade(document: dict[str, Any]) -> dict[str, Any]:
    """테스트 편의: 변환 결과를 dict[str, Any]로 다룬다(프로덕션 시그니처는 object 값)."""
    return dict(upgrade_document_1_0(document))


def test_upgrade_golden_matches_the_preserved_1_1_fixture() -> None:
    """1.0 golden 을 올리면 보존된 1.1 원본과 같은 tree 다(빈 `signal` 만 남는다).

    step 이 찍는 버전은 **자기 목표 버전**(1.1)이다. `CURRENT_SCHEMA_VERSION` 을 찍으면
    "1.2 라고 적혀 있지만 `data`·`execution` 이 남은 문서"가 나와 중간 단계 검증이 사라진다.
    """
    upgraded = upgrade_document_1_0(_yaml("quality_momentum.v1_0.yaml"))

    expected = _yaml("quality_momentum.v1_1.yaml")
    assert upgraded == {**expected, "signal": {}}
    assert upgraded["schema_version"] == LEGACY_UPGRADE_TARGET_VERSION


def test_the_1_1_upgrade_output_is_not_runnable_until_the_1_2_step_exists() -> None:
    """P2-09 전까지의 중간 상태를 명시적으로 고정한다(WORKFLOW P2-03 제약사항).

    1.0 문서의 업그레이드 결과는 은퇴 버전이라 hydrate 되지 않는다. 이 단언이 사라지는 시점이
    P2-09 가 1.1 → 1.2 step 을 붙였다는 신호다.
    """
    upgraded = upgrade_document_1_0(_yaml("quality_momentum.v1_0.yaml"))

    hydrated = hydrate_strategy_document(upgraded, identity=DRAFT)

    assert not hydrated.ok
    assert [issue.code for issue in hydrated.issues] == ["structure.unsupported_schema_version"]


def test_upgrade_does_not_mutate_its_input() -> None:
    document = _yaml("quality_momentum.v1_0.yaml")
    snapshot = copy.deepcopy(document)

    upgrade_document_1_0(document)

    assert document == snapshot


def test_frozen_means_any_version_other_than_current() -> None:
    """Phase 1 감사 DEFECT-P1X-001·003: 현재 버전은 모델 기본값과 같은 상수 하나이고, 동결 술어는
    `== "1.0"`이 아니라 `!= CURRENT`다(1.2 도입 때 1.1 row도 동결이 된다)."""
    assert StrategyIdentity("x", 1).schema_version == CURRENT_SCHEMA_VERSION
    assert not is_frozen_schema_version(CURRENT_SCHEMA_VERSION)
    assert is_frozen_schema_version(LEGACY_SCHEMA_VERSION)
    assert is_frozen_schema_version("0.9")
    assert is_frozen_schema_version("1.1")


@pytest.mark.parametrize("version", [CURRENT_SCHEMA_VERSION, "2.0", 1.0, None])
def test_a_1_0_body_upgrades_whatever_the_version_line_says(version: object) -> None:
    """버전 줄만 손으로 고친 1.0 본문도 업그레이드된다(P1-05 1차 리뷰 DEFECT-P105-001).

    진단(`structure.legacy_shape`)이 "업그레이드하세요"라고 시키고 배너까지 띄우므로, 판정이
    버전 문자열만 보면 버튼이 반드시 422로 끝난다. 판정 owner를 하나로 두고 둘이 같은 조건을
    읽게 한 결과를 여기서 고정한다.
    """
    document = _yaml("quality_momentum.v1_0.yaml")
    if version is None:
        del document["schema_version"]
    else:
        document["schema_version"] = version

    assert not is_legacy_document(document)
    assert is_upgradeable_document(document)

    upgraded = upgrade_document_1_0(document)

    # 버전 줄은 결과 버전으로 정규화되고 1.0 모양은 사라진다.
    # P2-03 이후 P2-09 전까지는 1.0 변환이 1.1(`LEGACY_UPGRADE_TARGET_VERSION`)에서 멈춰 결과가
    # `structure.unsupported_schema_version` 으로 거절되는 중간 상태다. P2-09 가 1.1 → 1.2 step 을
    # 붙이면 이 단언을 `CURRENT_SCHEMA_VERSION`·hydrate 성공으로 되돌린다.
    assert upgraded["schema_version"] == LEGACY_UPGRADE_TARGET_VERSION
    assert isinstance(upgraded["factors"], list)
    hydrated = hydrate_strategy_document(upgraded, identity=DRAFT)
    assert [issue.code for issue in hydrated.issues] == ["structure.unsupported_schema_version"]


def test_a_document_with_no_1_0_shape_stays_fail_closed() -> None:
    """옛 판 모양이 하나도 없으면 그대로 거절한다 — 저장 row 읽기 경로가 이 예외에 기댄다."""
    document = _yaml("quality_momentum.yaml")
    document["schema_version"] = "0.9"

    assert not is_upgradeable_document(document)
    with pytest.raises(NotALegacyDocumentError, match="only schema 1.0 documents or bodies"):
        upgrade_document_1_0(document)


def test_a_valid_current_document_is_never_mistaken_for_an_old_one() -> None:
    """정상 1.1 문서에 오탐이 없다. 오탐이면 멀쩡한 문서에 업그레이드 배너가 뜬다."""
    document = _yaml("quality_momentum.yaml")

    assert legacy_shape_hints(document) == {}
    assert not is_upgradeable_document(document)


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        pytest.param(
            "factors 두 겹",
            lambda d: d.__setitem__("factors", {"factors": d["factors"]}),
            id="nested-factors",
        ),
        pytest.param(
            "은퇴한 키",
            # 1.0 의 `execution.order_style` 은 1.2 에서 섹션째 사라져 `signal.method` 로 본다.
            lambda d: d.setdefault("signal", {}).__setitem__("method", "weighted_sum"),
            id="removed-field",
        ),
        pytest.param(
            "unary alias 노드",
            lambda d: d["factors"][0]["graph"]["nodes"].append(
                {
                    "kind": "unary",
                    "node_id": "ranked",
                    "operator": "rank",
                    "input_node_id": "close",
                }
            ),
            id="unary-alias",
        ),
    ],
)
def test_every_shape_the_diagnostic_points_at_can_actually_be_upgraded(
    name: str, mutate: Any
) -> None:
    """진단이 짚는 세 모양이 전부 실제로 업그레이드된다.

    진단이 시키는 일은 눌러서 되어야 한다 — 하나라도 판정에서 빠지면 그 문서의 배너가 422로
    끝나고 사용자에게 남는 길이 없다.
    """
    document = _yaml("quality_momentum.yaml")
    mutate(document)

    assert legacy_shape_hints(document) != {}, name
    assert is_upgradeable_document(document), name

    upgraded = upgrade_document_1_0(document)

    assert legacy_shape_hints(upgraded) == {}, name
    # P2-09 전 중간 상태: 결과는 1.1 이라 1.2 hydrate 가 은퇴 버전으로 거절한다(위 테스트와 같다).
    assert upgraded["schema_version"] == LEGACY_UPGRADE_TARGET_VERSION, name
    hydrated = hydrate_strategy_document(upgraded, identity=DRAFT)
    assert [issue.code for issue in hydrated.issues] == ["structure.unsupported_schema_version"], (
        name
    )


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

    upgraded = _upgrade(document)

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

    upgraded = _upgrade(document)

    assert upgraded["signal"] == {"score_threshold": 1.5}
    assert upgraded["execution"] == {"timing": "next_open", "fee_bps": 15.0}
    assert upgraded["portfolio"]["selection_count"] == 7
    assert "description" in upgraded and upgraded["eligibility"] == {
        "rules": []
    }  # 기본값을 지우지 않는다


def test_step_order_is_declared_and_flattening_precedes_node_rules() -> None:
    names = [name for name, _ in UPGRADE_STEPS]
    assert names == ["schema_version", "flatten_factors", "remove_dead_fields", "unary_aliases"]


def test_steps_apply_identically_to_ruamel_round_trip_containers() -> None:
    """P1-04의 source 경로가 같은 step을 CST에 쓴다: 결과 tree가 dict 경로와 같고 주석이 남는다."""
    text = (FIXTURES / "quality_momentum.v1_0.yaml").read_text(encoding="utf-8")
    loader = YAML(typ="rt")
    document = loader.load(text)

    apply_upgrade_steps(document)

    buffer = StringIO()
    loader.dump(document, buffer)
    dumped = buffer.getvalue()
    assert json.loads(json.dumps(yaml.safe_load(dumped))) == upgrade_document_1_0(
        yaml.safe_load(text)
    )
    assert dumped.startswith("# P0-01 golden authoring fixture")  # 선두 주석 보존
    assert (
        f"schema_version: '{LEGACY_UPGRADE_TARGET_VERSION}'" in dumped
        or f'schema_version: "{LEGACY_UPGRADE_TARGET_VERSION}"' in dumped
    )
