"""P2-01: 실행 설정은 `RunEnvironment` 하나가 소유한다.

유스케이스 서비스가 `spec.data.*` · `spec.execution.*` 를 직접 읽으면 호출자가 넘긴 명시
`environment` 가 조용히 무시된다 — 매니페스트에는 명시 값이, 엔진에는 문서 값이 들어가는
silent divergence 다. 소스 AST 로 세 서비스의 직접 참조가 0건인지 고정한다.
`domain/backtest` 의 브리지(`_bridge.py`)는 1.1 문서를 읽는 것이 일이므로 대상이 아니다.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "strategy_workbench"

# 실행 설정을 `environment` 로만 읽어야 하는 유스케이스 서비스.
ENVIRONMENT_CONSUMERS = (
    "application/backtest_run/_service.py",
    "application/portfolio_design/_service.py",
    "application/portfolio_design/_trace_service.py",
)

LEGACY_SECTIONS = ("data", "execution")


def _legacy_reads(path: Path) -> list[str]:
    """`<무엇>.data.<필드>` / `<무엇>.execution.<필드>` 모양의 속성 읽기를 모은다."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        parent = node.value
        if not isinstance(parent, ast.Attribute) or parent.attr not in LEGACY_SECTIONS:
            continue
        found.append(f"{path.name}:{node.lineno} {ast.unparse(node)}")
    return found


def test_use_case_services_read_the_run_environment_only() -> None:
    offenders = [item for name in ENVIRONMENT_CONSUMERS for item in _legacy_reads(SRC_ROOT / name)]
    assert offenders == [], (
        "유스케이스 서비스가 실행 설정을 전략 문서에서 직접 읽는다 — "
        f"reads={offenders} (RunEnvironment 를 거쳐야 한다)"
    )
