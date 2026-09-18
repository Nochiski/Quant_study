"""schema 1.0 → 1.1 업그레이드 변환 (spec D3, P1-03)."""

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
    UPGRADE_STEPS,
    DocumentNotUpgradeableError,
    apply_upgrade_steps,
    hydrate_strategy_document,
    is_legacy_document,
    upgrade_document_1_0,
)
from strategy_workbench.domain.strategy.facade.specification import (
    StrategyIdentity,
    strategy_spec_hash,
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


def test_upgrade_golden_matches_the_1_1_fixture_and_its_hash() -> None:
    """1.0 golden을 올리면 1.1 verbose fixture와 같은 tree(빈 `signal`만 남음)·같은 hash다."""
    upgraded = upgrade_document_1_0(_yaml("quality_momentum.v1_0.yaml"))

    expected = _yaml("quality_momentum.yaml")
    assert upgraded == {**expected, "signal": {}}
    hydrated = hydrate_strategy_document(upgraded, identity=DRAFT)
    assert hydrated.ok and hydrated.spec is not None
    reference = hydrate_strategy_document(expected, identity=DRAFT)
    assert reference.ok and reference.spec is not None
    assert strategy_spec_hash(hydrated.spec) == strategy_spec_hash(reference.spec)


def test_upgrade_does_not_mutate_its_input() -> None:
    document = _yaml("quality_momentum.v1_0.yaml")
    snapshot = copy.deepcopy(document)

    upgrade_document_1_0(document)

    assert document == snapshot


@pytest.mark.parametrize("version", ["1.1", "2.0", 1.0, None])
def test_only_schema_1_0_is_upgradeable(version: object) -> None:
    document = _yaml("quality_momentum.v1_0.yaml")
    if version is None:
        del document["schema_version"]
    else:
        document["schema_version"] = version

    assert not is_legacy_document(document)
    with pytest.raises(DocumentNotUpgradeableError, match="only schema 1.0"):
        upgrade_document_1_0(document)


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
    hydrated = hydrate_strategy_document(upgraded, identity=DRAFT)
    assert hydrated.ok, hydrated.issues


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
    assert "schema_version: '1.1'" in dumped or 'schema_version: "1.1"' in dumped
