"""은퇴 schema 문서를 현재 버전으로 올리는 변환 — 규칙의 유일한 owner (spec D3·D7).

**버전 디스패치다.** `UPGRADE_STEPS` 는 from-version → 그 버전을 다음 버전으로 올리는 step 들이고,
`apply_upgrade_steps` 가 문서의 버전에서 `CURRENT_SCHEMA_VERSION` 까지 단계를 순서대로 적용한다.
단계마다 그 단계의 목표 버전을 찍고, 그 단계가 없애야 하는 옛 모양이 남았는지 검증한다. step 을
한 줄로 이어 붙이면 1.0 문서가 1.1 step 과 1.2 step 을 한 번에 맞아 중간 상태 검증이 사라지고,
1.1 입력은 받을 자리가 없다.

두 입력 경로가 같은 체인을 쓴다.

- dict 경로: `upgrade_document`. 업그레이드 API 가 대조할 기대 tree, repository codec 이 저장된
  은퇴 버전 row 를 읽을 때.
- source 경로: ruamel round-trip CST(CommentedMap/CommentedSeq)에 `apply_upgrade_steps` 를 그대로
  적용해 주석·순서를 보존한다. 각 step 은 `MutableMapping`/`MutableSequence` API 만 쓰므로 plain
  dict/list 와 ruamel 컨테이너 양쪽에서 같은 결과를 낸다. 규칙표를 두 벌 두지 않는다.

변환 항목:

- 1.0 → 1.1 (spec D1): `factors.factors` 평탄화, 미사용 필드 3개 제거, `unary` alias →
  `cross_sectional`(`neutralize` → `demean`).
- 1.1 → 1.2 (spec D3 S1~S4·S7, D7): `data`·`execution`·`graph.missing_policy` 제거(값은 결과의
  `environment` 로 돌려준다), `signal.normalization: none` 명시, `saved_*` 노드는 거절.

기본값을 채우거나 지우지 않는다: 생략은 작성자의 선택이다. `normalization` 만 예외인데, 1.2 의
기본값(`rank`)이 1.1 의 의미(원시값 가중 합)와 달라서 생략한 채 두면 문서의 뜻이 바뀌기 때문이다.
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Callable, Mapping, MutableMapping, MutableSequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, get_args

from ._models import CURRENT_SCHEMA_VERSION, SignalNormalization, StrategySpec, WeightingMethod

UpgradeStep = Callable[[MutableMapping[str, object]], None]

# 1.0 전용 문법과 1.1 까지 문서에 있던 실행 설정 섹션. 단계가 없애야 하는 모양이고, 단계 뒤에
# 이 모양이 남았는지가 그 단계의 검증 조건이다.
REMOVED_FIELDS: tuple[tuple[str, str], ...] = (
    ("signal", "method"),
    ("signal", "entry_percentile"),
    ("execution", "order_style"),
)
RETIRED_EXECUTION_SECTIONS: tuple[str, ...] = ("data", "execution")
# 1.2 의 노드 union 에 없는 kind(spec D3 S7). 실행 경로가 원래 없었으므로 잃는 것이 없다.
RETIRED_NODE_KINDS: tuple[str, ...] = ("saved_factor", "saved_subgraph")
# 1.1 `FactorGraph.missing_policy` 의 모델 기본값. 1.1 모델은 지워졌고 이 값은 더 바뀌지 않는 과거
# 사실이다. 실행 설정 기본값(`domain/backtest` 의 `DEFAULT_MISSING_POLICY`)과 값은 같지만
# import 하면 `domain.strategy → domain.backtest` 순환이 된다. 결측 정책을 생략한 1.1 팩터의
# 실효 값이다.
_RETIRED_DEFAULT_MISSING_POLICY = "drop"

# `unary` operator → (1.1 kind, 1.1 operator). rank/zscore/winsorize는 평가 코드가 cross_sectional로
# 치환하던 순수 alias였고, neutralize는 cross-sectional demean이었다 (P1-02에서 제거).
_UNARY_ALIASES: Mapping[str, tuple[str, str]] = {
    "rank": ("cross_sectional", "rank"),
    "zscore": ("cross_sectional", "zscore"),
    "winsorize": ("cross_sectional", "winsorize"),
    "neutralize": ("cross_sectional", "demean"),
}

# 최상위 키의 문서 순서. 없던 섹션을 새로 넣을 때(1.1 템플릿에는 `signal` 이 없다) 모델 필드
# 순서 자리에 넣는다 — 순서의 SoT 는 모델이고 여기 다시 적지 않는다.
_TOP_LEVEL_ORDER: tuple[str, ...] = (
    "schema_version",
    *(f.name for f in dataclasses.fields(StrategySpec) if f.name != "identity"),
)

# 업그레이드 응답의 warning 코드 전부. 닫힌 `Literal` 이라 OpenAPI·생성 SDK 에 enum 으로 나가고
# (화면이 코드별 문장을 붙인다, P3-02), 구조 진단 코드(`STRUCTURE_CODES`)처럼 목록에 없는 코드가
# 조용히 생기지 않게 런타임 게이트도 둔다. 실행 설정을 옮기지 못했다는 코드는 `RunEnvironment` 를
# 만드는 application 이 내지만, 업그레이드 어휘의 owner 는 이 모듈이다.
UpgradeWarningCode = Literal[
    "strategy_document.upgrade_missing_policy_conflict",
    "strategy_document.upgrade_weighting_rule_changed",
    "strategy_document.upgrade_environment_unavailable",
]
MISSING_POLICY_CONFLICT_CODE: UpgradeWarningCode = (
    "strategy_document.upgrade_missing_policy_conflict"
)
WEIGHTING_RULE_CHANGED_CODE: UpgradeWarningCode = "strategy_document.upgrade_weighting_rule_changed"
ENVIRONMENT_UNAVAILABLE_CODE: UpgradeWarningCode = (
    "strategy_document.upgrade_environment_unavailable"
)
UPGRADE_WARNING_CODES: frozenset[str] = frozenset(get_args(UpgradeWarningCode))


@dataclass(frozen=True)
class UpgradeWarning:
    """업그레이드는 됐지만 사용자가 알아야 하는 사실 하나. `message` 는 한글 문장 + 기계 디테일."""

    code: UpgradeWarningCode
    pointer: str
    message: str

    def __post_init__(self) -> None:
        if self.code not in UPGRADE_WARNING_CODES:
            raise ValueError(
                "upgrade warning code has no owner — add it to UPGRADE_WARNING_CODES: "
                f"code={self.code!r} pointer={self.pointer!r}"
            )


@dataclass(frozen=True)
class RetiredExecutionSettings:
    """1.1 문서가 실행 설정으로 갖고 있던 원문 값 — 1.1 → 1.2 단계가 문서에서 떼어 낸 것.

    값은 문서에 적힌 그대로다(타입 변환 없음). `RunEnvironment` 로 바꾸는 규칙은 그 모델의
    owner 인 `domain/backtest` 가 갖는다 — 이 노드가 `domain.backtest` 를 import 하면 기존 반대
    방향(`domain.backtest → domain.strategy`)과 순환이 된다. 섹션이 없거나 mapping 이 아니면
    빈 mapping 이다.
    """

    data_section: Mapping[str, object]
    execution_section: Mapping[str, object]
    # 첫 팩터의 `graph.missing_policy` 값과 그 자리. 어느 팩터에도 없으면 둘 다 None 이다.
    missing_policy: object | None
    missing_policy_pointer: str | None


@dataclass(frozen=True)
class UpgradeOutcome:
    """체인 적용 결과.

    `tree` 는 `upgrade_document` 면 새 dict, `apply_upgrade_steps` 면 받은 그 객체다.

    `environment` 는 1.1 → 1.2 단계를 지났을 때만 있다(`until` 로 그 앞에서 멈추면 None).
    """

    tree: MutableMapping[str, object]
    source_version: str
    environment: RetiredExecutionSettings | None
    warnings: tuple[UpgradeWarning, ...]


class NotUpgradeableDocumentError(ValueError):
    """이 문서에 적용할 업그레이드 체인이 없거나, 체인이 문서를 끝까지 올리지 못했다.

    저장 row 읽기(`adapters/outbound/strategy_sqlite/_record_codec.py`)가 이 예외에 기대 fail-closed
    다: 모르는 버전을 현재 모델로 해석하면 사용자가 저장한 적 없는 값이 그 revision 의 사실로
    화면에 뜬다.

    `stage`·`older_shapes` 는 선언된 은퇴 버전보다 앞선 모양이 본문에 섞여 거절했을 때만 찬다(그
    모양의 버전과 자리). compile 진단이 이 값으로 고칠 곳을 말한다(`upgrade_refusal`).
    """

    def __init__(
        self,
        schema_version: object,
        reason: str,
        *,
        stage: str | None = None,
        older_shapes: tuple[str, ...] = (),
    ) -> None:
        super().__init__(
            f"document cannot be upgraded — {reason} — got={schema_version!r} "
            f"current={CURRENT_SCHEMA_VERSION!r} retired={sorted(FROZEN_SCHEMA_VERSIONS)}"
        )
        self.schema_version = schema_version
        self.stage = stage
        self.older_shapes = older_shapes


class UpgradeUnsupportedNodeError(ValueError):
    """1.2 문법에 없는 `saved_*` 노드가 있어 업그레이드를 거절한다(spec D7).

    조용히 지우면 팩터 그래프의 뜻이 바뀐다. 이 노드는 실행 경로가 원래 없었으므로(1.1 에서도
    실행 거부) 거절해도 사용자가 잃는 실행 결과는 없다.
    """

    code = "strategy_document.upgrade_unsupported_node"

    def __init__(self, pointer: str, kind: object) -> None:
        super().__init__(
            "저장한 팩터·부분 그래프를 참조하는 노드는 새 형식에 없어 업그레이드할 수 없습니다. "
            "그 노드를 지우거나 참조하던 그래프를 직접 옮겨 적은 뒤 다시 업그레이드하세요 — "
            f"code={self.code} pointer={pointer} kind={kind!r} "
            f"unsupported={list(RETIRED_NODE_KINDS)}"
        )
        self.pointer = pointer
        self.kind = kind


def is_frozen_schema_version(schema_version: str) -> bool:
    """저장 row가 동결(업그레이드 필요) 이력인가: 현재 버전이 아닌 모든 버전(spec D2).

    port의 `StrategyRevisionRecord.requires_upgrade`와 SQLite codec의 동결 읽기 분기가 같은 술어를
    쓴다(Phase 1 감사 DEFECT-P1X-003: `== "1.0"`과 `!= CURRENT`가 갈리면 1.2 도입 때 1.1 row가
    hydrate 실패로 500이 된다). 열린 집합이라 "읽어도 되는 버전"은 이 술어가 아니라
    `upgrade_refusal` 이 판정한다.
    """
    return schema_version != CURRENT_SCHEMA_VERSION


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
    for _pointer, node in _graph_nodes(document):
        if node.get("kind") != "unary":
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


def _step_reject_saved_nodes(document: MutableMapping[str, object]) -> None:
    for pointer, node in _graph_nodes(document):
        kind = node.get("kind")
        if kind in RETIRED_NODE_KINDS:
            raise UpgradeUnsupportedNodeError(f"{pointer}/kind", kind)


def _step_strip_execution_settings(document: MutableMapping[str, object]) -> None:
    """실행 설정 세 자리를 제거한다. 값은 단계 앞에서 `_retired_settings` 가 이미 읽었다."""
    for section in RETIRED_EXECUTION_SECTIONS:
        document.pop(section, None)
    for _pointer, graph in _graphs(document):
        graph.pop("missing_policy", None)


def _step_explicit_normalization(document: MutableMapping[str, object]) -> None:
    """1.1 의 합성(원시값 가중 합)을 `signal.normalization: none` 으로 적어 뜻을 보존한다."""
    signal = document.get("signal")
    if isinstance(signal, MutableMapping):
        if "normalization" not in signal:
            signal["normalization"] = SignalNormalization.NONE.value
        return
    if signal is None:  # 섹션이 없거나 값이 비어 있다
        _put_in_model_order(document, "signal", {"normalization": SignalNormalization.NONE.value})
    # mapping 도 null 도 아니면 1.1 에서도 구조 오류였던 문서다. 손대지 않고 hydrate 가 짚게 둔다.


# from-version → 그 버전을 다음 버전으로 올리는 step. 키 순서가 체인 순서이고, 단계 안의 순서도
# 의미를 가진다(평탄화 뒤에 노드를 훑는다, 거절은 무엇을 지우기 전에 한다). 목표 버전은 체인에서
# 다음 키(마지막이면 현재 버전)이며 단계 뒤에 디스패처가 찍는다.
UPGRADE_STEPS: Mapping[str, tuple[tuple[str, UpgradeStep], ...]] = MappingProxyType(
    {
        "1.0": (
            ("flatten_factors", _step_flatten_factors),
            ("remove_dead_fields", _step_remove_dead_fields),
            ("unary_aliases", _step_unary_aliases),
        ),
        "1.1": (
            ("reject_saved_nodes", _step_reject_saved_nodes),
            ("strip_execution_settings", _step_strip_execution_settings),
            ("explicit_normalization", _step_explicit_normalization),
        ),
    }
)
# 동결 이력으로 읽어 줄 수 있는 은퇴 버전의 **닫힌 집합**. 체인 키에서 유도하므로 버전을 더할 때
# 한 곳만 고친다.
FROZEN_SCHEMA_VERSIONS: frozenset[str] = frozenset(UPGRADE_STEPS)
UPGRADE_CHAIN: tuple[str, ...] = (*UPGRADE_STEPS, CURRENT_SCHEMA_VERSION)
# 실행 설정을 문서에 갖고 있던 마지막 버전. 이 단계에 들어가기 직전에 값을 읽어 둔다.
_EXECUTION_SETTINGS_STAGE = "1.1"


def _retired_setting_pointers(document: Mapping[str, object]) -> tuple[str, ...]:
    pointers = [f"/{section}" for section in RETIRED_EXECUTION_SECTIONS if section in document]
    pointers.extend(
        f"{pointer}/missing_policy"
        for pointer, graph in _graphs(document)
        if "missing_policy" in graph
    )
    return tuple(pointers)


def _legacy_shape_pointers(document: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(legacy_shape_hints(document))


# 단계의 검증 조건: 그 단계가 없애야 하는 모양이 남은 자리. 비어 있어야 목표 버전을 인정한다.
_STAGE_LEFTOVERS: Mapping[str, Callable[[Mapping[str, object]], tuple[str, ...]]] = (
    MappingProxyType({"1.0": _legacy_shape_pointers, "1.1": _retired_setting_pointers})
)


def _version_text(value: object) -> str | None:
    """버전 줄의 값을 비교할 수 있는 문자열로. 따옴표 없이 쓴 `1.0` 은 YAML 이 float 로 읽는다."""
    return None if value is None else str(value)


def _chain_start(document: Mapping[str, object]) -> str:
    """체인을 시작할 버전 — 업그레이드 가능 판정의 유일한 owner.

    **문서가 스스로 선언한 버전을 믿는다**(Phase 2 감사 NB-1). 체인은 선언된 은퇴 버전에서 시작하고
    그보다 앞선 단계는 타지 않는다. 그래서 받는 문서는 버전 줄이 은퇴 버전인 것뿐이다.

    - 버전 줄이 현재 판인데 1.0 모양이 섞였으면 업그레이드가 아니라 제자리에서 고칠 구조 오류다
      (hydrate 가 `structure.legacy_shape` 로 짚는다). 체인을 1.0 부터 태우면 1.1 → 1.2 단계가
      `normalization: none` 을 조용히 넣어 1.2 기본값 `rank` 의 뜻을 바꾸고, 문서에 없던 실행
      설정 자리를 짚는 warning 을 낸다.
    - 선언된 은퇴 버전보다 앞선 모양이 본문에 섞였으면(1.1 선언 + 1.0 키) 거절한다. 어느 단계부터
      태울지 문서가 말하는 것과 본문이 다르다.
    - 버전 줄이 없거나 모르는 버전(미래 버전·손상된 값)이면 거절한다. 모르는 버전을 1.x 로 강등해
      읽으면 그 버전이 지우거나 뜻을 바꾼 키가 현재 모델로 조용히 해석된다(버전 상한, P1-05 2차
      리뷰 P3-7·BACKLOG-010). 저장 row 읽기가 이 거절에 기대 fail-closed 다.

    Raises:
        NotUpgradeableDocumentError: 위 거절 사유 중 하나일 때.
    """
    raw = document.get("schema_version")
    version = _version_text(raw)
    if version in FROZEN_SCHEMA_VERSIONS:
        for stage in UPGRADE_CHAIN[: UPGRADE_CHAIN.index(version)]:
            older = _STAGE_LEFTOVERS[stage](document)
            if older:
                raise NotUpgradeableDocumentError(
                    raw,
                    "the body carries shapes older than the declared version — "
                    f"stage={stage} pointers={list(older)}",
                    stage=stage,
                    older_shapes=older,
                )
        return version
    if version == CURRENT_SCHEMA_VERSION:
        shapes = list(legacy_shape_hints(document))
        raise NotUpgradeableDocumentError(
            raw,
            "schema_version is already current; retired-version shapes in a current document "
            f"are structure errors — fix them in place: shapes={shapes}"
            if shapes
            else "schema_version is already current and the body has no retired-version shapes",
        )
    if version is None:
        raise NotUpgradeableDocumentError(
            raw, "schema_version is missing — the chain starts at the declared retired version"
        )
    raise NotUpgradeableDocumentError(
        raw, "schema_version is neither current nor a known retired version"
    )


def upgrade_refusal(
    document: Mapping[str, object],
) -> NotUpgradeableDocumentError | UpgradeUnsupportedNodeError | None:
    """이 문서를 업그레이드 체인에 태울 수 없는 사유. 태울 수 있으면 None.

    업그레이드 가능 판정의 공개 입구다. compile 진단(hydrate)이 이 사유로 업그레이드 배너를 띄울
    코드와 고칠 곳을 알리는 코드를 가른다. 업그레이더가 거절할 문서에 배너를 띄우면 누를 때마다
    실패하는 버튼이 된다(#267 DEFECT-2).

    체인 시작 판정은 `_chain_start` 이고, 단계 안의 거절(은퇴 노드 `saved_*`)은 사본에 체인을 태워
    업그레이드 API 와 같은 단계가 판정한다(#357 C-P3-10). 단계 뒤 검증에서 거절되는 드문 문서(세
    겹 factors)는 배너가 뜬 뒤 422가 된다(알려진 한계, #322 리뷰 P3-3).
    """
    try:
        _chain_start(document)
    except NotUpgradeableDocumentError as refusal:
        return refusal
    try:
        upgrade_document(document)
    except UpgradeUnsupportedNodeError as refusal:
        return refusal
    except NotUpgradeableDocumentError:
        return None  # 위 알려진 한계
    return None


# 1.0에서만 쓰던 문법이 놓이는 JSON Pointer → 사용자가 읽을 한글 힌트(P1-05).
#
# 판정 조건은 1.0 단계 step들이 이미 아는 것과 같다. 어떤 문법이 1.0 것인지를 두 벌 적지 않으려고
# step과 같은 모양을 읽는다: step이 바뀌면 이 함수도 같은 PR에서 바뀐다.
def legacy_shape_hints(document: Mapping[str, object]) -> dict[str, str]:
    """1.0 문법이 놓인 자리와, 그 자리를 지금 문법으로 고치는 한글 힌트.

    hydrate가 현재 판 문서에서 같은 pointer에 낸 구조 오류를 이 힌트로 바꿔 단다
    (`structure.legacy_shape`). `expected a sequence, got dict` 같은 문장만으로는 "이건 예전
    문법"이라는 사실이 보이지 않는다. 현재 판 문서는 업그레이드 대상이 아니므로(NB-1) 힌트는
    업그레이드를 시키지 않고 제자리 수정만 말한다 — 진단이 시키는 일은 해서 되어야 한다.
    같은 함수가 1.0 단계의 검증 조건(`_STAGE_LEFTOVERS`)이기도 하다.
    """
    hints: dict[str, str] = {}
    factors = document.get("factors")
    if isinstance(factors, Mapping) and "factors" in factors:
        hints["/factors"] = (
            "1.0 문법입니다. factors 아래에 또 factors 목록을 두던 방식이라 지금 버전에서는 읽지 "
            "못합니다. 안쪽 목록을 factors 바로 아래로 올리세요 — "
            f"expected=sequence got={type(factors).__name__}"
        )
    for section, key in REMOVED_FIELDS:
        block = document.get(section)
        if isinstance(block, Mapping) and key in block:
            hints[f"/{section}/{key}"] = (
                f"1.0에서만 쓰던 키입니다. 지금 버전은 읽지 않으니 지우세요 — "
                f"got={key!r} section={section!r}"
            )
    for pointer, node in _graph_nodes(document):
        if node.get("kind") != "unary":
            continue
        operator = str(node.get("operator"))
        alias = _UNARY_ALIASES.get(operator)
        if alias is None:
            continue
        kind, renamed = alias
        # `unary` kind 자체는 1.1에도 있다. 걸리는 자리는 은퇴한 operator 값이다.
        hints[f"{pointer}/operator"] = (
            f"1.0 문법입니다. unary {operator}는 지금 버전에서 {kind}의 {renamed}로 "
            f"옮겨졌습니다. kind와 operator를 함께 바꾸세요 — "
            f"got=unary/{operator} expected={kind}/{renamed}"
        )
    return hints


def _graphs(document: Mapping[str, object]) -> list[tuple[str, MutableMapping[str, object]]]:
    """평탄한 factors 목록의 팩터 그래프와 그 pointer. 옛 모양(두 겹 factors)은 건너뛴다."""
    factors = document.get("factors")
    if not isinstance(factors, MutableSequence):
        return []
    found: list[tuple[str, MutableMapping[str, object]]] = []
    for index, factor in enumerate(factors):
        graph = factor.get("graph") if isinstance(factor, Mapping) else None
        if isinstance(graph, MutableMapping):
            found.append((f"/factors/{index}/graph", graph))
    return found


def _graph_nodes(
    document: Mapping[str, object],
) -> list[tuple[str, MutableMapping[str, object]]]:
    found: list[tuple[str, MutableMapping[str, object]]] = []
    for pointer, graph in _graphs(document):
        nodes = graph.get("nodes")
        if not isinstance(nodes, MutableSequence):
            continue
        for index, node in enumerate(nodes):
            if isinstance(node, MutableMapping):
                found.append((f"{pointer}/nodes/{index}", node))
    return found


def _insert_before(
    document: MutableMapping[str, object], anchor: str | None, key: str, value: object
) -> None:
    """`anchor` 앞에 새 키를 넣는다(`MutableMapping` API 만 써서 ruamel CST 에도 같다).

    ruamel 은 키를 지웠다 다시 넣어도 그 키의 주석 슬롯(`ca.items[key]`)을 이름으로 들고 있어
    주석이 따라온다. `anchor` 가 없으면 끝에 붙인다.
    """
    if anchor is None or anchor not in document:
        document[key] = value
        return
    keys = list(document)
    moved = [(name, document.pop(name)) for name in keys[keys.index(anchor) :]]
    document[key] = value
    for name, moved_value in moved:
        document[name] = moved_value


def _put_in_model_order(document: MutableMapping[str, object], key: str, value: object) -> None:
    """최상위 키를 쓴다. 없던 키면 모델 필드 순서의 자리에 넣는다."""
    if key in document:
        document[key] = value
        return
    following = _TOP_LEVEL_ORDER[_TOP_LEVEL_ORDER.index(key) + 1 :]
    anchor = next((name for name in document if name in following), None)
    if key == "schema_version":  # 버전 줄은 맨 앞이다(모델 밖 키가 앞에 있어도)
        anchor = next(iter(document), None)
    _insert_before(document, anchor, key, value)


def _factor_label(document: Mapping[str, object], graph_pointer: str) -> str:
    """warning 문장에 쓸 팩터 이름: `factor_id`, 없으면 자리(`/factors/N`)."""
    factor_pointer = graph_pointer.removesuffix("/graph")
    factors = document.get("factors")
    index = int(factor_pointer.rsplit("/", 1)[1])
    factor = factors[index] if isinstance(factors, MutableSequence) else None
    factor_id = factor.get("factor_id") if isinstance(factor, Mapping) else None
    return str(factor_id) if factor_id is not None else factor_pointer


def _plain_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _retired_settings(
    document: Mapping[str, object],
) -> tuple[RetiredExecutionSettings, list[UpgradeWarning]]:
    """1.1 단계에 들어가기 직전의 문서에서 실행 설정 원문 값과 알릴 사실을 읽는다(읽기만)."""
    warnings: list[UpgradeWarning] = []
    # 팩터마다 **실효** 결측 정책. 생략한 팩터는 1.1 기본값으로 계산됐으므로 그 값으로 센다 — 명시한
    # 팩터만 세면 "a 생략(drop) + b zero" 가 warning 없이 zero 가 되어 a 의 결측 처리가 조용히
    # 바뀐다(P2-09 리뷰 DEFECT-P1-1, main P2-02 브리지와 같은 판정).
    policies = [
        (
            _factor_label(document, pointer),
            f"{pointer}/missing_policy",
            graph.get("missing_policy", _RETIRED_DEFAULT_MISSING_POLICY),
            "missing_policy" not in graph,
        )
        for pointer, graph in _graphs(document)
    ]
    first_label, first_pointer, first_policy, _ = (
        policies[0] if policies else (None, None, None, False)
    )
    conflicts = [entry for entry in policies[1:] if entry[2] != first_policy]
    if conflicts:
        changed = [
            f"{label}: {value!r}->{first_policy!r}" + (" (생략=1.1 기본값)" if omitted else "")
            for label, _pointer, value, omitted in conflicts
        ]
        warnings.append(
            UpgradeWarning(
                MISSING_POLICY_CONFLICT_CODE,
                conflicts[0][1],
                "팩터마다 결측 처리가 달라 첫 팩터의 값만 실행 설정으로 옮겼습니다. 새 형식에서는 "
                "결측 처리가 전략 전체에 하나라, 다른 값을 쓰던 팩터는 결측 처리가 바뀝니다. 실행 "
                "설정에서 확인하세요 — "
                f"used={first_policy!r} from={first_label}({first_pointer}) changed={changed}",
            )
        )
    portfolio = document.get("portfolio")
    if (
        isinstance(portfolio, Mapping)
        and portfolio.get("weighting") == WeightingMethod.FACTOR_SCORE.value
    ):
        # P2-04 결정 5: 비중 규칙(`_margin_strengths`)이 바뀌었다. 선정·보유 종목은 같다.
        warnings.append(
            UpgradeWarning(
                WEIGHTING_RULE_CHANGED_CODE,
                "/portfolio/weighting",
                "점수 비례 비중은 새 형식에서 계산 규칙이 바뀌었습니다. 고르는 종목은 같지만 목표 "
                "비중은 예전 결과와 다를 수 있습니다 — "
                f"weighting={WeightingMethod.FACTOR_SCORE.value} "
                "same=selection,holdings differs=target_weights",
            )
        )
    settings = RetiredExecutionSettings(
        data_section=_plain_mapping(document.get("data")),
        execution_section=_plain_mapping(document.get("execution")),
        missing_policy=first_policy,
        missing_policy_pointer=first_pointer,
    )
    return settings, warnings


def apply_upgrade_steps(
    document: MutableMapping[str, object], *, until: str | None = None
) -> UpgradeOutcome:
    """문서(plain 또는 ruamel 컨테이너)를 제자리에서 `until`(기본은 현재 버전)까지 올린다.

    단계마다 목표 버전을 찍고 그 단계의 옛 모양이 남지 않았는지 검증한다. 받아 주는 조건의
    owner 는 `_chain_start` 하나다.

    Raises:
        NotUpgradeableDocumentError: 체인이 없는 문서거나, 단계 뒤에 옛 모양이 남았을 때.
        UpgradeUnsupportedNodeError: 1.2 에 없는 `saved_*` 노드가 있을 때.
        ValueError: `until` 이 체인의 버전이 아닐 때(호출자 오류).
    """
    target = CURRENT_SCHEMA_VERSION if until is None else until
    if target not in UPGRADE_CHAIN:
        raise ValueError(
            f"upgrade target is not in the chain — until={until!r} chain={UPGRADE_CHAIN}"
        )
    original = document.get("schema_version")
    start = _chain_start(document)
    environment: RetiredExecutionSettings | None = None
    warnings: list[UpgradeWarning] = []
    for index in range(UPGRADE_CHAIN.index(start), UPGRADE_CHAIN.index(target)):
        version, next_version = UPGRADE_CHAIN[index], UPGRADE_CHAIN[index + 1]
        if version == _EXECUTION_SETTINGS_STAGE:
            environment, found = _retired_settings(document)
            warnings.extend(found)
        for _name, step in UPGRADE_STEPS[version]:
            step(document)
        _put_in_model_order(document, "schema_version", next_version)
        leftovers = _STAGE_LEFTOVERS[version](document)
        if leftovers:
            raise NotUpgradeableDocumentError(
                original,
                f"stage {version}->{next_version} left retired shapes at {list(leftovers)}",
            )
    return UpgradeOutcome(
        tree=document, source_version=start, environment=environment, warnings=tuple(warnings)
    )


def upgrade_document(tree: Mapping[str, object], *, until: str | None = None) -> UpgradeOutcome:
    """은퇴 버전 문서를 새 tree 로 올린다(입력은 바꾸지 않는다).

    규칙은 `apply_upgrade_steps` 그대로다.
    """
    return apply_upgrade_steps(copy.deepcopy(dict(tree)), until=until)
