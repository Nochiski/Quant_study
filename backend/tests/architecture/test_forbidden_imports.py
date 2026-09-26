"""Libraries that must stay out of the pure layers (WORKFLOW P1-04, parser ADR D1).

- domain/application never import pydantic: the inbound schema adapter owns discriminator metadata.
- strategy_workbench never imports PyYAML: ruamel.yaml (YAML 1.2) is the only parser (adapter).
- provider SDKs (`anthropic`, `openai`) live only in their own outbound adapters
  (AI 어시스턴트 설계 spec D1/D4, A-01): domain/application see providers through
  `LlmProviderPort`, so a second provider is a new adapter and not an edit to the tool loop.

Scope: these gates read `import` / `from … import` statements out of the AST. An import made
through a string (`importlib.import_module("anthropic")`, `__import__`) is outside what they can
see, so a reviewer of the provider adapters has to check that by eye.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "strategy_workbench"

LLM_SDKS = frozenset({"anthropic", "openai"})
# 끝의 "/"가 중요하다. 접두만 비교하면 `llm_openai_experiment/` 같은 인접 디렉터리도 통과한다.
LLM_SDK_OWNERS = (
    "adapters/outbound/llm_anthropic/",
    "adapters/outbound/llm_openai/",
)


def _imports(path: Path) -> set[str]:
    modules: set[str] = set()
    for statement in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(statement, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom) and statement.module and statement.level == 0:
            modules.add(statement.module.split(".")[0])
    return modules


def test_domain_and_application_do_not_import_pydantic() -> None:
    offenders = [
        str(path.relative_to(SRC_ROOT))
        for layer in ("domain", "application")
        for path in (SRC_ROOT / layer).rglob("*.py")
        if "pydantic" in _imports(path)
    ]
    assert offenders == [], f"pydantic leaked into pure layers — files={offenders}"


def test_nobody_imports_pyyaml() -> None:
    offenders = [
        str(path.relative_to(SRC_ROOT))
        for path in SRC_ROOT.rglob("*.py")
        if "yaml" in _imports(path)
    ]
    assert offenders == [], f"PyYAML import found — files={offenders} (use the ruamel codec)"


def _sdk_offenders(files: dict[Path, set[str]]) -> list[str]:
    """Files importing a provider SDK from outside the adapter that owns it.

    Takes the scanned imports as a parameter so the regression test below can plant an import
    without writing a file into the package tree.
    """
    offenders: list[str] = []
    for path, modules in files.items():
        location = path.relative_to(SRC_ROOT).as_posix()
        if location.startswith(LLM_SDK_OWNERS):
            continue
        for module in sorted(modules & LLM_SDKS):
            offenders.append(f"{location}: {module}")
    return offenders


def test_provider_sdks_stay_inside_their_outbound_adapters() -> None:
    offenders = _sdk_offenders({path: _imports(path) for path in SRC_ROOT.rglob("*.py")})

    assert offenders == [], (
        "provider SDK import outside its adapter — "
        f"files={offenders} allowed={list(LLM_SDK_OWNERS)} "
        "(use application.assistant_chat's LlmProviderPort instead)"
    )


def test_the_provider_sdk_gate_catches_an_import_in_the_use_case() -> None:
    """A planted import has to fail: a green gate over an empty tree proves nothing."""
    planted = SRC_ROOT / "application" / "assistant_chat" / "_chat.py"

    offenders = _sdk_offenders({planted: {"anthropic", "json"}})

    assert offenders == ["application/assistant_chat/_chat.py: anthropic"]


def test_the_provider_sdk_gate_allows_the_owning_adapters() -> None:
    owned = SRC_ROOT / "adapters" / "outbound" / "llm_openai" / "_adapter.py"

    assert _sdk_offenders({owned: {"openai"}}) == []
