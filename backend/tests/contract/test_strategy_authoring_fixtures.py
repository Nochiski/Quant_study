"""P0-01 Strategy Authoring Contract acceptance fixtures.

ADR: docs/superpowers/specs/2026-09-04-strategy-authoring-contract-adr.md

이 테스트는 authoring source(YAML/JSON)와 실행 SoT(StrategySpec) 사이의 계약을 고정한다.

- 같은 의미의 YAML, JSON, legacy JSON은 같은 canonical spec hash를 낸다.
- WORKFLOW 2.2의 완전한 YAML 예시는 identity 주입 후 StrategySpec으로 hydrate된다.
- identity를 제외한 canonical round-trip이 보존된다.
- 기존 hash 알고리즘은 바뀌지 않는다 (template golden).
- 구문이 깨진 source는 StrategySpec이 되지 않는다 (fail-closed).

YAML loader는 P1-02 codec이 생기기 전까지의 임시 수단으로 `yaml.safe_load`를 쓴다.
fixture는 YAML 1.1 implicit typing에 걸리지 않도록 모든 날짜·버전을 quoted string으로 적는다.
P0-03 cross-runtime fixture와 P1-02 codec이 이 loader를 대체한다.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from strategy_workbench.adapters.outbound.strategy_memory.facade.repository import (
    InMemoryStrategyRepository,
)
from strategy_workbench.application.strategy_design.facade.design import StrategyDesignService
from strategy_workbench.domain.strategy.facade.specification import (
    StrategyIdentity,
    StrategySpec,
    canonical_strategy_payload,
    strategy_spec_hash,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"

# 같은 의미의 YAML/JSON/legacy JSON fixture가 공유하는 canonical hash.
QUALITY_MOMENTUM_SPEC_HASH = "9eb6872a3ca250dfb78b0887e5b236a98b24fb2ccdf2d6af0540218d49e998fe"
# StrategyDesignService.template()의 2026-09-03 기준 hash. 알고리즘 변경 감지용 golden.
TEMPLATE_SPEC_HASH_2026_09_03 = "d6c0e1da4b05490bfa94b3b4c0c605fd4d6bca625f44d06c577b181f7911f2d4"

DRAFT_IDENTITY = StrategyIdentity(strategy_id="draft", revision=0)

_spec_adapter: TypeAdapter[StrategySpec] = TypeAdapter(StrategySpec)


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _load_yaml(name: str) -> dict[str, Any]:
    loaded = yaml.safe_load(_read(name))
    assert isinstance(loaded, dict)
    return loaded


def _load_json(name: str) -> dict[str, Any]:
    loaded = json.loads(_read(name))
    assert isinstance(loaded, dict)
    return loaded


def hydrate_authoring_document(
    document: dict[str, Any], identity: StrategyIdentity = DRAFT_IDENTITY
) -> StrategySpec:
    """identity-free authoring payload에 revision envelope identity를 주입해 typed spec을 만든다.

    P1-01이 이 역할을 domain hydrate로 옮긴다. untyped dict를 직접 canonical JSON으로
    직렬화하는 경로를 만들지 않기 위해 반드시 typed StrategySpec을 거친다.
    """
    payload = dict(document)
    schema_version = payload.pop("schema_version")
    payload["identity"] = {
        "strategy_id": identity.strategy_id,
        "revision": identity.revision,
        "schema_version": schema_version,
    }
    return _spec_adapter.validate_python(payload)


def _template(today: date = date(2026, 9, 3)) -> StrategySpec:
    return StrategyDesignService(
        InMemoryStrategyRepository(), new_id=lambda: "unused", today=lambda: today
    ).template()


def test_verbose_yaml_example_hydrates_to_strategy_spec() -> None:
    spec = hydrate_authoring_document(_load_yaml("quality_momentum.yaml"))

    assert spec.identity == DRAFT_IDENTITY
    assert spec.title == "퀄리티 모멘텀"
    assert spec.data.start == date(2021, 1, 1)
    assert spec.risk.max_name_weight == 0.05
    assert spec.factors.factors[0].graph.output_node_id == "mom_252"
    assert strategy_spec_hash(spec) == QUALITY_MOMENTUM_SPEC_HASH


@pytest.mark.parametrize(
    "name",
    ["quality_momentum.yaml", "quality_momentum.json", "quality_momentum.legacy.json"],
)
def test_same_meaning_sources_share_one_spec_hash(name: str) -> None:
    if name.endswith(".yaml"):
        spec = hydrate_authoring_document(_load_yaml(name))
    elif name.endswith(".legacy.json"):
        # legacy JSON은 identity를 문서 안에 가진 현행 API payload다.
        spec = _spec_adapter.validate_python(_load_json(name))
        assert spec.identity == StrategyIdentity("strategy-legacy", 3)
    else:
        spec = hydrate_authoring_document(_load_json(name))

    assert strategy_spec_hash(spec) == QUALITY_MOMENTUM_SPEC_HASH


def test_int_and_float_literals_hydrate_to_the_same_spec() -> None:
    document = _load_json("quality_momentum.json")
    assert document["execution"]["fee_bps"] == 15  # fixture는 일부러 int로 적는다.
    document["factors"]["factors"][0]["weight"] = 1
    variant = dict(document)
    variant["factors"] = json.loads(json.dumps(document["factors"]))
    variant["factors"]["factors"][0]["weight"] = 1.0

    assert strategy_spec_hash(hydrate_authoring_document(document)) == strategy_spec_hash(
        hydrate_authoring_document(variant)
    )


def test_canonical_round_trip_preserves_everything_except_identity() -> None:
    original = replace(
        hydrate_authoring_document(_load_yaml("quality_momentum.yaml")),
        identity=StrategyIdentity("strategy-7", 4),
    )

    rehydrated = hydrate_authoring_document(
        canonical_strategy_payload(original), StrategyIdentity("strategy-99", 1)
    )

    assert replace(rehydrated, identity=original.identity) == original
    assert strategy_spec_hash(rehydrated) == strategy_spec_hash(original)


def test_existing_hash_algorithm_is_unchanged() -> None:
    assert strategy_spec_hash(_template()) == TEMPLATE_SPEC_HASH_2026_09_03


def test_syntax_invalid_yaml_never_becomes_a_spec() -> None:
    with pytest.raises(yaml.YAMLError):
        _load_yaml("quality_momentum.invalid.yaml")


def test_structurally_invalid_document_fails_closed() -> None:
    document = _load_yaml("quality_momentum.yaml")
    del document["data"]["start"]

    with pytest.raises(ValidationError):
        hydrate_authoring_document(document)
