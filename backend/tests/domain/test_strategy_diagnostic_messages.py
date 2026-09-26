"""P1-05 진단 문장 golden: 구조 오류 코드마다 사용자가 읽을 한글 문장 하나를 고정한다.

compile 진단의 `message`는 backend가 한글 문장으로 완성해 보내고 Problems panel이 그대로 보여준다
(`.claude/rules/strategy-workbench-sot.md` authoring 진단 코드 행). 그래서 화면 문구의 golden이
frontend가 아니라 여기 있다. 기계가 읽는 디테일(`got=`·`expected=`·`allowed=`·`missing=`)은 문장
뒤에 남는다(`.claude/rules/error-messages.md`).

이 파일이 지키는 불변식 두 개.

1. 코드마다 문장이 있고, 그 문장이 파서·dataclass 원문이 아니다.
2. 사용자에게 보이는 문장에 영어 문장이 새지 않는다 — 파서가 준 원문은 `detail=` 뒤 기계 디테일로만
   남는다(ruamel·json 메시지는 우리가 쓴 문장이 아니다).
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from strategy_workbench.adapters.outbound.document_codec.facade.codec import RuamelDocumentCodec
from strategy_workbench.application.strategy_authoring.ports.outgoing.document_codec import (
    CodecLimits,
    ParseStatus,
    SourceFormat,
    diagnostic_codes,
)
from strategy_workbench.domain.factor.facade.expression import (
    BinaryNode,
    BinaryOperator,
    FactorGraph,
    FieldNode,
)
from strategy_workbench.domain.factor.facade.validation import validate_factor_graph
from strategy_workbench.domain.strategy._hydrate import _hydrate
from strategy_workbench.domain.strategy.facade.document import (
    CURRENT_SCHEMA_VERSION,
    STRUCTURE_CODES,
    hydrate_strategy_document,
)
from strategy_workbench.domain.strategy.facade.specification import StrategyIdentity

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "strategy_documents"
DRAFT = StrategyIdentity("draft", 0)

# 문장과 기계 디테일을 가르는 구분자. 디테일이 없는 문장은 이 파일에 없다.
_DETAIL_SEPARATOR = " — "
# 문장에는 한글이 있다. 스키마 키·enum 값·타입 이름·`YAML` 같은 고유명은 계약 문자열이라 문장
# 안에 영어로 남지만(협업 언어 규칙), 그것만으로 이루어진 문장은 없다.
_HANGUL = re.compile(r"[가-힣]")
# P1-05 이전 영어 원문. 하나라도 돌아오면 어딘가에서 문장을 되돌린 것이다.
_RETIRED_ENGLISH = (
    "expected a mapping",
    "expected a sequence",
    "expected one of",
    "expected ISO date",
    "expected bool",
    "expected int,",
    "expected str,",
    "expected float,",
    "expected null,",
    "unknown key",
    "unknown kind",
    "is required",
    "invalid YAML —",
    "invalid JSON —",
    "are not allowed",
    "exceeds limit",
    "document root must be",
    "document is empty",
    "duplicate key —",
    "mapping keys must be",
    "unsupported schema_version —",
    "source must contain",
    "non-finite numbers",
)


def _document() -> dict[str, Any]:
    return json.loads((FIXTURES / "quality_momentum.json").read_text(encoding="utf-8"))


def _first_issue(mutate: Any) -> tuple[str, str]:
    """문서를 한 곳 망가뜨리고 첫 구조 진단의 (code, message)를 돌려준다."""
    document = _document()
    mutate(document)
    result = hydrate_strategy_document(document, identity=DRAFT)
    assert not result.ok, "문서가 통과했다 — 이 입력은 구조 오류를 내야 한다"
    return result.issues[0].code, result.issues[0].message


def _reject(
    source: str,
    *,
    format: SourceFormat = SourceFormat.YAML,
    limits: CodecLimits | None = None,
) -> tuple[str, str]:
    parsed = RuamelDocumentCodec(limits or CodecLimits(max_depth=3, max_nodes=40)).parse(
        source, format=format
    )
    assert parsed.status is ParseStatus.REJECTED, "이 입력은 codec이 거절해야 한다"
    return parsed.diagnostics[0].code, parsed.diagnostics[0].message


def _set(document: dict[str, Any], path: str, value: object) -> None:
    *parents, leaf = path.split(".")
    target: Any = document
    for key in parents:
        target = target[int(key)] if isinstance(target, list) else target[key]
    if isinstance(target, list):
        target[int(leaf)] = value
    else:
        target[leaf] = value


# (이름, 문서를 망가뜨리는 함수, 기대 code, 기대 message) — message는 전문 golden이다.
HYDRATE_GOLDEN: tuple[tuple[str, Any, str, str], ...] = (
    (
        "schema_version 누락",
        lambda d: d.pop("schema_version"),
        "structure.missing_field",
        "문서 맨 위에 schema_version을 적어 주세요 — missing='schema_version' "
        f"supported=('{CURRENT_SCHEMA_VERSION}',)",
    ),
    (
        "지원하지 않는 버전",
        lambda d: _set(d, "schema_version", "9.9"),
        "structure.unsupported_schema_version",
        "지원하지 않는 schema_version입니다. 업그레이드하면 지금 버전으로 바꿔 줍니다 — "
        f"got='9.9' supported=('{CURRENT_SCHEMA_VERSION}',)",
    ),
    (
        "모르는 키(오타)",
        lambda d: _set(d, "risk.max_name_wieght", 0.1),
        "structure.unknown_key",
        "모르는 키입니다 혹시 `max_name_weight`인가요? — "
        "got='max_name_wieght' suggestion='max_name_weight' "
        "allowed=['gross_exposure', 'max_name_weight', "
        "'max_sector_weight', "
        "'net_exposure', 'risk_factor_id', 'risk_field_id', 'sector_neutral']",
    ),
    (
        "필수 키 누락",
        lambda d: d["factors"][0]["graph"]["nodes"][1].pop("window"),
        "structure.missing_field",
        "필수 키가 없습니다 — missing='window' in='TimeSeriesNode'",
    ),
    (
        "모르는 kind",
        lambda d: _set(d, "factors.0.graph.nodes.1.kind", "time_seris"),
        "structure.unknown_kind",
        "모르는 kind입니다 혹시 `time_series`인가요? — got='time_seris' "
        "suggestion='time_series' "
        "allowed=['binary', 'comparison', 'conditional', 'constant', 'cross_sectional', "
        "'field', 'group', 'parameter', "
        "'time_series', 'unary']",
    ),
    (
        "고를 수 없는 값",
        # schema 1.2(P2-03)는 `execution` 을 문서에서 뺐으므로 남아 있는 enum 필드로 본다.
        lambda d: _set(d, "portfolio.side", "long_onyl"),
        "structure.invalid_enum",
        "고를 수 있는 값이 아닙니다 혹시 `long_only`인가요? — "
        "got='long_onyl' suggestion='long_only' allowed=['long_only', 'long_short']",
    ),
    (
        "목록 자리에 블록",
        lambda d: _set(d, "factors", {"momentum": {}}),
        "structure.type_mismatch",
        "여기에는 목록이 와야 합니다. 항목마다 줄 앞에 `- `를 붙이세요 — "
        "expected=sequence got=dict",
    ),
    (
        "블록 자리에 스칼라",
        lambda d: _set(d, "risk", 3),
        "structure.type_mismatch",
        "여기에는 하위 항목을 가진 블록이 와야 합니다 — expected=mapping got=int",
    ),
    (
        "정수 자리에 문자열",
        lambda d: _set(d, "factors.0.graph.nodes.1.window", "252일"),
        "structure.type_mismatch",
        "정수가 와야 합니다 — expected=int got='252일'",
    ),
    (
        "숫자 자리에 문자열",
        lambda d: _set(d, "risk.max_name_weight", "5%"),
        "structure.type_mismatch",
        "숫자가 와야 합니다 — expected=float got='5%'",
    ),
    (
        "문자열 자리에 숫자",
        lambda d: _set(d, "title", 3),
        "structure.type_mismatch",
        "문자열이 와야 합니다 — expected=str got=3",
    ),
    (
        "참·거짓 자리에 문자열",
        lambda d: _set(d, "risk.sector_neutral", "yes"),
        "structure.type_mismatch",
        "참 또는 거짓(true·false)이 와야 합니다 — expected=bool got='yes'",
    ),
    # "날짜 형식"(`structure.invalid_date`) 사례는 schema 1.2(P2-03)에서 뺐다. 날짜 필드이던
    # `data.start`·`data.end` 가 실행 요청의 `environment` 로 옮겨져 전략 문서에는 날짜 필드가 없다.
    (
        "1.0 문법 — factors 두 겹",
        lambda d: _set(d, "factors", {"factors": d["factors"]}),
        "structure.legacy_shape",
        "1.0 문법입니다. factors 아래에 또 factors 목록을 두던 방식이라 지금 버전에서는 읽지 "
        "못합니다. 안쪽 목록을 factors 바로 아래로 올리거나 업그레이드하세요 — "
        "expected=sequence got=dict",
    ),
    (
        "1.0 문법 — 은퇴한 키",
        # 1.0 의 `execution.order_style` 은 1.2 에서 섹션째 사라져 `signal.method` 로 본다.
        lambda d: d.setdefault("signal", {}).update(method="weighted_sum"),
        "structure.legacy_shape",
        "1.0에서만 쓰던 키입니다. 지금 버전은 읽지 않으니 지우거나 업그레이드하세요 — "
        "got='method' section='signal'",
    ),
    (
        "1.0 문법 — unary alias 노드",
        lambda d: _set(
            d,
            "factors.0.graph.nodes.1",
            {"kind": "unary", "node_id": "mom_252", "operator": "rank", "input_node_id": "close"},
        ),
        "structure.legacy_shape",
        "1.0 문법입니다. unary rank는 지금 버전에서 cross_sectional의 rank로 옮겨졌습니다. "
        "kind와 operator를 함께 바꾸거나 업그레이드하세요 — "
        "got=unary/rank expected=cross_sectional/rank",
    ),
    (
        "identity는 봉투 소유",
        lambda d: _set(d, "identity", {"strategy_id": "s-1", "revision": 1}),
        "structure.unknown_key",
        "identity는 revision 봉투가 소유합니다. 문서에서 지워 주세요 — got='identity'",
    ),
)


@pytest.mark.parametrize(
    ("mutate", "code", "message"),
    [pytest.param(m, c, msg, id=name) for name, m, c, msg in HYDRATE_GOLDEN],
)
def test_structure_diagnostic_message_golden(mutate: Any, code: str, message: str) -> None:
    assert _first_issue(mutate) == (code, message)


# (이름, 원본, 형식, 기대 code, 기대 message)
CODEC_GOLDEN: tuple[tuple[str, str, SourceFormat, str, str], ...] = (
    (
        "맨 바깥이 목록",
        "- a\n- b\n",
        SourceFormat.YAML,
        "document.not_a_mapping",
        "문서 맨 바깥은 키와 값을 가진 블록이어야 합니다 — expected=mapping got=list",
    ),
    (
        "빈 문서",
        "\n",
        SourceFormat.YAML,
        "document.not_a_mapping",
        "문서가 비어 있습니다 — expected=mapping",
    ),
    (
        "같은 키 두 번",
        "title: a\ntitle: b\n",
        SourceFormat.YAML,
        "document.duplicate_key",
        "같은 키를 두 번 적었습니다 — key='title'",
    ),
    (
        "키가 문자열이 아님",
        "1: a\n",
        SourceFormat.YAML,
        "document.non_string_key",
        "키는 문자열이어야 합니다 — expected=str got=1",
    ),
    (
        "중첩이 너무 깊음",
        "a:\n  b:\n    c:\n      d: 1\n",
        SourceFormat.YAML,
        "document.too_deep",
        "중첩이 너무 깊습니다 — depth=4 max_depth=3",
    ),
    (
        "무한대",
        "a: .inf\n",
        SourceFormat.YAML,
        "document.non_finite_number",
        "무한대와 NaN은 쓸 수 없습니다 — value='.inf'",
    ),
    (
        "정수 범위 초과",
        "a: 9007199254740993\n",
        SourceFormat.YAML,
        "document.integer_out_of_range",
        "정수가 너무 큽니다(2^53-1 초과) — value=9007199254740993",
    ),
    (
        "지시자",
        "%YAML 1.2\n---\na: 1\n",
        SourceFormat.YAML,
        "yaml.directive",
        "YAML 지시자(%)는 쓸 수 없습니다 — directive=%YAML",
    ),
    (
        "앵커",
        "a: &x 1\nb: 2\n",
        SourceFormat.YAML,
        "yaml.anchor_or_alias",
        "앵커(&)와 별칭(*)은 쓸 수 없습니다 — token=AnchorToken",
    ),
    (
        "태그",
        "a: !!str 1\n",
        SourceFormat.YAML,
        "yaml.tag",
        "태그(!)는 쓸 수 없습니다 — tag=('!!', 'str')",
    ),
    (
        "문서 여러 개",
        "a: 1\n---\nb: 2\n",
        SourceFormat.YAML,
        "yaml.multiple_documents",
        "파일 하나에 YAML 문서는 하나만 둘 수 있습니다 — reason=multiple_documents",
    ),
    (
        "1.2 바깥 숫자 표기",
        "window: 1_000\n",
        SourceFormat.YAML,
        "yaml.non_core_number",
        "YAML 1.2 표준 바깥의 숫자 표기라 편집기와 서버가 다르게 읽습니다 — scalar='1_000'",
    ),
    (
        "병합 키",
        "<<: 1\n",
        SourceFormat.YAML,
        "yaml.merge_key",
        "병합 키(<<)는 쓸 수 없습니다 — token='<<'",
    ),
)

# 한도를 넘겨야만 나는 진단은 그 케이스만 한도를 좁힌다.
LIMIT_GOLDEN: tuple[tuple[str, str, CodecLimits, str, str], ...] = (
    (
        "문서가 너무 큼",
        "title: aaaaaaaaaaaa\n",
        CodecLimits(max_bytes=10),
        "document.too_large",
        "문서가 너무 큽니다 — bytes=20 max_bytes=10",
    ),
    (
        "항목이 너무 많음",
        "a: 1\nb: 2\n",
        CodecLimits(max_nodes=2),
        "document.too_many_nodes",
        "문서의 항목이 너무 많습니다 — max_nodes=2",
    ),
)


@pytest.mark.parametrize(
    ("source", "format", "code", "message"),
    [pytest.param(s, f, c, m, id=name) for name, s, f, c, m in CODEC_GOLDEN],
)
def test_codec_diagnostic_message_golden(
    source: str, format: SourceFormat, code: str, message: str
) -> None:
    assert _reject(source, format=format) == (code, message)


@pytest.mark.parametrize(
    ("name", "source", "format", "code"),
    [
        ("yaml", "a: [1, 2\n", SourceFormat.YAML, "yaml.syntax"),
        ("json", '{"a": 1,}', SourceFormat.JSON, "json.syntax"),
    ],
)
def test_parser_text_stays_behind_the_detail_marker(
    name: str, source: str, format: SourceFormat, code: str
) -> None:
    """파서가 준 영어 원문은 문장이 아니라 `detail=` 뒤 기계 디테일로만 남는다."""
    actual_code, message = _reject(source, format=format)
    sentence, separator, detail = message.partition(_DETAIL_SEPARATOR)

    assert actual_code == code
    assert separator == _DETAIL_SEPARATOR
    assert sentence.endswith("문법 오류입니다")
    assert detail.startswith("detail=")


@pytest.mark.parametrize(
    ("source", "limits", "code", "message"),
    [pytest.param(src, lim, c, m, id=name) for name, src, lim, c, m in LIMIT_GOLDEN],
)
def test_codec_limit_message_golden(
    source: str, limits: CodecLimits, code: str, message: str
) -> None:
    assert _reject(source, limits=limits) == (code, message)


@dataclass(frozen=True)
class _Dated:
    """날짜 필드 하나짜리 모델. `structure.invalid_date` 문장을 고정하는 데만 쓴다."""

    start: date


def test_invalid_date_message_golden() -> None:
    """날짜 형식 문장의 golden.

    schema 1.2(P2-03)에서 전략 문서의 날짜 필드(`data.start`·`data.end`)가 실행 요청의
    `environment` 로 옮겨져 문서 변조로는 이 코드에 닿지 않는다. 코드와 문장은 날짜 필드를 가진
    모델을 위해 hydrate 에 남으므로 날짜 필드 하나짜리 모델로 직접 확인한다.
    """
    issues: list[Any] = []
    _hydrate(_Dated, {"start": "2021/01/01"}, "", issues)

    assert [(issue.code, issue.message) for issue in issues] == [
        (
            "structure.invalid_date",
            "날짜는 YYYY-MM-DD로 적어 주세요. 예: 2021-01-01 — "
            "expected=YYYY-MM-DD example=2021-01-01 got='2021/01/01'",
        )
    ]


def test_every_declared_code_has_a_message_golden() -> None:
    """코드 레지스트리와 문장 golden이 1:1이다 — 코드를 늘리면 문장도 같은 PR에서 늘어난다."""
    structural = {code for _n, _m, code, _msg in HYDRATE_GOLDEN}
    # 날짜 형식은 schema 1.2 문서에 날짜 필드가 없어 문서 변조로는 닿지 않는다. 문장은 아래
    # `test_invalid_date_message_golden` 이 hydrate 를 직접 불러 고정한다.
    structural |= {"structure.invalid_date"}
    codec = {code for _n, _s, _f, code, _msg in CODEC_GOLDEN} | {
        code for _n, _s, _l, code, _msg in LIMIT_GOLDEN
    }
    # 파서 원문을 끼고 나가는 두 코드는 전문 golden 대신 구조 단언이 지킨다(아래 테스트).
    codec |= {"yaml.syntax", "json.syntax"}
    expected_codec = diagnostic_codes(SourceFormat.YAML) | diagnostic_codes(SourceFormat.JSON)

    assert structural == STRUCTURE_CODES, sorted(structural ^ STRUCTURE_CODES)
    assert codec == expected_codec, sorted(codec ^ expected_codec)


@pytest.mark.parametrize(
    ("code", "message"),
    [pytest.param(c, m, id=n) for n, _mutate, c, m in HYDRATE_GOLDEN]
    + [pytest.param(c, m, id=n) for n, _s, _f, c, m in CODEC_GOLDEN]
    + [pytest.param(c, m, id=n) for n, _s, _l, c, m in LIMIT_GOLDEN],
)
def test_no_english_sentence_reaches_the_reader(code: str, message: str) -> None:
    """모든 진단이 한국어 문장으로 열리고, 은퇴한 영어 원문이 돌아오지 않는다."""
    sentence, separator, _detail = message.partition(_DETAIL_SEPARATOR)

    assert separator == _DETAIL_SEPARATOR, f"{code}: 문장과 디테일을 가르는 구분자가 없다"
    assert _HANGUL.search(sentence), f"{code}: 문장이 한국어가 아니다 — {message!r}"
    returned = [phrase for phrase in _RETIRED_ENGLISH if phrase in message]
    assert not returned, f"{code}: 영어 원문이 돌아왔다 — {returned}"


@pytest.mark.parametrize(
    ("name", "mutate", "expected"),
    [
        pytest.param("모르는 키", HYDRATE_GOLDEN[2][1], "max_name_weight", id="키"),
        pytest.param("모르는 kind", HYDRATE_GOLDEN[4][1], "time_series", id="kind"),
        pytest.param("고를 수 없는 값", HYDRATE_GOLDEN[5][1], "long_only", id="enum"),
    ],
)
def test_near_miss_suggestion_appears_in_both_the_sentence_and_the_details(
    name: str, mutate: Any, expected: str
) -> None:
    """근접 후보 제안은 사람이 읽는 문장과 기계가 읽는 자리에 같이 실린다.

    문장에는 계약 문자열을 번역하지 않고 `` `키` ``로 인용한다 — 사람 말 이름의 정본은 runtime
    schema의 `x-description-key`와 frontend i18n이고(P1-03), backend가 한 벌 더 갖지 않는다.
    """
    _code, message = _first_issue(mutate)
    sentence, _separator, detail = message.partition(_DETAIL_SEPARATOR)

    assert f"혹시 `{expected}`인가요?" in sentence
    assert f"suggestion={expected!r}" in detail


def _graph(*nodes: object, output: str) -> FactorGraph:
    # reason: 테스트가 일부러 만든 잘못된 그래프라 타입 검사기가 막는 조합을 쓴다.
    return FactorGraph(nodes=tuple(nodes), output_node_id=output)  # type: ignore[arg-type]


def test_cycle_diagnostic_names_every_node_in_the_loop() -> None:
    """순환에 묶인 노드마다 진단이 붙고 고리 경로가 문장에 실린다.

    그래프 카드는 `node_id`로 노드를 집어 하이라이트하므로, 고리 전체를 가리키려면 진단도 노드마다
    있어야 한다.
    """
    left = BinaryNode(
        kind="binary",
        node_id="a",
        operator=BinaryOperator.ADD,
        left_node_id="b",
        right_node_id="b",
    )
    right = BinaryNode(
        kind="binary",
        node_id="b",
        operator=BinaryOperator.ADD,
        left_node_id="a",
        right_node_id="a",
    )

    issues = [i for i in validate_factor_graph(_graph(left, right, output="a")).issues]
    cycle = [issue for issue in issues if issue.code == "factor.graph.cycle"]

    assert [(issue.node_id, issue.path) for issue in cycle] == [("a", "nodes.0"), ("b", "nodes.1")]
    assert all("cycle=a → b → a" in issue.message for issue in cycle)


def test_cycle_diagnostic_misses_no_node_of_a_tangle() -> None:
    """고리에 묶인 노드를 하나도 빠뜨리지 않는다(1차 리뷰 P3-6).

    back-edge 한 번으로 순환 하나를 적던 방식은 이미 끝난 노드를 통해서만 닿는 순환을 놓쳐,
    그 노드 카드에만 배지가 붙지 않았다. `1→2, 1→4, 2→3, 3→1, 4→2`에서 노드 `4`가 그랬다.
    """
    edges = {"1": ("2", "4"), "2": ("3", "3"), "3": ("1", "1"), "4": ("2", "2")}
    graph_nodes = [
        BinaryNode(
            kind="binary",
            node_id=node_id,
            operator=BinaryOperator.ADD,
            left_node_id=left,
            right_node_id=right,
        )
        for node_id, (left, right) in edges.items()
    ]

    issues = validate_factor_graph(_graph(*graph_nodes, output="1")).issues
    cycle = [issue for issue in issues if issue.code == "factor.graph.cycle"]

    assert sorted(issue.node_id or "" for issue in cycle) == ["1", "2", "3", "4"]
    # 갈래가 있는 묶음은 있지도 않은 경로를 화살표로 그리지 않고, `키=값` 한 쌍으로 낸다.
    assert all("cycle_nodes=[1, 2, 3, 4]" in issue.message for issue in cycle)


def test_duplicate_node_diagnostic_names_the_repeated_id() -> None:
    """중복 id를 쓴 자리마다 진단이 붙는다 — "중복될 수 없습니다" 한 줄로는 어느 노드인지 모른다."""
    first = FieldNode(kind="field", node_id="px", field_id="price.close")
    second = FieldNode(kind="field", node_id="px", field_id="price.open")

    validation = validate_factor_graph(_graph(first, second, output="px"))
    duplicates = [i for i in validation.issues if i.code == "factor.graph.duplicate_node"]

    assert [(issue.node_id, issue.path) for issue in duplicates] == [("px", "nodes.1")]
    assert duplicates[0].message == (
        "앞에서 이미 쓴 node_id입니다. 다른 이름을 붙여 주세요 — node_id='px'"
    )


def test_graph_diagnostics_reach_a_strategy_document_as_expression_codes() -> None:
    """전략 문서에 실린 그래프 진단은 `strategy.expression.*`다 — `factor.*`는 새지 않는다."""
    from strategy_workbench.domain.strategy.facade.validation import validate_strategy

    document = _document()
    document["factors"][0]["graph"]["nodes"] = [
        {"kind": "field", "node_id": "close", "field_id": "price.close"},
        {
            "kind": "binary",
            "node_id": "loop",
            "operator": "add",
            "left_node_id": "loop",
            "right_node_id": "close",
        },
    ]
    document["factors"][0]["graph"]["output_node_id"] = "loop"
    hydrated = hydrate_strategy_document(copy.deepcopy(document), identity=DRAFT)
    assert hydrated.spec is not None, hydrated.issues

    issues = validate_strategy(hydrated.spec).issues
    graph_issues = [issue for issue in issues if "expression" in issue.code]

    assert not [issue for issue in issues if issue.code.startswith("factor.")]
    assert ("strategy.expression.cycle", "loop") in [
        (issue.code, issue.node_id) for issue in graph_issues
    ]
