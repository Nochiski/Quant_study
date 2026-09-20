"""P0-01 Strategy Authoring Contract acceptance fixtures.

ADR: docs/superpowers/specs/2026-09-04-strategy-authoring-contract-adr.md

이 테스트는 authoring source(YAML/JSON)와 실행 SoT(StrategySpec) 사이의 계약을 고정한다.

- 같은 의미의 YAML, JSON, legacy JSON은 같은 canonical spec hash를 낸다.
- WORKFLOW 2.2의 완전한 YAML 예시는 identity 주입 후 StrategySpec으로 hydrate된다.
- identity를 제외한 canonical round-trip이 보존된다.
- 기존 hash 알고리즘은 바뀌지 않는다 (template golden).
- 구문이 깨진 source는 StrategySpec이 되지 않는다 (fail-closed).
- unknown key는 structural fail-closed다 (P1-01 domain hydrate).

YAML loader는 P1-02 codec이 생기기 전까지의 임시 수단으로 `yaml.safe_load`(dev group의 pyyaml)를
쓴다.
fixture는 YAML 1.1 implicit typing에 걸리지 않도록 모든 날짜·버전을 quoted string으로 적는다.
P0-03 cross-runtime fixture와 P1-02 codec이 이 loader를 대체한다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from strategy_workbench.domain.strategy.facade.document import (
    hydrate_saved_strategy,
    hydrate_strategy_document,
)
from strategy_workbench.domain.strategy.facade.specification import (
    StrategyIdentity,
    StrategySpec,
    canonical_payload_json,
    canonical_strategy_payload,
    strategy_spec_hash,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"

# 같은 의미의 YAML/JSON/legacy JSON fixture가 공유하는 canonical hash.
# 재생성: strategy_spec_hash(hydrate_authoring_document(yaml.safe_load(quality_momentum.yaml)))
QUALITY_MOMENTUM_SPEC_HASH = "f14a9eaacf1214e8d697288ebed1047b723ef3de75fd222aafa19b400e6b43df"
# hash 알고리즘 golden. 모델 모양과 무관하게 canonical 직렬화 규칙(sort_keys·최소 separator·
# allow_nan=False·sha256)만 고정한다. payload는 2026-09-04 schema 1.0 fixture의 canonical payload를
# literal 파일로 옮긴 것이라 모델이 1.1로 바뀌어도 값이 변하지 않는다. 값이 바뀌면 알고리즘이 바뀐
# 것이다.
ALGORITHM_HASH_2026_09_04 = "9eb6872a3ca250dfb78b0887e5b236a98b24fb2ccdf2d6af0540218d49e998fe"

DRAFT_IDENTITY = StrategyIdentity(strategy_id="draft", revision=0)


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


class StructuralError(ValueError):
    pass


def hydrate_authoring_document(
    document: dict[str, Any], identity: StrategyIdentity = DRAFT_IDENTITY
) -> StrategySpec:
    """identity-free authoring payload를 domain typed hydrate로 StrategySpec으로 만든다.

    구조 오류가 있으면 StructuralError로 fail-closed한다.
    """
    result = hydrate_strategy_document(document, identity=identity)
    if not result.ok or result.spec is None:
        raise StructuralError(
            f"structural issues — {[(issue.code, issue.pointer) for issue in result.issues]}"
        )
    return result.spec


def test_verbose_yaml_example_hydrates_to_strategy_spec() -> None:
    spec = hydrate_authoring_document(_load_yaml("quality_momentum.yaml"))

    assert spec.identity == DRAFT_IDENTITY
    assert spec.title == "퀄리티 모멘텀"
    assert spec.risk.max_name_weight == 0.05
    assert spec.factors[0].graph.output_node_id == "mom_252"
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
        legacy = hydrate_saved_strategy(_load_json(name))
        assert legacy.ok and legacy.spec is not None
        spec = legacy.spec
        assert spec.identity == StrategyIdentity("strategy-legacy", 3)
    else:
        spec = hydrate_authoring_document(_load_json(name))

    assert strategy_spec_hash(spec) == QUALITY_MOMENTUM_SPEC_HASH


def test_int_and_float_literals_hydrate_to_the_same_spec() -> None:
    document = _load_json("quality_momentum.json")
    document["factors"][0]["weight"] = 1  # fixture는 일부러 int로 적는다.
    variant = dict(document)
    variant["factors"] = json.loads(json.dumps(document["factors"]))
    variant["factors"][0]["weight"] = 1.0

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
    payload = _load_json("canonical_payload.v1_0.json")
    encoded = canonical_payload_json(payload).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == ALGORITHM_HASH_2026_09_04


def test_minimal_document_hydrates_to_the_same_hash() -> None:
    """생략된 섹션·market·label·weight는 기본값으로 채워져 verbose 문서와 같은 hash를 낸다 (S3)."""
    verbose = hydrate_authoring_document(_load_yaml("quality_momentum.yaml"))
    minimal_document = _load_yaml("quality_momentum.minimal.yaml")
    for key in ("description", "eligibility", "parameters"):
        assert key not in minimal_document
    minimal = hydrate_authoring_document(minimal_document)

    assert strategy_spec_hash(minimal) == strategy_spec_hash(verbose) == QUALITY_MOMENTUM_SPEC_HASH


def test_factor_label_defaults_to_factor_id_and_weight_to_one() -> None:
    document = _load_yaml("quality_momentum.minimal.yaml")
    del document["factors"][0]["label"]
    del document["factors"][0]["weight"]

    spec = hydrate_authoring_document(document)

    assert spec.factors[0].label == "momentum"
    assert spec.factors[0].weight == 1.0


def test_schema_1_0_document_is_rejected() -> None:
    """1.0 문서는 새 입력으로 받지 않는다. P1-03/04의 업그레이드 경로로만 들어온다 (spec D2)."""
    result = hydrate_strategy_document(
        _load_yaml("quality_momentum.v1_0.yaml"), identity=DRAFT_IDENTITY
    )

    assert not result.ok
    assert [issue.code for issue in result.issues] == ["structure.unsupported_schema_version"]


def test_nested_factors_shape_is_a_type_mismatch() -> None:
    """1.0의 `factors: {factors: [...]}`는 1.1에서 sequence 자리의 mapping이다 (S1)."""
    document = _load_yaml("quality_momentum.yaml")
    document["factors"] = {"factors": document["factors"]}

    result = hydrate_strategy_document(document, identity=DRAFT_IDENTITY)

    assert not result.ok
    assert [(issue.code, issue.pointer) for issue in result.issues] == [
        ("structure.type_mismatch", "/factors")
    ]


def test_syntax_invalid_yaml_never_becomes_a_spec() -> None:
    """fixture sanity check.

    P1-02 codec이 syntax diagnostic으로 대체할 때까지 loader 실패만 확인한다.
    """
    with pytest.raises(yaml.YAMLError):
        _load_yaml("quality_momentum.invalid.yaml")


def test_structurally_invalid_document_fails_closed() -> None:
    document = _load_yaml("quality_momentum.yaml")
    del document["title"]

    with pytest.raises(StructuralError, match="structure.missing_field"):
        hydrate_authoring_document(document)


def test_unknown_key_fails_closed() -> None:
    """`risk.max_name_wieght` 오타가 default 0.1로 조용히 대체되면 안 된다."""
    document = _load_yaml("quality_momentum.unknown_key.yaml")

    with pytest.raises(StructuralError, match="/risk/max_name_wieght"):
        hydrate_authoring_document(document)
