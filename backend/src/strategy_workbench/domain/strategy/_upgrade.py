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
    """문서가 스스로 은퇴 버전이라고 적었는가. 버전 **문자열만** 본다."""
    return document.get("schema_version") == LEGACY_SCHEMA_VERSION


def is_upgradeable_document(document: Mapping[str, object]) -> bool:
    """이 문서에 1.0 → 1.1 변환을 적용할 수 있는가 — 업그레이드 가능 판정의 유일한 owner.

    버전 문자열이 은퇴 버전이거나, 버전 줄은 현재 판인데 **본문이 옛 판 모양**이면 참이다. 두
    번째 갈래가 필요한 이유는 진단과 판정이 갈라지면 화면이 모순되기 때문이다(P1-05 1차 리뷰
    DEFECT-P105-001): `structure.legacy_shape` 진단은 "업그레이드하세요"라고 시키고 배너까지
    띄우는데, 판정이 버전 문자열만 보면 버튼이 반드시 422로 끝나 사용자에게 남는 길이 없다.
    진단이 시키는 일은 눌러서 되는 것이 계약이라, 진단을 만드는 `legacy_shape_hints`와 이 판정이
    같은 조건을 읽는다.

    변환 자체는 어느 갈래든 같다. step 들은 옛 판 모양에만 반응하고(`flatten_factors` 는 factors
    가 mapping 일 때만, `unary_aliases` 는 `kind: unary` 일 때만), `schema_version` step 이 버전
    줄을 결과 버전으로 정규화한다. 그래서 버전 줄이 이미 현재 판이어도 결과는 같다.
    """
    return is_legacy_document(document) or bool(legacy_shape_hints(document))


# 1.0에서만 쓰던 문법이 놓이는 JSON Pointer → 사용자가 읽을 한글 힌트(P1-05).
#
# 판정 조건은 위 step들이 이미 아는 것과 같다. 어떤 문법이 1.0 것인지를 두 벌 적지 않으려고 step과
# 같은 모양을 읽는다: step이 바뀌면 이 함수도 같은 PR에서 바뀐다.
def legacy_shape_hints(document: Mapping[str, object]) -> dict[str, str]:
    """`schema_version`은 현재 버전인데 본문만 1.0인 문서에서, 1.0 문법이 놓인 자리와 힌트.

    hydrate가 같은 pointer에 낸 구조 오류를 이 힌트로 바꿔 단다(`structure.legacy_shape`).
    `expected a sequence, got dict` 같은 문장만으로는 "이건 예전 문법"이라는 사실이 보이지 않는다.
    """
    hints: dict[str, str] = {}
    factors = document.get("factors")
    if isinstance(factors, Mapping) and "factors" in factors:
        hints["/factors"] = (
            "1.0 문법입니다. factors 아래에 또 factors 목록을 두던 방식이라 지금 버전에서는 읽지 "
            "못합니다. 안쪽 목록을 factors 바로 아래로 올리거나 업그레이드하세요 — "
            f"expected=sequence got={type(factors).__name__}"
        )
    for section, key in REMOVED_FIELDS:
        block = document.get(section)
        if isinstance(block, Mapping) and key in block:
            hints[f"/{section}/{key}"] = (
                f"1.0에서만 쓰던 키입니다. 지금 버전은 읽지 않으니 지우거나 업그레이드하세요 — "
                f"got={key!r} section={section!r}"
            )
    if isinstance(factors, (list, tuple)):
        for factor_index, factor in enumerate(factors):
            graph = factor.get("graph") if isinstance(factor, Mapping) else None
            nodes = graph.get("nodes") if isinstance(graph, Mapping) else None
            if not isinstance(nodes, (list, tuple)):
                continue
            for node_index, node in enumerate(nodes):
                if not isinstance(node, Mapping) or node.get("kind") != "unary":
                    continue
                operator = str(node.get("operator"))
                alias = _UNARY_ALIASES.get(operator)
                if alias is None:
                    continue
                kind, renamed = alias
                # `unary` kind 자체는 1.1에도 있다. 걸리는 자리는 은퇴한 operator 값이다.
                hints[f"/factors/{factor_index}/graph/nodes/{node_index}/operator"] = (
                    f"1.0 문법입니다. unary {operator}는 지금 버전에서 {kind}의 {renamed}로 "
                    f"옮겨졌습니다. kind와 operator를 함께 바꾸거나 업그레이드하세요 — "
                    f"got=unary/{operator} expected={kind}/{renamed}"
                )
    return hints


def apply_upgrade_steps(document: MutableMapping[str, object]) -> None:
    """Mutate a 1.0 document (plain or ruamel containers) into 1.1 in place.

    받아 주는 조건의 owner 는 `is_upgradeable_document` 하나다 — 버전 줄이 은퇴 버전이거나 본문이
    옛 판 모양이면 변환한다. 어느 쪽도 아닌 문서는 그대로 fail-closed 다(저장 row 읽기 경로가
    이 예외에 기대고 있다: `adapters/outbound/strategy_sqlite/_record_codec.py`).
    """
    if not is_upgradeable_document(document):
        raise NotALegacyDocumentError(
            "only schema 1.0 documents or bodies still written in 1.0 shapes can be upgraded — "
            f"schema_version={document.get('schema_version')!r} "
            f"expected={LEGACY_SCHEMA_VERSION!r} legacy_shapes=0"
        )
    for _name, step in UPGRADE_STEPS:
        step(document)


def upgrade_document_1_0(document: Mapping[str, object]) -> dict[str, object]:
    """Return a new 1.1 document tree; the input is not modified."""
    upgraded: dict[str, object] = copy.deepcopy(dict(document))
    apply_upgrade_steps(upgraded)
    return upgraded
