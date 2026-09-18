"""schema 1.0 → 1.1 문서 업그레이드 변환 — 규칙의 유일한 owner (spec D3).

두 입력 경로가 같은 규칙을 쓴다.

- dict 경로: `upgrade_document_1_0`. repository codec이 저장된 1.0 `spec_json`을 읽을 때, legacy
  revision의 generated source를 만들 때.
- source 경로(P1-04): ruamel round-trip CST(CommentedMap/CommentedSeq)에 `apply_upgrade_steps`를
  그대로 적용해 주석·순서를 보존한다. 각 step은 `MutableMapping`/`MutableSequence` API만
  쓰므로 plain dict/list와 ruamel 컨테이너 양쪽에서 같은 결과를 낸다. 규칙표를 두 벌 두지
  않는다.

변환 항목(spec D1): schema_version, `factors.factors` 평탄화, 미사용 필드 3개 제거, `unary` alias →
`cross_sectional`(`neutralize` → `demean`). 기본값을 채우거나 지우지 않는다: 생략은 작성자의
선택이다.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, MutableMapping, MutableSequence

from ._models import CURRENT_SCHEMA_VERSION

LEGACY_SCHEMA_VERSION = "1.0"


def is_frozen_schema_version(schema_version: str) -> bool:
    """저장 row가 동결(업그레이드 필요) 이력인가: 현재 버전이 아닌 모든 버전(spec D2).

    port의 `StrategyRevisionRecord.requires_upgrade`와 SQLite codec의 동결 읽기 분기가 같은 술어를
    쓴다(Phase 1 감사 DEFECT-P1X-003: `== "1.0"`과 `!= CURRENT`가 갈리면 1.2 도입 때 1.1 row가
    hydrate 실패로 500이 된다).
    """
    return schema_version != CURRENT_SCHEMA_VERSION


# `unary` operator → (1.1 kind, 1.1 operator). rank/zscore/winsorize는 평가 코드가 cross_sectional로
# 치환하던 순수 alias였고, neutralize는 cross-sectional demean이었다 (P1-02에서 제거).
_UNARY_ALIASES: Mapping[str, tuple[str, str]] = {
    "rank": ("cross_sectional", "rank"),
    "zscore": ("cross_sectional", "zscore"),
    "winsorize": ("cross_sectional", "winsorize"),
    "neutralize": ("cross_sectional", "demean"),
}
REMOVED_FIELDS: tuple[tuple[str, str], ...] = (
    ("signal", "method"),
    ("signal", "entry_percentile"),
    ("execution", "order_style"),
)

UpgradeStep = Callable[[MutableMapping[str, object]], None]


class NotALegacyDocumentError(ValueError):
    """The document is not a schema 1.0 document, so no upgrade rule applies."""


def _step_schema_version(document: MutableMapping[str, object]) -> None:
    document["schema_version"] = CURRENT_SCHEMA_VERSION


def _step_flatten_factors(document: MutableMapping[str, object]) -> None:
    factors = document.get("factors")
    if isinstance(factors, MutableMapping) and "factors" in factors:
        document["factors"] = factors["factors"]


def _step_remove_dead_fields(document: MutableMapping[str, object]) -> None:
    for section, key in REMOVED_FIELDS:
        block = document.get(section)
        if isinstance(block, MutableMapping) and key in block:
            del block[key]


def _step_unary_aliases(document: MutableMapping[str, object]) -> None:
    factors = document.get("factors")
    if not isinstance(factors, MutableSequence):
        return
    for factor in factors:
        graph = factor.get("graph") if isinstance(factor, MutableMapping) else None
        nodes = graph.get("nodes") if isinstance(graph, MutableMapping) else None
        if not isinstance(nodes, MutableSequence):
            continue
        for node in nodes:
            if not isinstance(node, MutableMapping) or node.get("kind") != "unary":
                continue
            alias = _UNARY_ALIASES.get(str(node.get("operator")))
            if alias is None:
                continue
            kind, operator = alias
            node["kind"] = kind
            node["operator"] = operator
            # `periods`는 unary lag 전용이라 alias 노드에서는 무시되던 값이다. 1.1 cross_sectional
            # 노드에는 없는 키이므로 unknown key로 실패하지 않게 버린다.
            node.pop("periods", None)


# 순서가 의미를 가진다: 평탄화 뒤에 노드를 훑는다.
UPGRADE_STEPS: tuple[tuple[str, UpgradeStep], ...] = (
    ("schema_version", _step_schema_version),
    ("flatten_factors", _step_flatten_factors),
    ("remove_dead_fields", _step_remove_dead_fields),
    ("unary_aliases", _step_unary_aliases),
)


def is_legacy_document(document: Mapping[str, object]) -> bool:
    return document.get("schema_version") == LEGACY_SCHEMA_VERSION


def apply_upgrade_steps(document: MutableMapping[str, object]) -> None:
    """Mutate a 1.0 document (plain or ruamel containers) into 1.1 in place."""
    if not is_legacy_document(document):
        raise NotALegacyDocumentError(
            "only schema 1.0 documents can be upgraded — "
            f"schema_version={document.get('schema_version')!r} "
            f"expected={LEGACY_SCHEMA_VERSION!r}"
        )
    for _name, step in UPGRADE_STEPS:
        step(document)


def upgrade_document_1_0(document: Mapping[str, object]) -> dict[str, object]:
    """Return a new 1.1 document tree; the input is not modified."""
    upgraded: dict[str, object] = copy.deepcopy(dict(document))
    apply_upgrade_steps(upgraded)
    return upgraded
