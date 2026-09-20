"""P2-01·P2-02·P2-03: 실행 설정은 `RunEnvironment` 하나가 소유한다.

1.2 는 `data`·`execution`·`graph.missing_policy` 를 전략 문서에서 지웠다(spec D3 S1~S3). 모델에서
사라졌으므로 이 검사는 이제 "옛 경로가 되살아나지 않는다"를 지키는 회귀 그물이다 — 어딘가에
호환 shim 을 다시 얹으면 명시 `environment` 가 조용히 무시되는 P2-01 P0 이 그대로 돌아온다.
`src` 전체에서 `<무엇>.data.<필드>`·`<무엇>.execution.<필드>` 읽기와 `missing_policy` 속성 읽기가
0건인지 소스 AST 로 고정한다.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "strategy_workbench"

# 실행 설정을 `environment` 로만 읽어야 하는 유스케이스 서비스. 1.2 에서는 `src` 전체가 대상이지만
# 진단 메시지가 읽히도록 유스케이스 서비스를 먼저 이름으로 건다.
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


def test_no_module_reads_the_retired_document_sections() -> None:
    """1.2 에서 `spec.data.*`·`spec.execution.*` 는 어디에도 없다(P2-03)."""
    offenders = [item for path in sorted(SRC_ROOT.glob("**/*.py")) for item in _legacy_reads(path)]
    assert offenders == [], (
        "은퇴한 전략 문서 섹션을 읽는 코드가 있다 — "
        f"reads={offenders} (실행 설정은 RunEnvironment 가 소유한다)"
    )


# `PortfolioPreviewRequest` 를 만드는 유스케이스. 여기서 `environment` 를 빠뜨리면 파이프라인이
# 문서 브리지 값으로 되돌아가고, 매니페스트는 실행하지 않은 설정을 기록한다(리뷰 P0).
PREVIEW_REQUEST_CALLERS = (
    "application/backtest_run/_service.py",
    "application/portfolio_design/_service.py",
    "application/portfolio_design/_trace_service.py",
)


def _preview_requests_without_environment(path: Path) -> list[str]:
    """`PortfolioPreviewRequest(...)` 호출 중 `environment` 키워드가 없는 것을 모은다."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", None)
        if name != "PortfolioPreviewRequest":
            continue
        if any(keyword.arg == "environment" for keyword in node.keywords):
            continue
        found.append(f"{path.name}:{node.lineno} {ast.unparse(node)}")
    return found


def test_every_preview_request_carries_the_resolved_environment() -> None:
    offenders = [
        item
        for name in PREVIEW_REQUEST_CALLERS
        for item in _preview_requests_without_environment(SRC_ROOT / name)
    ]
    assert offenders == [], (
        "PortfolioPreviewRequest 가 실행 설정 없이 만들어진다 — "
        f"calls={offenders} (해소한 environment 를 그대로 넘겨야 한다)"
    )


def _missing_policy_reads(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        f"{path.relative_to(SRC_ROOT).as_posix()}:{node.lineno} {ast.unparse(node)}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "missing_policy"
    ]


def test_nothing_reads_a_graph_missing_policy_attribute() -> None:
    """P2-03: 필드가 모델에서 사라졌으므로 읽는 곳도 0 건이다.

    plan payload 의 `missing_policy` 는 dict 키라 속성 읽기가 아니다 — 그쪽은 실행 설정에서
    인자로 받은 값이고 `plan_hash` 에 남는다(P2-02 의 캐시 키 invariant).
    """
    offenders = [
        item for path in sorted(SRC_ROOT.glob("**/*.py")) for item in _missing_policy_reads(path)
    ]
    assert offenders == [], (
        "`graph.missing_policy` 속성을 읽는 코드가 있다 — "
        f"reads={offenders} (실행 설정의 missing 을 인자로 받아야 한다)"
    )
