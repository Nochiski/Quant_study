"""schema 1.0 → 1.1 문서 업그레이드 변환 — 규칙의 유일한 owner (spec D3).

**현재 버전은 1.2 이고 이 모듈은 아직 1.1 까지만 올린다.** 그래서 1.0 문서의 업그레이드 결과는
저장·실행할 수 없는 중간 산출물이다(`structure.unsupported_schema_version`). 1.1 → 1.2 step 과
버전 디스패치(`upgrade_document`)는 P2-09 가 추가한다(spec D7).

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
# 1.0 step 이 문서에 찍는 **자기 목표 버전**. `CURRENT_SCHEMA_VERSION` 을 찍으면 1.2 에서
# "1.2 라고 적혀 있지만 `data`·`execution` 이 남은 문서"가 나와 중간 단계 검증이 사라진다
# (spec D7 이 요구하는 버전 디스패치의 선행 조건). 1.1 → 1.2 step 과 체인 적용은 P2-09 다.
LEGACY_UPGRADE_TARGET_VERSION = "1.1"

# 저장 row 로 읽어 줄 수 있는 은퇴 버전의 **닫힌 집합**. `is_frozen_schema_version` 은
# "현재 버전이 아닌 모든 것"이라 집합이 열려 있어서, 저장된 row 를 현재 버전으로 해석해도 되는지
# 판정하는 데는 쓸 수 없다 — 그 술어만 믿으면 미래 버전(`"1.3"`)이나 손상된 값(`"9.9"`)이 조용히
# 현재 모델로 해석된다. 키를 **지우거나 의미를 바꾼** 버전은 1.2 기본값으로 채워져 읽히고,
# `spec_hash` 검증은 변환 전에 끝나므로 그 변형을 잡지 못한다(P2-03 리뷰 P2-02).
# P2-09 가 `FROZEN_SCHEMA_VERSIONS` 로 이름을 바꾸며 업그레이드 체인의 키 집합과 합친다.
RETIRED_SCHEMA_VERSIONS: frozenset[str] = frozenset(
    {LEGACY_SCHEMA_VERSION, LEGACY_UPGRADE_TARGET_VERSION}
)


class UnknownSchemaVersionError(ValueError):
    """저장 row 의 `schema_version` 이 알려진 은퇴 버전도 현재 버전도 아니다.

    더 새 backend 가 쓴 row 를 구 backend 가 읽는 다운그레이드이거나, 손으로 고친 값이다. 둘 다
    현재 모델로 해석하면 사용자가 저장한 적 없는 값이 그 revision 의 사실로 화면에 뜬다.
    """

    def __init__(self, schema_version: str) -> None:
        super().__init__(
            "stored schema_version is neither current nor a known retired version -- "
            f"got={schema_version!r} current={CURRENT_SCHEMA_VERSION!r} "
            f"retired={sorted(RETIRED_SCHEMA_VERSIONS)}"
        )
        self.schema_version = schema_version


def require_retired_schema_version(schema_version: str) -> None:
    """저장 row 를 현재 버전으로 변환해도 되는 버전인지 확인한다(fail-closed).

    Raises:
        UnknownSchemaVersionError: 알려진 은퇴 버전이 아닐 때.
    """
    if schema_version not in RETIRED_SCHEMA_VERSIONS:
        raise UnknownSchemaVersionError(schema_version)


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
    document["schema_version"] = LEGACY_UPGRADE_TARGET_VERSION


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


# 1.1 → 1.2 내용 변환: 실행 설정이 전략 문서를 떠난다(spec D3 S1~S3). 세 자리를 지우기만 하면
# 되고, 지운 값에서 `RunEnvironment` 를 만드는 것은 `domain/backtest` 의 몫이다.
#
# **아직 `UPGRADE_STEPS` 에 넣지 않는다.** 넣으면 source 경로(주석 보존 CST)의 1.0 기대 출력이
# 곧바로 1.2 가 되어 `quality_momentum.v1_1.commented.yaml` 골든이 깨진다 — 그 골든의 재배치와
# 응답에 `environment` 를 싣는 일은 버전 디스패치를 세우는 P2-09 의 acceptance 다(spec D7).
# 지금 이 함수를 쓰는 곳은 저장 row 를 읽는 repository codec 하나이며, 그 경로가 없으면 은퇴
# 버전 row 가 P2-09 까지 hydrate 실패로 500 이 된다.
RETIRED_EXECUTION_SECTIONS: tuple[str, ...] = ("data", "execution")


def strip_retired_execution_settings(document: MutableMapping[str, object]) -> None:
    """1.1 문서에서 실행 설정 세 자리를 제거한다(제자리 변경).

    `data`·`execution` 섹션과 팩터마다의 `graph.missing_policy` 다. 1.2 모델에는 이 키들이
    없어서 남겨 두면 `structure.unknown_key` 로 hydrate 가 실패한다.
    """
    for section in RETIRED_EXECUTION_SECTIONS:
        document.pop(section, None)
    factors = document.get("factors")
    if not isinstance(factors, MutableSequence):
        return
    for factor in factors:
        graph = factor.get("graph") if isinstance(factor, MutableMapping) else None
        if isinstance(graph, MutableMapping):
            graph.pop("missing_policy", None)


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
    """Return a new 1.1 document tree; the input is not modified.

    1.1 은 현재 버전이 아니므로(1.2) 결과는 그대로 hydrate 되지 않는다 — P2-09 의 1.1 → 1.2
    step 이 붙어야 실행 가능한 문서가 된다.
    """
    upgraded: dict[str, object] = copy.deepcopy(dict(document))
    apply_upgrade_steps(upgraded)
    return upgraded
