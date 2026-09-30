"""어시스턴트 도구 이름과 입력 스키마 (설계 spec D3).

도구 이름·스키마는 domain 상수다. 공급자 adapter는 `ToolSpec`을 자기 형식으로 옮길 뿐 이름을
알지 않고, 도구를 실제로 실행하는 쪽은 `application/assistant_chat`이다. 새 도구를 여기와
application에 추가하면 모든 공급자에서 동시에 동작한다.

모든 스키마는 strict다. 최상위와 중첩 객체 모두 `additionalProperties: false`이고 선언한 속성은
전부 `required`다. 모델이 임의 키를 섞어 보내면 공급자 쪽에서 거부되어야, application이 모르는
키를 조용히 버려 제안이 lossy해지는 일이 없다.

설명문(`description`)은 모델이 읽는 문장이므로 "무엇을 돌려주는지"와 "언제 부르는지"를 한국어로
구체적으로 적는다.
"""

from __future__ import annotations

from ._models import ToolSpec

__all__ = [
    "ASSISTANT_TOOLS",
    "LIST_EQUITY_FIELDS",
    "LIST_FACTOR_CATALOG",
    "PROPOSE_STRATEGY",
    "READ_BACKTEST_RESULT",
    "READ_CURRENT_STRATEGY",
    "RESULT_EXPLAIN_TOOLS",
    "VALIDATE_STRATEGY_YAML",
]

READ_CURRENT_STRATEGY = "read_current_strategy"
LIST_EQUITY_FIELDS = "list_equity_fields"
LIST_FACTOR_CATALOG = "list_factor_catalog"
VALIDATE_STRATEGY_YAML = "validate_strategy_yaml"
PROPOSE_STRATEGY = "propose_strategy"
READ_BACKTEST_RESULT = "read_backtest_result"


def _no_input() -> dict[str, object]:
    return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


ASSISTANT_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name=READ_CURRENT_STRATEGY,
        description=(
            "사용자가 지금 편집 중인 전략 문서의 YAML 원문, 형식, 실행 설정(기간·유니버스 등), "
            "그리고 현재 컴파일 진단 목록을 돌려준다. 전략을 고치거나 새로 제안하기 전에 반드시 "
            "먼저 호출해 현재 문서가 무엇인지 확인한다."
        ),
        input_schema=_no_input(),
    ),
    ToolSpec(
        name=LIST_EQUITY_FIELDS,
        description=(
            "전략에서 쓸 수 있는 데이터 필드 목록을 돌려준다. 각 항목은 식별자, 표시 이름, 단위, "
            "설명을 담는다. 여기에 없는 식별자는 존재하지 않는 데이터이므로 절대 전략에 쓰지 "
            "않는다."
        ),
        input_schema=_no_input(),
    ),
    ToolSpec(
        name=LIST_FACTOR_CATALOG,
        description=(
            "팩터 카탈로그를 돌려준다. 각 항목은 식별자, 표시 이름, 선호 방향(값이 큰 쪽과 "
            "작은 쪽 중 어느 쪽을 좋게 보는지), 가용성, 필요한 데이터 필드 식별자를 담는다. "
            "가용성이 implemented 인 팩터만 지금 연결된 데이터로 백테스트할 수 있다. "
            "catalog_only 는 아직 구현되지 않았고, unavailable 은 구현됐지만 연결된 데이터에 "
            "필요한 필드가 없다는 뜻이다. implemented 가 아닌 팩터는 제안에 넣지 않는다."
        ),
        input_schema=_no_input(),
    ),
    ToolSpec(
        name=VALIDATE_STRATEGY_YAML,
        description=(
            "전략 YAML 원문을 서버에서 파싱·검증해 진단 목록을 돌려준다. 진단이 비어 있으면 "
            "그 문서는 실행 가능하다. 제안을 제출하기 전에 이 도구로 먼저 검증하고, 오류가 "
            "남아 있으면 진단이 가리키는 위치를 고쳐 다시 검증한다."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "source_text": {
                    "type": "string",
                    "description": "검증할 전략 문서 YAML 원문 전체.",
                }
            },
            "required": ["source_text"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name=PROPOSE_STRATEGY,
        description=(
            "최종 전략 제안을 사용자에게 제출한다. 제출한 YAML은 서버가 다시 검증하며, 검증에 "
            "실패하면 진단이 오류로 되돌아오므로 고쳐서 다시 제출해야 한다. 검증을 통과한 제안만 "
            "제출하고, 한 턴에서 제출은 한 번으로 끝내는 것을 목표로 한다."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "제안 전략의 짧은 이름.",
                },
                "summary": {
                    "type": "string",
                    "description": "제안을 한 문장으로 요약한 설명.",
                },
                "rationale": {
                    "type": "string",
                    "description": (
                        "왜 이 전략인지에 대한 근거. 검색으로 확인한 사실을 쓸 때는 본문에 출처 "
                        "URL을 함께 적는다."
                    ),
                },
                "sources": {
                    "type": "array",
                    "description": "근거로 인용한 출처 목록. 검색을 쓰지 않았으면 빈 배열.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "출처 문서의 제목."},
                            "url": {"type": "string", "description": "출처 문서의 URL."},
                        },
                        "required": ["title", "url"],
                        "additionalProperties": False,
                    },
                },
                "source_text": {
                    "type": "string",
                    "description": "제안하는 전략 문서 YAML 원문 전체.",
                },
            },
            "required": ["title", "summary", "rationale", "sources", "source_text"],
            "additionalProperties": False,
        },
    ),
)

# 결과 설명 세션이 받는 도구(결과 설명 spec R2·R5). 전략 도구와 섞지 않는다 — 결과 화면에는 적용할
# 편집기가 없어 제안 도구를 주면 모델이 쓸모없는 제안을 만들고, 문서를 읽는 도구는 읽을 문서가 없다.
RESULT_EXPLAIN_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name=READ_BACKTEST_RESULT,
        description=(
            "사용자가 보고 있는 백테스트 실행 하나의 결과 요약을 돌려준다. 기간과 실행 설정, "
            "전략 요약, 성과 지표(이름·단위·높을수록 좋은지·값), 자산 곡선의 처음과 끝, "
            "가장 깊은 낙폭의 날짜, 최근 월별 수익률, 데이터 경고를 담는다. 길이 상한 때문에 "
            "덜어 낸 항목은 omitted에 개수로 적힌다. 답하기 전에 반드시 먼저 호출한다."
        ),
        input_schema=_no_input(),
    ),
)
