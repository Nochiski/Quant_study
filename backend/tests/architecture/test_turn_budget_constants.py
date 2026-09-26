"""턴 예산 상수를 adapter가 다시 선언하지 않는다 (C-01 감사 NB-5).

## 왜 구조 테스트인가

`MIN_CALL_OUTPUT_TOKENS`는 두 공급자 adapter에 각각 `= 256`으로 있었다. 값이 같았으므로 동작은
옳았지만 그 등가를 지키는 것이 없었다. 한쪽만 올리면 같은 `TurnRequest`가 공급자에 따라 다른
지점에서 예산 소진으로 끝나고, 그 차이는 예산 경계에서만 드러나 재현이 어렵다.

값을 domain으로 옮겨 owner를 하나로 만든 뒤에도, 다음 사람이 adapter 안에 같은 이름의 리터럴을
다시 두는 것을 막는 것은 없다. 그래서 "adapter 소스에 이 상수의 **할당문**이 없다"를 여기서
고정한다. 값 비교가 아니라 선언 위치를 보는 것이라 구조 테스트다.

## 무엇을 보지 않는가

adapter가 domain 값을 읽어 자기 판단으로 조정하는 것(예: 공급자 최소 단위에 맞춰 올림)은 막지
않는다. 그것은 adapter 정책이고, 막으려면 값이 아니라 그 판단을 domain으로 올려야 한다. 여기서
막는 것은 **같은 사실을 두 곳에 적는 것** 하나뿐이다.
"""

from __future__ import annotations

import ast
from pathlib import Path

from strategy_workbench.domain.assistant.facade.models import MIN_CALL_OUTPUT_TOKENS

_ADAPTER_ROOTS = (
    Path(__file__).resolve().parents[2] / "src" / "strategy_workbench" / "adapters" / "outbound"
)
_OWNED_NAMES = ("MIN_CALL_OUTPUT_TOKENS",)


def _assigned_names(source: Path) -> set[str]:
    """모듈 수준 할당문의 좌변 이름. 함수 안 지역 변수는 계약이 아니므로 보지 않는다."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names: set[str] = set()
    for statement in tree.body:
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
            if isinstance(statement, ast.AnnAssign)
            else []
        )
        names.update(target.id for target in targets if isinstance(target, ast.Name))
    return names


def _provider_adapter_sources() -> list[Path]:
    return sorted(
        path
        for directory in _ADAPTER_ROOTS.glob("llm_*")
        for path in directory.rglob("*.py")
    )


def test_no_provider_adapter_redeclares_a_turn_budget_constant() -> None:
    sources = _provider_adapter_sources()

    # 대상이 비면 아래 단언이 공허하게 통과한다. 그 통과가 이 가드의 유일한 실패 모드다.
    assert sources, f"공급자 adapter 소스를 찾지 못했다 — root={_ADAPTER_ROOTS}"
    offenders = [
        f"{path.relative_to(_ADAPTER_ROOTS)}:{name}"
        for path in sources
        for name in sorted(_OWNED_NAMES)
        if name in _assigned_names(path)
    ]
    assert offenders == [], (
        "턴 예산 상수는 domain이 소유한다. adapter는 "
        "`domain.assistant.facade.models`에서 읽는다 — "
        f"redeclared={offenders}"
    )


def test_both_provider_adapters_enforce_the_same_minimum_call_size() -> None:
    """두 adapter가 같은 값을 집행하는지 실제 import로 확인한다.

    앞 테스트가 재선언을 막고, 이 테스트가 "그래서 둘이 같은 값을 본다"를 확인한다. SDK가 없는
    환경에서는 adapter 모듈을 적재할 수 없으므로 건너뛴다.
    """
    import pytest

    anthropic_turn = pytest.importorskip(
        "strategy_workbench.adapters.outbound.llm_anthropic._turn",
        reason="공급자 SDK는 optional extra `llm`이다.",
    )
    openai_turn = pytest.importorskip(
        "strategy_workbench.adapters.outbound.llm_openai._turn",
        reason="공급자 SDK는 optional extra `llm`이다.",
    )

    assert anthropic_turn.MIN_CALL_OUTPUT_TOKENS == MIN_CALL_OUTPUT_TOKENS
    assert openai_turn.MIN_CALL_OUTPUT_TOKENS == MIN_CALL_OUTPUT_TOKENS
