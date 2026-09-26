"""어시스턴트 프롬프트 골든 파일을 다시 뽑는다 (WORKFLOW A-07).

모델이 읽는 문장은 코드처럼 리뷰되지 않는다. 한 줄만 바뀌어도 모델이 도구를 부르는 순서나
제안의 모양이 달라지는데, 그 변화는 테스트가 아니라 몇 주 뒤 사용자 제보로 드러난다. 그래서
프롬프트 전문·도구 설명·고정 통지 문구를 골든 파일로 잠그고, 바뀌면 diff가 리뷰에 올라오게 한다.

## 갱신 절차

1. `_prompt.py`(문장)나 `domain/assistant/_tools.py`(도구 설명·스키마)를 고친다.
2. `backend/`에서 아래를 실행한다.

       uv run python tools/export_assistant_prompts.py

3. `git diff backend/tests/fixtures/assistant/`로 모델이 읽을 문장이 의도대로 바뀌었는지 본다.
   진단과 달리 여기 diff는 **사람이 읽고 판단할 것**이지 기계가 판정할 것이 아니다.
4. 커밋에 코드 변경과 골든 갱신을 같이 넣는다.

갱신을 빠뜨리면 `tests/application/test_assistant_prompt_fixtures.py`가 재생성 명령을 띄우며
실패한다.

## 왜 live schema가 아니라 runtime schema fixture인가

시스템 프롬프트의 "전략 문서 언어 요약"은 runtime schema에서 생성된다(spec D8). 여기서 live
스키마를 쓰면 스키마가 바뀔 때마다 프롬프트 골든이 깨지고, 그 실패가 "스키마가 바뀌었다"인지
"문장을 고쳤다"인지 구분되지 않는다. 그래서 입력을 추적되는 fixture
(`tests/fixtures/strategy_documents/runtime-schema.json`)로 고정한다. 그 fixture가 live 스키마와
같다는 것은 `tests/domain/test_strategy_schema.py`가 따로 지키므로, 스키마 변경은 그 테스트에서
한 번, 프롬프트 골든에서 또 한 번 드러난다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path

from strategy_workbench.adapters.outbound.equity_mock.facade.provider import MockEquityDataAdapter
from strategy_workbench.application.assistant_chat.facade.context import AssistantContextBuilder
from strategy_workbench.application.assistant_chat.facade.prompt import MODEL_NOTICES
from strategy_workbench.domain.assistant.facade.models import ProposalCompileResult
from strategy_workbench.domain.assistant.facade.tools import ASSISTANT_TOOLS
from strategy_workbench.domain.factor.facade.registry import build_default_factor_registry

__all__ = [
    "FIXTURE_DIR",
    "FIXTURE_TODAY",
    "REGENERATE_COMMAND",
    "RUNTIME_SCHEMA_FIXTURE",
    "build_artifacts",
    "main",
]

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = _BACKEND_ROOT / "tests" / "fixtures" / "assistant"
RUNTIME_SCHEMA_FIXTURE = (
    _BACKEND_ROOT / "tests" / "fixtures" / "strategy_documents" / "runtime-schema.json"
)
REGENERATE_COMMAND = "uv run python tools/export_assistant_prompts.py"

# 골든을 뽑을 때 쓰는 고정 날짜. 프롬프트는 "오늘"을 싣기 때문에 실제 날짜를 쓰면 골든이 매일
# 깨진다. 값 자체에 의미는 없고, 날짜 자리가 실제로 채워지는지만 보면 된다.
FIXTURE_TODAY = date(2026, 1, 2)


class _UnusedStrategyCompiler:
    """`StrategyCompilerPort` 자리를 채우는 구현.

    `AssistantContextBuilder`는 도구 실행에 컴파일러가 필요하지만 `system_prompt()`는 쓰지
    않는다. 조용한 가짜를 두면 언젠가 프롬프트 생성이 컴파일에 기대게 되어도 아무도 모르므로,
    불리면 터지게 둔다.
    """

    def compile(self, source_text: str) -> ProposalCompileResult:
        raise RuntimeError(
            "system prompt export must not compile — "
            f"source_text_chars={len(source_text)} command={REGENERATE_COMMAND}"
        )


def _context_builder() -> AssistantContextBuilder:
    schema = json.loads(RUNTIME_SCHEMA_FIXTURE.read_text(encoding="utf-8"))
    return AssistantContextBuilder(
        equity_data=MockEquityDataAdapter.demo(),
        factor_registry=build_default_factor_registry(),
        compiler=_UnusedStrategyCompiler(),
        today=lambda: FIXTURE_TODAY,
        schema=lambda: schema,
    )


def _tools_document() -> str:
    """도구 5종의 이름·설명·입력 스키마. 모델이 읽는 계약 전부다."""
    payload = [
        {"name": spec.name, "description": spec.description, "input_schema": spec.input_schema}
        for spec in ASSISTANT_TOOLS
    ]
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _notices_document() -> str:
    """모델에게 되돌리는 고정 문구(제안 거절·검색 상한 통지 등)."""
    return json.dumps(dict(MODEL_NOTICES), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def build_artifacts() -> Mapping[str, str]:
    """파일 이름 → 내용. 테스트와 이 스크립트가 같은 함수를 쓴다(골든의 owner는 하나다)."""
    return {
        "system_prompt.ko.md": _context_builder().system_prompt(),
        "tools.json": _tools_document(),
        "model_notices.json": _notices_document(),
    }


def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in build_artifacts().items():
        (FIXTURE_DIR / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {(FIXTURE_DIR / name).relative_to(_BACKEND_ROOT)}")


if __name__ == "__main__":
    main()
